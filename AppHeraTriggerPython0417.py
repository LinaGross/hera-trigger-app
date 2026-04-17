import ctypes
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox


class HeraDeviceInfo(ctypes.Structure):
    _fields_ = [
        ("Id", ctypes.c_char * 128),
        ("ProductName", ctypes.c_char * 128),
        ("SerialNumber", ctypes.c_char * 128),
        ("Vendor", ctypes.c_char * 128),
    ]


class HeraController:
    OK_STATUS = 0
    HYPERSPECTRAL_DATA_OK = 0

    def __init__(self, dll_path=None):
        self.dll_path = dll_path or self.default_dll_path()
        self.dll = None
        self.device_handle = ctypes.c_void_p()
        self.connected = False
        self.callbacks_registered = False
        self.progress_handler_func = None
        self.data_handler_func = None
        self._progress_callback = None
        self._data_callback = None
        self._callback_refs = []
        self.load_dll()

    @staticmethod
    def default_dll_path():
        base = os.path.abspath(os.path.dirname(__file__))
        candidate = os.path.join(base, "HeraAPI.dll")
        if os.path.exists(candidate):
            return candidate
        return os.path.join(base, "HeraNetAPI.dll")

    @staticmethod
    def get_hera_devices_path():
        return os.environ.get("HERA_DEVICES", "")

    def load_dll(self):
        if not os.path.exists(self.dll_path):
            raise FileNotFoundError(f"DLL not found: {self.dll_path}")
        self.dll = ctypes.CDLL(self.dll_path)
        self._define_functions()

    def _define_function(self, name, restype=ctypes.c_int, argtypes=None):
        try:
            func = getattr(self.dll, name)
        except AttributeError as exc:
            raise AttributeError(f"DLL function not found: {name}") from exc
        func.restype = restype
        if argtypes is not None:
            func.argtypes = argtypes
        return func

    def _define_functions(self):
        self.FloatCallbackType = ctypes.CFUNCTYPE(None, ctypes.c_float)
        self.HyperspectralDataCallbackType = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p)

        self.HeraAPI_GetLastErrorMessage = self._define_function("HeraAPI_GetLastErrorMessage", ctypes.c_char_p, [])
        self.HeraAPI_GetVersion = self._define_function(
            "HeraAPI_GetVersion",
            ctypes.c_int,
            [ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)],
        )
        self.HeraAPI_IsLicensed = self._define_function(
            "HeraAPI_IsLicensed",
            ctypes.c_int,
            [ctypes.POINTER(ctypes.c_bool), ctypes.POINTER(ctypes.c_longlong), ctypes.POINTER(ctypes.c_longlong)],
        )
        self.HeraAPI_EnumerateDevices = self._define_function("HeraAPI_EnumerateDevices", ctypes.c_int, [ctypes.POINTER(ctypes.c_size_t)])
        self.HeraAPI_GetDeviceInfoByIndex = self._define_function(
            "HeraAPI_GetDeviceInfoByIndex",
            ctypes.c_int,
            [ctypes.c_size_t, ctypes.POINTER(HeraDeviceInfo)],
        )
        self.HeraAPI_CreateDevice = self._define_function(
            "HeraAPI_CreateDevice",
            ctypes.c_int,
            [ctypes.POINTER(HeraDeviceInfo), ctypes.POINTER(ctypes.c_void_p)],
        )
        self.HeraAPI_ReleaseDevice = self._define_function("HeraAPI_ReleaseDevice", ctypes.c_int, [ctypes.c_void_p])
        self.HeraAPI_Connect = self._define_function("HeraAPI_Connect", ctypes.c_int, [ctypes.c_void_p])
        self.HeraAPI_Disconnect = self._define_function("HeraAPI_Disconnect", ctypes.c_int, [ctypes.c_void_p])
        self.HeraAPI_IsConnected = self._define_function("HeraAPI_IsConnected", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)])
        self.HeraAPI_RegisterHyperspectralDataAcqCallbacks = self._define_function(
            "HeraAPI_RegisterHyperspectralDataAcqCallbacks",
            ctypes.c_int,
            [ctypes.c_void_p, self.FloatCallbackType, self.HyperspectralDataCallbackType],
        )
        self.HeraAPI_UnregisterHyperspectralDataAcqCallbacks = self._define_function(
            "HeraAPI_UnregisterHyperspectralDataAcqCallbacks",
            ctypes.c_int,
            [ctypes.c_void_p],
        )
        self.HeraAPI_StartHyperspectralDataAcquisitionEx = self._define_function(
            "HeraAPI_StartHyperspectralDataAcquisitionEx",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int],
        )
        self.HeraAPI_AbortHyperspectralDataAcquisition = self._define_function(
            "HeraAPI_AbortHyperspectralDataAcquisition",
            ctypes.c_int,
            [ctypes.c_void_p],
        )
        self.HeraAPI_IsAcquiringHyperspectralData = self._define_function(
            "HeraAPI_IsAcquiringHyperspectralData",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_GetHyperCubeEx = self._define_function(
            "HeraAPI_GetHyperCubeEx",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_uint, ctypes.c_int, ctypes.c_void_p],
        )
        self.HeraAPI_GetHyperCubeInfo = self._define_function(
            "HeraAPI_GetHyperCubeInfo",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)],
        )
        self.HeraAPI_GetHyperspectralDataInfo = self._define_function(
            "HeraAPI_GetHyperspectralDataInfo",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_void_p)],
        )
        self.HeraAPI_ExportHyperCubeAsEnvi = self._define_function(
            "HeraAPI_ExportHyperCubeAsEnvi",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p],
        )
        self.HeraAPI_ReleaseHyperspectralData = self._define_function("HeraAPI_ReleaseHyperspectralData", ctypes.c_int, [ctypes.c_void_p])
        self.HeraAPI_ReleaseHyperCube = self._define_function("HeraAPI_ReleaseHyperCube", ctypes.c_int, [ctypes.c_void_p])
        self.HeraAPI_GetGainLevelResolution = self._define_function(
            "HeraAPI_GetGainLevelResolution",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)],
        )
        self.HeraAPI_GetGainLevel = self._define_function(
            "HeraAPI_GetGainLevel",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)],
        )
        self.HeraAPI_SetGainLevel = self._define_function("HeraAPI_SetGainLevel", ctypes.c_int, [ctypes.c_void_p, ctypes.c_double])
        self.HeraAPI_IsGainLevelWritable = self._define_function(
            "HeraAPI_IsGainLevelWritable",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_SetExposure = self._define_function("HeraAPI_SetExposure", ctypes.c_int, [ctypes.c_void_p, ctypes.c_double])
        self.HeraAPI_GetExposure = self._define_function(
            "HeraAPI_GetExposure",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)],
        )
        self.HeraAPI_IsExposureWritable = self._define_function(
            "HeraAPI_IsExposureWritable",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_SetROI = self._define_function(
            "HeraAPI_SetROI",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint],
        )
        self.HeraAPI_GetOffsetX = self._define_function("HeraAPI_GetOffsetX", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)])
        self.HeraAPI_GetOffsetY = self._define_function("HeraAPI_GetOffsetY", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)])
        self.HeraAPI_GetWidth = self._define_function("HeraAPI_GetWidth", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)])
        self.HeraAPI_GetHeight = self._define_function("HeraAPI_GetHeight", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)])
        self.HeraAPI_IsROIWritable = self._define_function(
            "HeraAPI_IsROIWritable",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_IsScanModeSupported = self._define_function(
            "HeraAPI_IsScanModeSupported",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_IsTriggerModeSupported = self._define_function(
            "HeraAPI_IsTriggerModeSupported",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_GetDefaultOutBands = self._define_function(
            "HeraAPI_GetDefaultOutBands",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
        )

    def get_last_error(self):
        result = self.HeraAPI_GetLastErrorMessage()
        return result.decode("utf-8", errors="ignore") if result else "Unknown error"

    def check_status(self, status, action):
        if status != self.OK_STATUS:
            raise RuntimeError(f"{action} failed: {self.get_last_error()} (status={status})")

    def get_api_version(self):
        major = ctypes.c_int()
        minor = ctypes.c_int()
        build = ctypes.c_int()
        status = self.HeraAPI_GetVersion(ctypes.byref(major), ctypes.byref(minor), ctypes.byref(build))
        return status, (major.value, minor.value, build.value)

    def is_licensed(self):
        licensed = ctypes.c_bool(False)
        expiry_license = ctypes.c_longlong(0)
        expiry_cert = ctypes.c_longlong(0)
        status = self.HeraAPI_IsLicensed(ctypes.byref(licensed), ctypes.byref(expiry_license), ctypes.byref(expiry_cert))
        return status, licensed.value, expiry_license.value, expiry_cert.value

    def enumerate_devices(self):
        count = ctypes.c_size_t()
        self.check_status(self.HeraAPI_EnumerateDevices(ctypes.byref(count)), "Enumerate devices")
        devices = []
        for index in range(count.value):
            info = HeraDeviceInfo()
            self.check_status(self.HeraAPI_GetDeviceInfoByIndex(ctypes.c_size_t(index), ctypes.byref(info)), f"Read device info #{index}")
            devices.append(info)
        return devices

    def create_device(self, device_info):
        device_handle = ctypes.c_void_p()
        self.check_status(self.HeraAPI_CreateDevice(ctypes.byref(device_info), ctypes.byref(device_handle)), "Create device")
        self.device_handle = device_handle
        return device_handle

    def release_device(self):
        if self.device_handle:
            self.unregister_callbacks()
            self.HeraAPI_ReleaseDevice(self.device_handle)
            self.device_handle = ctypes.c_void_p()
            self.connected = False

    def connect(self):
        if not self.device_handle:
            raise RuntimeError("No Hera device was created.")
        self.check_status(self.HeraAPI_Connect(self.device_handle), "Connect device")
        self.connected = True
        self.register_callbacks()

    def disconnect(self):
        if not self.device_handle:
            return
        self.unregister_callbacks()
        self.check_status(self.HeraAPI_Disconnect(self.device_handle), "Disconnect device")
        self.connected = False

    def is_connected(self):
        if not self.device_handle:
            return False
        connected = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_IsConnected(self.device_handle, ctypes.byref(connected)), "Check connection")
        return connected.value

    def is_acquiring(self):
        if not self.device_handle:
            return False
        acquiring = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_IsAcquiringHyperspectralData(self.device_handle, ctypes.byref(acquiring)), "Check acquisition state")
        return acquiring.value

    def register_callbacks(self):
        if self.callbacks_registered:
            return

        def progress_handler(progress):
            if callable(self.progress_handler_func):
                self.progress_handler_func(progress)

        def data_handler(data_handle, data_status, message):
            if callable(self.data_handler_func):
                decoded_message = message.decode("utf-8", errors="ignore") if message else ""
                self.data_handler_func(data_handle, data_status, decoded_message)

        self._progress_callback = self.FloatCallbackType(progress_handler)
        self._data_callback = self.HyperspectralDataCallbackType(data_handler)
        self._callback_refs = [self._progress_callback, self._data_callback]
        self.check_status(
            self.HeraAPI_RegisterHyperspectralDataAcqCallbacks(self.device_handle, self._progress_callback, self._data_callback),
            "Register callbacks",
        )
        self.callbacks_registered = True

    def unregister_callbacks(self):
        if not self.device_handle or not self.callbacks_registered:
            return
        self.HeraAPI_UnregisterHyperspectralDataAcqCallbacks(self.device_handle)
        self._callback_refs = []
        self.callbacks_registered = False

    def set_gain(self, gain_level):
        self.check_status(self.HeraAPI_SetGainLevel(self.device_handle, ctypes.c_double(gain_level)), "Set gain")

    def get_gain(self):
        gain_level = ctypes.c_double()
        self.check_status(self.HeraAPI_GetGainLevel(self.device_handle, ctypes.byref(gain_level)), "Get gain")
        return gain_level.value

    def get_gain_resolution(self):
        resolution = ctypes.c_double()
        self.check_status(self.HeraAPI_GetGainLevelResolution(self.device_handle, ctypes.byref(resolution)), "Get gain resolution")
        return resolution.value

    def is_gain_writable(self):
        writable = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_IsGainLevelWritable(self.device_handle, ctypes.byref(writable)), "Check gain writability")
        return writable.value

    def set_exposure_ms(self, exposure_ms):
        exposure_us = exposure_ms * 1000.0
        self.check_status(self.HeraAPI_SetExposure(self.device_handle, ctypes.c_double(exposure_us)), "Set exposure")

    def get_exposure_ms(self):
        exposure_us = ctypes.c_double()
        self.check_status(self.HeraAPI_GetExposure(self.device_handle, ctypes.byref(exposure_us)), "Get exposure")
        return exposure_us.value / 1000.0

    def is_exposure_writable(self):
        writable = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_IsExposureWritable(self.device_handle, ctypes.byref(writable)), "Check exposure writability")
        return writable.value

    def set_roi(self, x, y, width, height):
        self.check_status(
            self.HeraAPI_SetROI(self.device_handle, ctypes.c_uint(x), ctypes.c_uint(y), ctypes.c_uint(width), ctypes.c_uint(height)),
            "Set ROI",
        )

    def get_roi(self):
        x = ctypes.c_uint()
        y = ctypes.c_uint()
        width = ctypes.c_uint()
        height = ctypes.c_uint()
        self.check_status(self.HeraAPI_GetOffsetX(self.device_handle, ctypes.byref(x)), "Get ROI X")
        self.check_status(self.HeraAPI_GetOffsetY(self.device_handle, ctypes.byref(y)), "Get ROI Y")
        self.check_status(self.HeraAPI_GetWidth(self.device_handle, ctypes.byref(width)), "Get ROI width")
        self.check_status(self.HeraAPI_GetHeight(self.device_handle, ctypes.byref(height)), "Get ROI height")
        return x.value, y.value, width.value, height.value

    def is_roi_writable(self):
        writable = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_IsROIWritable(self.device_handle, ctypes.byref(writable)), "Check ROI writability")
        return writable.value

    def is_scan_mode_supported(self, scan_mode):
        supported = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_IsScanModeSupported(self.device_handle, ctypes.c_int(scan_mode), ctypes.byref(supported)), "Check scan mode support")
        return supported.value

    def is_trigger_mode_supported(self, trigger_mode):
        supported = ctypes.c_bool(False)
        self.check_status(
            self.HeraAPI_IsTriggerModeSupported(self.device_handle, ctypes.c_int(trigger_mode), ctypes.byref(supported)),
            "Check trigger mode support",
        )
        return supported.value

    def get_default_output_bands(self, scan_mode):
        bands = ctypes.c_int()
        self.check_status(self.HeraAPI_GetDefaultOutBands(self.device_handle, ctypes.c_int(scan_mode), ctypes.byref(bands)), "Get default output bands")
        return bands.value

    def start_hyperspectral_acquisition(self, scan_mode, trigger_mode, averages, stabilization_ms):
        self.check_status(
            self.HeraAPI_StartHyperspectralDataAcquisitionEx(
                self.device_handle,
                ctypes.c_int(scan_mode),
                ctypes.c_int(trigger_mode),
                ctypes.c_int(averages),
                ctypes.c_int(stabilization_ms),
            ),
            "Start hyperspectral acquisition",
        )

    def abort_hyperspectral_acquisition(self):
        self.check_status(self.HeraAPI_AbortHyperspectralDataAcquisition(self.device_handle), "Abort hyperspectral acquisition")

    def get_hyperspectral_data_info(self, data_handle):
        width = ctypes.c_int()
        height = ctypes.c_int()
        data_ptr = ctypes.c_void_p()
        self.check_status(
            self.HeraAPI_GetHyperspectralDataInfo(data_handle, ctypes.byref(width), ctypes.byref(height), ctypes.byref(data_ptr)),
            "Get hyperspectral data info",
        )
        return width.value, height.value, data_ptr.value

    def get_hypercube(self, data_handle, data_type, bands_count, binning):
        hypercube_handle = ctypes.c_void_p()
        self.check_status(
            self.HeraAPI_GetHyperCubeEx(
                self.device_handle,
                data_handle,
                ctypes.byref(hypercube_handle),
                ctypes.c_int(data_type),
                ctypes.c_uint(bands_count),
                ctypes.c_int(binning),
                None,
            ),
            "Compute hypercube",
        )
        return hypercube_handle

    def get_hypercube_info(self, hypercube_handle):
        width = ctypes.c_int()
        height = ctypes.c_int()
        bands = ctypes.c_int()
        data_type = ctypes.c_int()
        self.check_status(
            self.HeraAPI_GetHyperCubeInfo(
                hypercube_handle,
                ctypes.byref(width),
                ctypes.byref(height),
                ctypes.byref(bands),
                ctypes.byref(data_type),
            ),
            "Get hypercube info",
        )
        return width.value, height.value, bands.value, data_type.value

    def export_hypercube_envi(self, hypercube_handle, output_path, description=None):
        desc_bytes = description.encode("utf-8") if description else None
        self.check_status(
            self.HeraAPI_ExportHyperCubeAsEnvi(hypercube_handle, output_path.encode("utf-8"), desc_bytes),
            "Export hypercube",
        )

    def release_hyperspectral_data(self, data_handle):
        self.HeraAPI_ReleaseHyperspectralData(data_handle)

    def release_hypercube(self, hypercube_handle):
        self.HeraAPI_ReleaseHyperCube(hypercube_handle)


class HeraTriggerApp(tk.Tk):
    STATE_LABELS = {
        "Idle": "Idle",
        "Connecting": "Connecting...",
        "Ready": "Ready",
        "WaitingForTrigger": "Waiting for trigger",
        "Acquiring": "Acquiring",
        "ComputingHypercube": "Computing hypercube",
        "Saving": "Saving",
        "Completed": "Completed",
        "Error": "Error",
    }

    SCAN_MODES = {
        "Short": 0,
        "Medium": 1,
        "Long": 2,
        "ExtraLong": 3,
    }

    TRIGGER_MODES = {
        "Internal": 0,
        "DeferredStartExtLineHi": 1,
        "StepScanExtLoHi": 2,
    }

    BINNING_OPTIONS = {
        "None": 0,
        "2x": 1,
        "4x": 2,
        "8x": 3,
        "2x Enhanced": 0x1000,
        "4x Enhanced": 0x1001,
    }

    DATA_TYPES = {"SinglePrecision": 0, "DoublePrecision": 1}

    def __init__(self):
        super().__init__()
        self.title("Hera Trigger Control")
        self.geometry("860x900")
        self.resizable(False, False)

        self.controller = None
        self.devices = []
        self.app_state = self.STATE_LABELS["Idle"]
        self.processing_lock = threading.Lock()
        self._build_ui()
        self.update_state("Idle")
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        self.dll_frame = tk.LabelFrame(self, text="Hera SDK and Diagnostics", padx=8, pady=8)
        self.dll_frame.pack(fill="x", padx=10, pady=6)

        tk.Label(self.dll_frame, text="SDK DLL Path:").grid(row=0, column=0, sticky="w")
        self.dll_path_var = tk.StringVar(value=HeraController.default_dll_path())
        tk.Entry(self.dll_frame, textvariable=self.dll_path_var, width=70).grid(row=0, column=1, padx=4, sticky="w")
        tk.Button(self.dll_frame, text="Browse...", command=self.browse_dll).grid(row=0, column=2, padx=4)

        tk.Label(self.dll_frame, text="HERA_DEVICES:").grid(row=1, column=0, sticky="w")
        self.env_var = tk.StringVar(value=HeraController.get_hera_devices_path() or "(HERA_DEVICES not set)")
        tk.Label(self.dll_frame, textvariable=self.env_var, wraplength=520, justify="left").grid(row=1, column=1, columnspan=3, sticky="w")

        tk.Label(self.dll_frame, text="License:").grid(row=2, column=0, sticky="w")
        self.license_var = tk.StringVar(value="Unknown")
        tk.Label(self.dll_frame, textvariable=self.license_var).grid(row=2, column=1, sticky="w")

        tk.Label(self.dll_frame, text="Device:").grid(row=3, column=0, sticky="w")
        self.selected_device_var = tk.StringVar(value="(none)")
        self.device_optionmenu = tk.OptionMenu(self.dll_frame, self.selected_device_var, "(none)")
        self.device_optionmenu.config(width=52)
        self.device_optionmenu.grid(row=3, column=1, sticky="w", padx=4)
        tk.Button(self.dll_frame, text="Refresh devices", command=self.refresh_device_list).grid(row=3, column=2, padx=4)

        tk.Button(self.dll_frame, text="Connect", command=self.connect).grid(row=4, column=0, pady=8)
        tk.Button(self.dll_frame, text="Disconnect", command=self.disconnect).grid(row=4, column=1, pady=8, sticky="w")
        tk.Button(self.dll_frame, text="SDK Version", command=self.show_sdk_version).grid(row=4, column=2, pady=8)
        tk.Button(self.dll_frame, text="Preflight Check", command=self.preflight_check).grid(row=4, column=3, pady=8)

        self.status_frame = tk.LabelFrame(self, text="Acquisition Parameters", padx=8, pady=8)
        self.status_frame.pack(fill="both", expand=False, padx=10, pady=6)

        param_labels = [
            ("Gain level (0-1):", "gain", 0.5),
            ("Exposure (ms):", "exposure", 10.0),
            ("ROI X:", "roi_x", 0),
            ("ROI Y:", "roi_y", 0),
            ("ROI Width:", "roi_w", 512),
            ("ROI Height:", "roi_h", 512),
            ("Scan mode:", "scan_mode", "Medium"),
            ("Trigger mode:", "trigger_mode", "Internal"),
            ("Averages:", "averages", 1),
            ("Stabilization ms:", "stabilization", 0),
            ("Bands (0=default):", "bands", 0),
            ("Binning:", "binning", "None"),
            ("Output path:", "output_path", os.path.join(os.path.abspath(os.path.dirname(__file__)), "output")),
            ("Data type:", "data_type", "SinglePrecision"),
        ]

        self.param_vars = {}
        row = 0
        for label_text, key, default in param_labels:
            tk.Label(self.status_frame, text=label_text).grid(row=row, column=0, sticky="w", pady=3)
            if key == "scan_mode":
                self.param_vars[key] = tk.StringVar(value=default)
                menu = tk.OptionMenu(self.status_frame, self.param_vars[key], *list(self.SCAN_MODES.keys()))
                menu.config(width=18)
                menu.grid(row=row, column=1, sticky="w")
            elif key == "trigger_mode":
                self.param_vars[key] = tk.StringVar(value=default)
                menu = tk.OptionMenu(self.status_frame, self.param_vars[key], *list(self.TRIGGER_MODES.keys()))
                menu.config(width=18)
                menu.grid(row=row, column=1, sticky="w")
            elif key == "binning":
                self.param_vars[key] = tk.StringVar(value=default)
                menu = tk.OptionMenu(self.status_frame, self.param_vars[key], *list(self.BINNING_OPTIONS.keys()))
                menu.config(width=18)
                menu.grid(row=row, column=1, sticky="w")
            elif key == "data_type":
                self.param_vars[key] = tk.StringVar(value=default)
                menu = tk.OptionMenu(self.status_frame, self.param_vars[key], *list(self.DATA_TYPES.keys()))
                menu.config(width=18)
                menu.grid(row=row, column=1, sticky="w")
            elif key == "output_path":
                self.param_vars[key] = tk.StringVar(value=default)
                tk.Entry(self.status_frame, textvariable=self.param_vars[key], width=40).grid(row=row, column=1, sticky="w")
                tk.Button(self.status_frame, text="Browse", command=self.browse_output_path).grid(row=row, column=2, padx=4)
            elif isinstance(default, int):
                self.param_vars[key] = tk.IntVar(value=default)
                tk.Entry(self.status_frame, textvariable=self.param_vars[key], width=20).grid(row=row, column=1, sticky="w")
            else:
                self.param_vars[key] = tk.DoubleVar(value=default)
                tk.Entry(self.status_frame, textvariable=self.param_vars[key], width=20).grid(row=row, column=1, sticky="w")
            row += 1

        action_frame = tk.Frame(self.status_frame)
        action_frame.grid(row=row, column=0, columnspan=3, pady=10)
        tk.Button(action_frame, text="Apply Parameters", command=self.apply_parameters).pack(side="left", padx=6)
        tk.Button(action_frame, text="Start Acquisition", command=self.start_acquisition).pack(side="left", padx=6)
        tk.Button(action_frame, text="Abort Acquisition", command=self.abort_acquisition).pack(side="left", padx=6)

        self.state_frame = tk.LabelFrame(self, text="Application State", padx=8, pady=8)
        self.state_frame.pack(fill="x", padx=10, pady=6)
        self.app_state_var = tk.StringVar(value=self.app_state)
        tk.Label(self.state_frame, text="Current state:").grid(row=0, column=0, sticky="w")
        self.app_state_label = tk.Label(self.state_frame, textvariable=self.app_state_var, fg="green")
        self.app_state_label.grid(row=0, column=1, sticky="w")

        self.log_frame = tk.LabelFrame(self, text="Status / Messages", padx=8, pady=8)
        self.log_frame.pack(fill="both", expand=True, padx=10, pady=6)
        self.log_text = tk.Text(self.log_frame, height=14, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def browse_dll(self):
        file_path = filedialog.askopenfilename(title="Select HeraAPI.dll", filetypes=[("DLL files", "*.dll"), ("All files", "*.*")])
        if file_path:
            self.dll_path_var.set(file_path)

    def browse_output_path(self):
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self.param_vars["output_path"].set(folder)

    def refresh_device_list(self):
        try:
            controller = HeraController(dll_path=self.dll_path_var.get())
            devices = controller.enumerate_devices()
            self.devices = devices
            menu = self.device_optionmenu["menu"]
            menu.delete(0, "end")
            for index, device in enumerate(devices):
                title = f"{index}: {self._device_title(device)}"
                menu.add_command(label=title, command=lambda value=title: self.selected_device_var.set(value))
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

    def connect(self):
        self.update_state("Connecting")
        self.log("Connecting to Hera device...")
        try:
            self.controller = HeraController(dll_path=self.dll_path_var.get())
            devices = self.controller.enumerate_devices()
        except Exception as exc:
            self.log(f"Failed during SDK startup: {exc}")
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
            self.controller.connect()
            self.update_state("Ready")
            self.log(f"Connected to Hera device {index}: {self._device_title(self.devices[index])}")
            self.check_license_status()
        except Exception as exc:
            self.log(f"Failed to connect to Hera device: {exc}")
            self.update_state("Error")

    def _selected_device_index(self):
        title = self.selected_device_var.get()
        if title != "(none)":
            try:
                return int(title.split(":", 1)[0])
            except ValueError:
                pass
        return 0

    def disconnect(self):
        if not self.controller or not self.controller.device_handle:
            self.log("No Hera device is connected.")
            return
        try:
            if self.controller.connected:
                self.controller.disconnect()
            self.controller.release_device()
            self.license_var.set("Unknown")
            self.update_state("Idle")
            self.log("Disconnected from Hera device.")
        except Exception as exc:
            self.log(f"Failed to disconnect Hera device: {exc}")
            self.update_state("Error")

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

    def check_license_status(self):
        if not self.controller:
            self.license_var.set("Unknown")
            return False
        try:
            status, licensed, expiry_license, expiry_cert = self.controller.is_licensed()
        except Exception as exc:
            self.license_var.set("License check failed")
            self.log(f"License check failed: {exc}")
            return False

        if status != 0:
            self.license_var.set("License check failed")
            self.log(self.controller.get_last_error())
            return False

        if licensed:
            self.license_var.set("Licensed")
            self.log(f"Hera SDK is licensed. License expiry UTC={expiry_license}, certificate expiry UTC={expiry_cert}")
            return True

        self.license_var.set("Not licensed")
        self.log("Hera SDK license is not active.")
        return False

    def preflight_check(self):
        self.log("Running preflight checks...")
        errors = []
        if not os.path.exists(self.dll_path_var.get()):
            errors.append("Hera SDK DLL path does not exist.")
        hera_devices = HeraController.get_hera_devices_path()
        self.env_var.set(hera_devices or "(HERA_DEVICES not set)")
        if not hera_devices:
            errors.append("HERA_DEVICES environment variable is not set.")
        elif not os.path.exists(hera_devices):
            errors.append("HERA_DEVICES path does not exist.")

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

    def apply_parameters(self):
        if not self.controller or not self.controller.connected:
            self.log("Connect to Hera before applying parameters.")
            return
        try:
            gain = float(self.param_vars["gain"].get())
            exposure_ms = float(self.param_vars["exposure"].get())
            roi_x = int(self.param_vars["roi_x"].get())
            roi_y = int(self.param_vars["roi_y"].get())
            roi_w = int(self.param_vars["roi_w"].get())
            roi_h = int(self.param_vars["roi_h"].get())
            scan_mode_name = self.param_vars["scan_mode"].get()
            trigger_mode_name = self.param_vars["trigger_mode"].get()
            scan_mode = self.SCAN_MODES[scan_mode_name]
            trigger_mode = self.TRIGGER_MODES[trigger_mode_name]
            bands = int(self.param_vars["bands"].get())

            if not self.controller.is_scan_mode_supported(scan_mode):
                raise RuntimeError(f"Scan mode '{scan_mode_name}' is not supported by the connected device.")
            if not self.controller.is_trigger_mode_supported(trigger_mode):
                raise RuntimeError(f"Trigger mode '{trigger_mode_name}' is not supported by the connected device.")

            if self.controller.is_gain_writable():
                try:
                    resolution = self.controller.get_gain_resolution()
                    if resolution > 0:
                        steps = round(gain / resolution)
                        gain = min(1.0, max(0.0, steps * resolution))
                    self.controller.set_gain(gain)
                    actual_gain = self.controller.get_gain()
                    self.log(f"Set gain level: requested={gain:.6f}, actual={actual_gain:.6f}")
                except Exception as exc:
                    current_gain = self.controller.get_gain()
                    self.log(f"Gain was not changed: {exc}. Current gain level remains {current_gain:.6f}")
            else:
                current_gain = self.controller.get_gain()
                self.log(f"Gain is read-only on this device. Current gain level: {current_gain:.6f}")

            if self.controller.is_exposure_writable():
                self.controller.set_exposure_ms(exposure_ms)
                actual_exposure = self.controller.get_exposure_ms()
                self.log(f"Set exposure: requested={exposure_ms} ms, actual={actual_exposure:.3f} ms")
            else:
                actual_exposure = self.controller.get_exposure_ms()
                self.log(f"Exposure is read-only on this device. Current exposure: {actual_exposure:.3f} ms")

            if self.controller.is_roi_writable():
                self.controller.set_roi(roi_x, roi_y, roi_w, roi_h)
                actual_roi = self.controller.get_roi()
                self.log(f"Set ROI: requested=({roi_x}, {roi_y}, {roi_w}, {roi_h}), actual={actual_roi}")
            else:
                actual_roi = self.controller.get_roi()
                self.log(f"ROI is read-only on this device. Current ROI: {actual_roi}")

            if bands == 0:
                bands = self.controller.get_default_output_bands(scan_mode)
                self.param_vars["bands"].set(bands)
                self.log(f"Using default bands for scan mode {scan_mode_name}: {bands}")

            self.update_state("Ready")
            self.log("Acquisition parameters applied.")
        except Exception as exc:
            self.log(f"Failed to apply parameters: {exc}")
            self.update_state("Error")

    def start_acquisition(self):
        if not self.controller or not self.controller.connected:
            self.log("Connect to Hera before starting acquisition.")
            return
        if not self.check_license_status():
            self.update_state("Error")
            return
        try:
            if self.controller.is_acquiring():
                self.log("The device is already acquiring.")
                return

            self.apply_parameters()
            if self.app_state == self.STATE_LABELS["Error"]:
                return

            scan_mode = self.SCAN_MODES[self.param_vars["scan_mode"].get()]
            trigger_mode_name = self.param_vars["trigger_mode"].get()
            trigger_mode = self.TRIGGER_MODES[trigger_mode_name]
            averages = int(self.param_vars["averages"].get())
            stabilization = int(self.param_vars["stabilization"].get())

            if trigger_mode_name == "Internal":
                self.log("Sending software acquisition command through Hera SDK.")
            else:
                self.log(f"Arming Hera SDK acquisition with trigger mode '{trigger_mode_name}'.")

            self.controller.start_hyperspectral_acquisition(scan_mode, trigger_mode, averages, stabilization)
            self.update_state("Acquiring" if trigger_mode_name == "Internal" else "WaitingForTrigger")
            self.log("Hyperspectral acquisition started.")
        except Exception as exc:
            self.log(f"Failed to start acquisition: {exc}")
            self.update_state("Error")

    def abort_acquisition(self):
        if not self.controller or not self.controller.connected:
            self.log("Connect to Hera before aborting acquisition.")
            return
        try:
            self.controller.abort_hyperspectral_acquisition()
            self.log("Abort request sent to Hera SDK.")
            self.update_state("Ready")
        except Exception as exc:
            self.log(f"Failed to abort acquisition: {exc}")
            self.update_state("Error")

    def on_progress_update(self, progress):
        def update():
            if self.app_state == self.STATE_LABELS["WaitingForTrigger"] and progress > 0:
                self.update_state("Acquiring")
            self.log(f"Acquisition progress: {progress * 100:.1f}%")

        self.after(0, update)

    def on_hyperspectral_data_acquired(self, data_handle, data_status, message):
        self.after(0, lambda: self._start_data_processing(data_handle, data_status, message))

    def _start_data_processing(self, data_handle, data_status, message):
        self.log(f'Acquisition callback received: status={data_status}, message="{message}"')
        if data_status != HeraController.HYPERSPECTRAL_DATA_OK:
            self.log("Hyperspectral acquisition failed or was aborted.")
            self.update_state("Error")
            return

        if not self.processing_lock.acquire(blocking=False):
            self.log("Processing is already running for a previous acquisition.")
            return

        self.update_state("ComputingHypercube")
        worker = threading.Thread(target=self._process_acquisition_worker, args=(data_handle,), daemon=True)
        worker.start()

    def _process_acquisition_worker(self, data_handle):
        hypercube_handle = None
        try:
            width, height, _ = self.controller.get_hyperspectral_data_info(data_handle)
            self._log_async(f"Raw hyperspectral data received: width={width}, height={height}")

            bands = int(self.param_vars["bands"].get())
            binning = self.BINNING_OPTIONS[self.param_vars["binning"].get()]
            data_type = self.DATA_TYPES[self.param_vars["data_type"].get()]

            hypercube_handle = self.controller.get_hypercube(data_handle, data_type, bands, binning)
            cube_width, cube_height, cube_bands, cube_type = self.controller.get_hypercube_info(hypercube_handle)
            self._log_async(
                f"Hypercube ready: width={cube_width}, height={cube_height}, bands={cube_bands}, dataType={cube_type}"
            )

            self.after(0, lambda: self.update_state("Saving"))
            output_dir = self.param_vars["output_path"].get()
            os.makedirs(output_dir, exist_ok=True)
            base_name = time.strftime("hera_hypercube_%Y%m%d_%H%M%S")
            output_path = os.path.join(output_dir, base_name)
            description = "Generated by AppHeraTriggerPython0417 using Hera SDK"
            self.controller.export_hypercube_envi(hypercube_handle, output_path, description)
            self._log_async(f"Exported hypercube to ENVI base path: {output_path}")
            self.after(0, lambda: self.update_state("Completed"))
        except Exception as exc:
            self._log_async(f"Failed to process hyperspectral data: {exc}")
            self.after(0, lambda: self.update_state("Error"))
        finally:
            if hypercube_handle:
                self.controller.release_hypercube(hypercube_handle)
            if data_handle:
                self.controller.release_hyperspectral_data(data_handle)
            self.processing_lock.release()

    def _log_async(self, message):
        self.after(0, lambda: self.log(message))

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
        try:
            if self.controller:
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
                self.controller.release_device()
        finally:
            self.destroy()


if __name__ == "__main__":
    app = HeraTriggerApp()
    app.mainloop()
