import ctypes
import math
import msvcrt
import os
import tempfile
import threading
import tkinter as tk

from hera_app.controllers import HeraController, NISZBridgeController, SavedPosition
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
    LIVE_PIXEL_FORMATS = {
        0: "Mono8",
        1: "Mono10",
        2: "Mono12",
        3: "Mono14",
        4: "Mono16",
    }

    def __init__(self):
        super().__init__()
        self.title("Hera + Tango Trigger Control")
        self.geometry("1480x980")
        self.minsize(1360, 900)
        self.theme_mode = "dark"
        self.theme_button_var = tk.StringVar(value="Light Mode")

        self.controller = None
        self.tango = None
        self.devices = []
        self.positions = [SavedPosition("Start", 0.0, 0.0, math.nan)]
        self.selected_position_index = None
        self.processing_lock = threading.Lock()
        self.stage_lock = threading.Lock()
        self.live_frame_lock = threading.Lock()
        self.parameter_apply_lock = threading.Lock()
        self.app_state = self.STATE_LABELS["Idle"]
        self.stage_poll_job = None
        self.timelapse_thread = None
        self.timelapse_stop_event = threading.Event()
        self.timelapse_pause_event = threading.Event()
        self.timelapse_roi = None
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
        self.tango_dll_var = tk.StringVar(value=os.path.join(os.path.abspath(os.path.dirname(__file__)), "Tango_DLL.dll"))
        self.stage_port_var = tk.StringVar(value="COM7")
        self.stage_baud_var = tk.IntVar(value=57600)
        self.stage_interface_var = tk.StringVar(value="RS232 / COM")
        self.timelapse_status_var = tk.StringVar(value="Timelapse: idle")
        self.time_remaining_var = tk.StringVar(value="Time remaining: -")
        self.center_stage_summary_var = tk.StringVar(value="Selected position: none")
        self.current_cycle_var = tk.StringVar(value="Cycle: -")
        self.current_site_var = tk.StringVar(value="Site: -")
        self.last_export_var = tk.StringVar(value="Last export: -")
        self.hyperlab_shortcut_var = tk.StringVar(value=r"C:\Users\Public\Desktop\Nireos HyperLAB.lnk")
        self.hypercube_summary_var = tk.StringVar(value="Cube: waiting for acquisition")
        self.live_view_status_var = tk.StringVar(value="Live view: waiting for frames")
        self.hdr_enabled_var = tk.BooleanVar(value=False)
        self.hdr_status_var = tk.StringVar(value="HDR: unknown")
        self.live_cursor_var = tk.StringVar(value=self._live_cursor_status_text("-"))
        self.pending_export_tag = None
        self.live_photo = None
        self.live_frame_info = None
        self.latest_live_frame = None
        self.latest_live_profile = None
        self.live_autocontrast_var = tk.BooleanVar(value=True)
        self.live_show_saturation_var = tk.BooleanVar(value=False)
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
        self.live_cursor_image_xy = None
        self.live_roi_selecting = False
        self.live_roi_points = []
        self.live_roi_rect = None
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
        self.live_render_pending = False
        self.last_live_render_time = 0.0
        self.live_render_interval_sec = 0.10
        self.resume_live_after_acquisition = False
        self.last_applied_roi = None
        self.acquisition_requested_roi = None
        self.acquisition_requested_hdr = False
        self.live_max_preview_width = 480
        self.live_display_rotation_degrees = 90
        self.live_auth_warning_logged = False
        self.last_live_decode_error = ""
        self.is_closing = False
        self.hyper_photo = None
        self.current_hypercube_handle = None
        self.current_hypercube_info = None
        self.current_hyper_band_cache = {}
        self.current_hyper_spectrum_cache = {}
        self.hyper_selected_pixel = None
        self.hyper_display_rect = None
        self.flatfield_hypercube_handle = None
        self.flatfield_info = None
        self.flatfield_status_var = tk.StringVar(value="Flatfield: none")
        self.use_flatfield_var = tk.BooleanVar(value=True)
        self.flatfield_at_timelapse_start_var = tk.BooleanVar(value=False)
        self.pending_acquisition_role = "sample"
        self.pending_save_context = None
        self.pending_acquisition_auto_save = True
        self.save_pending_button = None
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
        self.start_stage_polling()
        self._safe_after(250, self.auto_connect_devices)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _live_cursor_status_text(self, text):
        return f"{text:<48}"[:48]

    def update_state(self, state_key):
        label = self.STATE_LABELS.get(state_key, state_key)
        self.app_state = label
        self.app_state_var.set(label)
        self.app_state_label.config(fg="red" if state_key == "Error" else "green")

    def log(self, message):
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"{message}\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def on_close(self):
        if self.is_closing:
            return
        self.is_closing = True
        hard_exit_timer = threading.Timer(3.0, lambda: os._exit(0))
        hard_exit_timer.daemon = True
        hard_exit_timer.start()
        self.timelapse_stop_event.set()
        self.timelapse_pause_event.clear()
        self.acquisition_done_event.set()
        for job_attr in ("stage_poll_job", "nis_z_poll_job", "live_watchdog_job"):
            job = getattr(self, job_attr, None)
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
            setattr(self, job_attr, None)
        self._cleanup_hardware()
        self.quit()
        self.destroy()
        os._exit(0)

    def _cleanup_hardware(self):
        try:
            if self.tango and self.tango.connected:
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
                        if self.current_hypercube_handle != self.flatfield_hypercube_handle:
                            self.controller.release_hypercube(self.current_hypercube_handle)
                    except Exception:
                        pass
                    self.current_hypercube_handle = None
                if self.flatfield_hypercube_handle:
                    try:
                        self.controller.release_hypercube(self.flatfield_hypercube_handle)
                    except Exception:
                        pass
                    self.flatfield_hypercube_handle = None
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
