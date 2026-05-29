import math
import threading


class HyperspectralViewerMixin:
    def _clear_hypercube_viewer(self):
        if self.current_hypercube_handle and self.controller:
            if self.current_hypercube_handle != self.flatfield_hypercube_handle:
                try:
                    with self.hypercube_read_lock:
                        self.controller.release_hypercube(self.current_hypercube_handle)
                except Exception:
                    pass
        self.current_hypercube_handle = None
        self.current_hypercube_info = None
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
        self.hyper_display_rect = None
        self.current_hyper_band_index.set(0)
        self.current_hyper_band_var.set("Band: -")
        self.current_hyper_wavelength_var.set("Wavelength: -")
        self.hypercube_summary_var.set("Cube: waiting for acquisition")
        self.acquisition_requested_roi = None
        self.hyper_photo = None
        if hasattr(self, "hyper_band_scale"):
            self.hyper_band_scale.config(to=0)
        self._safe_after(0, self.render_current_hyper_band)

    def _hyper_display_mode(self):
        try:
            mode = self.hyper_display_mode_var.get()
        except Exception:
            mode = "Normalized"
        if mode not in {"Raw", "Flatfield", "Normalized"}:
            mode = "Normalized"
            try:
                self.hyper_display_mode_var.set(mode)
            except Exception:
                pass
        return mode

    def _get_hyper_display_context(self):
        mode = self._hyper_display_mode()
        if mode == "Flatfield":
            if self.flatfield_hypercube_handle and self.flatfield_info:
                return self.flatfield_hypercube_handle, self.flatfield_info, mode, False, ""
            return None, None, mode, False, "No flatfield loaded"

        if self.current_hypercube_handle and self.current_hypercube_info:
            normalize = (
                mode == "Normalized"
                and self.current_hypercube_info.get("role") != "flatfield"
                and self._should_use_flatfield_correction(self.current_hypercube_info)
            )
            notice = ""
            if mode == "Normalized" and not normalize and self.current_hypercube_info.get("role") != "flatfield":
                notice = "Normalized view needs a matching flatfield; showing raw."
            return self.current_hypercube_handle, self.current_hypercube_info, mode, normalize, notice

        if self.flatfield_hypercube_handle and self.flatfield_info:
            return self.flatfield_hypercube_handle, self.flatfield_info, "Flatfield", False, ""
        return None, None, mode, False, "No hypercube loaded"

    def _hyper_control_info(self):
        _handle, info, _mode, _normalize, _notice = self._get_hyper_display_context()
        return info

    def _hyper_band_cache_key(self, band_index, handle, info, normalize):
        return (
            self._handle_cache_key(handle),
            int(band_index),
            self._hyper_display_mode(),
            bool(normalize),
            info.get("source_width", info.get("width")),
            info.get("source_height", info.get("height")),
            info.get("width"),
            info.get("height"),
            info.get("display_roi"),
            info.get("bands"),
            info.get("data_type"),
        )

    def on_hyper_display_mode_changed(self):
        self.current_hyper_band_cache = {}
        self.current_hyper_spectrum_cache = {}
        self.hyper_selected_spectrum = None
        self.hyper_cursor_spectrum = None
        self.hyper_flatfield_spectrum = None
        self.hyper_spectrum_error = ""
        info = self._hyper_control_info()
        if info and hasattr(self, "hyper_band_scale"):
            max_band_index = max(info["bands"] - 1, 0)
            self.hyper_band_scale.config(to=max_band_index)
            self.current_hyper_band_index.set(min(max(int(self.current_hyper_band_index.get()), 0), max_band_index))
        if hasattr(self, "save_pending_button") and not self.pending_save_context:
            can_save_flatfield = self._hyper_display_mode() == "Flatfield" and bool(self.flatfield_hypercube_handle and self.flatfield_info)
            self.save_pending_button.config(state="normal" if can_save_flatfield else "disabled")
        self.render_current_hyper_band()
        if self.hyper_selected_pixel:
            self._start_hyper_selected_spectrum_load(self.hyper_selected_pixel)
        if self.hyper_cursor_pixel:
            self._start_hyper_cursor_spectrum_load(self.hyper_cursor_pixel)

    def on_hyper_band_changed(self, _value=None):
        self.render_current_hyper_band()

    def step_hyper_band(self, delta):
        info = self._hyper_control_info()
        if not info:
            self.log("Run an acquisition first so the hyperspectral viewer has bands to browse.")
            return
        max_band_index = max(info["bands"] - 1, 0)
        next_index = min(max(int(self.current_hyper_band_index.get()) + delta, 0), max_band_index)
        self.current_hyper_band_index.set(next_index)
        self.hyper_band_jump_var.set(str(next_index + 1))
        self.render_current_hyper_band()

    def jump_to_hyper_band(self):
        info = self._hyper_control_info()
        if not info:
            self.log("Run an acquisition first so the hyperspectral viewer has bands to browse.")
            return
        try:
            requested_band = int(self.hyper_band_jump_var.get().strip())
        except ValueError:
            self.log("Enter a whole-number band index to jump.")
            return
        max_band = info["bands"]
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

    def _get_hyper_band_values_for_display(self, band_index, handle=None, info=None, normalize=False):
        handle = handle or self.current_hypercube_handle
        info = info or self.current_hypercube_info
        source_width = info.get("source_width", info["width"])
        source_height = info.get("source_height", info["height"])
        display_roi = info.get("display_roi")
        with self.hypercube_read_lock:
            wavelength, band_values = self.controller.get_hypercube_band_data(
                handle,
                band_index,
                source_width,
                source_height,
                info["data_type"],
            )
            band_values = self._crop_hyper_band_values_for_display(band_values, source_width, display_roi)
            if normalize:
                _, flat_values = self.controller.get_hypercube_band_data(
                    self.flatfield_hypercube_handle,
                    band_index,
                    source_width,
                    source_height,
                    info["data_type"],
                )
                flat_values = self._crop_hyper_band_values_for_display(flat_values, source_width, display_roi)
                band_values = [
                    float(sample) / float(flat) if abs(float(flat)) > 1e-12 else 0.0
                    for sample, flat in zip(band_values, flat_values)
                ]
        return wavelength, band_values

    def _event_to_hyper_image_xy(self, event):
        rect = self.hyper_display_rect
        _handle, info, _mode, _normalize, _notice = self._get_hyper_display_context()
        if not rect or not info:
            return None
        left, top, out_w, out_h = rect
        if event.x < left or event.x >= left + out_w or event.y < top or event.y >= top + out_h:
            return None
        frame_width = info["width"]
        frame_height = info["height"]
        image_x = min(max(int((event.x - left) * frame_width / out_w), 0), frame_width - 1)
        image_y = min(max(int((event.y - top) * frame_height / out_h), 0), frame_height - 1)
        return image_x, image_y

    def on_hyper_mouse_click(self, event):
        if hasattr(self, "hyper_cross_enabled_var") and not self.hyper_cross_enabled_var.get():
            return
        image_pos = self._event_to_hyper_image_xy(event)
        if not image_pos:
            return
        self.hyper_selected_pixel = image_pos
        self.hyper_selected_spectrum = None
        self.hyper_flatfield_spectrum = None
        self._draw_hyper_spectrum_panel()
        self._start_hyper_selected_spectrum_load(image_pos)
        self.render_current_hyper_band()

    def on_hyper_mouse_move(self, event):
        image_pos = self._event_to_hyper_image_xy(event)
        if not image_pos:
            self.on_hyper_mouse_leave(event)
            return
        if image_pos == self.hyper_cursor_pixel:
            return
        self.hyper_cursor_pixel = image_pos
        self._start_hyper_cursor_spectrum_load(image_pos)

    def on_hyper_mouse_leave(self, _event=None):
        if self.hyper_cursor_pixel is None and self.hyper_cursor_spectrum is None:
            return
        self.hyper_cursor_pixel = None
        self.hyper_cursor_spectrum = None
        request_ids = getattr(self, "hyper_spectrum_request_ids", {"selected": 0, "cursor": 0, "warmup": 0})
        request_ids["cursor"] = request_ids.get("cursor", 0) + 1
        self.hyper_spectrum_request_ids = request_ids
        self.hyper_cursor_pending_pixel = None
        self._draw_hyper_spectrum_panel()

    def _handle_cache_key(self, handle):
        return getattr(handle, "value", None) or str(handle)

    def _hyper_pointer_cache_key(self, handle, info):
        return (
            self._handle_cache_key(handle),
            info.get("bands"),
            info.get("data_type"),
        )

    def _get_hyper_pointer_series_unlocked(self, handle, info):
        cache_key = self._hyper_pointer_cache_key(handle, info)
        cached = self.current_hyper_pointer_cache.get(cache_key)
        bands = info["bands"]
        if cached is not None and len(cached) == bands:
            return cached
        data_type = info["data_type"]
        pointers = [
            self.controller.get_hypercube_band_pointer(handle, band_index, data_type)
            for band_index in range(bands)
        ]
        self.current_hyper_pointer_cache[cache_key] = pointers
        return pointers

    def _start_hyper_pointer_cache_warmup(self):
        handle, info, _mode, normalize, _notice = self._get_hyper_display_context()
        if not info or not handle or not self.controller:
            return
        request_ids = getattr(self, "hyper_spectrum_request_ids", {"selected": 0, "cursor": 0, "warmup": 0})
        request_ids["warmup"] = request_ids.get("warmup", 0) + 1
        self.hyper_spectrum_request_ids = request_ids
        request_id = request_ids["warmup"]
        info = dict(info)
        flat_handle = self.flatfield_hypercube_handle
        flat_info = self._flatfield_info_for_sample(info) if info.get("role") != "flatfield" else None
        warm_flat = flat_info is not None and (normalize or info.get("role") != "flatfield")

        def worker():
            try:
                with self.hypercube_read_lock:
                    if getattr(self, "hyper_spectrum_request_ids", {}).get("warmup") != request_id:
                        return
                    self._get_hyper_pointer_series_unlocked(handle, info)
                    if warm_flat and flat_handle and flat_info:
                        self._get_hyper_pointer_series_unlocked(flat_handle, flat_info)
            except Exception as exc:
                self._log_async(f"Spectrum pointer cache warmup failed: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _display_pixel_to_source_index(self, info, image_x, image_y):
        source_width = info.get("source_width", info["width"])
        source_height = info.get("source_height", info["height"])
        display_roi = info.get("display_roi")
        if display_roi:
            roi_x, roi_y, _, _ = display_roi
            source_x = roi_x + image_x
            source_y = roi_y + image_y
        else:
            source_x = image_x
            source_y = image_y
        source_x = min(max(int(source_x), 0), source_width - 1)
        source_y = min(max(int(source_y), 0), source_height - 1)
        return source_y * source_width + source_x

    def _read_hyper_pixel_spectrum(self, handle, info, image_x, image_y, normalize=False, cache=True):
        flat_key = self._handle_cache_key(self.flatfield_hypercube_handle) if normalize else None
        cache_key = (
            "full_pixel_spectrum",
            self._handle_cache_key(handle),
            flat_key,
            int(image_x),
            int(image_y),
            bool(normalize),
            info.get("source_width", info.get("width")),
            info.get("source_height", info.get("height")),
            info.get("width"),
            info.get("height"),
            info.get("display_roi"),
            info.get("bands"),
            info.get("data_type"),
        )
        if cache:
            cached = self.current_hyper_spectrum_cache.get(cache_key)
            if cached is not None:
                return cached

        source_index = self._display_pixel_to_source_index(info, image_x, image_y)
        bands = info["bands"]
        flat_info = self._flatfield_info_for_sample(info) if normalize else None
        if normalize and not flat_info:
            raise RuntimeError("Normalized spectrum needs a compatible flatfield.")
        flat_index = self._display_pixel_to_source_index(flat_info, image_x, image_y) if flat_info else None
        spectrum = []

        with self.hypercube_read_lock:
            band_pointers = self._get_hyper_pointer_series_unlocked(handle, info)
            flat_pointers = self._get_hyper_pointer_series_unlocked(self.flatfield_hypercube_handle, flat_info) if normalize else None
            for band_index, (wavelength, values) in enumerate(band_pointers[:bands]):
                value = float(values[source_index])
                if normalize:
                    _, flat_values = flat_pointers[band_index]
                    flat = float(flat_values[flat_index])
                    value = value / flat if abs(flat) > 1e-12 else 0.0
                spectrum.append((float(wavelength), value))

        if cache:
            self.current_hyper_spectrum_cache[cache_key] = spectrum
        return spectrum

    def _start_hyper_cursor_spectrum_load(self, image_pos):
        handle, info, _mode, normalize, _notice = self._get_hyper_display_context()
        if not info or not handle or not self.controller:
            return
        request_ids = getattr(self, "hyper_spectrum_request_ids", {"selected": 0, "cursor": 0, "warmup": 0})
        request_ids["cursor"] = request_ids.get("cursor", 0) + 1
        self.hyper_spectrum_request_ids = request_ids
        self.hyper_cursor_pending_pixel = image_pos
        if self.hyper_cursor_spectrum_inflight:
            return
        self._launch_hyper_cursor_spectrum_worker()

    def _launch_hyper_cursor_spectrum_worker(self):
        image_pos = self.hyper_cursor_pending_pixel
        handle, info, _mode, normalize, _notice = self._get_hyper_display_context()
        if not image_pos or not info or not handle or not self.controller:
            self.hyper_cursor_spectrum_inflight = False
            return
        self.hyper_cursor_pending_pixel = None
        self.hyper_cursor_spectrum_inflight = True
        request_id = getattr(self, "hyper_spectrum_request_ids", {}).get("cursor", 0)
        info = dict(info)
        image_x, image_y = image_pos

        def worker():
            spectrum = None
            error = ""
            try:
                spectrum = self._read_hyper_pixel_spectrum(handle, info, image_x, image_y, normalize=normalize, cache=False)
            except Exception as exc:
                error = str(exc)

            def finish():
                is_current = (
                    getattr(self, "hyper_spectrum_request_ids", {}).get("cursor") == request_id
                    and self.hyper_cursor_pixel == image_pos
                )
                if is_current:
                    self.hyper_cursor_spectrum = spectrum
                    self.hyper_spectrum_error = error
                    self._draw_hyper_spectrum_panel()
                if self.hyper_cursor_pending_pixel:
                    self._launch_hyper_cursor_spectrum_worker()
                else:
                    self.hyper_cursor_spectrum_inflight = False

            self._safe_after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _start_hyper_selected_spectrum_load(self, image_pos):
        handle, info, mode, normalize, _notice = self._get_hyper_display_context()
        if not info or not handle or not self.controller:
            return
        request_ids = getattr(self, "hyper_spectrum_request_ids", {"selected": 0, "cursor": 0, "warmup": 0})
        request_ids["selected"] = request_ids.get("selected", 0) + 1
        self.hyper_spectrum_request_ids = request_ids
        request_id = request_ids["selected"]
        info = dict(info)
        flat_handle = self.flatfield_hypercube_handle
        flat_info = self._flatfield_info_for_sample(info) if info.get("role") != "flatfield" else None
        image_x, image_y = image_pos
        self.hyper_spectrum_loading = "Loading selected spectrum..."
        self.hyper_spectrum_error = ""
        self._draw_hyper_spectrum_panel()

        def worker():
            spectrum = None
            flat_spectrum = None
            error = ""
            try:
                spectrum = self._read_hyper_pixel_spectrum(handle, info, image_x, image_y, normalize=normalize)
                if mode != "Flatfield" and flat_handle and flat_info:
                    flat_spectrum = self._read_hyper_pixel_spectrum(flat_handle, flat_info, image_x, image_y, normalize=False)
            except Exception as exc:
                error = str(exc)

            def finish():
                if getattr(self, "hyper_spectrum_request_ids", {}).get("selected") != request_id:
                    return
                if self.hyper_selected_pixel == image_pos:
                    self.hyper_selected_spectrum = spectrum
                    self.hyper_flatfield_spectrum = flat_spectrum
                self.hyper_spectrum_loading = ""
                self.hyper_spectrum_error = error
                self._draw_hyper_spectrum_panel()

            self._safe_after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _spectrum_series(self):
        series = []
        if self.hyper_selected_spectrum:
            mode = self._hyper_display_mode()
            label = "Flatfield selection" if mode == "Flatfield" else ("Normalized selection" if mode == "Normalized" else "Pixel selection")
            series.append((label, self.hyper_selected_spectrum, "#ffd15c"))
        if self.hyper_flatfield_spectrum:
            series.append(("Flatfield", self.hyper_flatfield_spectrum, "#6fc3ff"))
        if self.hyper_cursor_spectrum:
            series.append(("Cursor", self.hyper_cursor_spectrum, "#7ad97a"))
        return series

    def _spectrum_value_range(self):
        values = [value for _, points, _ in self._spectrum_series() for _, value in points]
        if not values:
            return None
        min_v, max_v = min(values), max(values)
        if math.isclose(min_v, max_v):
            min_v -= 1.0
            max_v += 1.0
        padding = (max_v - min_v) * 0.08
        return min_v - padding, max_v + padding

    def on_hyper_spectrum_mousewheel(self, event):
        value_range = self._spectrum_value_range()
        if not value_range:
            return "break"
        min_v, max_v = self.hyper_spectrum_y_limits or value_range
        center = (min_v + max_v) / 2.0
        span = max_v - min_v
        if span <= 0:
            return "break"
        delta = getattr(event, "delta", 0)
        if delta == 0:
            if getattr(event, "num", None) == 4:
                delta = 120
            elif getattr(event, "num", None) == 5:
                delta = -120
        factor = 0.8 if delta > 0 else 1.25
        base_span = value_range[1] - value_range[0]
        new_span = max(base_span * 0.002, span * factor)
        self.hyper_spectrum_y_limits = (center - new_span / 2.0, center + new_span / 2.0)
        self._draw_hyper_spectrum_panel()
        return "break"

    def reset_hyper_spectrum_y_axis(self, _event=None):
        self.hyper_spectrum_y_limits = None
        self._draw_hyper_spectrum_panel()
        return "break"

    def _draw_hyper_spectrum_panel(self):
        if not hasattr(self, "hyper_spectrum_canvas"):
            return
        canvas = self.hyper_spectrum_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        pad_left, pad_right, pad_top, pad_bottom = 58, 18, 24, 34
        canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
        plot_w = max(width - pad_left - pad_right, 1)
        plot_h = max(height - pad_top - pad_bottom, 1)
        x0, y0 = pad_left, pad_top
        x1, y1 = pad_left + plot_w, pad_top + plot_h
        for i in range(5):
            y = y0 + i * plot_h / 4
            canvas.create_line(x0, y, x1, y, fill=self.theme["canvas_grid"])
        for i in range(5):
            x = x0 + i * plot_w / 4
            canvas.create_line(x, y0, x, y1, fill=self.theme["canvas_grid"])
        canvas.create_line(x0, y0, x0, y1, fill=self.theme["muted"])
        canvas.create_line(x0, y1, x1, y1, fill=self.theme["muted"])
        canvas.create_text(8, 6, anchor="nw", text="Spectrum", fill=self.theme["text"], font=("Segoe UI Semibold", 9))
        if not self.current_hypercube_info:
            canvas.create_text(width / 2, height / 2, text="No hypercube loaded", fill=self.theme["muted"], font=("Segoe UI", 9))
            return

        series = self._spectrum_series()
        if not series:
            status = self.hyper_spectrum_loading or self.hyper_spectrum_error or "Spectrum: -"
            canvas.create_text(width / 2, height / 2, text=status, fill=self.theme["muted"], font=("Segoe UI", 9))
            return
        wavelengths = [wavelength for _, points, _ in series for wavelength, _ in points]
        min_w, max_w = min(wavelengths), max(wavelengths)
        if math.isclose(min_w, max_w):
            min_w -= 0.5
            max_w += 0.5
        value_range = self._spectrum_value_range()
        min_v, max_v = self.hyper_spectrum_y_limits or value_range
        for i in range(5):
            tick_w = min_w + i * (max_w - min_w) / 4
            x = x0 + i * plot_w / 4
            canvas.create_text(x, height - 10, text=f"{tick_w:.0f}", fill=self.theme["muted"], font=("Segoe UI", 8))
        for i in range(5):
            tick_v = min_v + (4 - i) * (max_v - min_v) / 4
            y = y0 + i * plot_h / 4
            canvas.create_text(x0 - 6, y, anchor="e", text=f"{tick_v:.3g}", fill=self.theme["muted"], font=("Segoe UI", 8))
        canvas.create_text((x0 + x1) / 2, height - 2, anchor="s", text="Wavelength", fill=self.theme["muted"], font=("Segoe UI", 8))

        current_w = None
        current_band = min(max(int(self.current_hyper_band_index.get()), 0), self.current_hypercube_info["bands"] - 1)
        for _, points, _ in series:
            if 0 <= current_band < len(points):
                current_w = points[current_band][0]
                break
        if current_w is not None and max_w > min_w:
            current_x = x0 + ((current_w - min_w) / max(max_w - min_w, 1e-12)) * plot_w
            canvas.create_line(current_x, y0, current_x, y1, fill=self.theme["accent"], width=1)

        for label, points, color in series:
            prev = None
            for wavelength, value in points:
                x = x0 + ((wavelength - min_w) / max(max_w - min_w, 1e-12)) * plot_w
                y = y1 - ((value - min_v) / max(max_v - min_v, 1e-12)) * plot_h
                y = min(max(y, y0), y1)
                if prev:
                    canvas.create_line(prev[0], prev[1], x, y, fill=color, width=2)
                elif len(points) == 1:
                    canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=color, outline=color)
                prev = (x, y)

        legend_x = x1 - 130
        legend_y = y0 + 6
        for index, (label, _, color) in enumerate(series):
            y = legend_y + index * 16
            canvas.create_line(legend_x, y, legend_x + 18, y, fill=color, width=2)
            canvas.create_text(legend_x + 24, y, anchor="w", text=label, fill=self.theme["text"], font=("Segoe UI", 8))

        readout = []
        if self.hyper_selected_pixel:
            readout.append(f"Pixel selection X={self.hyper_selected_pixel[0]} Y={self.hyper_selected_pixel[1]}")
        if self.hyper_cursor_pixel:
            readout.append(f"Cursor X={self.hyper_cursor_pixel[0]} Y={self.hyper_cursor_pixel[1]}")
        if self.hyper_spectrum_loading:
            readout.append(self.hyper_spectrum_loading)
        elif self.hyper_spectrum_error:
            readout.append(f"Spectrum error: {self.hyper_spectrum_error}")
        canvas.create_text(width - 10, 8, anchor="ne", text=" | ".join(readout), fill=self.theme["text"], font=("Segoe UI", 8))

    def render_current_hyper_band(self):
        if not hasattr(self, "hyper_view_canvas"):
            return
        handle, info, _mode, normalize, notice = self._get_hyper_display_context()
        if not info or not handle or not self.controller:
            self._draw_hyperspectral_view_placeholder(notice)
            return
        try:
            band_index = min(max(int(self.current_hyper_band_index.get()), 0), info["bands"] - 1)
            self.current_hyper_band_index.set(band_index)
            cache_key = self._hyper_band_cache_key(band_index, handle, info, normalize)
            if cache_key not in self.current_hyper_band_cache:
                wavelength, band_values = self._get_hyper_band_values_for_display(band_index, handle, info, normalize=normalize)
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
                self.current_hyper_band_cache[cache_key] = (wavelength, gray_bytes, band_values)
                self.log(
                    f"Hyperspectral band {band_index + 1}/{info['bands']} "
                    f"render range: min={min_value:.6f}, max={max_value:.6f}, wavelength={wavelength:.3f}",
                    detail=True,
                )
            wavelength, gray_bytes = self.current_hyper_band_cache[cache_key][:2]
            canvas = self.hyper_view_canvas
            canvas.delete("all")
            width = max(canvas.winfo_width(), 10)
            height = max(canvas.winfo_height(), 10)
            self.hyper_photo, out_w, out_h = self._make_ppm_photo_from_grayscale(
                gray_bytes,
                info["width"],
                info["height"],
                max(width - 16, 1),
                max(height - 16, 1),
            )
            left = (width - out_w) / 2
            top = (height - out_h) / 2
            self.hyper_display_rect = (left, top, out_w, out_h)
            canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
            canvas.create_image(width / 2, height / 2, image=self.hyper_photo, anchor="center")
            if self.hyper_selected_pixel:
                sx, sy = self.hyper_selected_pixel
                if sx >= info["width"] or sy >= info["height"]:
                    self.hyper_selected_pixel = None
                    self.hyper_selected_spectrum = None
                    self.hyper_flatfield_spectrum = None
                else:
                    spectrum_cache_key = (self._hyper_display_mode(), sx, sy, bool(normalize))
                    index = sy * info["width"] + sx
                    if len(self.current_hyper_band_cache[cache_key]) >= 3:
                        _, _, band_values = self.current_hyper_band_cache[cache_key]
                        if 0 <= index < len(band_values):
                            self.current_hyper_spectrum_cache.setdefault(spectrum_cache_key, {})[band_index] = (wavelength, float(band_values[index]))
            if self.hyper_selected_pixel:
                sx, sy = self.hyper_selected_pixel
                if not hasattr(self, "hyper_cross_enabled_var") or self.hyper_cross_enabled_var.get():
                    marker_x = left + (sx + 0.5) * out_w / info["width"]
                    marker_y = top + (sy + 0.5) * out_h / info["height"]
                    canvas.create_line(marker_x - 9, marker_y, marker_x + 9, marker_y, fill="#ffd15c", width=2)
                    canvas.create_line(marker_x, marker_y - 9, marker_x, marker_y + 9, fill="#ffd15c", width=2)
                    canvas.create_oval(marker_x - 4, marker_y - 4, marker_x + 4, marker_y + 4, outline="#ffd15c", width=2)
            if self.hyper_cursor_pixel:
                cx, cy = self.hyper_cursor_pixel
                if cx < info["width"] and cy < info["height"]:
                    cursor_x = left + (cx + 0.5) * out_w / info["width"]
                    cursor_y = top + (cy + 0.5) * out_h / info["height"]
                    canvas.create_oval(cursor_x - 3, cursor_y - 3, cursor_x + 3, cursor_y + 3, outline="#7ad97a", width=1)
            canvas.create_text(
                12,
                12,
                anchor="nw",
                text=f"{info['width']} x {info['height']}",
                fill=self.theme["text"],
                font=("Segoe UI", 9),
            )
            if notice:
                canvas.create_text(width - 12, 12, anchor="ne", text=notice, fill=self.theme["muted"], font=("Segoe UI", 8))
            self.current_hyper_band_var.set(f"Band: {band_index + 1} / {info['bands']}")
            self.hyper_band_jump_var.set(str(band_index + 1))
            self.current_hyper_wavelength_var.set(f"Wavelength: {wavelength:.3f}")
            self._draw_hyper_spectrum_panel()
        except Exception as exc:
            self.log(f"Failed to render hyperspectral band: {exc}")
            self._draw_hyperspectral_view_placeholder()

    def _draw_hyperspectral_view_placeholder(self, detail_text=None):
        if not hasattr(self, "hyper_view_canvas"):
            return
        self.hyper_display_rect = None
        canvas = self.hyper_view_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
        for i, color in enumerate(["#24435b", "#2f6c8f", "#4ea4cf", "#7fd0ff", "#ff8b3d"]):
            x0 = 18 + i * 30
            canvas.create_rectangle(x0, height - 42, x0 + 20, height - 18, fill=color, outline="")
        if not detail_text:
            if self.app_state in {self.STATE_LABELS["Acquiring"], self.STATE_LABELS["WaitingForTrigger"], self.STATE_LABELS["ComputingHypercube"], self.STATE_LABELS["Saving"]}:
                detail_text = "Waiting for the current acquisition and cube computation to finish"
            else:
                detail_text = "Run one acquisition to populate the in-app band viewer"
        canvas.create_text(width / 2, height / 2 - 20, text="Hyperspectral View", fill=self.theme["text"], font=("Segoe UI Semibold", 14))
        canvas.create_text(width / 2, height / 2 + 2, text=detail_text, fill=self.theme["muted"], font=("Segoe UI", 10))
        export_text = self.last_export_var.get() if hasattr(self, "last_export_var") else "Last export: -"
        canvas.create_text(width / 2, height / 2 + 24, text=self.hypercube_summary_var.get(), fill=self.theme["text"], font=("Segoe UI", 10))
        canvas.create_text(width / 2, height / 2 + 46, text=export_text, fill=self.theme["muted"], font=("Segoe UI", 10))
        self._draw_hyper_spectrum_panel()
