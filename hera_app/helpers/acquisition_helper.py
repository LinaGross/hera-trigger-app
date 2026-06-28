import argparse
import ctypes
import json
import os
import sys
import time
import traceback
import uuid

from hera_app.controllers import HeraController


def emit(event, **payload):
    message = {"event": event, **payload}
    print(json.dumps(message, separators=(",", ":")), flush=True)


def read_request(path):
    with open(path, "r", encoding="utf-8") as request_file:
        return json.load(request_file)


def connect_controller(request, emit_func=emit):
    controller = HeraController(dll_path=request["dll_path"])
    devices = controller.enumerate_devices()
    if not devices:
        raise RuntimeError("No Hera devices found.")
    device_index = int(request.get("device_index", 0))
    if device_index < 0 or device_index >= len(devices):
        raise RuntimeError(f"Requested Hera device index {device_index} is not available.")
    controller.create_device(devices[device_index])
    controller.connect()
    product = devices[device_index].ProductName.decode("utf-8", errors="ignore")
    serial = devices[device_index].SerialNumber.decode("utf-8", errors="ignore")
    emit_func("log", message=f"Helper connected to Hera device {device_index}: {product} ({serial})")
    return controller


def apply_camera_settings(controller, request, emit_func=emit):
    exposure_ms = float(request["exposure_ms"])
    gain = float(request["gain"])
    hdr_enabled = bool(request.get("hdr_enabled", False))
    roi = request.get("roi")

    hdr_readback = None
    if controller.is_hdr_supported():
        controller.set_hdr(hdr_enabled)
        time.sleep(0.3 if hdr_enabled else 0.2)
        hdr_readback = controller.get_hdr()
        if hdr_readback != hdr_enabled:
            raise RuntimeError(
                "Helper HDR readback mismatch: "
                f"requested={hdr_enabled}, actual={hdr_readback}"
            )
        emit_func("log", message=f"Helper set HDR readback: {hdr_readback}")
    elif hdr_enabled:
        raise RuntimeError("HDR was requested, but this Hera device or SDK DLL reports HDR is not supported.")

    try:
        if controller.is_gain_writable():
            controller.set_gain(gain)
            emit_func("log", message=f"Helper set gain: {gain:.6f}")
        else:
            emit_func("log", message=f"Helper gain is read-only; current gain={controller.get_gain():.6f}")
    except Exception as exc:
        emit_func("log", message=f"Helper gain setting skipped: {exc}")

    controller.set_exposure_ms(exposure_ms)
    actual_exposure = controller.get_exposure_ms()
    emit_func("log", message=f"Helper set exposure: requested={exposure_ms:.3f} ms, actual={actual_exposure:.3f} ms")

    actual_roi = None
    if roi:
        roi_x, roi_y, roi_w, roi_h = (int(value) for value in roi)
        controller.set_roi(roi_x, roi_y, roi_w, roi_h)
        actual_roi = controller.get_roi()
        emit_func("log", message=f"Helper SetROI requested={(roi_x, roi_y, roi_w, roi_h)}, actual={actual_roi}")
    else:
        try:
            controller.clear_roi()
            emit_func("log", message="Helper cleared Hera ROI.")
        except Exception as exc:
            emit_func("log", message=f"Helper ClearROI skipped: {exc}")
        actual_roi = controller.get_roi()

    return hdr_readback, actual_exposure, actual_roi


def wait_for_data_callback(controller, timeout_sec, emit_func=emit):
    result = {"data_handle": None, "data_status": None, "message": ""}
    done = False
    progress_last = {"pct": -1}

    def progress_handler(progress):
        try:
            progress_value = float(progress)
        except Exception:
            return
        fraction = progress_value / 100.0 if progress_value > 1.0 else progress_value
        pct = max(0, min(100, int(round(fraction * 100.0))))
        if pct == progress_last["pct"]:
            return
        progress_last["pct"] = pct
        emit_func("progress", phase="acquiring", percent=pct)

    def data_handler(data_handle, data_status, message):
        nonlocal done
        result["data_handle"] = data_handle
        result["data_status"] = int(data_status)
        result["message"] = message or ""
        done = True

    controller.progress_handler_func = progress_handler
    controller.data_handler_func = data_handler
    controller.unregister_callbacks()
    controller.register_callbacks()

    deadline = time.monotonic() + float(timeout_sec)
    while not done and time.monotonic() < deadline:
        time.sleep(0.05)
    if not done:
        try:
            controller.abort_hyperspectral_acquisition()
        except Exception:
            pass
        raise RuntimeError(f"Timed out waiting for Hera SDK acquisition callback after {timeout_sec:.0f} s.")
    return result


def write_hypercube_cache(controller, hypercube_handle, request, cube_width, cube_height, cube_bands, cube_type, emit_func=emit):
    cache_dir = request["cache_dir"]
    os.makedirs(cache_dir, exist_ok=True)
    request_id = request.get("request_id") or uuid.uuid4().hex
    raw_path = os.path.join(cache_dir, f"{request_id}.bsq")
    wavelengths = []
    bytes_per_value = 4 if int(cube_type) == 0 else 8
    byte_count = int(cube_width) * int(cube_height) * bytes_per_value

    with open(raw_path, "wb") as raw_file:
        for band_index in range(int(cube_bands)):
            wavelength, values = controller.get_hypercube_band_pointer(
                hypercube_handle,
                band_index,
                int(cube_type),
            )
            if int(cube_type) == 0:
                address = ctypes.addressof(values.contents)
            else:
                address = ctypes.addressof(values.contents)
            raw_file.write(ctypes.string_at(address, byte_count))
            wavelengths.append(float(wavelength))
            if band_index == 0 or (band_index + 1) % 25 == 0 or band_index + 1 == int(cube_bands):
                pct = int(round((band_index + 1) * 100 / int(cube_bands)))
                emit_func("progress", phase="writing_cache", percent=pct)

    return raw_path, wavelengths


def run_request(request, emit_func=emit):
    controller = None
    data_handle = None
    hypercube_handle = None
    timings = {}
    try:
        controller = connect_controller(request, emit_func=emit_func)
        licensed_status, licensed, expiry_license, expiry_cert = controller.is_licensed()
        if licensed_status != 0 or not licensed:
            raise RuntimeError("Hera SDK license is not active in helper process.")
        emit_func("log", message=f"Helper license OK. License expiry UTC={expiry_license}, certificate expiry UTC={expiry_cert}")

        hdr_readback, actual_exposure, actual_roi = apply_camera_settings(controller, request, emit_func=emit_func)

        scan_mode = int(request["scan_mode"])
        trigger_mode = int(request["trigger_mode"])
        averages = int(request["averages"])
        stabilization_ms = int(request["stabilization_ms"])
        bands = int(request["bands"])
        binning = int(request["binning"])
        data_type = int(request["data_type"])

        if not controller.is_scan_mode_supported(scan_mode):
            raise RuntimeError(f"Scan mode {scan_mode} is not supported by the connected device.")
        if not controller.is_trigger_mode_supported(trigger_mode):
            raise RuntimeError(f"Trigger mode {trigger_mode} is not supported by the connected device.")

        if controller.is_hdr_supported():
            pre_start_hdr = controller.get_hdr()
        else:
            pre_start_hdr = None
        emit_func("log", message=f"Helper pre-start HDR readback: {pre_start_hdr}")

        start_time = time.perf_counter()
        controller.start_hyperspectral_acquisition(scan_mode, trigger_mode, averages, stabilization_ms)
        emit_func("log", message="Helper started HeraAPI_StartHyperspectralDataAcquisitionEx.")
        callback = wait_for_data_callback(controller, float(request.get("callback_timeout_sec", 900)), emit_func=emit_func)
        timings["acquisition_callback_sec"] = time.perf_counter() - start_time
        emit_func(
            "log",
            message=(
                "Helper acquisition callback received: "
                f"status={callback['data_status']}, elapsed={timings['acquisition_callback_sec']:.2f} s, "
                f"message={callback['message']!r}"
            ),
        )
        if callback["data_status"] != HeraController.HYPERSPECTRAL_DATA_OK:
            raise RuntimeError(callback["message"] or "Hyperspectral acquisition failed in helper process.")
        data_handle = callback["data_handle"]

        raw_info_start = time.perf_counter()
        raw_width, raw_height, _ = controller.get_hyperspectral_data_info(data_handle)
        try:
            data_is_hdr = controller.get_hyperspectral_data_is_hdr(data_handle)
        except Exception:
            data_is_hdr = None
        timings["raw_metadata_sec"] = time.perf_counter() - raw_info_start
        emit_func("log", message=f"Helper raw data: width={raw_width}, height={raw_height}, dataHDR={data_is_hdr}")

        compute_start = time.perf_counter()
        compute_last = {"pct": -1}

        def hypercube_progress(progress):
            try:
                progress_value = float(progress)
            except Exception:
                return
            fraction = progress_value / 100.0 if progress_value > 1.0 else progress_value
            pct = max(0, min(100, int(round(fraction * 100.0))))
            if pct == compute_last["pct"]:
                return
            compute_last["pct"] = pct
            emit_func("progress", phase="computing", percent=pct)

        hypercube_handle = controller.get_hypercube(
            data_handle,
            data_type,
            bands,
            binning,
            progress_handler=hypercube_progress,
        )
        cube_width, cube_height, cube_bands, cube_type = controller.get_hypercube_info(hypercube_handle)
        try:
            cube_is_hdr = controller.get_hypercube_is_hdr(hypercube_handle)
        except Exception:
            cube_is_hdr = None
        timings["hypercube_compute_sec"] = time.perf_counter() - compute_start
        emit_func(
            "log",
            message=(
                "Helper hypercube ready: "
                f"width={cube_width}, height={cube_height}, bands={cube_bands}, "
                f"dataType={cube_type}, cubeHDR={cube_is_hdr}, "
                f"compute={timings['hypercube_compute_sec']:.2f} s"
            ),
        )

        write_start = time.perf_counter()
        cache_path, wavelengths = write_hypercube_cache(
            controller,
            hypercube_handle,
            request,
            cube_width,
            cube_height,
            cube_bands,
            cube_type,
            emit_func=emit_func,
        )
        timings["cache_write_sec"] = time.perf_counter() - write_start
        emit_func("log", message=f"Helper wrote cube cache: {cache_path}")

        return {
            "request_id": request.get("request_id"),
            "role": request.get("role", "sample"),
            "cache_path": cache_path,
            "wavelengths": wavelengths,
            "raw_width": raw_width,
            "raw_height": raw_height,
            "actual_roi": actual_roi,
            "requested_roi": request.get("roi"),
            "cube_width": cube_width,
            "cube_height": cube_height,
            "cube_bands": cube_bands,
            "cube_type": cube_type,
            "data_is_hdr": data_is_hdr,
            "cube_is_hdr": cube_is_hdr,
            "pre_start_hdr": pre_start_hdr,
            "hdr_readback": hdr_readback,
            "actual_exposure_ms": actual_exposure,
            "timings": timings,
        }
    finally:
        if controller is not None:
            if hypercube_handle is not None:
                try:
                    controller.release_hypercube(hypercube_handle)
                except Exception:
                    pass
            if data_handle is not None:
                try:
                    controller.release_hyperspectral_data(data_handle)
                except Exception:
                    pass
            try:
                controller.disconnect()
            except Exception:
                pass
            try:
                controller.release_device()
            except Exception:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run isolated Hera hyperspectral acquisition.")
    parser.add_argument("--request", required=True, help="Path to JSON request file.")
    args = parser.parse_args(argv)
    try:
        request = read_request(args.request)
        result = run_request(request)
        emit("result", result=result)
        return 0
    except Exception as exc:
        emit("error", message=str(exc), traceback=traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
