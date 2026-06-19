import os
import re
import time
import uuid
import array
from pathlib import Path


class ExportMixin:
    def _sanitize_export_tag(self, text):
        keep = []
        for ch in text:
            if ch.isalnum() or ch in {"-", "_"}:
                keep.append(ch)
            elif ch in {" ", "."}:
                keep.append("_")
        sanitized = "".join(keep).strip("_")
        return sanitized or "measurement"

    def _export_tag_from_panel(self, default_tag, default_name=None):
        name_var = getattr(self, "export_name_var", None)
        append_var = getattr(self, "export_append_time_var", None)
        try:
            entered_name = name_var.get().strip() if name_var is not None else ""
        except Exception:
            entered_name = ""
        tag = entered_name or default_name or default_tag
        try:
            append_time = bool(append_var.get()) if append_var is not None else False
        except Exception:
            append_time = False
        if entered_name and append_time:
            tag = f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}"
        return self._sanitize_export_tag(tag)

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

    def _make_measurement_base_path(self, output_dir, export_tag, unique=True):
        os.makedirs(output_dir, exist_ok=True)
        base_name = self._sanitize_export_tag(export_tag)
        folder_name = base_name
        folder_path = os.path.join(output_dir, folder_name)
        if unique:
            suffix = 2
            while os.path.exists(folder_path):
                folder_name = f"{base_name}_{suffix}"
                folder_path = os.path.join(output_dir, folder_name)
                suffix += 1
        os.makedirs(folder_path, exist_ok=True)
        return os.path.join(folder_path, folder_name), folder_path

    def _export_hypercube_envi_with_roi(self, hypercube_handle, output_base_path, description, info=None, log_label="hypercube"):
        info = info or {}
        export_roi = info.get("export_roi")
        cube_width = info.get("source_width") or info.get("width") or 0
        cube_height = info.get("source_height") or info.get("height") or 0
        should_crop_export = False
        if export_roi and cube_width and cube_height:
            roi_x, roi_y, roi_w, roi_h = export_roi
            should_crop_export = (roi_x, roi_y, roi_w, roi_h) != (0, 0, cube_width, cube_height)

        if should_crop_export:
            bands = int(info.get("bands") or 0)
            data_type = int(info.get("data_type") if info.get("data_type") is not None else 0)
            if bands and data_type in (0, 1):
                try:
                    return self._export_hypercube_roi_envi_direct(
                        hypercube_handle,
                        output_base_path,
                        export_roi,
                        cube_width,
                        cube_height,
                        bands,
                        data_type,
                        description,
                        log_label,
                    )
                except Exception as exc:
                    self._log_async(
                        f"Direct ROI export failed for {log_label}: {exc}. "
                        "Falling back to full-frame SDK export plus crop."
                    )
            else:
                self._log_async(
                    f"Direct ROI export skipped for {log_label}: missing bands/data type in export info. "
                    "Falling back to full-frame SDK export plus crop."
                )
            return self._export_hypercube_fullframe_then_crop(
                hypercube_handle,
                output_base_path,
                description,
                export_roi,
                log_label,
            )

        if export_roi:
            self._log_async(f"ROI diagnostic: {log_label} already matches the selected ROI size; exporting directly.")
        self.controller.export_hypercube_envi(hypercube_handle, output_base_path, description)
        hdr_path = self._wait_for_export_files(output_base_path)
        data_path = self._find_envi_data_file(output_base_path)
        self._patch_envi_header_for_hyperlab(hdr_path, data_path)
        return hdr_path

    def _export_hypercube_fullframe_then_crop(self, hypercube_handle, output_base_path, description, export_roi, log_label):
        temp_output_path = f"{output_base_path}_fullframe_tmp_{uuid.uuid4().hex[:8]}"
        self._log_async(
            f"ROI diagnostic: exporting {log_label} full frame temporarily, then cropping "
            f"to x={export_roi[0]}, y={export_roi[1]}, w={export_roi[2]}, h={export_roi[3]}."
        )
        self.controller.export_hypercube_envi(hypercube_handle, temp_output_path, description)
        self._wait_for_export_files(temp_output_path)
        crop_description = (
            f"{description}\n"
            f"Post-export ROI crop: x={export_roi[0]}, y={export_roi[1]}, "
            f"width={export_roi[2]}, height={export_roi[3]}"
        )
        try:
            return self._crop_exported_envi_to_roi(temp_output_path, output_base_path, export_roi, crop_description)
        finally:
            self._remove_export_files(temp_output_path)

    def _export_hypercube_roi_envi_direct(
        self,
        hypercube_handle,
        output_base_path,
        roi,
        source_width,
        source_height,
        bands,
        data_type,
        description,
        log_label,
    ):
        roi_x, roi_y, roi_w, roi_h = (int(value) for value in roi)
        roi_x = max(0, min(roi_x, source_width - 1))
        roi_y = max(0, min(roi_y, source_height - 1))
        roi_w = max(1, min(roi_w, source_width - roi_x))
        roi_h = max(1, min(roi_h, source_height - roi_y))
        raw_path = output_base_path
        hdr_path = output_base_path + ".hdr"
        temp_suffix = f".tmp_{uuid.uuid4().hex[:8]}"
        temp_raw_path = raw_path + temp_suffix
        temp_hdr_path = hdr_path + temp_suffix
        envi_data_type = 4 if data_type == 0 else 5
        array_typecode = "f" if data_type == 0 else "d"
        wavelengths = []
        final_raw_replaced = False

        try:
            import numpy as np
        except Exception:
            np = None

        def write_band_with_numpy(raw_file, values):
            band_array = np.ctypeslib.as_array(values, shape=(source_height, source_width))
            roi_array = band_array[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]
            roi_array.astype("<f4" if data_type == 0 else "<f8", copy=False).tofile(raw_file)

        def write_band_with_python(raw_file, values):
            for row in range(roi_y, roi_y + roi_h):
                start = row * source_width + roi_x
                stop = start + roi_w
                array.array(array_typecode, (values[index] for index in range(start, stop))).tofile(raw_file)

        try:
            self._log_async(
                f"ROI diagnostic: exporting {log_label} directly from hypercube memory "
                f"to x={roi_x}, y={roi_y}, w={roi_w}, h={roi_h}, bands={bands}."
            )
            with self.hypercube_read_lock:
                with open(temp_raw_path, "wb") as raw_file:
                    for band_index in range(bands):
                        wavelength, values = self.controller.get_hypercube_band_pointer(
                            hypercube_handle,
                            band_index,
                            data_type,
                        )
                        if np is not None:
                            write_band_with_numpy(raw_file, values)
                        else:
                            write_band_with_python(raw_file, values)
                        wavelengths.append(wavelength)
                        if band_index == 0 or (band_index + 1) % 10 == 0 or band_index + 1 == bands:
                            self._log_async(f"Direct ROI export progress ({log_label}): band {band_index + 1}/{bands}")

            safe_description = (description or "Generated by AppHeraTriggerPython0417").replace("}", ")")
            safe_description = (
                f"{safe_description}\n"
                f"Direct ROI export: x={roi_x}, y={roi_y}, width={roi_w}, height={roi_h}"
            )
            wavelength_text = ", ".join(f"{wavelength:.6f}" for wavelength in wavelengths)
            header = (
                "ENVI\n"
                f"description = {{{safe_description}}}\n"
                f"samples = {roi_w}\n"
                f"lines = {roi_h}\n"
                f"bands = {bands}\n"
                "header offset = 0\n"
                "file type = ENVI Standard\n"
                f"data file = {os.path.basename(raw_path)}\n"
                f"data type = {envi_data_type}\n"
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

    def _patch_envi_header_for_hyperlab(self, hdr_path, data_path):
        header_text = Path(hdr_path).read_text(encoding="utf-8", errors="replace")
        header_text = self._replace_envi_header_value(header_text, "file type", "ENVI Standard")
        header_text = self._replace_envi_header_value(header_text, "data file", os.path.basename(data_path))
        Path(hdr_path).write_text(header_text, encoding="utf-8")

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
        cropped_header = self._replace_envi_header_value(cropped_header, "file type", "ENVI Standard")
        cropped_header = self._replace_envi_header_value(cropped_header, "data file", os.path.basename(target_data_path))
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
