import ctypes
import math
import os
import time
from dataclasses import dataclass


@dataclass
class SavedPosition:
    name: str
    x: float
    y: float
    z: float = math.nan
    roi: tuple = None


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
        self.live_callbacks_registered = False
        self.progress_handler_func = None
        self.data_handler_func = None
        self.live_error_handler_func = None
        self.live_timeout_handler_func = None
        self.live_capture_handler_func = None
        self._progress_callback = None
        self._data_callback = None
        self._live_error_callback = None
        self._live_timeout_callback = None
        self._live_capture_callback = None
        self._callback_refs = []
        self.load_dll()

    @staticmethod
    def default_dll_path():
        base = os.path.abspath(os.path.dirname(__file__))
        # Also search the project root (two levels up from hera_app/controllers/)
        project_root = os.path.abspath(os.path.join(base, "..", ".."))
        for search_dir in (base, project_root):
            candidate = os.path.join(search_dir, "HeraAPI.dll")
            if os.path.exists(candidate):
                return candidate
        candidate = os.path.join(base, "HeraNetAPI.dll")
        if os.path.exists(candidate):
            return candidate
        return os.path.join(project_root, "HeraNetAPI.dll")

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

    def _define_optional_function(self, name, restype=ctypes.c_int, argtypes=None):
        try:
            return self._define_function(name, restype, argtypes)
        except AttributeError:
            return None

    def _define_functions(self):
        self.FloatCallbackType = ctypes.CFUNCTYPE(None, ctypes.c_float)
        self.HyperspectralDataCallbackType = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p)
        self.StrCallbackType = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
        self.IntCallbackType = ctypes.CFUNCTYPE(None, ctypes.c_int)
        self.LiveCaptureCallbackType = ctypes.CFUNCTYPE(None, ctypes.c_void_p)

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
        self.HeraAPI_StartHyperspectralDataAcquisition = self._define_function(
            "HeraAPI_StartHyperspectralDataAcquisition",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int],
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
        self.HeraAPI_GetHyperCubeBandData = self._define_function(
            "HeraAPI_GetHyperCubeBandData",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_void_p)],
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
        try:
            self.HeraAPI_ClearROI = self._define_function("HeraAPI_ClearROI", ctypes.c_int, [ctypes.c_void_p])
        except AttributeError:
            self.HeraAPI_ClearROI = None
        self.HeraAPI_GetOffsetX = self._define_function("HeraAPI_GetOffsetX", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)])
        self.HeraAPI_GetOffsetY = self._define_function("HeraAPI_GetOffsetY", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)])
        self.HeraAPI_GetWidth = self._define_function("HeraAPI_GetWidth", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)])
        self.HeraAPI_GetHeight = self._define_function("HeraAPI_GetHeight", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)])
        self.HeraAPI_IsROIWritable = self._define_function(
            "HeraAPI_IsROIWritable",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_IsHDRSupported = self._define_optional_function(
            "HeraAPI_IsHDRSupported",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_SetHDR = self._define_optional_function(
            "HeraAPI_SetHDR",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_bool],
        )
        self.HeraAPI_GetHDR = self._define_optional_function(
            "HeraAPI_GetHDR",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_GetHyperCubeIsHDR = self._define_optional_function(
            "HeraAPI_GetHyperCubeIsHDR",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_GetHyperspectralDataIsHDR = self._define_optional_function(
            "HeraAPI_GetHyperspectralDataIsHDR",
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
        self.HeraAPI_RegisterLiveCaptureCallbacks = self._define_function(
            "HeraAPI_RegisterLiveCaptureCallbacks",
            ctypes.c_int,
            [ctypes.c_void_p, self.StrCallbackType, self.IntCallbackType, self.LiveCaptureCallbackType],
        )
        self.HeraAPI_UnregisterLiveCaptureCallbacks = self._define_function(
            "HeraAPI_UnregisterLiveCaptureCallbacks",
            ctypes.c_int,
            [ctypes.c_void_p],
        )
        self.HeraAPI_StartLiveCapture = self._define_function(
            "HeraAPI_StartLiveCapture",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_int],
        )
        self.HeraAPI_StopLiveCapture = self._define_function(
            "HeraAPI_StopLiveCapture",
            ctypes.c_int,
            [ctypes.c_void_p],
        )
        self.HeraAPI_IsLiveCapturing = self._define_function(
            "HeraAPI_IsLiveCapturing",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_GetLiveCaptureInfo = self._define_function(
            "HeraAPI_GetLiveCaptureInfo",
            ctypes.c_int,
            [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_void_p),
            ],
        )
        self.HeraAPI_ReleaseLiveCaptureResult = self._define_function(
            "HeraAPI_ReleaseLiveCaptureResult",
            ctypes.c_int,
            [ctypes.c_void_p],
        )
        self.HeraAPI_IsPixelFormatSupported = self._define_function(
            "HeraAPI_IsPixelFormatSupported",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_IsPixelFormatSupportedEx = self._define_optional_function(
            "HeraAPI_IsPixelFormatSupportedEx",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.c_int, ctypes.c_bool, ctypes.POINTER(ctypes.c_bool)],
        )
        self.HeraAPI_GetLiveCaptureIsHDR = self._define_optional_function(
            "HeraAPI_GetLiveCaptureIsHDR",
            ctypes.c_int,
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_bool)],
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
            self.stop_live_capture(silent=True)
            self.unregister_live_callbacks()
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
        self.stop_live_capture(silent=True)
        self.unregister_live_callbacks()
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

    def register_live_callbacks(self):
        if self.live_callbacks_registered:
            return

        def error_handler(message):
            if callable(self.live_error_handler_func):
                decoded = message.decode("utf-8", errors="ignore") if message else ""
                self.live_error_handler_func(decoded)

        def timeout_handler(free_buffers):
            if callable(self.live_timeout_handler_func):
                self.live_timeout_handler_func(free_buffers)

        def capture_handler(capture_handle):
            if callable(self.live_capture_handler_func):
                self.live_capture_handler_func(capture_handle)

        self._live_error_callback = self.StrCallbackType(error_handler)
        self._live_timeout_callback = self.IntCallbackType(timeout_handler)
        self._live_capture_callback = self.LiveCaptureCallbackType(capture_handler)
        self._callback_refs.extend([self._live_error_callback, self._live_timeout_callback, self._live_capture_callback])
        self.check_status(
            self.HeraAPI_RegisterLiveCaptureCallbacks(
                self.device_handle,
                self._live_error_callback,
                self._live_timeout_callback,
                self._live_capture_callback,
            ),
            "Register live capture callbacks",
        )
        self.live_callbacks_registered = True

    def unregister_callbacks(self):
        if not self.device_handle or not self.callbacks_registered:
            return
        self.HeraAPI_UnregisterHyperspectralDataAcqCallbacks(self.device_handle)
        self.callbacks_registered = False

    def unregister_live_callbacks(self):
        if not self.device_handle or not self.live_callbacks_registered:
            return
        self.HeraAPI_UnregisterLiveCaptureCallbacks(self.device_handle)
        self.live_callbacks_registered = False

    def is_live_capturing(self):
        if not self.device_handle:
            return False
        capturing = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_IsLiveCapturing(self.device_handle, ctypes.byref(capturing)), "Check live capture state")
        return capturing.value

    def is_pixel_format_supported(self, pixel_format, hdr=False):
        supported = ctypes.c_bool(False)
        if self.HeraAPI_IsPixelFormatSupportedEx:
            self.check_status(
                self.HeraAPI_IsPixelFormatSupportedEx(
                    self.device_handle,
                    ctypes.c_int(pixel_format),
                    ctypes.c_bool(bool(hdr)),
                    ctypes.byref(supported),
                ),
                "Check pixel format support",
            )
            return supported.value
        if hdr:
            return False
        self.check_status(self.HeraAPI_IsPixelFormatSupported(self.device_handle, ctypes.c_int(pixel_format), ctypes.byref(supported)), "Check pixel format support")
        return supported.value

    def is_hdr_supported(self):
        if not self.HeraAPI_IsHDRSupported or not self.HeraAPI_SetHDR or not self.HeraAPI_GetHDR:
            return False
        supported = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_IsHDRSupported(self.device_handle, ctypes.byref(supported)), "Check HDR support")
        return supported.value

    def set_hdr(self, enabled):
        if not self.HeraAPI_SetHDR:
            raise RuntimeError("HDR mode is not available in this Hera SDK DLL.")
        self.check_status(self.HeraAPI_SetHDR(self.device_handle, ctypes.c_bool(bool(enabled))), "Set HDR")

    def get_hdr(self):
        if not self.HeraAPI_GetHDR:
            raise RuntimeError("HDR mode readback is not available in this Hera SDK DLL.")
        hdr = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_GetHDR(self.device_handle, ctypes.byref(hdr)), "Get HDR")
        return hdr.value

    def get_live_capture_is_hdr(self, capture_handle):
        if not self.HeraAPI_GetLiveCaptureIsHDR:
            return None
        hdr = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_GetLiveCaptureIsHDR(capture_handle, ctypes.byref(hdr)), "Get live HDR flag")
        return hdr.value

    def start_live_capture(self, pixel_format=0):
        self.register_live_callbacks()
        self.check_status(self.HeraAPI_StartLiveCapture(self.device_handle, ctypes.c_int(pixel_format)), "Start live capture")

    def stop_live_capture(self, silent=False):
        if not self.device_handle:
            return
        try:
            if self.is_live_capturing():
                self.HeraAPI_StopLiveCapture(self.device_handle)
        except Exception:
            if not silent:
                raise

    def wait_for_live_capture_stopped(self, timeout_sec=5.0):
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if not self.is_live_capturing():
                return True
            time.sleep(0.05)
        raise RuntimeError("Timed out waiting for live capture to stop.")

    def get_live_capture_info(self, capture_handle):
        width = ctypes.c_int()
        height = ctypes.c_int()
        bit_depth = ctypes.c_int()
        bits_per_pixel = ctypes.c_int()
        saturation_threshold = ctypes.c_int()
        row_stride = ctypes.c_int()
        data_ptr = ctypes.c_void_p()
        self.check_status(
            self.HeraAPI_GetLiveCaptureInfo(
                capture_handle,
                ctypes.byref(width),
                ctypes.byref(height),
                ctypes.byref(bit_depth),
                ctypes.byref(bits_per_pixel),
                ctypes.byref(saturation_threshold),
                ctypes.byref(row_stride),
                ctypes.byref(data_ptr),
            ),
            "Get live capture info",
        )
        return {
            "width": width.value,
            "height": height.value,
            "bit_depth": bit_depth.value,
            "bits_per_pixel": bits_per_pixel.value,
            "saturation_threshold": saturation_threshold.value,
            "row_stride": row_stride.value,
            "data_ptr": data_ptr.value,
        }

    def release_live_capture_result(self, capture_handle):
        self.HeraAPI_ReleaseLiveCaptureResult(capture_handle)

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

    def clear_roi(self):
        if not self.HeraAPI_ClearROI:
            raise RuntimeError("Clear ROI is not available in this Hera SDK DLL.")
        self.check_status(self.HeraAPI_ClearROI(self.device_handle), "Clear ROI")

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

    def get_hyperspectral_data_is_hdr(self, data_handle):
        if not self.HeraAPI_GetHyperspectralDataIsHDR:
            return None
        hdr = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_GetHyperspectralDataIsHDR(data_handle, ctypes.byref(hdr)), "Get hyperspectral data HDR flag")
        return hdr.value

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

    def get_hypercube_is_hdr(self, hypercube_handle):
        if not self.HeraAPI_GetHyperCubeIsHDR:
            return None
        hdr = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_GetHyperCubeIsHDR(hypercube_handle, ctypes.byref(hdr)), "Get hypercube HDR flag")
        return hdr.value

    def get_hypercube_band_data(self, hypercube_handle, band_index, width, height, data_type):
        wavelength = ctypes.c_double()
        sample_count = width * height
        data_ptr = ctypes.c_void_p()
        self.check_status(
            self.HeraAPI_GetHyperCubeBandData(
                hypercube_handle,
                ctypes.c_uint(band_index),
                ctypes.byref(wavelength),
                ctypes.byref(data_ptr),
            ),
            "Get hypercube band data",
        )
        if not data_ptr.value:
            raise RuntimeError("Hypercube band data pointer was null.")
        if data_type == 0:
            values = ctypes.cast(data_ptr, ctypes.POINTER(ctypes.c_float))
        else:
            values = ctypes.cast(data_ptr, ctypes.POINTER(ctypes.c_double))
        return wavelength.value, [float(values[index]) for index in range(sample_count)]

    def get_hypercube_band_pixel_value(self, hypercube_handle, band_index, pixel_index, data_type):
        wavelength = ctypes.c_double()
        data_ptr = ctypes.c_void_p()
        self.check_status(
            self.HeraAPI_GetHyperCubeBandData(
                hypercube_handle,
                ctypes.c_uint(band_index),
                ctypes.byref(wavelength),
                ctypes.byref(data_ptr),
            ),
            "Get hypercube band data",
        )
        if not data_ptr.value:
            raise RuntimeError("Hypercube band data pointer was null.")
        if pixel_index < 0:
            raise RuntimeError(f"Invalid hypercube pixel index {pixel_index}.")
        if data_type == 0:
            values = ctypes.cast(data_ptr, ctypes.POINTER(ctypes.c_float))
        else:
            values = ctypes.cast(data_ptr, ctypes.POINTER(ctypes.c_double))
        return wavelength.value, float(values[pixel_index])

    def get_hypercube_band_pointer(self, hypercube_handle, band_index, data_type):
        wavelength = ctypes.c_double()
        data_ptr = ctypes.c_void_p()
        self.check_status(
            self.HeraAPI_GetHyperCubeBandData(
                hypercube_handle,
                ctypes.c_uint(band_index),
                ctypes.byref(wavelength),
                ctypes.byref(data_ptr),
            ),
            "Get hypercube band data pointer",
        )
        if not data_ptr.value:
            raise RuntimeError("Hypercube band data pointer was null.")
        if data_type == 0:
            values = ctypes.cast(data_ptr, ctypes.POINTER(ctypes.c_float))
        else:
            values = ctypes.cast(data_ptr, ctypes.POINTER(ctypes.c_double))
        return wavelength.value, values

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
