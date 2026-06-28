import os
import threading
import time

from hera_app.controllers import HeraController, TangoController
from hera_app.helpers.hera_service_client import HeraServiceClient


class DeviceMixin:
    def auto_connect_devices(self):
        self.log("Auto-connect startup sequence running...")
        try:
            self.refresh_device_list()
            if self.devices:
                self.connect_hera()
        except Exception as exc:
            self.log(f"Hera auto-connect skipped: {exc}")

        try:
            tango_dll_path = self.tango_dll_var.get()
            if os.path.exists(tango_dll_path):
                self.connect_stage()
            else:
                self.log(f"Tango auto-connect skipped: DLL not found at {tango_dll_path}")
        except Exception as exc:
            self.log(f"Tango auto-connect skipped: {exc}")

    def refresh_device_list(self):
        try:
            controller = HeraController(dll_path=self.dll_path_var.get())
            devices = controller.enumerate_devices()
            self.devices = devices
            if devices:
                self.selected_device_var.set(f"0: {self._device_title(devices[0])}")
                self.log(f"Found {len(devices)} Hera device(s).")
            else:
                self.selected_device_var.set("(none)")
                self.log("No Hera devices found.")
        except Exception as exc:
            self.log(f"Failed to refresh device list: {exc}")
            self.update_state("Error")

    def _device_title(self, device):
        product = device.ProductName.decode("utf-8", errors="ignore")
        serial = device.SerialNumber.decode("utf-8", errors="ignore")
        return f"{product} ({serial})"

    def _selected_device_index(self):
        title = self.selected_device_var.get()
        if title != "(none)":
            try:
                return int(title.split(":", 1)[0])
            except ValueError:
                pass
        return 0

    def connect_hera(self):
        if self.controller and self.controller.connected:
            self.log("Hera is already connected.")
            return
        self.update_state("Connecting")
        self.log("Connecting to Hera device...")
        try:
            self.controller = HeraController(dll_path=self.dll_path_var.get())
            devices = self.controller.enumerate_devices()
        except Exception as exc:
            self.log(f"Failed during Hera SDK startup: {exc}")
            self.update_state("Error")
            return

        if not devices:
            self.log("No Hera devices found.")
            self.update_state("Error")
            return

        self.devices = devices
        index = self._selected_device_index()
        try:
            self.controller.create_device(self.devices[index])
            self.controller.progress_handler_func = self.on_progress_update
            self.controller.data_handler_func = self.on_hyperspectral_data_acquired
            self.controller.live_error_handler_func = self.on_live_capture_error
            self.controller.live_timeout_handler_func = self.on_live_capture_timeout
            self.controller.live_capture_handler_func = self.on_live_capture_frame
            self.controller.connect()
            self.check_license_status()
            self.initialize_hdr_mode_on_connect()
            self.start_live_view()
            self.update_state("Ready")
            self.log(f"Connected to Hera device {index}: {self._device_title(self.devices[index])}")
        except Exception as exc:
            self.log(f"Failed to connect to Hera device: {exc}")
            self.update_state("Error")

    def _get_hera_service_client(self):
        client = getattr(self, "hera_service_client", None)
        if client and client.is_running():
            return client
        client = HeraServiceClient(log_func=lambda message: self._log_async(message))
        self.hera_service_client = client
        return client

    def run_hera_helper_probe(self):
        if getattr(self, "hera_service_probe_inflight", False):
            self.log("Hera helper probe is already running.")
            return
        if self._hera_disconnect_or_acquisition_busy():
            self.log("Helper Probe cannot start while Hera acquisition, saving, or disconnect is active.")
            return
        dll_path = self.dll_path_var.get()
        device_index = self._selected_device_index()
        self.hera_service_probe_inflight = True
        self.log("Starting Hera helper probe...")
        self._start_busy_progress("Helper probe: preparing")

        def worker():
            try:
                if self.controller and self.controller.connected:
                    if not self._release_main_hera_connection("Helper Probe", update_state_after_release=False):
                        raise RuntimeError("Main Hera connection could not be released before Helper Probe.")
                client = self._get_hera_service_client()
                client.start()
                self._log_async("Hera helper service is ready.")
                before = client.request("status")
                self._log_helper_service_result("Helper status before connect", before)
                connected = client.request(
                    "connect",
                    dll_path=dll_path,
                    device_index=device_index,
                    timeout_sec=30.0,
                )
                self._log_helper_service_result("Helper connect", connected)
                after = client.request("status")
                self._log_helper_service_result("Helper status after connect", after)
                disconnected = client.request("disconnect")
                self._log_helper_service_result("Helper disconnect", disconnected)
                shutdown = client.shutdown()
                self.hera_service_client = None
                self._log_helper_service_result("Helper shutdown", shutdown or {})
                self._finish_run_progress("Progress: helper probe complete")
                self._safe_after(0, lambda: self.update_state("Ready"))
            except Exception as exc:
                self._log_async(f"Hera helper probe failed: {exc}")
                self._fail_run_progress("Progress: helper probe failed")
                self._safe_after(0, lambda: self.update_state("Error"))
            finally:
                self.hera_service_probe_inflight = False

        threading.Thread(target=worker, daemon=True).start()

    def _hera_disconnect_or_acquisition_busy(self):
        busy_states = {
            self.STATE_LABELS["WaitingForTrigger"],
            self.STATE_LABELS["Acquiring"],
            self.STATE_LABELS["ComputingHypercube"],
            self.STATE_LABELS["Saving"],
            self.STATE_LABELS["RunningTimelapse"],
        }
        start_lock = getattr(self, "acquisition_start_lock", None)
        if getattr(self, "acquisition_inflight", False):
            return True
        if start_lock and start_lock.locked():
            return True
        if getattr(self, "app_state", "") in busy_states:
            return True
        return bool(getattr(self, "hera_disconnect_inflight", False))

    def disconnect_hera_async(self):
        if getattr(self, "hera_disconnect_inflight", False):
            self.log("Hera disconnect is already running.")
            return
        if self._hera_disconnect_or_acquisition_busy():
            self.log("Cannot disconnect Hera while acquisition, saving, timelapse, or another disconnect is active.")
            return
        if not self.controller or not self.controller.device_handle:
            self.log("No Hera device is connected.")
            return
        self.hera_disconnect_inflight = True
        self.log("Disconnecting Hera from the main app...")
        self._start_busy_progress("Disconnecting Hera...")

        def worker():
            try:
                released = self._release_main_hera_connection("manual disconnect")
                if released:
                    self._finish_run_progress("Progress: Hera disconnected")
                else:
                    self._fail_run_progress("Progress: Hera disconnect failed")
            except Exception as exc:
                self._log_async(f"Failed to disconnect Hera device: {exc}")
                self._fail_run_progress("Progress: Hera disconnect failed")
                self._safe_after(0, lambda: self.update_state("Error"))
            finally:
                self.hera_disconnect_inflight = False

        threading.Thread(target=worker, daemon=True).start()

    def _release_main_hera_connection(self, purpose, update_state_after_release=True, clear_cached_data=True):
        controller = self.controller
        if not controller or not controller.device_handle:
            if update_state_after_release:
                self._safe_after(0, lambda: self.update_state("Idle"))
            return True

        disconnect_lock = getattr(self, "hera_disconnect_lock", None)
        if disconnect_lock and not disconnect_lock.acquire(blocking=False):
            self._log_async("Main Hera disconnect is already in progress.")
            return False

        release_ok = True
        try:
            self._log_async(f"Releasing main Hera connection for {purpose}...")
            self.live_accept_frames = False
            try:
                if controller.connected and controller.is_live_capturing():
                    self._log_async("Stopping main Hera live capture before release.", detail=True)
                    controller.stop_live_capture(silent=True)
                    controller.wait_for_live_capture_stopped(timeout_sec=5.0)
            except Exception as exc:
                self._log_async(f"Could not fully stop live capture before Hera release: {exc}")
            try:
                controller.unregister_live_callbacks()
            except Exception as exc:
                self._log_async(f"Could not unregister live callbacks before Hera release: {exc}", detail=True)
            try:
                controller.unregister_callbacks()
            except Exception as exc:
                self._log_async(f"Could not unregister acquisition callbacks before Hera release: {exc}", detail=True)
            try:
                if controller.connected:
                    controller.disconnect()
            except Exception as exc:
                self._log_async(f"Could not disconnect Hera controller cleanly: {exc}")
            try:
                controller.release_device()
            except Exception as exc:
                release_ok = False
                self._log_async(f"Could not release Hera device cleanly: {exc}")

            if self.controller is controller:
                self.controller = None

            def finish_ui_cleanup():
                self._clear_live_view_frame_state()
                if clear_cached_data:
                    self._clear_hypercube_viewer()
                    self.clear_flatfield()
                self.license_var.set("Unknown")
                self.hdr_status_var.set(self.hdr_status_text(None))
                self.hdr_enabled_var.set(False)
                self.license_ok_seen = False
                if clear_cached_data:
                    self.last_export_var.set("Last export: -")
                self._set_live_view_status("Live view: disconnected")
                if update_state_after_release:
                    self.update_state("Idle" if release_ok else "Error")
                if release_ok:
                    self.log("Disconnected from Hera device.")

            self._safe_after(0, finish_ui_cleanup)
            return release_ok
        finally:
            if disconnect_lock:
                disconnect_lock.release()

    def _log_helper_service_result(self, label, result):
        if result is None:
            self._log_async(f"{label}: no result")
            return
        device = result.get("device") or {}
        parts = []
        if "connected" in result:
            parts.append(f"connected={result.get('connected')}")
        if result.get("already_released"):
            parts.append("already released")
        elif "released" in result:
            parts.append(f"released={result.get('released')}")
        if result.get("shutdown"):
            parts.append("shutdown=True")
        if device:
            parts.append(f"device={device.get('product', '-') } ({device.get('serial', '-')})")
        if "licensed" in result:
            parts.append(f"licensed={result.get('licensed')}")
        if "hdr" in result:
            parts.append(f"HDR={result.get('hdr')}")
        if "roi" in result:
            parts.append(f"ROI={result.get('roi')}")
        if "acquiring" in result:
            parts.append(f"acquiring={result.get('acquiring')}")
        if "live_capturing" in result:
            parts.append(f"live={result.get('live_capturing')}")
        errors = result.get("errors")
        if errors:
            parts.append(f"errors={errors}")
        self._log_async(f"{label}: " + ", ".join(parts))

    def stop_hera_helper_service(self):
        client = getattr(self, "hera_service_client", None)
        if not client or not client.is_running():
            self.log("Hera helper service is not running.")
            return
        self.log("Stopping Hera helper service...")

        def worker():
            try:
                result = client.shutdown()
                self.hera_service_client = None
                self._log_helper_service_result("Helper shutdown", result or {})
                self._finish_run_progress("Progress: helper stopped")
            except Exception as exc:
                self._log_async(f"Could not stop Hera helper service cleanly: {exc}")
                try:
                    client.kill()
                except Exception:
                    pass
                self.hera_service_client = None
                self._fail_run_progress("Progress: helper killed")

        threading.Thread(target=worker, daemon=True).start()

    def disconnect_hera(self):
        if not self.controller or not self.controller.device_handle:
            self.log("No Hera device is connected.")
            return
        try:
            self.stop_live_view()
            self._clear_hypercube_viewer()
            self.clear_flatfield()
            if self.controller.connected:
                self.controller.disconnect()
            self.controller.release_device()
            self.license_var.set("Unknown")
            self.hdr_status_var.set(self.hdr_status_text(None))
            self.hdr_enabled_var.set(False)
            self.license_ok_seen = False
            self.last_export_var.set("Last export: -")
            self.update_state("Idle")
            self.log("Disconnected from Hera device.")
        except Exception as exc:
            self.log(f"Failed to disconnect Hera device: {exc}")
            self.update_state("Error")

    def connect_stage(self):
        if self.tango and self.tango.connected:
            self.log("Tango stage is already connected.")
            return
        try:
            self.log("Connecting to Tango stage...")
            self.tango = TangoController(dll_path=self.tango_dll_var.get())
            self.tango.connect(
                interface_type=TangoController.INTERFACE_RS232,
                com_port=self.stage_port_var.get().strip(),
                baud_rate=int(self.stage_baud_var.get()),
                show_protocol=False,
            )
            self.stage_status_var.set(f"Stage: connected on {self.stage_port_var.get().strip()}")
            self.stage_version_var.set(f"Controller: {self.tango.get_version()}")
            self.apply_stage_motion_settings()
            self.update_stage_position_display()
            self.log("Tango stage connected and verified.")
        except Exception as exc:
            self.stage_status_var.set("Stage: connection failed")
            self.log(f"Failed to connect stage: {exc}")
            self.update_state("Error")

    def reconnect_stage(self):
        self.disconnect_stage()
        time.sleep(0.2)
        self.connect_stage()

    def disconnect_stage(self):
        try:
            if self.tango:
                self.tango.disconnect()
            self.stage_status_var.set("Stage: not connected")
            self.stage_version_var.set("Controller: -")
            self.stage_position_var.set("X: -, Y: -")
            self.current_x_label.config(text="X: -")
            self.current_y_label.config(text="Y: -")
            self.latest_stage_xy = None
            self._update_live_cursor_readout()
            self.log("Tango stage disconnected.")
        except Exception as exc:
            self.log(f"Failed to disconnect stage: {exc}")
            self.update_state("Error")

    def preflight_check(self):
        from tkinter import messagebox
        self.log("Running preflight checks...")
        errors = []
        if not os.path.exists(self.dll_path_var.get()):
            errors.append("Hera SDK DLL path does not exist.")
        hera_devices = HeraController.get_hera_devices_path()
        self.env_var.set(hera_devices or "")
        if not hera_devices:
            errors.append("HERA_DEVICES environment variable is not set.")
        elif not os.path.exists(hera_devices):
            errors.append("HERA_DEVICES path does not exist.")
        if not os.path.exists(self.tango_dll_var.get()):
            errors.append("Tango DLL path does not exist.")

        output_path = self.param_vars["output_path"].get()
        if not os.path.exists(output_path):
            try:
                os.makedirs(output_path, exist_ok=True)
            except Exception as exc:
                errors.append(f"Cannot create output folder: {exc}")

        if self.controller and self.controller.connected and not self.check_license_status():
            errors.append("SDK license is not active.")

        if errors:
            for err in errors:
                self.log(f"Preflight error: {err}")
            self.update_state("Error")
            messagebox.showwarning("Preflight failed", "\n".join(errors))
        else:
            self.log("Preflight passed.")
            if self.controller and self.controller.connected:
                self.update_state("Ready")

    def show_sdk_version(self):
        try:
            controller = self.controller or HeraController(dll_path=self.dll_path_var.get())
            status, version = controller.get_api_version()
            if status == 0:
                self.log(f"Hera SDK version: {version[0]}.{version[1]}.{version[2]}")
            else:
                self.log("Failed to get Hera SDK version.")
        except Exception as exc:
            self.log(f"Failed to read SDK version: {exc}")

    def check_license_status(self, allow_cached=False):
        if not self.controller:
            self._set_license_status_text("Unknown")
            return False
        if allow_cached and self.license_ok_seen:
            self._set_license_status_text("Licensed")
            self._log_async("Using cached Hera license status for acquisition start.", detail=True)
            return True
        try:
            status, licensed, expiry_license, expiry_cert = self.controller.is_licensed()
        except Exception as exc:
            if allow_cached and self.license_ok_seen:
                self._set_license_status_text("Licensed")
                self._log_async(f"License recheck failed after an earlier successful check; continuing: {exc}")
                return True
            self._set_license_status_text("License check failed")
            self._log_async(f"License check failed: {exc}")
            return False
        if status != 0:
            if allow_cached and self.license_ok_seen:
                self._set_license_status_text("Licensed")
                self._log_async(
                    "License recheck returned an SDK error after an earlier successful check; "
                    f"continuing: {self.controller.get_last_error()}"
                )
                return True
            self._set_license_status_text("License check failed")
            self._log_async(self.controller.get_last_error())
            return False
        if licensed:
            self._set_license_status_text("Licensed")
            self.license_ok_seen = True
            self._log_async(f"Hera SDK is licensed. License expiry UTC={expiry_license}, certificate expiry UTC={expiry_cert}")
            return True
        if allow_cached and self.license_ok_seen:
            self._set_license_status_text("Licensed")
            self._log_async("License recheck reported inactive after an earlier successful check; continuing with acquisition.")
            return True
        self._set_license_status_text("Not licensed")
        self._log_async("Hera SDK license is not active.")
        return False

    def _set_license_status_text(self, text):
        if self._is_ui_thread():
            self.license_var.set(text)
        else:
            self._set_var_async(self.license_var, text)

    def refresh_hdr_status(self):
        if not self.controller or not self.controller.connected:
            self.hdr_status_var.set(self.hdr_status_text(None))
            return False
        try:
            if not self.controller.is_hdr_supported():
                self.hdr_enabled_var.set(False)
                self.hdr_status_var.set("HDR mode: not supported")
                self.log("HDR mode is not supported by this Hera device or SDK DLL.", detail=True)
                return False
            actual_hdr = self.controller.get_hdr()
            self.hdr_enabled_var.set(actual_hdr)
            self.hdr_status_var.set(self.hdr_status_text(actual_hdr))
            self.log(f"HDR mode supported. Current camera HDR mode: {self.hdr_mode_text(actual_hdr)}.", detail=True)
            return True
        except Exception as exc:
            self.hdr_status_var.set("HDR mode: check failed")
            self.log(f"Could not read HDR support/status: {exc}", detail=True)
            return False

    def initialize_hdr_mode_on_connect(self):
        if not self.controller or not self.controller.connected:
            self.hdr_enabled_var.set(False)
            self.hdr_status_var.set(self.hdr_status_text(None))
            return False
        try:
            if not self.controller.is_hdr_supported():
                self.hdr_enabled_var.set(False)
                self.hdr_status_var.set("HDR mode: not supported")
                self.log("HDR mode is not supported by this Hera device or SDK DLL.", detail=True)
                return False

            previous_hdr = None
            try:
                previous_hdr = self.controller.get_hdr()
            except Exception as exc:
                self.log(f"Could not read previous camera HDR mode during connect: {exc}", detail=True)

            requested_hdr = bool(getattr(self, "hdr_startup_default_enabled", False))
            self.controller.set_hdr(requested_hdr)
            time.sleep(0.2)
            actual_hdr = self.controller.get_hdr()
            self.hdr_enabled_var.set(actual_hdr)
            self.hdr_status_var.set(self.hdr_status_text(actual_hdr))
            previous_text = self.hdr_mode_text(previous_hdr)
            self.log(
                "HDR mode supported. Startup HDR "
                f"requested={self.hdr_mode_text(requested_hdr)}, previous={previous_text}, "
                f"actual={self.hdr_mode_text(actual_hdr)}.",
                detail=True,
            )
            return True
        except Exception as exc:
            try:
                actual_hdr = self.controller.get_hdr()
                self.hdr_enabled_var.set(actual_hdr)
                self.hdr_status_var.set(self.hdr_status_text(actual_hdr))
                self.log(
                    "Could not apply startup HDR default; using camera readback "
                    f"{self.hdr_mode_text(actual_hdr)}: {exc}",
                    detail=True,
                )
            except Exception:
                self.hdr_enabled_var.set(False)
                self.hdr_status_var.set("HDR mode: check failed")
                self.log(f"Could not initialize HDR mode on connect: {exc}", detail=True)
            return False
