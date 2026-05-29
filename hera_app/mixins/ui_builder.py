import os
import subprocess
import tkinter as tk
from tkinter import filedialog, ttk


class UIBuilderMixin:
    def _build_ui(self):
        shell = tk.Frame(self, bg=self.theme["bg"])
        shell.pack(fill="both", expand=True, padx=8, pady=8)

        toolbar = tk.Frame(shell, bg=self.theme["bg"])
        toolbar.pack(fill="x", pady=(0, 5))
        title = tk.Label(toolbar, text="HERA + Tango Trigger", font=("Segoe UI Semibold", 14), bg=self.theme["bg"], fg=self.theme["title"])
        title.pack(side="left")
        subtitle = tk.Label(toolbar, text="Stage-guided hyperspectral acquisition", font=("Segoe UI", 9), bg=self.theme["bg"], fg=self.theme["muted"])
        subtitle.pack(side="left", padx=(8, 0), pady=(3, 0))
        tk.Button(toolbar, textvariable=self.theme_button_var, command=self.toggle_theme_mode).pack(side="right")

        body = tk.PanedWindow(shell, orient="horizontal", sashwidth=6, sashrelief="flat", bg=self.theme["bg"], bd=0)
        body.pack(fill="both", expand=True)

        left = self._make_scroll_column(body, width=278)
        body.add(left, minsize=218, width=278, stretch="never")

        center = tk.Frame(body, bg=self.theme["bg"], padx=6)
        center.grid_rowconfigure(1, weight=1)
        center.grid_columnconfigure(0, weight=1)
        body.add(center, minsize=560, stretch="always")

        right = self._make_scroll_column(body, width=260)
        body.add(right, minsize=210, width=260, stretch="never")

        self._build_left_controls(left.content)
        self._build_center_workspace(center)
        self._build_right_controls(right.content)
        self._apply_theme_recursive(shell)
        self._install_activation_shortcuts(shell)
        self._install_auto_apply_traces()

    def _make_scroll_column(self, parent, width):
        outer = tk.Frame(parent, bg=self.theme["bg"])
        scroll = ttk.Scrollbar(outer, orient="vertical")
        scroll.pack(side="right", fill="y")
        canvas = tk.Canvas(outer, bg=self.theme["bg"], highlightthickness=0, width=width, yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.config(command=canvas.yview)
        content = tk.Frame(canvas, bg=self.theme["bg"])
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(window_id, width=e.width))

        def bind_wheel(_event):
            canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        canvas.bind("<Enter>", bind_wheel)
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))
        outer.content = content
        return outer

    def _walk_widgets(self, widget):
        yield widget
        for child in widget.winfo_children():
            yield from self._walk_widgets(child)

    def _install_activation_shortcuts(self, root):
        for widget in self._walk_widgets(root):
            cls = widget.winfo_class()
            if cls in {"Button", "Checkbutton"}:
                try:
                    widget.configure(takefocus=1)
                except tk.TclError:
                    pass
                widget.bind("<Button-1>", lambda _e, w=widget: w.focus_set(), add="+")
                widget.bind("<Return>", lambda _e, w=widget: self._invoke_widget_from_key(w), add="+")
                widget.bind("<KP_Enter>", lambda _e, w=widget: self._invoke_widget_from_key(w), add="+")
            elif cls == "Entry":
                try:
                    widget.configure(takefocus=1)
                except tk.TclError:
                    pass
                widget.bind("<FocusIn>", lambda _e, w=widget: self._entry_focus_in(w), add="+")
                widget.bind("<Return>", lambda _e, w=widget: self._commit_entry_from_key(w), add="+")
                widget.bind("<KP_Enter>", lambda _e, w=widget: self._commit_entry_from_key(w), add="+")
                widget.bind("<FocusOut>", lambda _e, w=widget: self._commit_entry_from_focus_out(w), add="+")
            elif cls in {"Menubutton", "Scale"}:
                try:
                    widget.configure(takefocus=1)
                except tk.TclError:
                    pass
                widget.bind("<Button-1>", lambda _e, w=widget: w.focus_set(), add="+")

    def _invoke_widget_from_key(self, widget):
        try:
            if str(widget.cget("state")) == "disabled":
                return "break"
            widget.invoke()
        except Exception:
            pass
        return "break"

    def _entry_focus_in(self, widget):
        self._remember_entry_text(widget)
        self._select_entry_text(widget)

    def _select_entry_text(self, widget):
        def select_all():
            try:
                widget.selection_range(0, "end")
                widget.icursor("end")
            except tk.TclError:
                pass
        widget.after_idle(select_all)

    def _entry_text(self, widget):
        try:
            return widget.get()
        except tk.TclError:
            return ""

    def _remember_entry_text(self, widget):
        text = self._entry_text(widget)
        try:
            widget._hera_focus_text = text
        except Exception:
            pass

    def _entry_textvariable_name(self, widget):
        try:
            return str(widget.cget("textvariable"))
        except tk.TclError:
            return ""

    def _var_matches(self, var_name, *variables):
        return any(var is not None and str(var) == var_name for var in variables)

    def _commit_entry_from_key(self, widget):
        return self._commit_entry(widget, for_focusout=False)

    def _commit_entry_from_focus_out(self, widget):
        self._commit_entry(widget, for_focusout=True)
        return None

    def _commit_entry(self, widget, for_focusout=False):
        if getattr(self, "is_closing", False):
            return "break" if not for_focusout else None
        command = self._entry_commit_command(widget, for_focusout=for_focusout)
        current_text = self._entry_text(widget)
        if for_focusout:
            previous_text = getattr(widget, "_hera_focus_text", current_text)
            if current_text == previous_text:
                return None
        if command:
            try:
                command()
            except Exception as exc:
                action = "Field update" if for_focusout else "Enter"
                self.log(f"{action} action failed: {exc}")
        try:
            widget._hera_focus_text = current_text
        except Exception:
            pass
        return "break" if not for_focusout else None

    def _entry_commit_command(self, widget, for_focusout=False):
        var_name = self._entry_textvariable_name(widget)
        if not var_name:
            return None
        if self._var_matches(
            var_name,
            self.param_vars.get("gain"),
            self.param_vars.get("exposure"),
            self.param_vars.get("bands"),
            self.param_vars.get("stabilization"),
        ):
            return self.apply_parameters_async
        if self._var_matches(
            var_name,
            self.param_vars.get("roi_x"),
            self.param_vars.get("roi_y"),
            self.param_vars.get("roi_w"),
            self.param_vars.get("roi_h"),
        ):
            return self.apply_roi_from_size
        if self._var_matches(
            var_name,
            self.roi_tl_x_var,
            self.roi_tl_y_var,
            self.roi_tr_x_var,
            self.roi_tr_y_var,
            self.roi_br_x_var,
            self.roi_br_y_var,
            self.roi_bl_x_var,
            self.roi_bl_y_var,
        ):
            if for_focusout:
                return None
            return self.apply_roi_from_corners
        if self._var_matches(var_name, self.roi_area_var):
            if for_focusout:
                return None
            return self.apply_roi_from_area
        if self._var_matches(var_name, self.position_name_var):
            if for_focusout:
                return None
            return self.add_current_position
        if self._var_matches(var_name, self.selected_name_var):
            if for_focusout:
                return None
            return self.rename_selected_position if self.selected_position_index is not None else self.add_current_position
        if self._var_matches(var_name, self.selected_x_var, self.selected_y_var, self.selected_z_var):
            if for_focusout:
                return None
            return self.apply_selected_position_edits
        if self._var_matches(var_name, self.stage_speed_var):
            return self.apply_stage_motion_settings
        if self._var_matches(var_name, self.hyper_band_jump_var):
            return self.jump_to_hyper_band
        return None

    def _install_auto_apply_traces(self):
        if getattr(self, "_auto_apply_traces_installed", False):
            return
        self._auto_apply_traces_installed = True
        for var in (
            self.param_vars.get("scan_mode"),
            self.param_vars.get("averages"),
            self.param_vars.get("binning"),
            self.param_vars.get("data_type"),
            self.hdr_enabled_var,
        ):
            if var is None:
                continue
            try:
                var.trace_add("write", lambda *_args: self._schedule_auto_apply_parameters())
            except Exception:
                pass

    def _schedule_auto_apply_parameters(self):
        if not getattr(self, "controller", None) or not self.controller.connected:
            return
        job = getattr(self, "_auto_apply_parameters_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._auto_apply_parameters_job = self._safe_after(350, self._run_auto_apply_parameters)

    def _run_auto_apply_parameters(self):
        self._auto_apply_parameters_job = None
        if not getattr(self, "controller", None) or not self.controller.connected:
            return
        if self.parameter_apply_lock.locked():
            self._auto_apply_parameters_job = self._safe_after(350, self._run_auto_apply_parameters)
            return
        self.apply_parameters_async()

    def _param_entry(self, parent, row, label_text, key, default, width=10):
        tk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", pady=1)
        if isinstance(default, int):
            self.param_vars[key] = tk.IntVar(value=default)
        else:
            self.param_vars[key] = tk.DoubleVar(value=default)
        tk.Entry(parent, textvariable=self.param_vars[key], width=width).grid(row=row, column=1, sticky="ew", padx=(4, 0), pady=1)

    def _param_menu(self, parent, row, label_text, key, default, options):
        tk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", pady=1)
        self.param_vars[key] = tk.StringVar(value=default)
        tk.OptionMenu(parent, self.param_vars[key], *list(options)).grid(row=row, column=1, sticky="ew", padx=(4, 0), pady=1)

    def _build_left_controls(self, parent):
        self.param_vars = {}
        self.stage_speed_var = tk.DoubleVar(value=20.0)
        self.stage_dwell_var = tk.DoubleVar(value=0.0)
        self.live_pixel_size_var = tk.DoubleVar(value=1.0)
        self.live_invert_x_var = tk.BooleanVar(value=False)
        self.live_invert_y_var = tk.BooleanVar(value=False)
        self.live_swap_xy_var = tk.BooleanVar(value=False)
        self.position_name_var = tk.StringVar()
        self.selected_name_var = tk.StringVar()
        self.selected_x_var = tk.StringVar()
        self.selected_y_var = tk.StringVar()
        self.selected_z_var = tk.StringVar()
        self.roi_tl_x_var = tk.IntVar(value=0)
        self.roi_tl_y_var = tk.IntVar(value=0)
        self.roi_tr_x_var = tk.IntVar(value=511)
        self.roi_tr_y_var = tk.IntVar(value=0)
        self.roi_br_x_var = tk.IntVar(value=511)
        self.roi_br_y_var = tk.IntVar(value=511)
        self.roi_bl_x_var = tk.IntVar(value=0)
        self.roi_bl_y_var = tk.IntVar(value=511)
        self.roi_area_var = tk.StringVar(value=str(512 * 512))
        self.app_state_var = tk.StringVar(value=self.app_state)

        nb = ttk.Notebook(parent, style="Left.TNotebook")
        nb.pack(fill="both", expand=True)
        camera_tab = tk.Frame(nb, padx=2, pady=2)
        stage_tab = tk.Frame(nb, padx=2, pady=2)
        nb.add(camera_tab, text="  Camera  ")
        nb.add(stage_tab, text="  Stage  ")

        status = tk.LabelFrame(camera_tab, text="Status", padx=6, pady=5)
        status.pack(fill="x", pady=(0, 6))
        for text, var in (
            ("License", self.license_var),
            ("Live", self.live_view_status_var),
            ("Last export", self.last_export_var),
        ):
            row = tk.Frame(status)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{text}:", fg=self.theme["muted"], width=9, anchor="w").pack(side="left")
            tk.Label(row, textvariable=var, anchor="w", wraplength=185, justify="left").pack(side="left", fill="x", expand=True)
        cursor_row = tk.Frame(status)
        cursor_row.pack(fill="x", pady=1)
        tk.Label(cursor_row, text="Cursor:", fg=self.theme["muted"], width=9, anchor="w").pack(side="left")
        tk.Label(
            cursor_row,
            textvariable=self.live_cursor_var,
            anchor="w",
            width=30,
            justify="left",
            font=("Consolas", 8),
        ).pack(side="left", fill="x", expand=True)
        btns = tk.Frame(status)
        btns.pack(fill="x", pady=(6, 0))
        tk.Button(btns, text="Preflight", command=self.preflight_check).pack(side="left", padx=(0, 4))
        tk.Button(btns, text="Live Status", command=self.debug_live_status).pack(side="left", padx=(0, 4))
        tk.Button(btns, text="Restart Live", command=self.restart_live_view).pack(side="left")

        exposure = tk.LabelFrame(camera_tab, text="Exposure", padx=6, pady=5)
        exposure.pack(fill="x", pady=(0, 6))
        exposure.grid_columnconfigure(1, weight=1)
        self._param_entry(exposure, 0, "Gain [dB]:", "gain", 0.0)
        self._param_entry(exposure, 1, "Exposure [ms]:", "exposure", 1.0)
        tk.Checkbutton(exposure, text="HDR", variable=self.hdr_enabled_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        roi = tk.LabelFrame(camera_tab, text="ROI", padx=6, pady=5)
        roi.pack(fill="x", pady=(0, 6))
        roi.grid_columnconfigure(1, weight=1)
        for _k, _v in [("roi_x", 0), ("roi_y", 0), ("roi_w", 512), ("roi_h", 512)]:
            self.param_vars[_k] = tk.IntVar(value=_v)
        corners = tk.LabelFrame(roi, text="Corners", padx=4, pady=4)
        corners.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 0))
        corner_rows = [
            ("Top Left", self.roi_tl_x_var, self.roi_tl_y_var),
            ("Top Right", self.roi_tr_x_var, self.roi_tr_y_var),
            ("Bottom Right", self.roi_br_x_var, self.roi_br_y_var),
            ("Bottom Left", self.roi_bl_x_var, self.roi_bl_y_var),
        ]
        for corner_row, (label, x_var, y_var) in enumerate(corner_rows):
            tk.Label(corners, text=label).grid(row=corner_row, column=0, sticky="w", pady=1)
            tk.Label(corners, text="X").grid(row=corner_row, column=1, sticky="e", padx=(4, 1))
            tk.Entry(corners, textvariable=x_var, width=6).grid(row=corner_row, column=2, sticky="w")
            tk.Label(corners, text="Y").grid(row=corner_row, column=3, sticky="e", padx=(4, 1))
            tk.Entry(corners, textvariable=y_var, width=6).grid(row=corner_row, column=4, sticky="w")
        area_row = tk.Frame(roi)
        area_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        tk.Label(area_row, text="Area (px2)").pack(side="left")
        tk.Entry(area_row, textvariable=self.roi_area_var, width=8).pack(side="left", padx=(4, 0))
        tk.Button(area_row, text="Apply", command=self.apply_roi_from_area).pack(side="left", padx=(4, 0))
        roi_actions = tk.Frame(roi)
        roi_actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        tk.Button(roi_actions, text="Apply Box", command=self.apply_roi_from_corners).pack(side="left", padx=(0, 3))
        tk.Button(roi_actions, text="Square", command=self.apply_square_roi_from_corners).pack(side="left", padx=(0, 3))
        tk.Button(roi_actions, textvariable=self.live_roi_button_var, command=self.toggle_live_roi_selection).pack(side="left", padx=(0, 3))
        tk.Button(roi_actions, text="Clear", command=self.clear_live_roi_selection).pack(side="left")
        tk.Label(roi, textvariable=self.live_roi_status_var, fg=self.theme["muted"], wraplength=215, justify="left").grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        xyz = tk.LabelFrame(stage_tab, text="XYZ Position", padx=6, pady=5)
        xyz.pack(fill="x", pady=(0, 6))
        xyz.grid_columnconfigure(0, weight=1)
        self.stage_status_var = tk.StringVar(value="Stage: not connected")
        self.stage_version_var = tk.StringVar(value="Controller: -")
        self.stage_position_var = tk.StringVar(value="X: -, Y: -")
        tk.Label(xyz, textvariable=self.stage_status_var, font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.current_x_label = tk.Label(xyz, text="X: -")
        self.current_y_label = tk.Label(xyz, text="Y: -")
        self.current_z_label = tk.Label(xyz, textvariable=self.nis_z_current_z_var)

        position_panel = tk.Frame(xyz)
        position_panel.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        tk.Label(position_panel, text="Position name").pack(anchor="w")
        tk.Entry(position_panel, textvariable=self.selected_name_var, width=20).pack(fill="x", pady=(1, 4))
        for text, command in (
            ("Add Current Position", self.add_current_position),
            ("Update Selected Position", self.update_selected_position),
            ("Delete Selected Row", self.delete_selected_position),
            ("Rename Selected Position", self.rename_selected_position),
            ("Go To Selected Position", self.goto_selected_position),
            ("Reconnect Stage", self.reconnect_stage),
        ):
            tk.Button(position_panel, text=text, command=command).pack(fill="x", pady=1)

        saved = tk.LabelFrame(stage_tab, text="Saved Positions", padx=6, pady=5)
        saved.pack(fill="both", expand=True, pady=(0, 6))
        tree_wrap = tk.Frame(saved)
        tree_wrap.pack(fill="both", expand=True)
        self.positions_tree = ttk.Treeview(tree_wrap, columns=("name", "x", "y", "z", "roi"), show="headings", height=7, style="Dark.Treeview")
        for name, label, width, anchor in (
            ("name", "Name", 74, "w"),
            ("x", "X", 46, "e"),
            ("y", "Y", 46, "e"),
            ("z", "Z", 46, "e"),
            ("roi", "ROI", 34, "center"),
        ):
            self.positions_tree.heading(name, text=label)
            self.positions_tree.column(name, width=width, anchor=anchor)
        scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.positions_tree.yview)
        self.positions_tree.configure(yscrollcommand=scroll.set)
        self.positions_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.positions_tree.bind("<<TreeviewSelect>>", self.on_position_selected)

        self._build_nis_z_ui(stage_tab)

    def _build_center_workspace(self, parent):
        spectral = tk.LabelFrame(parent, text="Control Bar", padx=4, pady=3)
        spectral.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        compact_font = ("Segoe UI", 8)
        for col in range(13):
            spectral.grid_columnconfigure(col, weight=1 if col % 2 else 0, minsize=0)
        self.param_vars["scan_mode"] = tk.StringVar(value="Medium")
        self.param_vars["trigger_mode"] = tk.StringVar(value="Internal")
        self.param_vars["averages"] = tk.IntVar(value=1)
        self.param_vars["stabilization"] = tk.IntVar(value=0)
        self.param_vars["bands"] = tk.IntVar(value=0)
        self.param_vars["binning"] = tk.StringVar(value="None")
        self.param_vars["data_type"] = tk.StringVar(value="SinglePrecision")
        controls = [
            ("Spectral", "scan_mode", "menu", self.SCAN_MODES.keys(), 6),
            ("Bands", "bands", "entry", None, 4),
            ("Avg", "averages", "menu", ("1", "2", "3"), 2),
            ("Bin", "binning", "menu", self.BINNING_OPTIONS.keys(), 5),
            ("Data", "data_type", "menu", self.DATA_TYPES.keys(), 10),
        ]
        for index, (label, key, kind, options, width) in enumerate(controls):
            label_col = index * 2
            control_col = label_col + 1
            tk.Label(spectral, text=label, font=compact_font).grid(row=0, column=label_col, sticky="e", padx=(0, 2), pady=0)
            if kind == "menu":
                menu = tk.OptionMenu(spectral, self.param_vars[key], *list(options))
                menu.config(width=width, font=compact_font, padx=1, pady=0, highlightthickness=0)
                menu["menu"].config(font=compact_font)
                menu.grid(row=0, column=control_col, sticky="ew", padx=(0, 4), pady=0)
            else:
                tk.Entry(spectral, textvariable=self.param_vars[key], width=width, font=compact_font).grid(
                    row=0,
                    column=control_col,
                    sticky="ew",
                    padx=(0, 4),
                    pady=0,
                )
        flatfield_bar = tk.Frame(spectral)
        flatfield_bar.grid(row=1, column=0, columnspan=12, sticky="ew", pady=(3, 0))
        tk.Label(flatfield_bar, text="Flatfield", font=("Segoe UI Semibold", 8)).pack(side="left", padx=(0, 4))
        tk.Label(flatfield_bar, textvariable=self.flatfield_status_var, font=compact_font, width=23, anchor="w").pack(side="left", padx=(0, 3))
        tk.Button(flatfield_bar, text="Acquire", command=self.start_flatfield_acquisition, font=compact_font, padx=4, pady=0).pack(side="left", padx=(0, 2))
        tk.Button(flatfield_bar, text="Clear", command=self.clear_flatfield, font=compact_font, padx=4, pady=0).pack(side="left", padx=(0, 4))

        self._build_views_and_log(parent)

    def _build_views_and_log(self, parent):
        views_frame = tk.LabelFrame(parent, text="Live View / Hyperspectral View", padx=6, pady=6)
        views_frame.grid(row=1, column=0, sticky="nsew")
        views_frame.grid_rowconfigure(0, weight=1)
        views_frame.grid_columnconfigure(0, weight=1)
        notebook = ttk.Notebook(views_frame)
        notebook.grid(row=0, column=0, sticky="nsew")

        live_tab = tk.Frame(notebook, bg=self.theme["panel"])
        hyper_tab = tk.Frame(notebook, bg=self.theme["panel"])
        notebook.add(live_tab, text="Live View")
        notebook.add(hyper_tab, text="Hyperspectral View")

        live_controls = tk.Frame(live_tab, bg=self.theme["panel"])
        live_controls.pack(fill="x", padx=6, pady=(6, 3))
        live_display_bar = tk.Frame(live_controls, bg=self.theme["panel"])
        live_display_bar.pack(fill="x")
        tk.Checkbutton(live_display_bar, text="Autocontrast", variable=self.live_autocontrast_var,
                       command=lambda: self._schedule_live_render(force=True),
                       bg=self.theme["panel"], fg=self.theme["text"], selectcolor=self.theme["field"],
                       activebackground=self.theme["panel"]).pack(side="left", padx=(8, 0))
        tk.Checkbutton(live_display_bar, text="Show Saturation", variable=self.live_show_saturation_var,
                       command=lambda: self._schedule_live_render(force=True),
                       bg=self.theme["panel"], fg=self.theme["text"], selectcolor=self.theme["field"],
                       activebackground=self.theme["panel"]).pack(side="left", padx=(6, 0))
        tk.Checkbutton(live_display_bar, text="Cross", variable=self.live_cross_enabled_var,
                       command=self.toggle_live_cross,
                       bg=self.theme["panel"], fg=self.theme["text"], selectcolor=self.theme["field"],
                       activebackground=self.theme["panel"]).pack(side="left", padx=(6, 0))
        tk.Label(live_display_bar, textvariable=self.live_profile_status_var, fg="#9aa6b2", bg=self.theme["panel"]).pack(side="left", padx=(7, 3))
        tk.Label(live_display_bar, textvariable=self.live_gamma_label_var, fg="#9aa6b2", bg=self.theme["panel"]).pack(side="left", padx=(7, 3))
        tk.Scale(live_display_bar, variable=self.live_gamma_var, from_=0.2, to=3.0, resolution=0.1,
                 orient="horizontal", length=90, showvalue=False, command=self.on_live_gamma_change,
                 bg=self.theme["panel"], fg=self.theme["text"], troughcolor=self.theme["field"],
                 highlightthickness=0).pack(side="left")
        tk.Button(live_display_bar, text="Reset Gamma", command=self.reset_live_gamma).pack(side="left", padx=(4, 0))
        tk.Button(live_display_bar, text="Snapshot", command=self.snapshot_live_view).pack(side="left", padx=(5, 0))
        tk.Label(live_display_bar, textvariable=self.live_zoom_label_var, fg="#9aa6b2", bg=self.theme["panel"]).pack(side="left", padx=(8, 3))
        tk.Button(live_display_bar, text="-", width=2, command=lambda: self.zoom_live_view(1 / 1.25)).pack(side="left")
        tk.Button(live_display_bar, text="Fit", command=self.fit_live_view).pack(side="left", padx=(4, 0))
        tk.Button(live_display_bar, text="+", width=2, command=lambda: self.zoom_live_view(1.25)).pack(side="left", padx=(4, 0))
        live_profile_grid = tk.Frame(live_tab, bg=self.theme["panel"])
        live_profile_grid.pack(fill="both", expand=True)
        live_profile_grid.grid_rowconfigure(0, weight=1)
        live_profile_grid.grid_columnconfigure(0, weight=1)
        self.live_view_canvas = tk.Canvas(live_profile_grid, bg=self.theme["canvas"], highlightthickness=0)
        self.live_view_canvas.bind("<Motion>", self.on_live_mouse_move)
        self.live_view_canvas.bind("<Button-1>", self.on_live_mouse_click)
        self.live_view_canvas.bind("<MouseWheel>", self.on_live_mousewheel)
        self.live_view_canvas.bind("<Button-4>", lambda event: self.zoom_live_view(1.25, event))
        self.live_view_canvas.bind("<Button-5>", lambda event: self.zoom_live_view(1 / 1.25, event))
        self.live_view_canvas.bind("<ButtonPress-3>", self.start_live_pan)
        self.live_view_canvas.bind("<B3-Motion>", self.on_live_pan_drag)
        self.live_view_canvas.bind("<ButtonRelease-3>", self.end_live_pan)
        self.live_view_canvas.bind("<Leave>", self.on_live_mouse_leave)
        self.live_view_canvas.grid(row=0, column=0, sticky="nsew")
        self.live_vertical_profile_canvas = tk.Canvas(live_profile_grid, bg=self.theme["canvas"], highlightthickness=0, width=96)
        self.live_vertical_profile_canvas.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.live_horizontal_profile_canvas = tk.Canvas(live_profile_grid, bg=self.theme["canvas"], highlightthickness=0, height=86)
        self.live_horizontal_profile_canvas.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        hyper_controls = tk.Frame(hyper_tab, bg=self.theme["panel"])
        hyper_controls.pack(fill="x", padx=6, pady=(6, 3))
        tk.Button(hyper_controls, text="Prev Band", command=lambda: self.step_hyper_band(-1)).pack(side="left", padx=(0, 5))
        tk.Label(hyper_controls, textvariable=self.current_hyper_band_var, fg="#e7edf5").pack(side="left")
        ttk.Separator(hyper_controls, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=8)
        tk.Label(hyper_controls, textvariable=self.current_hyper_wavelength_var, fg="#9aa6b2").pack(side="left")
        show_wrap = tk.Frame(hyper_controls, bg=self.theme["panel"])
        show_wrap.pack(side="left", padx=(8, 0))
        tk.Label(show_wrap, text="Show", fg="#9aa6b2", bg=self.theme["panel"]).pack(side="left", padx=(0, 3))
        show_menu = tk.OptionMenu(
            show_wrap,
            self.hyper_display_mode_var,
            "Normalized",
            "Raw",
            "Flatfield",
            command=lambda _value: self.on_hyper_display_mode_changed(),
        )
        show_menu.config(width=9, padx=1, pady=0, highlightthickness=0)
        show_menu.pack(side="left")
        tk.Checkbutton(
            hyper_controls,
            text="Cross",
            variable=self.hyper_cross_enabled_var,
            command=self.render_current_hyper_band,
            bg=self.theme["panel"],
            fg=self.theme["text"],
            selectcolor=self.theme["field"],
            activebackground=self.theme["panel"],
        ).pack(side="left", padx=(8, 0))
        jump_wrap = tk.Frame(hyper_controls, bg=self.theme["panel"])
        jump_wrap.pack(side="right", padx=(6, 0))
        tk.Button(jump_wrap, text="Go", command=self.jump_to_hyper_band).pack(side="right")
        tk.Entry(jump_wrap, textvariable=self.hyper_band_jump_var, width=5).pack(side="right", padx=(0, 4))
        tk.Label(jump_wrap, text="Band", fg="#9aa6b2").pack(side="right", padx=(0, 4))
        tk.Button(hyper_controls, text="Next Band", command=lambda: self.step_hyper_band(1)).pack(side="right")
        self.hyper_band_scale = tk.Scale(
            hyper_tab, from_=0, to=0, orient="horizontal", variable=self.current_hyper_band_index,
            command=self.on_hyper_band_changed, showvalue=False, highlightthickness=0, bd=0,
            bg=self.theme["panel"], fg=self.theme["text"], troughcolor=self.theme["panel_alt"],
            activebackground=self.theme["accent"], sliderlength=24, width=14, repeatdelay=150,
            repeatinterval=80, takefocus=1, cursor="hand2",
        )
        self.hyper_band_scale.pack(fill="x", padx=6, pady=(0, 4))
        self.hyper_view_canvas = tk.Canvas(hyper_tab, bg=self.theme["canvas"], highlightthickness=0)
        self.hyper_view_canvas.pack(fill="both", expand=True, pady=(0, 4))
        self.hyper_spectrum_canvas = tk.Canvas(hyper_tab, bg=self.theme["canvas"], highlightthickness=0, height=165)
        self.hyper_spectrum_canvas.pack(fill="x", padx=6, pady=(0, 6))
        self.live_view_canvas.bind("<Configure>", lambda _e: self._draw_live_view_placeholder())
        self.live_vertical_profile_canvas.bind("<Configure>", lambda _e: self._render_live_profiles())
        self.live_horizontal_profile_canvas.bind("<Configure>", lambda _e: self._render_live_profiles())
        self.hyper_view_canvas.bind("<Configure>", lambda _e: self.render_current_hyper_band())
        self.hyper_view_canvas.bind("<Button-1>", self.on_hyper_mouse_click)
        self.hyper_view_canvas.bind("<Motion>", self.on_hyper_mouse_move)
        self.hyper_view_canvas.bind("<Leave>", self.on_hyper_mouse_leave)
        self.hyper_spectrum_canvas.bind("<Configure>", lambda _e: self._draw_hyper_spectrum_panel())
        self.hyper_spectrum_canvas.bind("<MouseWheel>", self.on_hyper_spectrum_mousewheel)
        self.hyper_spectrum_canvas.bind("<Button-3>", self.reset_hyper_spectrum_y_axis)
        self.hyper_spectrum_canvas.bind("<Button-4>", self.on_hyper_spectrum_mousewheel)
        self.hyper_spectrum_canvas.bind("<Button-5>", self.on_hyper_spectrum_mousewheel)
        for widget in (hyper_tab, self.hyper_band_scale, self.hyper_view_canvas):
            widget.bind("<Left>", lambda _e: self.step_hyper_band(-1))
            widget.bind("<Right>", lambda _e: self.step_hyper_band(1))
            widget.bind("<MouseWheel>", self.on_hyper_mousewheel)
            widget.bind("<Button-4>", lambda _e: self.step_hyper_band(1))
            widget.bind("<Button-5>", lambda _e: self.step_hyper_band(-1))
            widget.bind("<Button-1>", lambda _e, target=widget: target.focus_set(), add="+")

        status_frame = tk.LabelFrame(parent, text="Status / Messages", padx=6, pady=6)
        status_frame.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        status_strip = tk.Frame(status_frame)
        status_strip.pack(fill="x", pady=(0, 4))
        tk.Label(status_strip, textvariable=self.timelapse_status_var, font=("Segoe UI Semibold", 9)).pack(side="left")
        ttk.Separator(status_strip, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=8)
        tk.Label(status_strip, textvariable=self.time_remaining_var, fg="#9aa6b2").pack(side="left")
        ttk.Separator(status_strip, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=8)
        tk.Label(status_strip, textvariable=self.live_view_status_var, fg="#9aa6b2").pack(side="left")
        tk.Checkbutton(
            status_strip,
            text="Details",
            variable=self.show_detail_log_var,
            command=self.refresh_visible_log,
        ).pack(side="right")
        tk.Button(status_strip, text="Open Log", command=self.open_last_issues_log).pack(side="right", padx=(0, 6))
        self.log_text = tk.Text(status_frame, height=6, state="disabled", wrap="word", bg=self.theme["field"], fg=self.theme["text"], insertbackground=self.theme["accent_soft"], relief="flat")
        self.log_text.pack(fill="x", expand=False)
        self.refresh_visible_log()

    def _build_right_controls(self, parent):
        acquisition = tk.LabelFrame(parent, text="Acquisition / Timelapse", padx=6, pady=5)
        acquisition.pack(fill="x", pady=(0, 6))
        tk.Button(acquisition, text="Run Selected Site", command=self.manual_trigger_selected_position).pack(fill="x", pady=2)
        tk.Button(acquisition, text="Start Acquisition", command=self.start_acquisition).pack(fill="x", pady=2)
        tk.Button(acquisition, text="Abort Hera Acquisition", command=self.abort_acquisition).pack(fill="x", pady=2)
        run_status = tk.LabelFrame(acquisition, text="Run Status", padx=5, pady=4)
        run_status.pack(fill="x", pady=(5, 0))
        for label, var, strong in (
            ("Status", self.app_state_var, True),
            ("Site", self.current_site_var, False),
            ("Cycle", self.current_cycle_var, False),
        ):
            row = tk.Frame(run_status)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=f"{label}:", fg=self.theme["muted"], width=7, anchor="w").pack(side="left")
            value_label = tk.Label(
                row,
                textvariable=var,
                anchor="w",
                font=("Segoe UI Semibold", 9) if strong else ("Segoe UI", 9),
                fg="#7ad97a" if strong else self.theme["text"],
            )
            value_label.pack(side="left", fill="x", expand=True)
            if strong:
                self.right_app_state_label = value_label
        ttk.Separator(acquisition, orient="horizontal", style="Dark.TSeparator").pack(fill="x", pady=6)

        self.interval_var = tk.DoubleVar(value=10.0)
        self.stop_after_var = tk.DoubleVar(value=0.0)
        for label, var in (
            ("Interval (min)", self.interval_var),
            ("Dwell (s)", self.stage_dwell_var),
            ("Stop after (min)", self.stop_after_var),
            ("Speed XY", self.stage_speed_var),
        ):
            row = tk.Frame(acquisition)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=label, width=13, anchor="w").pack(side="left")
            tk.Entry(row, textvariable=var, width=7).pack(side="left")
        tk.Label(acquisition, textvariable=self.timelapse_status_var, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 0))
        tk.Label(acquisition, textvariable=self.time_remaining_var).pack(anchor="w", pady=(1, 5))
        tk.Button(acquisition, text="Start Timelapse", command=self.start_timelapse, bg="#ff8b3d", fg="#111111", activebackground="#ffb37a").pack(fill="x", pady=2)
        self.pause_button = tk.Button(acquisition, text="Pause", command=self.pause_or_resume_timelapse)
        self.pause_button.pack(fill="x", pady=2)
        tk.Button(acquisition, text="Stop Timelapse", command=self.stop_timelapse).pack(fill="x", pady=2)
        tk.Button(acquisition, text="Run First 2 Sites", command=self.run_first_two_sites).pack(fill="x", pady=2)

        saving = tk.LabelFrame(parent, text="Export", padx=6, pady=5)
        saving.pack(fill="x", pady=(0, 6))
        self.param_vars["output_path"] = tk.StringVar(value=self.default_output_dir)
        tk.Label(saving, text="Saving Folder").pack(anchor="w")
        tk.Entry(saving, textvariable=self.param_vars["output_path"], width=26).pack(fill="x", pady=(1, 4))
        tk.Button(saving, text="Browse", command=self.browse_output_path).pack(fill="x", pady=(0, 4))
        name_row = tk.Frame(saving)
        name_row.pack(fill="x", pady=(1, 3))
        tk.Label(name_row, text="Name").pack(side="left")
        tk.Entry(name_row, textvariable=self.export_name_var, width=13).pack(side="left", fill="x", expand=True, padx=(4, 4))
        tk.Checkbutton(name_row, text="Stamp", variable=self.export_append_time_var).pack(side="left")
        tk.Label(saving, text="Data to Export").pack(anchor="w", pady=(3, 0))
        data_row = tk.Frame(saving)
        data_row.pack(fill="x", pady=(1, 3))
        tk.Checkbutton(data_row, text="_raw", variable=self.export_raw_var).pack(side="left")
        tk.Checkbutton(data_row, text="_ref", variable=self.export_flatfield_var).pack(side="left", padx=(4, 0))
        tk.Checkbutton(data_row, text="_nrm", variable=self.export_normalized_var).pack(side="left", padx=(4, 0))
        tk.Label(saving, text="Notes").pack(anchor="w", pady=(3, 0))
        tk.Entry(saving, textvariable=self.saving_notes_var, width=26).pack(fill="x", pady=(1, 0))
        self.save_pending_button = tk.Button(saving, text="Export", command=self.save_pending_acquisition, state="disabled")
        self.save_pending_button.pack(fill="x", pady=(4, 0))
        ttk.Separator(saving, orient="horizontal").pack(fill="x", pady=6)
        tk.Button(saving, text="Open in HyperLAB", command=self.open_current_in_hyperlab).pack(fill="x")

    def _build_hera_ui(self, parent):
        frame = tk.LabelFrame(parent, text="Hera Acquisition", padx=8, pady=8)
        frame.pack(fill="x", pady=(0, 10))

        top = tk.Frame(frame)
        top.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        tk.Label(top, text="Connection and discovery are automatic.", fg="#9aa6b2").pack(side="left")
        tk.Label(top, textvariable=self.license_var, fg="#7ad97a").pack(side="right")

        buttons = tk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=3, sticky="w", pady=8)
        tk.Button(buttons, text="Preflight", command=self.preflight_check).pack(side="left", padx=6)
        tk.Button(buttons, text="Live Status", command=self.debug_live_status).pack(side="left", padx=6)
        tk.Button(buttons, text="Restart Live", command=self.restart_live_view).pack(side="left", padx=6)

        params = tk.LabelFrame(frame, text="Acquisition Parameters", padx=8, pady=8)
        params.grid(row=2, column=0, columnspan=3, sticky="ew")

        param_labels = [
            ("Gain [dB]:", "gain", 0.0),
            ("Exposure [ms]:", "exposure", 1.0),
            ("Spectral Resolution:", "scan_mode", "Medium"),
            ("Averages:", "averages", 1),
            ("Bands (0=default):", "bands", 0),
            ("Binning:", "binning", "None"),
            ("Output path:", "output_path", os.path.join(os.path.abspath(os.path.dirname(__file__)), "output")),
            ("Data type:", "data_type", "SinglePrecision"),
        ]

        self.param_vars = {}
        self.param_vars["trigger_mode"] = tk.StringVar(value="Internal")
        self.param_vars["stabilization"] = tk.IntVar(value=0)
        row = 0
        for label_text, key, default in param_labels:
            tk.Label(params, text=label_text).grid(row=row, column=0, sticky="w", pady=2)
            if key == "scan_mode":
                self.param_vars[key] = tk.StringVar(value=default)
                tk.OptionMenu(params, self.param_vars[key], *list(self.SCAN_MODES.keys())).grid(row=row, column=1, sticky="w")
            elif key == "binning":
                self.param_vars[key] = tk.StringVar(value=default)
                tk.OptionMenu(params, self.param_vars[key], *list(self.BINNING_OPTIONS.keys())).grid(row=row, column=1, sticky="w")
            elif key == "data_type":
                self.param_vars[key] = tk.StringVar(value=default)
                tk.OptionMenu(params, self.param_vars[key], *list(self.DATA_TYPES.keys())).grid(row=row, column=1, sticky="w")
            elif key == "output_path":
                self.param_vars[key] = tk.StringVar(value=default)
                tk.Entry(params, textvariable=self.param_vars[key], width=32).grid(row=row, column=1, sticky="w")
                tk.Button(params, text="Browse", command=self.browse_output_path).grid(row=row, column=2, padx=4)
            elif isinstance(default, int):
                self.param_vars[key] = tk.IntVar(value=default)
                tk.Entry(params, textvariable=self.param_vars[key], width=12).grid(row=row, column=1, sticky="w")
            else:
                self.param_vars[key] = tk.DoubleVar(value=default)
                tk.Entry(params, textvariable=self.param_vars[key], width=12).grid(row=row, column=1, sticky="w")
            row += 1

        for _k, _v in [("roi_x", 0), ("roi_y", 0), ("roi_w", 512), ("roi_h", 512)]:
            self.param_vars[_k] = tk.IntVar(value=_v)

        actions = tk.Frame(params)
        actions.grid(row=row, column=0, columnspan=3, pady=8, sticky="w")
        tk.Button(actions, text="Start Acquisition", command=self.start_acquisition).pack(side="left", padx=(0, 6))
        tk.Button(actions, text="Abort Hera Acquisition", command=self.abort_acquisition).pack(side="left", padx=6)

    def _build_tango_ui(self, parent):
        frame = tk.LabelFrame(parent, text="Stage Control", padx=10, pady=10)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        self.stage_speed_var = tk.DoubleVar(value=20.0)
        self.stage_dwell_var = tk.DoubleVar(value=0.0)
        self.live_pixel_size_var = tk.DoubleVar(value=1.0)
        self.live_invert_x_var = tk.BooleanVar(value=False)
        self.live_invert_y_var = tk.BooleanVar(value=False)
        self.live_swap_xy_var = tk.BooleanVar(value=False)
        self.position_name_var = tk.StringVar()
        self.selected_name_var = tk.StringVar()
        self.selected_x_var = tk.StringVar()
        self.selected_y_var = tk.StringVar()
        self.selected_z_var = tk.StringVar()

        actions = tk.Frame(frame)
        actions.grid(row=0, column=0, sticky="ew", pady=(10, 0))
        tk.Label(actions, text="Position name").pack(anchor="w", pady=(0, 2))
        tk.Entry(actions, textvariable=self.selected_name_var, width=24).pack(fill="x", pady=(0, 6))
        tk.Button(actions, text="Add Current Position", command=self.add_current_position).pack(fill="x", pady=4)
        tk.Button(actions, text="Update Selected Position", command=self.update_selected_position).pack(fill="x", pady=4)
        tk.Button(actions, text="Delete Selected Row", command=self.delete_selected_position).pack(fill="x", pady=4)
        tk.Button(actions, text="Rename Selected Position", command=self.rename_selected_position).pack(fill="x", pady=4)
        tk.Button(actions, text="Go To Selected Position", command=self.goto_selected_position).pack(fill="x", pady=4)
        tk.Button(actions, text="Reconnect Stage", command=self.reconnect_stage).pack(fill="x", pady=4)

        self.stage_status_var = tk.StringVar(value="Stage: not connected")
        self.stage_version_var = tk.StringVar(value="Controller: -")
        self.stage_position_var = tk.StringVar(value="X: -, Y: -")
        tk.Label(frame, textvariable=self.stage_status_var, font=("Segoe UI", 10, "bold")).grid(row=4, column=0, sticky="w", pady=(12, 0))
        tk.Label(frame, textvariable=self.stage_version_var).grid(row=5, column=0, sticky="w", pady=(4, 0))

        self.current_x_label = tk.Label(frame, text="X: -")
        self.current_y_label = tk.Label(frame, text="Y: -")
        self.current_z_label = tk.Label(frame, textvariable=self.nis_z_current_z_var)

        tl = tk.LabelFrame(frame, text="Timelapse Settings", padx=8, pady=8)
        tl.grid(row=8, column=0, sticky="ew", pady=(10, 0))
        tk.Label(tl, text="Interval (min)").grid(row=0, column=0, sticky="w")
        self.interval_var = tk.DoubleVar(value=10.0)
        tk.Entry(tl, textvariable=self.interval_var, width=8).grid(row=0, column=1, sticky="w", padx=(6, 10))
        tk.Label(tl, text="Dwell (s)").grid(row=0, column=2, sticky="w")
        tk.Entry(tl, textvariable=self.stage_dwell_var, width=8).grid(row=0, column=3, sticky="w")
        tk.Label(tl, text="Stop after (min)").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.stop_after_var = tk.DoubleVar(value=0.0)
        tk.Entry(tl, textvariable=self.stop_after_var, width=8).grid(row=1, column=1, sticky="w", padx=(6, 10), pady=(10, 0))
        tk.Label(tl, text="Speed XY").grid(row=1, column=2, sticky="w", pady=(10, 0))
        tk.Entry(tl, textvariable=self.stage_speed_var, width=8).grid(row=1, column=3, sticky="w", pady=(10, 0))
        tk.Label(tl, textvariable=self.timelapse_status_var, font=("Segoe UI", 10, "bold")).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        tk.Label(tl, textvariable=self.time_remaining_var).grid(row=2, column=2, columnspan=2, sticky="w", pady=(10, 0))

    def _build_nis_z_ui(self, parent):
        frame = tk.LabelFrame(parent, text="NIS Z Bridge", padx=6, pady=5)
        frame.pack(fill="x", pady=(6, 0))

        conn_row = tk.Frame(frame)
        conn_row.pack(fill="x", pady=(0, 4))
        tk.Label(conn_row, text="Shared folder:", fg=self.theme["muted"]).pack(side="left")
        tk.Entry(conn_row, textvariable=self.nis_z_shared_root_var, width=22).pack(side="left", fill="x", expand=True, padx=(4, 0))

        z_row = tk.Frame(frame)
        z_row.pack(fill="x", pady=(4, 4))
        tk.Label(z_row, text="NIS Z position:").pack(side="left")
        tk.Label(z_row, textvariable=self.nis_z_current_z_var, fg=self.theme["accent_soft"],
                 font=("Segoe UI Semibold", 9)).pack(side="left", padx=(5, 0))

        btns = tk.Frame(frame)
        btns.pack(fill="x", pady=(0, 5))
        tk.Button(btns, text="GET Z", command=self._nis_z_get).pack(side="left", padx=(0, 4))
        tk.Button(btns, text="STOP Z", command=self._nis_z_stop).pack(side="left")

        rel_frame = tk.LabelFrame(frame, text="Relative Move (um)", padx=5, pady=4)
        rel_frame.pack(fill="x", pady=(0, 5))
        step_row = tk.Frame(rel_frame)
        step_row.pack(fill="x", pady=(0, 3))
        tk.Label(step_row, text="Step (um):").pack(side="left")
        tk.Entry(step_row, textvariable=self.nis_z_step_var, width=7).pack(side="left", padx=(4, 0))
        btn_row = tk.Frame(rel_frame)
        btn_row.pack(fill="x")
        tk.Button(btn_row, text="Move +", command=lambda: self._nis_z_move_step(+1)).pack(side="left", padx=(0, 4))
        tk.Button(btn_row, text="Move -", command=lambda: self._nis_z_move_step(-1)).pack(side="left")

        tol_row = tk.Frame(frame)
        tol_row.pack(fill="x", pady=(0, 3))
        tk.Label(tol_row, text="Z tolerance (um):").pack(side="left")
        tk.Entry(tol_row, textvariable=self.nis_z_tolerance_var, width=5).pack(side="left", padx=(4, 0))
        tk.Label(tol_row, text="skip if within range", fg=self.theme["muted"], wraplength=92, justify="left").pack(side="left", padx=(5, 0))

        timeout_row = tk.Frame(frame)
        timeout_row.pack(fill="x")
        tk.Label(timeout_row, text="Response timeout (s):").pack(side="left")
        tk.Entry(timeout_row, textvariable=self.nis_z_timeout_var, width=5).pack(side="left", padx=(4, 0))

    def _build_log_ui(self, parent):
        state_frame = tk.LabelFrame(parent, text="Run Console", padx=10, pady=10)
        state_frame.pack(fill="x")
        self.app_state_var = tk.StringVar(value=self.app_state)
        tk.Label(state_frame, text="Current state:").pack(side="left")
        self.app_state_label = tk.Label(state_frame, textvariable=self.app_state_var, fg="#7ad97a", font=("Segoe UI Semibold", 10))
        self.app_state_label.pack(side="left", padx=6)
        ttk.Separator(state_frame, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(state_frame, textvariable=self.center_stage_summary_var, fg="#9aa6b2").pack(side="left")
        ttk.Separator(state_frame, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(state_frame, textvariable=self.current_cycle_var, fg="#9aa6b2").pack(side="left")
        ttk.Separator(state_frame, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(state_frame, textvariable=self.current_site_var, fg="#9aa6b2").pack(side="left")

        views_frame = tk.LabelFrame(parent, text="Views", padx=10, pady=10)
        views_frame.pack(fill="both", expand=True, pady=(10, 10))
        notebook = ttk.Notebook(views_frame)
        notebook.pack(fill="both", expand=True)

        live_tab = tk.Frame(notebook, bg=self.theme["panel"])
        hyper_tab = tk.Frame(notebook, bg=self.theme["panel"])
        notebook.add(live_tab, text="Live View")
        notebook.add(hyper_tab, text="Hyperspectral View")

        live_controls = tk.Frame(live_tab, bg=self.theme["panel"])
        live_controls.pack(fill="x", padx=8, pady=(8, 4))

        live_cursor_bar = tk.Frame(live_controls, bg=self.theme["panel"])
        live_cursor_bar.pack(fill="x")
        tk.Label(live_cursor_bar, textvariable=self.live_cursor_var, fg="#e7edf5", bg=self.theme["panel"],
                 font=("Segoe UI Semibold", 10), anchor="w").pack(side="left", fill="x", expand=True)

        live_display_bar = tk.Frame(live_controls, bg=self.theme["panel"])
        live_display_bar.pack(fill="x", pady=(4, 0))
        tk.Checkbutton(live_display_bar, text="Autocontrast", variable=self.live_autocontrast_var,
                       command=lambda: self._schedule_live_render(force=True),
                       bg=self.theme["panel"], fg=self.theme["text"], selectcolor=self.theme["field"],
                       activebackground=self.theme["panel"]).pack(side="left", padx=(12, 0))
        tk.Checkbutton(live_display_bar, text="Show Saturation", variable=self.live_show_saturation_var,
                       command=lambda: self._schedule_live_render(force=True),
                       bg=self.theme["panel"], fg=self.theme["text"], selectcolor=self.theme["field"],
                       activebackground=self.theme["panel"]).pack(side="left", padx=(8, 0))
        tk.Checkbutton(live_display_bar, text="Cross", variable=self.live_cross_enabled_var,
                       command=self.toggle_live_cross,
                       bg=self.theme["panel"], fg=self.theme["text"], selectcolor=self.theme["field"],
                       activebackground=self.theme["panel"]).pack(side="left", padx=(8, 0))
        tk.Label(live_display_bar, textvariable=self.live_profile_status_var, fg="#9aa6b2", bg=self.theme["panel"]).pack(side="left", padx=(10, 4))
        tk.Label(live_display_bar, textvariable=self.live_gamma_label_var, fg="#9aa6b2", bg=self.theme["panel"]).pack(side="left", padx=(10, 4))
        tk.Scale(live_display_bar, variable=self.live_gamma_var, from_=0.2, to=3.0, resolution=0.1,
                 orient="horizontal", length=110, showvalue=False, command=self.on_live_gamma_change,
                 bg=self.theme["panel"], fg=self.theme["text"], troughcolor=self.theme["field"],
                 highlightthickness=0).pack(side="left")
        tk.Button(live_display_bar, text="Reset Gamma", command=self.reset_live_gamma).pack(side="left", padx=(6, 0))
        tk.Button(live_display_bar, text="Snapshot", command=self.snapshot_live_view).pack(side="left", padx=(8, 0))

        live_zoom_bar = tk.Frame(live_controls, bg=self.theme["panel"])
        live_zoom_bar.pack(fill="x", pady=(4, 0))
        tk.Label(live_zoom_bar, textvariable=self.live_zoom_label_var, fg="#9aa6b2", bg=self.theme["panel"]).pack(side="left", padx=(12, 4))
        tk.Button(live_zoom_bar, text="-", width=3, command=lambda: self.zoom_live_view(1 / 1.25)).pack(side="left")
        tk.Button(live_zoom_bar, text="Fit to view", command=self.fit_live_view).pack(side="left", padx=(6, 0))
        tk.Button(live_zoom_bar, text="+", width=3, command=lambda: self.zoom_live_view(1.25)).pack(side="left", padx=(6, 0))
        tk.Label(live_zoom_bar, text="Mouse wheel zoom; right-drag pan", fg="#728091", bg=self.theme["panel"]).pack(side="left", padx=(10, 0))

        live_roi_bar = tk.Frame(live_controls, bg=self.theme["panel"])
        live_roi_bar.pack(fill="x", pady=(4, 0))
        tk.Button(live_roi_bar, textvariable=self.live_roi_button_var, command=self.toggle_live_roi_selection).pack(side="right")
        tk.Button(live_roi_bar, text="Clear ROI", command=self.clear_live_roi_selection).pack(side="right", padx=(0, 6))
        tk.Label(live_roi_bar, textvariable=self.live_roi_status_var, fg="#9aa6b2", bg=self.theme["panel"],
                 anchor="w").pack(side="left", fill="x", expand=True)
        live_profile_grid = tk.Frame(live_tab, bg=self.theme["panel"])
        live_profile_grid.pack(fill="both", expand=True)
        live_profile_grid.grid_rowconfigure(0, weight=1)
        live_profile_grid.grid_columnconfigure(0, weight=1)
        self.live_view_canvas = tk.Canvas(live_profile_grid, bg="#101418", highlightthickness=0)
        self.live_view_canvas.bind("<Motion>", self.on_live_mouse_move)
        self.live_view_canvas.bind("<Button-1>", self.on_live_mouse_click)
        self.live_view_canvas.bind("<MouseWheel>", self.on_live_mousewheel)
        self.live_view_canvas.bind("<Button-4>", lambda event: self.zoom_live_view(1.25, event))
        self.live_view_canvas.bind("<Button-5>", lambda event: self.zoom_live_view(1 / 1.25, event))
        self.live_view_canvas.bind("<ButtonPress-3>", self.start_live_pan)
        self.live_view_canvas.bind("<B3-Motion>", self.on_live_pan_drag)
        self.live_view_canvas.bind("<ButtonRelease-3>", self.end_live_pan)
        self.live_view_canvas.bind("<Leave>", self.on_live_mouse_leave)
        self.live_view_canvas.grid(row=0, column=0, sticky="nsew")
        self.live_vertical_profile_canvas = tk.Canvas(live_profile_grid, bg="#101418", highlightthickness=0, width=120)
        self.live_vertical_profile_canvas.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        self.live_horizontal_profile_canvas = tk.Canvas(live_profile_grid, bg="#101418", highlightthickness=0, height=105)
        self.live_horizontal_profile_canvas.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        hyper_controls = tk.Frame(hyper_tab, bg=self.theme["panel"])
        hyper_controls.pack(fill="x", padx=8, pady=(8, 4))
        tk.Button(hyper_controls, text="Prev Band", command=lambda: self.step_hyper_band(-1)).pack(side="left", padx=(0, 8))
        tk.Label(hyper_controls, textvariable=self.current_hyper_band_var, fg="#e7edf5").pack(side="left")
        ttk.Separator(hyper_controls, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(hyper_controls, textvariable=self.current_hyper_wavelength_var, fg="#9aa6b2").pack(side="left")
        jump_wrap = tk.Frame(hyper_controls, bg=self.theme["panel"])
        jump_wrap.pack(side="right", padx=(8, 0))
        tk.Button(jump_wrap, text="Go", command=self.jump_to_hyper_band).pack(side="right")
        tk.Entry(jump_wrap, textvariable=self.hyper_band_jump_var, width=6).pack(side="right", padx=(0, 6))
        tk.Label(jump_wrap, text="Band", fg="#9aa6b2").pack(side="right", padx=(0, 6))
        tk.Button(hyper_controls, text="Next Band", command=lambda: self.step_hyper_band(1)).pack(side="right")
        self.hyper_band_scale = tk.Scale(
            hyper_tab,
            from_=0,
            to=0,
            orient="horizontal",
            variable=self.current_hyper_band_index,
            command=self.on_hyper_band_changed,
            showvalue=False,
            highlightthickness=0,
            bd=0,
            bg=self.theme["panel"],
            fg=self.theme["text"],
            troughcolor=self.theme["panel_alt"],
            activebackground=self.theme["accent"],
            sliderlength=28,
            width=18,
            repeatdelay=150,
            repeatinterval=80,
            takefocus=1,
            cursor="hand2",
        )
        self.hyper_band_scale.pack(fill="x", padx=8, pady=(0, 6))
        self.hyper_view_canvas = tk.Canvas(hyper_tab, bg="#101418", highlightthickness=0)
        self.hyper_view_canvas.pack(fill="both", expand=True)
        self.live_view_canvas.bind("<Configure>", lambda _e: self._draw_live_view_placeholder())
        self.live_vertical_profile_canvas.bind("<Configure>", lambda _e: self._render_live_profiles())
        self.live_horizontal_profile_canvas.bind("<Configure>", lambda _e: self._render_live_profiles())
        self.hyper_view_canvas.bind("<Configure>", lambda _e: self.render_current_hyper_band())
        for widget in (hyper_tab, self.hyper_band_scale, self.hyper_view_canvas):
            widget.bind("<Left>", lambda _e: self.step_hyper_band(-1))
            widget.bind("<Right>", lambda _e: self.step_hyper_band(1))
            widget.bind("<MouseWheel>", self.on_hyper_mousewheel)
            widget.bind("<Button-4>", lambda _e: self.step_hyper_band(1))
            widget.bind("<Button-5>", lambda _e: self.step_hyper_band(-1))
            widget.bind("<Button-1>", lambda _e, target=widget: target.focus_set(), add="+")

        pos_frame = tk.LabelFrame(parent, text="Saved Positions", padx=10, pady=10)
        pos_frame.pack(fill="x", pady=(0, 10))
        header = tk.Frame(pos_frame)
        header.pack(fill="x", pady=(0, 8))
        tk.Label(header, text="Choose a site in the list, edit it on the left, then run or schedule it from the top bar.", fg="#9aa6b2").pack(side="left")

        center_tree_wrap = tk.Frame(pos_frame)
        center_tree_wrap.pack(fill="both", expand=True)
        self.positions_tree = ttk.Treeview(center_tree_wrap, columns=("name", "x", "y", "z", "roi"), show="headings", height=4, style="Dark.Treeview")
        self.positions_tree.heading("name", text="Name")
        self.positions_tree.heading("x", text="X")
        self.positions_tree.heading("y", text="Y")
        self.positions_tree.heading("z", text="Z")
        self.positions_tree.heading("roi", text="ROI")
        self.positions_tree.column("name", width=220, anchor="w")
        self.positions_tree.column("x", width=125, anchor="e")
        self.positions_tree.column("y", width=125, anchor="e")
        self.positions_tree.column("z", width=110, anchor="e")
        self.positions_tree.column("roi", width=60, anchor="center")
        center_scroll = ttk.Scrollbar(center_tree_wrap, orient="vertical", command=self.positions_tree.yview)
        self.positions_tree.configure(yscrollcommand=center_scroll.set)
        self.positions_tree.pack(side="left", fill="both", expand=True)
        center_scroll.pack(side="right", fill="y")
        self.positions_tree.bind("<<TreeviewSelect>>", self.on_position_selected)

        status_strip = tk.Frame(parent)
        status_strip.pack(fill="x", pady=(0, 10))
        tk.Label(status_strip, textvariable=self.timelapse_status_var, font=("Segoe UI Semibold", 10)).pack(side="left")
        ttk.Separator(status_strip, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(status_strip, textvariable=self.time_remaining_var, fg="#9aa6b2").pack(side="left")
        ttk.Separator(status_strip, orient="vertical", style="Dark.TSeparator").pack(side="left", fill="y", padx=12)
        tk.Label(status_strip, textvariable=self.last_export_var, fg="#9aa6b2").pack(side="left")

        log_frame = tk.LabelFrame(parent, text="Status / Messages", padx=10, pady=10)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=16, state="disabled", wrap="word", bg="#0f1318", fg="#e7edf5", insertbackground="#ffb37a", relief="flat")
        self.log_text.pack(fill="both", expand=True)

    def browse_dll(self):
        file_path = filedialog.askopenfilename(title="Select Hera API DLL", filetypes=[("DLL files", "*.dll"), ("All files", "*.*")])
        if file_path:
            self.dll_path_var.set(file_path)

    def browse_tango_dll(self):
        file_path = filedialog.askopenfilename(title="Select Tango DLL", filetypes=[("DLL files", "*.dll"), ("All files", "*.*")])
        if file_path:
            self.tango_dll_var.set(file_path)

    def browse_output_path(self):
        folder = filedialog.askdirectory(title="Select output folder")
        if folder:
            self.param_vars["output_path"].set(folder)

    def browse_flatfield_output_path(self):
        folder = filedialog.askdirectory(title="Select flatfield output folder")
        if folder:
            self.flatfield_output_path_var.set(folder)

    def browse_hyperlab_shortcut(self):
        file_path = filedialog.askopenfilename(
            title="Select Nireos HyperLAB shortcut or application",
            filetypes=[("Shortcut or executable", "*.lnk *.exe"), ("All files", "*.*")],
        )
        if file_path:
            self.hyperlab_shortcut_var.set(file_path)

    def open_last_issues_log(self):
        try:
            self._write_last_issues_log()
            os.startfile(self.last_issues_log_path)
        except Exception as exc:
            self.log(f"Open background log failed: {exc}")

    def open_current_in_hyperlab(self):
        shortcut_path = self.hyperlab_shortcut_var.get().strip()
        if not shortcut_path or not os.path.exists(shortcut_path):
            from tkinter import messagebox
            messagebox.showerror("Open in HyperLAB", f"HyperLAB shortcut not found:\n{shortcut_path}")
            self.log(f"Open in HyperLAB failed: shortcut not found: {shortcut_path}")
            return

        hdr_path = self._current_or_latest_export_hdr()
        try:
            launch_path = self._start_hyperlab(shortcut_path, hdr_path)
            if hdr_path:
                self._copy_last_export_path_to_clipboard(hdr_path)
                self.log(f"Started HyperLAB with export: {hdr_path}")
            else:
                self.log(f"Started HyperLAB from: {launch_path}")
        except Exception as exc:
            try:
                os.startfile(shortcut_path)
                if hdr_path:
                    self._copy_last_export_path_to_clipboard(hdr_path)
                    self.log(f"Requested HyperLAB launch from shortcut. Last export path copied to clipboard: {hdr_path}")
                else:
                    self.log("Requested HyperLAB launch from shortcut.")
            except Exception as fallback_exc:
                from tkinter import messagebox
                messagebox.showerror("Open in HyperLAB", f"Could not open HyperLAB:\n{fallback_exc}")
                self.log(f"Open in HyperLAB failed: {exc}; fallback failed: {fallback_exc}")

    def _current_or_latest_export_hdr(self):
        if self.last_export_path and os.path.exists(self.last_export_path):
            return self.last_export_path

        output_root = ""
        try:
            output_root = self.param_vars["output_path"].get().strip()
        except Exception:
            output_root = ""
        latest_hdr_path = self._find_latest_export_hdr(output_root)
        if latest_hdr_path:
            self.last_export_path = latest_hdr_path
            try:
                self.last_export_var.set(f"Last export: {os.path.basename(latest_hdr_path)}")
            except Exception:
                pass
        return latest_hdr_path

    def _find_latest_export_hdr(self, output_root):
        if not output_root or not os.path.isdir(output_root):
            return ""

        newest_path = ""
        newest_mtime = -1.0
        for folder, _dirnames, filenames in os.walk(output_root):
            for filename in filenames:
                lower_name = filename.lower()
                if not lower_name.endswith(".hdr"):
                    continue
                if not any(token in lower_name for token in ("_raw", "_ref", "_nrm")):
                    continue
                path = os.path.join(folder, filename)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                if mtime > newest_mtime:
                    newest_path = path
                    newest_mtime = mtime
        return newest_path

    def _start_hyperlab(self, shortcut_path, hdr_path=None):
        launch_path, working_dir = self._resolve_hyperlab_launch_target(shortcut_path)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if launch_path.lower().endswith(".lnk"):
            os.startfile(launch_path)
            return launch_path

        if not os.path.exists(launch_path):
            raise FileNotFoundError(launch_path)

        cwd = working_dir if working_dir and os.path.isdir(working_dir) else os.path.dirname(launch_path)
        args = [launch_path]
        if hdr_path and os.path.exists(hdr_path):
            args.append(hdr_path)
        subprocess.Popen(args, cwd=cwd or None, creationflags=creationflags)
        return launch_path

    def _resolve_hyperlab_launch_target(self, shortcut_path):
        if not shortcut_path.lower().endswith(".lnk"):
            return shortcut_path, os.path.dirname(shortcut_path)

        target_path, working_dir = self._read_windows_shortcut(shortcut_path)
        candidates = []
        if working_dir:
            candidates.append(os.path.join(working_dir, "HyperLAB.exe"))
        if target_path:
            candidates.append(target_path)
            candidates.append(os.path.join(os.path.dirname(target_path), "HyperLAB.exe"))
        candidates.append(r"C:\Program Files\Nireos\Nireos HyperLAB\bin\HyperLAB.exe")

        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate, working_dir
        return shortcut_path, working_dir

    def _read_windows_shortcut(self, shortcut_path):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        script = (
            "$p=$env:HERA_HYPERLAB_SHORTCUT;"
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
            "[Console]::WriteLine('TARGET=' + $s.TargetPath);"
            "[Console]::WriteLine('WORKDIR=' + $s.WorkingDirectory)"
        )
        try:
            env = os.environ.copy()
            env["HERA_HYPERLAB_SHORTCUT"] = shortcut_path
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", script],
                capture_output=True,
                env=env,
                text=True,
                timeout=5,
                creationflags=creationflags,
            )
        except Exception as exc:
            self.log(f"Could not inspect HyperLAB shortcut: {exc}", detail=True)
            return "", ""

        target_path = ""
        working_dir = ""
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip()
            self.log(f"Could not inspect HyperLAB shortcut: {message}", detail=True)
            return "", ""

        for line in result.stdout.splitlines():
            if line.startswith("TARGET="):
                target_path = line[len("TARGET="):].strip()
            elif line.startswith("WORKDIR="):
                working_dir = line[len("WORKDIR="):].strip()
        return target_path, working_dir

    def _copy_last_export_path_to_clipboard(self, hdr_path):
        self.clipboard_clear()
        self.clipboard_append(hdr_path)
