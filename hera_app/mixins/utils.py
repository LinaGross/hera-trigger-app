import threading
import tkinter as tk


class UtilsMixin:
    def _safe_after(self, delay_ms, callback):
        if getattr(self, "is_closing", False):
            return None

        def guarded_callback():
            if getattr(self, "is_closing", False):
                return
            try:
                exists = self.winfo_exists()
            except (RuntimeError, tk.TclError):
                return
            if not exists:
                return
            try:
                callback()
            except tk.TclError:
                return
            except RuntimeError as exc:
                if "main thread is not in main loop" in str(exc):
                    return
                raise

        try:
            if not self.winfo_exists():
                return None
            return self.after(delay_ms, guarded_callback)
        except (RuntimeError, tk.TclError):
            return None

    def _log_async(self, message, detail=None):
        if getattr(self, "is_closing", False):
            return
        if threading.current_thread() is threading.main_thread():
            self.log(message, detail=detail)
        else:
            self._safe_after(0, lambda: self.log(message, detail=detail))

    def _set_var_async(self, var, value):
        def setter():
            if self.is_closing:
                return
            var.set(value)
        self._safe_after(0, setter)
