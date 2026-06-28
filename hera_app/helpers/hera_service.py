import argparse
import json
import sys
import time
import traceback

from hera_app.controllers import HeraController
from hera_app.helpers.acquisition_helper import run_request


def decode_device_info(device):
    return {
        "id": device.Id.decode("utf-8", errors="ignore"),
        "product": device.ProductName.decode("utf-8", errors="ignore"),
        "serial": device.SerialNumber.decode("utf-8", errors="ignore"),
        "vendor": device.Vendor.decode("utf-8", errors="ignore"),
    }


def write_json(message):
    print(json.dumps(message, separators=(",", ":")), flush=True)


class HeraService:
    def __init__(self):
        self.controller = None
        self.device_index = None
        self.device_info = None
        self.dll_path = None
        self.started_at = time.time()

    def handle(self, request):
        command = request.get("command")
        if command == "connect":
            return self.connect(request)
        if command == "status":
            return self.status()
        if command == "disconnect":
            return self.disconnect()
        if command == "acquire":
            return self.acquire(request)
        if command == "shutdown":
            result = self.disconnect()
            result["shutdown"] = True
            return result
        raise RuntimeError(f"Unknown helper service command: {command!r}")

    def emit_request_event(self, request_id, event, **payload):
        write_json({"id": request_id, "event": event, **payload})

    def connect(self, request):
        if self.controller and self.controller.connected:
            status = self.status()
            status["already_connected"] = True
            return status

        self.dll_path = request.get("dll_path") or HeraController.default_dll_path()
        self.device_index = int(request.get("device_index", 0))
        controller = HeraController(dll_path=self.dll_path)
        devices = controller.enumerate_devices()
        if not devices:
            raise RuntimeError("No Hera devices found.")
        if self.device_index < 0 or self.device_index >= len(devices):
            raise RuntimeError(f"Requested Hera device index {self.device_index} is not available.")

        controller.create_device(devices[self.device_index])
        controller.connect()
        self.controller = controller
        self.device_info = decode_device_info(devices[self.device_index])
        licensed_status, licensed, expiry_license, expiry_cert = controller.is_licensed()
        hdr_supported = False
        hdr = None
        try:
            hdr_supported = controller.is_hdr_supported()
            hdr = controller.get_hdr() if hdr_supported else None
        except Exception:
            hdr_supported = False
            hdr = None
        roi = None
        try:
            roi = controller.get_roi()
        except Exception:
            roi = None
        return {
            "connected": True,
            "device_index": self.device_index,
            "device": self.device_info,
            "licensed": bool(licensed) if licensed_status == 0 else False,
            "license_status": int(licensed_status),
            "license_expiry_utc": int(expiry_license),
            "certificate_expiry_utc": int(expiry_cert),
            "hdr_supported": hdr_supported,
            "hdr": hdr,
            "roi": roi,
        }

    def status(self):
        if not self.controller:
            return {
                "connected": False,
                "device_index": self.device_index,
                "device": self.device_info,
                "uptime_sec": time.time() - self.started_at,
            }

        connected = False
        acquiring = False
        live_capturing = False
        hdr_supported = False
        hdr = None
        roi = None
        try:
            connected = self.controller.is_connected()
        except Exception:
            connected = False
        if connected:
            try:
                acquiring = self.controller.is_acquiring()
            except Exception:
                acquiring = False
            try:
                live_capturing = self.controller.is_live_capturing()
            except Exception:
                live_capturing = False
            try:
                hdr_supported = self.controller.is_hdr_supported()
                hdr = self.controller.get_hdr() if hdr_supported else None
            except Exception:
                hdr_supported = False
                hdr = None
            try:
                roi = self.controller.get_roi()
            except Exception:
                roi = None

        return {
            "connected": connected,
            "device_index": self.device_index,
            "device": self.device_info,
            "acquiring": acquiring,
            "live_capturing": live_capturing,
            "hdr_supported": hdr_supported,
            "hdr": hdr,
            "roi": roi,
            "uptime_sec": time.time() - self.started_at,
        }

    def disconnect(self):
        if not self.controller:
            return {"connected": False, "released": False, "already_released": True}
        errors = []
        try:
            if self.controller.connected:
                self.controller.disconnect()
        except Exception as exc:
            errors.append(f"disconnect: {exc}")
        try:
            self.controller.release_device()
        except Exception as exc:
            errors.append(f"release_device: {exc}")
        self.controller = None
        return {"connected": False, "released": True, "errors": errors}

    def acquire(self, request):
        if self.controller:
            self.disconnect()
        request_id = request.get("id")
        acquisition_request = dict(request)
        acquisition_request.setdefault("dll_path", HeraController.default_dll_path())
        acquisition_request.setdefault("device_index", 0)
        acquisition_request.setdefault("request_id", request_id or f"service_{int(time.time())}")

        def emit_func(event, **payload):
            self.emit_request_event(request_id, event, **payload)

        emit_func(
            "log",
            message=(
                "Helper service starting "
                f"{acquisition_request.get('role', 'sample')} acquisition "
                f"with ROI {acquisition_request.get('roi')}."
            ),
        )
        result = run_request(acquisition_request, emit_func=emit_func)
        emit_func("log", message="Helper service acquisition finished and released the Hera device.")
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Long-lived Hera helper service using JSON-lines stdin/stdout.")
    parser.add_argument("--ready", action="store_true", help="Emit a ready event when the service starts.")
    args = parser.parse_args(argv)

    service = HeraService()
    if args.ready:
        write_json({"event": "ready", "ok": True})

    should_exit = False
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id")
            result = service.handle(request)
            write_json({"id": request_id, "ok": True, "result": result})
            if request.get("command") == "shutdown":
                should_exit = True
        except Exception as exc:
            write_json(
                {
                    "id": request_id,
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        if should_exit:
            break

    if not should_exit:
        try:
            service.disconnect()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
