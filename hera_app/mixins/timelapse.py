import csv
import math
import os
import threading
import time
from datetime import datetime, timedelta


class TimelapseStopped(Exception):
    pass


class TimelapseMixin:
    def _begin_timelapse_run(self):
        self.timelapse_run_id += 1
        return self.timelapse_run_id

    def _roi_plan_message(self, label, positions):
        positions = list(positions)
        saved_count = sum(1 for position in positions if self._get_position_roi(position))
        total = len(positions)
        fallback_roi = self._normalize_roi_tuple(self.timelapse_roi)
        if saved_count and fallback_roi:
            return (
                f"{label}. Using saved ROI for {saved_count}/{total} site(s); "
                f"current ROI fallback for sites without saved ROI: {self._format_roi(fallback_roi)}."
            )
        if saved_count:
            return f"{label}. Using saved ROI for {saved_count}/{total} site(s); sites without ROI acquire full frame."
        if fallback_roi:
            return f"{label}. No per-site ROI saved; using current ROI for all sites: {self._format_roi(fallback_roi)}."
        return f"{label}. No ROI active; all sites will acquire the full frame."

    def run_one_cycle(self):
        if self.timelapse_thread and self.timelapse_thread.is_alive():
            self.log("Timelapse is already running.")
            return
        if not self.positions:
            self.log("Add at least one stage position before running a cycle.")
            return
        if not self._validate_auto_save_export_options():
            return

        self.timelapse_stop_event.clear()
        self.timelapse_pause_event.clear()
        self.trigger_log = []
        self.timelapse_started_at = datetime.now()
        self.timelapse_stop_at = None
        self.timelapse_roi = self._get_active_roi()
        self.pause_button.config(text="Pause")
        self.timelapse_status_var.set("Timelapse: running")
        self.update_state("RunningTimelapse")

        run_id = self._begin_timelapse_run()
        self.timelapse_thread = threading.Thread(target=self._timelapse_worker, args=(True, None, run_id), daemon=True)
        self.timelapse_thread.start()
        self.log(self._roi_plan_message("Running one cycle", self.positions))
        self.log(f"Auto-save products: {self._export_selection_text()}.")

    def _first_two_test_sites(self):
        positions = list(self.positions)
        real_sites = [position for position in positions if position.name.strip().lower() != "start"]
        return (real_sites or positions)[:2]

    def run_first_two_sites(self):
        if self.timelapse_thread and self.timelapse_thread.is_alive():
            self.log("Timelapse is already running.")
            return
        test_sites = self._first_two_test_sites()
        if not test_sites:
            self.log("Add at least one stage position before running a two-site test.")
            return
        if not self._validate_auto_save_export_options():
            return

        self.timelapse_stop_event.clear()
        self.timelapse_pause_event.clear()
        self.trigger_log = []
        self.timelapse_started_at = datetime.now()
        self.timelapse_stop_at = None
        self.timelapse_roi = self._get_active_roi()
        self.pause_button.config(text="Pause")
        self.timelapse_status_var.set("Timelapse: running")
        self.update_state("RunningTimelapse")

        run_id = self._begin_timelapse_run()
        self.timelapse_thread = threading.Thread(
            target=self._timelapse_worker,
            args=(True, test_sites, run_id),
            daemon=True,
        )
        self.timelapse_thread.start()
        site_names = ", ".join(position.name for position in test_sites)
        self.log(self._roi_plan_message(f"Running first {len(test_sites)} site(s): {site_names}", test_sites))
        self.log(f"Auto-save products: {self._export_selection_text()}.")

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
        if not self._validate_auto_save_export_options():
            return

        self.timelapse_stop_event.clear()
        self.timelapse_pause_event.clear()
        self.trigger_log = []
        self.timelapse_started_at = datetime.now()
        stop_after = float(self.stop_after_var.get())
        self.timelapse_stop_at = self.timelapse_started_at + timedelta(minutes=stop_after) if stop_after > 0 else None
        self.timelapse_roi = self._get_active_roi()
        self.pause_button.config(text="Pause")
        self.timelapse_status_var.set("Timelapse: running")
        self.update_state("RunningTimelapse")

        run_id = self._begin_timelapse_run()
        self.timelapse_thread = threading.Thread(target=self._timelapse_worker, args=(False, None, run_id), daemon=True)
        self.timelapse_thread.start()
        self.log(self._roi_plan_message("Timelapse started", self.positions))
        self.log(f"Auto-save products: {self._export_selection_text()}.")

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
        if not self.timelapse_thread or not self.timelapse_thread.is_alive():
            self.timelapse_stop_event.set()
            self.timelapse_pause_event.clear()
            self.timelapse_roi = None
            self.pause_button.config(text="Pause")
            self.timelapse_status_var.set("Timelapse: idle")
            self.log("Timelapse is not running.")
            return

        self.timelapse_stop_event.set()
        self.timelapse_pause_event.clear()
        self.timelapse_roi = None
        self.pause_button.config(text="Pause")
        self.timelapse_status_var.set("Timelapse: stopping")
        self._abort_current_timelapse_acquisition()
        self.log("Timelapse stop requested.")

    def _abort_current_timelapse_acquisition(self):
        if not self.controller or not self.controller.connected:
            return
        try:
            if not self.controller.is_acquiring():
                return
            self.controller.abort_hyperspectral_acquisition()
            self.acquisition_success = False
            self.last_acquisition_error = "Timelapse stopped by user."
            self.acquisition_done_event.set()
            self.log("Current Hera acquisition aborted for timelapse stop.")
        except Exception as exc:
            self.log(f"Could not abort current Hera acquisition during timelapse stop: {exc}")

    def _timelapse_worker(self, single_cycle=False, positions_override=None, run_id=None):
        if run_id is None:
            run_id = self.timelapse_run_id
        cycle = 0
        interval_min = float(self.interval_var.get())
        positions = list(positions_override) if positions_override is not None else list(self.positions)
        try:
            while not self.timelapse_stop_event.is_set():
                if self.timelapse_stop_at and datetime.now() >= self.timelapse_stop_at:
                    self._log_async("Reached requested stop time.")
                    break

                cycle += 1
                self._set_var_async(self.current_cycle_var, f"Cycle: {cycle}")
                self._log_async(f"Cycle {cycle} started.")
                for position in positions:
                    if self.timelapse_stop_event.is_set():
                        break
                    self._wait_while_paused()
                    if self.timelapse_stop_event.is_set():
                        break

                    export_path, confirmed_z, z_status = self.run_stage_site_acquisition(position, cycle_index=cycle)
                    if self.timelapse_stop_event.is_set():
                        break
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
                            "ROI": self._format_roi_short(self._get_position_roi(position) or self.timelapse_roi),
                            "Status": "confirmed",
                        }
                    )
                    self._log_async(f"Cycle {cycle}: completed {position.name} -> {export_path}")

                if self.timelapse_stop_event.is_set():
                    break
                if single_cycle:
                    self._log_async("Test run complete." if positions_override is not None else "Single cycle complete.")
                    break
                if self.timelapse_stop_at and datetime.now() >= self.timelapse_stop_at:
                    self._log_async("Reached requested stop time.")
                    break

                next_cycle_time = datetime.now() + timedelta(minutes=interval_min)
                self._log_async(
                    f"Cycle {cycle} complete. Waiting {interval_min:.2f} minutes from loop completion before next cycle."
                )
                while datetime.now() < next_cycle_time:
                    if self.timelapse_stop_event.is_set():
                        break
                    self._wait_while_paused()
                    if self.timelapse_stop_at and datetime.now() >= self.timelapse_stop_at:
                        self.timelapse_stop_event.set()
                        break
                    time.sleep(0.25)
        except TimelapseStopped as exc:
            self._log_async(str(exc) or "Timelapse stopped.")
        except Exception as exc:
            self._log_async(f"Timelapse failed: {exc}")
            self._safe_after(0, lambda: self.update_state("Error"))
        finally:
            self._write_trigger_log_if_needed()
            self._safe_after(0, lambda run_id=run_id: self._finish_timelapse(run_id))

    def _finish_timelapse(self, run_id=None):
        if run_id is not None and run_id != self.timelapse_run_id:
            return
        self.timelapse_stop_event.set()
        self.timelapse_pause_event.clear()
        self.pause_button.config(text="Pause")
        self.timelapse_status_var.set("Timelapse: idle")
        self.time_remaining_var.set("Time remaining: -")
        self.current_cycle_var.set("Cycle: -")
        self.current_site_var.set("Site: -")
        if self.app_state != self.STATE_LABELS["Error"]:
            self.update_state("Ready" if self.controller and self.controller.connected else "Idle")
        if not getattr(self, "is_closing", False) and not (self.controller and self.controller.connected):
            self.log("Reconnecting Hera after helper timelapse.")
            self._schedule_helper_reconnect()
        self.log("Timelapse stopped.")

    def _write_trigger_log_if_needed(self):
        if not self.trigger_log:
            return
        output_dir = self.param_vars["output_path"].get()
        os.makedirs(output_dir, exist_ok=True)
        log_path = os.path.join(output_dir, f"hera_tango_trigger_log_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        with open(log_path, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=["Cycle", "Site", "X", "Y", "Z", "ZStatus", "Timestamp", "ExportPath", "ROI", "Status"])
            writer.writeheader()
            writer.writerows(self.trigger_log)
        self._log_async(f"Trigger log saved: {log_path}")

    def _wait_while_paused(self):
        while self.timelapse_pause_event.is_set() and not self.timelapse_stop_event.is_set():
            time.sleep(0.1)

    def _await_timelapse_acquisition_completion(self, timeout_sec=300):
        deadline = time.time() + timeout_sec
        abort_requested = False
        while time.time() < deadline:
            if self.acquisition_done_event.wait(timeout=0.25):
                break
            if self.timelapse_stop_event.is_set() and not abort_requested:
                abort_requested = True
                self._abort_current_timelapse_acquisition()
        else:
            raise RuntimeError("Timed out waiting for Hera acquisition to complete.")

        if self.timelapse_stop_event.is_set() and not self.acquisition_success:
            raise TimelapseStopped("Timelapse stopped by user.")
        if not self.acquisition_success:
            raise RuntimeError(self.last_acquisition_error or "Hera acquisition failed.")
        return self.last_export_path

    def _update_time_remaining(self):
        if self.timelapse_thread and self.timelapse_thread.is_alive() and self.timelapse_stop_at:
            remaining = self.timelapse_stop_at - datetime.now()
            seconds = max(int(remaining.total_seconds()), 0)
            self.time_remaining_var.set(f"Time remaining: {seconds / 60:.2f} min")
        elif not (self.timelapse_thread and self.timelapse_thread.is_alive()):
            self.time_remaining_var.set("Time remaining: -")

    def _timelapse_site_z_target(self, position):
        if not getattr(self, "z_motion_enabled", False):
            return None, "Z disabled"
        try:
            target_z = float(position.z)
        except (TypeError, ValueError):
            return None, "no Z"
        if math.isnan(target_z):
            return None, "no Z"
        if self.nis_z is None or self.nis_z_last_value is None:
            return None, "Z skipped: bridge not ready"
        if str(self.nis_z_last_status).lower() != "ok":
            return None, f"Z skipped: {self.nis_z_last_status}"
        return target_z, "pending"

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

        if self.timelapse_stop_event.is_set():
            raise TimelapseStopped("Timelapse stopped before acquisition.")

        # Z motion is disabled for now; only XY is moved before Hera acquisition.
        confirmed_z = None
        z_status = "no Z"
        target_z, z_status = self._timelapse_site_z_target(position)
        if target_z is not None:
            self._log_async(f"NIS Z: targeting {target_z:.3f} um for {position.name}...")
            confirmed_z, z_status = self._move_z_to_position(target_z)
        elif z_status not in ("no Z", "Z disabled"):
            self._log_async(f"{z_status} for {position.name}; starting Hera acquisition without Z move.")

        if self.timelapse_stop_event.is_set():
            raise TimelapseStopped("Timelapse stopped before acquisition.")

        self.log(f"Starting Hera acquisition at {position.name}.")
        if cycle_index is None:
            export_tag = self._sanitize_export_tag(f"{position.name}_{time.strftime('%Y%m%d_%H%M%S')}")
        else:
            export_tag = self._sanitize_export_tag(f"{position.name}_{cycle_index:03d}")
        site_roi = self._get_position_roi(position) or self.timelapse_roi
        if site_roi:
            self.log(f"Using ROI for {position.name}: {self._format_roi(site_roi)}.")
        else:
            self.log(f"No ROI saved for {position.name}; acquiring full frame.")
        self._arm_and_start_acquisition(export_tag=export_tag, forced_roi=site_roi)
        export_path = self._await_timelapse_acquisition_completion()
        return export_path, confirmed_z, z_status
