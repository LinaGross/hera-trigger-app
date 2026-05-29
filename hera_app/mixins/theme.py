import tkinter as tk
from tkinter import ttk


class ThemeMixin:
    def _configure_theme(self):
        palettes = {
            "dark": {
                "bg": "#14181d",
                "panel": "#181e25",
                "panel_section": "#202832",
                "panel_subsection": "#2a3541",
                "panel_alt": "#344252",
                "button_bg": "#3b4d60",
                "button_active": "#4a6178",
                "field": "#0f1318",
                "border": "#617181",
                "border_strong": "#b2c3d4",
                "text": "#e7edf5",
                "muted": "#9aa6b2",
                "accent": "#ff8b3d",
                "accent_soft": "#ffb37a",
                "success": "#7ad97a",
                "danger": "#ff6a6a",
                "canvas": "#101418",
                "canvas_grid": "#1b2229",
                "title": "#f3f6fb",
                "button_text": "#e7edf5",
                "accent_text": "#111111",
            },
            "light": {
                "bg": "#eef2f6",
                "panel": "#f8fbff",
                "panel_section": "#dbe5ef",
                "panel_subsection": "#f4f8fc",
                "panel_alt": "#dfe8f2",
                "button_bg": "#d2e1f0",
                "button_active": "#bfd3e7",
                "field": "#f7f9fc",
                "border": "#8fa2b5",
                "border_strong": "#51697f",
                "text": "#16202a",
                "muted": "#5c6b79",
                "accent": "#d96f22",
                "accent_soft": "#a9571d",
                "success": "#247a3d",
                "danger": "#ba3030",
                "canvas": "#f4f7fa",
                "canvas_grid": "#dde5ee",
                "title": "#101820",
                "button_text": "#16202a",
                "accent_text": "#ffffff",
            },
        }
        self.theme = palettes.get(self.theme_mode, palettes["dark"])
        self.configure(bg=self.theme["bg"])
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Dark.Treeview", background=self.theme["panel"], fieldbackground=self.theme["panel"], foreground=self.theme["text"], rowheight=23, bordercolor=self.theme["border_strong"], lightcolor=self.theme["border_strong"], darkcolor=self.theme["border_strong"])
        style.configure("Dark.Treeview.Heading", background=self.theme["panel_alt"], foreground=self.theme["text"], relief="flat")
        style.map("Dark.Treeview", background=[("selected", self.theme["accent"])], foreground=[("selected", "#111111")])
        style.configure("Dark.TSeparator", background=self.theme["border"])
        style.configure("Left.TNotebook", background=self.theme["bg"], borderwidth=0)
        style.configure("Left.TNotebook.Tab", background=self.theme["panel_alt"], foreground=self.theme["text"], padding=[8, 3], font=("Segoe UI", 9))
        style.map("Left.TNotebook.Tab", background=[("selected", self.theme["accent"]), ("active", self.theme["panel"])], foreground=[("selected", self.theme["accent_text"])])

        self.option_add("*Font", "{Segoe UI} 9")
        self.option_add("*Background", self.theme["panel"])
        self.option_add("*Foreground", self.theme["text"])
        self.option_add("*Label.Background", self.theme["panel"])
        self.option_add("*Label.Foreground", self.theme["text"])
        self.option_add("*LabelFrame.Background", self.theme["panel"])
        self.option_add("*LabelFrame.Foreground", self.theme["text"])
        self.option_add("*LabelFrame.BorderWidth", 2)
        self.option_add("*LabelFrame.HighlightThickness", 1)
        self.option_add("*LabelFrame.HighlightBackground", self.theme["border_strong"])
        self.option_add("*Button.Background", self.theme["button_bg"])
        self.option_add("*Button.Foreground", self.theme["text"])
        self.option_add("*Button.Relief", "solid")
        self.option_add("*Button.BorderWidth", 1)
        self.option_add("*Button.HighlightThickness", 1)
        self.option_add("*Button.HighlightBackground", self.theme["border_strong"])
        self.option_add("*Button.HighlightColor", self.theme["accent"])
        self.option_add("*Entry.Background", self.theme["field"])
        self.option_add("*Entry.Foreground", self.theme["text"])
        self.option_add("*Text.Background", self.theme["field"])
        self.option_add("*Text.Foreground", self.theme["text"])
        if hasattr(self, "theme_button_var"):
            self.theme_button_var.set("Dark Mode" if self.theme_mode == "light" else "Light Mode")
        self._apply_theme_recursive(self)

    def _safe_widget_bg(self, widget, fallback=None):
        try:
            return widget.cget("bg")
        except Exception:
            return fallback or self.theme["panel"]

    def _label_frame_bg(self, widget):
        parent = getattr(widget, "master", None)
        if parent is None:
            return self.theme["panel_section"]
        parent_bg = self._safe_widget_bg(parent, self.theme["panel"])
        parent_cls = parent.winfo_class()
        if parent_cls in {"Labelframe", "LabelFrame"} or parent_bg in {
            self.theme["panel_section"],
            self.theme["panel_subsection"],
        }:
            return self.theme["panel_subsection"]
        return self.theme["panel_section"]

    def _container_bg_for_widget(self, widget):
        cls = widget.winfo_class()
        if cls in {"Labelframe", "LabelFrame"}:
            return self._label_frame_bg(widget)
        parent = getattr(widget, "master", None)
        if parent is None:
            return self.theme["bg"]
        parent_cls = parent.winfo_class()
        if parent is self or parent_cls in {"Tk", "Toplevel", "Panedwindow"}:
            return self.theme["bg"]
        parent_bg = self._safe_widget_bg(parent, self.theme["panel"])
        if parent_cls in {"Labelframe", "LabelFrame"}:
            return self.theme["panel_subsection"] if cls == "Frame" else parent_bg
        if parent_bg == self.theme["panel_subsection"]:
            return self.theme["panel_subsection"]
        if parent_bg == self.theme["panel_section"]:
            return self.theme["panel_subsection"]
        if parent_bg == self.theme["bg"]:
            return self.theme["bg"]
        return self.theme["panel"]

    def _is_primary_button(self, widget):
        try:
            return widget.cget("text") == "Start Timelapse"
        except Exception:
            return False

    def _apply_theme_recursive(self, widget):
        cls = widget.winfo_class()
        try:
            if cls in {"Frame", "Toplevel"}:
                bg = self._container_bg_for_widget(widget)
                widget.configure(bg=bg, highlightbackground=self.theme["border"], highlightcolor=self.theme["border"])
            elif cls in {"Labelframe", "LabelFrame"}:
                bg = self._label_frame_bg(widget)
                widget.configure(
                    bg=bg,
                    fg=self.theme["text"],
                    relief="solid",
                    bd=2,
                    highlightthickness=1,
                    highlightbackground=self.theme["border_strong"],
                    highlightcolor=self.theme["border_strong"],
                )
            elif cls == "Panedwindow":
                widget.configure(bg=self.theme["bg"], sashrelief="flat")
            elif cls == "Canvas":
                widget.configure(bg=self.theme["canvas"], highlightbackground=self.theme["border"], highlightcolor=self.theme["border"])
            elif cls == "Label":
                widget.configure(bg=self._container_bg_for_widget(widget), fg=self.theme["text"])
            elif cls == "Button":
                bg = self.theme["accent"] if self._is_primary_button(widget) else self.theme["button_bg"]
                fg = self.theme["accent_text"] if self._is_primary_button(widget) else self.theme["button_text"]
                widget.configure(
                    bg=bg,
                    fg=fg,
                    activebackground=self.theme["accent"] if self._is_primary_button(widget) else self.theme["button_active"],
                    activeforeground=self.theme["accent_text"] if self._is_primary_button(widget) else self.theme["button_text"],
                    relief="solid",
                    bd=1,
                    highlightthickness=1,
                    highlightbackground=self.theme["border_strong"],
                    highlightcolor=self.theme["accent"],
                    padx=6,
                    pady=3,
                    cursor="hand2",
                )
            elif cls == "Entry":
                widget.configure(bg=self.theme["field"], fg=self.theme["text"], insertbackground=self.theme["accent_soft"], relief="solid", bd=1, highlightthickness=1, highlightbackground=self.theme["border_strong"], highlightcolor=self.theme["accent"])
            elif cls == "Text":
                widget.configure(bg=self.theme["field"], fg=self.theme["text"], insertbackground=self.theme["accent_soft"], relief="solid", bd=1, highlightthickness=1, highlightbackground=self.theme["border_strong"])
            elif cls == "Checkbutton":
                bg = self._container_bg_for_widget(widget)
                widget.configure(bg=bg, fg=self.theme["text"], selectcolor=self.theme["field"], activebackground=bg, activeforeground=self.theme["text"], highlightthickness=0)
            elif cls == "Scale":
                widget.configure(bg=self._container_bg_for_widget(widget), fg=self.theme["text"], troughcolor=self.theme["field"], activebackground=self.theme["accent"], highlightthickness=0)
            elif cls == "Menubutton":
                widget.configure(
                    bg=self.theme["button_bg"],
                    fg=self.theme["button_text"],
                    activebackground=self.theme["button_active"],
                    activeforeground=self.theme["button_text"],
                    relief="solid",
                    bd=1,
                    highlightthickness=1,
                    highlightbackground=self.theme["border_strong"],
                    highlightcolor=self.theme["accent"],
                )
                try:
                    widget["menu"].configure(bg=self.theme["panel_alt"], fg=self.theme["text"], activebackground=self.theme["button_active"], activeforeground=self.theme["button_text"])
                except Exception:
                    pass
        except Exception:
            pass

        for child in widget.winfo_children():
            self._apply_theme_recursive(child)

    def toggle_theme_mode(self):
        self.theme_mode = "light" if self.theme_mode == "dark" else "dark"
        self._configure_theme()
        self._draw_live_view_placeholder()
        self.render_current_hyper_band()
