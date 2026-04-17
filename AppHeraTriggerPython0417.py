import ctypes
import csv
import math
import os
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, simpledialog, ttk


@dataclass
class SavedPosition:
    name: str
    x: float
    y: float


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

    def is_pixel_format_supported(self, pixel_format):
        supported = ctypes.c_bool(False)
        self.check_status(self.HeraAPI_IsPixelFormatSupported(self.device_handle, ctypes.c_int(pixel_format), ctypes.byref(supported)), "Check pixel format support")
        return supported.value

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


class TangoController:
    OK_STATUS = 0
    INTERFACE_RS232 = 1
    INTERFACE_OPTIONS = {
        "RS232 / COM": 1,
        "USB": 2,
        "PCI": 3,
    }
    AXIS_XY_FLAGS = 3

    def __init__(self, dll_path=None):
        base = os.path.abspath(os.path.dirname(__file__))
        self.dll_path = dll_path or os.path.join(base, "Tango_DLL.dll")
        self.dll = None
        self.lsid = 0
        self.connected = False
        self.load_dll()

    def load_dll(self):
        if not os.path.exists(self.dll_path):
            raise FileNotFoundError(f"Tango DLL not found: {self.dll_path}")
        self.dll = ctypes.WinDLL(self.dll_path)
        self._define_functions()

    def _define_function(self, name, restype=ctypes.c_int, argtypes=None):
        try:
            func = getattr(self.dll, name)
        except AttributeError as exc:
            raise AttributeError(f"Tango DLL function not found: {name}") from exc
        func.restype = restype
        if argtypes is not None:
            func.argtypes = argtypes
        return func

    def _define_functions(self):
        bool_ptr = ctypes.POINTER(ctypes.c_int)
        double_ptr = ctypes.POINTER(ctypes.c_double)
        int_ptr = ctypes.POINTER(ctypes.c_int)

        self.LSX_CreateLSID = self._define_function("LSX_CreateLSID", ctypes.c_int, [int_ptr])
        self.LSX_FreeLSID = self._define_function("LSX_FreeLSID", ctypes.c_int, [ctypes.c_int])
        self.LSX_ConnectSimple = self._define_function(
            "LSX_ConnectSimple",
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_int],
        )
        self.LSX_Disconnect = self._define_function("LSX_Disconnect", ctypes.c_int, [ctypes.c_int])
        self.LSX_GetTangoVersion = self._define_function(
            "LSX_GetTangoVersion",
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_char_p, ctypes.c_int],
        )
        self.LSX_GetError = self._define_function("LSX_GetError", ctypes.c_int, [ctypes.c_int, int_ptr])
        self.LSX_GetErrorString = self._define_function(
            "LSX_GetErrorString",
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int],
        )
        self.LSX_GetPos = self._define_function(
            "LSX_GetPos",
            ctypes.c_int,
            [ctypes.c_int, double_ptr, double_ptr, double_ptr, double_ptr],
        )
        self.LSX_GetVel = self._define_function(
            "LSX_GetVel",
            ctypes.c_int,
            [ctypes.c_int, double_ptr, double_ptr, double_ptr, double_ptr],
        )
        self.LSX_GetSecVel = self._define_function(
            "LSX_GetSecVel",
            ctypes.c_int,
            [ctypes.c_int, double_ptr, double_ptr, double_ptr, double_ptr],
        )
        self.LSX_GetAccel = self._define_function(
            "LSX_GetAccel",
            ctypes.c_int,
            [ctypes.c_int, double_ptr, double_ptr, double_ptr, double_ptr],
        )
        self.LSX_SetVel = self._define_function(
            "LSX_SetVel",
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double],
        )
        self.LSX_SetSecVel = self._define_function(
            "LSX_SetSecVel",
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double],
        )
        self.LSX_SetAccel = self._define_function(
            "LSX_SetAccel",
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double],
        )
        self.LSX_MoveAbs = self._define_function(
            "LSX_MoveAbs",
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_int],
        )
        self.LSX_WaitForAxisStop = self._define_function(
            "LSX_WaitForAxisStop",
            ctypes.c_int,
            [ctypes.c_int, ctypes.c_int, ctypes.c_int, bool_ptr],
        )
        self.LSX_StopAxes = self._define_function("LSX_StopAxes", ctypes.c_int, [ctypes.c_int])
        self.LSX_Calibrate = self._define_function("LSX_Calibrate", ctypes.c_int, [ctypes.c_int])
        self.LSX_RMeasure = self._define_function("LSX_RMeasure", ctypes.c_int, [ctypes.c_int])

    def check_status(self, status, action):
        if status != self.OK_STATUS:
            raise RuntimeError(f"{action} failed with Tango error code {status}: {self.get_error_string(status)}")

    def get_error_string(self, error_code):
        if not self.dll:
            return "Unknown Tango error"
        buffer = ctypes.create_string_buffer(512)
        lsid = ctypes.c_int(self.lsid if self.lsid else 0)
        try:
            lookup_status = self.LSX_GetErrorString(lsid, ctypes.c_int(error_code), buffer, ctypes.c_int(511))
            if lookup_status == self.OK_STATUS:
                text = buffer.value.decode("ascii", errors="ignore").strip()
                if text:
                    return text
        except Exception:
            pass
        return "Unknown Tango error"

    def connect(self, interface_type, com_port, baud_rate, show_protocol=False):
        if self.connected:
            return
        lsid_ptr = ctypes.c_int()
        self.check_status(self.LSX_CreateLSID(ctypes.byref(lsid_ptr)), "Create LSID")
        self.lsid = lsid_ptr.value
        try:
            self.check_status(
                self.LSX_ConnectSimple(
                    ctypes.c_int(self.lsid),
                    ctypes.c_int(interface_type),
                    com_port.encode("ascii"),
                    ctypes.c_int(baud_rate),
                    ctypes.c_int(1 if show_protocol else 0),
                ),
                "Connect stage",
            )
        except Exception:
            self.LSX_FreeLSID(ctypes.c_int(self.lsid))
            self.lsid = 0
            raise
        self.connected = True

    def disconnect(self):
        if self.lsid:
            try:
                self.LSX_Disconnect(ctypes.c_int(self.lsid))
            finally:
                self.LSX_FreeLSID(ctypes.c_int(self.lsid))
                self.lsid = 0
                self.connected = False

    def get_version(self):
        self._require_connected()
        buffer = ctypes.create_string_buffer(256)
        self.check_status(self.LSX_GetTangoVersion(ctypes.c_int(self.lsid), buffer, ctypes.c_int(255)), "Get controller version")
        return buffer.value.decode("ascii", errors="ignore").strip()

    def get_position(self):
        self._require_connected()
        x = ctypes.c_double()
        y = ctypes.c_double()
        z = ctypes.c_double()
        a = ctypes.c_double()
        self.check_status(self.LSX_GetPos(ctypes.c_int(self.lsid), ctypes.byref(x), ctypes.byref(y), ctypes.byref(z), ctypes.byref(a)), "Get position")
        return x.value, y.value, z.value, a.value

    def _get_motion_values(self, func, action):
        x = ctypes.c_double()
        y = ctypes.c_double()
        z = ctypes.c_double()
        a = ctypes.c_double()
        self.check_status(func(ctypes.c_int(self.lsid), ctypes.byref(x), ctypes.byref(y), ctypes.byref(z), ctypes.byref(a)), action)
        return x.value, y.value, z.value, a.value

    def get_velocity(self):
        self._require_connected()
        return self._get_motion_values(self.LSX_GetVel, "Get velocity")

    def get_secure_velocity(self):
        self._require_connected()
        return self._get_motion_values(self.LSX_GetSecVel, "Get secure velocity")

    def get_acceleration(self):
        self._require_connected()
        return self._get_motion_values(self.LSX_GetAccel, "Get acceleration")

    def apply_motion_settings(self, speed_xy, accel_xy, secure_vel_xy):
        self._require_connected()
        cur_vel = self.get_velocity()
        cur_accel = self.get_acceleration()
        cur_sec_vel = self.get_secure_velocity()
        self.check_status(
            self.LSX_SetVel(ctypes.c_int(self.lsid), speed_xy, speed_xy, cur_vel[2], cur_vel[3]),
            "Set velocity",
        )
        self.check_status(
            self.LSX_SetAccel(ctypes.c_int(self.lsid), accel_xy, accel_xy, cur_accel[2], cur_accel[3]),
            "Set acceleration",
        )
        self.check_status(
            self.LSX_SetSecVel(ctypes.c_int(self.lsid), secure_vel_xy, secure_vel_xy, cur_sec_vel[2], cur_sec_vel[3]),
            "Set secure velocity",
        )

    def move_absolute_xy(self, x, y):
        self._require_connected()
        _, _, z, a = self.get_position()
        self.check_status(
            self.LSX_MoveAbs(ctypes.c_int(self.lsid), x, y, z, a, ctypes.c_int(0)),
            "Move absolute XY",
        )

    def wait_for_xy_stop(self, timeout_ms):
        self._require_connected()
        timed_out = ctypes.c_int(0)
        self.check_status(
            self.LSX_WaitForAxisStop(
                ctypes.c_int(self.lsid),
                ctypes.c_int(self.AXIS_XY_FLAGS),
                ctypes.c_int(timeout_ms),
                ctypes.byref(timed_out),
            ),
            "Wait for XY stop",
        )
        if timed_out.value != 0:
            raise RuntimeError("XY motion timed out")

    def stop_axes(self):
        if self.connected:
            self.LSX_StopAxes(ctypes.c_int(self.lsid))

    def calibrate(self):
        self._require_connected()
        self.check_status(self.LSX_Calibrate(ctypes.c_int(self.lsid)), "Calibrate stage")

    def range_measure(self):
        self._require_connected()
        self.check_status(self.LSX_RMeasure(ctypes.c_int(self.lsid)), "Range measure")

    def _require_connected(self):
        if not self.connected or not self.lsid:
            raise RuntimeError("Tango stage is not connected.")


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
        "RunningTimelapse": "Running timelapse",
        "Paused": "Paused",
        "Error": "Error",
    }

    SCAN_MODES = {"Short": 0, "Medium": 1, "Long": 2, "ExtraLong": 3}
    TRIGGER_MODES = {"Internal": 0, "DeferredStartExtLineHi": 1, "StepScanExtLoHi": 2}
    BINNING_OPTIONS = {"None": 0, "2x": 1, "4x": 2, "8x": 3, "2x Enhanced": 0x1000, "4x Enhanced": 0x1001}
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
        self.configure(bg="#14181d")

        self.controller = None
        self.tango = None
        self.devices = []
        self.positions = [SavedPosition("Start", 0.0, 0.0)]
        self.selected_position_index = None
        self.processing_lock = threading.Lock()
        self.stage_lock = threading.Lock()
        self.live_frame_lock = threading.Lock()
        self.app_state = self.STATE_LABELS["Idle"]
        self.stage_poll_job = None
        self.timelapse_thread = None
        self.timelapse_stop_event = threading.Event()
        self.timelapse_pause_event = threading.Event()
        self.acquisition_done_event = threading.Event()
        self.acquisition_success = False
        self.last_export_path = ""
        self.last_acquisition_error = ""
        self.trigger_log = []
        self.dll_path_var = tk.StringVar(value=HeraController.default_dll_path())
        self.env_var = tk.StringVar(value=HeraController.get_hera_devices_path() or "")
        self.license_var = tk.StringVar(value="Unknown")
        self.selected_device_var = tk.StringVar(value="(none)")
        self.tango_dll_var = tk.StringVar(value=os.path.join(os.path.abspath(os.path.dirname(__file__)), "Tango_DLL.dll"))
        self.stage_port_var = tk.StringVar(value="COM6")
        self.stage_baud_var = tk.IntVar(value=57600)
        self.stage_interface_var = tk.StringVar(value="RS232 / COM")
        self.timelapse_status_var = tk.StringVar(value="Timelapse: idle")
        self.time_remaining_var = tk.StringVar(value="Time remaining: -")
        self.center_stage_summary_var = tk.StringVar(value="Selected position: none")
        self.current_cycle_var = tk.StringVar(value="Cycle: -")
        self.current_site_var = tk.StringVar(value="Site: -")
        self.last_export_var = tk.StringVar(value="Last export: -")
        self.hypercube_summary_var = tk.StringVar(value="Cube: waiting for acquisition")
        self.live_view_status_var = tk.StringVar(value="Live view: waiting for frames")
        self.pending_export_tag = None
        self.live_photo = None
        self.live_frame_info = None
        self.latest_live_frame = None
        self.live_pixel_format_name = "Unknown"
        self.live_first_frame_logged = False
        self.live_first_frame_rendered = False
        self.live_watchdog_job = None
        self.live_render_pending = False
        self.last_live_render_time = 0.0
        self.live_render_interval_sec = 0.20
        self.live_max_preview_width = 480
        self.hyper_photo = None
        self.current_hypercube_handle = None
        self.current_hypercube_info = None
        self.current_hyper_band_cache = {}
        self.current_hyper_band_index = tk.IntVar(value=0)
        self.hyper_band_jump_var = tk.StringVar(value="1")
        self.current_hyper_wavelength_var = tk.StringVar(value="Wavelength: -")
        self.current_hyper_band_var = tk.StringVar(value="Band: -")

        self._configure_theme()
        self._build_ui()
        self.refresh_positions_tree()
        self.update_state("Idle")
        self.start_stage_polling()
        self.after(250, self.auto_connect_devices)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _configure_theme(self):
        self.theme = {
            "bg": "#14181d",
            "panel": "#1d232a",
            "panel_alt": "#232a32",
            "field": "#0f1318",
            "border": "#3a434d",
            "text": "#e7edf5",
            "muted": "#9aa6b2",
            "accent": "#ff8b3d",
            "accent_soft": "#ffb37a",
            "success": "#7ad97a",
            "danger": "#ff6a6a",
        }
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Dark.Treeview", background=self.theme["panel"], fieldbackground=self.theme["panel"], foreground=self.theme["text"], rowheight=28, bordercolor=self.theme["border"], lightcolor=self.theme["border"], darkcolor=self.theme["border"])
        style.configure("Dark.Treeview.Heading", background=self.theme["panel_alt"], foreground=self.theme["text"], relief="flat")
        style.map("Dark.Treeview", background=[("selected", self.theme["accent"])], foreground=[("selected", "#111111")])
        style.configure("Dark.TSeparator", background=self.theme["border"])

        self.option_add("*Font", "{Segoe UI} 10")
        self.option_add("*Background", self.theme["panel"])
        self.option_add("*Foreground", self.theme["text"])
        self.option_add("*Label.Background", self.theme["panel"])
        self.option_add("*Label.Foreground", self.theme["text"])
        self.option_add("*LabelFrame.Background", self.theme["panel"])
        self.option_add("*LabelFrame.Foreground", self.theme["text"])
        self.option_add("*Button.Background", self.theme["panel_alt"])
        self.option_add("*Button.Foreground", self.theme["text"])
        self.option_add("*Entry.Background", self.theme["field"])
        self.option_add("*Entry.Foreground", self.theme["text"])
        self.option_add("*Text.Background", self.theme["field"])
        self.option_add("*Text.Foreground", self.theme["text"])
        self._apply_theme_recursive(self)

    def _apply_theme_recursive(self, widget):
        cls = widget.winfo_class()
        try:
            if cls in {"Frame", "Labelframe", "LabelFrame", "Toplevel"}:
                widget.configure(bg=self.theme["panel"], highlightbackground=self.theme["border"], highlightcolor=self.theme["border"])
            elif cls == "Label":
                widget.configure(bg=self.theme["panel"], fg=self.theme["text"])
            elif cls == "Button":
                widget.configure(bg=self.theme["panel_alt"], fg=self.theme["text"], activebackground=self.theme["accent"], activeforeground="#111111", relief="flat", bd=0, padx=10, pady=6, cursor="hand2")
            elif cls == "Entry":
                widget.configure(bg=self.theme["field"], fg=self.theme["text"], insertbackground=self.theme["accent_soft"], relief="flat", bd=6)
            elif cls == "Text":
                widget.configure(bg=self.theme["field"], fg=self.theme["text"], insertbackground=self.theme["accent_soft"], relief="flat", bd=0)
            elif cls == "Menubutton":
                widget.configure(bg=self.theme["panel_alt"], fg=self.theme["text"], activebackground=self.theme["accent"], activeforeground="#111111", relief="flat", bd=0, highlightthickness=0)
                try:
                    widget["menu"].configure(bg=self.theme["panel_alt"], fg=self.theme["text"], activebackground=self.theme["accent"], activeforeground="#111111")
                except Exception:
                    pass
        except Exception:
            pass

        for child in widget.winfo_children():
            self._apply_theme_recursive(child)

    def _build_ui(self):
        shell = tk.Frame(self, bg="#14181d")
        shell.pack(fill="both", expand=True, padx=14, pady=14)

        toolbar = tk.Frame(shell, bg="#14181d")
        toolbar.pack(fill="x", pady=(0, 12))
        title = tk.Label(toolbar, text="HERA + Tango Trigger", font=("Segoe UI Semibold", 16), bg="#14181d", fg="#f3f6fb")
        title.pack(side="left")
        subtitle = tk.Label(toolbar, text="Stage-guided hyperspectral acquisition", font=("Segoe UI", 10), bg="#14181d", fg="#9aa6b2")
        subtitle.pack(side="left", padx=(12, 0), pady=(4, 0))
        top_actions = tk.Frame(toolbar, bg="#14181d")
        top_actions.pack(side="right")
        tk.Button(top_actions, text="Run Selected Site", command=self.manual_trigger_selected_position).pack(side="left", padx=4)
        tk.Button(
            top_actions,
            text="Start Timelapse",
            command=self.start_timelapse,
            bg="#ff8b3d",
            fg="#111111",
            activebackground="#ffb37a",
        ).pack(side="left", padx=4)
        self.pause_button = tk.Button(top_actions, text="Pause", command=self.pause_or_resume_timelapse)
        self.pause_button.pack(side="left", padx=4)
        tk.Button(top_actions, text="Stop Timelapse", command=self.stop_timelapse).pack(side="left", padx=4)

        body = tk.Frame(shell, bg="#14181d")
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_columnconfigure(2, weight=0)
        body.grid_rowconfigure(0, weight=1)

        left = tk.Frame(body, bg="#14181d")
        left.grid(row=0, column=0, sticky="nsw")
        center = tk.Frame(body, bg="#14181d")
        center.grid(row=0, column=1, sticky="nsew", padx=12)
        right = tk.Frame(body, bg="#14181d")
        right.grid(row=0, column=2, sticky="nse")

        self._build_tango_ui(left)
        self._build_log_ui(center)
        self._build_hera_ui(right)

    def _build_hera_ui(self, parent):
        frame = tk.LabelFrame(parent, text="Hera Acquisition", padx=8, pady=8)
        frame.pack(fill="x", pady=(0, 10))

        top = tk.Frame(frame)
        top.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        tk.Label(top, text="Connection and discovery are automatic.", fg="#9aa6b2").pack(side="left")
        tk.Label(top, textvariable=self.license_var, fg="#7ad97a").pack(side="right")

        buttons = tk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=3, sticky="w", pady=8)
        tk.Button(buttons, text="Preflight", command=self.preflight_check).pack(side="left", padx=6)
        tk.Button(buttons, text="Live Status", command=self.debug_live_status).pack(side="left", padx=6)
        tk.Button(buttons, text="Restart Live", command=self.restart_live_view).pack(side="left", padx=6)

        params = tk.LabelFrame(frame, text="Acquisition Parameters", padx=8, pady=8)
        params.grid(row=2, column=0, columnspan=3, sticky="ew")

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
            tk.Label(params, text=label_text).grid(row=row, column=0, sticky="w", pady=2)
            if key == "scan_mode":
                self.param_vars[key] = tk.StringVar(value=default)
                tk.OptionMenu(params, self.param_vars[key], *list(self.SCAN_MODES.keys())).grid(row=row, column=1, sticky="w")
            elif key == "trigger_mode":
                self.param_vars[key] = tk.StringVar(value=default)
                tk.OptionMenu(params, self.param_vars[key], *list(self.TRIGGER_MODES.keys())).grid(row=row, column=1, sticky="w")
            elif key == "binning":
                self.param_vars[key] = tk.StringVar(value=default)
                tk.OptionMenu(params, self.param_vars[key], *list(self.BINNING_OPTIONS.keys())).grid(row=row, column=1, sticky="w")
            elif key == "data_type":
                self.param_vars[key] = tk.StringVar(value=default)
                tk.OptionMenu(params, self.param_vars[key], *list(self.DATA_TYPES.keys())).grid(row=row, column=1, sticky="w")
            elif key == "output_path":
                self.param_vars[key] = tk.StringVar(value=default)
                tk.Entry(params, textvariable=self.param_vars[key], width=32).grid(row=row, column=1, sticky="w")
                tk.Button(params, text="Browse", command=self.browse_output_path).grid(row=row, column=2, padx=4)
            elif isinstance(default, int):
                self.param_vars[key] = tk.IntVar(value=default)
                tk.Entry(params, textvariable=self.param_vars[key], width=12).grid(row=row, column=1, sticky="w")
            else:
                self.param_vars[key] = tk.DoubleVar(value=default)
                tk.Entry(params, textvariable=self.param_vars[key], width=12).grid(row=row, column=1, sticky="w")
            row += 1

        actions = tk.Frame(params)
        actions.grid(row=row, column=0, columnspan=3, pady=8, sticky="w")
        tk.Button(actions, text="Apply Parameters", command=self.apply_parameters).pack(side="left", padx=(0, 6))
        tk.Button(actions, text="Start Hera Acquisition", command=self.start_acquisition).pack(side="left", padx=6)
        tk.Button(actions, text="Abort Hera Acquisition", command=self.abort_acquisition).pack(side="left", padx=6)

    def _build_tango_ui(self, parent):
        frame = tk.LabelFrame(parent, text="Stage Control", padx=10, pady=10)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        self.stage_speed_var = tk.DoubleVar(value=20.0)
        self.stage_dwell_var = tk.DoubleVar(value=0.0)
        self.position_name_var = tk.StringVar()
        self.selected_name_var = tk.StringVar()
        self.selected_x_var = tk.StringVar()
        self.selected_y_var = tk.StringVar()

        tk.Label(frame, text="Position name").grid(row=0, column=0, sticky="w", pady=(0, 2))
        tk.Entry(frame, textvariable=self.position_name_var, width=24).grid(row=1, column=0, sticky="ew")

        actions = tk.Frame(frame)
        actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        tk.Button(actions, text="Add Current Position", command=self.add_current_position).pack(fill="x", pady=4)
        tk.Button(actions, text="Update Selected Position", command=self.update_selected_position).pack(fill="x", pady=4)
        tk.Button(actions, text="Delete Selected Row", command=self.delete_selected_position).pack(fill="x", pady=4)
        tk.Button(actions, text="Reconnect Stage", command=self.reconnect_stage).pack(fill="x", pady=4)

        rename_panel = tk.LabelFrame(frame, text="Rename Selected Position", padx=8, pady=8)
        rename_panel.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        tk.Entry(rename_panel, textvariable=self.selected_name_var, width=18).pack(side="left", padx=(0, 6))
        tk.Button(rename_panel, text="Rename", command=self.rename_selected_position).pack(side="left")

        self.stage_status_var = tk.StringVar(value="Stage: not connected")
        self.stage_version_var = tk.StringVar(value="Controller: -")
        self.stage_position_var = tk.StringVar(value="X: -, Y: -")
        tk.Label(frame, textvariable=self.stage_status_var, font=("Segoe UI", 10, "bold")).grid(row=4, column=0, sticky="w", pady=(12, 0))
        tk.Label(frame, textvariable=self.stage_version_var).grid(row=5, column=0, sticky="w", pady=(4, 0))

        pos_panel = tk.LabelFrame(frame, text="Current XY Position", padx=8, pady=8)
        pos_panel.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        self.current_x_label = tk.Label(pos_panel, text="X: -")
        self.current_x_label.pack(anchor="w")
        self.current_y_label = tk.Label(pos_panel, text="Y: -")
        self.current_y_label.pack(anchor="w", pady=(4, 0))

        goto_panel = tk.LabelFrame(frame, text="Go To Saved Position", padx=8, pady=8)
        goto_panel.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        top_row = tk.Frame(goto_panel)
        top_row.pack(fill="x")
        tk.Button(top_row, text="Go", width=6, command=self.goto_selected_position).pack(side="left")
        tk.Label(top_row, text="Select a row in the table, then press Go", wraplength=180, justify="left", fg="#9aa6b2").pack(side="left", padx=8)
        coord_row = tk.Frame(goto_panel)
        coord_row.pack(fill="x", pady=(8, 0))
        tk.Label(coord_row, text="X").pack(side="left")
        tk.Entry(coord_row, textvariable=self.selected_x_var, width=10).pack(side="left", padx=(4, 10))
        tk.Label(coord_row, text="Y").pack(side="left")
        tk.Entry(coord_row, textvariable=self.selected_y_var, width=10).pack(side="left", padx=(4, 0))
        coord_actions = tk.Frame(goto_panel)
        coord_actions.pack(fill="x", pady=(8, 0))
        tk.Button(coord_actions, text="Use Current XY", command=self.capture_current_stage_position_into_selected).pack(side="left", padx=(0, 6))
        tk.Button(coord_actions, text="Save Selected Edits", command=self.apply_selected_position_edits).pack(side="left", padx=6)

        tl = tk.LabelFrame(frame, text="Timelapse Settings", padx=8, pady=8)
        tl.grid(row=8, column=0, sticky="ew", pady=(10, 0))
        tk.Label(tl, text="Interval (min)").grid(row=0, column=0, sticky="w")
        self.interval_var = tk.DoubleVar(value=10.0)
        tk.Entry(tl, textvariable=self.interval_var, width=8).grid(row=0, column=1, sticky="w", padx=(6, 10))
        tk.Label(tl, text="Dwell (s)").grid(row=0, column=2, sticky="w")
        tk.Entry(tl, textvariable=self.stage_dwell_var, width=8).grid(row=0, column=3, sticky="w")
        tk.Label(tl, text="Stop after (min)").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.stop_after_var = tk.DoubleVar(value=0.0)
        tk.Entry(tl, textvariable=self.stop_after_var, width=8).grid(row=1, column=1, sticky="w", padx=(6, 10), pady=(10, 0))
        tk.Label(tl, text="Speed XY").grid(row=1, column=2, sticky="w", pady=(10, 0))
        tk.Entry(tl, textvariable=self.stage_speed_var, width=8).grid(row=1, column=3, sticky="w", pady=(10, 0))
        tk.Label(tl, textvariable=self.timelapse_status_var, font=("Segoe UI", 10, "bold")).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        tk.Label(tl, textvariable=self.time_remaining_var).grid(row=2, column=2, columnspan=2, sticky="w", pady=(10, 0))

    def _build_log_ui(self, parent):
        state_frame = tk.LabelFrame(parent, text="Run Console", padx=10, pady=10)
        state_frame.pack(fill="x")
        self.app_state_var = tk.StringVar(value=self.app_state)
        tk.Label(state_frame, text="Current state:").pack(side="left")
        self.app_state_label = tk.Label(state_frame, textvariable=self.app_state_var, fg="#7ad97a", font=("Segoe UI Semibold", 10))
        self.app_state_label.pack(side="left", padx=6)
        ttk.Separator(state_frame, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(state_frame, textvariable=self.center_stage_summary_var, fg="#9aa6b2").pack(side="left")
        ttk.Separator(state_frame, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(state_frame, textvariable=self.current_cycle_var, fg="#9aa6b2").pack(side="left")
        ttk.Separator(state_frame, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(state_frame, textvariable=self.current_site_var, fg="#9aa6b2").pack(side="left")

        views_frame = tk.LabelFrame(parent, text="Views", padx=10, pady=10)
        views_frame.pack(fill="both", expand=True, pady=(10, 10))
        notebook = ttk.Notebook(views_frame)
        notebook.pack(fill="both", expand=True)

        live_tab = tk.Frame(notebook, bg=self.theme["panel"])
        hyper_tab = tk.Frame(notebook, bg=self.theme["panel"])
        notebook.add(live_tab, text="Live View")
        notebook.add(hyper_tab, text="Hyperspectral View")

        self.live_view_canvas = tk.Canvas(live_tab, bg="#101418", highlightthickness=0)
        self.live_view_canvas.pack(fill="both", expand=True)
        hyper_controls = tk.Frame(hyper_tab, bg=self.theme["panel"])
        hyper_controls.pack(fill="x", padx=8, pady=(8, 4))
        tk.Button(hyper_controls, text="Prev Band", command=lambda: self.step_hyper_band(-1)).pack(side="left", padx=(0, 8))
        tk.Label(hyper_controls, textvariable=self.current_hyper_band_var, fg="#e7edf5").pack(side="left")
        ttk.Separator(hyper_controls, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(hyper_controls, textvariable=self.current_hyper_wavelength_var, fg="#9aa6b2").pack(side="left")
        jump_wrap = tk.Frame(hyper_controls, bg=self.theme["panel"])
        jump_wrap.pack(side="right", padx=(8, 0))
        tk.Button(jump_wrap, text="Go", command=self.jump_to_hyper_band).pack(side="right")
        tk.Entry(jump_wrap, textvariable=self.hyper_band_jump_var, width=6).pack(side="right", padx=(0, 6))
        tk.Label(jump_wrap, text="Band", fg="#9aa6b2").pack(side="right", padx=(0, 6))
        tk.Button(hyper_controls, text="Next Band", command=lambda: self.step_hyper_band(1)).pack(side="right")
        self.hyper_band_scale = tk.Scale(
            hyper_tab,
            from_=0,
            to=0,
            orient="horizontal",
            variable=self.current_hyper_band_index,
            command=self.on_hyper_band_changed,
            showvalue=False,
            highlightthickness=0,
            bd=0,
            bg=self.theme["panel"],
            fg=self.theme["text"],
            troughcolor=self.theme["panel_alt"],
            activebackground=self.theme["accent"],
            sliderlength=28,
            width=18,
            repeatdelay=150,
            repeatinterval=80,
            takefocus=1,
            cursor="hand2",
        )
        self.hyper_band_scale.pack(fill="x", padx=8, pady=(0, 6))
        self.hyper_view_canvas = tk.Canvas(hyper_tab, bg="#101418", highlightthickness=0)
        self.hyper_view_canvas.pack(fill="both", expand=True)
        self.live_view_canvas.bind("<Configure>", lambda _e: self._draw_live_view_placeholder())
        self.hyper_view_canvas.bind("<Configure>", lambda _e: self.render_current_hyper_band())
        for widget in (hyper_tab, self.hyper_band_scale, self.hyper_view_canvas):
            widget.bind("<Left>", lambda _e: self.step_hyper_band(-1))
            widget.bind("<Right>", lambda _e: self.step_hyper_band(1))
            widget.bind("<MouseWheel>", self.on_hyper_mousewheel)
            widget.bind("<Button-4>", lambda _e: self.step_hyper_band(1))
            widget.bind("<Button-5>", lambda _e: self.step_hyper_band(-1))
            widget.bind("<Button-1>", lambda _e, target=widget: target.focus_set(), add="+")

        pos_frame = tk.LabelFrame(parent, text="Saved Positions", padx=10, pady=10)
        pos_frame.pack(fill="x", pady=(0, 10))
        header = tk.Frame(pos_frame)
        header.pack(fill="x", pady=(0, 8))
        tk.Label(header, text="Choose a site in the list, edit it on the left, then run or schedule it from the top bar.", fg="#9aa6b2").pack(side="left")

        center_tree_wrap = tk.Frame(pos_frame)
        center_tree_wrap.pack(fill="both", expand=True)
        self.positions_tree = ttk.Treeview(center_tree_wrap, columns=("name", "x", "y"), show="headings", height=4, style="Dark.Treeview")
        self.positions_tree.heading("name", text="Name")
        self.positions_tree.heading("x", text="X")
        self.positions_tree.heading("y", text="Y")
        self.positions_tree.column("name", width=300, anchor="w")
        self.positions_tree.column("x", width=180, anchor="e")
        self.positions_tree.column("y", width=180, anchor="e")
        center_scroll = ttk.Scrollbar(center_tree_wrap, orient="vertical", command=self.positions_tree.yview)
        self.positions_tree.configure(yscrollcommand=center_scroll.set)
        self.positions_tree.pack(side="left", fill="both", expand=True)
        center_scroll.pack(side="right", fill="y")
        self.positions_tree.bind("<<TreeviewSelect>>", self.on_position_selected)

        status_strip = tk.Frame(parent)
        status_strip.pack(fill="x", pady=(0, 10))
        tk.Label(status_strip, textvariable=self.timelapse_status_var, font=("Segoe UI Semibold", 10)).pack(side="left")
        ttk.Separator(status_strip, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(status_strip, textvariable=self.time_remaining_var, fg="#9aa6b2").pack(side="left")
        ttk.Separator(status_strip, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(status_strip, textvariable=self.last_export_var, fg="#9aa6b2").pack(side="left")

        log_frame = tk.LabelFrame(parent, text="Status / Messages", padx=10, pady=10)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=16, state="disabled", wrap="word", bg="#0f1318", fg="#e7edf5", insertbackground="#ffb37a", relief="flat")
        self.log_text.pack(fill="both", expand=True)

    def browse_dll(self):
        file_path = filedialog.askopenfilename(title="Select Hera API DLL", filetypes=[("DLL files", "*.dll"), ("All files", "*.*")])
        if file_path:
            self.dll_path_var.set(file_path)

    def browse_tango_dll(self):
        file_path = filedialog.askopenfilename(title="Select Tango DLL", filetypes=[("DLL files", "*.dll"), ("All files", "*.*")])
        if file_path:
            self.tango_dll_var.set(file_path)

    def browse_output_path(self):
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self.param_vars["output_path"].set(folder)

    def auto_connect_devices(self):
        self.log("Auto-connect startup sequence running...")
        try:
            self.refresh_device_list()
            if self.devices:
                self.connect_hera()
        except Exception as exc:
            self.log(f"Hera auto-connect skipped: {exc}")

        try:
            if os.path.exists(self.tango_dll_var.get()):
                self.connect_stage()
        except Exception as exc:
            self.log(f"Tango auto-connect skipped: {exc}")

    def _sanitize_export_tag(self, text):
        keep = []
        for ch in text:
            if ch.isalnum() or ch in {"-", "_"}:
                keep.append(ch)
            elif ch in {" ", "."}:
                keep.append("_")
        sanitized = "".join(keep).strip("_")
        return sanitized or "measurement"

    def _unique_position_name(self, requested_name, ignore_index=None):
        base_name = requested_name.strip() or "Site"
        existing = {
            pos.name
            for index, pos in enumerate(self.positions)
            if ignore_index is None or index != ignore_index
        }
        if base_name not in existing:
            return base_name

        suffix = 2
        while True:
            candidate = f"{base_name}_{suffix}"
            if candidate not in existing:
                return candidate
            suffix += 1

    def _wait_for_export_files(self, output_base_path, timeout_sec=15):
        hdr_path = output_base_path + ".hdr"
        data_candidates = [output_base_path, output_base_path + ".raw", output_base_path + ".img", output_base_path + ".dat"]
        deadline = time.time() + timeout_sec
        last_sizes = None
        stable_count = 0

        while time.time() < deadline:
            hdr_exists = os.path.exists(hdr_path)
            present_data_files = [path for path in data_candidates if os.path.exists(path)]
            if hdr_exists and present_data_files:
                sizes = [os.path.getsize(hdr_path)] + [os.path.getsize(path) for path in present_data_files]
                if sizes == last_sizes:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_sizes = sizes
                if stable_count >= 3:
                    return hdr_path
            time.sleep(0.25)

        raise RuntimeError(f"Timed out waiting for exported files for {output_base_path}")

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
            self.start_live_view()
            self.update_state("Ready")
            self.log(f"Connected to Hera device {index}: {self._device_title(self.devices[index])}")
            self.check_license_status()
        except Exception as exc:
            self.log(f"Failed to connect to Hera device: {exc}")
            self.update_state("Error")

    def disconnect_hera(self):
        if not self.controller or not self.controller.device_handle:
            self.log("No Hera device is connected.")
            return
        try:
            self.stop_live_view()
            self._clear_hypercube_viewer()
            if self.controller.connected:
                self.controller.disconnect()
            self.controller.release_device()
            self.license_var.set("Unknown")
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
            self.log("Tango stage disconnected.")
        except Exception as exc:
            self.log(f"Failed to disconnect stage: {exc}")
            self.update_state("Error")

    def apply_stage_motion_settings(self):
        if not self.tango or not self.tango.connected:
            self.log("Connect the stage before applying motion settings.")
            return
        try:
            speed_xy = float(self.stage_speed_var.get())
            if speed_xy <= 0:
                raise RuntimeError("Stage speed must be greater than zero.")
            self.tango.apply_motion_settings(speed_xy=speed_xy, accel_xy=1.0, secure_vel_xy=50.0)
            self.log(f"Stage motion updated: speedXY={speed_xy:.3f}")
        except Exception as exc:
            self.log(f"Failed to apply stage motion settings: {exc}")
            self.update_state("Error")

    def update_stage_position_display(self):
        if self.tango and self.tango.connected:
            try:
                x, y, _, _ = self.tango.get_position()
                self.stage_position_var.set(f"X: {x:.3f}, Y: {y:.3f}")
                self.current_x_label.config(text=f"X: {x:.3f}")
                self.current_y_label.config(text=f"Y: {y:.3f}")
                self._draw_live_view_placeholder()
            except Exception:
                pass

    def start_stage_polling(self):
        self._poll_stage_position()

    def _poll_stage_position(self):
        self.update_stage_position_display()
        self._update_time_remaining()
        self.stage_poll_job = self.after(250, self._poll_stage_position)

    def preflight_check(self):
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

    def start_live_view(self):
        if not self.controller or not self.controller.connected:
            return
        try:
            if self.controller.is_live_capturing():
                self.log("Hera live capture already running.")
                return
            supported_formats = []
            for pixel_format, pixel_name in self.LIVE_PIXEL_FORMATS.items():
                if self.controller.is_pixel_format_supported(pixel_format):
                    supported_formats.append((pixel_format, pixel_name))
            if supported_formats:
                self.log("Supported live pixel formats: " + ", ".join(name for _, name in supported_formats))
            selected_format = None
            for pixel_format, pixel_name in supported_formats:
                if selected_format is None:
                    selected_format = pixel_format
                    self.live_pixel_format_name = pixel_name
                    break
            if selected_format is None:
                self.log("Live view could not start: no supported live pixel format reported by the SDK.")
                self._set_live_view_status("Live view: no supported pixel format")
                return
            self.controller.start_live_capture(pixel_format=selected_format)
            self.live_first_frame_logged = False
            self.live_first_frame_rendered = False
            self._set_live_view_status(f"Live view: starting ({self.live_pixel_format_name})")
            if self.live_watchdog_job:
                try:
                    self.after_cancel(self.live_watchdog_job)
                except Exception:
                    pass
            self.live_watchdog_job = self.after(8000, self._check_live_view_started)
            self.log(f"Hera live capture started using {self.live_pixel_format_name}.")
        except Exception as exc:
            self._set_live_view_status("Live view: failed to start")
            self.log(f"Live view could not start: {exc}")

    def restart_live_view(self):
        if not self.controller or not self.controller.connected:
            self.log("Cannot restart live view because Hera is not connected.")
            return
        self.log("Restarting Hera live view...")
        self._set_live_view_status("Live view: restarting")

        def worker():
            try:
                self.controller.stop_live_capture(silent=True)
                self.after(0, self._clear_live_view_frame_state)
                self.after(0, self.start_live_view)
            except Exception as exc:
                self._log_async(f"Live view restart failed: {exc}")
                self.after(0, lambda: self._set_live_view_status("Live view: restart failed"))

        threading.Thread(target=worker, daemon=True).start()

    def stop_live_view(self):
        if not self.controller:
            return
        try:
            self.controller.stop_live_capture(silent=True)
        except Exception:
            pass
        self._clear_live_view_frame_state()
        self._set_live_view_status("Live view: stopped")
        self.after(0, self._draw_live_view_placeholder)

    def _clear_live_view_frame_state(self):
        with self.live_frame_lock:
            self.latest_live_frame = None
            self.live_frame_info = None
            self.live_render_pending = False
            self.last_live_render_time = 0.0
        self.live_photo = None
        self.live_first_frame_rendered = False
        if self.live_watchdog_job:
            try:
                self.after_cancel(self.live_watchdog_job)
            except Exception:
                pass
            self.live_watchdog_job = None

    def _set_live_view_status(self, text):
        if threading.current_thread() is threading.main_thread():
            self.live_view_status_var.set(text)
        else:
            self.after(0, lambda: self.live_view_status_var.set(text))

    def _schedule_live_render(self, force=False):
        now = time.time()
        with self.live_frame_lock:
            if self.live_render_pending:
                return
            if not force and (now - self.last_live_render_time) < self.live_render_interval_sec:
                return
            self.live_render_pending = True
        self.after(0, self._render_live_photo)

    def on_live_capture_error(self, message):
        self._log_async(f"Live capture error: {message}")

    def on_live_capture_timeout(self, free_buffers):
        if free_buffers <= 1:
            self._log_async(f"Live capture buffer warning: free buffers={free_buffers}")

    def on_live_capture_frame(self, capture_handle):
        try:
            info = self.controller.get_live_capture_info(capture_handle)
            if not info["data_ptr"]:
                return

            width = info["width"]
            height = info["height"]
            bit_depth = info["bit_depth"]
            row_stride = info["row_stride"]
            bits_per_pixel = info["bits_per_pixel"]
            bytes_per_pixel = max(1, (bits_per_pixel + 7) // 8)
            raw_size = row_stride * height
            raw_buffer = ctypes.string_at(info["data_ptr"], raw_size)
            if row_stride != width * bytes_per_pixel:
                rows = [raw_buffer[row * row_stride: row * row_stride + (width * bytes_per_pixel)] for row in range(height)]
                frame_bytes = b"".join(rows)
            else:
                frame_bytes = raw_buffer

            if bytes_per_pixel == 1:
                mono8_bytes = frame_bytes
            else:
                effective_depth = bit_depth if bit_depth > 0 else bits_per_pixel
                max_value = float((1 << effective_depth) - 1) if effective_depth > 0 else 65535.0
                converted = bytearray(width * height)
                src_index = 0
                dst_index = 0
                for _ in range(width * height):
                    sample = int.from_bytes(frame_bytes[src_index:src_index + bytes_per_pixel], "little", signed=False)
                    converted[dst_index] = max(0, min(255, int(round((sample / max_value) * 255.0))))
                    src_index += bytes_per_pixel
                    dst_index += 1
                mono8_bytes = bytes(converted)

            target_w = min(width, self.live_max_preview_width)
            scale = max(1, width // target_w)
            if scale > 1:
                sampled_rows = []
                for row_index in range(0, height, scale):
                    row = mono8_bytes[row_index * width:(row_index + 1) * width]
                    sampled_rows.append(row[::scale])
                display_bytes = b"".join(sampled_rows)
                disp_width = len(sampled_rows[0]) if sampled_rows else width
                disp_height = len(sampled_rows)
            else:
                display_bytes = mono8_bytes
                disp_width = width
                disp_height = height
            display_bytes, preview_min, preview_max = self._normalize_grayscale_for_display(display_bytes)

            with self.live_frame_lock:
                self.live_frame_info = (width, height, bits_per_pixel)
                self.latest_live_frame = (disp_width, disp_height, display_bytes)
            self._set_live_view_status(f"Live view: receiving {self.live_pixel_format_name}")
            if not self.live_first_frame_logged:
                self.live_first_frame_logged = True
                if self.live_watchdog_job:
                    try:
                        self.after_cancel(self.live_watchdog_job)
                    except Exception:
                        pass
                    self.live_watchdog_job = None
                self._log_async(
                    f"First live frame received: {width}x{height}, bitDepth={bit_depth}, bitsPerPixel={bits_per_pixel}, format={self.live_pixel_format_name}"
                )
                self._log_async(f"Live preview auto-contrast range: min={preview_min}, max={preview_max}")
            self._schedule_live_render(force=not self.live_photo)
        except Exception as exc:
            self._log_async(f"Live frame decode failed: {exc}")
        finally:
            try:
                self.controller.release_live_capture_result(capture_handle)
            except Exception:
                pass

    def _check_live_view_started(self):
        self.live_watchdog_job = None
        if not self.live_first_frame_logged:
            self._set_live_view_status("Live view: no frames received")
            self.log("Live view started but no frames were received from the Hera SDK.")
        elif not self.live_first_frame_rendered:
            self._set_live_view_status(f"Live view: frames received, waiting to draw {self.live_pixel_format_name}")
            self.log("Live view has received frames from the Hera SDK, but the preview has not drawn yet.")

    def debug_live_status(self):
        if not self.controller or not self.controller.connected:
            self.log("Live diagnostics: Hera is not connected.")
            return
        try:
            capturing = self.controller.is_live_capturing()
        except Exception as exc:
            self.log(f"Live diagnostics: failed to read live capture state: {exc}")
            return

        supported_names = []
        for pixel_format, pixel_name in self.LIVE_PIXEL_FORMATS.items():
            try:
                if self.controller.is_pixel_format_supported(pixel_format):
                    supported_names.append(pixel_name)
            except Exception:
                pass

        self.log(
            "Live diagnostics: "
            f"capturing={capturing}, "
            f"status='{self.live_view_status_var.get()}', "
            f"first_frame={self.live_first_frame_logged}, "
            f"first_render={self.live_first_frame_rendered}, "
            f"selected_format={self.live_pixel_format_name}, "
            f"supported={supported_names or ['none']}"
        )
        if self.live_frame_info:
            width, height, bits_per_pixel = self.live_frame_info
            self.log(
                "Live diagnostics: "
                f"last_frame={width}x{height}, "
                f"bits={bits_per_pixel}"
            )
        else:
            self.log("Live diagnostics: no live frame metadata available yet.")

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

    def _arm_and_start_acquisition(self, export_tag=None):
        if not self.controller or not self.controller.connected:
            raise RuntimeError("Connect to Hera before starting acquisition.")
        if not self.check_license_status():
            raise RuntimeError("Hera SDK license is not active.")
        if self.controller.is_acquiring():
            raise RuntimeError("The device is already acquiring.")

        self.apply_parameters()
        if self.app_state == self.STATE_LABELS["Error"]:
            raise RuntimeError("Applying Hera parameters failed.")

        scan_mode = self.SCAN_MODES[self.param_vars["scan_mode"].get()]
        trigger_mode_name = self.param_vars["trigger_mode"].get()
        trigger_mode = self.TRIGGER_MODES[trigger_mode_name]
        averages = int(self.param_vars["averages"].get())
        stabilization = int(self.param_vars["stabilization"].get())

        self.acquisition_done_event.clear()
        self.acquisition_success = False
        self.last_export_path = ""
        self.last_acquisition_error = ""
        self.pending_export_tag = export_tag

        if trigger_mode_name == "Internal":
            self.log("Sending software acquisition command through Hera SDK.")
        else:
            self.log(f"Arming Hera SDK acquisition with trigger mode '{trigger_mode_name}'.")

        self.controller.start_hyperspectral_acquisition(scan_mode, trigger_mode, averages, stabilization)
        self.update_state("Acquiring" if trigger_mode_name == "Internal" else "WaitingForTrigger")
        self.log("Hyperspectral acquisition started.")

    def start_acquisition(self):
        try:
            tag = self._sanitize_export_tag(f"manual_{time.strftime('%Y%m%d_%H%M%S')}")
            self._arm_and_start_acquisition(export_tag=tag)
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
            self.acquisition_done_event.set()
        except Exception as exc:
            self.log(f"Failed to abort acquisition: {exc}")
            self.update_state("Error")

    def run_stage_site_acquisition(self, position, cycle_index=None):
        with self.stage_lock:
            if not self.tango or not self.tango.connected:
                raise RuntimeError("Connect the Tango stage before running a site acquisition.")
            self.apply_stage_motion_settings()
            self.log(f"Moving to {position.name} ...")
            self.tango.move_absolute_xy(position.x, position.y)
            self.tango.wait_for_xy_stop(60000)
            self.update_stage_position_display()
            self._set_var_async(self.current_site_var, f"Site: {position.name}")

            dwell = float(self.stage_dwell_var.get())
            if dwell > 0:
                self.log(f"Settling at {position.name} for {dwell:.1f} seconds.")
                time.sleep(dwell)

            self.log(f"Starting Hera acquisition at {position.name}.")
            if cycle_index is None:
                export_tag = self._sanitize_export_tag(f"{position.name}_{time.strftime('%Y%m%d_%H%M%S')}")
            else:
                export_tag = self._sanitize_export_tag(f"cycle_{cycle_index:03d}_{position.name}")
            self._arm_and_start_acquisition(export_tag=export_tag)

        if not self.acquisition_done_event.wait(timeout=300):
            raise RuntimeError("Timed out waiting for Hera acquisition to complete.")
        if not self.acquisition_success:
            raise RuntimeError(self.last_acquisition_error or "Hera acquisition failed.")
        return self.last_export_path

    def add_current_position(self):
        try:
            if not self.tango or not self.tango.connected:
                raise RuntimeError("Connect the stage before saving positions.")
            x, y, _, _ = self.tango.get_position()
            requested_name = self.position_name_var.get().strip() or f"Site_{len(self.positions) + 1}"
            name = self._unique_position_name(requested_name)
            self.positions.append(SavedPosition(name, x, y))
            self.selected_position_index = len(self.positions) - 1
            self._populate_selected_position_fields(self.positions[self.selected_position_index])
            self.refresh_positions_tree()
            self.position_name_var.set("")
            self.log(f"Added position {name} at X={x:.3f}, Y={y:.3f}.")
        except Exception as exc:
            self.log(f"Failed to add position: {exc}")
            self.update_state("Error")

    def refresh_positions_tree(self):
        for item in self.positions_tree.get_children():
            self.positions_tree.delete(item)
        for index, pos in enumerate(self.positions):
            self.positions_tree.insert("", "end", iid=str(index), values=(pos.name, f"{pos.x:.3f}", f"{pos.y:.3f}"))
        if self.selected_position_index is None and self.positions:
            self.selected_position_index = 0
        if self.selected_position_index is not None and 0 <= self.selected_position_index < len(self.positions):
            self.positions_tree.selection_set(str(self.selected_position_index))
            self.positions_tree.focus(str(self.selected_position_index))
            self._populate_selected_position_fields(self.positions[self.selected_position_index])
            self.center_stage_summary_var.set(
                f"Selected position: {self.positions[self.selected_position_index].name}  |  "
                f"X={self.positions[self.selected_position_index].x:.3f}  "
                f"Y={self.positions[self.selected_position_index].y:.3f}"
            )
        else:
            self._clear_selected_position_fields()
            self.center_stage_summary_var.set("Selected position: none")

    def on_position_selected(self, _event=None):
        selection = self.positions_tree.selection()
        if selection:
            self.selected_position_index = int(selection[0])
            position = self.positions[self.selected_position_index]
            self._populate_selected_position_fields(position)
            self.center_stage_summary_var.set(f"Selected position: {position.name}  |  X={position.x:.3f}  Y={position.y:.3f}")
        else:
            self.selected_position_index = None
            self._clear_selected_position_fields()
            self.center_stage_summary_var.set("Selected position: none")

    def _populate_selected_position_fields(self, position):
        self.selected_name_var.set(position.name)
        self.selected_x_var.set(f"{position.x:.3f}")
        self.selected_y_var.set(f"{position.y:.3f}")

    def _clear_selected_position_fields(self):
        self.selected_name_var.set("")
        self.selected_x_var.set("")
        self.selected_y_var.set("")

    def capture_current_stage_position_into_selected(self):
        try:
            if not self.tango or not self.tango.connected:
                raise RuntimeError("Connect the stage before capturing current XY.")
            x, y, _, _ = self.tango.get_position()
            if not self.selected_name_var.get().strip():
                default_name = f"Site_{len(self.positions) + 1}" if self.selected_position_index is None else self.positions[self.selected_position_index].name
                self.selected_name_var.set(default_name)
            self.selected_x_var.set(f"{x:.3f}")
            self.selected_y_var.set(f"{y:.3f}")
            self.log(f"Loaded current stage position into editor: X={x:.3f}, Y={y:.3f}")
        except Exception as exc:
            self.log(f"Failed to capture current stage position: {exc}")
            self.update_state("Error")

    def apply_selected_position_edits(self):
        try:
            raw_name = self.selected_name_var.get().strip()
            name = self._unique_position_name(raw_name, ignore_index=self.selected_position_index)
            if not name:
                raise RuntimeError("Enter a position name first.")
            x = float(self.selected_x_var.get())
            y = float(self.selected_y_var.get())
            if self.selected_position_index is None:
                self.positions.append(SavedPosition(name, x, y))
                self.selected_position_index = len(self.positions) - 1
                self.log(f"Added new position {name} at X={x:.3f}, Y={y:.3f}.")
            else:
                position = self.positions[self.selected_position_index]
                position.name = name
                position.x = x
                position.y = y
                self.log(f"Saved edits for {name}: X={x:.3f}, Y={y:.3f}.")
            self._populate_selected_position_fields(self.positions[self.selected_position_index])
            self.refresh_positions_tree()
        except Exception as exc:
            self.log(f"Failed to save selected position edits: {exc}")
            self.update_state("Error")

    def _get_selected_position(self):
        if self.selected_position_index is None or not (0 <= self.selected_position_index < len(self.positions)):
            raise RuntimeError("Select a saved position first.")
        return self.positions[self.selected_position_index]

    def update_selected_position(self):
        try:
            position = self._get_selected_position()
            if not self.tango or not self.tango.connected:
                raise RuntimeError("Connect the stage before updating positions.")
            x, y, _, _ = self.tango.get_position()
            position.x = x
            position.y = y
            self._populate_selected_position_fields(position)
            self.refresh_positions_tree()
            self.log(f"Updated {position.name} to X={x:.3f}, Y={y:.3f}.")
        except Exception as exc:
            self.log(f"Failed to update selected position: {exc}")
            self.update_state("Error")

    def rename_selected_position(self):
        try:
            position = self._get_selected_position()
            new_name = self.selected_name_var.get().strip()
            if not new_name:
                raise RuntimeError("Enter a new position name first.")
            old_name = position.name
            position.name = self._unique_position_name(new_name, ignore_index=self.selected_position_index)
            self._populate_selected_position_fields(position)
            self.refresh_positions_tree()
            self.log(f'Renamed "{old_name}" to "{position.name}".')
        except Exception as exc:
            self.log(f"Failed to rename position: {exc}")
            self.update_state("Error")

    def delete_selected_position(self):
        try:
            position = self._get_selected_position()
            del self.positions[self.selected_position_index]
            self.selected_position_index = None
            self.refresh_positions_tree()
            self.center_stage_summary_var.set("Selected position: none")
            self.log(f"Deleted position {position.name}.")
        except Exception as exc:
            self.log(f"Failed to delete position: {exc}")
            self.update_state("Error")

    def goto_selected_position(self):
        try:
            position = self._get_selected_position()
            if not self.tango or not self.tango.connected:
                raise RuntimeError("Connect the stage before moving.")
            with self.stage_lock:
                self.apply_stage_motion_settings()
                self.log(f"Moving to {position.name}.")
                self.tango.move_absolute_xy(position.x, position.y)
                self.tango.wait_for_xy_stop(60000)
                self.update_stage_position_display()
            self.log(f"Reached {position.name}.")
        except Exception as exc:
            self.log(f"Failed to go to selected position: {exc}")
            self.update_state("Error")

    def manual_trigger_selected_position(self):
        try:
            position = self._get_selected_position()
        except Exception as exc:
            self.log(f"Manual site run failed: {exc}")
            self.update_state("Error")
            return

        def worker():
            try:
                export_path = self.run_stage_site_acquisition(position)
                self._log_async(f"Manual site run completed for {position.name}: {export_path}")
            except Exception as exc:
                self._log_async(f"Manual site run failed: {exc}")
                self.after(0, lambda: self.update_state("Error"))

        threading.Thread(target=worker, daemon=True).start()

    def start_timelapse(self):
        if self.timelapse_thread and self.timelapse_thread.is_alive():
            self.log("Timelapse is already running.")
            return
        if not self.positions:
            self.log("Add at least one stage position before starting timelapse.")
            return

        interval_min = float(self.interval_var.get())
        if interval_min <= 0:
            self.log("Interval must be greater than zero.")
            return

        self.timelapse_stop_event.clear()
        self.timelapse_pause_event.clear()
        self.trigger_log = []
        self.timelapse_started_at = datetime.now()
        stop_after = float(self.stop_after_var.get())
        self.timelapse_stop_at = self.timelapse_started_at + timedelta(minutes=stop_after) if stop_after > 0 else None
        self.pause_button.config(text="Pause")
        self.timelapse_status_var.set("Timelapse: running")
        self.update_state("RunningTimelapse")

        self.timelapse_thread = threading.Thread(target=self._timelapse_worker, daemon=True)
        self.timelapse_thread.start()
        self.log("Timelapse started.")

    def pause_or_resume_timelapse(self):
        if not self.timelapse_thread or not self.timelapse_thread.is_alive():
            self.log("Timelapse is not running.")
            return
        if self.timelapse_pause_event.is_set():
            self.timelapse_pause_event.clear()
            self.pause_button.config(text="Pause")
            self.timelapse_status_var.set("Timelapse: running")
            self.update_state("RunningTimelapse")
            self.log("Timelapse resumed.")
        else:
            self.timelapse_pause_event.set()
            self.pause_button.config(text="Resume")
            self.timelapse_status_var.set("Timelapse: paused")
            self.update_state("Paused")
            self.log("Timelapse paused.")

    def stop_timelapse(self):
        self.timelapse_stop_event.set()
        self.timelapse_pause_event.clear()
        self.pause_button.config(text="Pause")
        self.timelapse_status_var.set("Timelapse: stopping")
        self.log("Timelapse stop requested.")

    def _timelapse_worker(self):
        cycle = 0
        interval_min = float(self.interval_var.get())
        try:
            while not self.timelapse_stop_event.is_set():
                if self.timelapse_stop_at and datetime.now() >= self.timelapse_stop_at:
                    self._log_async("Reached requested stop time.")
                    break

                cycle_started_at = datetime.now()
                cycle += 1
                self._set_var_async(self.current_cycle_var, f"Cycle: {cycle}")
                self._log_async(f"Cycle {cycle} started.")
                for position in list(self.positions):
                    if self.timelapse_stop_event.is_set():
                        break
                    self._wait_while_paused()
                    if self.timelapse_stop_event.is_set():
                        break

                    export_path = self.run_stage_site_acquisition(position, cycle_index=cycle)
                    x, y, _, _ = self.tango.get_position()
                    self.trigger_log.append(
                        {
                            "Cycle": cycle,
                            "Site": position.name,
                            "X": f"{x:.6f}",
                            "Y": f"{y:.6f}",
                            "Timestamp": datetime.now().isoformat(timespec="seconds"),
                            "ExportPath": export_path,
                            "Status": "confirmed",
                        }
                    )
                    self._log_async(f"Cycle {cycle}: completed {position.name} -> {export_path}")

                if self.timelapse_stop_event.is_set():
                    break
                if self.timelapse_stop_at and datetime.now() >= self.timelapse_stop_at:
                    self._log_async("Reached requested stop time.")
                    break

                next_cycle_time = cycle_started_at + timedelta(minutes=interval_min)
                self._log_async(f"Cycle {cycle} complete. Waiting {interval_min:.2f} minutes before next cycle.")
                while datetime.now() < next_cycle_time:
                    if self.timelapse_stop_event.is_set():
                        break
                    self._wait_while_paused()
                    if self.timelapse_stop_at and datetime.now() >= self.timelapse_stop_at:
                        self.timelapse_stop_event.set()
                        break
                    time.sleep(0.25)
        except Exception as exc:
            self._log_async(f"Timelapse failed: {exc}")
            self.after(0, lambda: self.update_state("Error"))
        finally:
            self._write_trigger_log_if_needed()
            self.after(0, self._finish_timelapse)

    def _finish_timelapse(self):
        self.timelapse_stop_event.set()
        self.timelapse_pause_event.clear()
        self.pause_button.config(text="Pause")
        self.timelapse_status_var.set("Timelapse: idle")
        self.time_remaining_var.set("Time remaining: -")
        self.current_cycle_var.set("Cycle: -")
        self.current_site_var.set("Site: -")
        if self.app_state != self.STATE_LABELS["Error"]:
            self.update_state("Ready" if self.controller and self.controller.connected else "Idle")
        self.log("Timelapse stopped.")

    def _write_trigger_log_if_needed(self):
        if not self.trigger_log:
            return
        output_dir = self.param_vars["output_path"].get()
        os.makedirs(output_dir, exist_ok=True)
        log_path = os.path.join(output_dir, f"hera_tango_trigger_log_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        with open(log_path, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=["Cycle", "Site", "X", "Y", "Timestamp", "ExportPath", "Status"])
            writer.writeheader()
            writer.writerows(self.trigger_log)
        self._log_async(f"Trigger log saved: {log_path}")

    def _wait_while_paused(self):
        while self.timelapse_pause_event.is_set() and not self.timelapse_stop_event.is_set():
            time.sleep(0.1)

    def _update_time_remaining(self):
        if self.timelapse_thread and self.timelapse_thread.is_alive() and self.timelapse_stop_at:
            remaining = self.timelapse_stop_at - datetime.now()
            seconds = max(int(remaining.total_seconds()), 0)
            self.time_remaining_var.set(f"Time remaining: {seconds / 60:.2f} min")
        elif not (self.timelapse_thread and self.timelapse_thread.is_alive()):
            self.time_remaining_var.set("Time remaining: -")

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
            self._log_async(f"Raw hyperspectral data received: width={width}, height={height}")

            bands = int(self.param_vars["bands"].get())
            binning = self.BINNING_OPTIONS[self.param_vars["binning"].get()]
            data_type = self.DATA_TYPES[self.param_vars["data_type"].get()]

            hypercube_handle = self.controller.get_hypercube(data_handle, data_type, bands, binning)
            cube_width, cube_height, cube_bands, cube_type = self.controller.get_hypercube_info(hypercube_handle)
            self._set_var_async(
                self.hypercube_summary_var,
                f"Cube: {cube_width} x {cube_height}, bands={cube_bands}, type={cube_type}",
            )
            self._log_async(
                f"Hypercube ready: width={cube_width}, height={cube_height}, bands={cube_bands}, dataType={cube_type}"
            )
            previous_handle = self.current_hypercube_handle
            self.current_hypercube_handle = hypercube_handle
            self.current_hypercube_info = {
                "width": cube_width,
                "height": cube_height,
                "bands": cube_bands,
                "data_type": cube_type,
            }
            self.current_hyper_band_cache = {}
            viewer_bound = True
            self.after(
                0,
                lambda: (
                    self.hyper_band_scale.config(to=max(cube_bands - 1, 0)),
                    self.current_hyper_band_index.set(0),
                    self.render_current_hyper_band(),
                ),
            )
            self._log_async("Hyperspectral viewer is ready. Open the Hyperspectral View tab and move the band slider.")
            if previous_handle:
                try:
                    self.controller.release_hypercube(previous_handle)
                except Exception:
                    pass

            self.after(0, lambda: self.update_state("Saving"))
            output_dir = self.param_vars["output_path"].get()
            os.makedirs(output_dir, exist_ok=True)
            export_tag = self.pending_export_tag or self._sanitize_export_tag(time.strftime("hera_hypercube_%Y%m%d_%H%M%S"))
            output_path = os.path.join(output_dir, export_tag)
            description = "Generated by AppHeraTriggerPython0417 using Hera SDK and Tango stage control"
            self.controller.export_hypercube_envi(hypercube_handle, output_path, description)
            hdr_path = self._wait_for_export_files(output_path)
            self.last_export_path = hdr_path
            self._set_var_async(self.last_export_var, f"Last export: {os.path.basename(hdr_path)}")
            self._log_async(f"Exported hypercube and confirmed files: {hdr_path}")
            self.acquisition_success = True
            self.last_acquisition_error = ""
            self.after(0, lambda: self.update_state("Completed"))
        except Exception as exc:
            self.last_acquisition_error = str(exc)
            self.acquisition_success = False
            self._log_async(f"Failed to process hyperspectral data: {exc}")
            if viewer_bound and self.current_hypercube_handle == hypercube_handle:
                self.current_hypercube_handle = None
                self.current_hypercube_info = None
                self.current_hyper_band_cache = {}
                self.after(
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
            self.after(0, lambda: self.update_state("Error"))
        finally:
            if data_handle:
                self.controller.release_hyperspectral_data(data_handle)
            self.acquisition_done_event.set()
            self.processing_lock.release()

    def _log_async(self, message):
        self.after(0, lambda: self.log(message))

    def _set_var_async(self, var, value):
        def setter():
            var.set(value)
            self._draw_live_view_placeholder()
            self.render_current_hyper_band()
        self.after(0, setter)

    def _fit_dimensions(self, src_width, src_height, dest_width, dest_height):
        if src_width <= 0 or src_height <= 0:
            return 1, 1
        scale = min(dest_width / src_width, dest_height / src_height)
        if scale <= 0:
            scale = 1.0
        out_w = max(1, int(src_width * scale))
        out_h = max(1, int(src_height * scale))
        return out_w, out_h

    def _resample_grayscale_nearest(self, src_bytes, src_width, src_height, dst_width, dst_height):
        if (src_width, src_height) == (dst_width, dst_height):
            return src_bytes
        result = bytearray(dst_width * dst_height)
        for y in range(dst_height):
            src_y = min(src_height - 1, int(y * src_height / dst_height))
            src_row = src_y * src_width
            dst_row = y * dst_width
            for x in range(dst_width):
                src_x = min(src_width - 1, int(x * src_width / dst_width))
                result[dst_row + x] = src_bytes[src_row + src_x]
        return bytes(result)

    def _normalize_grayscale_for_display(self, gray_bytes):
        if not gray_bytes:
            return gray_bytes, 0, 0
        min_value = min(gray_bytes)
        max_value = max(gray_bytes)
        if min_value == max_value:
            return gray_bytes, min_value, max_value
        scale = 255.0 / (max_value - min_value)
        normalized = bytes(
            max(0, min(255, int(round((value - min_value) * scale))))
            for value in gray_bytes
        )
        return normalized, min_value, max_value

    def _make_ppm_photo_from_grayscale(self, gray_bytes, src_width, src_height, dest_width, dest_height):
        out_w, out_h = self._fit_dimensions(src_width, src_height, dest_width, dest_height)
        scaled = self._resample_grayscale_nearest(gray_bytes, src_width, src_height, out_w, out_h)
        photo = tk.PhotoImage(width=out_w, height=out_h)
        rows = []
        for row_index in range(out_h):
            start = row_index * out_w
            row = scaled[start:start + out_w]
            rows.append("{" + " ".join(f"#{value:02x}{value:02x}{value:02x}" for value in row) + "}")
        photo.put(" ".join(rows))
        return photo, out_w, out_h

    def _clear_hypercube_viewer(self):
        if self.current_hypercube_handle and self.controller:
            try:
                self.controller.release_hypercube(self.current_hypercube_handle)
            except Exception:
                pass
        self.current_hypercube_handle = None
        self.current_hypercube_info = None
        self.current_hyper_band_cache = {}
        self.current_hyper_band_index.set(0)
        self.current_hyper_band_var.set("Band: -")
        self.current_hyper_wavelength_var.set("Wavelength: -")
        self.hypercube_summary_var.set("Cube: waiting for acquisition")
        self.hyper_photo = None
        if hasattr(self, "hyper_band_scale"):
            self.hyper_band_scale.config(to=0)
        self.after(0, self.render_current_hyper_band)

    def on_hyper_band_changed(self, _value=None):
        self.render_current_hyper_band()

    def step_hyper_band(self, delta):
        if not self.current_hypercube_info:
            self.log("Run an acquisition first so the hyperspectral viewer has bands to browse.")
            return
        max_band_index = max(self.current_hypercube_info["bands"] - 1, 0)
        next_index = min(max(int(self.current_hyper_band_index.get()) + delta, 0), max_band_index)
        self.current_hyper_band_index.set(next_index)
        self.hyper_band_jump_var.set(str(next_index + 1))
        self.render_current_hyper_band()

    def jump_to_hyper_band(self):
        if not self.current_hypercube_info:
            self.log("Run an acquisition first so the hyperspectral viewer has bands to browse.")
            return
        try:
            requested_band = int(self.hyper_band_jump_var.get().strip())
        except ValueError:
            self.log("Enter a whole-number band index to jump.")
            return
        max_band = self.current_hypercube_info["bands"]
        clamped_band = min(max(requested_band, 1), max_band)
        self.current_hyper_band_index.set(clamped_band - 1)
        self.hyper_band_jump_var.set(str(clamped_band))
        self.render_current_hyper_band()

    def on_hyper_mousewheel(self, event):
        if getattr(event, "delta", 0) > 0:
            self.step_hyper_band(1)
        elif getattr(event, "delta", 0) < 0:
            self.step_hyper_band(-1)

    def render_current_hyper_band(self):
        if not hasattr(self, "hyper_view_canvas"):
            return
        if not self.current_hypercube_info or not self.current_hypercube_handle or not self.controller:
            self._draw_hyperspectral_view_placeholder()
            return
        try:
            band_index = min(max(int(self.current_hyper_band_index.get()), 0), self.current_hypercube_info["bands"] - 1)
            self.current_hyper_band_index.set(band_index)
            if band_index not in self.current_hyper_band_cache:
                wavelength, band_values = self.controller.get_hypercube_band_data(
                    self.current_hypercube_handle,
                    band_index,
                    self.current_hypercube_info["width"],
                    self.current_hypercube_info["height"],
                    self.current_hypercube_info["data_type"],
                )
                min_value = min(band_values)
                max_value = max(band_values)
                if math.isclose(min_value, max_value):
                    gray_bytes = bytes([0] * len(band_values))
                else:
                    scale = 255.0 / (max_value - min_value)
                    gray_bytes = bytes(
                        max(0, min(255, int((value - min_value) * scale)))
                        for value in band_values
                    )
                self.current_hyper_band_cache[band_index] = (wavelength, gray_bytes)
                self.log(
                    f"Hyperspectral band {band_index + 1}/{self.current_hypercube_info['bands']} "
                    f"render range: min={min_value:.6f}, max={max_value:.6f}, wavelength={wavelength:.3f}"
                )
            wavelength, gray_bytes = self.current_hyper_band_cache[band_index]
            canvas = self.hyper_view_canvas
            canvas.delete("all")
            width = max(canvas.winfo_width(), 10)
            height = max(canvas.winfo_height(), 10)
            self.hyper_photo, out_w, out_h = self._make_ppm_photo_from_grayscale(
                gray_bytes,
                self.current_hypercube_info["width"],
                self.current_hypercube_info["height"],
                max(width - 16, 1),
                max(height - 16, 1),
            )
            canvas.create_rectangle(0, 0, width, height, fill="#101418", outline="")
            canvas.create_image(width / 2, height / 2, image=self.hyper_photo, anchor="center")
            canvas.create_text(
                12,
                12,
                anchor="nw",
                text=f"{self.current_hypercube_info['width']} x {self.current_hypercube_info['height']}",
                fill="#e7edf5",
                font=("Segoe UI", 9),
            )
            self.current_hyper_band_var.set(f"Band: {band_index + 1} / {self.current_hypercube_info['bands']}")
            self.hyper_band_jump_var.set(str(band_index + 1))
            self.current_hyper_wavelength_var.set(f"Wavelength: {wavelength:.3f}")
        except Exception as exc:
            self.log(f"Failed to render hyperspectral band: {exc}")
            self._draw_hyperspectral_view_placeholder()

    def _draw_live_view_placeholder(self):
        if not hasattr(self, "live_view_canvas"):
            return
        if self.live_photo is not None:
            self._render_live_photo()
            return
        canvas = self.live_view_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        canvas.create_rectangle(0, 0, width, height, fill="#101418", outline="")
        step = 24
        for x in range(0, width, step):
            canvas.create_line(x, 0, x, height, fill="#1b2229")
        for y in range(0, height, step):
            canvas.create_line(0, y, width, y, fill="#1b2229")
        canvas.create_text(width / 2, height / 2 - 14, text="Live View", fill="#e7edf5", font=("Segoe UI Semibold", 14))
        stage_text = self.stage_position_var.get() if hasattr(self, "stage_position_var") else "X: -, Y: -"
        canvas.create_text(width / 2, height / 2 + 12, text=self.live_view_status_var.get(), fill="#9aa6b2", font=("Segoe UI", 10))
        canvas.create_text(width / 2, height / 2 + 34, text=stage_text, fill="#728091", font=("Segoe UI", 10))

    def _render_live_photo(self):
        if not hasattr(self, "live_view_canvas"):
            return
        with self.live_frame_lock:
            frame = self.latest_live_frame
            frame_info = self.live_frame_info
        if not frame:
            with self.live_frame_lock:
                self.live_render_pending = False
            return
        try:
            src_width, src_height, gray_bytes = frame
            canvas = self.live_view_canvas
            width = max(canvas.winfo_width(), 10)
            height = max(canvas.winfo_height(), 10)
            self.live_photo, out_w, out_h = self._make_ppm_photo_from_grayscale(
                gray_bytes,
                src_width,
                src_height,
                max(width - 16, 1),
                max(height - 16, 1),
            )
        except tk.TclError as exc:
            with self.live_frame_lock:
                self.live_render_pending = False
            self.log(f"Live preview render failed: {exc}")
            return
        except Exception as exc:
            with self.live_frame_lock:
                self.live_render_pending = False
            self.log(f"Live preview render failed: {exc}")
            return
        canvas = self.live_view_canvas
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill="#101418", outline="")
        canvas.create_image(width / 2, height / 2, image=self.live_photo, anchor="center")
        if frame_info:
            w, h, bpp = frame_info
            canvas.create_text(
                12,
                12,
                anchor="nw",
                text=f"Live frame: {w} x {h}  |  {bpp}-bit  |  {self.live_pixel_format_name}",
                fill="#e7edf5",
                font=("Segoe UI", 9),
            )
            stage_text = self.stage_position_var.get() if hasattr(self, "stage_position_var") else ""
            if stage_text:
                canvas.create_text(12, 30, anchor="nw", text=stage_text, fill="#9aa6b2", font=("Segoe UI", 9))
        if not self.live_first_frame_rendered:
            self.live_first_frame_rendered = True
            self._set_live_view_status(f"Live view: displaying {self.live_pixel_format_name}")
            self.log("Live preview rendered successfully on the canvas.")
        with self.live_frame_lock:
            self.last_live_render_time = time.time()
            self.live_render_pending = False

    def _draw_hyperspectral_view_placeholder(self):
        if not hasattr(self, "hyper_view_canvas"):
            return
        canvas = self.hyper_view_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        canvas.create_rectangle(0, 0, width, height, fill="#101418", outline="")
        for i, color in enumerate(["#24435b", "#2f6c8f", "#4ea4cf", "#7fd0ff", "#ff8b3d"]):
            x0 = 18 + i * 30
            canvas.create_rectangle(x0, height - 42, x0 + 20, height - 18, fill=color, outline="")
        if self.app_state in {self.STATE_LABELS["Acquiring"], self.STATE_LABELS["WaitingForTrigger"], self.STATE_LABELS["ComputingHypercube"], self.STATE_LABELS["Saving"]}:
            detail_text = "Waiting for the current acquisition and cube computation to finish"
        else:
            detail_text = "Run one acquisition to populate the in-app band viewer"
        canvas.create_text(width / 2, height / 2 - 20, text="Hyperspectral View", fill="#e7edf5", font=("Segoe UI Semibold", 14))
        canvas.create_text(width / 2, height / 2 + 2, text=detail_text, fill="#9aa6b2", font=("Segoe UI", 10))
        export_text = self.last_export_var.get() if hasattr(self, "last_export_var") else "Last export: -"
        canvas.create_text(width / 2, height / 2 + 24, text=self.hypercube_summary_var.get(), fill="#d3dbe5", font=("Segoe UI", 10))
        canvas.create_text(width / 2, height / 2 + 46, text=export_text, fill="#728091", font=("Segoe UI", 10))

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
        self.timelapse_stop_event.set()
        try:
            if self.stage_poll_job:
                self.after_cancel(self.stage_poll_job)
        except Exception:
            pass
        try:
            if self.tango and self.tango.connected:
                try:
                    self.tango.stop_axes()
                except Exception:
                    pass
                self.tango.disconnect()
        except Exception:
            pass
        try:
            if self.controller:
                self._clear_hypercube_viewer()
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
