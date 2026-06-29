import ctypes
import faulthandler
import msvcrt
import os
import queue
import sys
import tempfile
import threading
import tkinter as tk
import traceback
from datetime import datetime

from hera_app.controllers import HeraController, NISZBridgeController
from hera_app.mixins.acquisition import AcquisitionMixin
from hera_app.mixins.device import DeviceMixin
from hera_app.mixins.export import ExportMixin
from hera_app.mixins.flatfield import FlatfieldMixin
from hera_app.mixins.hyperspectral_viewer import HyperspectralViewerMixin
from hera_app.mixins.live_view import LiveViewMixin
from hera_app.mixins.nis_z_mixin import NISZMixin
from hera_app.mixins.roi import ROIMixin
from hera_app.mixins.stage import StageMixin
from hera_app.mixins.theme import ThemeMixin
from hera_app.mixins.timelapse import TimelapseMixin
from hera_app.mixins.ui_builder import UIBuilderMixin
from hera_app.mixins.utils import UtilsMixin


class HeraTriggerApp(
    tk.Tk,
    UtilsMixin,
    ThemeMixin,
    UIBuilderMixin,
    DeviceMixin,
    NISZMixin,
    StageMixin,
    ExportMixin,
    FlatfieldMixin,
    AcquisitionMixin,
    TimelapseMixin,
    LiveViewMixin,
    ROIMixin,
    HyperspectralViewerMixin,
):
    STATE_LABELS = {
        "Idle": "Idle",
        "Connecting": "Connecting...",
        "Ready": "Ready",
        "WaitingForTrigger": "Waiting for trigger",
        "Acquiring": "Acquiring",
        "ComputingHypercube": "Computing hypercube",
        "Saving": "Saving",
        "Completed": "Completed",
        "RunningTimelapse": "Running timelapse",
        "Paused": "Paused",
        "Error": "Error",
    }

    SCAN_MODES = {"Low": 0, "Medium": 1, "High": 2, "Extra High": 3}
    TRIGGER_MODES = {"Internal": 0, "DeferredStartExtLineHi": 1, "StepScanExtLoHi": 2}
    BINNING_OPTIONS = {"None": 0, "2x": 1, "4x": 2, "8x": 3, "2x Sharp": 0x1000, "4x Sharp": 0x1001}
    DATA_TYPES = {"SinglePrecision": 0, "DoublePrecision": 1}
    SPECTRAL_SAMPLING = {"Uniform lambda": 0, "Uniform nu": 1}
    LIVE_PIXEL_FORMATS = {
        0: "Mono8",
        1: "Mono10",
        2: "Mono12",
        3: "Mono14",
        4: "Mono16",
    }
    HDR_DYNAMIC_RANGE_TEXT = "Dynamic Range 16-bit HDR"
    HDR_DYNAMIC_RANGE_SHORT_TEXT = "Dynamic Range HDR"
    HDR_SENSITIVITY_TEXT = "Sensitivity 12-bit"
    HDR_CHECKBOX_TEXT = "Dynamic Range (16-bit HDR)"

    @classmethod
    def hdr_mode_text(cls, enabled, short=False):
        if enabled is None:
            return "unknown"
        if bool(enabled):
            return cls.HDR_DYNAMIC_RANGE_SHORT_TEXT if short else cls.HDR_DYNAMIC_RANGE_TEXT
        return cls.HDR_SENSITIVITY_TEXT

    @classmethod
    def hdr_status_text(cls, enabled):
        if enabled is None:
            return "HDR mode: unknown"
        return f"HDR mode: {cls.hdr_mode_text(enabled)}"

    @staticmethod
    def default_tango_dll_path():
        base = os.path.abspath(os.path.dirname(__file__))
        project_root = os.path.abspath(os.path.join(base, ".."))
        for search_dir in (base, project_root):
            candidate = os.path.join(search_dir, "Tango_DLL.dll")
            if os.path.exists(candidate):
                return candidate
        return os.path.join(project_root, "Tango_DLL.dll")

    def __init__(self):
        super().__init__()
        self.ui_thread_id = threading.get_ident()
        self.ui_call_queue = queue.Queue()
        self.ui_queue_poll_job = None
        self.ui_queue_poll_interval_ms = 25
        self.title("Hera + Tango Trigger Control")
        self.geometry("1400x900")
        self.minsize(1240, 800)
        self.theme_mode = "dark"
        self.theme_button_var = tk.StringVar(value="Light Mode")

        self.controller = None
        self.tango = None
        self.devices = []
        self.positions = []
        self.selected_position_index = None
        self.processing_lock = threading.Lock()
        self.acquisition_start_lock = threading.Lock()
        self.hera_disconnect_lock = threading.Lock()
        self.stage_lock = threading.Lock()
        self.live_frame_lock = threading.Lock()
        self.hypercube_read_lock = threading.Lock()
        self.parameter_apply_lock = threading.Lock()
        self.app_state = self.STATE_LABELS["Idle"]
        self.stage_poll_job = None
        self._auto_apply_parameters_job = None
        self.timelapse_thread = None
        self.timelapse_stop_event = threading.Event()
        self.timelapse_pause_event = threading.Event()
        self.timelapse_run_id = 0
        self.timelapse_roi = None
        self.acquisition_inflight = False
        self.stage_motion_inflight = False
        self.acquisition_watchdog_token = 0
        self.acquisition_heartbeat_token = 0
        self.acquisition_done_event = threading.Event()
        self.acquisition_success = False
        self.last_export_path = ""
        self.last_acquisition_error = ""
        self.trigger_log = []
        self.dll_path_var = tk.StringVar(value=HeraController.default_dll_path())
        self.env_var = tk.StringVar(value=HeraController.get_hera_devices_path() or "")
        self.license_var = tk.StringVar(value="Unknown")
        self.license_ok_seen = False
        self.selected_device_var = tk.StringVar(value="(none)")
        self.tango_dll_var = tk.StringVar(value=self.default_tango_dll_path())
        self.stage_port_var = tk.StringVar(value="COM7")
        self.stage_baud_var = tk.IntVar(value=57600)
        self.stage_interface_var = tk.StringVar(value="RS232 / COM")
        self.timelapse_status_var = tk.StringVar(value="Timelapse: idle")
        self.time_remaining_var = tk.StringVar(value="Time remaining: -")
        self.center_stage_summary_var = tk.StringVar(value="Selected position: none")
        self.current_cycle_var = tk.StringVar(value="Cycle: -")
        self.current_site_var = tk.StringVar(value="Site: -")
        self.last_export_var = tk.StringVar(value="Last export: -")
        self.run_progress_var = tk.DoubleVar(value=0.0)
        self.run_progress_text_var = tk.StringVar(value="Progress: idle")
        self.run_progress_mode = "determinate"
        self.default_output_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), "output")
        self.show_detail_log_var = tk.BooleanVar(value=False)
        self.detail_log_messages = []
        self.background_log_path = os.path.join(self.default_output_dir, "hera_background_status.log")
        self.last_issues_log_path = os.path.join(self.default_output_dir, "hera_last_issues.log")
        self.fatal_crash_log_path = os.path.join(self.default_output_dir, "hera_fatal_crash.log")
        self._fatal_crash_log_file = None
        self.detail_log_path = self.background_log_path
        self.recent_issue_messages = []
        self._original_sys_excepthook = sys.excepthook
        self._original_threading_excepthook = getattr(threading, "excepthook", None)
        self._installed_sys_excepthook = None
        self._installed_threading_excepthook = None
        self._install_fatal_crash_logging()
        self._install_background_exception_logging()
        self._write_startup_log_marker()
        self.hyperlab_shortcut_var = tk.StringVar(value=r"C:\Users\Public\Desktop\Nireos HyperLAB.lnk")
        self.hypercube_summary_var = tk.StringVar(value="Cube: waiting for acquisition")
        self.live_view_status_var = tk.StringVar(value="Live view: waiting for frames")
        self.hdr_enabled_var = tk.BooleanVar(value=False)
        self.hdr_status_var = tk.StringVar(value=self.hdr_status_text(None))
        self.hdr_startup_default_enabled = False
        self.live_cursor_var = tk.StringVar(value=self._live_cursor_status_text("-"))
        self.pending_export_tag = None
        self.live_photo = None
        self.live_frame_info = None
        self.latest_live_frame = None
        self.latest_live_profile = None
        self.latest_live_is_hdr = None
        self.latest_live_mode_text = "unknown"
        self.live_hdr_requested = False
        self.live_autocontrast_var = tk.BooleanVar(value=True)
        self.live_show_saturation_var = tk.BooleanVar(value=True)
        self.live_cross_enabled_var = tk.BooleanVar(value=False)
        self.live_profile_status_var = tk.StringVar(value="Cross: center")
        self.live_cross_point = None
        self.live_gamma_var = tk.DoubleVar(value=1.0)
        self.live_gamma_label_var = tk.StringVar(value="Gamma Value 1.0")
        self.live_zoom_factor = 1.0
        self.live_zoom_label_var = tk.StringVar(value="Zoom 100%")
        self.live_pan_x = 0.0
        self.live_pan_y = 0.0
        self.live_pan_drag_start = None
        self.live_display_rect = None
        self.live_display_frame_size = None
        self.live_sensor_frame_size = None
        self.live_cursor_image_xy = None
        self.live_roi_selecting = False
        self.live_roi_points = []
        self.live_roi_rect = None
        self.roi_fields_initialized_from_live_frame = False
        self.roi_fields_frame_size = None
        self.roi_selection_active = False
        self.selected_export_roi = None
        self.live_view_crop_roi = None
        self._live_crop_offset = (0, 0)
        self.live_roi_button_var = tk.StringVar(value="Select ROI")
        self.live_roi_status_var = tk.StringVar(value="ROI: -")
        self.latest_stage_xy = None
        self.live_pixel_format_name = "Unknown"
        self.saving_notes_var = tk.StringVar(value="")
        self.live_first_frame_logged = False
        self.live_first_frame_rendered = False
        self.live_watchdog_job = None
        self.live_accept_frames = False
        self.live_render_pending = False
        self.last_live_render_time = 0.0
        self.live_render_interval_sec = 0.10
        self.resume_live_after_acquisition = False
        self.last_applied_roi = None
        self.acquisition_requested_roi = None
        self.acquisition_camera_roi = None
        self.acquisition_requested_hdr = False
        self.acquisition_pre_start_hdr = None
        self.acquisition_start_perf_time = None
        self.last_acquisition_progress_time = None
        self.last_acquisition_heartbeat_log_sec = 0
        self.helper_acquisition_enabled = True
        self.helper_acquisition_process = None
        self.helper_acquisition_request_id = None
        self.helper_acquisition_timeout_sec = 900
        self.helper_process_timeout_sec = 1200
        self.hera_service_client = None
        self.hera_service_probe_inflight = False
        self.hera_service_acquisition_inflight = False
        self.hera_disconnect_inflight = False
        self.hdr_pixel_format_diagnostics_enabled = False
        self.live_max_preview_width = 480
        self.live_display_rotation_degrees = 90
        self.live_auth_warning_logged = False
        self.last_live_decode_error = ""
        self.is_closing = False
        self.shutdown_complete_event = threading.Event()
        self.shutdown_thread = None
        self.shutdown_watchdog_timer = None
        self.hyper_photo = None
        self.current_hypercube_handle = None
        self.current_hypercube_info = None
        self.current_hypercube_data = None
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
        self.hyper_spectrum_y_limits = None
        self.hyper_spectrum_request_ids = {"selected": 0, "cursor": 0, "warmup": 0}
        self.hyper_cursor_spectrum_inflight = False
        self.hyper_cursor_pending_pixel = None
        self.hyper_cross_enabled_var = tk.BooleanVar(value=True)
        self.hyper_display_mode_var = tk.StringVar(value="Normalized")
        self.default_hyper_wavelength_nm = 600.0
        self.hyper_display_rect = None
        self.flatfield_hypercube_handle = None
        self.flatfield_info = None
        self.flatfield_hypercube_data = None
        self.owned_cube_cache_max_bytes = 900 * 1024 * 1024
        self.flatfield_status_var = tk.StringVar(value="none")
        self.flatfield_keep_sample_var = tk.BooleanVar(value=False)
        self.flatfield_output_path_var = tk.StringVar(value=self.default_output_dir)
        self.flatfield_name_var = tk.StringVar(value="flatfield")
        self.flatfield_append_time_var = tk.BooleanVar(value=True)
        self.export_name_var = tk.StringVar(value="")
        self.export_append_time_var = tk.BooleanVar(value=False)
        self.export_raw_var = tk.BooleanVar(value=False)
        self.export_flatfield_var = tk.BooleanVar(value=False)
        self.export_normalized_var = tk.BooleanVar(value=False)
        self.pending_acquisition_role = "sample"
        self.promote_next_sample_to_flatfield = False
        self.pending_save_context = None
        self.pending_acquisition_auto_save = True
        self.save_pending_button = None
        self.flatfield_acquire_button = None
        self.flatfield_use_current_button = None
        self.flatfield_clear_button = None
        self.start_acquisition_buttons = []
        self.current_hyper_band_index = tk.IntVar(value=0)
        self.hyper_band_jump_var = tk.StringVar(value="1")
        self.current_hyper_wavelength_var = tk.StringVar(value="Wavelength: -")
        self.current_hyper_band_var = tk.StringVar(value="Band: -")
        self.nis_z = None
        self.nis_z_shared_root_var = tk.StringVar(value=NISZBridgeController.DEFAULT_SHARED_ROOT)
        self.nis_z_current_z_var = tk.StringVar(value="Z: -")
        self.nis_z_status_var = tk.StringVar(value="NIS Z: idle")
        self.nis_z_timeout_var = tk.IntVar(value=90)
        self.nis_z_step_var = tk.StringVar(value="1.0")
        self.nis_z_tolerance_var = tk.DoubleVar(value=0.5)
        self.z_motion_enabled = False
        if not self.z_motion_enabled:
            self.nis_z_current_z_var.set("Z: disabled")
            self.nis_z_status_var.set("Z motion: disabled")
        self.nis_z_last_value = None
        self.nis_z_last_status = "not checked"
        self.nis_z_poll_job = None
        self.nis_z_poll_inflight = False
        self.nis_z_request_lock = threading.Lock()
        self.nis_z_poll_interval_ms = 30000
        self.dummy_z_position = 0.0
        self._configure_theme()
        self._build_ui()
        self.refresh_positions_tree()
        self.update_state("Idle")
        self._start_ui_call_queue_pump()
        self.start_stage_polling()
        self._safe_after(250, self.auto_connect_devices)
        if self.z_motion_enabled:
            self._safe_after(5000, self.start_nis_z_polling)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _live_cursor_status_text(self, text):
        return f"{text:<48}"[:48]

    def update_state(self, state_key):
        label = self.STATE_LABELS.get(state_key, state_key)
        self.app_state = label
        if hasattr(self, "app_state_var"):
            self.app_state_var.set(label)
        if hasattr(self, "app_state_label"):
            self.app_state_label.config(fg="red" if state_key == "Error" else "green")
        if hasattr(self, "right_app_state_label"):
            self.right_app_state_label.config(fg="red" if state_key == "Error" else "#7ad97a")
        if hasattr(self, "_refresh_run_action_controls"):
            self._refresh_run_action_controls()

    def _log_timestamp(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _rotate_log_if_large(self, path, max_bytes=8 * 1024 * 1024):
        try:
            if os.path.exists(path) and os.path.getsize(path) > max_bytes:
                backup_path = f"{path}.old"
                if os.path.exists(backup_path):
                    os.remove(backup_path)
                os.replace(path, backup_path)
        except Exception:
            pass

    def _write_log_file_lines(self, path, lines, mode="a"):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._rotate_log_if_large(path)
            timestamp = self._log_timestamp()
            with open(path, mode, encoding="utf-8") as log_file:
                for line in lines:
                    text = str(line)
                    split_lines = text.splitlines() or [""]
                    for split_line in split_lines:
                        log_file.write(f"{timestamp} | {split_line}\n")
                log_file.flush()
        except Exception:
            pass

    def _write_detail_log_line(self, message):
        self._write_log_file_lines(self.detail_log_path, [message])

    def _write_startup_log_marker(self):
        self._write_log_file_lines(
            self.detail_log_path,
            [
                "",
                "=== HERA app started ===",
                f"Background log: {self.detail_log_path}",
                f"Last issues summary: {self.last_issues_log_path}",
                f"Fatal crash log: {self.fatal_crash_log_path}",
            ],
        )
        self._write_last_issues_log()

    def _install_fatal_crash_logging(self):
        try:
            os.makedirs(os.path.dirname(self.fatal_crash_log_path), exist_ok=True)
            self._fatal_crash_log_file = open(self.fatal_crash_log_path, "a", encoding="utf-8")
            self._fatal_crash_log_file.write(f"\n{self._log_timestamp()} | === fatal crash logging enabled ===\n")
            self._fatal_crash_log_file.flush()
            faulthandler.enable(file=self._fatal_crash_log_file, all_threads=True)
        except Exception:
            self._fatal_crash_log_file = None

    def _restore_fatal_crash_logging(self):
        try:
            if faulthandler.is_enabled():
                faulthandler.disable()
        except Exception:
            pass
        log_file = getattr(self, "_fatal_crash_log_file", None)
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass
        self._fatal_crash_log_file = None

    def _safe_log_var(self, var, fallback="-"):
        try:
            return var.get()
        except Exception:
            return fallback

    def _is_issue_log_message(self, message):
        lower = str(message).lower()
        issue_tokens = (
            "failed",
            "error",
            "warning",
            "exception",
            "traceback",
            "crash",
            "timeout",
            "timed out",
            "aborted",
            "invalid",
            "could not",
            "unable",
        )
        return any(token in lower for token in issue_tokens)

    def _write_last_issues_log(self, latest_trace=None):
        lines = [
            "HERA last issues summary",
            f"Updated: {self._log_timestamp()}",
            f"App state: {getattr(self, 'app_state', '-')}",
            f"Current site: {self._safe_log_var(getattr(self, 'current_site_var', None))}",
            f"Cycle: {self._safe_log_var(getattr(self, 'current_cycle_var', None))}",
            f"Last export: {self._safe_log_var(getattr(self, 'last_export_var', None))}",
            f"Full background log: {self.detail_log_path}",
            "",
            "Recent issues:",
        ]
        if self.recent_issue_messages:
            lines.extend(self.recent_issue_messages[-40:])
        else:
            lines.append("No issue messages recorded in this app session yet.")
        lines.extend(["", "Recent background log tail:"])
        for text, _is_detail in self.detail_log_messages[-80:]:
            lines.append(f"- {text}")
        if latest_trace:
            lines.extend(["", "Latest traceback:", latest_trace])
        self._write_log_file_lines(self.last_issues_log_path, lines, mode="w")

    def _record_recent_issue(self, message, trace_text=None):
        entry = f"{self._log_timestamp()} | {message}"
        if trace_text:
            entry = f"{entry}\n{trace_text.rstrip()}"
        self.recent_issue_messages.append(entry)
        del self.recent_issue_messages[:-80]
        self._write_last_issues_log(latest_trace=trace_text)

    def _install_background_exception_logging(self):
        self._installed_sys_excepthook = self._handle_sys_exception
        sys.excepthook = self._installed_sys_excepthook
        if hasattr(threading, "excepthook"):
            self._installed_threading_excepthook = self._handle_thread_exception
            threading.excepthook = self._installed_threading_excepthook

    def _restore_background_exception_logging(self):
        try:
            if self._installed_sys_excepthook and sys.excepthook == self._installed_sys_excepthook:
                sys.excepthook = self._original_sys_excepthook
        except Exception:
            pass
        try:
            if (
                self._installed_threading_excepthook
                and hasattr(threading, "excepthook")
                and threading.excepthook == self._installed_threading_excepthook
            ):
                threading.excepthook = self._original_threading_excepthook
        except Exception:
            pass

    def _handle_sys_exception(self, exc_type, exc_value, exc_traceback):
        self._log_unhandled_exception("Unhandled Python exception", exc_type, exc_value, exc_traceback)
        original = self._original_sys_excepthook
        if original and original != self._installed_sys_excepthook:
            original(exc_type, exc_value, exc_traceback)

    def _handle_thread_exception(self, args):
        if args.exc_type is SystemExit:
            return
        thread_name = getattr(args.thread, "name", "unknown")
        self._log_unhandled_exception(
            f"Unhandled thread exception ({thread_name})",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )
        original = self._original_threading_excepthook
        if original and original != self._installed_threading_excepthook:
            original(args)

    def report_callback_exception(self, exc_type, exc_value, exc_traceback):
        self._log_unhandled_exception("Unhandled Tk callback exception", exc_type, exc_value, exc_traceback)

    def _log_unhandled_exception(self, title, exc_type, exc_value, exc_traceback):
        trace_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        summary = f"{title}: {exc_value}"
        self._write_log_file_lines(self.detail_log_path, [summary, trace_text])
        self.detail_log_messages.append((summary, False))
        self._record_recent_issue(summary, trace_text)
        try:
            if hasattr(self, "log_text") and self.log_text.winfo_exists():
                self._append_visible_log_line(summary)
        except Exception:
            pass


    def _is_essential_log_message(self, message):
        text = str(message).strip()
        lower = text.lower()
        if not lower:
            return False
        detail_prefixes = (
            "first live frame received:",
            "live preview auto-contrast",
            "live saturation threshold",
            "live preview rendered successfully",
            "supported live pixel formats",
            "hyperspectral band ",
            "acquisition callback received:",
            "raw hyperspectral data received:",
            "hypercube ready:",
            "stopping hera live view",
            "restarting hera live view",
            "set hdr:",
            "set gain level:",
            "gain is read-only",
            "set exposure:",
            "exposure is read-only",
            "using default bands",
            "acquisition parameters applied.",
            "hdr re-asserted",
            "preparing hera camera parameters",
            "sending software acquisition command",
            "no roi is active.",
            "roi cleared on hera",
        )
        if lower.startswith(detail_prefixes):
            return False
        essential_tokens = (
            "failed",
            "error",
            "warning",
            "aborted",
            "stopped",
            "connected",
            "verified",
            "licensed",
            "license",
            "started",
            "complete",
            "completed",
            "ready",
            "saved",
            "exported",
            "moving",
            "reached",
            "cycle",
            "site",
            "timelapse",
            "acquisition",
            "flatfield",
            "roi",
            "nis z",
            "tango",
            "hera",
        )
        return any(token in lower for token in essential_tokens)

    def _append_visible_log_line(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def refresh_visible_log(self):
        if not hasattr(self, "log_text"):
            return
        show_details = bool(self.show_detail_log_var.get()) if hasattr(self, "show_detail_log_var") else False
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        for text, is_detail in self.detail_log_messages:
            if show_details or not is_detail:
                self.log_text.insert("end", f"{text}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def log(self, message, detail=None):
        text = str(message)
        is_detail = (not self._is_essential_log_message(text)) if detail is None else bool(detail)
        self.detail_log_messages.append((text, is_detail))
        self._write_detail_log_line(text)
        if self._is_issue_log_message(text):
            self._record_recent_issue(text)
        if not hasattr(self, "log_text"):
            return
        if self.show_detail_log_var.get() or not is_detail:
            self._append_visible_log_line(text)

    def on_close(self):
        if self.is_closing:
            return
        self._write_detail_log_line("Close requested; starting cleanup.")
        self.is_closing = True
        self.live_accept_frames = False
        self.shutdown_complete_event.clear()
        self.timelapse_stop_event.set()
        self.timelapse_pause_event.clear()
        self.acquisition_done_event.set()
        self.resume_live_after_acquisition = False
        for job_attr in (
            "stage_poll_job",
            "nis_z_poll_job",
            "live_watchdog_job",
            "_auto_apply_parameters_job",
            "ui_queue_poll_job",
        ):
            job = getattr(self, job_attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
            setattr(self, job_attr, None)
        try:
            self.withdraw()
        except tk.TclError:
            pass

        self.shutdown_watchdog_timer = threading.Timer(8.0, self._force_exit_after_shutdown_timeout)
        self.shutdown_watchdog_timer.daemon = True
        self.shutdown_watchdog_timer.start()
        self.shutdown_thread = threading.Thread(target=self._shutdown_worker, name="HeraShutdown")
        self.shutdown_thread.daemon = True
        self.shutdown_thread.start()
        try:
            self.after(50, self._poll_shutdown_complete)
        except (RuntimeError, tk.TclError):
            self._finish_close(cancel_watchdog=False)

    def _shutdown_worker(self):
        try:
            self._cleanup_hardware()
        finally:
            self.shutdown_complete_event.set()

    def _poll_shutdown_complete(self):
        if self.shutdown_complete_event.is_set():
            self._finish_close()
            return
        try:
            self.after(50, self._poll_shutdown_complete)
        except (RuntimeError, tk.TclError):
            pass

    def _finish_close(self, cancel_watchdog=True):
        self._write_detail_log_line("Shutdown cleanup completed.")
        timer = getattr(self, "shutdown_watchdog_timer", None)
        if timer and cancel_watchdog:
            try:
                timer.cancel()
            except Exception:
                pass
            self.shutdown_watchdog_timer = None
        try:
            self.quit()
        except (RuntimeError, tk.TclError):
            pass
        try:
            if self.winfo_exists():
                self.destroy()
        except (RuntimeError, tk.TclError):
            pass
        self._restore_background_exception_logging()
        self._restore_fatal_crash_logging()

    def _force_exit_after_shutdown_timeout(self):
        self._write_detail_log_line("Shutdown watchdog forced process exit after cleanup timeout.")
        os._exit(0)

    def _cleanup_hardware(self):
        try:
            helper_process = getattr(self, "helper_acquisition_process", None)
            if helper_process and helper_process.poll() is None:
                helper_process.kill()
                self._write_detail_log_line("Killed helper acquisition process during shutdown.")
        except Exception:
            pass
        try:
            helper_client = getattr(self, "hera_service_client", None)
            if helper_client:
                helper_client.shutdown(timeout_sec=5.0)
                self.hera_service_client = None
                self._write_detail_log_line("Stopped Hera helper service during shutdown.")
        except Exception as exc:
            try:
                helper_client.kill()
            except Exception:
                pass
            self._write_detail_log_line(f"Could not stop Hera helper service cleanly during shutdown: {exc}")
        try:
            if self.tango and self.tango.connected:
                with self.stage_lock:
                    try:
                        self.tango.stop_axes()
                    except Exception:
                        pass
                    try:
                        self.tango.disconnect()
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            if self.controller:
                if self.current_hypercube_handle:
                    try:
                        if (
                            self.current_hypercube_handle != self.flatfield_hypercube_handle
                            and not self._is_owned_cube_info(self.current_hypercube_info)
                        ):
                            with self.hypercube_read_lock:
                                self.controller.release_hypercube(self.current_hypercube_handle)
                    except Exception:
                        pass
                    self.current_hypercube_handle = None
                if self.flatfield_hypercube_handle:
                    try:
                        if not self._is_owned_cube_info(self.flatfield_info):
                            with self.hypercube_read_lock:
                                self.controller.release_hypercube(self.flatfield_hypercube_handle)
                    except Exception:
                        pass
                    self.flatfield_hypercube_handle = None
                self.current_hypercube_data = None
                self.flatfield_hypercube_data = None
                if self.controller.connected:
                    try:
                        if self.controller.is_acquiring():
                            self.controller.abort_hyperspectral_acquisition()
                    except Exception:
                        pass
                    try:
                        self.controller.disconnect()
                    except Exception:
                        pass
                try:
                    self.controller.release_device()
                except Exception:
                    pass
        except Exception:
            pass


def _claim_single_instance():
    mutex_name = "Global\\HeraTriggerAppNISZBridgeSingleInstance"
    handle = ctypes.windll.kernel32.CreateMutexW(None, True, mutex_name)
    if not handle:
        return None
    if ctypes.windll.kernel32.GetLastError() == 183:
        ctypes.windll.user32.MessageBoxW(
            None,
            "HERA Trigger is already running. Close the existing HERA window before opening it again.",
            "HERA Trigger",
            0x00000030,
        )
        return None

    lock_path = os.path.join(tempfile.gettempdir(), "hera_trigger_app_nis_z_bridge.lock")
    try:
        lock_file = open(lock_path, "a+b")
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        ctypes.windll.user32.MessageBoxW(
            None,
            "HERA Trigger is already running. Close the existing HERA window before opening it again.",
            "HERA Trigger",
            0x00000030,
        )
        try:
            ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass
        return None

    return handle, lock_file


def main():
    _single_instance_claim = _claim_single_instance()
    if _single_instance_claim:
        _single_instance_handle, _single_instance_lock_file = _single_instance_claim
        try:
            app = HeraTriggerApp()
            app.mainloop()
        finally:
            try:
                msvcrt.locking(_single_instance_lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                _single_instance_lock_file.close()
            except Exception:
                pass
            try:
                ctypes.windll.kernel32.CloseHandle(_single_instance_handle)
            except Exception:
                pass
