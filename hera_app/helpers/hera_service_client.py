import json
import os
import queue
import subprocess
import sys
import threading
import time


class HeraServiceClient:
    def __init__(self, python_executable=None, project_root=None, log_func=None):
        self.python_executable = python_executable or sys.executable
        self.project_root = project_root or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        self.log_func = log_func
        self.process = None
        self._messages = queue.Queue()
        self._request_lock = threading.Lock()
        self._reader_thread = None
        self._stderr_thread = None

    def _log(self, message):
        if callable(self.log_func):
            self.log_func(message)

    def start(self, timeout_sec=8.0):
        if self.is_running():
            return
        command = [self.python_executable, "-m", "hera_app.helpers.hera_service", "--ready"]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            command,
            cwd=self.project_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_thread.start()
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"Hera helper service exited early with code {self.process.returncode}.")
            try:
                message = self._messages.get(timeout=0.1)
            except queue.Empty:
                continue
            if message.get("event") == "ready" and message.get("ok"):
                return
        self.kill()
        raise RuntimeError("Timed out waiting for Hera helper service to become ready.")

    def _read_stdout(self):
        try:
            for line in self.process.stdout:
                text = line.strip()
                if not text:
                    continue
                try:
                    self._messages.put(json.loads(text))
                except json.JSONDecodeError:
                    self._messages.put({"event": "stdout", "text": text})
        finally:
            self._messages.put({"event": "closed"})

    def _read_stderr(self):
        try:
            for line in self.process.stderr:
                text = line.strip()
                if text:
                    self._log(f"Hera helper stderr: {text}")
        except Exception:
            pass

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def request(self, command, timeout_sec=15.0, event_callback=None, **payload):
        if not self.is_running():
            self.start()
        request_id = f"{command}_{time.time():.6f}"
        request = {"id": request_id, "command": command, **payload}
        with self._request_lock:
            self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
            deadline = time.monotonic() + timeout_sec
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(f"Hera helper service exited with code {self.process.returncode}.")
                try:
                    message = self._messages.get(timeout=0.1)
                except queue.Empty:
                    continue
                if message.get("event") in {"ready", "closed"}:
                    continue
                if message.get("event") == "stdout":
                    self._log(f"Hera helper output: {message.get('text')}")
                    continue
                if message.get("id") != request_id:
                    self._messages.put(message)
                    time.sleep(0.02)
                    continue
                if message.get("event") in {"log", "progress"}:
                    if callable(event_callback):
                        event_callback(message)
                    elif message.get("event") == "log":
                        self._log(message.get("message", "Hera helper log message"))
                    continue
                if not message.get("ok"):
                    raise RuntimeError(message.get("error") or "Hera helper service command failed.")
                return message.get("result")
        raise RuntimeError(f"Timed out waiting for Hera helper service response to {command!r}.")

    def shutdown(self, timeout_sec=8.0):
        if not self.is_running():
            self.process = None
            return None
        try:
            result = self.request("shutdown", timeout_sec=timeout_sec)
            try:
                self.process.wait(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                self.kill()
            return result
        finally:
            self.process = None

    def kill(self):
        if self.process and self.process.poll() is None:
            self.process.kill()
            try:
                self.process.wait(timeout=3)
            except Exception:
                pass
        self.process = None
