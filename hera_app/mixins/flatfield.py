import array
import os
import threading
import time
import uuid
from pathlib import Path


class FlatfieldMixin:
    def _clear_hypercube_display_state(self):
        self.current_hyper_band_cache = {}
        self.current_hyper_spectrum_cache = {}
        self.current_hyper_pointer_cache = {}
        self.hyper_selected_pixel = None
        self.hyper_cursor_pixel = None
        self.hyper_selected_spectrum = None
        self.hyper_cursor_spectrum = None
        self.hyper_flatfield_spectrum = None
        self.hyper_spectrum_loading = ""
        self.hyper_spectrum_error = ""
        self.hyper_spectrum_y_limits = None
        self.hyper_cursor_spectrum_inflight = False
        self.hyper_cursor_pending_pixel = None
        self.hyper_spectrum_request_ids = {
            key: self.hyper_spectrum_request_ids.get(key, 0) + 1
            for key in ("selected", "cursor", "warmup")
        }

    def _prepare_flatfield_memory(self):
        handles_to_release = []
        current_handle = self.current_hypercube_handle
        flatfield_handle = self.flatfield_hypercube_handle
        keep_current_sample = bool(
            current_handle
            and self.current_hypercube_info
            and self.current_hypercube_info.get("role") != "flatfield"
        )
        if current_handle and not keep_current_sample:
            handles_to_release.append(current_handle)
        if flatfield_handle and flatfield_handle != current_handle:
            handles_to_release.append(flatfield_handle)

        for handle in handles_to_release:
            try:
                with self.hypercube_read_lock:
                    self.controller.release_hypercube(handle)
            except Exception as exc:
                self.log(f"Could not release previous hypercube before flatfield: {exc}")

        if not keep_current_sample:
            self.current_hypercube_handle = None
            self.current_hypercube_info = None
        self.flatfield_hypercube_handle = None
        self.flatfield_info = None
        self.pending_save_context = None
        self._clear_hypercube_display_state()
        self.flatfield_status_var.set("acquiring")
        if not keep_current_sample:
            self.hypercube_summary_var.set("Cube: waiting for flatfield")
        if self.save_pending_button:
            self.save_pending_button.config(state="disabled")
        if hasattr(self, "hyper_band_scale") and not keep_current_sample:
            self.hyper_band_scale.config(to=0)
        if not keep_current_sample:
            self.current_hyper_band_index.set(0)
            self.current_hyper_band_var.set("Band: -")
            self.current_hyper_wavelength_var.set("Wavelength: -")
            self._draw_hyperspectral_view_placeholder("Flatfield acquisition is starting")
        if handles_to_release:
            self.log("Released previous flatfield data before acquiring a new flatfield.")
        if keep_current_sample:
            self.log("Keeping the current sample cube while acquiring a new flatfield.")

    def _flatfield_matches_current_cube(self, info=None):
        info = info or self.current_hypercube_info
        if not info or not self.flatfield_hypercube_handle or not self.flatfield_info:
            return False
        if not self._flatfield_source_matches(info):
            return False
        return self._flatfield_covers_display_roi(info)

    def _should_use_flatfield_correction(self, info=None):
        return self._flatfield_matches_current_cube(info)

    def _flatfield_source_matches(self, info):
        if not info or not self.flatfield_info:
            return False
        if info.get("camera_roi") or self.flatfield_info.get("camera_roi"):
            if info.get("camera_roi") != self.flatfield_info.get("camera_roi"):
                return False
        keys = ("source_width", "source_height", "bands", "data_type")
        return all(info.get(key) == self.flatfield_info.get(key) for key in keys)

    def _flatfield_covers_display_roi(self, info):
        if not info or not self.flatfield_info:
            return False
        display_roi = info.get("display_roi")
        if not display_roi:
            return True
        roi_x, roi_y, roi_w, roi_h = display_roi
        flat_width = int(self.flatfield_info.get("source_width", self.flatfield_info.get("width", 0)) or 0)
        flat_height = int(self.flatfield_info.get("source_height", self.flatfield_info.get("height", 0)) or 0)
        return (
            roi_x >= 0
            and roi_y >= 0
            and roi_w > 0
            and roi_h > 0
            and roi_x + roi_w <= flat_width
            and roi_y + roi_h <= flat_height
        )

    def _flatfield_mismatch_reason(self, info=None):
        info = info or self.current_hypercube_info
        if not self.flatfield_hypercube_handle or not self.flatfield_info:
            return "no flatfield is loaded"
        if not info:
            return "no sample cube is loaded"
        if not self._flatfield_source_matches(info):
            return (
                "camera ROI, source size, band count, or data type differs "
                f"(sample {info.get('source_width')}x{info.get('source_height')}, "
                f"camera_roi={info.get('camera_roi')}, "
                f"bands={info.get('bands')}, type={info.get('data_type')}; "
                f"flatfield {self.flatfield_info.get('source_width')}x{self.flatfield_info.get('source_height')}, "
                f"camera_roi={self.flatfield_info.get('camera_roi')}, "
                f"bands={self.flatfield_info.get('bands')}, type={self.flatfield_info.get('data_type')})"
            )
        if not self._flatfield_covers_display_roi(info):
            return f"flatfield does not cover the sample ROI {info.get('display_roi')}"
        return "flatfield is compatible"

    def _flatfield_info_for_sample(self, sample_info):
        if not self._flatfield_matches_current_cube(sample_info):
            return None
        export_info = dict(sample_info)
        export_info["role"] = "flatfield"
        export_info["is_hdr"] = self.flatfield_info.get("is_hdr")
        return export_info

    def _export_normalized_envi_from_cubes(self, sample_handle, flatfield_handle, output_base_path, description, info):
        width = int(info["width"])
        height = int(info["height"])
        source_width = int(info.get("source_width", width))
        source_height = int(info.get("source_height", height))
        display_roi = info.get("display_roi")
        bands = int(info["bands"])
        data_type = int(info["data_type"])
        if display_roi:
            roi_x, roi_y, roi_w, roi_h = (int(value) for value in display_roi)
            roi_x = max(0, min(roi_x, source_width - 1))
            roi_y = max(0, min(roi_y, source_height - 1))
            roi_w = max(1, min(roi_w, source_width - roi_x))
            roi_h = max(1, min(roi_h, source_height - roi_y))
        else:
            roi_x, roi_y, roi_w, roi_h = 0, 0, width, height
        if roi_w != width or roi_h != height:
            self._log_async(
                "Normalized export ROI size was adjusted to match source data: "
                f"{roi_w} x {roi_h}."
            )
            width, height = roi_w, roi_h
        raw_path = output_base_path
        hdr_path = output_base_path + ".hdr"
        temp_suffix = f".tmp_{uuid.uuid4().hex[:8]}"
        temp_raw_path = raw_path + temp_suffix
        temp_hdr_path = hdr_path + temp_suffix
        wavelengths = []

        try:
            import numpy as np
        except Exception:
            np = None

        def write_band_with_numpy(raw_file, sample_values, flat_values):
            sample_array = np.ctypeslib.as_array(sample_values, shape=(source_height, source_width))
            flat_array = np.ctypeslib.as_array(flat_values, shape=(source_height, source_width))
            sample_roi = sample_array[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]
            flat_roi = flat_array[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]
            normalized = np.zeros((roi_h, roi_w), dtype=np.float32)
            np.divide(sample_roi, flat_roi, out=normalized, where=np.abs(flat_roi) > 1e-12)
            normalized.tofile(raw_file)

        def write_band_with_python(raw_file, sample_values, flat_values):
            for row in range(roi_y, roi_y + roi_h):
                start = row * source_width + roi_x
                stop = start + roi_w

                def normalized_row():
                    for index in range(start, stop):
                        flat = float(flat_values[index])
                        yield float(sample_values[index]) / flat if abs(flat) > 1e-12 else 0.0

                array.array("f", normalized_row()).tofile(raw_file)

        final_raw_replaced = False
        try:
            self._log_async(
                f"Exporting normalized measurement (_nrm): ROI x={roi_x}, y={roi_y}, "
                f"w={roi_w}, h={roi_h}, bands={bands}."
            )
            with self.hypercube_read_lock:
                with open(temp_raw_path, "wb") as raw_file:
                    for band_index in range(bands):
                        wavelength, sample_values = self.controller.get_hypercube_band_pointer(
                            sample_handle,
                            band_index,
                            data_type,
                        )
                        _, flat_values = self.controller.get_hypercube_band_pointer(
                            flatfield_handle,
                            band_index,
                            data_type,
                        )
                        if np is not None:
                            write_band_with_numpy(raw_file, sample_values, flat_values)
                        else:
                            write_band_with_python(raw_file, sample_values, flat_values)
                        wavelengths.append(wavelength)
                        if band_index == 0 or (band_index + 1) % 10 == 0 or band_index + 1 == bands:
                            self._log_async(f"Normalized export progress: band {band_index + 1}/{bands}")

            safe_description = (description or "Generated by AppHeraTriggerPython0417").replace("}", ")")
            wavelength_text = ", ".join(f"{wavelength:.6f}" for wavelength in wavelengths)
            header = (
                "ENVI\n"
                f"description = {{{safe_description}}}\n"
                f"samples = {width}\n"
                f"lines = {height}\n"
                f"bands = {bands}\n"
                "header offset = 0\n"
                "file type = ENVI Standard\n"
                f"data file = {os.path.basename(raw_path)}\n"
                "data type = 4\n"
                "interleave = bsq\n"
                "byte order = 0\n"
                f"wavelength = {{{wavelength_text}}}\n"
            )
            Path(temp_hdr_path).write_text(header, encoding="utf-8")
            os.replace(temp_raw_path, raw_path)
            final_raw_replaced = True
            os.replace(temp_hdr_path, hdr_path)
        except Exception:
            for path in (temp_raw_path, temp_hdr_path):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
            if final_raw_replaced and not os.path.exists(hdr_path):
                try:
                    os.remove(raw_path)
                except Exception:
                    pass
            raise
        return hdr_path

    def start_flatfield_acquisition(self):
        try:
            if not self.controller or not self.controller.connected:
                raise RuntimeError("Connect to Hera before starting flatfield acquisition.")
            if self.controller.is_acquiring():
                raise RuntimeError("The device is already acquiring.")
            self._prepare_flatfield_memory()
            tag = self._sanitize_export_tag(f"flatfield_{time.strftime('%Y%m%d_%H%M%S')}")
            self._arm_and_start_acquisition(export_tag=tag, acquisition_role="flatfield", auto_save=False)
        except Exception as exc:
            self.log(f"Failed to start flatfield acquisition: {exc}")
            self._fail_run_progress("Progress: flatfield failed")
            self.update_state("Error")

    def export_flatfield_reference(self):
        if not self.flatfield_hypercube_handle or not self.flatfield_info:
            self.log("Acquire a flatfield before saving a reference.")
            return

        folder = self.param_vars["output_path"].get().strip()
        export_tag = self._export_tag_from_panel(f"flatfield_{time.strftime('%Y%m%d_%H%M%S')}")

        def worker():
            try:
                self._safe_after(0, lambda: self.update_state("Saving"))
                self._start_busy_progress("Saving flatfield reference...")
                description = "Generated by AppHeraTriggerPython0417 using Hera SDK and Tango stage control"
                description = f"{description}\nFlatfield reference acquisition (_ref)"
                hdr_path, saved_paths, measurement_dir = self._export_flatfield_reference_set(
                    self.flatfield_hypercube_handle,
                    export_tag,
                    folder,
                    description,
                    self.flatfield_info,
                )
                self.last_export_path = hdr_path
                self._set_var_async(self.last_export_var, f"Last export: {os.path.basename(hdr_path)}")
                self._log_async(
                    "Saved flatfield folder: "
                    f"{measurement_dir} ({', '.join(sorted(saved_paths))})"
                )
                self._finish_run_progress("Progress: flatfield saved")
                self._safe_after(0, lambda: self.update_state("Completed"))
            except Exception as exc:
                self._log_async(f"Flatfield save failed: {exc}")
                self._fail_run_progress("Progress: flatfield save failed")
                self._safe_after(0, lambda: self.update_state("Error"))

        threading.Thread(target=worker, daemon=True).start()

    def clear_flatfield(self):
        if self.flatfield_hypercube_handle and self.controller:
            try:
                if self.current_hypercube_handle != self.flatfield_hypercube_handle:
                    with self.hypercube_read_lock:
                        self.controller.release_hypercube(self.flatfield_hypercube_handle)
            except Exception:
                pass
        self.flatfield_hypercube_handle = None
        self.flatfield_info = None
        self.flatfield_status_var.set("none")
        self.current_hyper_band_cache = {}
        self.current_hyper_spectrum_cache = {}
        self.current_hyper_pointer_cache = {}
        self.hyper_selected_spectrum = None
        self.hyper_cursor_spectrum = None
        self.hyper_flatfield_spectrum = None
        self.hyper_spectrum_loading = ""
        self.hyper_spectrum_error = ""
        self.log("Flatfield cleared.")
        self._refresh_export_controls_for_display_mode()
        self.render_current_hyper_band()
