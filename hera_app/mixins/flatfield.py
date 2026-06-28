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
            and self._cube_source_available(current_handle, self.current_hypercube_info)
        )
        if current_handle and not keep_current_sample and not self._is_owned_cube_info(self.current_hypercube_info):
            handles_to_release.append(current_handle)
        if flatfield_handle and flatfield_handle != current_handle and not self._is_owned_cube_info(self.flatfield_info):
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
            self.current_hypercube_data = None
        self.flatfield_hypercube_handle = None
        self.flatfield_info = None
        self.flatfield_hypercube_data = None
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
        if current_handle and not keep_current_sample:
            self.log("Released the current sample cube before flatfield acquisition for SDK compatibility.")
        elif flatfield_handle and flatfield_handle != current_handle:
            self.log("Released previous flatfield data before acquiring a new flatfield.")
        elif keep_current_sample:
            self.log("Keeping cached sample cube in memory while acquiring a new flatfield reference.")

    def _flatfield_matches_current_cube(self, info=None):
        info = info or self.current_hypercube_info
        if not info or not self.flatfield_hypercube_handle or not self.flatfield_info:
            return False
        if not self._flatfield_source_matches(info):
            return False
        return self._flatfield_covers_display_roi(info)

    def _should_use_flatfield_correction(self, info=None):
        return self._flatfield_matches_current_cube(info)

    def _info_source_dimensions(self, info):
        if not info:
            return 0, 0
        try:
            width = int(info.get("source_width") or info.get("width") or 0)
            height = int(info.get("source_height") or info.get("height") or 0)
        except Exception:
            return 0, 0
        return width, height

    def _flatfield_alignment_for_sample(self, sample_info):
        flat_info = self.flatfield_info
        if not sample_info or not flat_info or not self.flatfield_hypercube_handle:
            return None
        if sample_info.get("bands") != flat_info.get("bands"):
            return None
        if sample_info.get("data_type") != flat_info.get("data_type"):
            return None

        sample_source_width, sample_source_height = self._info_source_dimensions(sample_info)
        flat_source_width, flat_source_height = self._info_source_dimensions(flat_info)
        if sample_source_width <= 0 or sample_source_height <= 0:
            return None
        if flat_source_width <= 0 or flat_source_height <= 0:
            return None

        sample_display_roi = self._normalize_roi_tuple(sample_info.get("display_roi"))
        sample_camera_roi = self._normalize_roi_tuple(sample_info.get("camera_roi"))
        flat_camera_roi = self._normalize_roi_tuple(flat_info.get("camera_roi"))

        def make_plan(sample_roi, flat_roi, mode):
            sample_roi = self._clip_roi_to_dimensions(sample_roi, sample_source_width, sample_source_height)
            flat_roi = self._clip_roi_to_dimensions(flat_roi, flat_source_width, flat_source_height)
            if not sample_roi or not flat_roi:
                return None
            sample_x, sample_y, sample_w, sample_h = sample_roi
            flat_x, flat_y, flat_w, flat_h = flat_roi
            width = min(sample_w, flat_w)
            height = min(sample_h, flat_h)
            if width <= 0 or height <= 0:
                return None
            return {
                "mode": mode,
                "sample_source_width": sample_source_width,
                "sample_source_height": sample_source_height,
                "flatfield_source_width": flat_source_width,
                "flatfield_source_height": flat_source_height,
                "sample_roi": (sample_x, sample_y, width, height),
                "flatfield_roi": (flat_x, flat_y, width, height),
                "width": width,
                "height": height,
            }

        if sample_camera_roi and not flat_camera_roi:
            camera_x, camera_y, camera_w, camera_h = sample_camera_roi
            if camera_w > 0 and camera_h > 0:
                scale_x = sample_source_width / camera_w
                scale_y = sample_source_height / camera_h
                flat_x = int(round(camera_x * scale_x))
                flat_y = int(round(camera_y * scale_y))
                plan = make_plan(
                    (0, 0, sample_source_width, sample_source_height),
                    (flat_x, flat_y, sample_source_width, sample_source_height),
                    "sample-camera-roi-flatfield-full-frame",
                )
                if plan:
                    return plan

        if sample_camera_roi or flat_camera_roi:
            if sample_camera_roi != flat_camera_roi:
                return None
            if sample_source_width != flat_source_width or sample_source_height != flat_source_height:
                return None

        if sample_source_width != flat_source_width or sample_source_height != flat_source_height:
            return None

        sample_roi = sample_display_roi or (0, 0, sample_source_width, sample_source_height)
        return make_plan(sample_roi, sample_roi, "matching-source")

    def _flatfield_source_matches(self, info):
        return self._flatfield_alignment_for_sample(info) is not None

    def _flatfield_covers_display_roi(self, info):
        return self._flatfield_alignment_for_sample(info) is not None

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
        alignment = self._flatfield_alignment_for_sample(sample_info)
        if not alignment:
            return None
        export_info = dict(self.flatfield_info)
        export_info["role"] = "flatfield"
        export_info["is_hdr"] = self.flatfield_info.get("is_hdr")
        export_info["width"] = alignment["width"]
        export_info["height"] = alignment["height"]
        export_info["source_width"] = alignment["flatfield_source_width"]
        export_info["source_height"] = alignment["flatfield_source_height"]
        export_info["display_roi"] = alignment["flatfield_roi"]
        export_info["export_roi"] = alignment["flatfield_roi"]
        return export_info

    def _export_normalized_envi_from_cubes(self, sample_handle, flatfield_handle, output_base_path, description, info):
        alignment = self._flatfield_alignment_for_sample(info)
        if not alignment:
            raise RuntimeError(f"Normalized export needs a compatible flatfield ({self._flatfield_mismatch_reason(info)}).")
        width = alignment["width"]
        height = alignment["height"]
        source_width = alignment["sample_source_width"]
        source_height = alignment["sample_source_height"]
        flat_source_width = alignment["flatfield_source_width"]
        flat_source_height = alignment["flatfield_source_height"]
        roi_x, roi_y, roi_w, roi_h = alignment["sample_roi"]
        flat_roi_x, flat_roi_y, flat_roi_w, flat_roi_h = alignment["flatfield_roi"]
        bands = int(info["bands"])
        data_type = int(info["data_type"])
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

        def write_band(raw_file, sample_values, flat_values, sample_width, flat_width):
            if np is not None:
                sample_array = np.asarray(sample_values).reshape((source_height, source_width))
                flat_array = np.asarray(flat_values).reshape((flat_source_height, flat_source_width))
                sample_roi = sample_array[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]
                flat_roi = flat_array[flat_roi_y:flat_roi_y + flat_roi_h, flat_roi_x:flat_roi_x + flat_roi_w]
                normalized = np.zeros((roi_h, roi_w), dtype=np.float32)
                np.divide(sample_roi, flat_roi, out=normalized, where=np.abs(flat_roi) > 1e-12)
                normalized.tofile(raw_file)
                return

            for sample_row, flat_row in zip(range(roi_y, roi_y + roi_h), range(flat_roi_y, flat_roi_y + flat_roi_h)):
                sample_start = sample_row * sample_width + roi_x
                flat_start = flat_row * flat_width + flat_roi_x

                def normalized_row():
                    for offset in range(roi_w):
                        sample = float(sample_values[sample_start + offset])
                        flat = float(flat_values[flat_start + offset])
                        yield sample / flat if abs(flat) > 1e-12 else 0.0

                array.array("f", normalized_row()).tofile(raw_file)

        final_raw_replaced = False
        try:
            self._log_async(
                "Exporting normalized measurement (_nrm): "
                f"sample ROI x={roi_x}, y={roi_y}, w={roi_w}, h={roi_h}; "
                f"flatfield ROI x={flat_roi_x}, y={flat_roi_y}, w={flat_roi_w}, h={flat_roi_h}; "
                f"bands={bands}."
            )
            with open(temp_raw_path, "wb") as raw_file:
                for band_index in range(bands):
                    wavelength, sample_values, sample_width, _sample_height = self._get_cube_band_flat_values(
                        sample_handle,
                        info,
                        band_index,
                    )
                    _, flat_values, flat_width, _flat_height = self._get_cube_band_flat_values(
                        flatfield_handle,
                        self.flatfield_info,
                        band_index,
                    )
                    write_band(raw_file, sample_values, flat_values, sample_width, flat_width)
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
            busy_reason = self._acquisition_busy_reason()
            if busy_reason:
                self.log(f"Flatfield acquisition ignored because {busy_reason}.")
                return
            self._prepare_flatfield_memory()
            tag = self._sanitize_export_tag(f"flatfield_{time.strftime('%Y%m%d_%H%M%S')}")
            active_roi = self._get_active_roi()
            helper_roi_ready = bool(
                active_roi
                and getattr(self, "helper_acquisition_enabled", True)
                and self.param_vars["trigger_mode"].get() == "Internal"
                and not self._helper_blocking_sdk_cube_reason()
            )
            self._arm_and_start_acquisition(
                export_tag=tag,
                acquisition_role="flatfield",
                auto_save=False,
                use_camera_roi=helper_roi_ready,
            )
        except Exception as exc:
            self.promote_next_sample_to_flatfield = False
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

    def use_current_sample_as_flatfield(self):
        if not self.current_hypercube_handle or not self.current_hypercube_info:
            self.log("Acquire a sample/reference cube before using it as flatfield.")
            return
        if self.current_hypercube_info.get("role") == "flatfield":
            self.log("The current cube is already a flatfield reference.")
            return

        previous_flatfield = self.flatfield_hypercube_handle
        current_handle = self.current_hypercube_handle
        if (
            previous_flatfield
            and previous_flatfield != current_handle
            and self.controller
            and not self._is_owned_cube_info(self.flatfield_info)
        ):
            try:
                with self.hypercube_read_lock:
                    self.controller.release_hypercube(previous_flatfield)
            except Exception as exc:
                self.log(f"Could not release previous flatfield before using current cube: {exc}")

        self.flatfield_hypercube_handle = current_handle
        self.flatfield_info = dict(self.current_hypercube_info)
        self.flatfield_info["role"] = "flatfield"
        if self._is_owned_cube_info(self.current_hypercube_info):
            self.flatfield_info["owned_data_role"] = "flatfield"
            self.flatfield_hypercube_data = self.current_hypercube_data
        width = int(self.flatfield_info.get("width", 0) or 0)
        height = int(self.flatfield_info.get("height", 0) or 0)
        bands = int(self.flatfield_info.get("bands", 0) or 0)
        self.flatfield_status_var.set(f"ready ({width} x {height}, bands={bands})")
        self.current_hyper_band_cache = {}
        self.current_hyper_spectrum_cache = {}
        self.current_hyper_pointer_cache = {}
        self.hyper_selected_spectrum = None
        self.hyper_cursor_spectrum = None
        self.hyper_flatfield_spectrum = None
        self.hyper_spectrum_loading = ""
        self.hyper_spectrum_error = ""
        self.log(
            "Current sample cube is now the flatfield reference. "
            "Keep the same ROI, bands, binning, data type, and HDR setting for normalized samples."
        )
        self._refresh_export_controls_for_display_mode()
        if self.current_hypercube_info.get("role") != "flatfield":
            try:
                self.hyper_display_mode_var.set("Raw")
            except Exception:
                pass
        self.render_current_hyper_band()

    def clear_flatfield(self):
        if self.flatfield_hypercube_handle and self.controller and not self._is_owned_cube_info(self.flatfield_info):
            try:
                if self.current_hypercube_handle != self.flatfield_hypercube_handle:
                    with self.hypercube_read_lock:
                        self.controller.release_hypercube(self.flatfield_hypercube_handle)
            except Exception:
                pass
        self.flatfield_hypercube_handle = None
        self.flatfield_info = None
        self.flatfield_hypercube_data = None
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
