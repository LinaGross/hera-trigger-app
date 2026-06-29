import argparse
import array
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
    spectral_sampling = int(request.get("spectral_sampling", 0))
    spectral_sampling_name = request.get("spectral_sampling_name") or str(spectral_sampling)
    binning = int(request.get("binning", 0))
    roi = request.get("roi")

    if controller.set_spectral_sampling(spectral_sampling):
        emit_func("log", message=f"Helper set spectral sampling: {spectral_sampling_name}")
    else:
        emit_func("log", message="Helper spectral sampling control is not available; using SDK default.")

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
        divisor = binning_spatial_divisor(binning)
        if divisor > 1:
            adjusted_w = (roi_w // divisor) * divisor
            adjusted_h = (roi_h // divisor) * divisor
            if adjusted_w < divisor or adjusted_h < divisor:
                raise RuntimeError(
                    f"ROI {(roi_x, roi_y, roi_w, roi_h)} is too small for binning factor {binning}."
                )
            if (adjusted_w, adjusted_h) != (roi_w, roi_h):
                emit_func(
                    "log",
                    message=(
                        "Helper adjusted ROI dimensions for binning: "
                        f"{(roi_x, roi_y, roi_w, roi_h)} -> {(roi_x, roi_y, adjusted_w, adjusted_h)}"
                    ),
                )
                roi_w, roi_h = adjusted_w, adjusted_h
        roi_writable = controller.is_roi_writable()
        emit_func("log", message=f"Helper ROI writable before SetROI: {roi_writable}")
        if not roi_writable:
            raise RuntimeError(
                "Hera SDK reports ROI is not writable in the helper process. "
                "ROI-limited acquisition was not started."
            )
        controller.set_roi(roi_x, roi_y, roi_w, roi_h)
        actual_roi = controller.get_roi()
        emit_func("log", message=f"Helper SetROI: requested={(roi_x, roi_y, roi_w, roi_h)}, actual={actual_roi}")
    else:
        try:
            controller.clear_roi()
            emit_func("log", message="Helper cleared Hera ROI.")
        except Exception as exc:
            emit_func("log", message=f"Helper ClearROI skipped: {exc}")
        actual_roi = controller.get_roi()

    return hdr_readback, actual_exposure, actual_roi


def binning_spatial_divisor(binning):
    binning = int(binning)
    if binning == 3:
        return 8
    if binning in (2, 0x1001):
        return 4
    if binning in (1, 0x1000):
        return 2
    return 1


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


def sanitize_export_tag(text):
    keep = []
    for ch in str(text or ""):
        if ch.isalnum() or ch in {"-", "_"}:
            keep.append(ch)
        elif ch in {" ", "."}:
            keep.append("_")
    sanitized = "".join(keep).strip("_")
    return sanitized or "measurement"


def make_measurement_base_path(output_dir, export_tag):
    os.makedirs(output_dir, exist_ok=True)
    base_name = sanitize_export_tag(export_tag)
    folder_name = base_name
    folder_path = os.path.join(output_dir, folder_name)
    suffix = 2
    while os.path.exists(folder_path):
        folder_name = f"{base_name}_{suffix}"
        folder_path = os.path.join(output_dir, folder_name)
        suffix += 1
    os.makedirs(folder_path, exist_ok=True)
    return os.path.join(folder_path, folder_name), folder_path


def envi_data_type(cube_type):
    return 4 if int(cube_type) == 0 else 5


def bytes_per_value(cube_type):
    return 4 if int(cube_type) == 0 else 8


def array_typecode(cube_type):
    return "f" if int(cube_type) == 0 else "d"


def write_envi_header(hdr_path, raw_path, description, samples, lines, bands, data_type, wavelengths):
    safe_description = (description or "Generated by AppHeraTriggerPython0417").replace("}", ")")
    wavelength_text = ", ".join(f"{float(wavelength):.6f}" for wavelength in wavelengths)
    header = (
        "ENVI\n"
        f"description = {{{safe_description}}}\n"
        f"samples = {int(samples)}\n"
        f"lines = {int(lines)}\n"
        f"bands = {int(bands)}\n"
        "header offset = 0\n"
        "file type = ENVI Standard\n"
        f"data file = {os.path.basename(raw_path)}\n"
        f"data type = {int(data_type)}\n"
        "interleave = bsq\n"
        "byte order = 0\n"
        f"wavelength = {{{wavelength_text}}}\n"
    )
    with open(hdr_path, "w", encoding="utf-8") as header_file:
        header_file.write(header)


def atomic_cleanup(*paths):
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def hdr_mode_text(enabled):
    if enabled is None:
        return "unknown"
    return "Dynamic Range 16-bit HDR" if bool(enabled) else "Sensitivity 12-bit"


def build_acquisition_description(request, cube_is_hdr, role="sample"):
    cube_hdr_text = "unknown" if cube_is_hdr is None else ("on" if cube_is_hdr else "off")
    description = "Generated by AppHeraTriggerPython0417 using Hera SDK and Tango stage control"
    description = f"{description}\nHyperspectral acquisition HDR flag: {cube_hdr_text}"
    description = f"{description}\nHyperspectral acquisition mode: {hdr_mode_text(cube_is_hdr)}"
    if bool(request.get("hdr_enabled")) and cube_is_hdr is False:
        description = (
            f"{description}\nDynamic Range HDR was requested, but the authoritative SDK cube flag "
            "returned Sensitivity 12-bit hyperspectral data"
        )
    notes = str(request.get("saving_notes") or "").strip()
    if notes:
        description = f"{description}\nUser notes: {notes}"
    if role == "flatfield":
        description = f"{description}\nFlatfield reference acquisition"
    return description


def normalized_roi(roi):
    if not roi:
        return None
    try:
        values = tuple(int(value) for value in roi)
    except Exception:
        return None
    if len(values) != 4:
        return None
    x, y, w, h = values
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def clip_roi(roi, width, height):
    roi = normalized_roi(roi)
    if not roi or width <= 0 or height <= 0:
        return None
    x, y, w, h = roi
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    return x, y, w, h


def flatfield_alignment_for_sample(sample_info, flat_info):
    if not sample_info or not flat_info:
        return None
    if int(sample_info.get("bands") or 0) != int(flat_info.get("bands") or 0):
        return None
    if int(sample_info.get("data_type") or 0) != int(flat_info.get("data_type") or 0):
        return None

    sample_source_width = int(sample_info.get("source_width") or 0)
    sample_source_height = int(sample_info.get("source_height") or 0)
    flat_source_width = int(flat_info.get("source_width") or 0)
    flat_source_height = int(flat_info.get("source_height") or 0)
    if min(sample_source_width, sample_source_height, flat_source_width, flat_source_height) <= 0:
        return None

    sample_display_roi = normalized_roi(sample_info.get("display_roi"))
    sample_camera_roi = normalized_roi(sample_info.get("camera_roi"))
    flat_camera_roi = normalized_roi(flat_info.get("camera_roi"))

    def make_plan(sample_roi, flat_roi, mode):
        sample_roi = clip_roi(sample_roi, sample_source_width, sample_source_height)
        flat_roi = clip_roi(flat_roi, flat_source_width, flat_source_height)
        if not sample_roi or not flat_roi:
            return None
        sample_x, sample_y, sample_w, sample_h = sample_roi
        flat_x, flat_y, flat_w, flat_h = flat_roi
        width = min(sample_w, flat_w)
        height = min(sample_h, flat_h)
        if width <= 0 or height <= 0:
            return None
        return {
            "mode": mode,
            "sample_source_width": sample_source_width,
            "sample_source_height": sample_source_height,
            "flatfield_source_width": flat_source_width,
            "flatfield_source_height": flat_source_height,
            "sample_roi": (sample_x, sample_y, width, height),
            "flatfield_roi": (flat_x, flat_y, width, height),
            "width": width,
            "height": height,
        }

    if sample_camera_roi and not flat_camera_roi:
        camera_x, camera_y, camera_w, camera_h = sample_camera_roi
        if camera_w > 0 and camera_h > 0:
            scale_x = sample_source_width / camera_w
            scale_y = sample_source_height / camera_h
            flat_x = int(round(camera_x * scale_x))
            flat_y = int(round(camera_y * scale_y))
            plan = make_plan(
                (0, 0, sample_source_width, sample_source_height),
                (flat_x, flat_y, sample_source_width, sample_source_height),
                "sample-camera-roi-flatfield-full-frame",
            )
            if plan:
                return plan

    if sample_camera_roi or flat_camera_roi:
        if sample_camera_roi != flat_camera_roi:
            return None
        if sample_source_width != flat_source_width or sample_source_height != flat_source_height:
            return None

    if sample_source_width != flat_source_width or sample_source_height != flat_source_height:
        return None

    sample_roi = sample_display_roi or (0, 0, sample_source_width, sample_source_height)
    return make_plan(sample_roi, sample_roi, "matching-source")


def read_file_backed_band(flat_info, band_index):
    file_path = flat_info["file_path"]
    width = int(flat_info["source_width"])
    height = int(flat_info["source_height"])
    data_type = int(flat_info["data_type"])
    sample_count = width * height
    byte_count = sample_count * bytes_per_value(data_type)
    with open(file_path, "rb") as flat_file:
        flat_file.seek(int(band_index) * byte_count)
        raw = flat_file.read(byte_count)
    if len(raw) != byte_count:
        raise RuntimeError("File-backed flatfield band is incomplete.")
    values = array.array(array_typecode(data_type))
    values.frombytes(raw)
    return values


def write_sdk_hypercube_envi(controller, hypercube_handle, output_base_path, description, width, height, bands, cube_type, emit_func=emit):
    raw_path = output_base_path
    hdr_path = output_base_path + ".hdr"
    temp_suffix = f".tmp_{uuid.uuid4().hex[:8]}"
    temp_raw_path = raw_path + temp_suffix
    temp_hdr_path = hdr_path + temp_suffix
    wavelengths = []
    byte_count = int(width) * int(height) * bytes_per_value(cube_type)
    final_raw_replaced = False
    try:
        with open(temp_raw_path, "wb") as raw_file:
            for band_index in range(int(bands)):
                wavelength, values = controller.get_hypercube_band_pointer(
                    hypercube_handle,
                    band_index,
                    int(cube_type),
                )
                raw_file.write(ctypes.string_at(ctypes.addressof(values.contents), byte_count))
                wavelengths.append(float(wavelength))
                if band_index == 0 or (band_index + 1) % 25 == 0 or band_index + 1 == int(bands):
                    pct = int(round((band_index + 1) * 100 / int(bands)))
                    emit_func("progress", phase="direct_saving", percent=pct)
        write_envi_header(temp_hdr_path, raw_path, description, width, height, bands, envi_data_type(cube_type), wavelengths)
        os.replace(temp_raw_path, raw_path)
        final_raw_replaced = True
        os.replace(temp_hdr_path, hdr_path)
        return hdr_path
    except Exception:
        atomic_cleanup(temp_raw_path, temp_hdr_path)
        if final_raw_replaced and not os.path.exists(hdr_path):
            atomic_cleanup(raw_path)
        raise


def write_file_backed_hypercube_envi(flat_info, output_base_path, description, roi=None, emit_func=emit):
    raw_path = output_base_path
    hdr_path = output_base_path + ".hdr"
    temp_suffix = f".tmp_{uuid.uuid4().hex[:8]}"
    temp_raw_path = raw_path + temp_suffix
    temp_hdr_path = hdr_path + temp_suffix
    source_width = int(flat_info["source_width"])
    source_height = int(flat_info["source_height"])
    bands = int(flat_info["bands"])
    data_type = int(flat_info["data_type"])
    roi_x, roi_y, roi_w, roi_h = clip_roi(roi, source_width, source_height) or (0, 0, source_width, source_height)
    wavelengths = flat_info.get("wavelengths") or []
    bytes_value = bytes_per_value(data_type)
    source_row_bytes = source_width * bytes_value
    source_band_bytes = source_width * source_height * bytes_value
    row_bytes = roi_w * bytes_value
    final_raw_replaced = False
    try:
        with open(flat_info["file_path"], "rb") as source_file, open(temp_raw_path, "wb") as raw_file:
            for band_index in range(bands):
                band_offset = band_index * source_band_bytes
                for row in range(roi_y, roi_y + roi_h):
                    source_file.seek(band_offset + row * source_row_bytes + roi_x * bytes_value)
                    raw_file.write(source_file.read(row_bytes))
                if band_index == 0 or (band_index + 1) % 25 == 0 or band_index + 1 == bands:
                    pct = int(round((band_index + 1) * 100 / bands))
                    emit_func("progress", phase="direct_saving", percent=pct)
        wavelength_list = [
            float(wavelengths[index]) if index < len(wavelengths) else float(index)
            for index in range(bands)
        ]
        write_envi_header(temp_hdr_path, raw_path, description, roi_w, roi_h, bands, envi_data_type(data_type), wavelength_list)
        os.replace(temp_raw_path, raw_path)
        final_raw_replaced = True
        os.replace(temp_hdr_path, hdr_path)
        return hdr_path
    except Exception:
        atomic_cleanup(temp_raw_path, temp_hdr_path)
        if final_raw_replaced and not os.path.exists(hdr_path):
            atomic_cleanup(raw_path)
        raise


def write_normalized_envi(controller, hypercube_handle, flat_info, alignment, output_base_path, description, bands, cube_type, emit_func=emit):
    raw_path = output_base_path
    hdr_path = output_base_path + ".hdr"
    temp_suffix = f".tmp_{uuid.uuid4().hex[:8]}"
    temp_raw_path = raw_path + temp_suffix
    temp_hdr_path = hdr_path + temp_suffix
    source_width = int(alignment["sample_source_width"])
    source_height = int(alignment["sample_source_height"])
    flat_source_width = int(alignment["flatfield_source_width"])
    roi_x, roi_y, roi_w, roi_h = alignment["sample_roi"]
    flat_roi_x, flat_roi_y, _flat_roi_w, _flat_roi_h = alignment["flatfield_roi"]
    sample_byte_count = source_width * source_height * bytes_per_value(cube_type)
    sample_typecode = array_typecode(cube_type)
    wavelengths = []
    final_raw_replaced = False
    try:
        with open(temp_raw_path, "wb") as raw_file:
            for band_index in range(int(bands)):
                wavelength, sample_pointer = controller.get_hypercube_band_pointer(
                    hypercube_handle,
                    band_index,
                    int(cube_type),
                )
                sample_values = array.array(sample_typecode)
                sample_values.frombytes(
                    ctypes.string_at(ctypes.addressof(sample_pointer.contents), sample_byte_count)
                )
                flat_values = read_file_backed_band(flat_info, band_index)

                for sample_row, flat_row in zip(range(roi_y, roi_y + roi_h), range(flat_roi_y, flat_roi_y + roi_h)):
                    sample_start = sample_row * source_width + roi_x
                    flat_start = flat_row * flat_source_width + flat_roi_x

                    def normalized_row():
                        for offset in range(roi_w):
                            sample = float(sample_values[sample_start + offset])
                            flat = float(flat_values[flat_start + offset])
                            yield sample / flat if abs(flat) > 1e-12 else 0.0

                    array.array("f", normalized_row()).tofile(raw_file)

                wavelengths.append(float(wavelength))
                if band_index == 0 or (band_index + 1) % 10 == 0 or band_index + 1 == int(bands):
                    pct = int(round((band_index + 1) * 100 / int(bands)))
                    emit_func("progress", phase="direct_saving", percent=pct)

        write_envi_header(temp_hdr_path, raw_path, description, roi_w, roi_h, bands, 4, wavelengths)
        os.replace(temp_raw_path, raw_path)
        final_raw_replaced = True
        os.replace(temp_hdr_path, hdr_path)
        return hdr_path
    except Exception:
        atomic_cleanup(temp_raw_path, temp_hdr_path)
        if final_raw_replaced and not os.path.exists(hdr_path):
            atomic_cleanup(raw_path)
        raise


def direct_save_measurement(controller, hypercube_handle, request, cube_width, cube_height, cube_bands, cube_type, cube_is_hdr, actual_roi, emit_func=emit):
    export_raw = bool(request.get("export_raw", True))
    export_ref = bool(request.get("export_ref", False))
    export_nrm = bool(request.get("export_nrm", False))
    if not (export_raw or export_ref or export_nrm):
        raise RuntimeError("No helper direct-save products were selected.")

    output_base_path, measurement_dir = make_measurement_base_path(
        request.get("output_dir") or request["cache_dir"],
        request.get("export_tag") or request.get("request_id") or "measurement",
    )
    description = build_acquisition_description(request, cube_is_hdr, role=request.get("role", "sample"))
    saved_paths = {}
    flat_info = request.get("flatfield") or None
    sample_info = {
        "source_width": int(cube_width),
        "source_height": int(cube_height),
        "camera_roi": list(actual_roi) if actual_roi and request.get("roi") else [],
        "display_roi": [],
        "bands": int(cube_bands),
        "data_type": int(cube_type),
    }
    alignment = flatfield_alignment_for_sample(sample_info, flat_info) if flat_info else None

    if export_raw:
        raw_path = f"{output_base_path}_raw"
        saved_paths["raw"] = write_sdk_hypercube_envi(
            controller,
            hypercube_handle,
            raw_path,
            f"{description}\nNative measurement (_raw)",
            cube_width,
            cube_height,
            cube_bands,
            cube_type,
            emit_func=emit_func,
        )
        emit_func("log", message=f"Helper direct-saved native measurement (_raw): {saved_paths['raw']}")

    if export_ref:
        if alignment:
            ref_path = f"{output_base_path}_ref"
            saved_paths["ref"] = write_file_backed_hypercube_envi(
                flat_info,
                ref_path,
                f"{description}\nFlatfield reference (_ref)",
                alignment["flatfield_roi"],
                emit_func=emit_func,
            )
            emit_func("log", message=f"Helper direct-saved flatfield reference (_ref): {saved_paths['ref']}")
        else:
            emit_func("log", message="Helper direct-save skipped _ref because the flatfield is missing or incompatible.")

    if export_nrm:
        if alignment:
            nrm_path = f"{output_base_path}_nrm"
            saved_paths["nrm"] = write_normalized_envi(
                controller,
                hypercube_handle,
                flat_info,
                alignment,
                nrm_path,
                f"{description}\nNormalized measurement (_nrm): native sample divided by flatfield reference",
                cube_bands,
                cube_type,
                emit_func=emit_func,
            )
            emit_func("log", message=f"Helper direct-saved normalized measurement (_nrm): {saved_paths['nrm']}")
        else:
            emit_func("log", message="Helper direct-save skipped _nrm because the flatfield is missing or incompatible.")

    if not saved_paths:
        try:
            os.rmdir(measurement_dir)
        except Exception:
            pass
        raise RuntimeError("Helper direct-save did not export any files. Check selected products and flatfield compatibility.")

    preferred_path = saved_paths.get("nrm") or saved_paths.get("raw") or saved_paths.get("ref")
    return preferred_path, saved_paths, measurement_dir


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
        spectral_sampling_name = request.get("spectral_sampling_name") or str(request.get("spectral_sampling", 0))

        if not controller.is_scan_mode_supported(scan_mode):
            raise RuntimeError(f"Scan mode {scan_mode} is not supported by the connected device.")
        if not controller.is_trigger_mode_supported(trigger_mode):
            raise RuntimeError(f"Trigger mode {trigger_mode} is not supported by the connected device.")

        if bands <= 0:
            bands = controller.get_default_output_bands(scan_mode)
            emit_func(
                "log",
                message=f"Helper using default bands for current spectral sampling: {bands}",
            )

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

        try:
            hypercube_handle = controller.get_hypercube(
                data_handle,
                data_type,
                bands,
                binning,
                progress_handler=hypercube_progress,
            )
        except RuntimeError as exc:
            if "bandsCount" in str(exc):
                try:
                    default_bands = controller.get_default_output_bands(scan_mode)
                except Exception:
                    default_bands = "unknown"
                raise RuntimeError(
                    f"{exc}. Requested bands={bands}, spectral_sampling={spectral_sampling_name}, "
                    f"SDK default/recommended bands={default_bands}. "
                    "Use Bands=0 for the SDK default, or select Uniform lambda for custom wavelength spacing."
                ) from exc
            raise
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

        if request.get("direct_save"):
            direct_save_start = time.perf_counter()
            preferred_path, saved_paths, measurement_dir = direct_save_measurement(
                controller,
                hypercube_handle,
                request,
                cube_width,
                cube_height,
                cube_bands,
                cube_type,
                cube_is_hdr,
                actual_roi,
                emit_func=emit_func,
            )
            timings["direct_save_sec"] = time.perf_counter() - direct_save_start
            emit_func(
                "log",
                message=(
                    "Helper direct-save complete: "
                    f"{measurement_dir} ({', '.join(sorted(saved_paths))}), "
                    f"elapsed={timings['direct_save_sec']:.2f} s"
                ),
            )
            return {
                "request_id": request.get("request_id"),
                "role": request.get("role", "sample"),
                "direct_saved_paths": saved_paths,
                "preferred_export_path": preferred_path,
                "measurement_dir": measurement_dir,
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
