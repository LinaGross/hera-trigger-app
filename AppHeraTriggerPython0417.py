import ctypes
import csv
import math
import msvcrt
import os
import re
import socket
import tempfile
import threading
import time
import uuid
import zlib
import tkinter as tk
from base64 import b64encode
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk


@dataclass
class SavedPosition:
    name: str
    x: float
    y: float
    z: float = math.nan


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

    def apply_motion_settings(self, speed_xy, accel_xy, secure_vel_xy, speed_z=None, accel_z=None, secure_vel_z=None):
        self._require_connected()
        cur_vel = self.get_velocity()
        cur_accel = self.get_acceleration()
        cur_sec_vel = self.get_secure_velocity()
        speed_z = speed_xy if speed_z is None else speed_z
        accel_z = accel_xy if accel_z is None else accel_z
        secure_vel_z = secure_vel_xy if secure_vel_z is None else secure_vel_z
        self.check_status(
            self.LSX_SetVel(ctypes.c_int(self.lsid), speed_xy, speed_xy, speed_z, cur_vel[3]),
            "Set velocity",
        )
        self.check_status(
            self.LSX_SetAccel(ctypes.c_int(self.lsid), accel_xy, accel_xy, accel_z, cur_accel[3]),
            "Set acceleration",
        )
        self.check_status(
            self.LSX_SetSecVel(ctypes.c_int(self.lsid), secure_vel_xy, secure_vel_xy, secure_vel_z, cur_sec_vel[3]),
            "Set secure velocity",
        )

    def move_absolute_xy(self, x, y):
        self._require_connected()
        _, _, z, a = self.get_position()
        self.check_status(
            self.LSX_MoveAbs(ctypes.c_int(self.lsid), x, y, z, a, ctypes.c_int(0)),
            "Move absolute XY",
        )

    def move_absolute_a(self, a):
        self._require_connected()
        x, y, z, _ = self.get_position()
        self.check_status(
            self.LSX_MoveAbs(ctypes.c_int(self.lsid), x, y, z, a, ctypes.c_int(0)),
            "Move absolute A",
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


class NISZBridgeController:
    """Shared-folder client for the stable NIS-Z-Bridge sync workflow."""

    DEFAULT_SHARED_ROOT = r"\\sti-nas1.rcp.epfl.ch\bios\bios-raw\backups\visible\cell\Jiayi_bios-raw\Z control shared"

    def __init__(self, shared_root=None):
        self.shared_root = Path(shared_root or self.DEFAULT_SHARED_ROOT)
        self.commands_dir = self.shared_root / "commands"
        self.responses_dir = self.shared_root / "responses"
        self.last_command_path = None
        self.last_response_path = None

    def _decode_response_bytes(self, raw):
        if len(raw) > 1 and raw[1] == 0:
            response = raw.decode("utf-16-le", errors="replace")
        else:
            response = raw.decode("ascii", errors="replace")
        return response.replace("\x00", "").strip()

    def _send_and_wait(self, command_text, timeout_sec=90):
        command_text = command_text.strip()
        valid = (
            command_text in {"GET_Z", "STOP"}
            or command_text.startswith("MOVE_REL ")
            or command_text.startswith("MOVE_ABS ")
        )
        if not valid:
            raise RuntimeError(f"Unsupported NIS Z command: {command_text!r}")

        self.commands_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)

        command_id = f"hera_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        command_path = self.commands_dir / f"{command_id}.txt"
        response_path = self.responses_dir / f"{command_id}.txt"
        tmp_path = command_path.with_suffix(command_path.suffix + ".tmp")
        self.last_command_path = command_path
        self.last_response_path = response_path

        tmp_path.write_text(command_text + "\n", encoding="ascii", newline="\n")
        tmp_path.replace(command_path)

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if response_path.exists():
                try:
                    raw = response_path.read_bytes()
                except PermissionError:
                    time.sleep(0.25)
                    continue
                except OSError:
                    time.sleep(0.25)
                    continue

                response = self._decode_response_bytes(raw)
                try:
                    response_path.unlink()
                except OSError:
                    pass
                return response
            time.sleep(0.25)

        try:
            command_path.unlink()
        except OSError:
            pass
        raise RuntimeError(
            f"Timed out waiting for shared response {response_path}. "
            f"The command was written to {command_path}. "
            "On the NIS PC, make sure nis_z_sync_shared_to_local.py is running and that F4 runs the NIS macro."
        )

    def _parse_z(self, response):
        match = re.match(r"^OK\s+([-+]?\d+\.\d+)\s*$", response)
        if match:
            return float(match.group(1))
        if response.startswith("OK"):
            raise RuntimeError(f"NIS Z bridge returned malformed OK response: {response!r}")
        raise RuntimeError(f"NIS Z bridge error: {response}")

    def get_z(self, timeout_sec=90):
        return self._parse_z(self._send_and_wait("GET_Z", timeout_sec))

    def move_rel(self, dz, timeout_sec=90):
        return self._parse_z(self._send_and_wait(f"MOVE_REL {dz:.6f}", timeout_sec))

    def move_abs(self, z, z_min=None, z_max=None, timeout_sec=90):
        if z_min is None or z_max is None:
            command = f"MOVE_ABS {z:.6f}"
        else:
            command = f"MOVE_ABS {z:.6f} {z_min:.6f} {z_max:.6f}"
        return self._parse_z(self._send_and_wait(command, timeout_sec))

    def stop(self, timeout_sec=30):
        return self._parse_z(self._send_and_wait("STOP", timeout_sec))

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
        self.live_cursor_var = tk.StringVar(value=self._live_cursor_status_text("-"))
        self.pending_export_tag = None
        self.live_photo = None
        self.live_frame_info = None
        self.latest_live_frame = None
        self.live_autocontrast_var = tk.BooleanVar(value=True)
        self.live_show_saturation_var = tk.BooleanVar(value=False)
        self.live_gamma_var = tk.DoubleVar(value=1.0)
        self.live_gamma_label_var = tk.StringVar(value="Gamma 1.0")
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
        self.live_render_interval_sec = 0.20
        self.resume_live_after_acquisition = False
        self.last_applied_roi = None
        self.acquisition_requested_roi = None
        self.live_max_preview_width = 480
        self.live_auth_warning_logged = False
        self.last_live_decode_error = ""
        self.is_closing = False
        self.hyper_photo = None
        self.current_hypercube_handle = None
        self.current_hypercube_info = None
        self.current_hyper_band_cache = {}
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
        self._configure_theme()
        self._build_ui()
        self.refresh_positions_tree()
        self.update_state("Idle")
        self.start_stage_polling()
        self._safe_after(250, self.auto_connect_devices)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _live_cursor_status_text(self, text):
        return f"{text:<48}"[:48]

    def _configure_theme(self):
        palettes = {
            "dark": {
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
                "canvas": "#101418",
                "canvas_grid": "#1b2229",
                "title": "#f3f6fb",
                "button_text": "#e7edf5",
                "accent_text": "#111111",
            },
            "light": {
                "bg": "#eef2f6",
                "panel": "#ffffff",
                "panel_alt": "#e5ebf2",
                "field": "#f7f9fc",
                "border": "#c8d2df",
                "text": "#16202a",
                "muted": "#5c6b79",
                "accent": "#d96f22",
                "accent_soft": "#a9571d",
                "success": "#247a3d",
                "danger": "#ba3030",
                "canvas": "#f4f7fa",
                "canvas_grid": "#dde5ee",
                "title": "#101820",
                "button_text": "#16202a",
                "accent_text": "#ffffff",
            },
        }
        self.theme = palettes.get(self.theme_mode, palettes["dark"])
        self.configure(bg=self.theme["bg"])
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
        if hasattr(self, "theme_button_var"):
            self.theme_button_var.set("Dark Mode" if self.theme_mode == "light" else "Light Mode")
        self._apply_theme_recursive(self)

    def _apply_theme_recursive(self, widget):
        cls = widget.winfo_class()
        try:
            if cls in {"Frame", "Labelframe", "LabelFrame", "Toplevel"}:
                widget.configure(bg=self.theme["panel"], highlightbackground=self.theme["border"], highlightcolor=self.theme["border"])
            elif cls == "Panedwindow":
                widget.configure(bg=self.theme["bg"], sashrelief="flat")
            elif cls == "Canvas":
                widget.configure(bg=self.theme["canvas"], highlightbackground=self.theme["border"], highlightcolor=self.theme["border"])
            elif cls == "Label":
                widget.configure(bg=self.theme["panel"], fg=self.theme["text"])
            elif cls == "Button":
                widget.configure(bg=self.theme["panel_alt"], fg=self.theme["button_text"], activebackground=self.theme["accent"], activeforeground=self.theme["accent_text"], relief="flat", bd=0, padx=10, pady=6, cursor="hand2")
            elif cls == "Entry":
                widget.configure(bg=self.theme["field"], fg=self.theme["text"], insertbackground=self.theme["accent_soft"], relief="flat", bd=6)
            elif cls == "Text":
                widget.configure(bg=self.theme["field"], fg=self.theme["text"], insertbackground=self.theme["accent_soft"], relief="flat", bd=0)
            elif cls == "Checkbutton":
                widget.configure(bg=self.theme["panel"], fg=self.theme["text"], selectcolor=self.theme["field"], activebackground=self.theme["panel"], activeforeground=self.theme["text"])
            elif cls == "Scale":
                widget.configure(bg=self.theme["panel"], fg=self.theme["text"], troughcolor=self.theme["field"], activebackground=self.theme["accent"], highlightthickness=0)
            elif cls == "Menubutton":
                widget.configure(bg=self.theme["panel_alt"], fg=self.theme["button_text"], activebackground=self.theme["accent"], activeforeground=self.theme["accent_text"], relief="flat", bd=0, highlightthickness=0)
                try:
                    widget["menu"].configure(bg=self.theme["panel_alt"], fg=self.theme["text"], activebackground=self.theme["accent"], activeforeground=self.theme["accent_text"])
                except Exception:
                    pass
        except Exception:
            pass

        for child in widget.winfo_children():
            self._apply_theme_recursive(child)

    def toggle_theme_mode(self):
        self.theme_mode = "light" if self.theme_mode == "dark" else "dark"
        self._configure_theme()
        self._draw_live_view_placeholder()
        self.render_current_hyper_band()

    def _build_ui(self):
        shell = tk.Frame(self, bg=self.theme["bg"])
        shell.pack(fill="both", expand=True, padx=14, pady=14)

        toolbar = tk.Frame(shell, bg=self.theme["bg"])
        toolbar.pack(fill="x", pady=(0, 8))
        title = tk.Label(toolbar, text="HERA + Tango Trigger", font=("Segoe UI Semibold", 16), bg=self.theme["bg"], fg=self.theme["title"])
        title.pack(side="left")
        subtitle = tk.Label(toolbar, text="Stage-guided hyperspectral acquisition", font=("Segoe UI", 10), bg=self.theme["bg"], fg=self.theme["muted"])
        subtitle.pack(side="left", padx=(12, 0), pady=(4, 0))
        tk.Button(toolbar, textvariable=self.theme_button_var, command=self.toggle_theme_mode).pack(side="right")

        body = tk.PanedWindow(shell, orient="horizontal", sashwidth=8, sashrelief="flat", bg=self.theme["bg"], bd=0)
        body.pack(fill="both", expand=True)

        left = self._make_scroll_column(body, width=330)
        body.add(left, minsize=260, width=330, stretch="never")

        center = tk.Frame(body, bg=self.theme["bg"], padx=10)
        center.grid_rowconfigure(1, weight=1)
        center.grid_columnconfigure(0, weight=1)
        body.add(center, minsize=560, stretch="always")

        right = self._make_scroll_column(body, width=315)
        body.add(right, minsize=245, width=315, stretch="never")

        self._build_left_controls(left.content)
        self._build_center_workspace(center)
        self._build_right_controls(right.content)

    def _make_scroll_column(self, parent, width):
        outer = tk.Frame(parent, bg=self.theme["bg"])
        scroll = ttk.Scrollbar(outer, orient="vertical")
        scroll.pack(side="right", fill="y")
        canvas = tk.Canvas(outer, bg=self.theme["bg"], highlightthickness=0, width=width, yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.config(command=canvas.yview)
        content = tk.Frame(canvas, bg=self.theme["bg"])
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(window_id, width=e.width))

        def bind_wheel(_event):
            canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        canvas.bind("<Enter>", bind_wheel)
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        outer.content = content
        return outer

    def _param_entry(self, parent, row, label_text, key, default, width=10):
        tk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", pady=2)
        if isinstance(default, int):
            self.param_vars[key] = tk.IntVar(value=default)
        else:
            self.param_vars[key] = tk.DoubleVar(value=default)
        tk.Entry(parent, textvariable=self.param_vars[key], width=width).grid(row=row, column=1, sticky="ew", padx=(6, 0), pady=2)

    def _param_menu(self, parent, row, label_text, key, default, options):
        tk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", pady=2)
        self.param_vars[key] = tk.StringVar(value=default)
        tk.OptionMenu(parent, self.param_vars[key], *list(options)).grid(row=row, column=1, sticky="ew", padx=(6, 0), pady=2)

    def _build_left_controls(self, parent):
        self.param_vars = {}
        self.stage_speed_var = tk.DoubleVar(value=20.0)
        self.stage_dwell_var = tk.DoubleVar(value=0.0)
        self.live_pixel_size_var = tk.DoubleVar(value=1.0)
        self.live_invert_x_var = tk.BooleanVar(value=False)
        self.live_invert_y_var = tk.BooleanVar(value=False)
        self.live_swap_xy_var = tk.BooleanVar(value=False)
        self.position_name_var = tk.StringVar()
        self.selected_name_var = tk.StringVar()
        self.selected_x_var = tk.StringVar()
        self.selected_y_var = tk.StringVar()
        self.selected_z_var = tk.StringVar()
        self.roi_tl_x_var = tk.IntVar(value=0)
        self.roi_tl_y_var = tk.IntVar(value=0)
        self.roi_tr_x_var = tk.IntVar(value=511)
        self.roi_tr_y_var = tk.IntVar(value=0)
        self.roi_br_x_var = tk.IntVar(value=511)
        self.roi_br_y_var = tk.IntVar(value=511)
        self.roi_bl_x_var = tk.IntVar(value=0)
        self.roi_bl_y_var = tk.IntVar(value=511)
        self.roi_area_var = tk.StringVar(value=str(512 * 512))

        status = tk.LabelFrame(parent, text="Status", padx=8, pady=8)
        status.pack(fill="x", pady=(0, 10))
        for text, var in (
            ("License", self.license_var),
            ("Live", self.live_view_status_var),
            ("NIS Z", self.nis_z_status_var),
            ("Site", self.current_site_var),
            ("Cycle", self.current_cycle_var),
            ("Last export", self.last_export_var),
        ):
            row = tk.Frame(status)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{text}:", fg=self.theme["muted"], width=10, anchor="w").pack(side="left")
            tk.Label(row, textvariable=var, anchor="w", wraplength=210, justify="left").pack(side="left", fill="x", expand=True)
        cursor_row = tk.Frame(status)
        cursor_row.pack(fill="x", pady=1)
        tk.Label(cursor_row, text="Cursor:", fg=self.theme["muted"], width=10, anchor="w").pack(side="left")
        tk.Label(
            cursor_row,
            textvariable=self.live_cursor_var,
            anchor="w",
            width=48,
            justify="left",
            font=("Consolas", 9),
        ).pack(side="left", fill="x", expand=True)
        state_row = tk.Frame(status)
        state_row.pack(fill="x", pady=(4, 0))
        self.app_state_var = tk.StringVar(value=self.app_state)
        tk.Label(state_row, text="State:", fg=self.theme["muted"], width=10, anchor="w").pack(side="left")
        self.app_state_label = tk.Label(state_row, textvariable=self.app_state_var, fg="#7ad97a", font=("Segoe UI Semibold", 10))
        self.app_state_label.pack(side="left", fill="x", expand=True)
        btns = tk.Frame(status)
        btns.pack(fill="x", pady=(8, 0))
        tk.Button(btns, text="Preflight", command=self.preflight_check).pack(side="left", padx=(0, 6))
        tk.Button(btns, text="Live Status", command=self.debug_live_status).pack(side="left", padx=(0, 6))
        tk.Button(btns, text="Restart Live", command=self.restart_live_view).pack(side="left")

        exposure = tk.LabelFrame(parent, text="Exposure", padx=8, pady=8)
        exposure.pack(fill="x", pady=(0, 10))
        exposure.grid_columnconfigure(1, weight=1)
        self._param_entry(exposure, 0, "Gain level (0-1):", "gain", 0.5)
        self._param_entry(exposure, 1, "Exposure (ms):", "exposure", 1.0)
        tk.Button(exposure, text="Apply Parameters", command=self.apply_parameters_async).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        roi = tk.LabelFrame(parent, text="Region Of Interest", padx=8, pady=8)
        roi.pack(fill="x", pady=(0, 10))
        roi.grid_columnconfigure(1, weight=1)
        self._param_entry(roi, 0, "ROI X:", "roi_x", 0)
        self._param_entry(roi, 1, "ROI Y:", "roi_y", 0)
        self._param_entry(roi, 2, "ROI Width:", "roi_w", 512)
        self._param_entry(roi, 3, "ROI Height:", "roi_h", 512)
        corners = tk.LabelFrame(roi, text="Corners", padx=6, pady=6)
        corners.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        corner_rows = [
            ("Top left", self.roi_tl_x_var, self.roi_tl_y_var),
            ("Top right", self.roi_tr_x_var, self.roi_tr_y_var),
            ("Bottom right", self.roi_br_x_var, self.roi_br_y_var),
            ("Bottom left", self.roi_bl_x_var, self.roi_bl_y_var),
        ]
        for corner_row, (label, x_var, y_var) in enumerate(corner_rows):
            tk.Label(corners, text=label).grid(row=corner_row, column=0, sticky="w", pady=1)
            tk.Label(corners, text="X").grid(row=corner_row, column=1, sticky="e", padx=(6, 2))
            tk.Entry(corners, textvariable=x_var, width=7).grid(row=corner_row, column=2, sticky="w")
            tk.Label(corners, text="Y").grid(row=corner_row, column=3, sticky="e", padx=(6, 2))
            tk.Entry(corners, textvariable=y_var, width=7).grid(row=corner_row, column=4, sticky="w")
        tk.Button(corners, text="Use Corners", command=self.apply_roi_from_corners).grid(row=4, column=0, columnspan=5, sticky="ew", pady=(6, 0))
        area_row = tk.Frame(roi)
        area_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        tk.Label(area_row, text="Area (px2)").pack(side="left")
        tk.Entry(area_row, textvariable=self.roi_area_var, width=9).pack(side="left", padx=(6, 4))
        tk.Button(area_row, text="Set Area", command=self.apply_roi_from_area).pack(side="left")
        roi_actions = tk.Frame(roi)
        roi_actions.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        tk.Button(roi_actions, text="Use Size", command=self.apply_roi_from_size).pack(side="left", padx=(0, 4))
        tk.Button(roi_actions, textvariable=self.live_roi_button_var, command=self.toggle_live_roi_selection).pack(side="left", padx=(0, 4))
        tk.Button(roi_actions, text="Clear", command=self.clear_live_roi_selection).pack(side="left")
        tk.Label(roi, textvariable=self.live_roi_status_var, fg=self.theme["muted"], wraplength=250, justify="left").grid(row=7, column=0, columnspan=2, sticky="w", pady=(6, 0))

        xyz = tk.LabelFrame(parent, text="XYZ Position", padx=8, pady=8)
        xyz.pack(fill="x", pady=(0, 10))
        xyz.grid_columnconfigure(0, weight=1)
        self.stage_status_var = tk.StringVar(value="Stage: not connected")
        self.stage_version_var = tk.StringVar(value="Controller: -")
        self.stage_position_var = tk.StringVar(value="X: -, Y: -")
        tk.Label(xyz, textvariable=self.stage_status_var, font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        pos_panel = tk.Frame(xyz)
        pos_panel.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.current_x_label = tk.Label(pos_panel, text="X: -")
        self.current_x_label.pack(anchor="w")
        self.current_y_label = tk.Label(pos_panel, text="Y: -")
        self.current_y_label.pack(anchor="w", pady=(4, 0))
        self.current_z_label = tk.Label(pos_panel, textvariable=self.nis_z_current_z_var)
        self.current_z_label.pack(anchor="w", pady=(4, 0))

        map_panel = tk.Frame(xyz)
        map_panel.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        pixel_row = tk.Frame(map_panel)
        pixel_row.pack(fill="x")
        tk.Label(pixel_row, text="Stage units / pixel").pack(side="left")
        tk.Entry(pixel_row, textvariable=self.live_pixel_size_var, width=8).pack(side="left", padx=(6, 0))
        axis_row = tk.Frame(map_panel)
        axis_row.pack(fill="x", pady=(6, 0))
        tk.Checkbutton(axis_row, text="Invert X", variable=self.live_invert_x_var, command=self._update_live_cursor_readout).pack(side="left")
        tk.Checkbutton(axis_row, text="Invert Y", variable=self.live_invert_y_var, command=self._update_live_cursor_readout).pack(side="left", padx=(6, 0))
        tk.Checkbutton(axis_row, text="Swap XY", variable=self.live_swap_xy_var, command=self._update_live_cursor_readout).pack(side="left", padx=(6, 0))

        position_panel = tk.Frame(xyz)
        position_panel.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        tk.Label(position_panel, text="Position name").pack(anchor="w")
        tk.Entry(position_panel, textvariable=self.position_name_var, width=24).pack(fill="x", pady=(2, 6))
        for text, command in (
            ("Add Current Position", self.add_current_position),
            ("Update Selected Position", self.update_selected_position),
            ("Delete Selected Row", self.delete_selected_position),
            ("Reconnect Stage", self.reconnect_stage),
        ):
            tk.Button(position_panel, text=text, command=command).pack(fill="x", pady=2)

        edit_panel = tk.LabelFrame(parent, text="Selected XYZ Site", padx=8, pady=8)
        edit_panel.pack(fill="x", pady=(0, 10))
        tk.Entry(edit_panel, textvariable=self.selected_name_var, width=24).pack(fill="x", pady=(0, 6))
        tk.Button(edit_panel, text="Rename", command=self.rename_selected_position).pack(fill="x", pady=(0, 6))
        coord_row = tk.Frame(edit_panel)
        coord_row.pack(fill="x")
        for label, var in (("X", self.selected_x_var), ("Y", self.selected_y_var), ("Z", self.selected_z_var)):
            tk.Label(coord_row, text=label).pack(side="left")
            tk.Entry(coord_row, textvariable=var, width=8).pack(side="left", padx=(3, 6))
        tk.Button(edit_panel, text="Use Current XYZ", command=self.capture_current_stage_position_into_selected).pack(fill="x", pady=(8, 2))
        tk.Button(edit_panel, text="Save Selected Edits", command=self.apply_selected_position_edits).pack(fill="x", pady=2)
        tk.Button(edit_panel, text="Go To Selected Position", command=self.goto_selected_position).pack(fill="x", pady=2)

        saved = tk.LabelFrame(parent, text="Saved Positions", padx=8, pady=8)
        saved.pack(fill="both", expand=True, pady=(0, 10))
        tree_wrap = tk.Frame(saved)
        tree_wrap.pack(fill="both", expand=True)
        self.positions_tree = ttk.Treeview(tree_wrap, columns=("name", "x", "y", "z"), show="headings", height=8, style="Dark.Treeview")
        for name, label, width, anchor in (
            ("name", "Name", 105, "w"),
            ("x", "X", 58, "e"),
            ("y", "Y", 58, "e"),
            ("z", "Z", 58, "e"),
        ):
            self.positions_tree.heading(name, text=label)
            self.positions_tree.column(name, width=width, anchor=anchor)
        scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.positions_tree.yview)
        self.positions_tree.configure(yscrollcommand=scroll.set)
        self.positions_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.positions_tree.bind("<<TreeviewSelect>>", self.on_position_selected)

        self._build_nis_z_ui(parent)

    def _build_center_workspace(self, parent):
        spectral = tk.LabelFrame(parent, text="Spectral / Hypercube Settings", padx=8, pady=8)
        spectral.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        for col in range(12):
            spectral.grid_columnconfigure(col, weight=1)
        self.param_vars["scan_mode"] = tk.StringVar(value="Medium")
        self.param_vars["trigger_mode"] = tk.StringVar(value="Internal")
        self.param_vars["averages"] = tk.IntVar(value=1)
        self.param_vars["stabilization"] = tk.IntVar(value=0)
        self.param_vars["bands"] = tk.IntVar(value=0)
        self.param_vars["binning"] = tk.StringVar(value="None")
        self.param_vars["data_type"] = tk.StringVar(value="SinglePrecision")
        controls = [
            ("Resolution", "scan_mode", "menu", self.SCAN_MODES.keys(), 8),
            ("Bands", "bands", "entry", None, 5),
            ("Avg", "averages", "menu", ("1", "2", "3"), 3),
            ("Binning", "binning", "menu", self.BINNING_OPTIONS.keys(), 7),
            ("Stabilize ms", "stabilization", "entry", None, 6),
            ("Data", "data_type", "menu", self.DATA_TYPES.keys(), 13),
        ]
        for index, (label, key, kind, options, width) in enumerate(controls):
            col = index * 2
            tk.Label(spectral, text=label).grid(row=0, column=col, sticky="w", padx=(0, 3), pady=2)
            if kind == "menu":
                menu = tk.OptionMenu(spectral, self.param_vars[key], *list(options))
                menu.config(width=width)
                menu.grid(row=0, column=col + 1, sticky="w", padx=(0, 8), pady=2)
            else:
                tk.Entry(spectral, textvariable=self.param_vars[key], width=width).grid(row=0, column=col + 1, sticky="w", padx=(0, 8), pady=2)

        self._build_views_and_log(parent)

    def _build_views_and_log(self, parent):
        views_frame = tk.LabelFrame(parent, text="Live And Hyperspectral Views", padx=8, pady=8)
        views_frame.grid(row=1, column=0, sticky="nsew")
        views_frame.grid_rowconfigure(0, weight=1)
        views_frame.grid_columnconfigure(0, weight=1)
        notebook = ttk.Notebook(views_frame)
        notebook.grid(row=0, column=0, sticky="nsew")

        live_tab = tk.Frame(notebook, bg=self.theme["panel"])
        hyper_tab = tk.Frame(notebook, bg=self.theme["panel"])
        notebook.add(live_tab, text="Live View")
        notebook.add(hyper_tab, text="Hyperspectral View")

        live_controls = tk.Frame(live_tab, bg=self.theme["panel"])
        live_controls.pack(fill="x", padx=8, pady=(8, 4))
        live_display_bar = tk.Frame(live_controls, bg=self.theme["panel"])
        live_display_bar.pack(fill="x")
        tk.Checkbutton(live_display_bar, text="Auto Contrast", variable=self.live_autocontrast_var,
                       command=lambda: self._schedule_live_render(force=True),
                       bg=self.theme["panel"], fg=self.theme["text"], selectcolor=self.theme["field"],
                       activebackground=self.theme["panel"]).pack(side="left", padx=(12, 0))
        tk.Checkbutton(live_display_bar, text="Show Saturation", variable=self.live_show_saturation_var,
                       command=lambda: self._schedule_live_render(force=True),
                       bg=self.theme["panel"], fg=self.theme["text"], selectcolor=self.theme["field"],
                       activebackground=self.theme["panel"]).pack(side="left", padx=(8, 0))
        tk.Label(live_display_bar, textvariable=self.live_gamma_label_var, fg="#9aa6b2", bg=self.theme["panel"]).pack(side="left", padx=(10, 4))
        tk.Scale(live_display_bar, variable=self.live_gamma_var, from_=0.2, to=3.0, resolution=0.1,
                 orient="horizontal", length=110, showvalue=False, command=self.on_live_gamma_change,
                 bg=self.theme["panel"], fg=self.theme["text"], troughcolor=self.theme["field"],
                 highlightthickness=0).pack(side="left")
        tk.Button(live_display_bar, text="Reset Gamma", command=self.reset_live_gamma).pack(side="left", padx=(6, 0))
        tk.Button(live_display_bar, text="Snapshot", command=self.snapshot_live_view).pack(side="left", padx=(8, 0))
        tk.Label(live_display_bar, textvariable=self.live_zoom_label_var, fg="#9aa6b2", bg=self.theme["panel"]).pack(side="left", padx=(12, 4))
        tk.Button(live_display_bar, text="-", width=3, command=lambda: self.zoom_live_view(1 / 1.25)).pack(side="left")
        tk.Button(live_display_bar, text="Fit", command=self.fit_live_view).pack(side="left", padx=(6, 0))
        tk.Button(live_display_bar, text="+", width=3, command=lambda: self.zoom_live_view(1.25)).pack(side="left", padx=(6, 0))
        self.live_view_canvas = tk.Canvas(live_tab, bg=self.theme["canvas"], highlightthickness=0)
        self.live_view_canvas.bind("<Motion>", self.on_live_mouse_move)
        self.live_view_canvas.bind("<Button-1>", self.on_live_mouse_click)
        self.live_view_canvas.bind("<MouseWheel>", self.on_live_mousewheel)
        self.live_view_canvas.bind("<Button-4>", lambda event: self.zoom_live_view(1.25, event))
        self.live_view_canvas.bind("<Button-5>", lambda event: self.zoom_live_view(1 / 1.25, event))
        self.live_view_canvas.bind("<ButtonPress-3>", self.start_live_pan)
        self.live_view_canvas.bind("<B3-Motion>", self.on_live_pan_drag)
        self.live_view_canvas.bind("<ButtonRelease-3>", self.end_live_pan)
        self.live_view_canvas.bind("<Leave>", self.on_live_mouse_leave)
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
            hyper_tab, from_=0, to=0, orient="horizontal", variable=self.current_hyper_band_index,
            command=self.on_hyper_band_changed, showvalue=False, highlightthickness=0, bd=0,
            bg=self.theme["panel"], fg=self.theme["text"], troughcolor=self.theme["panel_alt"],
            activebackground=self.theme["accent"], sliderlength=28, width=18, repeatdelay=150,
            repeatinterval=80, takefocus=1, cursor="hand2",
        )
        self.hyper_band_scale.pack(fill="x", padx=8, pady=(0, 6))
        self.hyper_view_canvas = tk.Canvas(hyper_tab, bg=self.theme["canvas"], highlightthickness=0)
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

        status_frame = tk.LabelFrame(parent, text="Status / Messages", padx=8, pady=8)
        status_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        status_strip = tk.Frame(status_frame)
        status_strip.pack(fill="x", pady=(0, 6))
        tk.Label(status_strip, textvariable=self.timelapse_status_var, font=("Segoe UI Semibold", 10)).pack(side="left")
        ttk.Separator(status_strip, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(status_strip, textvariable=self.time_remaining_var, fg="#9aa6b2").pack(side="left")
        ttk.Separator(status_strip, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(status_strip, textvariable=self.live_view_status_var, fg="#9aa6b2").pack(side="left")
        self.log_text = tk.Text(status_frame, height=7, state="disabled", wrap="word", bg=self.theme["field"], fg=self.theme["text"], insertbackground=self.theme["accent_soft"], relief="flat")
        self.log_text.pack(fill="x", expand=False)

    def _build_right_controls(self, parent):
        acquisition = tk.LabelFrame(parent, text="Acquisition / Timelapse", padx=8, pady=8)
        acquisition.pack(fill="x", pady=(0, 10))
        tk.Button(acquisition, text="Run Selected Site", command=self.manual_trigger_selected_position).pack(fill="x", pady=3)
        tk.Button(acquisition, text="Start Hera Acquisition", command=self.start_acquisition).pack(fill="x", pady=3)
        tk.Button(acquisition, text="Abort Hera Acquisition", command=self.abort_acquisition).pack(fill="x", pady=3)
        ttk.Separator(acquisition, orient="horizontal", style="Dark.TSeparator").pack(fill="x", pady=8)

        self.interval_var = tk.DoubleVar(value=10.0)
        self.stop_after_var = tk.DoubleVar(value=0.0)
        for label, var in (
            ("Trigger", self.param_vars["trigger_mode"]),
            ("Interval (min)", self.interval_var),
            ("Dwell (s)", self.stage_dwell_var),
            ("Stop after (min)", self.stop_after_var),
            ("Speed XY", self.stage_speed_var),
        ):
            row = tk.Frame(acquisition)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, width=15, anchor="w").pack(side="left")
            if label == "Trigger":
                menu = tk.OptionMenu(row, var, *list(self.TRIGGER_MODES.keys()))
                menu.config(width=10)
                menu.pack(side="left")
            else:
                tk.Entry(row, textvariable=var, width=9).pack(side="left")
        tk.Label(acquisition, textvariable=self.timelapse_status_var, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 0))
        tk.Label(acquisition, textvariable=self.time_remaining_var).pack(anchor="w", pady=(2, 8))
        tk.Button(acquisition, text="Start Timelapse", command=self.start_timelapse, bg="#ff8b3d", fg="#111111", activebackground="#ffb37a").pack(fill="x", pady=3)
        self.pause_button = tk.Button(acquisition, text="Pause", command=self.pause_or_resume_timelapse)
        self.pause_button.pack(fill="x", pady=3)
        tk.Button(acquisition, text="Stop Timelapse", command=self.stop_timelapse).pack(fill="x", pady=3)

        saving = tk.LabelFrame(parent, text="Saving", padx=8, pady=8)
        saving.pack(fill="x", pady=(0, 10))
        self.param_vars["output_path"] = tk.StringVar(value=os.path.join(os.path.abspath(os.path.dirname(__file__)), "output"))
        tk.Label(saving, text="Output path").pack(anchor="w")
        tk.Entry(saving, textvariable=self.param_vars["output_path"], width=30).pack(fill="x", pady=(2, 6))
        tk.Button(saving, text="Browse", command=self.browse_output_path).pack(fill="x", pady=(0, 6))
        tk.Label(saving, text="Notes saved in ENVI description").pack(anchor="w", pady=(4, 0))
        tk.Entry(saving, textvariable=self.saving_notes_var, width=30).pack(fill="x", pady=(2, 0))
        ttk.Separator(saving, orient="horizontal").pack(fill="x", pady=8)
        tk.Label(saving, text="HyperLAB").pack(anchor="w")
        tk.Entry(saving, textvariable=self.hyperlab_shortcut_var, width=30).pack(fill="x", pady=(2, 6))
        hyperlab_buttons = tk.Frame(saving)
        hyperlab_buttons.pack(fill="x")
        tk.Button(hyperlab_buttons, text="Browse", command=self.browse_hyperlab_shortcut).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(hyperlab_buttons, text="Open Current", command=self.open_current_in_hyperlab).pack(side="left", fill="x", expand=True, padx=(4, 0))

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
            ("Exposure (ms):", "exposure", 1.0),
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
        tk.Button(actions, text="Apply Parameters", command=self.apply_parameters_async).pack(side="left", padx=(0, 6))
        tk.Button(actions, text="Start Hera Acquisition", command=self.start_acquisition).pack(side="left", padx=6)
        tk.Button(actions, text="Abort Hera Acquisition", command=self.abort_acquisition).pack(side="left", padx=6)

    def _build_tango_ui(self, parent):
        frame = tk.LabelFrame(parent, text="Stage Control", padx=10, pady=10)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        self.stage_speed_var = tk.DoubleVar(value=20.0)
        self.stage_dwell_var = tk.DoubleVar(value=0.0)
        self.live_pixel_size_var = tk.DoubleVar(value=1.0)
        self.live_invert_x_var = tk.BooleanVar(value=False)
        self.live_invert_y_var = tk.BooleanVar(value=False)
        self.live_swap_xy_var = tk.BooleanVar(value=False)
        self.position_name_var = tk.StringVar()
        self.selected_name_var = tk.StringVar()
        self.selected_x_var = tk.StringVar()
        self.selected_y_var = tk.StringVar()
        self.selected_z_var = tk.StringVar()

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

        pos_panel = tk.LabelFrame(frame, text="Current XYZ Position", padx=8, pady=8)
        pos_panel.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        self.current_x_label = tk.Label(pos_panel, text="X: -")
        self.current_x_label.pack(anchor="w")
        self.current_y_label = tk.Label(pos_panel, text="Y: -")
        self.current_y_label.pack(anchor="w", pady=(4, 0))
        self.current_z_label = tk.Label(pos_panel, textvariable=self.nis_z_current_z_var)
        self.current_z_label.pack(anchor="w", pady=(4, 0))

        live_cal_panel = tk.LabelFrame(frame, text="Live Cursor Sample Mapping", padx=8, pady=8)
        live_cal_panel.grid(row=7, column=0, sticky="ew", pady=(10, 0))
        pixel_row = tk.Frame(live_cal_panel)
        pixel_row.pack(fill="x")
        tk.Label(pixel_row, text="Stage units / pixel").pack(side="left")
        tk.Entry(pixel_row, textvariable=self.live_pixel_size_var, width=8).pack(side="left", padx=(6, 0))
        axis_row = tk.Frame(live_cal_panel)
        axis_row.pack(fill="x", pady=(6, 0))
        tk.Checkbutton(axis_row, text="Invert X", variable=self.live_invert_x_var,
                       command=self._update_live_cursor_readout).pack(side="left")
        tk.Checkbutton(axis_row, text="Invert Y", variable=self.live_invert_y_var,
                       command=self._update_live_cursor_readout).pack(side="left", padx=(8, 0))
        tk.Checkbutton(axis_row, text="Swap XY", variable=self.live_swap_xy_var,
                       command=self._update_live_cursor_readout).pack(side="left", padx=(8, 0))

        goto_panel = tk.LabelFrame(frame, text="Go To Saved Position", padx=8, pady=8)
        goto_panel.grid(row=8, column=0, sticky="ew", pady=(10, 0))
        top_row = tk.Frame(goto_panel)
        top_row.pack(fill="x")
        tk.Button(top_row, text="Go", width=6, command=self.goto_selected_position).pack(side="left")
        tk.Label(top_row, text="Select a row in the table, then press Go", wraplength=180, justify="left", fg="#9aa6b2").pack(side="left", padx=8)
        coord_row = tk.Frame(goto_panel)
        coord_row.pack(fill="x", pady=(8, 0))
        tk.Label(coord_row, text="X").pack(side="left")
        tk.Entry(coord_row, textvariable=self.selected_x_var, width=10).pack(side="left", padx=(4, 10))
        tk.Label(coord_row, text="Y").pack(side="left")
        tk.Entry(coord_row, textvariable=self.selected_y_var, width=10).pack(side="left", padx=(4, 10))
        tk.Label(coord_row, text="Z").pack(side="left")
        tk.Entry(coord_row, textvariable=self.selected_z_var, width=10).pack(side="left", padx=(4, 0))
        coord_actions = tk.Frame(goto_panel)
        coord_actions.pack(fill="x", pady=(8, 0))
        tk.Button(coord_actions, text="Use Current XYZ", command=self.capture_current_stage_position_into_selected).pack(side="left", padx=(0, 6))
        tk.Button(coord_actions, text="Save Selected Edits", command=self.apply_selected_position_edits).pack(side="left", padx=6)

        tl = tk.LabelFrame(frame, text="Timelapse Settings", padx=8, pady=8)
        tl.grid(row=9, column=0, sticky="ew", pady=(10, 0))
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

    def _build_nis_z_ui(self, parent):
        frame = tk.LabelFrame(parent, text="NIS Z Bridge", padx=10, pady=10)
        frame.pack(fill="x", pady=(10, 0))

        conn_row = tk.Frame(frame)
        conn_row.pack(fill="x", pady=(0, 6))
        tk.Label(conn_row, text="Shared folder:", fg=self.theme["muted"]).pack(side="left")
        tk.Entry(conn_row, textvariable=self.nis_z_shared_root_var, width=32).pack(side="left", padx=(6, 0))

        z_row = tk.Frame(frame)
        z_row.pack(fill="x", pady=(6, 6))
        tk.Label(z_row, text="NIS Z position:").pack(side="left")
        tk.Label(z_row, textvariable=self.nis_z_current_z_var, fg=self.theme["accent_soft"],
                 font=("Segoe UI Semibold", 10)).pack(side="left", padx=(8, 0))

        btns = tk.Frame(frame)
        btns.pack(fill="x", pady=(0, 8))
        tk.Button(btns, text="GET Z", command=self._nis_z_get).pack(side="left", padx=(0, 6))
        tk.Button(btns, text="STOP Z", command=self._nis_z_stop).pack(side="left")

        rel_frame = tk.LabelFrame(frame, text="Relative Move (um)", padx=8, pady=6)
        rel_frame.pack(fill="x", pady=(0, 8))
        step_row = tk.Frame(rel_frame)
        step_row.pack(fill="x", pady=(0, 4))
        tk.Label(step_row, text="Step (um):").pack(side="left")
        tk.Entry(step_row, textvariable=self.nis_z_step_var, width=8).pack(side="left", padx=(6, 0))
        btn_row = tk.Frame(rel_frame)
        btn_row.pack(fill="x")
        tk.Button(btn_row, text="Move +", command=lambda: self._nis_z_move_step(+1)).pack(side="left", padx=(0, 6))
        tk.Button(btn_row, text="Move -", command=lambda: self._nis_z_move_step(-1)).pack(side="left")

        tol_row = tk.Frame(frame)
        tol_row.pack(fill="x", pady=(0, 4))
        tk.Label(tol_row, text="Z tolerance (um):").pack(side="left")
        tk.Entry(tol_row, textvariable=self.nis_z_tolerance_var, width=6).pack(side="left", padx=(6, 0))
        tk.Label(tol_row, text="(skip move if already within this range)", fg=self.theme["muted"]).pack(side="left", padx=(8, 0))

        timeout_row = tk.Frame(frame)
        timeout_row.pack(fill="x")
        tk.Label(timeout_row, text="Response timeout (s):").pack(side="left")
        tk.Entry(timeout_row, textvariable=self.nis_z_timeout_var, width=5).pack(side="left", padx=(6, 0))

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
        ttk.Separator(state_frame, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(state_frame, textvariable=self.nis_z_status_var, fg=self.theme["accent_soft"]).pack(side="left")

        views_frame = tk.LabelFrame(parent, text="Views", padx=10, pady=10)
        views_frame.pack(fill="both", expand=True, pady=(10, 10))
        notebook = ttk.Notebook(views_frame)
        notebook.pack(fill="both", expand=True)

        live_tab = tk.Frame(notebook, bg=self.theme["panel"])
        hyper_tab = tk.Frame(notebook, bg=self.theme["panel"])
        notebook.add(live_tab, text="Live View")
        notebook.add(hyper_tab, text="Hyperspectral View")

        live_controls = tk.Frame(live_tab, bg=self.theme["panel"])
        live_controls.pack(fill="x", padx=8, pady=(8, 4))

        live_cursor_bar = tk.Frame(live_controls, bg=self.theme["panel"])
        live_cursor_bar.pack(fill="x")
        tk.Label(live_cursor_bar, textvariable=self.live_cursor_var, fg="#e7edf5", bg=self.theme["panel"],
                 font=("Segoe UI Semibold", 10), anchor="w").pack(side="left", fill="x", expand=True)

        live_display_bar = tk.Frame(live_controls, bg=self.theme["panel"])
        live_display_bar.pack(fill="x", pady=(4, 0))
        tk.Checkbutton(live_display_bar, text="Auto Contrast", variable=self.live_autocontrast_var,
                       command=lambda: self._schedule_live_render(force=True),
                       bg=self.theme["panel"], fg=self.theme["text"], selectcolor=self.theme["field"],
                       activebackground=self.theme["panel"]).pack(side="left", padx=(12, 0))
        tk.Checkbutton(live_display_bar, text="Show Saturation", variable=self.live_show_saturation_var,
                       command=lambda: self._schedule_live_render(force=True),
                       bg=self.theme["panel"], fg=self.theme["text"], selectcolor=self.theme["field"],
                       activebackground=self.theme["panel"]).pack(side="left", padx=(8, 0))
        tk.Label(live_display_bar, textvariable=self.live_gamma_label_var, fg="#9aa6b2", bg=self.theme["panel"]).pack(side="left", padx=(10, 4))
        tk.Scale(live_display_bar, variable=self.live_gamma_var, from_=0.2, to=3.0, resolution=0.1,
                 orient="horizontal", length=110, showvalue=False, command=self.on_live_gamma_change,
                 bg=self.theme["panel"], fg=self.theme["text"], troughcolor=self.theme["field"],
                 highlightthickness=0).pack(side="left")
        tk.Button(live_display_bar, text="Reset Gamma", command=self.reset_live_gamma).pack(side="left", padx=(6, 0))
        tk.Button(live_display_bar, text="Snapshot", command=self.snapshot_live_view).pack(side="left", padx=(8, 0))

        live_zoom_bar = tk.Frame(live_controls, bg=self.theme["panel"])
        live_zoom_bar.pack(fill="x", pady=(4, 0))
        tk.Label(live_zoom_bar, textvariable=self.live_zoom_label_var, fg="#9aa6b2", bg=self.theme["panel"]).pack(side="left", padx=(12, 4))
        tk.Button(live_zoom_bar, text="-", width=3, command=lambda: self.zoom_live_view(1 / 1.25)).pack(side="left")
        tk.Button(live_zoom_bar, text="Fit", command=self.fit_live_view).pack(side="left", padx=(6, 0))
        tk.Button(live_zoom_bar, text="+", width=3, command=lambda: self.zoom_live_view(1.25)).pack(side="left", padx=(6, 0))
        tk.Label(live_zoom_bar, text="Mouse wheel zoom; right-drag pan", fg="#728091", bg=self.theme["panel"]).pack(side="left", padx=(10, 0))

        live_roi_bar = tk.Frame(live_controls, bg=self.theme["panel"])
        live_roi_bar.pack(fill="x", pady=(4, 0))
        tk.Button(live_roi_bar, textvariable=self.live_roi_button_var, command=self.toggle_live_roi_selection).pack(side="right")
        tk.Button(live_roi_bar, text="Clear ROI", command=self.clear_live_roi_selection).pack(side="right", padx=(0, 6))
        tk.Label(live_roi_bar, textvariable=self.live_roi_status_var, fg="#9aa6b2", bg=self.theme["panel"],
                 anchor="w").pack(side="left", fill="x", expand=True)
        self.live_view_canvas = tk.Canvas(live_tab, bg="#101418", highlightthickness=0)
        self.live_view_canvas.bind("<Motion>", self.on_live_mouse_move)
        self.live_view_canvas.bind("<Button-1>", self.on_live_mouse_click)
        self.live_view_canvas.bind("<MouseWheel>", self.on_live_mousewheel)
        self.live_view_canvas.bind("<Button-4>", lambda event: self.zoom_live_view(1.25, event))
        self.live_view_canvas.bind("<Button-5>", lambda event: self.zoom_live_view(1 / 1.25, event))
        self.live_view_canvas.bind("<ButtonPress-3>", self.start_live_pan)
        self.live_view_canvas.bind("<B3-Motion>", self.on_live_pan_drag)
        self.live_view_canvas.bind("<ButtonRelease-3>", self.end_live_pan)
        self.live_view_canvas.bind("<Leave>", self.on_live_mouse_leave)
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
        self.positions_tree = ttk.Treeview(center_tree_wrap, columns=("name", "x", "y", "z"), show="headings", height=4, style="Dark.Treeview")
        self.positions_tree.heading("name", text="Name")
        self.positions_tree.heading("x", text="X")
        self.positions_tree.heading("y", text="Y")
        self.positions_tree.heading("z", text="Z")
        self.positions_tree.column("name", width=260, anchor="w")
        self.positions_tree.column("x", width=150, anchor="e")
        self.positions_tree.column("y", width=150, anchor="e")
        self.positions_tree.column("z", width=150, anchor="e")
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

    def browse_hyperlab_shortcut(self):
        file_path = filedialog.askopenfilename(
            title="Select Nireos HyperLAB shortcut or application",
            filetypes=[("Shortcut or executable", "*.lnk *.exe"), ("All files", "*.*")],
        )
        if file_path:
            self.hyperlab_shortcut_var.set(file_path)

    def open_current_in_hyperlab(self):
        hdr_path = self.last_export_path
        if not hdr_path or not os.path.exists(hdr_path):
            messagebox.showinfo("Open in HyperLAB", "No exported hyperspectral cube is available yet.")
            self.log("Open in HyperLAB skipped: no exported hyperspectral cube is available yet.")
            return

        shortcut_path = self.hyperlab_shortcut_var.get().strip()
        if not shortcut_path or not os.path.exists(shortcut_path):
            messagebox.showerror("Open in HyperLAB", f"HyperLAB shortcut not found:\n{shortcut_path}")
            self.log(f"Open in HyperLAB failed: shortcut not found: {shortcut_path}")
            return

        try:
            try:
                os.startfile(shortcut_path, "open", f'"{hdr_path}"')
                self.log(f"Opened current hyperspectral cube in HyperLAB: {hdr_path}")
            except TypeError:
                os.startfile(shortcut_path)
                self._copy_last_export_path_to_clipboard(hdr_path)
                self.log(f"Opened HyperLAB. Last export path copied to clipboard: {hdr_path}")
        except Exception as exc:
            try:
                os.startfile(shortcut_path)
                self._copy_last_export_path_to_clipboard(hdr_path)
                messagebox.showinfo(
                    "Open in HyperLAB",
                    "HyperLAB was opened, but Windows did not accept the cube path automatically. "
                    "The last .hdr path was copied to the clipboard.",
                )
                self.log(f"Opened HyperLAB without file argument. Last export path copied to clipboard: {hdr_path}")
            except Exception as fallback_exc:
                messagebox.showerror("Open in HyperLAB", f"Could not open HyperLAB:\n{fallback_exc}")
                self.log(f"Open in HyperLAB failed: {exc}; fallback failed: {fallback_exc}")

    def _copy_last_export_path_to_clipboard(self, hdr_path):
        self.clipboard_clear()
        self.clipboard_append(hdr_path)

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

    def _find_envi_data_file(self, output_base_path):
        for candidate in (output_base_path, output_base_path + ".raw", output_base_path + ".img", output_base_path + ".dat"):
            if os.path.exists(candidate):
                return candidate
        raise RuntimeError(f"Could not find ENVI data file for {output_base_path}")

    def _read_envi_header_value(self, header_text, key, default=None):
        match = re.search(rf"(?im)^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", header_text)
        if not match:
            return default
        return match.group(1).strip()

    def _replace_envi_header_value(self, header_text, key, value):
        line = f"{key} = {value}"
        pattern = rf"(?im)^\s*{re.escape(key)}\s*=.*$"
        if re.search(pattern, header_text):
            return re.sub(pattern, line, header_text)
        return header_text.rstrip() + "\n" + line + "\n"

    def _crop_exported_envi_to_roi(self, source_base_path, target_base_path, roi, description=None):
        source_hdr_path = source_base_path + ".hdr"
        source_data_path = self._find_envi_data_file(source_base_path)
        target_hdr_path = target_base_path + ".hdr"
        target_data_path = target_base_path

        header_text = Path(source_hdr_path).read_text(encoding="utf-8", errors="replace")
        samples = int(self._read_envi_header_value(header_text, "samples"))
        lines = int(self._read_envi_header_value(header_text, "lines"))
        bands = int(self._read_envi_header_value(header_text, "bands"))
        data_type = int(self._read_envi_header_value(header_text, "data type"))
        interleave = (self._read_envi_header_value(header_text, "interleave", "bsq") or "bsq").lower()
        header_offset = int(self._read_envi_header_value(header_text, "header offset", "0"))
        if interleave != "bsq":
            raise RuntimeError(f"ROI export crop only supports ENVI bsq interleave, not {interleave}.")
        bytes_per_sample = {4: 4, 5: 8}.get(data_type)
        if not bytes_per_sample:
            raise RuntimeError(f"ROI export crop does not support ENVI data type {data_type}.")

        roi_x, roi_y, roi_w, roi_h = roi
        roi_x = max(0, min(int(roi_x), samples - 1))
        roi_y = max(0, min(int(roi_y), lines - 1))
        roi_w = max(1, min(int(roi_w), samples - roi_x))
        roi_h = max(1, min(int(roi_h), lines - roi_y))
        row_bytes = roi_w * bytes_per_sample
        source_row_bytes = samples * bytes_per_sample
        source_band_bytes = samples * lines * bytes_per_sample

        with open(source_data_path, "rb") as source_file, open(target_data_path, "wb") as target_file:
            for band_index in range(bands):
                band_offset = header_offset + band_index * source_band_bytes
                for row in range(roi_y, roi_y + roi_h):
                    source_file.seek(band_offset + row * source_row_bytes + roi_x * bytes_per_sample)
                    target_file.write(source_file.read(row_bytes))

        cropped_header = header_text
        if description:
            safe_description = description.replace("}", ")")
            cropped_header = self._replace_envi_header_value(cropped_header, "description", f"{{{safe_description}}}")
        cropped_header = self._replace_envi_header_value(cropped_header, "samples", str(roi_w))
        cropped_header = self._replace_envi_header_value(cropped_header, "lines", str(roi_h))
        cropped_header = self._replace_envi_header_value(cropped_header, "header offset", "0")
        Path(target_hdr_path).write_text(cropped_header, encoding="utf-8")
        return target_hdr_path

    def _remove_export_files(self, output_base_path):
        for candidate in (
            output_base_path,
            output_base_path + ".hdr",
            output_base_path + ".raw",
            output_base_path + ".img",
            output_base_path + ".dat",
        ):
            try:
                if os.path.exists(candidate):
                    os.remove(candidate)
            except Exception as exc:
                self._log_async(f"Could not remove temporary export file {candidate}: {exc}")

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

    def _get_nis_z_controller(self):
        shared_root = self.nis_z_shared_root_var.get().strip()
        if self.nis_z is None or str(self.nis_z.shared_root) != shared_root:
            self.nis_z = NISZBridgeController(shared_root)
        return self.nis_z

    def _nis_z_get(self):
        if self.nis_z_request_lock.locked():
            self._log_async("NIS Z GET_Z ignored because another Z request is still waiting.")
            self._set_var_async(self.nis_z_status_var, "NIS Z: busy")
            return
        self._log_async("NIS Z GET_Z requested; waiting for NIS bridge response...")
        self._set_var_async(self.nis_z_status_var, "NIS Z: GET_Z waiting")
        def worker():
            with self.nis_z_request_lock:
                try:
                    nis = self._get_nis_z_controller()
                    z = nis.get_z(timeout_sec=int(self.nis_z_timeout_var.get()))
                    self._safe_after(0, lambda: self._set_nis_z_value(z))
                    self._set_var_async(self.nis_z_status_var, f"NIS Z: {z:.3f} um")
                    self._log_async(f"NIS Z GET_Z: {z:.3f} um")
                except Exception as exc:
                    self._log_async(f"NIS Z GET_Z failed: {exc}")
                    self._set_var_async(self.nis_z_status_var, "NIS Z: GET_Z failed")
                    self._safe_after(0, lambda exc=exc: self._set_nis_z_status(f"GET_Z failed: {exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def _nis_z_move_rel(self, dz):
        self._set_var_async(self.nis_z_status_var, f"NIS Z: MOVE_REL {dz:+.3f} waiting")
        def worker():
            with self.nis_z_request_lock:
                try:
                    self._log_async(f"NIS Z: sending MOVE_REL {dz:+.6f}; waiting for NIS macro response...")
                    z = self._get_nis_z_controller().move_rel(dz, timeout_sec=int(self.nis_z_timeout_var.get()))
                    self._safe_after(0, lambda: self._set_nis_z_value(z))
                    self._set_var_async(self.nis_z_status_var, f"NIS Z: {z:.3f} um")
                    self._log_async(f"NIS Z after MOVE_REL: {z:.3f} um")
                except Exception as exc:
                    self._log_async(f"NIS Z MOVE_REL {dz:+.6f} failed: {exc}")
                    self._set_var_async(self.nis_z_status_var, "NIS Z: MOVE_REL failed")
                    self._safe_after(0, lambda: self.nis_z_current_z_var.set("Z: error"))
        threading.Thread(target=worker, daemon=True).start()

    def _nis_z_move_step(self, sign):
        try:
            step = abs(float(self.nis_z_step_var.get()))
        except ValueError:
            self._log_async("NIS Z: invalid step value")
            return
        self._nis_z_move_rel(sign * step)

    def _nis_z_stop(self):
        def worker():
            with self.nis_z_request_lock:
                try:
                    z = self._get_nis_z_controller().stop(timeout_sec=int(self.nis_z_timeout_var.get()))
                    self._safe_after(0, lambda: self._set_nis_z_value(z))
                    self._log_async(f"NIS Z STOP: Z={z:.3f} um")
                except Exception as exc:
                    self._log_async(f"NIS Z STOP failed: {exc}")
        threading.Thread(target=worker, daemon=True).start()

    def apply_stage_motion_settings(self):
        if not self.tango or not self.tango.connected:
            self.log("Connect the stage before applying motion settings.")
            return
        try:
            speed_xy = float(self.stage_speed_var.get())
            if speed_xy <= 0:
                raise RuntimeError("Stage speed must be greater than zero.")
            self.tango.apply_motion_settings(speed_xy=speed_xy, accel_xy=1.0, secure_vel_xy=50.0)
            velocities = self.tango.get_velocity()
            self.log(
                f"Stage motion updated: speedXY={speed_xy:.3f}, "
                f"controllerVel=(X={velocities[0]:.3f}, Y={velocities[1]:.3f})"
            )
        except Exception as exc:
            self.log(f"Failed to apply stage motion settings: {exc}")
            self.update_state("Error")

    def update_stage_position_display(self):
        if self.tango and self.tango.connected:
            try:
                x, y, _, _ = self.tango.get_position()
                self.latest_stage_xy = (x, y)
                z_text = self.nis_z_current_z_var.get() if hasattr(self, "nis_z_current_z_var") else "Z: -"
                self.stage_position_var.set(f"X: {x:.3f}, Y: {y:.3f}, {z_text}")
                self.current_x_label.config(text=f"X: {x:.3f}")
                self.current_y_label.config(text=f"Y: {y:.3f}")
                self._update_live_cursor_readout()
                self._draw_live_view_placeholder()
            except Exception:
                pass
        else:
            self.latest_stage_xy = None

    def start_stage_polling(self):
        self._poll_stage_position()

    def start_nis_z_polling(self):
        self._poll_nis_z_position()

    def _safe_after(self, delay_ms, callback):
        if self.is_closing or not self.winfo_exists():
            return None
        try:
            return self.after(delay_ms, callback)
        except tk.TclError:
            return None

    def _poll_stage_position(self):
        if self.is_closing:
            self.stage_poll_job = None
            return
        self.update_stage_position_display()
        self._update_time_remaining()
        self.stage_poll_job = self._safe_after(250, self._poll_stage_position)

    def _set_nis_z_value(self, z, status="ok"):
        self.nis_z_last_value = z
        self.nis_z_last_status = status
        self.nis_z_current_z_var.set(f"Z: {z:.3f} um")
        self.nis_z_status_var.set(f"NIS Z: {z:.3f} um")
        if hasattr(self, "selected_z_var") and not self.selected_z_var.get().strip():
            self.selected_z_var.set(f"{z:.3f}")
        if self.selected_position_index is not None and 0 <= self.selected_position_index < len(self.positions):
            position = self.positions[self.selected_position_index]
            try:
                saved_z_blank = math.isnan(float(position.z))
            except Exception:
                saved_z_blank = True
            if saved_z_blank:
                position.z = z
                self.refresh_positions_tree()
        self.update_stage_position_display()

    def _set_nis_z_status(self, status):
        self.nis_z_last_status = status
        if self.nis_z_last_value is None:
            self.nis_z_current_z_var.set(f"Z: {status}")
            self.nis_z_status_var.set(f"NIS Z: {status}")

    def _read_nis_z_for_log(self):
        with self.nis_z_request_lock:
            try:
                z = self._get_nis_z_controller().get_z(timeout_sec=int(self.nis_z_timeout_var.get()))
                self._set_var_async(self.nis_z_current_z_var, f"Z: {z:.3f} um")
                self.nis_z_last_value = z
                self.nis_z_last_status = "ok"
                self._safe_after(0, lambda z=z: self._set_nis_z_value(z))
                return z, "ok"
            except Exception as exc:
                self.nis_z_last_status = str(exc)
                return None, str(exc)

    def _poll_nis_z_position(self):
        if self.is_closing:
            self.nis_z_poll_job = None
            return
        if not self.nis_z_poll_inflight and self.nis_z_request_lock.acquire(blocking=False):
            self.nis_z_poll_inflight = True

            def worker():
                try:
                    z = self._get_nis_z_controller().get_z(timeout_sec=3)
                    self._safe_after(0, lambda: self._set_nis_z_value(z))
                except Exception as exc:
                    self._safe_after(0, lambda exc=exc: self._set_nis_z_status(str(exc)))
                finally:
                    self.nis_z_poll_inflight = False
                    self.nis_z_request_lock.release()

            threading.Thread(target=worker, daemon=True).start()
        self.nis_z_poll_job = self._safe_after(self.nis_z_poll_interval_ms, self._poll_nis_z_position)

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

    def check_license_status(self, allow_cached=False):
        if not self.controller:
            self.license_var.set("Unknown")
            return False
        try:
            status, licensed, expiry_license, expiry_cert = self.controller.is_licensed()
        except Exception as exc:
            if allow_cached and self.license_ok_seen:
                self.license_var.set("Licensed")
                self.log(f"License recheck failed after an earlier successful check; continuing: {exc}")
                return True
            self.license_var.set("License check failed")
            self.log(f"License check failed: {exc}")
            return False
        if status != 0:
            if allow_cached and self.license_ok_seen:
                self.license_var.set("Licensed")
                self.log(
                    "License recheck returned an SDK error after an earlier successful check; "
                    f"continuing: {self.controller.get_last_error()}"
                )
                return True
            self.license_var.set("License check failed")
            self.log(self.controller.get_last_error())
            return False
        if licensed:
            self.license_var.set("Licensed")
            self.license_ok_seen = True
            self.log(f"Hera SDK is licensed. License expiry UTC={expiry_license}, certificate expiry UTC={expiry_cert}")
            return True
        if allow_cached and self.license_ok_seen:
            self.license_var.set("Licensed")
            self.log("License recheck reported inactive after an earlier successful check; continuing with acquisition.")
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
            self.live_watchdog_job = self._safe_after(8000, self._check_live_view_started)
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
                self._safe_after(0, self._clear_live_view_frame_state)
                self._safe_after(0, self.start_live_view)
            except Exception as exc:
                self._log_async(f"Live view restart failed: {exc}")
                self._safe_after(0, lambda: self._set_live_view_status("Live view: restart failed"))

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
        self._safe_after(0, self._draw_live_view_placeholder)

    def _clear_live_view_frame_state(self):
        with self.live_frame_lock:
            self.latest_live_frame = None
            self.live_frame_info = None
            self.live_display_rect = None
            self.live_display_frame_size = None
            self.live_cursor_image_xy = None
            self.live_render_pending = False
            self.last_live_render_time = 0.0
        self.live_photo = None
        self.live_cursor_var.set(self._live_cursor_status_text("-"))
        self.live_first_frame_rendered = False
        self.live_auth_warning_logged = False
        self.last_live_decode_error = ""
        if self.live_watchdog_job:
            try:
                self.after_cancel(self.live_watchdog_job)
            except Exception:
                pass
            self.live_watchdog_job = None

    def _set_live_view_status(self, text):
        if self.is_closing:
            return
        if self.live_view_status_var.get() == text:
            return
        if threading.current_thread() is threading.main_thread():
            self.live_view_status_var.set(text)
        else:
            self._safe_after(0, lambda: self.live_view_status_var.set(text))

    def _schedule_live_render(self, force=False):
        if self.is_closing:
            return
        now = time.time()
        with self.live_frame_lock:
            if self.live_render_pending:
                return
            if not force and (now - self.last_live_render_time) < self.live_render_interval_sec:
                return
            self.live_render_pending = True
        self._safe_after(0, self._render_live_photo)

    def on_live_capture_error(self, message):
        self._log_async(f"Live capture error: {message}")

    def on_live_capture_timeout(self, free_buffers):
        if free_buffers <= 1:
            self._log_async(f"Live capture buffer warning: free buffers={free_buffers}")

    def on_live_capture_frame(self, capture_handle):
        try:
            if self.is_closing:
                return
            info = self.controller.get_live_capture_info(capture_handle)
            self.live_auth_warning_logged = False
            self.last_live_decode_error = ""
            if not info["data_ptr"] or self.is_closing:
                return

            width = info["width"]
            height = info["height"]
            bit_depth = info["bit_depth"]
            row_stride = info["row_stride"]
            bits_per_pixel = info["bits_per_pixel"]
            saturation_threshold = info["saturation_threshold"]
            bytes_per_pixel = max(1, (bits_per_pixel + 7) // 8)
            raw_size = row_stride * height
            raw_buffer = ctypes.string_at(info["data_ptr"], raw_size)
            scale = self._live_preview_scale(width)
            display_bytes, disp_width, disp_height, saturation_mask = self._extract_live_preview_bytes(
                raw_buffer,
                width,
                height,
                row_stride,
                bytes_per_pixel,
                bit_depth,
                bits_per_pixel,
                saturation_threshold,
                scale,
            )
            _, preview_min, preview_max = self._normalize_grayscale_for_display(display_bytes)

            with self.live_frame_lock:
                self.live_frame_info = (width, height, bits_per_pixel)
                self.latest_live_frame = (disp_width, disp_height, display_bytes, saturation_mask)
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
            error_text = str(exc)
            if "Invalid authentication code" in error_text:
                if not self.live_auth_warning_logged:
                    self.live_auth_warning_logged = True
                    self._set_live_view_status("Live view: SDK authentication warning")
                    self._log_async(f"Live capture warning: {error_text}")
            elif error_text != self.last_live_decode_error:
                self.last_live_decode_error = error_text
                self._log_async(f"Live frame decode failed: {error_text}")
        finally:
            try:
                self.controller.release_live_capture_result(capture_handle)
            except Exception:
                pass

    def _check_live_view_started(self):
        if self.is_closing:
            self.live_watchdog_job = None
            return
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

    def _read_hera_parameter_settings(self):
        return {
            "gain": float(self.param_vars["gain"].get()),
            "exposure_ms": float(self.param_vars["exposure"].get()),
            "roi_x": int(self.param_vars["roi_x"].get()),
            "roi_y": int(self.param_vars["roi_y"].get()),
            "roi_w": int(self.param_vars["roi_w"].get()),
            "roi_h": int(self.param_vars["roi_h"].get()),
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

    def apply_parameters(self, restart_live=True):
        try:
            settings = self._read_hera_parameter_settings()
        except Exception as exc:
            self.log(f"Failed to read Hera parameters from the UI: {exc}")
            self.update_state("Error")
            return False
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
                self._safe_after(0, self._clear_live_view_frame_state)
                self._safe_after(0, lambda: self._set_live_view_status("Live view: stopped"))
                self._safe_after(0, self._draw_live_view_placeholder)

            gain = settings["gain"]
            exposure_ms = settings["exposure_ms"]
            roi_x = settings["roi_x"]
            roi_y = settings["roi_y"]
            roi_w = settings["roi_w"]
            roi_h = settings["roi_h"]
            scan_mode_name = settings["scan_mode_name"]
            trigger_mode_name = settings["trigger_mode_name"]
            scan_mode = self.SCAN_MODES[scan_mode_name]
            trigger_mode = self.TRIGGER_MODES[trigger_mode_name]
            bands = settings["bands"]

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

    def _arm_and_start_acquisition(self, export_tag=None):
        if not self.controller or not self.controller.connected:
            raise RuntimeError("Connect to Hera before starting acquisition.")
        if not self.check_license_status(allow_cached=True):
            raise RuntimeError("Hera SDK license is not active.")
        if self.controller.is_acquiring():
            raise RuntimeError("The device is already acquiring.")

        live_was_running = self.controller.is_live_capturing()
        self.resume_live_after_acquisition = live_was_running
        self.acquisition_requested_roi = self.selected_export_roi if self.roi_selection_active else None
        if self.acquisition_requested_roi:
            self.log(f"Selected ROI for exported cube: {self.acquisition_requested_roi}")
        else:
            self.log("No ROI selected for export; hyperspectral cube will use the full returned image.")
        if not self.apply_parameters(restart_live=False):
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

    def _move_z_to_position(self, target_z):
        """Move NIS Z to target_z via GET_Z then MOVE_REL. Blocks until confirmed.
        Returns (confirmed_z, status_str). Always called from a worker thread."""
        with self.nis_z_request_lock:
            timeout = int(self.nis_z_timeout_var.get())
            tolerance = float(self.nis_z_tolerance_var.get())
            nis = self._get_nis_z_controller()
            try:
                current_z = nis.get_z(timeout_sec=timeout)
                self._safe_after(0, lambda z=current_z: self._set_nis_z_value(z))
                dz = target_z - current_z
                if abs(dz) <= tolerance:
                    self._log_async(
                        f"NIS Z already at {current_z:.3f} um (target {target_z:.3f} um, within {tolerance} um)."
                    )
                    return current_z, "ok"
                self._log_async(
                    f"NIS Z: moving from {current_z:.3f} to {target_z:.3f} um (delta={dz:+.3f} um)..."
                )
                confirmed_z = nis.move_rel(dz, timeout_sec=timeout)
                self._safe_after(0, lambda z=confirmed_z: self._set_nis_z_value(z))
                self._log_async(f"NIS Z confirmed at {confirmed_z:.3f} um (target {target_z:.3f} um).")
                return confirmed_z, "ok"
            except Exception as exc:
                self._log_async(f"NIS Z move to {target_z:.3f} um failed: {exc}")
                return None, str(exc)

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

        # Move Z after XY is settled. Only proceeds to acquisition once Z is confirmed.
        confirmed_z = None
        z_status = "no Z"
        try:
            target_z = float(position.z)
            if not math.isnan(target_z):
                self._log_async(f"NIS Z: targeting {target_z:.3f} um for {position.name}...")
                confirmed_z, z_status = self._move_z_to_position(target_z)
        except (TypeError, ValueError):
            pass

        self.log(f"Starting Hera acquisition at {position.name}.")
        if cycle_index is None:
            export_tag = self._sanitize_export_tag(f"{position.name}_{time.strftime('%Y%m%d_%H%M%S')}")
        else:
            export_tag = self._sanitize_export_tag(f"cycle_{cycle_index:03d}_{position.name}")
        self._arm_and_start_acquisition(export_tag=export_tag)
        export_path = self._await_acquisition_completion()
        return export_path, confirmed_z, z_status

    def _format_saved_z(self, z):
        if z is None:
            return ""
        try:
            if math.isnan(float(z)):
                return ""
        except Exception:
            return ""
        return f"{float(z):.3f}"

    def _current_cached_z(self):
        return self.nis_z_last_value if self.nis_z_last_value is not None else math.nan

    def _get_current_z_or_nan(self):
        """Read NIS Z synchronously for position saving; return NaN if unavailable."""
        try:
            with self.nis_z_request_lock:
                z = self._get_nis_z_controller().get_z(timeout_sec=int(self.nis_z_timeout_var.get()))
            self._set_nis_z_value(z)
            return z
        except Exception as exc:
            self.log(f"Could not read NIS Z while saving position: {exc}")
            return math.nan

    def _parse_optional_z(self, text):
        text = text.strip()
        return float(text) if text else math.nan

    def add_current_position(self):
        try:
            if not self.tango or not self.tango.connected:
                raise RuntimeError("Connect the stage before saving positions.")
            x, y, _, _ = self.tango.get_position()
            requested_name = self.position_name_var.get().strip() or f"Site_{len(self.positions) + 1}"
            name = self._unique_position_name(requested_name)
            z = self._get_current_z_or_nan()
            self.positions.append(SavedPosition(name, x, y, z))
            self.selected_position_index = len(self.positions) - 1
            self._populate_selected_position_fields(self.positions[self.selected_position_index])
            self.refresh_positions_tree()
            self.position_name_var.set("")
            self.log(f"Added position {name} at X={x:.3f}, Y={y:.3f}, Z={self._format_saved_z(z) or '-'}.")
        except Exception as exc:
            self.log(f"Failed to add position: {exc}")
            self.update_state("Error")

    def refresh_positions_tree(self):
        for item in self.positions_tree.get_children():
            self.positions_tree.delete(item)
        for index, pos in enumerate(self.positions):
            self.positions_tree.insert("", "end", iid=str(index), values=(pos.name, f"{pos.x:.3f}", f"{pos.y:.3f}", self._format_saved_z(pos.z)))
        if self.selected_position_index is None and self.positions:
            self.selected_position_index = 0
        if self.selected_position_index is not None and 0 <= self.selected_position_index < len(self.positions):
            self.positions_tree.selection_set(str(self.selected_position_index))
            self.positions_tree.focus(str(self.selected_position_index))
            self._populate_selected_position_fields(self.positions[self.selected_position_index])
            self.center_stage_summary_var.set(
                f"Selected position: {self.positions[self.selected_position_index].name}  |  "
                f"X={self.positions[self.selected_position_index].x:.3f}  "
                f"Y={self.positions[self.selected_position_index].y:.3f}  "
                f"Z={self._format_saved_z(self.positions[self.selected_position_index].z) or '-'}"
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
            self.center_stage_summary_var.set(f"Selected position: {position.name}  |  X={position.x:.3f}  Y={position.y:.3f}  Z={self._format_saved_z(position.z) or '-'}")
        else:
            self.selected_position_index = None
            self._clear_selected_position_fields()
            self.center_stage_summary_var.set("Selected position: none")

    def _populate_selected_position_fields(self, position):
        self.selected_name_var.set(position.name)
        self.selected_x_var.set(f"{position.x:.3f}")
        self.selected_y_var.set(f"{position.y:.3f}")
        self.selected_z_var.set(self._format_saved_z(position.z))

    def _clear_selected_position_fields(self):
        self.selected_name_var.set("")
        self.selected_x_var.set("")
        self.selected_y_var.set("")
        self.selected_z_var.set("")

    def capture_current_stage_position_into_selected(self):
        try:
            if not self.tango or not self.tango.connected:
                raise RuntimeError("Connect the stage before capturing current XYZ.")
            x, y, _, _ = self.tango.get_position()
            z = self._get_current_z_or_nan()
            if not self.selected_name_var.get().strip():
                default_name = f"Site_{len(self.positions) + 1}" if self.selected_position_index is None else self.positions[self.selected_position_index].name
                self.selected_name_var.set(default_name)
            self.selected_x_var.set(f"{x:.3f}")
            self.selected_y_var.set(f"{y:.3f}")
            self.selected_z_var.set(self._format_saved_z(z))
            self.log(f"Loaded current stage position into editor: X={x:.3f}, Y={y:.3f}, Z={self._format_saved_z(z) or '-'}")
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
            z = self._parse_optional_z(self.selected_z_var.get())
            if self.selected_position_index is None:
                self.positions.append(SavedPosition(name, x, y, z))
                self.selected_position_index = len(self.positions) - 1
                self.log(f"Added new position {name} at X={x:.3f}, Y={y:.3f}, Z={self._format_saved_z(z) or '-'}.")
            else:
                position = self.positions[self.selected_position_index]
                position.name = name
                position.x = x
                position.y = y
                position.z = z
                self.log(f"Saved edits for {name}: X={x:.3f}, Y={y:.3f}, Z={self._format_saved_z(z) or '-'}.")
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
            position.z = self._get_current_z_or_nan()
            self._populate_selected_position_fields(position)
            self.refresh_positions_tree()
            self.log(f"Updated {position.name} to X={x:.3f}, Y={y:.3f}, Z={self._format_saved_z(position.z) or '-'}.")
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
        except Exception as exc:
            self.log(f"Failed to go to selected position: {exc}")
            self.update_state("Error")
            return

        def worker():
            try:
                if not self.tango or not self.tango.connected:
                    raise RuntimeError("Connect the stage before moving.")
                with self.stage_lock:
                    speed_xy = float(self.stage_speed_var.get())
                    if speed_xy <= 0:
                        raise RuntimeError("Stage speed must be greater than zero.")
                    self.tango.apply_motion_settings(speed_xy=speed_xy, accel_xy=1.0, secure_vel_xy=50.0)
                    self._log_async(f"Moving to {position.name}.")
                    self.tango.move_absolute_xy(position.x, position.y)
                    self.tango.wait_for_xy_stop(60000)
                    self._safe_after(0, self.update_stage_position_display)
                try:
                    target_z = float(position.z)
                    if not math.isnan(target_z):
                        self._log_async(f"NIS Z: targeting {target_z:.3f} um for {position.name}...")
                        self._move_z_to_position(target_z)
                except (TypeError, ValueError):
                    pass
                self._log_async(f"Reached {position.name}.")
            except Exception as exc:
                self._log_async(f"Failed to go to selected position: {exc}")
                self._safe_after(0, lambda: self.update_state("Error"))

        threading.Thread(target=worker, daemon=True).start()

    def manual_trigger_selected_position(self):
        try:
            position = self._get_selected_position()
        except Exception as exc:
            self.log(f"Manual site run failed: {exc}")
            self.update_state("Error")
            return

        def worker():
            try:
                export_path, confirmed_z, _ = self.run_stage_site_acquisition(position)
                z_info = f", Z={confirmed_z:.3f} um" if confirmed_z is not None else ""
                self._log_async(f"Manual site run completed for {position.name}{z_info}: {export_path}")
            except Exception as exc:
                self._log_async(f"Manual site run failed: {exc}")
                self._safe_after(0, lambda: self.update_state("Error"))

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

                    export_path, confirmed_z, z_status = self.run_stage_site_acquisition(position, cycle_index=cycle)
                    x, y, _, _ = self.tango.get_position()
                    self.trigger_log.append(
                        {
                            "Cycle": cycle,
                            "Site": position.name,
                            "X": f"{x:.6f}",
                            "Y": f"{y:.6f}",
                            "Z": f"{confirmed_z:.6f}" if confirmed_z is not None else "",
                            "ZStatus": z_status,
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
            self._safe_after(0, lambda: self.update_state("Error"))
        finally:
            self._write_trigger_log_if_needed()
            self._safe_after(0, self._finish_timelapse)

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
            writer = csv.DictWriter(csv_file, fieldnames=["Cycle", "Site", "X", "Y", "Z", "ZStatus", "Timestamp", "ExportPath", "Status"])
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
            if self.is_closing:
                return
            if self.app_state == self.STATE_LABELS["WaitingForTrigger"] and progress > 0:
                self.update_state("Acquiring")
            self.log(f"Acquisition progress: {progress * 100:.1f}%")

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
            self._log_async(f"Raw hyperspectral data received: width={width}, height={height}")
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
                + (f" (ROI x={display_roi[0]}, y={display_roi[1]})" if display_roi else ""),
            )
            self._log_async(
                f"Hypercube ready: width={cube_width}, height={cube_height}, bands={cube_bands}, dataType={cube_type}"
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
            }
            self.current_hyper_band_cache = {}
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
            if previous_handle:
                try:
                    self.controller.release_hypercube(previous_handle)
                except Exception:
                    pass

            self._safe_after(0, lambda: self.update_state("Saving"))
            output_dir = self.param_vars["output_path"].get()
            os.makedirs(output_dir, exist_ok=True)
            export_tag = self.pending_export_tag or self._sanitize_export_tag(time.strftime("hera_hypercube_%Y%m%d_%H%M%S"))
            output_path = os.path.join(output_dir, export_tag)
            description = "Generated by AppHeraTriggerPython0417 using Hera SDK and Tango stage control"
            notes = self.saving_notes_var.get().strip()
            if notes:
                description = f"{description}\nUser notes: {notes}"
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

    def _log_async(self, message):
        self._safe_after(0, lambda: self.log(message))

    def _set_var_async(self, var, value):
        def setter():
            if self.is_closing:
                return
            var.set(value)
            self._draw_live_view_placeholder()
            self.render_current_hyper_band()
        self._safe_after(0, setter)

    def _fit_dimensions(self, src_width, src_height, dest_width, dest_height):
        if src_width <= 0 or src_height <= 0:
            return 1, 1
        scale = min(dest_width / src_width, dest_height / src_height)
        if scale <= 0:
            scale = 1.0
        out_w = max(1, int(src_width * scale))
        out_h = max(1, int(src_height * scale))
        return out_w, out_h

    def _clamp_live_pan(self, canvas_width=None, canvas_height=None, image_width=None, image_height=None):
        if not hasattr(self, "live_view_canvas"):
            return
        canvas = self.live_view_canvas
        canvas_width = max(canvas_width if canvas_width is not None else canvas.winfo_width(), 10)
        canvas_height = max(canvas_height if canvas_height is not None else canvas.winfo_height(), 10)
        image_width = max(image_width if image_width is not None else 1, 1)
        image_height = max(image_height if image_height is not None else 1, 1)

        if image_width <= canvas_width:
            self.live_pan_x = 0.0
        else:
            max_pan_x = (image_width - canvas_width) / 2
            self.live_pan_x = max(-max_pan_x, min(max_pan_x, self.live_pan_x))

        if image_height <= canvas_height:
            self.live_pan_y = 0.0
        else:
            max_pan_y = (image_height - canvas_height) / 2
            self.live_pan_y = max(-max_pan_y, min(max_pan_y, self.live_pan_y))

    def _update_live_zoom_label(self):
        self.live_zoom_label_var.set(f"Zoom {int(round(self.live_zoom_factor * 100))}%")

    def zoom_live_view(self, factor, event=None):
        old_zoom = self.live_zoom_factor
        new_zoom = max(1.0, min(8.0, old_zoom * factor))
        if abs(new_zoom - old_zoom) < 0.001:
            return

        if event is not None:
            with self.live_frame_lock:
                frame = self.latest_live_frame
                rect = self.live_display_rect
            if frame and rect:
                src_width, src_height = frame[0], frame[1]
                canvas = self.live_view_canvas
                canvas_width = max(canvas.winfo_width(), 10)
                canvas_height = max(canvas.winfo_height(), 10)
                old_left, old_top, old_w, old_h = rect
                if old_w > 0 and old_h > 0:
                    rel_x = (event.x - old_left) / old_w
                    rel_y = (event.y - old_top) / old_h
                    base_w, base_h = self._fit_dimensions(src_width, src_height, max(canvas_width - 16, 1), max(canvas_height - 16, 1))
                    new_w = max(1, int(round(base_w * new_zoom)))
                    new_h = max(1, int(round(base_h * new_zoom)))
                    centered_left = (canvas_width - new_w) / 2
                    centered_top = (canvas_height - new_h) / 2
                    self.live_pan_x = event.x - rel_x * new_w - centered_left
                    self.live_pan_y = event.y - rel_y * new_h - centered_top
                    self._clamp_live_pan(canvas_width, canvas_height, new_w, new_h)

        self.live_zoom_factor = new_zoom
        self._update_live_zoom_label()
        self._schedule_live_render(force=True)

    def fit_live_view(self):
        self.live_zoom_factor = 1.0
        self.live_pan_x = 0.0
        self.live_pan_y = 0.0
        self.live_pan_drag_start = None
        self._update_live_zoom_label()
        self._schedule_live_render(force=True)

    def zoom_live_view_to_roi(self, roi_rect=None):
        if not hasattr(self, "live_view_canvas"):
            return
        roi_rect = roi_rect or self.live_roi_rect
        if not roi_rect:
            return
        with self.live_frame_lock:
            frame = self.latest_live_frame
            frame_size = self.live_display_frame_size
        if not frame:
            return
        if frame_size:
            frame_width, frame_height = frame_size
        else:
            frame_width, frame_height = frame[0], frame[1]
        if frame_width <= 0 or frame_height <= 0:
            return

        roi_x, roi_y, roi_w, roi_h = roi_rect
        roi_w = max(1, roi_w)
        roi_h = max(1, roi_h)
        canvas = self.live_view_canvas
        canvas_width = max(canvas.winfo_width(), 10)
        canvas_height = max(canvas.winfo_height(), 10)
        base_w, base_h = self._fit_dimensions(frame[0], frame[1], max(canvas_width - 16, 1), max(canvas_height - 16, 1))
        roi_fit_w = max(1.0, roi_w * base_w / frame_width)
        roi_fit_h = max(1.0, roi_h * base_h / frame_height)
        zoom_x = (canvas_width * 0.82) / roi_fit_w
        zoom_y = (canvas_height * 0.82) / roi_fit_h
        self.live_zoom_factor = max(1.0, min(8.0, zoom_x, zoom_y))

        out_w = max(1, int(round(base_w * self.live_zoom_factor)))
        out_h = max(1, int(round(base_h * self.live_zoom_factor)))
        centered_left = (canvas_width - out_w) / 2
        centered_top = (canvas_height - out_h) / 2
        roi_center_x = roi_x + (roi_w - 1) / 2.0
        roi_center_y = roi_y + (roi_h - 1) / 2.0
        self.live_pan_x = (canvas_width / 2.0) - centered_left - (roi_center_x * out_w / frame_width)
        self.live_pan_y = (canvas_height / 2.0) - centered_top - (roi_center_y * out_h / frame_height)
        self._clamp_live_pan(canvas_width, canvas_height, out_w, out_h)
        self._update_live_zoom_label()
        self._schedule_live_render(force=True)

    def on_live_mousewheel(self, event):
        if event.delta > 0:
            self.zoom_live_view(1.25, event)
        elif event.delta < 0:
            self.zoom_live_view(1 / 1.25, event)

    def start_live_pan(self, event):
        self.live_pan_drag_start = (event.x, event.y, self.live_pan_x, self.live_pan_y)

    def on_live_pan_drag(self, event):
        if not self.live_pan_drag_start:
            return
        start_x, start_y, pan_x, pan_y = self.live_pan_drag_start
        self.live_pan_x = pan_x + event.x - start_x
        self.live_pan_y = pan_y + event.y - start_y
        with self.live_frame_lock:
            rect = self.live_display_rect
        if rect:
            _, _, out_w, out_h = rect
            self._clamp_live_pan(image_width=out_w, image_height=out_h)
        self._schedule_live_render(force=True)

    def end_live_pan(self, _event=None):
        self.live_pan_drag_start = None

    def _live_preview_scale(self, width):
        target_w = min(width, self.live_max_preview_width)
        return max(1, math.ceil(width / target_w))

    def _extract_live_preview_bytes(self, raw_buffer, width, height, row_stride, bytes_per_pixel, bit_depth, bits_per_pixel, saturation_threshold, scale):
        display_width = max(1, math.ceil(width / scale))
        display_height = max(1, math.ceil(height / scale))
        pixel_row_bytes = width * bytes_per_pixel
        raw_view = memoryview(raw_buffer)
        saturation_mask = bytearray(display_width * display_height)
        saturation_threshold = int(saturation_threshold or 0)

        if bytes_per_pixel == 1:
            sampled_rows = []
            dst_index = 0
            for row_index in range(0, height, scale):
                row_start = row_index * row_stride
                row_end = row_start + pixel_row_bytes
                row_samples = raw_view[row_start:row_end:scale]
                sampled_rows.append(bytes(row_samples))
                if saturation_threshold > 0:
                    for sample in row_samples:
                        if sample >= saturation_threshold and dst_index < len(saturation_mask):
                            saturation_mask[dst_index] = 1
                        dst_index += 1
            sampled = b"".join(sampled_rows)
            display_height = max(1, len(sampled) // display_width)
            return sampled, display_width, display_height, bytes(saturation_mask[:len(sampled)])

        if bytes_per_pixel == 2:
            effective_depth = bit_depth if bit_depth > 0 else bits_per_pixel
            shift = max(effective_depth - 8, 0)
            sampled = bytearray(display_width * display_height)
            dst_index = 0
            for row_index in range(0, height, scale):
                row_start = row_index * row_stride
                row_end = row_start + pixel_row_bytes
                row_samples = raw_view[row_start:row_end].cast("H")
                for sample in row_samples[::scale]:
                    if saturation_threshold > 0 and sample >= saturation_threshold:
                        saturation_mask[dst_index] = 1
                    if shift:
                        sample = sample >> shift
                    if sample > 255:
                        sample = 255
                    sampled[dst_index] = sample
                    dst_index += 1
            return bytes(sampled[:dst_index]), display_width, max(1, dst_index // display_width), bytes(saturation_mask[:dst_index])

        effective_depth = bit_depth if bit_depth > 0 else bits_per_pixel
        max_value = float((1 << effective_depth) - 1) if effective_depth > 0 else 65535.0
        sampled = bytearray(display_width * display_height)
        dst_index = 0
        for row_index in range(0, height, scale):
            row_start = row_index * row_stride
            row_end = row_start + pixel_row_bytes
            row_bytes = raw_view[row_start:row_end]
            for column_index in range(0, width, scale):
                src_index = column_index * bytes_per_pixel
                sample = int.from_bytes(row_bytes[src_index:src_index + bytes_per_pixel], "little", signed=False)
                if saturation_threshold > 0 and sample >= saturation_threshold:
                    saturation_mask[dst_index] = 1
                sampled[dst_index] = max(0, min(255, int(round((sample / max_value) * 255.0))))
                dst_index += 1
        return bytes(sampled[:dst_index]), display_width, max(1, dst_index // display_width), bytes(saturation_mask[:dst_index])

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

    def _get_live_display_gamma(self):
        try:
            gamma = float(self.live_gamma_var.get())
        except Exception:
            gamma = 1.0
        return max(0.2, min(3.0, gamma))

    def on_live_gamma_change(self, _value=None):
        gamma = self._get_live_display_gamma()
        self.live_gamma_label_var.set(f"Gamma {gamma:.1f}")
        self._schedule_live_render(force=True)

    def reset_live_gamma(self):
        self.live_gamma_var.set(1.0)
        self.on_live_gamma_change()

    def _apply_live_display_gamma(self, gray_bytes):
        gamma = self._get_live_display_gamma()
        if not gray_bytes or abs(gamma - 1.0) < 0.01:
            return gray_bytes
        inverse_gamma = 1.0 / gamma
        lookup = bytes(
            max(0, min(255, int(round(((value / 255.0) ** inverse_gamma) * 255.0))))
            for value in range(256)
        )
        return gray_bytes.translate(lookup)

    def _prepare_live_display_bytes(self, gray_bytes):
        if self.live_autocontrast_var.get():
            render_bytes, _, _ = self._normalize_grayscale_for_display(gray_bytes)
        else:
            render_bytes = gray_bytes
        return self._apply_live_display_gamma(render_bytes)

    def _grayscale_to_rgb_bytes(self, gray_bytes, src_width, src_height, dest_width, dest_height, saturation_mask=None):
        scaled = self._resample_grayscale_nearest(gray_bytes, src_width, src_height, dest_width, dest_height)
        scaled_mask = None
        if saturation_mask:
            scaled_mask = self._resample_grayscale_nearest(saturation_mask, src_width, src_height, dest_width, dest_height)
        rgb_payload = bytearray(len(scaled) * 3)
        dst_index = 0
        for index, value in enumerate(scaled):
            if scaled_mask and scaled_mask[index]:
                rgb_payload[dst_index] = 255
                rgb_payload[dst_index + 1] = 0
                rgb_payload[dst_index + 2] = 0
            else:
                rgb_payload[dst_index] = value
                rgb_payload[dst_index + 1] = value
                rgb_payload[dst_index + 2] = value
            dst_index += 3
        return bytes(rgb_payload)

    def _make_ppm_photo_from_grayscale(self, gray_bytes, src_width, src_height, dest_width, dest_height, saturation_mask=None):
        out_w, out_h = self._fit_dimensions(src_width, src_height, dest_width, dest_height)
        ppm_payload = self._grayscale_to_rgb_bytes(gray_bytes, src_width, src_height, out_w, out_h, saturation_mask)
        ppm_bytes = f"P6\n{out_w} {out_h}\n255\n".encode("ascii") + bytes(ppm_payload)
        try:
            photo = tk.PhotoImage(data=ppm_bytes, format="PPM")
        except tk.TclError:
            photo = tk.PhotoImage(data=b64encode(ppm_bytes), format="PPM")
        return photo, out_w, out_h

    def _png_chunk(self, chunk_type, payload):
        chunk_name = chunk_type.encode("ascii")
        checksum = zlib.crc32(chunk_name + payload) & 0xFFFFFFFF
        return (
            len(payload).to_bytes(4, "big")
            + chunk_name
            + payload
            + checksum.to_bytes(4, "big")
        )

    def _write_rgb_png(self, path, rgb_payload, width, height):
        row_stride = width * 3
        raw_rows = bytearray((row_stride + 1) * height)
        dst_index = 0
        for row_index in range(height):
            raw_rows[dst_index] = 0
            dst_index += 1
            row_start = row_index * row_stride
            raw_rows[dst_index:dst_index + row_stride] = rgb_payload[row_start:row_start + row_stride]
            dst_index += row_stride

        ihdr = (
            width.to_bytes(4, "big")
            + height.to_bytes(4, "big")
            + bytes([8, 2, 0, 0, 0])
        )
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            + self._png_chunk("IHDR", ihdr)
            + self._png_chunk("IDAT", zlib.compress(bytes(raw_rows)))
            + self._png_chunk("IEND", b"")
        )
        with open(path, "wb") as handle:
            handle.write(png_bytes)

    def snapshot_live_view(self):
        with self.live_frame_lock:
            frame = self.latest_live_frame
        if not frame:
            messagebox.showinfo("Live Snapshot", "Start live view first, then take a snapshot.")
            return

        if len(frame) == 4:
            src_width, src_height, gray_bytes, saturation_mask = frame
        else:
            src_width, src_height, gray_bytes = frame
            saturation_mask = None

        render_bytes = self._prepare_live_display_bytes(gray_bytes)
        render_mask = saturation_mask if self.live_show_saturation_var.get() else None

        default_dir = self.param_vars.get("output_path").get() if "output_path" in self.param_vars else ""
        if not default_dir or not os.path.isdir(default_dir):
            default_dir = os.path.abspath(os.path.dirname(__file__))
        default_name = f"hera_live_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        snapshot_path = filedialog.asksaveasfilename(
            title="Save live snapshot",
            initialdir=default_dir,
            initialfile=default_name,
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
        )
        if not snapshot_path:
            return
        if not os.path.splitext(snapshot_path)[1]:
            snapshot_path += ".png"

        try:
            rgb_payload = self._grayscale_to_rgb_bytes(
                render_bytes,
                src_width,
                src_height,
                src_width,
                src_height,
                render_mask,
            )
            self._write_rgb_png(snapshot_path, rgb_payload, src_width, src_height)
        except Exception as exc:
            messagebox.showerror("Live Snapshot", f"Could not save snapshot:\n{exc}")
            self.log(f"Live snapshot failed: {exc}")
            return

        self.log(f"Live snapshot saved: {snapshot_path}")
        self._set_live_view_status(f"Live snapshot saved: {os.path.basename(snapshot_path)}")

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
        self.acquisition_requested_roi = None
        self.hyper_photo = None
        if hasattr(self, "hyper_band_scale"):
            self.hyper_band_scale.config(to=0)
        self._safe_after(0, self.render_current_hyper_band)

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

    def _crop_hyper_band_values_for_display(self, band_values, source_width, display_roi):
        if not display_roi:
            return band_values
        roi_x, roi_y, roi_w, roi_h = display_roi
        cropped = []
        for row in range(roi_y, roi_y + roi_h):
            start = row * source_width + roi_x
            cropped.extend(band_values[start:start + roi_w])
        return cropped

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
                source_width = self.current_hypercube_info.get("source_width", self.current_hypercube_info["width"])
                source_height = self.current_hypercube_info.get("source_height", self.current_hypercube_info["height"])
                display_roi = self.current_hypercube_info.get("display_roi")
                wavelength, band_values = self.controller.get_hypercube_band_data(
                    self.current_hypercube_handle,
                    band_index,
                    source_width,
                    source_height,
                    self.current_hypercube_info["data_type"],
                )
                band_values = self._crop_hyper_band_values_for_display(band_values, source_width, display_roi)
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
            canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
            canvas.create_image(width / 2, height / 2, image=self.hyper_photo, anchor="center")
            canvas.create_text(
                12,
                12,
                anchor="nw",
                text=f"{self.current_hypercube_info['width']} x {self.current_hypercube_info['height']}",
                fill=self.theme["text"],
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
        with self.live_frame_lock:
            self.live_display_rect = None
            self.live_display_frame_size = None
            self.live_cursor_image_xy = None
        self.live_cursor_var.set(self._live_cursor_status_text("-"))
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
        step = 24
        for x in range(0, width, step):
            canvas.create_line(x, 0, x, height, fill=self.theme["canvas_grid"])
        for y in range(0, height, step):
            canvas.create_line(0, y, width, y, fill=self.theme["canvas_grid"])
        canvas.create_text(width / 2, height / 2 - 14, text="Live View", fill=self.theme["text"], font=("Segoe UI Semibold", 14))
        canvas.create_text(width / 2, height / 2 + 12, text=self.live_view_status_var.get(), fill=self.theme["muted"], font=("Segoe UI", 10))

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
            if len(frame) == 4:
                src_width, src_height, gray_bytes, saturation_mask = frame
            else:
                src_width, src_height, gray_bytes = frame
                saturation_mask = None
            render_bytes = self._prepare_live_display_bytes(gray_bytes)
            render_mask = saturation_mask if self.live_show_saturation_var.get() else None
            canvas = self.live_view_canvas
            width = max(canvas.winfo_width(), 10)
            height = max(canvas.winfo_height(), 10)
            base_w, base_h = self._fit_dimensions(src_width, src_height, max(width - 16, 1), max(height - 16, 1))
            target_w = max(1, int(round(base_w * self.live_zoom_factor)))
            target_h = max(1, int(round(base_h * self.live_zoom_factor)))
            self.live_photo, out_w, out_h = self._make_ppm_photo_from_grayscale(
                render_bytes,
                src_width,
                src_height,
                target_w,
                target_h,
                render_mask,
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
        canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
        self._clamp_live_pan(width, height, out_w, out_h)
        left = (width - out_w) / 2 + self.live_pan_x
        top = (height - out_h) / 2 + self.live_pan_y
        if frame_info:
            frame_width, frame_height, _ = frame_info
        else:
            frame_width, frame_height = src_width, src_height
        with self.live_frame_lock:
            self.live_display_rect = (left, top, out_w, out_h)
            self.live_display_frame_size = (frame_width, frame_height)
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
        self._draw_live_roi_overlay(canvas)
        if not self.live_first_frame_rendered:
            self.live_first_frame_rendered = True
            self._set_live_view_status(f"Live view: displaying {self.live_pixel_format_name}")
            self.log("Live preview rendered successfully on the canvas.")
        with self.live_frame_lock:
            self.last_live_render_time = time.time()
            self.live_render_pending = False

    def _live_event_to_image_xy(self, event):
        with self.live_frame_lock:
            rect = self.live_display_rect
            frame_size = self.live_display_frame_size
        if not rect or not frame_size:
            return None

        left, top, out_w, out_h = rect
        frame_width, frame_height = frame_size
        if out_w <= 0 or out_h <= 0 or frame_width <= 0 or frame_height <= 0:
            return None
        if event.x < left or event.x >= left + out_w or event.y < top or event.y >= top + out_h:
            return None

        image_x = min(max(int((event.x - left) * frame_width / out_w), 0), frame_width - 1)
        image_y = min(max(int((event.y - top) * frame_height / out_h), 0), frame_height - 1)
        return image_x, image_y, frame_width, frame_height

    def on_live_mouse_move(self, event):
        image_pos = self._live_event_to_image_xy(event)
        if not image_pos:
            self.live_cursor_var.set(self._live_cursor_status_text("-"))
            return

        image_x, image_y, frame_width, frame_height = image_pos
        self.live_cursor_image_xy = (image_x, image_y, frame_width, frame_height)
        self._update_live_cursor_readout()

    def on_live_mouse_click(self, event):
        if not self.live_roi_selecting:
            return
        image_pos = self._live_event_to_image_xy(event)
        if not image_pos:
            self.live_roi_status_var.set("ROI: click inside live image")
            return

        image_x, image_y, frame_width, frame_height = image_pos
        self.live_roi_points.append((image_x, image_y))
        if len(self.live_roi_points) == 1:
            self.live_roi_status_var.set(f"ROI: first corner ({image_x}, {image_y}); click opposite corner")
            self._draw_live_view_placeholder()
            return

        (x0, y0), (x1, y1) = self.live_roi_points[:2]
        left = min(x0, x1)
        top = min(y0, y1)
        right = max(x0, x1)
        bottom = max(y0, y1)
        width = min(frame_width - left, right - left + 1)
        height = min(frame_height - top, bottom - top + 1)
        self.live_roi_points = []
        self.live_roi_selecting = False
        self.live_roi_button_var.set("Select ROI")
        self._set_roi_fields(left, top, width, height, update_live=True, selected=True)
        self.log(f"Live ROI selected: x={left}, y={top}, width={width}, height={height}. Press Apply Parameters to send it to Hera.")
        self._draw_live_view_placeholder()

    def on_live_mouse_leave(self, _event=None):
        self.live_cursor_image_xy = None
        self.live_cursor_var.set(self._live_cursor_status_text("-"))

    def toggle_live_roi_selection(self):
        self.live_roi_selecting = not self.live_roi_selecting
        self.live_roi_points = []
        if self.live_roi_selecting:
            self.live_roi_button_var.set("Cancel ROI")
            self.live_roi_status_var.set("ROI: click first corner")
        else:
            self.live_roi_button_var.set("Select ROI")
            self.live_roi_status_var.set("ROI: selection cancelled" if self.live_roi_rect is None else self._format_live_roi_status())
        self._draw_live_view_placeholder()

    def clear_live_roi_selection(self):
        self.live_roi_selecting = False
        self.live_roi_points = []
        self.live_roi_rect = None
        self.roi_selection_active = False
        self.selected_export_roi = None
        self.live_roi_button_var.set("Select ROI")
        with self.live_frame_lock:
            frame_size = self.live_display_frame_size
        if frame_size:
            frame_width, frame_height = frame_size
            self._set_roi_fields(0, 0, frame_width, frame_height, update_live=False, status=f"ROI: full frame {frame_width} x {frame_height}")
            self.fit_live_view()
            self.log(f"Live ROI cleared to full frame: width={frame_width}, height={frame_height}. Press Apply Parameters to send it to Hera.")
        else:
            self.live_roi_status_var.set("ROI: -")
        self._draw_live_view_placeholder()

    def _read_int_var(self, var, name):
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            raise ValueError(f"{name} must be a whole number.")

    def _read_roi_size_fields(self):
        left = self._read_int_var(self.param_vars["roi_x"], "ROI X")
        top = self._read_int_var(self.param_vars["roi_y"], "ROI Y")
        width = max(1, self._read_int_var(self.param_vars["roi_w"], "ROI width"))
        height = max(1, self._read_int_var(self.param_vars["roi_h"], "ROI height"))
        return left, top, width, height

    def _set_roi_fields(self, left, top, width, height, update_live=True, status=None, selected=False):
        left = int(left)
        top = int(top)
        width = max(1, int(width))
        height = max(1, int(height))
        roi_rect = (left, top, width, height)
        self.param_vars["roi_x"].set(left)
        self.param_vars["roi_y"].set(top)
        self.param_vars["roi_w"].set(width)
        self.param_vars["roi_h"].set(height)
        right = left + width - 1
        bottom = top + height - 1
        self.roi_tl_x_var.set(left)
        self.roi_tl_y_var.set(top)
        self.roi_tr_x_var.set(right)
        self.roi_tr_y_var.set(top)
        self.roi_br_x_var.set(right)
        self.roi_br_y_var.set(bottom)
        self.roi_bl_x_var.set(left)
        self.roi_bl_y_var.set(bottom)
        self.roi_area_var.set(str(width * height))
        if update_live:
            self.live_roi_rect = roi_rect
        if selected:
            self.roi_selection_active = True
            self.selected_export_roi = roi_rect
        self.live_roi_status_var.set(status or f"ROI: x={left}, y={top}, w={width}, h={height}, area={width * height}")
        if update_live:
            self.zoom_live_view_to_roi((left, top, width, height))

    def apply_roi_from_size(self):
        try:
            left, top, width, height = self._read_roi_size_fields()
            self._set_roi_fields(left, top, width, height, update_live=True, selected=True)
            self.log(f"ROI size applied: x={left}, y={top}, width={width}, height={height}. Press Apply Parameters to send it to Hera.")
            self._draw_live_view_placeholder()
        except Exception as exc:
            self.live_roi_status_var.set(f"ROI: {exc}")

    def apply_roi_from_corners(self):
        try:
            x0 = self._read_int_var(self.roi_tl_x_var, "Top-left X")
            y0 = self._read_int_var(self.roi_tl_y_var, "Top-left Y")
            x1 = self._read_int_var(self.roi_br_x_var, "Bottom-right X")
            y1 = self._read_int_var(self.roi_br_y_var, "Bottom-right Y")
            left = min(x0, x1)
            top = min(y0, y1)
            width = abs(x1 - x0) + 1
            height = abs(y1 - y0) + 1
            self._set_roi_fields(left, top, width, height, update_live=True, selected=True)
            self.log(f"ROI corners applied from top-left and bottom-right: x={left}, y={top}, width={width}, height={height}. Press Apply Parameters to send it to Hera.")
            self._draw_live_view_placeholder()
        except Exception as exc:
            self.live_roi_status_var.set(f"ROI: {exc}")

    def apply_roi_from_area(self):
        try:
            area = max(1, int(float(self.roi_area_var.get())))
            left, top, width, height = self._read_roi_size_fields()
            center_x = left + (width - 1) / 2.0
            center_y = top + (height - 1) / 2.0
            new_width = max(1, int(round(math.sqrt(area))))
            new_height = max(1, int(math.ceil(area / new_width)))
            new_left = max(0, int(round(center_x - (new_width - 1) / 2.0)))
            new_top = max(0, int(round(center_y - (new_height - 1) / 2.0)))
            self._set_roi_fields(new_left, new_top, new_width, new_height, update_live=True, selected=True)
            self.log(f"ROI area applied as near-square region: area={area}, width={new_width}, height={new_height}. Press Apply Parameters to send it to Hera.")
            self._draw_live_view_placeholder()
        except Exception as exc:
            self.live_roi_status_var.set(f"ROI: {exc}")

    def _format_live_roi_status(self):
        if not self.live_roi_rect:
            return "ROI: -"
        x, y, width, height = self.live_roi_rect
        return f"ROI: x={x}, y={y}, w={width}, h={height}"

    def _image_xy_to_canvas_xy(self, image_x, image_y):
        with self.live_frame_lock:
            rect = self.live_display_rect
            frame_size = self.live_display_frame_size
        if not rect or not frame_size:
            return None
        left, top, out_w, out_h = rect
        frame_width, frame_height = frame_size
        if frame_width <= 0 or frame_height <= 0:
            return None
        canvas_x = left + (image_x + 0.5) * out_w / frame_width
        canvas_y = top + (image_y + 0.5) * out_h / frame_height
        return canvas_x, canvas_y

    def _draw_live_roi_overlay(self, canvas):
        corners = []
        if self.live_roi_rect:
            x, y, width, height = self.live_roi_rect
            corners = [(x, y), (x + width - 1, y + height - 1)]
        elif self.live_roi_selecting and self.live_roi_points:
            x, y = self.live_roi_points[0]
            corners = [(x, y), (x, y)]
        if not corners:
            return

        p0 = self._image_xy_to_canvas_xy(*corners[0])
        p1 = self._image_xy_to_canvas_xy(*corners[1])
        if not p0 or not p1:
            return
        x0, y0 = p0
        x1, y1 = p1
        if abs(x1 - x0) < 2 and abs(y1 - y0) < 2:
            canvas.create_line(x0 - 8, y0, x0 + 8, y0, fill="#7ad97a", width=2)
            canvas.create_line(x0, y0 - 8, x0, y0 + 8, fill="#7ad97a", width=2)
            return
        canvas.create_rectangle(x0, y0, x1, y1, outline="#7ad97a", width=2)

    def _update_live_cursor_readout(self):
        cursor = self.live_cursor_image_xy
        if not cursor:
            self.live_cursor_var.set(self._live_cursor_status_text("-"))
            return

        image_x, image_y, frame_width, frame_height = cursor
        if not self.latest_stage_xy:
            self.live_cursor_var.set(self._live_cursor_status_text("sample: stage not connected"))
            return

        try:
            units_per_pixel = float(self.live_pixel_size_var.get())
        except (tk.TclError, ValueError):
            self.live_cursor_var.set(self._live_cursor_status_text("sample: invalid scale"))
            return

        dx_px = image_x - ((frame_width - 1) / 2.0)
        dy_px = image_y - ((frame_height - 1) / 2.0)
        if self.live_swap_xy_var.get():
            dx_px, dy_px = dy_px, dx_px
        if self.live_invert_x_var.get():
            dx_px = -dx_px
        if self.live_invert_y_var.get():
            dy_px = -dy_px

        stage_x, stage_y = self.latest_stage_xy
        sample_x = stage_x + dx_px * units_per_pixel
        sample_y = stage_y + dy_px * units_per_pixel
        self.live_cursor_var.set(self._live_cursor_status_text(f"sample X={sample_x:.3f}, Y={sample_y:.3f}"))

    def _draw_hyperspectral_view_placeholder(self):
        if not hasattr(self, "hyper_view_canvas"):
            return
        canvas = self.hyper_view_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
        for i, color in enumerate(["#24435b", "#2f6c8f", "#4ea4cf", "#7fd0ff", "#ff8b3d"]):
            x0 = 18 + i * 30
            canvas.create_rectangle(x0, height - 42, x0 + 20, height - 18, fill=color, outline="")
        if self.app_state in {self.STATE_LABELS["Acquiring"], self.STATE_LABELS["WaitingForTrigger"], self.STATE_LABELS["ComputingHypercube"], self.STATE_LABELS["Saving"]}:
            detail_text = "Waiting for the current acquisition and cube computation to finish"
        else:
            detail_text = "Run one acquisition to populate the in-app band viewer"
        canvas.create_text(width / 2, height / 2 - 20, text="Hyperspectral View", fill=self.theme["text"], font=("Segoe UI Semibold", 14))
        canvas.create_text(width / 2, height / 2 + 2, text=detail_text, fill=self.theme["muted"], font=("Segoe UI", 10))
        export_text = self.last_export_var.get() if hasattr(self, "last_export_var") else "Last export: -"
        canvas.create_text(width / 2, height / 2 + 24, text=self.hypercube_summary_var.get(), fill=self.theme["text"], font=("Segoe UI", 10))
        canvas.create_text(width / 2, height / 2 + 46, text=export_text, fill=self.theme["muted"], font=("Segoe UI", 10))

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
                        self.controller.release_hypercube(self.current_hypercube_handle)
                    except Exception:
                        pass
                    self.current_hypercube_handle = None
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


if __name__ == "__main__":
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

