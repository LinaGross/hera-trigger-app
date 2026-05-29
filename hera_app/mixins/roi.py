import math
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
        with self.live_frame_lock:
            frame_size = self.live_display_frame_size
        if frame_size:
            frame_width, frame_height = frame_size
            self._set_roi_fields(0, 0, frame_width, frame_height, update_live=False, status=f"ROI: full frame {frame_width} x {frame_height}")
            self.fit_live_view()
            self.log(f"Live ROI cleared to full frame: width={frame_width}, height={frame_height}. Press Apply Parameters to send it to Hera.")
        else:
            self.live_roi_status_var.set("ROI: -")
        self._draw_live_view_placeholder()

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
        right = left + width - 1
        bottom = top + height - 1
        self.roi_tl_x_var.set(left)
        self.roi_tl_y_var.set(top)
        self.roi_tr_x_var.set(right)
        self.roi_tr_y_var.set(top)
        self.roi_br_x_var.set(right)
        self.roi_br_y_var.set(bottom)
        self.roi_bl_x_var.set(left)
        self.roi_bl_y_var.set(bottom)
        self.roi_area_var.set(str(width * height))
        if update_live:
            self.live_roi_rect = roi_rect
        if selected:
            self.roi_selection_active = True
            self.selected_export_roi = roi_rect
        self.live_roi_status_var.set(status or f"ROI: x={left}, y={top}, w={width}, h={height}, area={width * height}")
        if update_live:
            self.zoom_live_view_to_roi((left, top, width, height))

    def apply_roi_from_size(self):
        try:
            left, top, width, height = self._read_roi_size_fields()
            self._set_roi_fields(left, top, width, height, update_live=True, selected=True)
            self.log(f"ROI size applied: x={left}, y={top}, width={width}, height={height}. Press Apply Parameters to send it to Hera.")
            self._draw_live_view_placeholder()
        except Exception as exc:
            self.live_roi_status_var.set(f"ROI: {exc}")

    def apply_roi_from_corners(self):
        try:
            x0 = self._read_int_var(self.roi_tl_x_var, "Top-left X")
            y0 = self._read_int_var(self.roi_tl_y_var, "Top-left Y")
            x1 = self._read_int_var(self.roi_br_x_var, "Bottom-right X")
            y1 = self._read_int_var(self.roi_br_y_var, "Bottom-right Y")
            left = min(x0, x1)
            top = min(y0, y1)
            width = abs(x1 - x0) + 1
            height = abs(y1 - y0) + 1
            self._set_roi_fields(left, top, width, height, update_live=True, selected=True)
            self.log(f"ROI corners applied from top-left and bottom-right: x={left}, y={top}, width={width}, height={height}. Press Apply Parameters to send it to Hera.")
            self._draw_live_view_placeholder()
        except Exception as exc:
            self.live_roi_status_var.set(f"ROI: {exc}")

    def apply_roi_from_area(self):
        try:
            area = max(1, int(float(self.roi_area_var.get())))
            left, top, width, height = self._read_roi_size_fields()
            center_x = left + (width - 1) / 2.0
            center_y = top + (height - 1) / 2.0
            new_width = max(1, int(round(math.sqrt(area))))
            new_height = max(1, int(math.ceil(area / new_width)))
            new_left = max(0, int(round(center_x - (new_width - 1) / 2.0)))
            new_top = max(0, int(round(center_y - (new_height - 1) / 2.0)))
            self._set_roi_fields(new_left, new_top, new_width, new_height, update_live=True, selected=True)
            self.log(f"ROI area applied as near-square region: area={area}, width={new_width}, height={new_height}. Press Apply Parameters to send it to Hera.")
            self._draw_live_view_placeholder()
        except Exception as exc:
            self.live_roi_status_var.set(f"ROI: {exc}")

    def _format_live_roi_status(self):
        if not self.live_roi_rect:
            return "ROI: -"
        x, y, width, height = self.live_roi_rect
        return f"ROI: x={x}, y={y}, w={width}, h={height}"

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

        image_x, image_y, frame_width, frame_height = cursor
        if not self.latest_stage_xy:
            self.live_cursor_var.set(self._live_cursor_status_text("sample: stage not connected"))
            return

        try:
            units_per_pixel = float(self.live_pixel_size_var.get())
        except (tk.TclError, ValueError):
            self.live_cursor_var.set(self._live_cursor_status_text("sample: invalid scale"))
            return

        dx_px = image_x - ((frame_width - 1) / 2.0)
        dy_px = image_y - ((frame_height - 1) / 2.0)
        if self.live_swap_xy_var.get():
            dx_px, dy_px = dy_px, dx_px
        if self.live_invert_x_var.get():
            dx_px = -dx_px
        if self.live_invert_y_var.get():
            dy_px = -dy_px

        stage_x, stage_y = self.latest_stage_xy
        sample_x = stage_x + dx_px * units_per_pixel
        sample_y = stage_y + dy_px * units_per_pixel
        self.live_cursor_var.set(self._live_cursor_status_text(f"sample X={sample_x:.3f}, Y={sample_y:.3f}"))
