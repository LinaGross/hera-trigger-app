import array
import ctypes
import math
import threading
import time
import tkinter as tk
import zlib
from base64 import b64encode
from tkinter import filedialog
from datetime import datetime


class LiveViewMixin:
    def start_live_view(self):
        if not self.controller or not self.controller.connected:
            return
        try:
            if self.controller.is_live_capturing():
                self.log("Hera live capture already running.")
                return
            hdr_requested = bool(self.hdr_enabled_var.get())
            supported_formats = []
            for pixel_format, pixel_name in self.LIVE_PIXEL_FORMATS.items():
                if self.controller.is_pixel_format_supported(pixel_format, hdr=hdr_requested):
                    supported_formats.append((pixel_format, pixel_name))
            if supported_formats:
                self.log(
                    "Supported live pixel formats"
                    f" ({'HDR' if hdr_requested else 'non-HDR'}): "
                    + ", ".join(name for _, name in supported_formats)
                )
            selected_format = None
            for pixel_format, pixel_name in supported_formats:
                if selected_format is None:
                    selected_format = pixel_format
                    self.live_pixel_format_name = pixel_name
                    break
            if selected_format is None:
                self.log(
                    "Live view could not start: no supported live pixel format reported by the SDK "
                    f"for {'HDR' if hdr_requested else 'non-HDR'} mode."
                )
                self._set_live_view_status("Live view: no supported pixel format")
                return
            self.controller.start_live_capture(pixel_format=selected_format)
            self.live_first_frame_logged = False
            self.live_first_frame_rendered = False
            self._set_live_view_status(f"Live view: starting ({self.live_pixel_format_name})")
            if self.live_watchdog_job:
                try:
                    self.after_cancel(self.live_watchdog_job)
                except Exception:
                    pass
            self.live_watchdog_job = self._safe_after(8000, self._check_live_view_started)
            self.log(f"Hera live capture started using {self.live_pixel_format_name} ({'HDR' if hdr_requested else 'non-HDR'}).")
        except Exception as exc:
            self._set_live_view_status("Live view: failed to start")
            self.log(f"Live view could not start: {exc}")

    def restart_live_view(self):
        if not self.controller or not self.controller.connected:
            self.log("Cannot restart live view because Hera is not connected.")
            return
        self.log("Restarting Hera live view...")
        self._set_live_view_status("Live view: restarting")

        def worker():
            try:
                self.controller.stop_live_capture(silent=True)
                self.controller.wait_for_live_capture_stopped(timeout_sec=5.0)
                self.controller.unregister_live_callbacks()
                self._safe_after(0, self._clear_live_view_frame_state)
                self._safe_after(0, self.start_live_view)
            except Exception as exc:
                self._log_async(f"Live view restart failed: {exc}")
                self._safe_after(0, lambda: self._set_live_view_status("Live view: restart failed"))

        threading.Thread(target=worker, daemon=True).start()

    def stop_live_view(self):
        if not self.controller:
            return
        try:
            self.controller.stop_live_capture(silent=True)
            self.controller.wait_for_live_capture_stopped(timeout_sec=5.0)
            self.controller.unregister_live_callbacks()
        except Exception:
            pass
        self._clear_live_view_frame_state()
        self._set_live_view_status("Live view: stopped")
        self._safe_after(0, self._draw_live_view_placeholder)

    def _clear_live_view_frame_state(self):
        with self.live_frame_lock:
            self.latest_live_frame = None
            self.latest_live_profile = None
            self.live_frame_info = None
            self.live_display_rect = None
            self.live_display_frame_size = None
            self.live_cursor_image_xy = None
            self.live_render_pending = False
            self.last_live_render_time = 0.0
        self.live_photo = None
        self.live_cross_point = None
        self.live_profile_status_var.set("Cross: click to pin" if self.live_cross_enabled_var.get() else "Cross: center")
        self.live_cursor_var.set(self._live_cursor_status_text("-"))
        self.live_first_frame_rendered = False
        self.live_auth_warning_logged = False
        self.last_live_decode_error = ""
        if self.live_watchdog_job:
            try:
                self.after_cancel(self.live_watchdog_job)
            except Exception:
                pass
            self.live_watchdog_job = None

    def _set_live_view_status(self, text):
        if self.is_closing:
            return
        if self.live_view_status_var.get() == text:
            return
        if threading.current_thread() is threading.main_thread():
            self.live_view_status_var.set(text)
        else:
            self._safe_after(0, lambda: self.live_view_status_var.set(text))

    def _schedule_live_render(self, force=False):
        if self.is_closing:
            return
        now = time.time()
        with self.live_frame_lock:
            if self.live_render_pending:
                return
            if not force and (now - self.last_live_render_time) < self.live_render_interval_sec:
                return
            self.live_render_pending = True
        self._safe_after(0, self._render_live_photo)

    def on_live_capture_error(self, message):
        self._log_async(f"Live capture error: {message}")

    def on_live_capture_timeout(self, free_buffers):
        if free_buffers <= 1:
            self._log_async(f"Live capture buffer warning: free buffers={free_buffers}")

    def on_live_capture_frame(self, capture_handle):
        try:
            if self.is_closing:
                return
            info = self.controller.get_live_capture_info(capture_handle)
            live_is_hdr = None
            try:
                live_is_hdr = self.controller.get_live_capture_is_hdr(capture_handle)
            except Exception:
                live_is_hdr = None
            self.live_auth_warning_logged = False
            self.last_live_decode_error = ""
            if not info["data_ptr"] or self.is_closing:
                return

            width = info["width"]
            height = info["height"]
            bit_depth = info["bit_depth"]
            row_stride = info["row_stride"]
            bits_per_pixel = info["bits_per_pixel"]
            saturation_threshold = info["saturation_threshold"]
            if not saturation_threshold:
                effective_depth = bit_depth if bit_depth > 0 else bits_per_pixel
                saturation_threshold = (1 << effective_depth) - 1 if effective_depth > 0 else 65535
            bytes_per_pixel = max(1, (bits_per_pixel + 7) // 8)
            raw_size = row_stride * height
            raw_buffer = ctypes.string_at(info["data_ptr"], raw_size)
            scale = self._live_preview_scale(width)
            display_bytes, disp_width, disp_height, saturation_mask = self._extract_live_preview_bytes(
                raw_buffer,
                width,
                height,
                row_stride,
                bytes_per_pixel,
                bit_depth,
                bits_per_pixel,
                saturation_threshold,
                scale,
            )
            _, preview_min, preview_max = self._normalize_grayscale_for_display(display_bytes)
            threshold_display = self._display_saturation_threshold(saturation_threshold, bit_depth, bits_per_pixel)

            with self.live_frame_lock:
                self.live_frame_info = (width, height, bits_per_pixel)
                self.latest_live_frame = (disp_width, disp_height, display_bytes, saturation_mask)
                self.latest_live_profile = (disp_width, disp_height, display_bytes, threshold_display)
            self._set_live_view_status(f"Live view: receiving {self.live_pixel_format_name}")
            if not self.live_first_frame_logged:
                self.live_first_frame_logged = True
                if self.live_watchdog_job:
                    try:
                        self.after_cancel(self.live_watchdog_job)
                    except Exception:
                        pass
                    self.live_watchdog_job = None
                hdr_text = "unknown" if live_is_hdr is None else ("on" if live_is_hdr else "off")
                self._log_async(
                    f"First live frame received: {width}x{height}, bitDepth={bit_depth}, "
                    f"bitsPerPixel={bits_per_pixel}, format={self.live_pixel_format_name}, HDR={hdr_text}"
                )
                self._log_async(f"Live preview auto-contrast range: min={preview_min}, max={preview_max}")
                self._log_async(f"Live saturation threshold from SDK: {saturation_threshold} (display scale: {threshold_display})")
            self._schedule_live_render(force=not self.live_photo)
        except Exception as exc:
            error_text = str(exc)
            if "Invalid authentication code" in error_text:
                if not self.live_auth_warning_logged:
                    self.live_auth_warning_logged = True
                    self._set_live_view_status("Live view: SDK authentication warning")
                    self._log_async(f"Live capture warning: {error_text}")
            elif error_text != self.last_live_decode_error:
                self.last_live_decode_error = error_text
                self._log_async(f"Live frame decode failed: {error_text}")
        finally:
            try:
                self.controller.release_live_capture_result(capture_handle)
            except Exception:
                pass

    def _check_live_view_started(self):
        if self.is_closing:
            self.live_watchdog_job = None
            return
        self.live_watchdog_job = None
        if not self.live_first_frame_logged:
            self._set_live_view_status("Live view: no frames received")
            self.log("Live view started but no frames were received from the Hera SDK.")
        elif not self.live_first_frame_rendered:
            self._set_live_view_status(f"Live view: frames received, waiting to draw {self.live_pixel_format_name}")
            self.log("Live view has received frames from the Hera SDK, but the preview has not drawn yet.")

    def debug_live_status(self):
        if not self.controller or not self.controller.connected:
            self.log("Live diagnostics: Hera is not connected.")
            return
        try:
            capturing = self.controller.is_live_capturing()
        except Exception as exc:
            self.log(f"Live diagnostics: failed to read live capture state: {exc}")
            return

        supported_names = []
        hdr_requested = bool(self.hdr_enabled_var.get())
        for pixel_format, pixel_name in self.LIVE_PIXEL_FORMATS.items():
            try:
                if self.controller.is_pixel_format_supported(pixel_format, hdr=hdr_requested):
                    supported_names.append(pixel_name)
            except Exception:
                pass

        self.log(
            "Live diagnostics: "
            f"capturing={capturing}, "
            f"status='{self.live_view_status_var.get()}', "
            f"first_frame={self.live_first_frame_logged}, "
            f"first_render={self.live_first_frame_rendered}, "
            f"HDR={'on' if hdr_requested else 'off'}, "
            f"selected_format={self.live_pixel_format_name}, "
            f"supported={supported_names or ['none']}"
        )
        if self.live_frame_info:
            width, height, bits_per_pixel = self.live_frame_info
            self.log(
                "Live diagnostics: "
                f"last_frame={width}x{height}, "
                f"bits={bits_per_pixel}"
            )
        else:
            self.log("Live diagnostics: no live frame metadata available yet.")

    def _fit_dimensions(self, src_width, src_height, dest_width, dest_height):
        if src_width <= 0 or src_height <= 0:
            return 1, 1
        scale = min(dest_width / src_width, dest_height / src_height)
        if scale <= 0:
            scale = 1.0
        out_w = max(1, int(src_width * scale))
        out_h = max(1, int(src_height * scale))
        return out_w, out_h

    def _clamp_live_pan(self, canvas_width=None, canvas_height=None, image_width=None, image_height=None):
        if not hasattr(self, "live_view_canvas"):
            return
        canvas = self.live_view_canvas
        canvas_width = max(canvas_width if canvas_width is not None else canvas.winfo_width(), 10)
        canvas_height = max(canvas_height if canvas_height is not None else canvas.winfo_height(), 10)
        image_width = max(image_width if image_width is not None else 1, 1)
        image_height = max(image_height if image_height is not None else 1, 1)

        if image_width <= canvas_width:
            self.live_pan_x = 0.0
        else:
            max_pan_x = (image_width - canvas_width) / 2
            self.live_pan_x = max(-max_pan_x, min(max_pan_x, self.live_pan_x))

        if image_height <= canvas_height:
            self.live_pan_y = 0.0
        else:
            max_pan_y = (image_height - canvas_height) / 2
            self.live_pan_y = max(-max_pan_y, min(max_pan_y, self.live_pan_y))

    def _update_live_zoom_label(self):
        self.live_zoom_label_var.set(f"Zoom {int(round(self.live_zoom_factor * 100))}%")

    def zoom_live_view(self, factor, event=None):
        old_zoom = self.live_zoom_factor
        new_zoom = max(1.0, min(8.0, old_zoom * factor))
        if abs(new_zoom - old_zoom) < 0.001:
            return

        if event is not None:
            with self.live_frame_lock:
                frame = self.latest_live_frame
                rect = self.live_display_rect
                frame_size = self.live_display_frame_size
            if frame and rect:
                if frame_size:
                    src_width, src_height = frame_size
                else:
                    src_width, src_height = frame[0], frame[1]
                canvas = self.live_view_canvas
                canvas_width = max(canvas.winfo_width(), 10)
                canvas_height = max(canvas.winfo_height(), 10)
                old_left, old_top, old_w, old_h = rect
                if old_w > 0 and old_h > 0:
                    rel_x = (event.x - old_left) / old_w
                    rel_y = (event.y - old_top) / old_h
                    display_src_width, display_src_height = self._live_display_dimensions(src_width, src_height)
                    base_w, base_h = self._fit_dimensions(display_src_width, display_src_height, max(canvas_width - 16, 1), max(canvas_height - 16, 1))
                    new_w = max(1, int(round(base_w * new_zoom)))
                    new_h = max(1, int(round(base_h * new_zoom)))
                    centered_left = (canvas_width - new_w) / 2
                    centered_top = (canvas_height - new_h) / 2
                    self.live_pan_x = event.x - rel_x * new_w - centered_left
                    self.live_pan_y = event.y - rel_y * new_h - centered_top
                    self._clamp_live_pan(canvas_width, canvas_height, new_w, new_h)

        self.live_zoom_factor = new_zoom
        self._update_live_zoom_label()
        self._schedule_live_render(force=True)

    def fit_live_view(self):
        self.live_zoom_factor = 1.0
        self.live_pan_x = 0.0
        self.live_pan_y = 0.0
        self.live_pan_drag_start = None
        self._update_live_zoom_label()
        self._schedule_live_render(force=True)

    def zoom_live_view_to_roi(self, roi_rect=None):
        if not hasattr(self, "live_view_canvas"):
            return
        roi_rect = roi_rect or self.live_roi_rect
        if not roi_rect:
            return
        self.live_view_crop_roi = roi_rect
        self.live_zoom_factor = 1.0
        self.live_pan_x = 0.0
        self.live_pan_y = 0.0
        self._update_live_zoom_label()
        self._schedule_live_render(force=True)

    def _crop_live_frame_bytes(self, gray_bytes, saturation_mask, src_width, src_height, frame_width, frame_height, roi_x, roi_y, roi_w, roi_h):
        if src_width <= 0 or src_height <= 0 or frame_width <= 0 or frame_height <= 0:
            return gray_bytes, saturation_mask, src_width, src_height, 0, 0
        scale_x = frame_width / src_width
        scale_y = frame_height / src_height
        ds_x = max(0, int(roi_x / scale_x))
        ds_y = max(0, int(roi_y / scale_y))
        ds_x2 = min(src_width, int(math.ceil((roi_x + roi_w) / scale_x)))
        ds_y2 = min(src_height, int(math.ceil((roi_y + roi_h) / scale_y)))
        ds_w = max(1, ds_x2 - ds_x)
        ds_h = max(1, ds_y2 - ds_y)
        cropped = bytearray(ds_w * ds_h)
        for y in range(ds_h):
            src_row = (ds_y + y) * src_width + ds_x
            cropped[y * ds_w:(y + 1) * ds_w] = gray_bytes[src_row:src_row + ds_w]
        cropped_mask = None
        if saturation_mask:
            m = bytearray(ds_w * ds_h)
            for y in range(ds_h):
                src_row = (ds_y + y) * src_width + ds_x
                m[y * ds_w:(y + 1) * ds_w] = saturation_mask[src_row:src_row + ds_w]
            cropped_mask = bytes(m)
        return bytes(cropped), cropped_mask, ds_w, ds_h, roi_x, roi_y

    def on_live_mousewheel(self, event):
        if event.delta > 0:
            self.zoom_live_view(1.25, event)
        elif event.delta < 0:
            self.zoom_live_view(1 / 1.25, event)

    def start_live_pan(self, event):
        self.live_pan_drag_start = (event.x, event.y, self.live_pan_x, self.live_pan_y)

    def on_live_pan_drag(self, event):
        if not self.live_pan_drag_start:
            return
        start_x, start_y, pan_x, pan_y = self.live_pan_drag_start
        self.live_pan_x = pan_x + event.x - start_x
        self.live_pan_y = pan_y + event.y - start_y
        with self.live_frame_lock:
            rect = self.live_display_rect
        if rect:
            _, _, out_w, out_h = rect
            self._clamp_live_pan(image_width=out_w, image_height=out_h)
        self._schedule_live_render(force=True)

    def end_live_pan(self, _event=None):
        self.live_pan_drag_start = None

    def _live_preview_scale(self, width):
        target_w = min(width, self.live_max_preview_width)
        return max(1, math.ceil(width / target_w))

    def _display_saturation_threshold(self, saturation_threshold, bit_depth, bits_per_pixel):
        threshold = int(saturation_threshold or 0)
        if threshold <= 0:
            return None
        effective_depth = bit_depth if bit_depth > 0 else bits_per_pixel
        if effective_depth <= 8:
            return max(0, min(255, threshold))
        max_value = float((1 << effective_depth) - 1)
        return max(0, min(255, int(round((threshold / max_value) * 255.0))))

    def _extract_live_preview_bytes(self, raw_buffer, width, height, row_stride, bytes_per_pixel, bit_depth, bits_per_pixel, saturation_threshold, scale):
        display_width = max(1, math.ceil(width / scale))
        display_height = max(1, math.ceil(height / scale))
        pixel_row_bytes = width * bytes_per_pixel
        raw_view = memoryview(raw_buffer)
        saturation_mask = bytearray(display_width * display_height)
        saturation_threshold = int(saturation_threshold or 0)

        if bytes_per_pixel == 1:
            sampled_rows = []
            dst_index = 0
            for row_index in range(0, height, scale):
                row_start = row_index * row_stride
                row_end = row_start + pixel_row_bytes
                row_samples = raw_view[row_start:row_end:scale]
                sampled_rows.append(bytes(row_samples))
                if saturation_threshold > 0:
                    for sample in row_samples:
                        if sample >= saturation_threshold and dst_index < len(saturation_mask):
                            saturation_mask[dst_index] = 1
                        dst_index += 1
            sampled = b"".join(sampled_rows)
            display_height = max(1, len(sampled) // display_width)
            return sampled, display_width, display_height, bytes(saturation_mask[:len(sampled)])

        if bytes_per_pixel == 2:
            effective_depth = bit_depth if bit_depth > 0 else bits_per_pixel
            shift = max(effective_depth - 8, 0)
            sampled = bytearray(display_width * display_height)
            dst_index = 0
            for row_index in range(0, height, scale):
                row_start = row_index * row_stride
                row_end = row_start + pixel_row_bytes
                row_samples = raw_view[row_start:row_end].cast("H")
                for sample in row_samples[::scale]:
                    if saturation_threshold > 0 and sample >= saturation_threshold:
                        saturation_mask[dst_index] = 1
                    if shift:
                        sample = sample >> shift
                    if sample > 255:
                        sample = 255
                    sampled[dst_index] = sample
                    dst_index += 1
            return bytes(sampled[:dst_index]), display_width, max(1, dst_index // display_width), bytes(saturation_mask[:dst_index])

        effective_depth = bit_depth if bit_depth > 0 else bits_per_pixel
        max_value = float((1 << effective_depth) - 1) if effective_depth > 0 else 65535.0
        sampled = bytearray(display_width * display_height)
        dst_index = 0
        for row_index in range(0, height, scale):
            row_start = row_index * row_stride
            row_end = row_start + pixel_row_bytes
            row_bytes = raw_view[row_start:row_end]
            for column_index in range(0, width, scale):
                src_index = column_index * bytes_per_pixel
                sample = int.from_bytes(row_bytes[src_index:src_index + bytes_per_pixel], "little", signed=False)
                if saturation_threshold > 0 and sample >= saturation_threshold:
                    saturation_mask[dst_index] = 1
                sampled[dst_index] = max(0, min(255, int(round((sample / max_value) * 255.0))))
                dst_index += 1
        return bytes(sampled[:dst_index]), display_width, max(1, dst_index // display_width), bytes(saturation_mask[:dst_index])

    def _resample_grayscale_nearest(self, src_bytes, src_width, src_height, dst_width, dst_height):
        if (src_width, src_height) == (dst_width, dst_height):
            return src_bytes
        x_map = [min(src_width - 1, int(x * src_width / dst_width)) for x in range(dst_width)]
        result = bytearray(dst_width * dst_height)
        dst_offset = 0
        for y in range(dst_height):
            src_y = min(src_height - 1, int(y * src_height / dst_height))
            src_row_start = src_y * src_width
            src_row = src_bytes[src_row_start:src_row_start + src_width]
            result[dst_offset:dst_offset + dst_width] = bytes(src_row[x] for x in x_map)
            dst_offset += dst_width
        return bytes(result)

    def _get_live_display_rotation(self):
        try:
            rotation = int(getattr(self, "live_display_rotation_degrees", 0))
        except Exception:
            rotation = 0
        return rotation % 360

    def _live_display_dimensions(self, frame_width, frame_height):
        rotation = self._get_live_display_rotation()
        if rotation in (90, 270):
            return frame_height, frame_width
        return frame_width, frame_height

    def _rotate_grayscale_clockwise(self, src_bytes, src_width, src_height):
        expected_len = src_width * src_height
        if not src_bytes or src_width <= 0 or src_height <= 0 or len(src_bytes) < expected_len:
            return src_bytes
        dst_width = src_height
        dst = bytearray(expected_len)
        for src_y in range(src_height):
            src_row_start = src_y * src_width
            dst_x = src_height - 1 - src_y
            for src_x, value in enumerate(src_bytes[src_row_start:src_row_start + src_width]):
                dst[src_x * dst_width + dst_x] = value
        return bytes(dst)

    def _rotate_grayscale_counterclockwise(self, src_bytes, src_width, src_height):
        expected_len = src_width * src_height
        if not src_bytes or src_width <= 0 or src_height <= 0 or len(src_bytes) < expected_len:
            return src_bytes
        dst_width = src_height
        dst = bytearray(expected_len)
        for src_y in range(src_height):
            src_row_start = src_y * src_width
            for src_x, value in enumerate(src_bytes[src_row_start:src_row_start + src_width]):
                dst[(src_width - 1 - src_x) * dst_width + src_y] = value
        return bytes(dst)

    def _orient_live_display_bytes(self, gray_bytes, src_width, src_height, saturation_mask=None):
        rotation = self._get_live_display_rotation()
        if rotation == 90:
            oriented_bytes = self._rotate_grayscale_clockwise(gray_bytes, src_width, src_height)
            oriented_mask = self._rotate_grayscale_clockwise(saturation_mask, src_width, src_height) if saturation_mask else None
            return oriented_bytes, src_height, src_width, oriented_mask
        if rotation == 180:
            oriented_bytes = bytes(reversed(gray_bytes)) if gray_bytes else gray_bytes
            oriented_mask = bytes(reversed(saturation_mask)) if saturation_mask else None
            return oriented_bytes, src_width, src_height, oriented_mask
        if rotation == 270:
            oriented_bytes = self._rotate_grayscale_counterclockwise(gray_bytes, src_width, src_height)
            oriented_mask = self._rotate_grayscale_counterclockwise(saturation_mask, src_width, src_height) if saturation_mask else None
            return oriented_bytes, src_height, src_width, oriented_mask
        return gray_bytes, src_width, src_height, saturation_mask

    def _raw_live_xy_to_display_xy(self, image_x, image_y, frame_width, frame_height):
        rotation = self._get_live_display_rotation()
        display_width, display_height = self._live_display_dimensions(frame_width, frame_height)
        if rotation == 90:
            return frame_height - 1 - image_y, image_x, display_width, display_height
        if rotation == 180:
            return frame_width - 1 - image_x, frame_height - 1 - image_y, display_width, display_height
        if rotation == 270:
            return image_y, frame_width - 1 - image_x, display_width, display_height
        return image_x, image_y, display_width, display_height

    def _display_live_xy_to_raw_xy(self, display_x, display_y, frame_width, frame_height):
        rotation = self._get_live_display_rotation()
        if rotation == 90:
            return display_y, frame_height - 1 - display_x
        if rotation == 180:
            return frame_width - 1 - display_x, frame_height - 1 - display_y
        if rotation == 270:
            return frame_width - 1 - display_y, display_x
        return display_x, display_y

    def _normalize_grayscale_for_display(self, gray_bytes):
        if not gray_bytes:
            return gray_bytes, 0, 0
        min_value = min(gray_bytes)
        max_value = max(gray_bytes)
        if min_value == max_value:
            return gray_bytes, min_value, max_value
        scale = 255.0 / (max_value - min_value)
        lut = bytes(max(0, min(255, int((i - min_value) * scale + 0.5))) for i in range(256))
        return gray_bytes.translate(lut), min_value, max_value

    def _get_live_display_gamma(self):
        try:
            gamma = float(self.live_gamma_var.get())
        except Exception:
            gamma = 1.0
        return max(0.2, min(3.0, gamma))

    def on_live_gamma_change(self, _value=None):
        gamma = self._get_live_display_gamma()
        self.live_gamma_label_var.set(f"Gamma Value {gamma:.1f}")
        self._schedule_live_render(force=True)

    def reset_live_gamma(self):
        self.live_gamma_var.set(1.0)
        self.on_live_gamma_change()

    def _apply_live_display_gamma(self, gray_bytes):
        gamma = self._get_live_display_gamma()
        if not gray_bytes or abs(gamma - 1.0) < 0.01:
            return gray_bytes
        cached = getattr(self, "_gamma_lut_cache", None)
        if cached is None or cached[0] != gamma:
            inverse_gamma = 1.0 / gamma
            lut = bytes(
                max(0, min(255, int(round(((v / 255.0) ** inverse_gamma) * 255.0))))
                for v in range(256)
            )
            self._gamma_lut_cache = (gamma, lut)
            cached = self._gamma_lut_cache
        return gray_bytes.translate(cached[1])

    def _prepare_live_display_bytes(self, gray_bytes):
        if self.live_autocontrast_var.get():
            render_bytes, _, _ = self._normalize_grayscale_for_display(gray_bytes)
        else:
            render_bytes = gray_bytes
        return self._apply_live_display_gamma(render_bytes)

    def _grayscale_to_rgb_bytes(self, gray_bytes, src_width, src_height, dest_width, dest_height, saturation_mask=None):
        scaled = self._resample_grayscale_nearest(gray_bytes, src_width, src_height, dest_width, dest_height)
        n = len(scaled)
        gray_arr = array.array('B', scaled)
        rgb_arr = array.array('B', b'\x00' * (n * 3))
        rgb_arr[0::3] = gray_arr
        rgb_arr[1::3] = gray_arr
        rgb_arr[2::3] = gray_arr
        if saturation_mask:
            scaled_mask = self._resample_grayscale_nearest(saturation_mask, src_width, src_height, dest_width, dest_height)
            for i, m in enumerate(scaled_mask):
                if m:
                    base = i * 3
                    rgb_arr[base] = 255
                    rgb_arr[base + 1] = 0
                    rgb_arr[base + 2] = 0
        return rgb_arr.tobytes()

    def _make_ppm_photo_from_grayscale(self, gray_bytes, src_width, src_height, dest_width, dest_height, saturation_mask=None):
        out_w, out_h = self._fit_dimensions(src_width, src_height, dest_width, dest_height)
        ppm_payload = self._grayscale_to_rgb_bytes(gray_bytes, src_width, src_height, out_w, out_h, saturation_mask)
        ppm_bytes = f"P6\n{out_w} {out_h}\n255\n".encode("ascii") + bytes(ppm_payload)
        try:
            photo = tk.PhotoImage(data=ppm_bytes, format="PPM")
        except tk.TclError:
            photo = tk.PhotoImage(data=b64encode(ppm_bytes), format="PPM")
        return photo, out_w, out_h

    def _png_chunk(self, chunk_type, payload):
        chunk_name = chunk_type.encode("ascii")
        checksum = zlib.crc32(chunk_name + payload) & 0xFFFFFFFF
        return (
            len(payload).to_bytes(4, "big")
            + chunk_name
            + payload
            + checksum.to_bytes(4, "big")
        )

    def _write_rgb_png(self, path, rgb_payload, width, height):
        row_stride = width * 3
        raw_rows = bytearray((row_stride + 1) * height)
        dst_index = 0
        for row_index in range(height):
            raw_rows[dst_index] = 0
            dst_index += 1
            row_start = row_index * row_stride
            raw_rows[dst_index:dst_index + row_stride] = rgb_payload[row_start:row_start + row_stride]
            dst_index += row_stride

        ihdr = (
            width.to_bytes(4, "big")
            + height.to_bytes(4, "big")
            + bytes([8, 2, 0, 0, 0])
        )
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            + self._png_chunk("IHDR", ihdr)
            + self._png_chunk("IDAT", zlib.compress(bytes(raw_rows)))
            + self._png_chunk("IEND", b"")
        )
        with open(path, "wb") as handle:
            handle.write(png_bytes)

    def snapshot_live_view(self):
        import os
        from tkinter import messagebox
        with self.live_frame_lock:
            frame = self.latest_live_frame
        if not frame:
            messagebox.showinfo("Live Snapshot", "Start live view first, then take a snapshot.")
            return

        if len(frame) == 4:
            src_width, src_height, gray_bytes, saturation_mask = frame
        else:
            src_width, src_height, gray_bytes = frame
            saturation_mask = None

        render_bytes = self._prepare_live_display_bytes(gray_bytes)
        render_mask = saturation_mask if self.live_show_saturation_var.get() else None
        render_bytes, display_width, display_height, render_mask = self._orient_live_display_bytes(
            render_bytes,
            src_width,
            src_height,
            render_mask,
        )

        default_dir = self.param_vars.get("output_path").get() if "output_path" in self.param_vars else ""
        if not default_dir or not os.path.isdir(default_dir):
            default_dir = os.path.abspath(os.path.dirname(__file__))
        default_name = f"hera_live_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        snapshot_path = filedialog.asksaveasfilename(
            title="Save live snapshot",
            initialdir=default_dir,
            initialfile=default_name,
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
        )
        if not snapshot_path:
            return
        if not os.path.splitext(snapshot_path)[1]:
            snapshot_path += ".png"

        try:
            rgb_payload = self._grayscale_to_rgb_bytes(
                render_bytes,
                display_width,
                display_height,
                display_width,
                display_height,
                render_mask,
            )
            self._write_rgb_png(snapshot_path, rgb_payload, display_width, display_height)
        except Exception as exc:
            messagebox.showerror("Live Snapshot", f"Could not save snapshot:\n{exc}")
            self.log(f"Live snapshot failed: {exc}")
            return

        self.log(f"Live snapshot saved: {snapshot_path}")
        self._set_live_view_status(f"Live snapshot saved: {os.path.basename(snapshot_path)}")

    def _draw_live_view_placeholder(self):
        if not hasattr(self, "live_view_canvas"):
            return
        if self.live_photo is not None:
            self._render_live_photo()
            return
        canvas = self.live_view_canvas
        canvas.delete("all")
        with self.live_frame_lock:
            self.live_display_rect = None
            self.live_display_frame_size = None
            self.live_cursor_image_xy = None
        self.live_cursor_var.set(self._live_cursor_status_text("-"))
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
        step = 24
        for x in range(0, width, step):
            canvas.create_line(x, 0, x, height, fill=self.theme["canvas_grid"])
        for y in range(0, height, step):
            canvas.create_line(0, y, width, y, fill=self.theme["canvas_grid"])
        canvas.create_text(width / 2, height / 2 - 14, text="Live View", fill=self.theme["text"], font=("Segoe UI Semibold", 14))
        canvas.create_text(width / 2, height / 2 + 12, text=self.live_view_status_var.get(), fill=self.theme["muted"], font=("Segoe UI", 10))
        self._render_live_profiles()

    def toggle_live_cross(self):
        if not self.live_cross_enabled_var.get():
            self.live_cross_point = None
        self._schedule_live_render(force=True)
        self._render_live_profiles()

    def _draw_live_cross_overlay(self, canvas):
        if not self.live_cross_enabled_var.get() or not self.live_cross_point:
            return
        with self.live_frame_lock:
            rect = self.live_display_rect
            frame_size = self.live_display_frame_size
        if not rect or not frame_size:
            return
        image_x, image_y = self.live_cross_point
        left, top, out_w, out_h = rect
        frame_width, frame_height = frame_size
        if frame_width <= 0 or frame_height <= 0:
            return
        ox, oy = self._live_crop_offset
        image_x -= ox
        image_y -= oy
        display_x, display_y, display_width, display_height = self._raw_live_xy_to_display_xy(
            image_x,
            image_y,
            frame_width,
            frame_height,
        )
        cx = left + (display_x + 0.5) * out_w / display_width
        cy = top + (display_y + 0.5) * out_h / display_height
        if cx < left or cx > left + out_w or cy < top or cy > top + out_h:
            return
        color = "#46d66f"
        canvas.create_line(cx - 10, cy, cx + 10, cy, fill=color, width=2)
        canvas.create_line(cx, cy - 10, cx, cy + 10, fill=color, width=2)
        canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, outline=color, width=2)

    def _draw_empty_profile_canvas(self, canvas, text):
        if not canvas:
            return
        canvas.delete("all")
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
        canvas.create_text(width / 2, height / 2, text=text, fill=self.theme["muted"], font=("Segoe UI", 9))

    def _render_live_profiles(self):
        if not hasattr(self, "live_horizontal_profile_canvas") or not hasattr(self, "live_vertical_profile_canvas"):
            return
        with self.live_frame_lock:
            profile = self.latest_live_profile
            frame_info = self.live_frame_info
        if not profile or not frame_info:
            self._draw_empty_profile_canvas(self.live_horizontal_profile_canvas, "Waiting for frame")
            self._draw_empty_profile_canvas(self.live_vertical_profile_canvas, "Waiting for frame")
            return
        prof_w, prof_h, gray_bytes, threshold = profile
        full_w, full_h = frame_info[0], frame_info[1]
        if prof_w <= 0 or prof_h <= 0 or full_w <= 0 or full_h <= 0:
            return
        if self.live_cross_point:
            image_x, image_y = self.live_cross_point
        elif self.live_cursor_image_xy:
            image_x, image_y = self.live_cursor_image_xy[0], self.live_cursor_image_xy[1]
        else:
            image_x, image_y = full_w // 2, full_h // 2
        px = max(0, min(prof_w - 1, int(image_x * prof_w / full_w)))
        py = max(0, min(prof_h - 1, int(image_y * prof_h / full_h)))
        row_values = gray_bytes[py * prof_w:(py + 1) * prof_w]
        col_values = gray_bytes[px:prof_w * prof_h:prof_w]
        self._draw_horizontal_profile(row_values, threshold)
        self._draw_vertical_profile(col_values, threshold)

    def _draw_horizontal_profile(self, values, threshold):
        canvas = self.live_horizontal_profile_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        pad = 10
        canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
        if len(values) < 2:
            canvas.create_text(8, 8, anchor="nw", text="Horizontal", fill=self.theme["muted"], font=("Segoe UI", 8))
            return
        data_max = max(values)
        y_max = max(threshold if threshold is not None else 0, data_max, 1)
        plot_w = max(width - 2 * pad, 1)
        plot_h = max(height - 2 * pad, 1)
        if threshold is not None:
            ty = height - pad - (threshold / y_max) * plot_h
            canvas.create_line(pad, ty, width - pad, ty, fill="#e84b4b", width=1)
            canvas.create_text(width - pad, ty - 3, anchor="se", text=str(threshold), fill="#e84b4b", font=("Segoe UI", 7))
        canvas.create_text(8, 8, anchor="nw", text="Horizontal", fill=self.theme["muted"], font=("Segoe UI", 8))
        prev = None
        step = max(1, math.ceil(len(values) / max(plot_w, 1)))
        sampled = values[::step]
        for index, value in enumerate(sampled):
            x = pad + index * plot_w / max(len(sampled) - 1, 1)
            y = height - pad - (value / y_max) * plot_h
            if prev:
                canvas.create_line(prev[0], prev[1], x, y, fill=self.theme["accent_soft"], width=1)
            prev = (x, y)

    def _draw_vertical_profile(self, values, threshold):
        canvas = self.live_vertical_profile_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 10)
        height = max(canvas.winfo_height(), 10)
        pad = 10
        canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
        if len(values) < 2:
            canvas.create_text(8, 8, anchor="nw", text="Vertical", fill=self.theme["muted"], font=("Segoe UI", 8))
            return
        data_max = max(values)
        x_max = max(threshold if threshold is not None else 0, data_max, 1)
        plot_w = max(width - 2 * pad, 1)
        plot_h = max(height - 2 * pad, 1)
        if threshold is not None:
            tx = pad + (threshold / x_max) * plot_w
            canvas.create_line(tx, pad, tx, height - pad, fill="#e84b4b", width=1)
        canvas.create_text(8, 8, anchor="nw", text="Vertical", fill=self.theme["muted"], font=("Segoe UI", 8))
        prev = None
        step = max(1, math.ceil(len(values) / max(plot_h, 1)))
        sampled = values[::step]
        for index, value in enumerate(sampled):
            x = pad + (value / x_max) * plot_w
            y = pad + index * plot_h / max(len(sampled) - 1, 1)
            if prev:
                canvas.create_line(prev[0], prev[1], x, y, fill=self.theme["accent_soft"], width=1)
            prev = (x, y)

    def _render_live_photo(self):
        if not hasattr(self, "live_view_canvas"):
            return
        with self.live_frame_lock:
            frame = self.latest_live_frame
            frame_info = self.live_frame_info
        if not frame:
            with self.live_frame_lock:
                self.live_render_pending = False
            return
        try:
            if len(frame) == 4:
                src_width, src_height, gray_bytes, saturation_mask = frame
            else:
                src_width, src_height, gray_bytes = frame
                saturation_mask = None
            crop_roi = self.live_view_crop_roi
            if crop_roi:
                fw = frame_info[0] if frame_info else src_width
                fh = frame_info[1] if frame_info else src_height
                rx, ry, rw, rh = crop_roi
                gray_bytes, saturation_mask, src_width, src_height, cx, cy = self._crop_live_frame_bytes(
                    gray_bytes, saturation_mask, src_width, src_height, fw, fh, rx, ry, rw, rh
                )
                self._live_crop_offset = (cx, cy)
                frame_info = (rw, rh, frame_info[2] if frame_info else 0)
            else:
                self._live_crop_offset = (0, 0)
            render_bytes = self._prepare_live_display_bytes(gray_bytes)
            render_mask = saturation_mask if self.live_show_saturation_var.get() else None
            display_bytes, display_src_width, display_src_height, display_mask = self._orient_live_display_bytes(
                render_bytes,
                src_width,
                src_height,
                render_mask,
            )
            canvas = self.live_view_canvas
            width = max(canvas.winfo_width(), 10)
            height = max(canvas.winfo_height(), 10)
            base_w, base_h = self._fit_dimensions(display_src_width, display_src_height, max(width - 16, 1), max(height - 16, 1))
            target_w = max(1, int(round(base_w * self.live_zoom_factor)))
            target_h = max(1, int(round(base_h * self.live_zoom_factor)))
            self.live_photo, out_w, out_h = self._make_ppm_photo_from_grayscale(
                display_bytes,
                display_src_width,
                display_src_height,
                target_w,
                target_h,
                display_mask,
            )
        except tk.TclError as exc:
            with self.live_frame_lock:
                self.live_render_pending = False
            self.log(f"Live preview render failed: {exc}")
            return
        except Exception as exc:
            with self.live_frame_lock:
                self.live_render_pending = False
            self.log(f"Live preview render failed: {exc}")
            return
        canvas = self.live_view_canvas
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill=self.theme["canvas"], outline="")
        self._clamp_live_pan(width, height, out_w, out_h)
        left = (width - out_w) / 2 + self.live_pan_x
        top = (height - out_h) / 2 + self.live_pan_y
        if frame_info:
            frame_width, frame_height, _ = frame_info
        else:
            frame_width, frame_height = src_width, src_height
        with self.live_frame_lock:
            self.live_display_rect = (left, top, out_w, out_h)
            self.live_display_frame_size = (frame_width, frame_height)
        canvas.create_image(left + out_w / 2, top + out_h / 2, image=self.live_photo, anchor="center")
        if frame_info:
            w, h, bpp = frame_info
            canvas.create_text(
                12,
                12,
                anchor="nw",
                text=f"Live frame: {w} x {h}  |  {bpp}-bit  |  {self.live_pixel_format_name}",
                fill="#e7edf5",
                font=("Segoe UI", 9),
            )
        self._draw_live_roi_overlay(canvas)
        self._draw_live_cross_overlay(canvas)
        self._render_live_profiles()
        if not self.live_first_frame_rendered:
            self.live_first_frame_rendered = True
            self._set_live_view_status(f"Live view: displaying {self.live_pixel_format_name}")
            self.log("Live preview rendered successfully on the canvas.")
        with self.live_frame_lock:
            self.last_live_render_time = time.time()
            self.live_render_pending = False

    def _live_event_to_image_xy(self, event):
        with self.live_frame_lock:
            rect = self.live_display_rect
            frame_size = self.live_display_frame_size
        if not rect or not frame_size:
            return None

        left, top, out_w, out_h = rect
        frame_width, frame_height = frame_size
        if out_w <= 0 or out_h <= 0 or frame_width <= 0 or frame_height <= 0:
            return None
        if event.x < left or event.x >= left + out_w or event.y < top or event.y >= top + out_h:
            return None

        display_width, display_height = self._live_display_dimensions(frame_width, frame_height)
        display_x = min(max(int((event.x - left) * display_width / out_w), 0), display_width - 1)
        display_y = min(max(int((event.y - top) * display_height / out_h), 0), display_height - 1)
        image_x, image_y = self._display_live_xy_to_raw_xy(display_x, display_y, frame_width, frame_height)
        ox, oy = self._live_crop_offset
        return image_x + ox, image_y + oy, frame_width, frame_height

    def on_live_mouse_move(self, event):
        image_pos = self._live_event_to_image_xy(event)
        if not image_pos:
            self.live_cursor_var.set(self._live_cursor_status_text("-"))
            return

        image_x, image_y, frame_width, frame_height = image_pos
        self.live_cursor_image_xy = (image_x, image_y, frame_width, frame_height)
        self._update_live_cursor_readout()
        if not self.live_cross_point:
            self._render_live_profiles()

    def on_live_mouse_click(self, event):
        image_pos = self._live_event_to_image_xy(event)
        if not image_pos:
            if self.live_roi_selecting:
                self.live_roi_status_var.set("ROI: click inside live image")
            return

        image_x, image_y, frame_width, frame_height = image_pos
        if not self.live_roi_selecting:
            if self.live_cross_enabled_var.get():
                self.live_cross_point = (image_x, image_y)
                self.live_profile_status_var.set(f"Cross: pinned X={image_x} Y={image_y}")
                self._schedule_live_render(force=True)
                self._render_live_profiles()
            return

        self.live_roi_points.append((image_x, image_y))
        if len(self.live_roi_points) == 1:
            self.live_roi_status_var.set(f"ROI: first corner ({image_x}, {image_y}); click opposite corner")
            self._draw_live_view_placeholder()
            return

        (x0, y0), (x1, y1) = self.live_roi_points[:2]
        left = min(x0, x1)
        top = min(y0, y1)
        right = max(x0, x1)
        bottom = max(y0, y1)
        width = min(frame_width - left, right - left + 1)
        height = min(frame_height - top, bottom - top + 1)
        self.live_roi_points = []
        self.live_roi_selecting = False
        self.live_roi_button_var.set("Select ROI")
        self._set_roi_fields(left, top, width, height, update_live=True, selected=True)
        self.log(f"Live ROI selected: x={left}, y={top}, width={width}, height={height}. Press Apply Parameters to send it to Hera.")
        self._draw_live_view_placeholder()

    def on_live_mouse_leave(self, _event=None):
        self.live_cursor_image_xy = None
        self.live_cursor_var.set(self._live_cursor_status_text("-"))
        if not self.live_cross_point:
            self._render_live_profiles()
