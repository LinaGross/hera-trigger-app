import math
import os
import threading
import time

from hera_app.controllers import HeraController


class AcquisitionMixin:
    def _set_var_if_changed(self, var, value):
        try:
            if var.get() != value:
                var.set(value)
        except Exception:
            pass

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

    def _put_roi_in_settings(self, settings, roi):
        roi = self._normalize_roi_tuple(roi)
        if not roi:
            return None
        roi_x, roi_y, roi_w, roi_h = roi
        settings["roi_x"] = roi_x
        settings["roi_y"] = roi_y
        settings["roi_w"] = roi_w
        settings["roi_h"] = roi_h
        return roi

    def _clip_roi_to_dimensions(self, roi, width, height):
        roi = self._normalize_roi_tuple(roi)
        if not roi or width <= 0 or height <= 0:
            return None
        roi_x, roi_y, roi_w, roi_h = roi
        roi_x = max(0, min(roi_x, width - 1))
        roi_y = max(0, min(roi_y, height - 1))
        roi_w = max(1, min(roi_w, width - roi_x))
        roi_h = max(1, min(roi_h, height - roi_y))
        return roi_x, roi_y, roi_w, roi_h

    def _scale_roi_to_dimensions(self, roi, source_width, source_height, target_width, target_height):
        if source_width <= 0 or source_height <= 0 or target_width <= 0 or target_height <= 0:
            return None
        roi = self._clip_roi_to_dimensions(roi, source_width, source_height)
        if not roi:
            return None
        roi_x, roi_y, roi_w, roi_h = roi
        left = int(math.floor(roi_x * target_width / source_width))
        top = int(math.floor(roi_y * target_height / source_height))
        right = int(math.ceil((roi_x + roi_w) * target_width / source_width))
        bottom = int(math.ceil((roi_y + roi_h) * target_height / source_height))
        left = max(0, min(left, target_width - 1))
        top = max(0, min(top, target_height - 1))
        right = max(left + 1, min(right, target_width))
        bottom = max(top + 1, min(bottom, target_height))
        return left, top, right - left, bottom - top

    def _actual_camera_roi_looks_like_crop(self, camera_roi, raw_width, raw_height, cube_width, cube_height):
        camera_roi = self._normalize_roi_tuple(camera_roi)
        if not camera_roi:
            return False
        camera_x, camera_y, camera_w, camera_h = camera_roi
        camera_size_matches_data = (raw_width, raw_height) == (camera_w, camera_h) or (cube_width, cube_height) == (camera_w, camera_h)
        if not camera_size_matches_data:
            return False
        return (camera_x, camera_y) != (0, 0)

    def _resolve_hypercube_roi(self, requested_roi, camera_roi, raw_width, raw_height, cube_width, cube_height):
        requested_roi = self._normalize_roi_tuple(requested_roi)
        if not requested_roi:
            return None, None, cube_width, cube_height, "none"

        source_width = raw_width if raw_width > 0 else cube_width
        source_height = raw_height if raw_height > 0 else cube_height
        requested_in_source = self._clip_roi_to_dimensions(requested_roi, source_width, source_height)
        if not requested_in_source:
            return None, None, cube_width, cube_height, "none"

        _, _, requested_w, requested_h = requested_in_source
        if (raw_width, raw_height) == (requested_w, requested_h) or (cube_width, cube_height) == (requested_w, requested_h):
            return None, None, cube_width, cube_height, "camera"
        if self._actual_camera_roi_looks_like_crop(camera_roi, raw_width, raw_height, cube_width, cube_height):
            return None, None, cube_width, cube_height, "camera"

        export_roi = self._scale_roi_to_dimensions(requested_in_source, source_width, source_height, cube_width, cube_height)
        if not export_roi or export_roi == (0, 0, cube_width, cube_height):
            return None, None, cube_width, cube_height, "full"

        _, _, display_w, display_h = export_roi
        return export_roi, export_roi, display_w, display_h, "post_export"

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

        active_roi = self._get_active_roi()
        if active_roi:
            self._put_roi_in_settings(settings, active_roi)
            settings["apply_roi"] = True
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
        if apply_roi:
            active_roi = self._get_active_roi()
            if active_roi:
                self._put_roi_in_settings(settings, active_roi)
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
                    self._safe_after(0, lambda actual_hdr=actual_hdr: self._set_var_if_changed(self.hdr_enabled_var, actual_hdr))
                    self._set_var_async(self.hdr_status_var, "HDR: on" if actual_hdr else "HDR: off")
                    self._log_async(f"Set HDR: requested={'on' if hdr_enabled else 'off'}, actual={'on' if actual_hdr else 'off'}")
                else:
                    if hdr_enabled:
                        raise RuntimeError("HDR was requested, but this Hera device or SDK DLL reports HDR is not supported.")
                    self._safe_after(0, lambda: self._set_var_if_changed(self.hdr_enabled_var, False))
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
                requested_roi = self._normalize_roi_tuple((roi_x, roi_y, roi_w, roi_h))
                if self.controller.is_roi_writable():
                    self.controller.set_roi(roi_x, roi_y, roi_w, roi_h)
                    actual_roi = self.controller.get_roi()
                    actual_roi = self._normalize_roi_tuple(actual_roi)
                    self.last_applied_roi = actual_roi
                    self._log_async(f"Set ROI: requested=({roi_x}, {roi_y}, {roi_w}, {roi_h}), actual={actual_roi}")
                    if actual_roi != (roi_x, roi_y, roi_w, roi_h):
                        self._log_async(
                            "ROI readback differs from the requested ROI. "
                            "The SDK/camera may have rounded or rejected one of the ROI values."
                        )
                else:
                    actual_roi = self.controller.get_roi()
                    actual_roi = self._normalize_roi_tuple(actual_roi)
                    self.last_applied_roi = actual_roi
                    self._log_async(f"ROI is read-only on this device. Current ROI: {actual_roi}")
                active_roi = requested_roi
                if actual_roi == requested_roi or (actual_roi and (actual_roi[0], actual_roi[1]) != (0, 0)):
                    active_roi = actual_roi
                elif actual_roi != requested_roi:
                    self._log_async(
                        f"Hera ROI readback is {actual_roi}; keeping requested ROI {requested_roi} "
                        "so export is cropped to the user-selected region."
                    )
                self._set_active_roi(active_roi)
                try:
                    actual_x, actual_y, actual_w, actual_h = active_roi
                    self._safe_after(
                        0,
                        lambda x=actual_x, y=actual_y, w=actual_w, h=actual_h: self._set_roi_fields(
                            x,
                            y,
                            w,
                            h,
                            update_live=True,
                            selected=True,
                            status=f"ROI: active x={x}, y={y}, w={w}, h={h}",
                        ),
                    )
                except Exception:
                    pass
            else:
                try:
                    self.controller.clear_roi()
                    self._log_async("ROI cleared on Hera; acquisitions will use the full frame.")
                except Exception as exc:
                    self._log_async(f"ROI was not active; could not clear Hera ROI: {exc}")
                self.last_applied_roi = None
                self._log_async("No ROI is active. Use the ROI controls to select, size, clear, or apply a ROI.")

            if bands == 0:
                bands = self.controller.get_default_output_bands(scan_mode)
                self._safe_after(0, lambda bands=bands: self._set_var_if_changed(self.param_vars["bands"], bands))
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
        self.acquisition_camera_roi = None
        if forced_roi is not None:
            forced_roi = self._normalize_roi_tuple(forced_roi)
            self.acquisition_requested_roi = forced_roi
            apply_roi = True
            roi_x, roi_y, roi_w, roi_h = forced_roi
            self.param_vars["roi_x"].set(roi_x)
            self.param_vars["roi_y"].set(roi_y)
            self.param_vars["roi_w"].set(roi_w)
            self.param_vars["roi_h"].set(roi_h)
            self._set_active_roi(forced_roi)
            self.log(f"ROI for this acquisition: {self._format_roi(forced_roi)}.")
        else:
            self.acquisition_requested_roi = self._get_active_roi()
            apply_roi = self.acquisition_requested_roi is not None
            if self.acquisition_requested_roi:
                self.log(f"Selected ROI for exported cube: {self.acquisition_requested_roi}")
            else:
                self.log("No ROI selected for export; hyperspectral cube will use the full returned image.")
        acquisition_hdr_enabled = bool(self.hdr_enabled_var.get())
        self.log(f"HDR mode for acquisition: {'on' if acquisition_hdr_enabled else 'off'}.", detail=True)
        self.log("Preparing Hera camera parameters before starting acquisition.")
        if not self.apply_parameters(restart_live=False, apply_roi=apply_roi, hdr_enabled=acquisition_hdr_enabled):
            if live_was_running and self.controller and self.controller.connected:
                self.start_live_view()
                self.resume_live_after_acquisition = False
            raise RuntimeError("Applying Hera parameters failed.")
        self.acquisition_camera_roi = self._normalize_roi_tuple(self.last_applied_roi) if apply_roi and self.last_applied_roi else None
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
                    self.log(
                        f"HDR re-asserted immediately before acquisition start: camera reports HDR={'on' if hdr_confirmed else 'off (camera reset it)'}",
                        detail=True,
                    )
                    self._set_var_async(self.hdr_status_var, "HDR: on" if hdr_confirmed else "HDR: reset by camera")
            except Exception as exc:
                self.log(f"HDR re-assert before acquisition failed: {exc}", detail=True)

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

    def _bool_var_value(self, attr_name, default=False):
        var = getattr(self, attr_name, None)
        if var is None:
            return default
        try:
            return bool(var.get())
        except Exception:
            return default

    def _export_selection_text(self):
        selected = []
        if self._bool_var_value("export_raw_var", True):
            selected.append("_raw")
        if self._bool_var_value("export_flatfield_var", True):
            selected.append("_ref")
        if self._bool_var_value("export_normalized_var", True):
            selected.append("_nrm")
        return ", ".join(selected) if selected else "none"

    def _validate_auto_save_export_options(self):
        export_raw = self._bool_var_value("export_raw_var", True)
        export_ref = self._bool_var_value("export_flatfield_var", True)
        export_nrm = self._bool_var_value("export_normalized_var", True)
        if not (export_raw or export_ref or export_nrm):
            self.log("Select at least one Data to Export option before starting an auto-saved run.")
            return False
        if export_raw:
            return True
        if (export_ref or export_nrm) and not self.flatfield_hypercube_handle:
            self.log("The saving panel has only _ref/_nrm selected, but no flatfield is loaded. Enable _raw or acquire a flatfield first.")
            return False
        return True

    def _build_acquisition_description(self, cube_hdr_text, cube_is_hdr, role="sample"):
        description = "Generated by AppHeraTriggerPython0417 using Hera SDK and Tango stage control"
        description = f"{description}\nHyperspectral acquisition HDR flag: {cube_hdr_text}"
        if self.acquisition_requested_hdr and cube_is_hdr is False:
            description = f"{description}\nHDR was requested, but SDK returned non-HDR hyperspectral data"
        notes = self.saving_notes_var.get().strip()
        if notes:
            description = f"{description}\nUser notes: {notes}"
        if role == "flatfield":
            description = f"{description}\nFlatfield reference acquisition"
        return description

    def _export_measurement_set(self, hypercube_handle, export_tag, output_dir, description, info):
        export_raw = self._bool_var_value("export_raw_var", True)
        export_ref = self._bool_var_value("export_flatfield_var", True)
        export_nrm = self._bool_var_value("export_normalized_var", True)
        if not (export_raw or export_ref or export_nrm):
            raise RuntimeError("Select at least one Data to Export option before saving.")

        output_base_path, measurement_dir = self._make_measurement_base_path(output_dir, export_tag)
        saved_paths = {}

        if export_raw:
            raw_path = f"{output_base_path}_raw"
            raw_hdr_path = self._export_hypercube_envi_with_roi(
                hypercube_handle,
                raw_path,
                f"{description}\nNative measurement (_raw)",
                info,
                log_label="native measurement",
            )
            saved_paths["raw"] = raw_hdr_path
            self._log_async(f"Exported native measurement (_raw): {raw_hdr_path}")

        flatfield_export_info = self._flatfield_info_for_sample(info)
        flatfield_matches = flatfield_export_info is not None
        if export_ref:
            if flatfield_matches:
                ref_path = f"{output_base_path}_ref"
                ref_hdr_path = self._export_hypercube_envi_with_roi(
                    self.flatfield_hypercube_handle,
                    ref_path,
                    f"{description}\nFlatfield reference (_ref)",
                    flatfield_export_info,
                    log_label="flatfield reference",
                )
                saved_paths["ref"] = ref_hdr_path
                self._log_async(f"Exported flatfield reference (_ref): {ref_hdr_path}")
            elif self.flatfield_hypercube_handle:
                reason = self._flatfield_mismatch_reason(info)
                self._log_async(f"Flatfield (_ref) selected, but the loaded flatfield does not match this cube ({reason}); _ref skipped.")
            else:
                self._log_async("Flatfield (_ref) selected, but no flatfield is loaded; _ref skipped.")

        if export_nrm:
            if flatfield_matches:
                normalized_path = f"{output_base_path}_nrm"
                normalized_description = f"{description}\nNormalized measurement (_nrm): native sample divided by flatfield reference"
                nrm_hdr_path = self._export_normalized_envi_from_cubes(
                    hypercube_handle,
                    self.flatfield_hypercube_handle,
                    normalized_path,
                    normalized_description,
                    info,
                )
                saved_paths["nrm"] = nrm_hdr_path
                self._log_async(f"Exported normalized measurement (_nrm): {nrm_hdr_path}")
            elif self.flatfield_hypercube_handle:
                reason = self._flatfield_mismatch_reason(info)
                self._log_async(f"Normalized (_nrm) selected, but the loaded flatfield does not match this cube ({reason}); _nrm skipped.")
            else:
                self._log_async("Normalized (_nrm) selected, but no flatfield is loaded; _nrm skipped.")

        preferred_path = saved_paths.get("nrm") or saved_paths.get("raw") or saved_paths.get("ref")
        if not preferred_path:
            try:
                os.rmdir(measurement_dir)
            except Exception:
                pass
            raise RuntimeError("No files were exported. Check the selected data types and flatfield compatibility.")
        return preferred_path, saved_paths, measurement_dir

    def _export_flatfield_reference_set(self, hypercube_handle, export_tag, output_dir, description, info):
        output_base_path, measurement_dir = self._make_measurement_base_path(output_dir, export_tag)
        ref_path = f"{output_base_path}_ref"
        ref_hdr_path = self._export_hypercube_envi_with_roi(
            hypercube_handle,
            ref_path,
            f"{description}\nFlatfield reference (_ref)",
            info,
            log_label="flatfield reference",
        )
        saved_paths = {"ref": ref_hdr_path}
        self._log_async(f"Exported flatfield reference (_ref): {ref_hdr_path}")
        return ref_hdr_path, saved_paths, measurement_dir

    def save_pending_acquisition(self):
        ctx = self.pending_save_context
        if not ctx:
            if self.hyper_display_mode_var.get() == "Flatfield" and self.flatfield_hypercube_handle and self.flatfield_info:
                cube_is_hdr = self.flatfield_info.get("is_hdr")
                cube_hdr_text = "unknown" if cube_is_hdr is None else ("on" if cube_is_hdr else "off")
                ctx = {
                    "hypercube_handle": self.flatfield_hypercube_handle,
                    "export_tag": self._sanitize_export_tag(f"flatfield_{time.strftime('%Y%m%d_%H%M%S')}"),
                    "cube_hdr_text": cube_hdr_text,
                    "cube_is_hdr": cube_is_hdr,
                    "info": dict(self.flatfield_info),
                    "role": "flatfield",
                }
            else:
                self.log("No pending acquisition to save.")
                return
        if self.save_pending_button:
            self.save_pending_button.config(state="disabled")
        self.pending_save_context = None
        hypercube_handle = ctx["hypercube_handle"]
        export_tag = self._export_tag_from_panel(ctx["export_tag"])
        cube_hdr_text = ctx["cube_hdr_text"]
        cube_is_hdr = ctx["cube_is_hdr"]
        role = ctx.get("role", "sample")
        sample_info = ctx.get("info") or self.current_hypercube_info

        def _do_save():
            try:
                self._safe_after(0, lambda: self.update_state("Saving"))
                output_dir = self.param_vars["output_path"].get()
                os.makedirs(output_dir, exist_ok=True)
                description = self._build_acquisition_description(cube_hdr_text, cube_is_hdr, role=role)
                if role == "flatfield":
                    hdr_path, saved_paths, measurement_dir = self._export_flatfield_reference_set(
                        hypercube_handle,
                        export_tag,
                        output_dir,
                        description,
                        sample_info,
                    )
                else:
                    hdr_path, saved_paths, measurement_dir = self._export_measurement_set(
                        hypercube_handle,
                        export_tag,
                        output_dir,
                        description,
                        sample_info,
                    )
                self.last_export_path = hdr_path
                self._set_var_async(self.last_export_var, f"Last export: {os.path.basename(hdr_path)}")
                self._log_async(
                    ("Saved flatfield folder: " if role == "flatfield" else "Saved measurement folder: ")
                    +
                    f"{measurement_dir} ({', '.join(sorted(saved_paths))})"
                )
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
                self._log_async(f"Could not read raw hyperspectral HDR flag: {exc}", detail=True)
            data_hdr_text = "unknown" if data_is_hdr is None else ("on" if data_is_hdr else "off")
            self._log_async(f"Raw hyperspectral data received: width={width}, height={height}", detail=True)
            if self.acquisition_requested_hdr and data_is_hdr is False:
                self._log_async(
                    "HDR was requested before acquisition, but the SDK returned non-HDR raw data. "
                    "The device acquisition pipeline may not support HDR for this scan configuration.",
                    detail=True,
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
                self._log_async(f"Could not read hypercube HDR flag: {exc}", detail=True)
            cube_hdr_text = "unknown" if cube_is_hdr is None else ("on" if cube_is_hdr else "off")
            if self.acquisition_requested_hdr and cube_is_hdr is False:
                self._log_async(
                    "HDR was requested, but the computed hypercube reports HDR=off. "
                    "The exported cube is a normal dynamic-range acquisition.",
                    detail=True,
                )
            display_roi, export_roi, display_width, display_height, roi_mode = self._resolve_hypercube_roi(
                self.acquisition_requested_roi,
                self.acquisition_camera_roi,
                width,
                height,
                cube_width,
                cube_height,
            )
            self._set_var_async(
                self.hypercube_summary_var,
                f"Cube: {display_width} x {display_height}, bands={cube_bands}, type={cube_type}"
                + (
                    f" (ROI x={display_roi[0]}, y={display_roi[1]})"
                    if display_roi
                    else (" (camera ROI)" if roi_mode == "camera" else "")
                ),
            )
            self._log_async(
                f"Hypercube ready: width={cube_width}, height={cube_height}, bands={cube_bands}, "
                f"dataType={cube_type}",
                detail=True,
            )
            if roi_mode == "camera":
                self._log_async(
                    f"Hera returned an already-cropped ROI cube for {self.acquisition_requested_roi}; "
                    "export will not be cropped again."
                )
            elif display_roi:
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
                "camera_roi": (self.acquisition_camera_roi or self.acquisition_requested_roi) if roi_mode == "camera" else None,
                "export_roi": export_roi,
                "bands": cube_bands,
                "data_type": cube_type,
                "is_hdr": cube_is_hdr,
                "role": self.pending_acquisition_role,
            }
            self.current_hyper_band_cache = {}
            self.current_hyper_spectrum_cache = {}
            self.current_hyper_pointer_cache = {}
            self.hyper_spectrum_request_ids = {
                key: self.hyper_spectrum_request_ids.get(key, 0) + 1
                for key in ("selected", "cursor", "warmup")
            }
            self.hyper_cursor_spectrum_inflight = False
            self.hyper_cursor_pending_pixel = None
            self.hyper_selected_pixel = None
            self.hyper_cursor_pixel = None
            self.hyper_selected_spectrum = None
            self.hyper_cursor_spectrum = None
            self.hyper_flatfield_spectrum = None
            self.hyper_spectrum_loading = ""
            self.hyper_spectrum_error = ""
            released_as_flatfield = False
            if self.pending_acquisition_role == "flatfield":
                if self.flatfield_hypercube_handle and self.flatfield_hypercube_handle != hypercube_handle:
                    try:
                        with self.hypercube_read_lock:
                            self.controller.release_hypercube(self.flatfield_hypercube_handle)
                        released_as_flatfield = (self.flatfield_hypercube_handle == previous_handle)
                    except Exception:
                        pass
                self.flatfield_hypercube_handle = hypercube_handle
                self.flatfield_info = dict(self.current_hypercube_info)
                self._set_var_async(self.flatfield_status_var, f"Flatfield: {display_width} x {display_height}, bands={cube_bands}")
            elif self.hyper_display_mode_var.get() == "Flatfield":
                next_mode = "Normalized" if self._should_use_flatfield_correction(self.current_hypercube_info) else "Raw"
                self._set_var_async(self.hyper_display_mode_var, next_mode)
            viewer_bound = True
            is_flatfield_acquisition = self.pending_acquisition_role == "flatfield"
            if is_flatfield_acquisition:
                self._safe_after(
                    0,
                    lambda: (
                        self.hyper_band_scale.config(to=max(cube_bands - 1, 0)),
                        self.current_hyper_band_index.set(0),
                        self.current_hyper_band_var.set(f"Band: 1 / {cube_bands}"),
                        self.current_hyper_wavelength_var.set("Wavelength: -"),
                        self._draw_hyperspectral_view_placeholder("Flatfield ready. Press Export to save it as _ref."),
                    ),
                )
                self._log_async("Flatfield cube is ready; deferred preview rendering to avoid a full-frame memory spike.")
            else:
                self._safe_after(
                    0,
                    lambda: (
                        self.hyper_band_scale.config(to=max(cube_bands - 1, 0)),
                        self.current_hyper_band_index.set(0),
                        self.render_current_hyper_band(),
                        self._start_hyper_pointer_cache_warmup(),
                    ),
                )
                self._log_async("Hyperspectral viewer is ready. Open the Hyperspectral View tab and move the band slider.")
            if previous_handle and not released_as_flatfield:
                if previous_handle != self.flatfield_hypercube_handle:
                    try:
                        with self.hypercube_read_lock:
                            self.controller.release_hypercube(previous_handle)
                    except Exception:
                        pass

            if not self.pending_acquisition_auto_save and self.pending_acquisition_role == "flatfield":
                self.pending_save_context = {
                    "hypercube_handle": hypercube_handle,
                    "export_tag": self.pending_export_tag or self._sanitize_export_tag(time.strftime("flatfield_%Y%m%d_%H%M%S")),
                    "requested_roi": self.acquisition_requested_roi,
                    "export_roi": export_roi,
                    "cube_width": cube_width,
                    "cube_height": cube_height,
                    "cube_hdr_text": cube_hdr_text,
                    "cube_is_hdr": cube_is_hdr,
                    "info": dict(self.current_hypercube_info),
                    "role": "flatfield",
                }
                self.acquisition_success = True
                self.last_acquisition_error = ""
                self._set_var_async(self.flatfield_status_var, f"Flatfield: ready ({display_width} x {display_height}, bands={cube_bands})")
                self._set_var_async(self.hyper_display_mode_var, "Flatfield")
                if self.save_pending_button:
                    self._safe_after(0, lambda: self.save_pending_button.config(state="normal"))
                self._log_async("Flatfield acquired and kept in memory. Press Export to save it as _ref.")
                self._safe_after(0, lambda: self.update_state("Completed"))
                return

            if not self.pending_acquisition_auto_save and self.pending_acquisition_role == "sample":
                self.pending_save_context = {
                    "hypercube_handle": hypercube_handle,
                    "export_tag": self.pending_export_tag or self._sanitize_export_tag(time.strftime("hera_hypercube_%Y%m%d_%H%M%S")),
                    "requested_roi": self.acquisition_requested_roi,
                    "export_roi": export_roi,
                    "cube_width": cube_width,
                    "cube_height": cube_height,
                    "cube_hdr_text": cube_hdr_text,
                    "cube_is_hdr": cube_is_hdr,
                    "info": dict(self.current_hypercube_info),
                    "role": "sample",
                }
                self.acquisition_success = True
                self.last_acquisition_error = ""
                if self.save_pending_button:
                    self._safe_after(0, lambda: self.save_pending_button.config(state="normal"))
                self._log_async("Acquisition complete. Press Export to save the selected data products.")
                self._safe_after(0, lambda: self.update_state("Completed"))
                return

            self._safe_after(0, lambda: self.update_state("Saving"))
            output_dir = self.param_vars["output_path"].get()
            os.makedirs(output_dir, exist_ok=True)
            export_tag = self.pending_export_tag or self._sanitize_export_tag(time.strftime("hera_hypercube_%Y%m%d_%H%M%S"))
            description = self._build_acquisition_description(cube_hdr_text, cube_is_hdr, role=self.pending_acquisition_role)
            if self.pending_acquisition_role == "flatfield":
                hdr_path, saved_paths, measurement_dir = self._export_flatfield_reference_set(
                    hypercube_handle,
                    export_tag,
                    output_dir,
                    description,
                    self.current_hypercube_info,
                )
            else:
                hdr_path, saved_paths, measurement_dir = self._export_measurement_set(
                    hypercube_handle,
                    export_tag,
                    output_dir,
                    description,
                    self.current_hypercube_info,
                )
            self.last_export_path = hdr_path
            self._set_var_async(self.last_export_var, f"Last export: {os.path.basename(hdr_path)}")
            self._log_async(
                "Saved measurement folder: "
                f"{measurement_dir} ({', '.join(sorted(saved_paths))})"
            )
            if self.pending_acquisition_role == "flatfield":
                self._set_var_async(self.flatfield_status_var, f"Flatfield: ready ({display_width} x {display_height}, bands={cube_bands})")
                self._log_async("Flatfield baseline is ready and saved as _ref.")
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
                self.current_hyper_pointer_cache = {}
                self.hyper_spectrum_request_ids = {
                    key: self.hyper_spectrum_request_ids.get(key, 0) + 1
                    for key in ("selected", "cursor", "warmup")
                }
                self.hyper_cursor_spectrum_inflight = False
                self.hyper_cursor_pending_pixel = None
                self.hyper_selected_pixel = None
                self.hyper_cursor_pixel = None
                self.hyper_selected_spectrum = None
                self.hyper_cursor_spectrum = None
                self.hyper_flatfield_spectrum = None
                self.hyper_spectrum_loading = ""
                self.hyper_spectrum_error = ""
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
                    with self.hypercube_read_lock:
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
