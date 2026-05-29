import array
import os
import threading
import time
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
        if current_handle:
            handles_to_release.append(current_handle)
        if flatfield_handle and flatfield_handle != current_handle:
            handles_to_release.append(flatfield_handle)

        for handle in handles_to_release:
            try:
                with self.hypercube_read_lock:
                    self.controller.release_hypercube(handle)
            except Exception as exc:
                self.log(f"Could not release previous hypercube before flatfield: {exc}")

        self.current_hypercube_handle = None
        self.current_hypercube_info = None
        self.flatfield_hypercube_handle = None
        self.flatfield_info = None
        self.pending_save_context = None
        self._clear_hypercube_display_state()
        self.flatfield_status_var.set("Flatfield: acquiring")
        self.hypercube_summary_var.set("Cube: waiting for flatfield")
        if self.save_pending_button:
            self.save_pending_button.config(state="disabled")
        if hasattr(self, "hyper_band_scale"):
            self.hyper_band_scale.config(to=0)
        self.current_hyper_band_index.set(0)
        self.current_hyper_band_var.set("Band: -")
        self.current_hyper_wavelength_var.set("Wavelength: -")
        self._draw_hyperspectral_view_placeholder("Flatfield acquisition is starting")
        if handles_to_release:
            self.log("Released previous hypercube data before acquiring a new flatfield.")

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
        width = info["width"]
        height = info["height"]
        source_width = info.get("source_width", width)
        source_height = info.get("source_height", height)
        display_roi = info.get("display_roi")
        bands = info["bands"]
        data_type = info["data_type"]
        raw_path = output_base_path
        hdr_path = output_base_path + ".hdr"
        wavelengths = []

        with open(raw_path, "wb") as raw_file:
            for band_index in range(bands):
                wavelength, sample_values = self.controller.get_hypercube_band_data(
                    sample_handle,
                    band_index,
                    source_width,
                    source_height,
                    data_type,
                )
                _, flat_values = self.controller.get_hypercube_band_data(
                    flatfield_handle,
                    band_index,
                    source_width,
                    source_height,
                    data_type,
                )
                sample_values = self._crop_hyper_band_values_for_display(sample_values, source_width, display_roi)
                flat_values = self._crop_hyper_band_values_for_display(flat_values, source_width, display_roi)
                normalized = array.array(
                    "f",
                    (
                        float(sample) / float(flat) if abs(float(flat)) > 1e-12 else 0.0
                        for sample, flat in zip(sample_values, flat_values)
                    ),
                )
                normalized.tofile(raw_file)
                wavelengths.append(wavelength)

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
        Path(hdr_path).write_text(header, encoding="utf-8")
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
                self._safe_after(0, lambda: self.update_state("Completed"))
            except Exception as exc:
                self._log_async(f"Flatfield save failed: {exc}")
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
        self.flatfield_status_var.set("Flatfield: none")
        self.current_hyper_band_cache = {}
        self.current_hyper_spectrum_cache = {}
        self.current_hyper_pointer_cache = {}
        self.hyper_selected_spectrum = None
        self.hyper_cursor_spectrum = None
        self.hyper_flatfield_spectrum = None
        self.hyper_spectrum_loading = ""
        self.hyper_spectrum_error = ""
        self.log("Flatfield cleared.")
        self.render_current_hyper_band()
