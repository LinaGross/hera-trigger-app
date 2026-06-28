import math
import threading
import time
import tkinter as tk


class ROIMixin:
    def toggle_live_roi_selection(self):
        self.live_roi_selecting = not self.live_roi_selecting
        self.live_roi_points = []
        if self.live_roi_selecting:
            self.live_roi_button_var.set("Cancel ROI")
            self.live_roi_status_var.set("ROI: click first corner")
        else:
            self.live_roi_button_var.set("Select ROI")
            self.live_roi_status_var.set("ROI: selection cancelled" if self.live_roi_rect is None else self._format_live_roi_status())
        self._draw_live_view_placeholder()

    def clear_live_roi_selection(self):
        self.live_roi_selecting = False
        self.live_roi_points = []
        self.live_roi_rect = None
        self.roi_selection_active = False
        self.selected_export_roi = None
        self.live_view_crop_roi = None
        self._live_crop_offset = (0, 0)
        self.live_zoom_factor = 1.0
        self.live_pan_x = 0.0
        self.live_pan_y = 0.0
        self.live_roi_button_var.set("Select ROI")
        if self.controller and self.controller.connected:
            self.live_roi_status_var.set("ROI: clearing Hera ROI...")
            self.fit_live_view()
            self.log("Live ROI selection cleared; clearing Hera ROI on the camera.")
        else:
            frame_size = getattr(self, "live_sensor_frame_size", None)
            if not frame_size:
                with self.live_frame_lock:
                    frame_size = self.live_display_frame_size
            if frame_size:
                frame_width, frame_height = frame_size
                self._set_roi_fields(0, 0, frame_width, frame_height, update_live=False, status=f"ROI: full frame {frame_width} x {frame_height}")
                self.fit_live_view()
                self.log(f"Live ROI cleared to full frame: width={frame_width}, height={frame_height}.")
            else:
                self.live_roi_status_var.set("ROI: -")
        self._draw_live_view_placeholder()
        self._clear_hera_roi_async()

    def _clear_hera_roi_async(self):
        if not self.controller or not self.controller.connected:
            return
        if getattr(self, "app_state", "") in {
            self.STATE_LABELS["WaitingForTrigger"],
            self.STATE_LABELS["Acquiring"],
            self.STATE_LABELS["ComputingHypercube"],
            self.STATE_LABELS["Saving"],
        }:
            self.log("ROI clear on Hera skipped while acquisition is active.")
            return
        if not self.parameter_apply_lock.acquire(blocking=False):
            self.log("ROI clear on Hera skipped because camera parameters are being applied.")
            return

        def worker():
            live_was_running = False
            try:
                live_was_running = self.controller.is_live_capturing()
                if live_was_running:
                    self._log_async("Stopping Hera live view before clearing ROI.")
                    self.live_accept_frames = False
                    self.controller.stop_live_capture(silent=True)
                    self.controller.wait_for_live_capture_stopped(timeout_sec=5.0)
                    self.controller.unregister_live_callbacks()
                    self._safe_after(0, self._clear_live_view_frame_state)
                self.controller.clear_roi()
                time.sleep(0.2)
                actual_roi = self._normalize_roi_tuple(self.controller.get_roi())
                self.last_applied_roi = None
                if actual_roi and actual_roi[0] == 0 and actual_roi[1] == 0:
                    self.live_sensor_frame_size = (actual_roi[2], actual_roi[3])
                self._log_async(f"Hera ROI cleared immediately. Current ROI: {actual_roi}")
                if actual_roi:
                    x, y, w, h = actual_roi
                    self._safe_after(
                        0,
                        lambda x=x, y=y, w=w, h=h: self._set_roi_fields(
                            x,
                            y,
                            w,
                            h,
                            update_live=False,
                            selected=False,
                            status=f"ROI: full frame {w} x {h}",
                        ),
                    )
            except Exception as exc:
                self._log_async(f"Could not clear Hera ROI immediately: {exc}")
            finally:
                self.parameter_apply_lock.release()
                if live_was_running and self.controller and self.controller.connected:
                    self._log_async("Restarting Hera live view after clearing ROI.")
                    self._safe_after(0, self.start_live_view)

        threading.Thread(target=worker, daemon=True).start()

    def _read_int_var(self, var, name):
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            raise ValueError(f"{name} must be a whole number.")

    def _read_roi_size_fields(self):
        left = self._read_int_var(self.param_vars["roi_x"], "ROI X")
        top = self._read_int_var(self.param_vars["roi_y"], "ROI Y")
        width = max(1, self._read_int_var(self.param_vars["roi_w"], "ROI width"))
        height = max(1, self._read_int_var(self.param_vars["roi_h"], "ROI height"))
        return left, top, width, height

    def _normalize_roi_tuple(self, roi):
        if not roi:
            return None
        left, top, width, height = roi
        return int(left), int(top), max(1, int(width)), max(1, int(height))

    def _format_roi(self, roi):
        roi = self._normalize_roi_tuple(roi)
        if not roi:
            return "-"
        left, top, width, height = roi
        return f"x={left}, y={top}, w={width}, h={height}"

    def _format_roi_short(self, roi):
        roi = self._normalize_roi_tuple(roi)
        if not roi:
            return "-"
        left, top, width, height = roi
        return f"{width}x{height}@{left},{top}"

    def _get_position_roi(self, position):
        return self._normalize_roi_tuple(getattr(position, "roi", None))

    def _capture_current_roi_for_position(self):
        return self._normalize_roi_tuple(self._get_active_roi())

    def _apply_position_roi_to_ui(self, position):
        roi = self._get_position_roi(position)
        self.live_roi_selecting = False
        self.live_roi_points = []
        self.live_roi_button_var.set("Select ROI")
        if roi:
            left, top, width, height = roi
            self._set_roi_fields(
                left,
                top,
                width,
                height,
                update_live=True,
                selected=True,
                status=f"ROI: saved for {position.name}, w={width}, h={height}",
            )
        else:
            self.roi_selection_active = False
            self.selected_export_roi = None
            self.live_roi_rect = None
            self.live_view_crop_roi = None
            self._live_crop_offset = (0, 0)
            self.live_zoom_factor = 1.0
            self.live_pan_x = 0.0
            self.live_pan_y = 0.0
            self.live_roi_status_var.set(f"ROI: no saved ROI for {position.name}")
            self._draw_live_view_placeholder()

    def _get_active_roi(self):
        roi = self._normalize_roi_tuple(self.selected_export_roi) if self.roi_selection_active else None
        if not roi and self.live_roi_rect:
            roi = self._normalize_roi_tuple(self.live_roi_rect)
        if not roi:
            return None
        return roi

    def _set_active_roi(self, roi):
        roi = self._normalize_roi_tuple(roi)
        if not roi:
            return None
        self.roi_selection_active = True
        self.selected_export_roi = roi
        return roi

    def _full_live_frame_size_for_roi(self):
        with self.live_frame_lock:
            frame_info = getattr(self, "live_frame_info", None)
            frame_size = getattr(self, "live_display_frame_size", None)
        roi = self._normalize_roi_tuple(self.selected_export_roi or self.live_roi_rect)

        def valid_size(size):
            return size and len(size) >= 2 and int(size[0]) > 0 and int(size[1]) > 0

        def contains_roi(size):
            if not roi:
                return True
            frame_width, frame_height = int(size[0]), int(size[1])
            left, top, width, height = roi
            return left + width <= frame_width and top + height <= frame_height

        candidates = (
            getattr(self, "live_sensor_frame_size", None),
            getattr(self, "roi_fields_frame_size", None),
            frame_info,
            frame_size,
        )
        for candidate in candidates:
            if valid_size(candidate) and contains_roi(candidate):
                return int(candidate[0]), int(candidate[1])
        for candidate in (frame_info, frame_size, getattr(self, "live_sensor_frame_size", None)):
            if valid_size(candidate):
                return int(candidate[0]), int(candidate[1])
        return None

    def _clip_rect_to_size(self, left, top, width, height, frame_width, frame_height):
        left = max(0, min(int(left), frame_width - 1))
        top = max(0, min(int(top), frame_height - 1))
        width = max(1, min(int(width), frame_width - left))
        height = max(1, min(int(height), frame_height - top))
        return left, top, width, height

    def _raw_roi_to_live_view_bounds(self, left, top, width, height):
        frame_size = self._full_live_frame_size_for_roi()
        if not frame_size:
            return int(left), int(top), max(1, int(width)), max(1, int(height))
        frame_width, frame_height = frame_size
        left, top, width, height = self._clip_rect_to_size(left, top, width, height, frame_width, frame_height)
        right = left + width - 1
        bottom = top + height - 1
        display_points = [
            self._raw_live_xy_to_display_xy(x, y, frame_width, frame_height)[:2]
            for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))
        ]
        xs = [point[0] for point in display_points]
        ys = [point[1] for point in display_points]
        display_left = min(xs)
        display_top = min(ys)
        return display_left, display_top, max(xs) - display_left + 1, max(ys) - display_top + 1

    def _live_view_bounds_to_raw_roi(self, left, top, width, height):
        frame_size = self._full_live_frame_size_for_roi()
        if not frame_size:
            return int(left), int(top), max(1, int(width)), max(1, int(height))
        frame_width, frame_height = frame_size
        display_width, display_height = self._live_display_dimensions(frame_width, frame_height)
        left, top, width, height = self._clip_rect_to_size(left, top, width, height, display_width, display_height)
        right = left + width - 1
        bottom = top + height - 1
        raw_points = [
            self._display_live_xy_to_raw_xy(x, y, frame_width, frame_height)
            for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))
        ]
        xs = [point[0] for point in raw_points]
        ys = [point[1] for point in raw_points]
        raw_left = min(xs)
        raw_top = min(ys)
        return self._clip_rect_to_size(raw_left, raw_top, max(xs) - raw_left + 1, max(ys) - raw_top + 1, frame_width, frame_height)

    def _roi_corner_fields_match_bounds(self, left, top, width, height):
        right = left + width - 1
        bottom = top + height - 1
        try:
            return (
                int(self.roi_tl_x_var.get()) == left
                and int(self.roi_tl_y_var.get()) == top
                and int(self.roi_tr_x_var.get()) == right
                and int(self.roi_tr_y_var.get()) == top
                and int(self.roi_br_x_var.get()) == right
                and int(self.roi_br_y_var.get()) == bottom
                and int(self.roi_bl_x_var.get()) == left
                and int(self.roi_bl_y_var.get()) == bottom
            )
        except Exception:
            return False

    def _maybe_initialize_roi_fields_from_live_frame(self, frame_width, frame_height):
        if frame_width <= 0 or frame_height <= 0:
            return
        frame_size = (int(frame_width), int(frame_height))
        if getattr(self, "roi_selection_active", False) or self.live_roi_rect or self.live_roi_selecting:
            return
        previous_size = getattr(self, "roi_fields_frame_size", None)
        if getattr(self, "roi_fields_initialized_from_live_frame", False) and previous_size == frame_size:
            return
        display_width, display_height = self._live_display_dimensions(*frame_size)
        corner_fields_are_default = self._roi_corner_fields_match_bounds(0, 0, 512, 512)
        corner_fields_are_previous_full_frame = False
        if previous_size:
            prev_display_width, prev_display_height = self._live_display_dimensions(*previous_size)
            corner_fields_are_previous_full_frame = self._roi_corner_fields_match_bounds(
                0,
                0,
                prev_display_width,
                prev_display_height,
            )
        if not corner_fields_are_default and not corner_fields_are_previous_full_frame:
            return
        self._set_roi_fields(
            0,
            0,
            frame_width,
            frame_height,
            update_live=False,
            selected=False,
            status=f"ROI: full live view {display_width} x {display_height}",
        )
        self.roi_fields_initialized_from_live_frame = True
        self.roi_fields_frame_size = frame_size

    def _set_roi_fields(self, left, top, width, height, update_live=True, status=None, selected=False):
        left = int(left)
        top = int(top)
        width = max(1, int(width))
        height = max(1, int(height))
        roi_rect = (left, top, width, height)
        self.param_vars["roi_x"].set(left)
        self.param_vars["roi_y"].set(top)
        self.param_vars["roi_w"].set(width)
        self.param_vars["roi_h"].set(height)
        display_left, display_top, display_width, display_height = self._raw_roi_to_live_view_bounds(left, top, width, height)
        display_right = display_left + display_width - 1
        display_bottom = display_top + display_height - 1
        self.roi_tl_x_var.set(display_left)
        self.roi_tl_y_var.set(display_top)
        self.roi_tr_x_var.set(display_right)
        self.roi_tr_y_var.set(display_top)
        self.roi_br_x_var.set(display_right)
        self.roi_br_y_var.set(display_bottom)
        self.roi_bl_x_var.set(display_left)
        self.roi_bl_y_var.set(display_bottom)
        self.roi_area_var.set(str(width * height))
        if update_live:
            self.live_roi_rect = roi_rect
        if selected:
            self._set_active_roi(roi_rect)
        self.live_roi_status_var.set(status or f"ROI: w={width}, h={height}")
        if update_live:
            self.zoom_live_view_to_roi((left, top, width, height))

    def apply_roi_from_size(self):
        try:
            left, top, width, height = self._read_roi_size_fields()
            self._set_roi_fields(left, top, width, height, update_live=True, selected=True)
            self.log(
                f"ROI size applied: x={left}, y={top}, width={width}, height={height}. "
                "Next acquisition will use it; Add/Update Position saves it with the site."
            )
            self._draw_live_view_placeholder()
        except Exception as exc:
            self.live_roi_status_var.set(f"ROI: {exc}")

    def _read_roi_corner_points(self):
        return (
            (
                self._read_int_var(self.roi_tl_x_var, "Top-left X"),
                self._read_int_var(self.roi_tl_y_var, "Top-left Y"),
            ),
            (
                self._read_int_var(self.roi_tr_x_var, "Top-right X"),
                self._read_int_var(self.roi_tr_y_var, "Top-right Y"),
            ),
            (
                self._read_int_var(self.roi_br_x_var, "Bottom-right X"),
                self._read_int_var(self.roi_br_y_var, "Bottom-right Y"),
            ),
            (
                self._read_int_var(self.roi_bl_x_var, "Bottom-left X"),
                self._read_int_var(self.roi_bl_y_var, "Bottom-left Y"),
            ),
        )

    def _roi_bounds_from_corner_fields(self):
        points = self._read_roi_corner_points()
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        left = min(xs)
        top = min(ys)
        width = max(xs) - left + 1
        height = max(ys) - top + 1
        return left, top, width, height

    def apply_roi_from_corners(self):
        try:
            view_left, view_top, view_width, view_height = self._roi_bounds_from_corner_fields()
            left, top, width, height = self._live_view_bounds_to_raw_roi(view_left, view_top, view_width, view_height)
            self._set_roi_fields(left, top, width, height, update_live=True, selected=True)
            self.log(
                f"ROI corners applied: view x={view_left}, y={view_top}, width={view_width}, height={view_height}; "
                f"camera x={left}, y={top}, width={width}, height={height}. Next acquisition will use it; "
                "Add/Update Position saves it with the site."
            )
            self._draw_live_view_placeholder()
        except Exception as exc:
            self.live_roi_status_var.set(f"ROI: {exc}")

    def apply_square_roi_from_corners(self):
        try:
            view_left, view_top, view_width, view_height = self._roi_bounds_from_corner_fields()
            side = max(view_width, view_height)
            left, top, width, height = self._live_view_bounds_to_raw_roi(view_left, view_top, side, side)
            self._set_roi_fields(left, top, width, height, update_live=True, selected=True)
            self.log(
                f"Square ROI applied: view x={view_left}, y={view_top}, side={side}; "
                f"camera x={left}, y={top}, width={width}, height={height}. "
                "Next acquisition will use it; Add/Update Position saves it with the site."
            )
            self._draw_live_view_placeholder()
        except Exception as exc:
            self.live_roi_status_var.set(f"ROI: {exc}")

    def apply_roi_from_area(self):
        try:
            area = max(1, int(float(self.roi_area_var.get())))
            view_left = self._read_int_var(self.roi_tl_x_var, "Top-left X")
            view_top = self._read_int_var(self.roi_tl_y_var, "Top-left Y")
            side = max(1, int(round(math.sqrt(area))))
            left, top, width, height = self._live_view_bounds_to_raw_roi(view_left, view_top, side, side)
            self._set_roi_fields(left, top, width, height, update_live=True, selected=True)
            self.log(
                f"Square ROI area applied: view x={view_left}, y={view_top}, target area={area}, side={side}, "
                f"camera x={left}, y={top}, actual area={width * height}. "
                "Next acquisition will use it; Add/Update Position saves it with the site."
            )
            self._draw_live_view_placeholder()
        except Exception as exc:
            self.live_roi_status_var.set(f"ROI: {exc}")

    def _format_live_roi_status(self):
        if not self.live_roi_rect:
            return "ROI: -"
        left, top, width, height = self.live_roi_rect
        return f"ROI: w={width}, h={height}"

    def _raw_live_point_to_view_xy(self, image_x, image_y):
        frame_size = self._full_live_frame_size_for_roi()
        if not frame_size:
            return int(image_x), int(image_y)
        frame_width, frame_height = frame_size
        display_x, display_y, _display_width, _display_height = self._raw_live_xy_to_display_xy(
            image_x,
            image_y,
            frame_width,
            frame_height,
        )
        return display_x, display_y

    def _image_xy_to_canvas_xy(self, image_x, image_y):
        with self.live_frame_lock:
            rect = self.live_display_rect
            frame_size = self.live_display_frame_size
        if not rect or not frame_size:
            return None
        left, top, out_w, out_h = rect
        frame_width, frame_height = frame_size
        if frame_width <= 0 or frame_height <= 0:
            return None
        display_x, display_y, display_width, display_height = self._raw_live_xy_to_display_xy(
            image_x,
            image_y,
            frame_width,
            frame_height,
        )
        canvas_x = left + (display_x + 0.5) * out_w / display_width
        canvas_y = top + (display_y + 0.5) * out_h / display_height
        return canvas_x, canvas_y

    def _draw_live_roi_overlay(self, canvas):
        if self.live_view_crop_roi:
            return
        corners = []
        if self.live_roi_rect:
            x, y, width, height = self.live_roi_rect
            corners = [(x, y), (x + width - 1, y + height - 1)]
        elif self.live_roi_selecting and self.live_roi_points:
            x, y = self.live_roi_points[0]
            corners = [(x, y), (x, y)]
        if not corners:
            return

        p0 = self._image_xy_to_canvas_xy(*corners[0])
        p1 = self._image_xy_to_canvas_xy(*corners[1])
        if not p0 or not p1:
            return
        x0, y0 = p0
        x1, y1 = p1
        if abs(x1 - x0) < 2 and abs(y1 - y0) < 2:
            canvas.create_line(x0 - 8, y0, x0 + 8, y0, fill="#7ad97a", width=2)
            canvas.create_line(x0, y0 - 8, x0, y0 + 8, fill="#7ad97a", width=2)
            return
        canvas.create_rectangle(x0, y0, x1, y1, outline="#7ad97a", width=2)

    def _update_live_cursor_readout(self):
        cursor = self.live_cursor_image_xy
        if not cursor:
            self.live_cursor_var.set(self._live_cursor_status_text("-"))
            return

        image_x, image_y, _frame_width, _frame_height = cursor
        view_x, view_y = self._raw_live_point_to_view_xy(image_x, image_y)
        self.live_cursor_var.set(self._live_cursor_status_text(f"X={view_x}, Y={view_y}"))
