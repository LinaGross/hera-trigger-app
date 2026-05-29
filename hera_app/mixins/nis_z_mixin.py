import threading

from hera_app.controllers import NISZBridgeController


class NISZMixin:
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

    def _set_nis_z_value(self, z, status="ok"):
        import math
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
                    z = self._get_nis_z_controller().get_z(timeout_sec=15)
                    self._safe_after(0, lambda: self._set_nis_z_value(z))
                except Exception as exc:
                    self._safe_after(0, lambda exc=exc: self._set_nis_z_status(str(exc)))
                finally:
                    self.nis_z_poll_inflight = False
                    self.nis_z_request_lock.release()

            threading.Thread(target=worker, daemon=True).start()
        self.nis_z_poll_job = self._safe_after(self.nis_z_poll_interval_ms, self._poll_nis_z_position)

    def start_nis_z_polling(self):
        self._poll_nis_z_position()
