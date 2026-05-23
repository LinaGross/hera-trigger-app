import os
import threading
import time
import uuid

from hera_app.controllers import HeraController


class AcquisitionMixin:
    def _read_hera_parameter_settings(self):
        return {
            "gain": float(self.param_vars["gain"].get()),
            "exposure_ms": float(self.param_vars["exposure"].get()),
            "roi_x": int(self.param_vars["roi_x"].get()),
            "roi_y": int(self.param_vars["roi_y"].get()),
            "roi_w": int(self.param_vars["roi_w"].get()),
            "roi_h": int(self.param_vars["roi_h"].get()),
            "apply_roi": False,
            "hdr_enabled": bool(self.hdr_enabled_var.get()),
            "scan_mode_name": self.param_vars["scan_mode"].get(),
            "trigger_mode_name": self.param_vars["trigger_mode"].get(),
            "bands": int(self.param_vars["bands"].get()),
        }

    def apply_parameters_async(self):
        if not self.parameter_apply_lock.acquire(blocking=False):
            self.log("Hera parameter apply is already running.")
            return
        try:
            settings = self._read_hera_parameter_settings()
        except Exception as exc:
            self.parameter_apply_lock.release()
            self.log(f"Failed to read Hera parameters from the UI: {exc}")
            self.update_state("Error")
            return

        self.log("Applying Hera parameters...")

        def worker():
            try:
                self._apply_parameters_from_settings(settings, restart_live=True)
            finally:
                self.parameter_apply_lock.release()

        threading.Thread(target=worker, daemon=True).start()

    def apply_parameters(self, restart_live=True, apply_roi=False, hdr_enabled=None):
        try:
            settings = self._read_hera_parameter_settings()
        except Exception as exc:
            self.log(f"Failed to read Hera parameters from the UI: {exc}")
            self.update_state("Error")
            return False
        settings["apply_roi"] = bool(apply_roi)
        if hdr_enabled is not None:
            settings["hdr_enabled"] = bool(hdr_enabled)
        return self._apply_parameters_from_settings(settings, restart_live=restart_live)

    def _apply_parameters_from_settings(self, settings, restart_live=True):
        if not self.controller or not self.controller.connected:
            self._log_async("Connect to Hera before applying parameters.")
            return False
        live_was_running = False
        try:
            live_was_running = self.controller.is_live_capturing()
            if live_was_running:
                self._log_async("Stopping Hera live view before applying camera parameters.")
                self.controller.stop_live_capture(silent=True)
                self.controller.wait_for_live_capture_stopped(timeout_sec=5.0)
                self.controller.unregister_live_callbacks()
                self._safe_after(0, self._clear_live_view_frame_state)
                self._safe_after(0, lambda: self._set_live_view_status("Live view: stopped"))
                self._safe_after(0, self._draw_live_view_placeholder)

            gain = settings["gain"]
            exposure_ms = settings["exposure_ms"]
            roi_x = settings["roi_x"]
            roi_y = settings["roi_y"]
            roi_w = settings["roi_w"]
            roi_h = settings["roi_h"]
            apply_roi = settings.get("apply_roi", False)
            hdr_enabled = settings.get("hdr_enabled", False)
            scan_mode_name = settings["scan_mode_name"]
            trigger_mode_name = settings["trigger_mode_name"]
            scan_mode = self.SCAN_MODES[scan_mode_name]
            trigger_mode = self.TRIGGER_MODES[trigger_mode_name]
            bands = settings["bands"]

            if not self.controller.is_scan_mode_supported(scan_mode):
                raise RuntimeError(f"Scan mode '{scan_mode_name}' is not supported by the connected device.")
            if not self.controller.is_trigger_mode_supported(trigger_mode):
                raise RuntimeError(f"Trigger mode '{trigger_mode_name}' is not supported by the connected device.")

            try:
                if self.controller.is_hdr_supported():
                    self.controller.set_hdr(hdr_enabled)
                    actual_hdr = self.controller.get_hdr()
                    time.sleep(0.2)
                    self._safe_after(0, lambda actual_hdr=actual_hdr: self.hdr_enabled_var.set(actual_hdr))
                    self._set_var_async(self.hdr_status_var, "HDR: on" if actual_hdr else "HDR: off")
                    self._log_async(f"Set HDR: requested={'on' if hdr_enabled else 'off'}, actual={'on' if actual_hdr else 'off'}")
                else:
                    if hdr_enabled:
                        raise RuntimeError("HDR was requested, but this Hera device or SDK DLL reports HDR is not supported.")
                    self._safe_after(0, lambda: self.hdr_enabled_var.set(False))
                    self._set_var_async(self.hdr_status_var, "HDR: not supported")
            except Exception as exc:
                self._log_async(f"HDR mode was not changed: {exc}")
                if hdr_enabled:
                    raise
                self._set_var_async(self.hdr_status_var, "HDR: check failed")

            if self.controller.is_gain_writable():
                try:
                    resolution = self.controller.get_gain_resolution()
                    if resolution > 0:
                        steps = round(gain / resolution)
                        gain = min(1.0, max(0.0, steps * resolution))
                    self.controller.set_gain(gain)
                    actual_gain = self.controller.get_gain()
                    self._log_async(f"Set gain level: requested={gain:.6f}, actual={actual_gain:.6f}")
                except Exception as exc:
                    current_gain = self.controller.get_gain()
                    self._log_async(f"Gain was not changed: {exc}. Current gain level remains {current_gain:.6f}")
            else:
                current_gain = self.controller.get_gain()
                self._log_async(f"Gain is read-only on this device. Current gain level: {current_gain:.6f}")

            if self.controller.is_exposure_writable():
                self.controller.set_exposure_ms(exposure_ms)
                actual_exposure = self.controller.get_exposure_ms()
                self._log_async(f"Set exposure: requested={exposure_ms} ms, actual={actual_exposure:.3f} ms")
            else:
                actual_exposure = self.controller.get_exposure_ms()
                self._log_async(f"Exposure is read-only on this device. Current exposure: {actual_exposure:.3f} ms")

            if apply_roi:
                if self.controller.is_roi_writable():
                    self.controller.set_roi(roi_x, roi_y, roi_w, roi_h)
                    actual_roi = self.controller.get_roi()
                    self.last_applied_roi = actual_roi
                    self._log_async(f"Set ROI: requested=({roi_x}, {roi_y}, {roi_w}, {roi_h}), actual={actual_roi}")
                    if actual_roi != (roi_x, roi_y, roi_w, roi_h):
                        self._log_async(
                            "ROI readback differs from the requested ROI. "
                            "The SDK/camera may have rounded or rejected one of the ROI values."
                        )
                else:
                    actual_roi = self.controller.get_roi()
                    self.last_applied_roi = actual_roi
                    self._log_async(f"ROI is read-only on this device. Current ROI: {actual_roi}")
                try:
                    actual_x, actual_y, actual_w, actual_h = actual_roi
                    if self.roi_selection_active:
                        self._log_async(
                            f"Keeping selected export ROI {self.selected_export_roi}; camera ROI readback is "
                            f"({actual_x}, {actual_y}, {actual_w}, {actual_h})."
                        )
                    else:
                        self._safe_after(0, lambda x=actual_x, y=actual_y, w=actual_w, h=actual_h: self._set_roi_fields(x, y, w, h, update_live=True))
                except Exception:
                    pass
            else:
                self.last_applied_roi = None
                self._log_async("ROI was not changed. Use the ROI controls to select, size, clear, or apply a ROI.")

            if bands == 0:
                bands = self.controller.get_default_output_bands(scan_mode)
                self._safe_after(0, lambda bands=bands: self.param_vars["bands"].set(bands))
                self._log_async(f"Using default bands for scan mode {scan_mode_name}: {bands}")

            self._safe_after(0, lambda: self.update_state("Ready"))
            self._log_async("Acquisition parameters applied.")
            return True
        except Exception as exc:
            self._log_async(f"Failed to apply parameters: {exc}")
            self._safe_after(0, lambda: self.update_state("Error"))
            return False
        finally:
            if restart_live and live_was_running and self.controller and self.controller.connected:
                self._log_async("Restarting Hera live view after applying parameters.")
                self._safe_after(0, self.start_live_view)

    def _arm_and_start_acquisition(self, export_tag=None, acquisition_role="sample", forced_roi=None, auto_save=True):
        if not self.controller or not self.controller.connected:
            raise RuntimeError("Connect to Hera before starting acquisition.")
        if not self.check_license_status(allow_cached=True):
            raise RuntimeError("Hera SDK license is not active.")
        if self.controller.is_acquiring():
            raise RuntimeError("The device is already acquiring.")

        live_was_running = self.controller.is_live_capturing()
        self.resume_live_after_acquisition = live_was_running
        if forced_roi is not None:
            self.acquisition_requested_roi = forced_roi
            apply_roi = True
            roi_x, roi_y, roi_w, roi_h = forced_roi
            self.param_vars["roi_x"].set(roi_x)
            self.param_vars["roi_y"].set(roi_y)
            self.param_vars["roi_w"].set(roi_w)
            self.param_vars["roi_h"].set(roi_h)
            self.log(f"ROI for this acquisition (from timelapse): {forced_roi}.")
        else:
            self.acquisition_requested_roi = self.selected_export_roi if self.roi_selection_active else None
            apply_roi = self.roi_selection_active
            if self.acquisition_requested_roi:
                self.log(f"Selected ROI for exported cube: {self.acquisition_requested_roi}")
            else:
                self.log("No ROI selected for export; hyperspectral cube will use the full returned image.")
        acquisition_hdr_enabled = bool(self.hdr_enabled_var.get())
        self.log(f"HDR mode for acquisition: {'on' if acquisition_hdr_enabled else 'off'}.")
        self.log("Preparing Hera camera parameters before starting acquisition.")
        if not self.apply_parameters(restart_live=False, apply_roi=apply_roi, hdr_enabled=acquisition_hdr_enabled):
            if live_was_running and self.controller and self.controller.connected:
                self.start_live_view()
                self.resume_live_after_acquisition = False
            raise RuntimeError("Applying Hera parameters failed.")
        if self.acquisition_requested_roi:
            try:
                self.log(f"Camera ROI after parameter apply: {self.controller.get_roi()}")
            except Exception as exc:
                self.log(f"Could not read camera ROI after parameter apply: {exc}")

        scan_mode = self.SCAN_MODES[self.param_vars["scan_mode"].get()]
        trigger_mode_name = self.param_vars["trigger_mode"].get()
        trigger_mode = self.TRIGGER_MODES[trigger_mode_name]
        averages = int(self.param_vars["averages"].get())
        stabilization = int(self.param_vars["stabilization"].get())
        self.acquisition_requested_hdr = acquisition_hdr_enabled

        self.acquisition_done_event.clear()
        self.acquisition_success = False
        self.last_export_path = ""
        self.last_acquisition_error = ""
        self.pending_export_tag = export_tag
        self.pending_acquisition_role = acquisition_role
        self.pending_acquisition_auto_save = auto_save
        self.pending_save_context = None
        if self.save_pending_button:
            self.save_pending_button.config(state="disabled")

        if acquisition_hdr_enabled:
            try:
                if self.controller.is_hdr_supported():
                    self.controller.set_hdr(True)
                    time.sleep(0.3)
                    hdr_confirmed = self.controller.get_hdr()
                    self.log(f"HDR re-asserted immediately before acquisition start: camera reports HDR={'on' if hdr_confirmed else 'off (camera reset it)'}")
                    self._set_var_async(self.hdr_status_var, "HDR: on" if hdr_confirmed else "HDR: reset by camera")
            except Exception as exc:
                self.log(f"HDR re-assert before acquisition failed: {exc}")

        if trigger_mode_name == "Internal":
            if acquisition_role == "flatfield":
                self.log("Sending software flatfield acquisition command through Hera SDK.")
            else:
                self.log("Sending software acquisition command through Hera SDK.")
        else:
            self.log(f"Arming Hera SDK acquisition with trigger mode '{trigger_mode_name}'.")

        self.controller.start_hyperspectral_acquisition(
            scan_mode,
            trigger_mode,
            averages,
            stabilization,
        )
        self.update_state("Acquiring" if trigger_mode_name == "Internal" else "WaitingForTrigger")
        self.log("Hyperspectral acquisition started.")

    def start_acquisition(self):
        try:
            tag = self._sanitize_export_tag(f"manual_{time.strftime('%Y%m%d_%H%M%S')}")
            self._arm_and_start_acquisition(export_tag=tag, acquisition_role="sample", auto_save=False)
        except Exception as exc:
            self.log(f"Failed to start acquisition: {exc}")
            self.update_state("Error")

    def save_pending_acquisition(self):
        ctx = self.pending_save_context
        if not ctx:
            self.log("No pending acquisition to save.")
            return
        if self.save_pending_button:
            self.save_pending_button.config(state="disabled")
        self.pending_save_context = None
        hypercube_handle = ctx["hypercube_handle"]
        export_tag = ctx["export_tag"]
        requested_roi = ctx["requested_roi"]
        cube_width = ctx["cube_width"]
        cube_height = ctx["cube_height"]
        cube_hdr_text = ctx["cube_hdr_text"]
        cube_is_hdr = ctx["cube_is_hdr"]

        def _do_save():
            try:
                self._safe_after(0, lambda: self.update_state("Saving"))
                output_dir = self.param_vars["output_path"].get()
                os.makedirs(output_dir, exist_ok=True)
                output_path = os.path.join(output_dir, export_tag)
                description = "Generated by AppHeraTriggerPython0417 using Hera SDK and Tango stage control"
                description = f"{description}\nHyperspectral acquisition HDR flag: {cube_hdr_text}"
                if self.acquisition_requested_hdr and cube_is_hdr is False:
                    description = f"{description}\nHDR was requested, but SDK returned non-HDR hyperspectral data"
                notes = self.saving_notes_var.get().strip()
                if notes:
                    description = f"{description}\nUser notes: {notes}"
                should_crop_export = False
                if requested_roi:
                    roi_x, roi_y, roi_w, roi_h = requested_roi
                    should_crop_export = (roi_x, roi_y, roi_w, roi_h) != (0, 0, cube_width, cube_height)
                if should_crop_export:
                    temp_output_path = f"{output_path}_fullframe_tmp_{uuid.uuid4().hex[:8]}"
                    self.controller.export_hypercube_envi(hypercube_handle, temp_output_path, description)
                    self._wait_for_export_files(temp_output_path)
                    crop_description = (
                        f"{description}\n"
                        f"Post-export ROI crop: x={requested_roi[0]}, y={requested_roi[1]}, "
                        f"width={requested_roi[2]}, height={requested_roi[3]}"
                    )
                    hdr_path = self._crop_exported_envi_to_roi(temp_output_path, output_path, requested_roi, crop_description)
                    self._remove_export_files(temp_output_path)
                else:
                    self.controller.export_hypercube_envi(hypercube_handle, output_path, description)
                    hdr_path = self._wait_for_export_files(output_path)
                self.last_export_path = hdr_path
                self._set_var_async(self.last_export_var, f"Last export: {os.path.basename(hdr_path)}")
                self._log_async(f"Saved hypercube: {hdr_path}")
                if self._should_use_flatfield_correction(self.current_hypercube_info):
                    normalized_path = f"{output_path}_nrm"
                    normalized_description = f"{description}\nNormalized measurement: native sample divided by flatfield reference"
                    nrm_hdr_path = self._export_normalized_envi_from_cubes(
                        hypercube_handle,
                        self.flatfield_hypercube_handle,
                        normalized_path,
                        normalized_description,
                        self.current_hypercube_info,
                    )
                    self._log_async(f"Exported flatfield-normalized hypercube: {nrm_hdr_path}")
                elif self.flatfield_hypercube_handle and not self.use_flatfield_var.get():
                    self._log_async("Flatfield correction is off; saved native hypercube only.")
                elif self.flatfield_hypercube_handle:
                    self._log_async("Flatfield present but does not match this cube; normalized export skipped.")
                self._safe_after(0, lambda: self.update_state("Completed"))
            except Exception as exc:
                self._log_async(f"Save failed: {exc}")
                self._safe_after(0, lambda: self.update_state("Error"))

        threading.Thread(target=_do_save, daemon=True).start()

    def abort_acquisition(self):
        if not self.controller or not self.controller.connected:
            self.log("Connect to Hera before aborting acquisition.")
            return
        try:
            self.controller.abort_hyperspectral_acquisition()
            self.log("Abort request sent to Hera SDK.")
            self.update_state("Ready")
            self.acquisition_done_event.set()
            if self.resume_live_after_acquisition:
                self.resume_live_after_acquisition = False
                self.start_live_view()
        except Exception as exc:
            self.log(f"Failed to abort acquisition: {exc}")
            self.update_state("Error")

    def _await_acquisition_completion(self, timeout_sec=300):
        if not self.acquisition_done_event.wait(timeout=timeout_sec):
            raise RuntimeError("Timed out waiting for Hera acquisition to complete.")
        if not self.acquisition_success:
            raise RuntimeError(self.last_acquisition_error or "Hera acquisition failed.")
        return self.last_export_path

    def on_progress_update(self, progress):
        def update():
            if self.is_closing:
                return
            if self.app_state == self.STATE_LABELS["WaitingForTrigger"] and progress > 0:
                self.update_state("Acquiring")
            pct = int(progress * 100)
            if pct >= 99:
                progress_notice = "Acquiring: finishing"
            elif pct >= 50:
                progress_notice = "Acquiring: halfway"
            elif pct > 0:
                progress_notice = "Acquiring..."
            else:
                return
            if getattr(self, "last_acquisition_progress_notice", None) != progress_notice:
                self.last_acquisition_progress_notice = progress_notice
                self.log(progress_notice)

        self._safe_after(0, update)

    def on_hyperspectral_data_acquired(self, data_handle, data_status, message):
        self._safe_after(0, lambda: self._start_data_processing(data_handle, data_status, message))

    def _start_data_processing(self, data_handle, data_status, message):
        self.log(f'Acquisition callback received: status={data_status}, message="{message}"')
        if data_status != HeraController.HYPERSPECTRAL_DATA_OK:
            self.last_acquisition_error = message or "Hyperspectral acquisition failed or was aborted."
            self.acquisition_success = False
            self.acquisition_done_event.set()
            self.log(self.last_acquisition_error)
            self.update_state("Error")
            return

        if not self.processing_lock.acquire(blocking=False):
            self.last_acquisition_error = "Processing is already running for a previous acquisition."
            self.acquisition_success = False
            self.acquisition_done_event.set()
            self.log(self.last_acquisition_error)
            return

        self.update_state("ComputingHypercube")
        worker = threading.Thread(target=self._process_acquisition_worker, args=(data_handle,), daemon=True)
        worker.start()

    def _process_acquisition_worker(self, data_handle):
        hypercube_handle = None
        viewer_bound = False
        try:
            width, height, _ = self.controller.get_hyperspectral_data_info(data_handle)
            data_is_hdr = None
            try:
                data_is_hdr = self.controller.get_hyperspectral_data_is_hdr(data_handle)
            except Exception as exc:
                self._log_async(f"Could not read raw hyperspectral HDR flag: {exc}")
            data_hdr_text = "unknown" if data_is_hdr is None else ("on" if data_is_hdr else "off")
            self._log_async(f"Raw hyperspectral data received: width={width}, height={height}, HDR={data_hdr_text}")
            if self.acquisition_requested_hdr and data_is_hdr is False:
                self._log_async(
                    "HDR was requested before acquisition, but the SDK returned non-HDR raw data. "
                    "The device acquisition pipeline may not support HDR for this scan configuration."
                )
            if self.last_applied_roi:
                roi_x, roi_y, roi_w, roi_h = self.last_applied_roi
                if (width, height) != (roi_w, roi_h):
                    self._log_async(
                        "Warning: raw hyperspectral data size does not match the camera ROI "
                        f"({roi_w} x {roi_h} at x={roi_x}, y={roi_y}). "
                        f"The SDK returned {width} x {height}."
                    )

            bands = int(self.param_vars["bands"].get())
            binning = self.BINNING_OPTIONS[self.param_vars["binning"].get()]
            data_type = self.DATA_TYPES[self.param_vars["data_type"].get()]

            hypercube_handle = self.controller.get_hypercube(data_handle, data_type, bands, binning)
            cube_width, cube_height, cube_bands, cube_type = self.controller.get_hypercube_info(hypercube_handle)
            cube_is_hdr = None
            try:
                cube_is_hdr = self.controller.get_hypercube_is_hdr(hypercube_handle)
            except Exception as exc:
                self._log_async(f"Could not read hypercube HDR flag: {exc}")
            cube_hdr_text = "unknown" if cube_is_hdr is None else ("on" if cube_is_hdr else "off")
            if self.acquisition_requested_hdr and cube_is_hdr is False:
                self._log_async(
                    "HDR was requested, but the computed hypercube reports HDR=off. "
                    "The exported cube is a normal dynamic-range acquisition."
                )
            display_roi = None
            display_width = cube_width
            display_height = cube_height
            if self.acquisition_requested_roi:
                roi_x, roi_y, roi_w, roi_h = self.acquisition_requested_roi
                roi_x = max(0, min(int(roi_x), cube_width - 1))
                roi_y = max(0, min(int(roi_y), cube_height - 1))
                roi_w = max(1, min(int(roi_w), cube_width - roi_x))
                roi_h = max(1, min(int(roi_h), cube_height - roi_y))
                display_roi = (roi_x, roi_y, roi_w, roi_h)
                display_width = roi_w
                display_height = roi_h
            self._set_var_async(
                self.hypercube_summary_var,
                f"Cube: {display_width} x {display_height}, bands={cube_bands}, type={cube_type}"
                + f", HDR={cube_hdr_text}"
                + (f" (ROI x={display_roi[0]}, y={display_roi[1]})" if display_roi else ""),
            )
            self._log_async(
                f"Hypercube ready: width={cube_width}, height={cube_height}, bands={cube_bands}, "
                f"dataType={cube_type}, HDR={cube_hdr_text}"
            )
            if display_roi:
                self._log_async(
                    f"Hyperspectral viewer will display selected ROI: x={display_roi[0]}, y={display_roi[1]}, "
                    f"width={display_roi[2]}, height={display_roi[3]}"
                )
            previous_handle = self.current_hypercube_handle
            self.current_hypercube_handle = hypercube_handle
            self.current_hypercube_info = {
                "width": display_width,
                "height": display_height,
                "source_width": cube_width,
                "source_height": cube_height,
                "display_roi": display_roi,
                "bands": cube_bands,
                "data_type": cube_type,
                "is_hdr": cube_is_hdr,
                "role": self.pending_acquisition_role,
            }
            self.current_hyper_band_cache = {}
            self.current_hyper_spectrum_cache = {}
            self.hyper_selected_pixel = None
            released_as_flatfield = False
            if self.pending_acquisition_role == "flatfield":
                if self.flatfield_hypercube_handle and self.flatfield_hypercube_handle != hypercube_handle:
                    try:
                        self.controller.release_hypercube(self.flatfield_hypercube_handle)
                        released_as_flatfield = (self.flatfield_hypercube_handle == previous_handle)
                    except Exception:
                        pass
                self.flatfield_hypercube_handle = hypercube_handle
                self.flatfield_info = dict(self.current_hypercube_info)
                self._set_var_async(self.flatfield_status_var, f"Flatfield: {display_width} x {display_height}, bands={cube_bands}")
            viewer_bound = True
            self._safe_after(
                0,
                lambda: (
                    self.hyper_band_scale.config(to=max(cube_bands - 1, 0)),
                    self.current_hyper_band_index.set(0),
                    self.render_current_hyper_band(),
                ),
            )
            self._log_async("Hyperspectral viewer is ready. Open the Hyperspectral View tab and move the band slider.")
            if previous_handle and not released_as_flatfield:
                if previous_handle != self.flatfield_hypercube_handle:
                    try:
                        self.controller.release_hypercube(previous_handle)
                    except Exception:
                        pass

            if not self.pending_acquisition_auto_save and self.pending_acquisition_role == "sample":
                self.pending_save_context = {
                    "hypercube_handle": hypercube_handle,
                    "export_tag": self.pending_export_tag or self._sanitize_export_tag(time.strftime("hera_hypercube_%Y%m%d_%H%M%S")),
                    "requested_roi": self.acquisition_requested_roi,
                    "cube_width": cube_width,
                    "cube_height": cube_height,
                    "cube_hdr_text": cube_hdr_text,
                    "cube_is_hdr": cube_is_hdr,
                }
                self.acquisition_success = True
                self.last_acquisition_error = ""
                if self.save_pending_button:
                    self._safe_after(0, lambda: self.save_pending_button.config(state="normal"))
                self._log_async("Acquisition complete. Press Export to save the native hypercube.")
                self._safe_after(0, lambda: self.update_state("Completed"))
                return

            self._safe_after(0, lambda: self.update_state("Saving"))
            output_dir = self.param_vars["output_path"].get()
            os.makedirs(output_dir, exist_ok=True)
            export_tag = self.pending_export_tag or self._sanitize_export_tag(time.strftime("hera_hypercube_%Y%m%d_%H%M%S"))
            output_path = os.path.join(output_dir, export_tag)
            description = "Generated by AppHeraTriggerPython0417 using Hera SDK and Tango stage control"
            description = f"{description}\nHyperspectral acquisition HDR flag: {cube_hdr_text}"
            if self.acquisition_requested_hdr and cube_is_hdr is False:
                description = f"{description}\nHDR was requested, but SDK returned non-HDR hyperspectral data"
            notes = self.saving_notes_var.get().strip()
            if notes:
                description = f"{description}\nUser notes: {notes}"
            if self.pending_acquisition_role == "flatfield":
                description = f"{description}\nFlatfield reference acquisition"
            requested_roi = self.acquisition_requested_roi
            should_crop_export = False
            if requested_roi:
                roi_x, roi_y, roi_w, roi_h = requested_roi
                should_crop_export = (roi_x, roi_y, roi_w, roi_h) != (0, 0, cube_width, cube_height)

            if should_crop_export:
                temp_output_path = f"{output_path}_fullframe_tmp_{uuid.uuid4().hex[:8]}"
                self._log_async(
                    "ROI diagnostic: SDK hypercube is "
                    f"{cube_width} x {cube_height}; selected ROI is "
                    f"x={requested_roi[0]}, y={requested_roi[1]}, w={requested_roi[2]}, h={requested_roi[3]}. "
                    "Exporting full cube temporarily, then cropping ENVI output on disk."
                )
                self.controller.export_hypercube_envi(hypercube_handle, temp_output_path, description)
                self._wait_for_export_files(temp_output_path)
                crop_description = (
                    f"{description}\n"
                    f"Post-export ROI crop: x={requested_roi[0]}, y={requested_roi[1]}, "
                    f"width={requested_roi[2]}, height={requested_roi[3]}"
                )
                hdr_path = self._crop_exported_envi_to_roi(temp_output_path, output_path, requested_roi, crop_description)
                self._remove_export_files(temp_output_path)
                self._log_async(f"Exported ROI-cropped hypercube and confirmed files: {hdr_path}")
            else:
                if requested_roi:
                    self._log_async("ROI diagnostic: SDK hypercube already matches the selected ROI size; exporting directly.")
                self.controller.export_hypercube_envi(hypercube_handle, output_path, description)
                hdr_path = self._wait_for_export_files(output_path)
            self.last_export_path = hdr_path
            self._set_var_async(self.last_export_var, f"Last export: {os.path.basename(hdr_path)}")
            self._log_async(f"Exported hypercube and confirmed files: {hdr_path}")
            if self.pending_acquisition_role == "flatfield":
                self._set_var_async(self.flatfield_status_var, f"Flatfield: ready ({display_width} x {display_height}, bands={cube_bands})")
                self._log_async("Flatfield baseline is ready. Enable Use flatfield correction to add normalized exports for compatible sample cubes.")
            elif self._should_use_flatfield_correction(self.current_hypercube_info):
                normalized_path = f"{output_path}_nrm"
                normalized_description = f"{description}\nNormalized measurement: native sample divided by flatfield reference"
                nrm_hdr_path = self._export_normalized_envi_from_cubes(
                    hypercube_handle,
                    self.flatfield_hypercube_handle,
                    normalized_path,
                    normalized_description,
                    self.current_hypercube_info,
                )
                self._log_async(f"Exported flatfield-normalized hypercube: {nrm_hdr_path}")
            elif self.flatfield_hypercube_handle and not self.use_flatfield_var.get():
                self._log_async("Flatfield correction is off; exported native hypercube only.")
            elif self.flatfield_hypercube_handle:
                self._log_async("Flatfield is present but does not match this cube dimensions/ROI/bands; normalized export skipped.")
            self.acquisition_success = True
            self.last_acquisition_error = ""
            self._safe_after(0, lambda: self.update_state("Completed"))
        except Exception as exc:
            self.last_acquisition_error = str(exc)
            self.acquisition_success = False
            self._log_async(f"Failed to process hyperspectral data: {exc}")
            if viewer_bound and self.current_hypercube_handle == hypercube_handle:
                self.current_hypercube_handle = None
                self.current_hypercube_info = None
                self.current_hyper_band_cache = {}
                self.current_hyper_spectrum_cache = {}
                self.hyper_selected_pixel = None
                self._safe_after(
                    0,
                    lambda: (
                        self.hyper_band_scale.config(to=0),
                        self.current_hyper_band_index.set(0),
                        self.current_hyper_band_var.set("Band: -"),
                        self.current_hyper_wavelength_var.set("Wavelength: -"),
                        self._draw_hyperspectral_view_placeholder(),
                    ),
                )
            if hypercube_handle:
                try:
                    self.controller.release_hypercube(hypercube_handle)
                except Exception:
                    pass
            self._safe_after(0, lambda: self.update_state("Error"))
        finally:
            if data_handle:
                self.controller.release_hyperspectral_data(data_handle)
            self.acquisition_done_event.set()
            self.processing_lock.release()
            if self.resume_live_after_acquisition:
                self.resume_live_after_acquisition = False
                self._safe_after(0, self.start_live_view)
