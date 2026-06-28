import json
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from hera_app.controllers import HeraController


class AcquisitionMixin:
    def _set_run_progress(self, text=None, percent=None, mode="determinate"):
        def update():
            if getattr(self, "is_closing", False):
                return
            progressbar = getattr(self, "run_progressbar", None)
            mode_name = "indeterminate" if mode == "indeterminate" else "determinate"
            current_mode = getattr(self, "run_progress_mode", "determinate")
            if progressbar:
                if current_mode == "indeterminate" and mode_name != "indeterminate":
                    try:
                        progressbar.stop()
                    except Exception:
                        pass
                if mode_name == "indeterminate":
                    if current_mode != "indeterminate":
                        try:
                            progressbar.config(mode="indeterminate", maximum=100)
                            progressbar.start(12)
                        except Exception:
                            pass
                    if percent is not None:
                        self.run_progress_var.set(max(0.0, min(100.0, float(percent))))
                else:
                    try:
                        progressbar.config(mode="determinate", maximum=100)
                    except Exception:
                        pass
                    if percent is not None:
                        self.run_progress_var.set(max(0.0, min(100.0, float(percent))))
                self.run_progress_mode = mode_name
            elif percent is not None:
                self.run_progress_var.set(max(0.0, min(100.0, float(percent))))
            if text is not None:
                self.run_progress_text_var.set(text)

        self._safe_after(0, update)

    def _set_acquisition_progress(self, progress):
        try:
            progress_value = float(progress)
        except Exception:
            return
        self.last_acquisition_progress_time = time.perf_counter()
        fraction = progress_value / 100.0 if progress_value > 1.0 else progress_value
        pct = max(0, min(100, int(round(fraction * 100.0))))
        self._set_run_progress(f"Acquiring: {pct}%", pct, mode="determinate")
        return pct

    def _start_busy_progress(self, text):
        self._set_run_progress(text, mode="indeterminate")

    def _finish_run_progress(self, text="Progress: complete"):
        self._set_run_progress(text, 100, mode="determinate")

    def _fail_run_progress(self, text="Progress: error"):
        self._set_run_progress(text, 0, mode="determinate")

    def _acquisition_busy_reason(self, include_sdk=True, include_parameter_apply=True):
        busy_states = {
            self.STATE_LABELS["WaitingForTrigger"],
            self.STATE_LABELS["Acquiring"],
            self.STATE_LABELS["ComputingHypercube"],
            self.STATE_LABELS["Saving"],
            self.STATE_LABELS["RunningTimelapse"],
        }
        if getattr(self, "acquisition_inflight", False):
            return "an acquisition is already running"
        if getattr(self, "stage_motion_inflight", False):
            return "stage motion is still in progress"
        if getattr(self, "app_state", None) in busy_states:
            return f"the app is {self.app_state}"
        start_lock = getattr(self, "acquisition_start_lock", None)
        if start_lock and start_lock.locked():
            return "an acquisition start is already in progress"
        processing_lock = getattr(self, "processing_lock", None)
        if processing_lock and processing_lock.locked():
            return "the previous acquisition is still being processed"
        parameter_lock = getattr(self, "parameter_apply_lock", None)
        if include_parameter_apply and parameter_lock and parameter_lock.locked():
            return "camera parameters are still being applied"
        if include_sdk and self.controller and self.controller.connected:
            try:
                if self.controller.is_acquiring():
                    return "the Hera SDK reports an acquisition in progress"
            except Exception as exc:
                self.log(f"Could not verify Hera acquisition state before starting: {exc}", detail=True)
        return None

    def _set_acquisition_inflight(self, active):
        self.acquisition_inflight = bool(active)
        if not active:
            self.acquisition_heartbeat_token += 1
        self._safe_after(0, self._refresh_run_action_controls)

    def _schedule_acquisition_heartbeat(self, acquisition_role):
        self.acquisition_heartbeat_token += 1
        token = self.acquisition_heartbeat_token
        self.last_acquisition_heartbeat_log_sec = 0
        self._safe_after(
            10000,
            lambda token=token, role=acquisition_role: self._acquisition_heartbeat_notice(token, role),
        )

    def _acquisition_heartbeat_notice(self, token, acquisition_role):
        if token != getattr(self, "acquisition_heartbeat_token", None):
            return
        if not getattr(self, "acquisition_inflight", False):
            return
        start_time = getattr(self, "acquisition_start_perf_time", None)
        if start_time is None:
            return
        elapsed = max(0, int(round(time.perf_counter() - start_time)))
        label = "Flatfield" if acquisition_role == "flatfield" else "Acquisition"
        last_progress_time = getattr(self, "last_acquisition_progress_time", None)
        progress_is_recent = last_progress_time is not None and (time.perf_counter() - last_progress_time) < 8
        if not progress_is_recent:
            self._set_run_progress(
                f"{label}: waiting for Hera SDK callback ({elapsed} s elapsed)",
                mode="indeterminate",
            )
        log_bucket = (elapsed // 30) * 30
        if log_bucket >= 30 and log_bucket != getattr(self, "last_acquisition_heartbeat_log_sec", 0):
            self.last_acquisition_heartbeat_log_sec = log_bucket
            self.log(f"{label} is still running; waiting for Hera SDK callback ({elapsed} s elapsed).")
        self._safe_after(
            10000,
            lambda token=token, role=acquisition_role: self._acquisition_heartbeat_notice(token, role),
        )

    def _schedule_acquisition_watchdog(self, acquisition_role):
        self.acquisition_watchdog_token += 1
        token = self.acquisition_watchdog_token
        delay_ms = 300000
        self._safe_after(
            delay_ms,
            lambda token=token, role=acquisition_role, delay_ms=delay_ms: self._acquisition_watchdog_notice(
                token,
                role,
                delay_ms,
            ),
        )

    def _cancel_acquisition_watchdog(self):
        self.acquisition_watchdog_token += 1

    def _acquisition_watchdog_notice(self, token, acquisition_role, delay_ms):
        if token != getattr(self, "acquisition_watchdog_token", None):
            return
        if not getattr(self, "acquisition_inflight", False):
            return
        label = "Flatfield" if acquisition_role == "flatfield" else "Hyperspectral"
        seconds = delay_ms // 1000
        self.log(
            f"{label} acquisition is still waiting for a Hera SDK callback after {seconds} s. "
            "If progress has stopped, press Abort Hera Acquisition before trying again."
        )
        if acquisition_role == "flatfield":
            self._start_busy_progress("Acquiring flatfield: waiting for SDK callback...")

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
        busy_states = {
            self.STATE_LABELS["WaitingForTrigger"],
            self.STATE_LABELS["Acquiring"],
            self.STATE_LABELS["ComputingHypercube"],
            self.STATE_LABELS["Saving"],
        }
        start_lock = getattr(self, "acquisition_start_lock", None)
        if self.app_state in busy_states or (start_lock and start_lock.locked()):
            self.log("Hera parameter apply skipped while acquisition is active.")
            return
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

    def apply_parameters(self, restart_live=True, apply_roi=False, hdr_enabled=None, reset_roi_before_set=False):
        try:
            settings = self._read_hera_parameter_settings()
        except Exception as exc:
            self.log(f"Failed to read Hera parameters from the UI: {exc}")
            self.update_state("Error")
            return False
        settings["apply_roi"] = bool(apply_roi)
        settings["reset_roi_before_set"] = bool(reset_roi_before_set)
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
                self.live_accept_frames = False
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
                    current_hdr = self.controller.get_hdr()
                    if current_hdr != hdr_enabled:
                        self.controller.set_hdr(hdr_enabled)
                        time.sleep(0.2)
                        actual_hdr = self.controller.get_hdr()
                    else:
                        actual_hdr = current_hdr
                    self._safe_after(0, lambda actual_hdr=actual_hdr: self._set_var_if_changed(self.hdr_enabled_var, actual_hdr))
                    self._set_var_async(self.hdr_status_var, self.hdr_status_text(actual_hdr))
                    self._log_async(
                        f"Set HDR: requested={self.hdr_mode_text(hdr_enabled)}, actual={self.hdr_mode_text(actual_hdr)}"
                    )
                else:
                    if hdr_enabled:
                        raise RuntimeError("HDR was requested, but this Hera device or SDK DLL reports HDR is not supported.")
                    self._safe_after(0, lambda: self._set_var_if_changed(self.hdr_enabled_var, False))
                    self._set_var_async(self.hdr_status_var, "HDR mode: not supported")
            except Exception as exc:
                self._log_async(f"HDR mode was not changed: {exc}")
                if hdr_enabled:
                    raise
                self._set_var_async(self.hdr_status_var, "HDR mode: check failed")

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
                roi_writable = None
                try:
                    roi_writable = self.controller.is_roi_writable()
                    self._log_async(f"ROI writability before SetROI: {roi_writable}")
                except Exception as exc:
                    self._log_async(f"ROI writability check failed before SetROI: {exc}")
                if settings.get("reset_roi_before_set"):
                    try:
                        self.controller.clear_roi()
                        time.sleep(0.2)
                        reset_roi = self._normalize_roi_tuple(self.controller.get_roi())
                        self._log_async(f"Reset Hera ROI before SetROI. Current ROI after reset: {reset_roi}")
                    except Exception as exc:
                        self._log_async(f"Could not reset Hera ROI before SetROI: {exc}")
                try:
                    self.controller.set_roi(roi_x, roi_y, roi_w, roi_h)
                    actual_roi = self.controller.get_roi()
                    actual_roi = self._normalize_roi_tuple(actual_roi)
                    self.last_applied_roi = actual_roi
                    self._log_async(
                        f"Set ROI forced: writable={roi_writable}, "
                        f"requested=({roi_x}, {roi_y}, {roi_w}, {roi_h}), actual={actual_roi}"
                    )
                    if actual_roi != (roi_x, roi_y, roi_w, roi_h):
                        self._log_async(
                            "ROI readback differs from the requested ROI. "
                            "The SDK/camera may have rounded or rejected one of the ROI values."
                        )
                except Exception as exc:
                    try:
                        actual_roi = self._normalize_roi_tuple(self.controller.get_roi())
                    except Exception:
                        actual_roi = None
                    self.last_applied_roi = actual_roi
                    self._log_async(
                        f"Forced SetROI failed despite Marta diagnostic request: {exc}. "
                        f"Current ROI: {actual_roi}"
                    )
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
                            status=f"ROI: active w={w}, h={h}",
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

            if not getattr(self, "acquisition_inflight", False):
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

    def _cancel_pending_auto_apply_parameters(self):
        job = getattr(self, "_auto_apply_parameters_job", None)
        if not job:
            return
        self._auto_apply_parameters_job = None
        try:
            self.after_cancel(job)
            self.log("Canceled pending automatic parameter apply before starting acquisition.", detail=True)
        except Exception:
            pass

    def _release_current_sample_before_new_sample(self):
        current_handle = self.current_hypercube_handle
        current_info = self.current_hypercube_info
        if not current_handle or not current_info or current_info.get("role") == "flatfield":
            return
        if current_handle == self.flatfield_hypercube_handle:
            return
        if not self._is_owned_cube_info(current_info):
            try:
                with self.hypercube_read_lock:
                    self.controller.release_hypercube(current_handle)
            except Exception as exc:
                self.log(f"Could not release previous sample cube before starting a new sample: {exc}")
                return
        self.current_hypercube_handle = None
        self.current_hypercube_info = None
        self.current_hypercube_data = None
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
        self.pending_save_context = None
        self._safe_after(
            0,
            lambda: (
                self.hyper_band_scale.config(to=0),
                self.current_hyper_band_index.set(0),
                self.current_hyper_band_var.set("Band: -"),
                self.current_hyper_wavelength_var.set("Wavelength: -"),
                self.hypercube_summary_var.set("Cube: waiting for acquisition"),
                self._draw_hyperspectral_view_placeholder("Previous sample released before starting a new acquisition"),
                self._refresh_export_controls_for_display_mode(),
            ),
        )
        self.log("Released previous sample cube before starting a new sample acquisition to reduce memory usage.")

    def _helper_project_root(self):
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    def _helper_cache_dir(self):
        cache_dir = os.path.join(self.default_output_dir, "helper_cache")
        os.makedirs(cache_dir, exist_ok=True)
        return cache_dir

    def _helper_request_dir(self):
        request_dir = os.path.join(tempfile.gettempdir(), "hera_helper_requests")
        os.makedirs(request_dir, exist_ok=True)
        return request_dir

    def _helper_blocking_sdk_cube_reason(self):
        candidates = (
            ("current sample", self.current_hypercube_handle, self.current_hypercube_info),
            ("flatfield reference", self.flatfield_hypercube_handle, self.flatfield_info),
        )
        for label, handle, info in candidates:
            if handle and info and not self._is_owned_cube_info(info):
                return (
                    f"the {label} is still held by the main Hera SDK process. "
                    "Helper acquisition would invalidate that SDK handle."
                )
        return None

    def _should_use_helper_acquisition(self, acquisition_role, trigger_mode_name, apply_roi, auto_save):
        if not bool(getattr(self, "helper_acquisition_enabled", True)):
            return False, "helper acquisition is disabled"
        if auto_save:
            return False, "helper acquisition is limited to manual acquisitions"
        if trigger_mode_name != "Internal":
            return False, "helper acquisition currently supports Internal trigger only"
        if not apply_roi or not self.acquisition_requested_roi:
            return False, "helper acquisition currently requires an active camera ROI"
        blocking_reason = self._helper_blocking_sdk_cube_reason()
        if blocking_reason:
            return False, blocking_reason
        return True, ""

    def _build_helper_request(self, export_tag, acquisition_role, scan_mode, trigger_mode, averages, stabilization):
        request_id = f"{acquisition_role}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        return {
            "request_id": request_id,
            "role": acquisition_role,
            "dll_path": self.dll_path_var.get(),
            "device_index": self._selected_device_index(),
            "cache_dir": self._helper_cache_dir(),
            "export_tag": export_tag,
            "gain": float(self.param_vars["gain"].get()),
            "exposure_ms": float(self.param_vars["exposure"].get()),
            "hdr_enabled": bool(self.hdr_enabled_var.get()),
            "roi": list(self.acquisition_requested_roi) if self.acquisition_requested_roi else None,
            "scan_mode": int(scan_mode),
            "trigger_mode": int(trigger_mode),
            "averages": int(averages),
            "stabilization_ms": int(stabilization),
            "bands": int(self.param_vars["bands"].get()),
            "binning": int(self.BINNING_OPTIONS[self.param_vars["binning"].get()]),
            "data_type": int(self.DATA_TYPES[self.param_vars["data_type"].get()]),
            "callback_timeout_sec": int(getattr(self, "helper_acquisition_timeout_sec", 900)),
        }

    def _write_helper_request(self, request):
        request_path = os.path.join(self._helper_request_dir(), f"{request['request_id']}.json")
        with open(request_path, "w", encoding="utf-8") as request_file:
            json.dump(request, request_file, indent=2)
        return request_path

    def _disconnect_hera_for_helper(self):
        if not self.controller:
            return
        self.log("Pausing main Hera connection while helper process owns the camera.")
        self.live_accept_frames = False
        try:
            if self.controller.connected:
                try:
                    if self.controller.is_live_capturing():
                        self.controller.stop_live_capture(silent=True)
                        self.controller.wait_for_live_capture_stopped(timeout_sec=5.0)
                except Exception as exc:
                    self.log(f"Could not fully stop live capture before helper acquisition: {exc}")
                self.controller.unregister_live_callbacks()
                self.controller.unregister_callbacks()
                self.controller.disconnect()
        finally:
            try:
                self.controller.release_device()
            except Exception:
                pass
            self.controller = None
            self._clear_live_view_frame_state()
            self._set_live_view_status("Live view: paused for helper acquisition")

    def _schedule_helper_reconnect(self):
        if getattr(self, "is_closing", False):
            return
        self.hdr_startup_default_enabled = bool(getattr(self, "acquisition_requested_hdr", False))
        self._safe_after(0, self.connect_hera)

    def _start_helper_acquisition(self, request):
        self.helper_acquisition_process = None
        self.helper_acquisition_request_id = request.get("request_id")
        self.hera_service_acquisition_inflight = True
        self._start_busy_progress(
            "Acquiring flatfield in helper service..."
            if request.get("role") == "flatfield"
            else "Acquiring in helper service..."
        )
        self.log(
            "Starting Hera helper service for "
            f"{request.get('role')} acquisition with ROI {request.get('roi')}."
        )
        threading.Thread(target=self._helper_service_acquisition_worker, args=(request,), daemon=True).start()

    def _helper_service_acquisition_worker(self, request):
        result = None
        start_time = time.perf_counter()
        try:
            if self.controller and self.controller.connected:
                released = self._release_main_hera_connection(
                    "helper service acquisition",
                    update_state_after_release=False,
                    clear_cached_data=False,
                )
                if not released:
                    raise RuntimeError("Main Hera connection could not be released before helper service acquisition.")

            client = self._get_hera_service_client()
            client.start()
            self._log_async("Hera helper service is ready for acquisition.")
            result = client.request(
                "acquire",
                timeout_sec=float(getattr(self, "helper_process_timeout_sec", 1200)),
                event_callback=self._handle_helper_service_acquisition_event,
                **request,
            )
            try:
                disconnected = client.request("disconnect", timeout_sec=20.0)
                self._log_helper_service_result("Helper acquisition disconnect", disconnected)
            except Exception as exc:
                self._log_async(f"Helper service did not disconnect cleanly after acquisition; killing it: {exc}")
                try:
                    client.kill()
                except Exception:
                    pass
                self.hera_service_client = None
            if not result:
                raise RuntimeError("Helper service finished without returning acquisition data.")
            self._finish_helper_acquisition_result(result, request, time.perf_counter() - start_time)
        except Exception as exc:
            if self.last_acquisition_error == "Helper service acquisition was aborted.":
                self._log_async("Helper service acquisition stopped after Abort.")
                return
            self.last_acquisition_error = str(exc)
            self.acquisition_success = False
            self._log_async(f"Helper service acquisition failed: {exc}")
            self._fail_run_progress("Progress: helper acquisition failed")
            self._safe_after(0, lambda: self.update_state("Error"))
            self.acquisition_done_event.set()
        finally:
            self.hera_service_acquisition_inflight = False
            self.helper_acquisition_request_id = None
            self._set_acquisition_inflight(False)
            self._schedule_helper_reconnect()

    def _handle_helper_service_acquisition_event(self, event):
        event_name = event.get("event")
        if event_name == "log":
            self._log_async(event.get("message", "Helper service log message"))
        elif event_name == "progress":
            phase = event.get("phase", "helper")
            percent = max(0, min(100, int(event.get("percent", 0))))
            labels = {
                "acquiring": "Helper acquiring",
                "computing": "Helper computing hypercube",
                "writing_cache": "Helper writing cache",
            }
            self._set_run_progress(f"{labels.get(phase, 'Helper')}: {percent}%", percent, mode="determinate")

    def _helper_acquisition_worker(self, request_path, request):
        process = None
        reader_queue = queue.Queue()
        result = None
        error_message = None
        error_traceback = ""
        start_time = time.perf_counter()
        try:
            command = [
                sys.executable,
                "-m",
                "hera_app.helpers.acquisition_helper",
                "--request",
                request_path,
            ]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                command,
                cwd=self._helper_project_root(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=creationflags,
            )
            self.helper_acquisition_process = process

            def reader():
                try:
                    for line in process.stdout:
                        reader_queue.put(line)
                finally:
                    reader_queue.put(None)

            threading.Thread(target=reader, daemon=True).start()
            deadline = time.monotonic() + float(getattr(self, "helper_process_timeout_sec", 1200))
            reader_done = False
            while True:
                if time.monotonic() > deadline and process.poll() is None:
                    process.kill()
                    raise RuntimeError("Helper process timed out and was killed.")
                try:
                    line = reader_queue.get(timeout=0.25)
                except queue.Empty:
                    if process.poll() is not None and reader_done:
                        break
                    continue
                if line is None:
                    reader_done = True
                    if process.poll() is not None:
                        break
                    continue
                parsed = self._handle_helper_output_line(line)
                if not parsed:
                    continue
                event = parsed.get("event")
                if event == "result":
                    result = parsed.get("result")
                elif event == "error":
                    error_message = parsed.get("message") or "Helper process failed."
                    error_traceback = parsed.get("traceback") or ""

            return_code = process.wait(timeout=2)
            if return_code != 0:
                raise RuntimeError(error_message or f"Helper process exited with code {return_code}.")
            if not result:
                raise RuntimeError("Helper process finished without returning acquisition data.")
            self._finish_helper_acquisition_result(result, request, time.perf_counter() - start_time)
        except Exception as exc:
            self.last_acquisition_error = str(exc)
            self.acquisition_success = False
            self._log_async(f"Helper acquisition failed: {exc}")
            if error_traceback:
                self._log_async(error_traceback, detail=True)
            self._fail_run_progress("Progress: helper acquisition failed")
            self._safe_after(0, lambda: self.update_state("Error"))
            self.acquisition_done_event.set()
        finally:
            self.helper_acquisition_process = None
            self._set_acquisition_inflight(False)
            self._schedule_helper_reconnect()

    def _handle_helper_output_line(self, line):
        text = (line or "").strip()
        if not text:
            return None
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            self._log_async(f"Helper output: {text}", detail=True)
            return None
        event_name = event.get("event")
        if event_name == "log":
            self._log_async(event.get("message", "Helper log message"))
        elif event_name == "progress":
            phase = event.get("phase", "helper")
            percent = max(0, min(100, int(event.get("percent", 0))))
            labels = {
                "acquiring": "Helper acquiring",
                "computing": "Helper computing hypercube",
                "writing_cache": "Helper writing cache",
            }
            self._set_run_progress(f"{labels.get(phase, 'Helper')}: {percent}%", percent, mode="determinate")
        return event

    def _owned_data_from_helper_result(self, result, role):
        cache_path = result.get("cache_path")
        if not cache_path or not os.path.exists(cache_path):
            raise RuntimeError(f"Helper cache file is missing: {cache_path}")
        return {
            "file_path": cache_path,
            "wavelengths": result.get("wavelengths") or [],
            "storage_kind": "file",
            "width": int(result["cube_width"]),
            "height": int(result["cube_height"]),
            "data_type": int(result["cube_type"]),
            "role": role,
        }

    def _finish_helper_acquisition_result(self, result, request, total_elapsed):
        role = request.get("role", "sample")
        requested_roi = self._normalize_roi_tuple(result.get("requested_roi") or request.get("roi"))
        actual_roi = self._normalize_roi_tuple(result.get("actual_roi"))
        cube_width = int(result["cube_width"])
        cube_height = int(result["cube_height"])
        cube_bands = int(result["cube_bands"])
        cube_type = int(result["cube_type"])
        raw_width = int(result["raw_width"])
        raw_height = int(result["raw_height"])
        display_roi, export_roi, display_width, display_height, roi_mode = self._resolve_hypercube_roi(
            requested_roi,
            actual_roi,
            raw_width,
            raw_height,
            cube_width,
            cube_height,
        )
        owned_handle = f"owned-helper:{role}:{result.get('request_id') or time.time()}"
        owned_info = {
            "width": display_width,
            "height": display_height,
            "source_width": cube_width,
            "source_height": cube_height,
            "display_roi": display_roi,
            "camera_roi": actual_roi if roi_mode == "camera" else None,
            "export_roi": export_roi,
            "bands": cube_bands,
            "data_type": cube_type,
            "is_hdr": result.get("cube_is_hdr"),
            "role": role,
            "storage": "owned",
            "owned_data_role": "flatfield" if role == "flatfield" else "sample",
            "owned_storage_kind": "file",
            "owned_file_path": result.get("cache_path"),
        }
        owned_data = self._owned_data_from_helper_result(result, owned_info["owned_data_role"])
        previous_handle = self.current_hypercube_handle
        previous_info = dict(self.current_hypercube_info) if self.current_hypercube_info else None
        keep_previous_sample = bool(
            role == "flatfield"
            and previous_handle
            and previous_info
            and previous_info.get("role") != "flatfield"
        )

        if role == "flatfield":
            self.flatfield_hypercube_handle = owned_handle
            self.flatfield_info = dict(owned_info)
            self.flatfield_hypercube_data = owned_data
            if keep_previous_sample:
                self.current_hypercube_handle = previous_handle
                self.current_hypercube_info = previous_info
        else:
            self.current_hypercube_handle = owned_handle
            self.current_hypercube_info = owned_info
            self.current_hypercube_data = owned_data

        self.current_hyper_band_cache = {}
        self.current_hyper_spectrum_cache = {}
        self.current_hyper_pointer_cache = {}
        self.hyper_selected_pixel = None
        self.hyper_cursor_pixel = None
        self.hyper_selected_spectrum = None
        self.hyper_cursor_spectrum = None
        self.hyper_flatfield_spectrum = None
        self.hyper_spectrum_loading = ""
        self.hyper_spectrum_error = ""

        self.pending_save_context = {
            "hypercube_handle": owned_handle,
            "export_tag": self.pending_export_tag or request.get("export_tag") or self._sanitize_export_tag(time.strftime("%Y%m%d_%H%M%S")),
            "requested_roi": requested_roi,
            "export_roi": export_roi,
            "cube_width": cube_width,
            "cube_height": cube_height,
            "cube_hdr_text": "unknown" if result.get("cube_is_hdr") is None else ("on" if result.get("cube_is_hdr") else "off"),
            "cube_is_hdr": result.get("cube_is_hdr"),
            "info": dict(self.flatfield_info if role == "flatfield" else self.current_hypercube_info),
            "role": role,
        }
        self.acquisition_success = True
        self.last_acquisition_error = ""
        self.acquisition_done_event.set()

        timings = result.get("timings") or {}
        self._log_async(
            "Helper performance timing: "
            f"callback={float(timings.get('acquisition_callback_sec', 0.0)):.2f} s, "
            f"hypercube={float(timings.get('hypercube_compute_sec', 0.0)):.2f} s, "
            f"cache_write={float(timings.get('cache_write_sec', 0.0)):.2f} s, "
            f"total={total_elapsed:.2f} s.",
            detail=True,
        )
        self._set_var_async(
            self.hypercube_summary_var,
            f"Cube: {display_width} x {display_height}, bands={cube_bands}, type={cube_type}"
            + (f" (camera ROI)" if roi_mode == "camera" else ""),
        )
        if role == "flatfield":
            self._set_var_async(self.flatfield_status_var, f"ready ({display_width} x {display_height}, bands={cube_bands})")
            if keep_previous_sample:
                next_mode = "Normalized" if self._should_use_flatfield_correction(self.current_hypercube_info) else "Raw"
                self._set_var_async(self.hyper_display_mode_var, next_mode)
                render_info = previous_info
                render_handle = previous_handle
            else:
                self._set_var_async(self.hyper_display_mode_var, "Flatfield")
                render_info = self.flatfield_info
                render_handle = self.flatfield_hypercube_handle
            self._log_async("Flatfield acquired by helper process and kept in memory.")
        else:
            render_info = self.current_hypercube_info
            render_handle = self.current_hypercube_handle
            self._log_async("Sample acquired by helper process and kept in memory.")

        default_band = self._default_hyper_band_index_for_info(render_handle, render_info)

        def finalize_helper_view(default_band=default_band, bands=render_info["bands"], role=role, render_info=render_info):
            if role != "flatfield" and self.hyper_display_mode_var.get() == "Flatfield":
                next_mode = "Normalized" if self._should_use_flatfield_correction(render_info) else "Raw"
                self.hyper_display_mode_var.set(next_mode)
            self.hyper_band_scale.config(to=max(bands - 1, 0))
            self.current_hyper_band_index.set(default_band)
            self.render_current_hyper_band()
            self._refresh_export_controls_for_display_mode()
            self.update_state("Completed")

        self._safe_after(
            0,
            finalize_helper_view,
        )
        self._finish_run_progress(
            "Progress: flatfield ready" if role == "flatfield" else "Progress: acquisition ready"
        )

    def _arm_and_start_acquisition(
        self,
        export_tag=None,
        acquisition_role="sample",
        forced_roi=None,
        auto_save=True,
        use_camera_roi=True,
    ):
        if not self.acquisition_start_lock.acquire(blocking=False):
            raise RuntimeError("An acquisition start is already in progress.")
        parameter_lock_acquired = False
        try:
            self._cancel_pending_auto_apply_parameters()
            if self.parameter_apply_lock.locked():
                self.log("Waiting for the current Hera parameter apply to finish before starting acquisition.")
            if not self.parameter_apply_lock.acquire(timeout=20.0):
                raise RuntimeError("Timed out waiting for Hera parameter apply to finish.")
            parameter_lock_acquired = True
            return self._arm_and_start_acquisition_locked(export_tag, acquisition_role, forced_roi, auto_save, use_camera_roi)
        except Exception:
            self._set_acquisition_inflight(False)
            raise
        finally:
            if parameter_lock_acquired:
                self.parameter_apply_lock.release()
            self.acquisition_start_lock.release()

    def _arm_and_start_acquisition_locked(
        self,
        export_tag=None,
        acquisition_role="sample",
        forced_roi=None,
        auto_save=True,
        use_camera_roi=True,
    ):
        if not self.controller or not self.controller.connected:
            raise RuntimeError("Connect to Hera before starting acquisition.")
        if not self.check_license_status(allow_cached=True):
            raise RuntimeError("Hera SDK license is not active.")
        if self.controller.is_acquiring():
            raise RuntimeError("The device is already acquiring.")
        if self.processing_lock.locked():
            raise RuntimeError("The previous acquisition is still being processed.")

        self._set_acquisition_inflight(True)
        arm_start_time = time.perf_counter()
        live_was_running = self.controller.is_live_capturing()
        self.resume_live_after_acquisition = live_was_running
        self.acquisition_camera_roi = None
        if acquisition_role == "sample":
            self._release_current_sample_before_new_sample()
        if forced_roi is not None:
            forced_roi = self._normalize_roi_tuple(forced_roi)
            self.acquisition_requested_roi = forced_roi
            roi_x, roi_y, roi_w, roi_h = forced_roi
            self.param_vars["roi_x"].set(roi_x)
            self.param_vars["roi_y"].set(roi_y)
            self.param_vars["roi_w"].set(roi_w)
            self.param_vars["roi_h"].set(roi_h)
            self._set_active_roi(forced_roi)
            apply_roi = bool(use_camera_roi)
            if acquisition_role == "flatfield":
                if apply_roi:
                    self.log(
                        "Flatfield acquisition will use ROI-limited Hera capture; "
                        f"ROI {self._format_roi(forced_roi)} will be computed as the flatfield reference."
                    )
                else:
                    self.log(
                        "Flatfield acquisition will use full-frame Hera capture; "
                        f"selected ROI {self._format_roi(forced_roi)} is kept for display/export."
                    )
            else:
                self.log(f"ROI for this acquisition: {self._format_roi(forced_roi)}.")
        else:
            self.acquisition_requested_roi = self._get_active_roi()
            apply_roi = bool(self.acquisition_requested_roi is not None and use_camera_roi)
            if self.acquisition_requested_roi:
                if acquisition_role == "flatfield":
                    if apply_roi:
                        self.log(
                            "Flatfield acquisition will use ROI-limited Hera capture; "
                            f"ROI {self.acquisition_requested_roi} will be computed as the flatfield reference."
                        )
                    else:
                        self.log(
                            "Flatfield acquisition will use full-frame Hera capture; "
                            f"selected ROI {self.acquisition_requested_roi} is kept for display/export."
                        )
                else:
                    self.log(f"Selected ROI for exported cube: {self.acquisition_requested_roi}")
            else:
                if acquisition_role == "flatfield":
                    self.log("No ROI selected for flatfield; flatfield acquisition will use the full returned image.")
                else:
                    self.log("No ROI selected for export; hyperspectral cube will use the full returned image.")
        acquisition_hdr_enabled = bool(self.hdr_enabled_var.get())
        self.log(f"HDR mode for acquisition: {self.hdr_mode_text(acquisition_hdr_enabled)}.", detail=True)
        trigger_mode_name = self.param_vars["trigger_mode"].get()
        self.update_state("Acquiring" if trigger_mode_name == "Internal" else "WaitingForTrigger")
        if acquisition_role == "flatfield":
            self._start_busy_progress("Preparing flatfield acquisition...")
        else:
            self._start_busy_progress("Preparing acquisition...")

        scan_mode = self.SCAN_MODES[self.param_vars["scan_mode"].get()]
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
        self.last_acquisition_progress_time = None
        self.last_acquisition_heartbeat_log_sec = 0
        if self.save_pending_button:
            self.save_pending_button.config(state="disabled")

        use_helper, helper_reason = self._should_use_helper_acquisition(
            acquisition_role,
            trigger_mode_name,
            apply_roi,
            auto_save,
        )
        if use_helper:
            helper_request = self._build_helper_request(
                export_tag,
                acquisition_role,
                scan_mode,
                trigger_mode,
                averages,
                stabilization,
            )
            self._start_helper_acquisition(helper_request)
            arm_elapsed = time.perf_counter() - arm_start_time
            self.log(f"Helper acquisition started. Start preparation took {arm_elapsed:.2f} s.", detail=True)
            return
        if helper_reason:
            self.log(f"Helper acquisition not used: {helper_reason}", detail=True)

        self.log("Preparing Hera camera parameters before starting acquisition.")
        apply_start_time = time.perf_counter()
        reset_roi_before_set = False
        if not self.apply_parameters(
            restart_live=False,
            apply_roi=apply_roi,
            hdr_enabled=acquisition_hdr_enabled,
            reset_roi_before_set=reset_roi_before_set,
        ):
            if live_was_running and self.controller and self.controller.connected:
                self.start_live_view()
                self.resume_live_after_acquisition = False
            raise RuntimeError("Applying Hera parameters failed.")
        apply_elapsed = time.perf_counter() - apply_start_time
        self.log(f"Performance timing: camera parameter apply took {apply_elapsed:.2f} s.", detail=True)
        self.acquisition_camera_roi = self._normalize_roi_tuple(self.last_applied_roi) if apply_roi and self.last_applied_roi else None
        if self.acquisition_requested_roi:
            try:
                self.log(f"Camera ROI after parameter apply: {self.controller.get_roi()}")
            except Exception as exc:
                self.log(f"Could not read camera ROI after parameter apply: {exc}")

        try:
            live_still_running = self.controller.is_live_capturing()
        except Exception as exc:
            live_still_running = None
            self.log(f"Could not verify live capture state immediately before acquisition start: {exc}", detail=True)
        if live_still_running:
            self.log("Live capture was still running immediately before hyperspectral start; stopping it again.", detail=True)
            self.live_accept_frames = False
            self.controller.stop_live_capture(silent=True)
            self.controller.wait_for_live_capture_stopped(timeout_sec=5.0)
            self.controller.unregister_live_callbacks()

        self.acquisition_pre_start_hdr = None
        try:
            if self.controller.is_hdr_supported():
                hdr_confirmed = self.controller.get_hdr()
                if hdr_confirmed != acquisition_hdr_enabled:
                    self.controller.set_hdr(acquisition_hdr_enabled)
                    time.sleep(0.3 if acquisition_hdr_enabled else 0.2)
                    hdr_confirmed = self.controller.get_hdr()
                self.acquisition_pre_start_hdr = hdr_confirmed
                self.log(
                    "Pre-start HDR readback immediately before "
                    "HeraAPI_StartHyperspectralDataAcquisitionEx: "
                    f"requested={self.hdr_mode_text(acquisition_hdr_enabled)}, "
                    f"camera={self.hdr_mode_text(hdr_confirmed)}, "
                    f"live_capturing={live_still_running}.",
                    detail=True,
                )
                self._set_var_async(self.hdr_status_var, self.hdr_status_text(hdr_confirmed))
                if acquisition_hdr_enabled and not hdr_confirmed:
                    raise RuntimeError("HDR was requested, but HeraAPI_GetHDR returned off immediately before acquisition start.")
            else:
                self._set_var_async(self.hdr_status_var, "HDR mode: not supported")
                if acquisition_hdr_enabled:
                    raise RuntimeError("HDR was requested, but this Hera device or SDK DLL reports HDR is not supported.")
                self.log("Pre-start HDR readback skipped because HDR is not supported.", detail=True)
        except Exception as exc:
            self.log(f"Pre-start HDR check failed: {exc}", detail=True)
            if acquisition_hdr_enabled:
                raise
        if getattr(self, "hdr_pixel_format_diagnostics_enabled", False):
            self._log_pre_start_pixel_format_support()

        if trigger_mode_name == "Internal":
            if acquisition_role == "flatfield":
                self.log("Sending software flatfield acquisition command through Hera SDK.")
                self._start_busy_progress("Acquiring flatfield...")
            else:
                self.log("Sending software acquisition command through Hera SDK.")
                self._set_run_progress("Acquiring: 0%", 0, mode="determinate")
        else:
            self.log(f"Arming Hera SDK acquisition with trigger mode '{trigger_mode_name}'.")
            self._set_run_progress("Waiting for trigger...", 0, mode="determinate")

        self.acquisition_start_perf_time = time.perf_counter()
        self.controller.start_hyperspectral_acquisition(
            scan_mode,
            trigger_mode,
            averages,
            stabilization,
        )
        self.update_state("Acquiring" if trigger_mode_name == "Internal" else "WaitingForTrigger")
        self._schedule_acquisition_heartbeat(acquisition_role)
        self._schedule_acquisition_watchdog(acquisition_role)
        if acquisition_role == "flatfield":
            self.log("Flatfield acquisition is running; waiting for Hera SDK callback.")
        arm_elapsed = time.perf_counter() - arm_start_time
        self.log(f"Hyperspectral acquisition started. Start preparation took {arm_elapsed:.2f} s.", detail=True)

    def _log_pre_start_pixel_format_support(self):
        if not self.controller or not self.controller.connected:
            return
        api_name = (
            "HeraAPI_IsPixelFormatSupportedEx"
            if getattr(self.controller, "HeraAPI_IsPixelFormatSupportedEx", None)
            else "HeraAPI_IsPixelFormatSupported"
        )
        entries = []
        for pixel_format, pixel_name in self.LIVE_PIXEL_FORMATS.items():
            states = []
            for hdr in (False, True):
                mode_text = self.hdr_mode_text(hdr, short=True)
                try:
                    supported = self.controller.is_pixel_format_supported(pixel_format, hdr=hdr)
                    states.append(f"{mode_text}={'yes' if supported else 'no'}")
                except Exception as exc:
                    states.append(f"{mode_text}=error({exc})")
            entries.append(f"{pixel_name}: " + ", ".join(states))
        self.log(
            "Pre-start Live Capture PixelFormat support check "
            f"using {api_name}: " + "; ".join(entries),
            detail=True,
        )

    def start_acquisition(self):
        try:
            busy_reason = self._acquisition_busy_reason()
            if busy_reason:
                self.log(f"Start acquisition ignored because {busy_reason}.")
                return
            tag = self._sanitize_export_tag(f"manual_{time.strftime('%Y%m%d_%H%M%S')}")
            self._arm_and_start_acquisition(export_tag=tag, acquisition_role="sample", auto_save=False)
        except Exception as exc:
            self.log(f"Failed to start acquisition: {exc}")
            self._fail_run_progress("Progress: acquisition failed")
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
        description = f"{description}\nHyperspectral acquisition mode: {self.hdr_mode_text(cube_is_hdr)}"
        if self.acquisition_requested_hdr and cube_is_hdr is False:
            description = f"{description}\nDynamic Range HDR was requested, but SDK returned Sensitivity 12-bit hyperspectral data"
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

    def _build_current_sample_save_context(self):
        if not self.current_hypercube_handle or not self.current_hypercube_info:
            return None
        if self.current_hypercube_info.get("role") == "flatfield":
            return None
        cube_is_hdr = self.current_hypercube_info.get("is_hdr")
        cube_hdr_text = "unknown" if cube_is_hdr is None else ("on" if cube_is_hdr else "off")
        return {
            "hypercube_handle": self.current_hypercube_handle,
            "export_tag": self._sanitize_export_tag(f"sample_{time.strftime('%Y%m%d_%H%M%S')}"),
            "cube_hdr_text": cube_hdr_text,
            "cube_is_hdr": cube_is_hdr,
            "info": dict(self.current_hypercube_info),
            "role": "sample",
        }

    def _build_flatfield_save_context(self):
        if not self.flatfield_hypercube_handle or not self.flatfield_info:
            return None
        cube_is_hdr = self.flatfield_info.get("is_hdr")
        cube_hdr_text = "unknown" if cube_is_hdr is None else ("on" if cube_is_hdr else "off")
        return {
            "hypercube_handle": self.flatfield_hypercube_handle,
            "export_tag": self._sanitize_export_tag(f"flatfield_{time.strftime('%Y%m%d_%H%M%S')}"),
            "cube_hdr_text": cube_hdr_text,
            "cube_is_hdr": cube_is_hdr,
            "info": dict(self.flatfield_info),
            "role": "flatfield",
        }

    def save_pending_acquisition(self):
        ctx = self.pending_save_context
        if ctx and ctx.get("role") == "flatfield" and self.current_hypercube_info:
            ctx = None
        if not ctx:
            ctx = self._build_current_sample_save_context()
        if not ctx:
            if not self._bool_var_value("export_flatfield_var", True):
                self.log("No sample cube is loaded. Check _ref to save the flatfield reference.")
                return
            ctx = self._build_flatfield_save_context()
        if not ctx:
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
                self._start_busy_progress("Saving selected data...")
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
                self._finish_run_progress("Progress: saved")
                self._safe_after(0, lambda: self.update_state("Completed"))
                self._safe_after(0, self._refresh_export_controls_for_display_mode)
            except Exception as exc:
                self._log_async(f"Save failed: {exc}")
                self._fail_run_progress("Progress: save failed")
                self._safe_after(0, lambda: self.update_state("Error"))
                self._safe_after(0, self._refresh_export_controls_for_display_mode)

        threading.Thread(target=_do_save, daemon=True).start()

    def abort_acquisition(self):
        if getattr(self, "hera_service_acquisition_inflight", False):
            client = getattr(self, "hera_service_client", None)
            try:
                if client:
                    client.kill()
                    self.hera_service_client = None
                self.log("Helper service acquisition was killed by Abort.")
                self._fail_run_progress("Progress: helper acquisition aborted")
                self.hera_service_acquisition_inflight = False
                self.helper_acquisition_request_id = None
                self._set_acquisition_inflight(False)
                self.update_state("Ready")
                self.acquisition_success = False
                self.last_acquisition_error = "Helper service acquisition was aborted."
                self.acquisition_done_event.set()
                self._schedule_helper_reconnect()
            except Exception as exc:
                self.log(f"Failed to abort helper service acquisition: {exc}")
                self._fail_run_progress("Progress: helper abort failed")
                self.update_state("Error")
            return
        helper_process = getattr(self, "helper_acquisition_process", None)
        if helper_process and helper_process.poll() is None:
            try:
                helper_process.kill()
                self.log("Helper acquisition process was killed by Abort.")
                self._fail_run_progress("Progress: helper acquisition aborted")
                self._set_acquisition_inflight(False)
                self.update_state("Ready")
                self.acquisition_success = False
                self.last_acquisition_error = "Helper acquisition was aborted."
                self.acquisition_done_event.set()
                self._schedule_helper_reconnect()
            except Exception as exc:
                self.log(f"Failed to abort helper acquisition: {exc}")
                self._fail_run_progress("Progress: helper abort failed")
                self.update_state("Error")
            return
        if not self.controller or not self.controller.connected:
            self.log("Connect to Hera before aborting acquisition.")
            return
        try:
            self.controller.abort_hyperspectral_acquisition()
            self.log("Abort request sent to Hera SDK.")
            self._fail_run_progress("Progress: acquisition aborted")
            self._set_acquisition_inflight(False)
            self.update_state("Ready")
            self.acquisition_done_event.set()
            if self.resume_live_after_acquisition:
                self.resume_live_after_acquisition = False
                self.start_live_view()
        except Exception as exc:
            self.log(f"Failed to abort acquisition: {exc}")
            self._fail_run_progress("Progress: abort failed")
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
            pct = self._set_acquisition_progress(progress)
            if pct is None:
                return
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

    def _copy_hypercube_to_owned_cache(self, hypercube_handle, info, role):
        try:
            import numpy as np
        except Exception:
            np = None

        bands = int(info.get("bands") or 0)
        width = int(info.get("source_width") or info.get("width") or 0)
        height = int(info.get("source_height") or info.get("height") or 0)
        data_type = int(info.get("data_type") if info.get("data_type") is not None else 0)
        bytes_per_value = 4 if data_type == 0 else 8
        if bands <= 0 or width <= 0 or height <= 0:
            self._log_async(f"Cached {role} cube skipped because dimensions are invalid.")
            return None, None, info

        estimated_bytes = bands * width * height * bytes_per_value
        max_bytes = int(getattr(self, "owned_cube_cache_max_bytes", 0) or 0)
        if max_bytes > 0 and estimated_bytes > max_bytes:
            self._log_async(
                f"Cached {role} cube skipped: estimated size {estimated_bytes / (1024 ** 2):.1f} MB "
                f"exceeds limit {max_bytes / (1024 ** 2):.1f} MB. Keeping SDK handle."
            )
            return None, None, info

        if np is not None:
            dtype = np.float32 if data_type == 0 else np.float64
            cube = np.empty((bands, height, width), dtype=dtype)
            wavelengths = np.empty((bands,), dtype=np.float64)
            storage_kind = "numpy"
        else:
            import array
            import ctypes

            cube = []
            wavelengths = array.array("d")
            array_typecode = "f" if data_type == 0 else "d"
            c_value_type = ctypes.c_float if data_type == 0 else ctypes.c_double
            storage_kind = "array"
        self._log_async(
            f"Caching {role} cube in app memory: {width} x {height} x {bands}, "
            f"{estimated_bytes / (1024 ** 2):.1f} MB ({storage_kind})."
        )
        with self.hypercube_read_lock:
            for band_index in range(bands):
                wavelength, values = self.controller.get_hypercube_band_pointer(
                    hypercube_handle,
                    band_index,
                    data_type,
                )
                if np is not None:
                    band_array = np.ctypeslib.as_array(values, shape=(height, width))
                    cube[band_index, :, :] = band_array
                    wavelengths[band_index] = float(wavelength)
                else:
                    band_values = array.array(array_typecode)
                    byte_count = width * height * bytes_per_value
                    band_values.frombytes(ctypes.string_at(ctypes.addressof(values.contents), byte_count))
                    cube.append(band_values)
                    wavelengths.append(float(wavelength))
                if band_index == 0 or (band_index + 1) % 25 == 0 or band_index + 1 == bands:
                    pct = int(round((band_index + 1) * 100 / bands))
                    self._set_run_progress(f"Caching {role} cube: {pct}%", pct, mode="determinate")

        owned_handle = f"owned:{role}:{time.time():.6f}"
        owned_info = dict(info)
        owned_info["storage"] = "owned"
        owned_info["owned_data_role"] = role
        owned_info["owned_bytes"] = int(estimated_bytes)
        owned_data = {
            "array": cube if storage_kind == "numpy" else None,
            "bands": cube if storage_kind == "array" else None,
            "wavelengths": wavelengths,
            "storage_kind": storage_kind,
            "width": width,
            "height": height,
        }
        return owned_handle, owned_data, owned_info

    def _start_data_processing(self, data_handle, data_status, message):
        self.log(f'Acquisition callback received: status={data_status}, message="{message}"')
        self._cancel_acquisition_watchdog()
        self.acquisition_heartbeat_token += 1
        acquisition_start_time = getattr(self, "acquisition_start_perf_time", None)
        if acquisition_start_time is not None:
            acquisition_elapsed = time.perf_counter() - acquisition_start_time
            self.log(f"Performance timing: SDK acquisition callback arrived after {acquisition_elapsed:.2f} s.", detail=True)
        if data_status != HeraController.HYPERSPECTRAL_DATA_OK:
            self.last_acquisition_error = message or "Hyperspectral acquisition failed or was aborted."
            self.acquisition_success = False
            self.acquisition_done_event.set()
            self._set_acquisition_inflight(False)
            self.log(self.last_acquisition_error)
            self._fail_run_progress("Progress: acquisition failed")
            self.update_state("Error")
            return

        if not self.processing_lock.acquire(blocking=False):
            self.last_acquisition_error = "Processing is already running for a previous acquisition."
            self.acquisition_success = False
            self.acquisition_done_event.set()
            self._set_acquisition_inflight(False)
            self.log(self.last_acquisition_error)
            self._fail_run_progress("Progress: processing busy")
            return

        self.update_state("ComputingHypercube")
        self._start_busy_progress("Computing hypercube...")
        worker = threading.Thread(target=self._process_acquisition_worker, args=(data_handle,), daemon=True)
        worker.start()

    def _process_acquisition_worker(self, data_handle):
        hypercube_handle = None
        viewer_bound = False
        process_start_time = time.perf_counter()
        try:
            raw_info_start_time = time.perf_counter()
            width, height, _ = self.controller.get_hyperspectral_data_info(data_handle)
            data_is_hdr = None
            try:
                data_is_hdr = self.controller.get_hyperspectral_data_is_hdr(data_handle)
            except Exception as exc:
                self._log_async(f"Could not read raw hyperspectral HDR flag: {exc}", detail=True)
            data_hdr_text = "unknown" if data_is_hdr is None else ("on" if data_is_hdr else "off")
            pre_start_hdr = getattr(self, "acquisition_pre_start_hdr", None)
            pre_start_hdr_text = "unknown" if pre_start_hdr is None else ("on" if pre_start_hdr else "off")
            self._log_async(
                "Raw hyperspectral data received: "
                f"width={width}, height={height}, dataHDR={data_hdr_text}, "
                f"preStartHDR={pre_start_hdr_text}",
                detail=True,
            )
            if self.acquisition_requested_hdr and data_is_hdr is False:
                self._log_async(
                    "Dynamic Range HDR was requested before acquisition, but the SDK returned Sensitivity 12-bit raw data. "
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

            raw_info_elapsed = time.perf_counter() - raw_info_start_time
            hypercube_compute_start_time = time.perf_counter()
            last_hypercube_progress_pct = {"value": -1}

            def hypercube_progress(progress):
                try:
                    progress_value = float(progress)
                except Exception:
                    return
                fraction = progress_value / 100.0 if progress_value > 1.0 else progress_value
                pct = max(0, min(100, int(round(fraction * 100.0))))
                if pct == last_hypercube_progress_pct["value"]:
                    return
                last_hypercube_progress_pct["value"] = pct
                self._set_run_progress(f"Computing hypercube: {pct}%", pct, mode="determinate")

            hypercube_handle = self.controller.get_hypercube(
                data_handle,
                data_type,
                bands,
                binning,
                progress_handler=hypercube_progress,
            )
            cube_width, cube_height, cube_bands, cube_type = self.controller.get_hypercube_info(hypercube_handle)
            cube_is_hdr = None
            try:
                cube_is_hdr = self.controller.get_hypercube_is_hdr(hypercube_handle)
            except Exception as exc:
                self._log_async(f"Could not read hypercube HDR flag: {exc}", detail=True)
            hypercube_compute_elapsed = time.perf_counter() - hypercube_compute_start_time
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
                f"dataType={cube_type}, cubeHDR={cube_hdr_text}, preStartHDR={pre_start_hdr_text}",
                detail=True,
            )
            self._log_async(
                "Performance timing: "
                f"raw metadata={raw_info_elapsed:.2f} s, "
                f"hypercube compute={hypercube_compute_elapsed:.2f} s, "
                f"bands={bands}, binning={self.param_vars['binning'].get()}, dataType={self.param_vars['data_type'].get()}.",
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
            previous_info = dict(self.current_hypercube_info) if self.current_hypercube_info else None
            keep_previous_sample = bool(
                self.pending_acquisition_role == "flatfield"
                and previous_handle
                and previous_info
                and previous_info.get("role") != "flatfield"
            )
            new_cube_info = {
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
            stored_handle = hypercube_handle
            stored_info = new_cube_info
            stored_data = None
            owned_role = "flatfield" if self.pending_acquisition_role == "flatfield" else "sample"
            owned_handle, owned_data, owned_info = self._copy_hypercube_to_owned_cache(
                hypercube_handle,
                new_cube_info,
                owned_role,
            )
            if owned_handle and owned_data is not None:
                stored_handle = owned_handle
                stored_info = owned_info
                stored_data = owned_data
                try:
                    with self.hypercube_read_lock:
                        self.controller.release_hypercube(hypercube_handle)
                    hypercube_handle = None
                    self._log_async(f"Released SDK {owned_role} hypercube after caching it in app memory.")
                except Exception as exc:
                    self._log_async(f"Could not release SDK {owned_role} hypercube after caching: {exc}")

            if self.pending_acquisition_role != "flatfield":
                self.current_hypercube_handle = stored_handle
                self.current_hypercube_info = stored_info
                self.current_hypercube_data = stored_data
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
                if (
                    self.flatfield_hypercube_handle
                    and self.flatfield_hypercube_handle != stored_handle
                    and not self._is_owned_cube_info(self.flatfield_info)
                ):
                    try:
                        with self.hypercube_read_lock:
                            self.controller.release_hypercube(self.flatfield_hypercube_handle)
                        released_as_flatfield = (self.flatfield_hypercube_handle == previous_handle)
                    except Exception:
                        pass
                self.flatfield_hypercube_handle = stored_handle
                self.flatfield_info = dict(stored_info)
                self.flatfield_hypercube_data = stored_data
                self._set_var_async(self.flatfield_status_var, f"ready ({display_width} x {display_height}, bands={cube_bands})")
                if keep_previous_sample:
                    self.current_hypercube_handle = previous_handle
                    self.current_hypercube_info = previous_info
                    summary = (
                        f"Cube: {previous_info['width']} x {previous_info['height']}, "
                        f"bands={previous_info['bands']}, type={previous_info['data_type']}"
                    )
                    display_sample_roi = previous_info.get("display_roi")
                    if display_sample_roi:
                        summary += f" (ROI x={display_sample_roi[0]}, y={display_sample_roi[1]})"
                    elif previous_info.get("camera_roi"):
                        summary += " (camera ROI)"
                    self._set_var_async(self.hypercube_summary_var, summary)
            elif self.hyper_display_mode_var.get() == "Flatfield":
                next_mode = "Normalized" if self._should_use_flatfield_correction(self.current_hypercube_info) else "Raw"
                self._set_var_async(self.hyper_display_mode_var, next_mode)
            viewer_bound = True
            is_flatfield_acquisition = self.pending_acquisition_role == "flatfield"
            if is_flatfield_acquisition:
                if keep_previous_sample:
                    next_mode = "Normalized" if self._should_use_flatfield_correction(self.current_hypercube_info) else "Raw"
                    self._set_var_async(self.hyper_display_mode_var, next_mode)
                    sample_bands = int(previous_info["bands"])
                    default_band = self._default_hyper_band_index_for_info(previous_handle, previous_info)
                    self._safe_after(
                        0,
                        lambda sample_bands=sample_bands, default_band=default_band: (
                            self.hyper_band_scale.config(to=max(sample_bands - 1, 0)),
                            self.current_hyper_band_index.set(default_band),
                            self.render_current_hyper_band(),
                        ),
                    )
                    self._log_async("Flatfield cube is ready; kept the current sample for Raw/Normalized viewing.")
                else:
                    default_band = self._default_hyper_band_index_for_info(stored_handle, stored_info)
                    self._safe_after(
                        0,
                        lambda default_band=default_band: (
                            self.hyper_band_scale.config(to=max(cube_bands - 1, 0)),
                            self.current_hyper_band_index.set(default_band),
                            self.current_hyper_band_var.set(f"Band: {default_band + 1} / {cube_bands}"),
                            self.current_hyper_wavelength_var.set("Wavelength: -"),
                            self._draw_hyperspectral_view_placeholder("Flatfield ready. Press Export to save it as _ref."),
                        ),
                    )
                    self._log_async("Flatfield cube is ready; deferred preview rendering to avoid a full-frame memory spike.")
            else:
                default_band = self._default_hyper_band_index_for_info(stored_handle, stored_info)
                self._safe_after(
                    0,
                    lambda default_band=default_band: (
                        self.hyper_band_scale.config(to=max(cube_bands - 1, 0)),
                        self.current_hyper_band_index.set(default_band),
                        self.render_current_hyper_band(),
                    ),
                )
                self._log_async("Hyperspectral viewer is ready. Open the Hyperspectral View tab and move the band slider.")
            if previous_handle and not released_as_flatfield and not keep_previous_sample:
                if previous_handle != self.flatfield_hypercube_handle and not self._is_owned_cube_info(previous_info):
                    try:
                        with self.hypercube_read_lock:
                            self.controller.release_hypercube(previous_handle)
                    except Exception:
                        pass

            postprocess_elapsed = time.perf_counter() - process_start_time - hypercube_compute_elapsed
            self._log_async(
                f"Performance timing: post-compute app processing took {postprocess_elapsed:.2f} s.",
                detail=True,
            )

            if not self.pending_acquisition_auto_save and self.pending_acquisition_role == "flatfield":
                self.pending_save_context = {
                    "hypercube_handle": stored_handle,
                    "export_tag": self.pending_export_tag or self._sanitize_export_tag(time.strftime("flatfield_%Y%m%d_%H%M%S")),
                    "requested_roi": self.acquisition_requested_roi,
                    "export_roi": export_roi,
                    "cube_width": cube_width,
                    "cube_height": cube_height,
                    "cube_hdr_text": cube_hdr_text,
                    "cube_is_hdr": cube_is_hdr,
                    "info": dict(self.flatfield_info),
                    "role": "flatfield",
                }
                self.acquisition_success = True
                self.last_acquisition_error = ""
                self._set_var_async(self.flatfield_status_var, f"ready ({display_width} x {display_height}, bands={cube_bands})")
                if not keep_previous_sample:
                    self._set_var_async(self.hyper_display_mode_var, "Flatfield")
                if self.save_pending_button:
                    self._safe_after(0, self._refresh_export_controls_for_display_mode)
                if keep_previous_sample:
                    self._log_async("Flatfield acquired and kept in memory. Use the view mode to export sample products or the flatfield reference.")
                else:
                    self._log_async("Flatfield acquired and kept in memory. Press Export to save it as _ref.")
                self._finish_run_progress("Progress: flatfield ready")
                self._safe_after(0, lambda: self.update_state("Completed"))
                return

            promote_sample_to_flatfield = bool(getattr(self, "promote_next_sample_to_flatfield", False))
            if promote_sample_to_flatfield:
                self.promote_next_sample_to_flatfield = False
                self.flatfield_hypercube_handle = stored_handle
                self.flatfield_info = dict(self.current_hypercube_info)
                self.flatfield_info["role"] = "flatfield"
                if self._is_owned_cube_info(self.current_hypercube_info):
                    self.flatfield_info["owned_data_role"] = "flatfield"
                    self.flatfield_hypercube_data = self.current_hypercube_data
                self._set_var_async(self.flatfield_status_var, f"ready ({display_width} x {display_height}, bands={cube_bands})")
                self.pending_save_context = {
                    "hypercube_handle": stored_handle,
                    "export_tag": self.pending_export_tag or self._sanitize_export_tag(time.strftime("flatfield_%Y%m%d_%H%M%S")),
                    "requested_roi": self.acquisition_requested_roi,
                    "export_roi": export_roi,
                    "cube_width": cube_width,
                    "cube_height": cube_height,
                    "cube_hdr_text": cube_hdr_text,
                    "cube_is_hdr": cube_is_hdr,
                    "info": dict(self.flatfield_info),
                    "role": "flatfield",
                }
                self.acquisition_success = True
                self.last_acquisition_error = ""
                if self.save_pending_button:
                    self._safe_after(0, self._refresh_export_controls_for_display_mode)
                self._set_var_async(self.hyper_display_mode_var, "Flatfield")
                self._log_async(
                    "Flatfield acquired through the sample-safe SDK path and kept in memory. "
                    "Press Export to save it as _ref."
                )
                self._finish_run_progress("Progress: flatfield ready")
                self._safe_after(0, lambda: self.update_state("Completed"))
                return

            if not self.pending_acquisition_auto_save and self.pending_acquisition_role == "sample":
                self.pending_save_context = {
                    "hypercube_handle": stored_handle,
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
                    self._safe_after(0, self._refresh_export_controls_for_display_mode)
                self._log_async("Acquisition complete. Press Export to save the selected data products.")
                self._finish_run_progress("Progress: acquisition complete")
                self._safe_after(0, lambda: self.update_state("Completed"))
                return

            self._safe_after(0, lambda: self.update_state("Saving"))
            self._start_busy_progress("Saving acquisition data...")
            output_dir = self.param_vars["output_path"].get()
            os.makedirs(output_dir, exist_ok=True)
            export_tag = self.pending_export_tag or self._sanitize_export_tag(time.strftime("hera_hypercube_%Y%m%d_%H%M%S"))
            description = self._build_acquisition_description(cube_hdr_text, cube_is_hdr, role=self.pending_acquisition_role)
            if self.pending_acquisition_role == "flatfield":
                hdr_path, saved_paths, measurement_dir = self._export_flatfield_reference_set(
                    stored_handle,
                    export_tag,
                    output_dir,
                    description,
                    self.flatfield_info,
                )
            else:
                hdr_path, saved_paths, measurement_dir = self._export_measurement_set(
                    stored_handle,
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
                self._set_var_async(self.flatfield_status_var, f"ready ({display_width} x {display_height}, bands={cube_bands})")
                self._log_async("Flatfield baseline is ready and saved as _ref.")
            self.acquisition_success = True
            self.last_acquisition_error = ""
            self._finish_run_progress("Progress: saved")
            self._safe_after(0, lambda: self.update_state("Completed"))
        except Exception as exc:
            self.last_acquisition_error = str(exc)
            self.acquisition_success = False
            self._log_async(f"Failed to process hyperspectral data: {exc}")
            self._fail_run_progress("Progress: processing failed")
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
            self.acquisition_start_perf_time = None
            self.acquisition_done_event.set()
            self._set_acquisition_inflight(False)
            self.processing_lock.release()
            if self.resume_live_after_acquisition:
                self.resume_live_after_acquisition = False
                self._safe_after(0, self.start_live_view)
