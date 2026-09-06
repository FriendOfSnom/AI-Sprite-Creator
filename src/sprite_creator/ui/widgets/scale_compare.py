"""
Scale comparison panel.

Side-by-side bottom-anchored canvases: a reference ST sprite (left, from
data/reference_sprites/scale_references/) and the user's sprite (right),
with a 0.1,2.5 scale slider and an eye-line guide across both canvases.

Extracted from ScaleStep in ui/screens/finalization_steps.py (the AI
flow's height-crop slider is intentionally omitted).
"""

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable, Dict, Optional, Union

import yaml
from PIL import Image, ImageTk

from ...config import (
    BG_COLOR,
    CARD_BG,
    TEXT_COLOR,
    TEXT_SECONDARY,
    BODY_FONT,
    BODY_FONT_BOLD,
    SMALL_FONT,
    LINE_COLOR,
    REF_SPRITES_DIR,
)
from ..tk_common import get_primary_screen


class ScaleComparePanel(tk.Frame):
    """Reference-vs-user scale comparison with a slider; read .scale_value."""

    def __init__(
        self,
        parent: tk.Widget,
        on_change: Optional[Callable[[float], None]] = None,
        canvas_height: Optional[int] = None,
    ):
        super().__init__(parent, bg=BG_COLOR)
        self._canvas_height_override = canvas_height
        self._on_change = on_change
        self._references: Dict[str, dict] = {}
        self._user_img: Optional[Image.Image] = None
        self._eye_line_ratio: Optional[float] = None
        self._img_refs: Dict[str, ImageTk.PhotoImage] = {}

        self._load_references()

        # Controls block, centered above the canvases
        controls = tk.Frame(self, bg=BG_COLOR)
        controls.pack(pady=(0, 8))

        ref_row = tk.Frame(controls, bg=BG_COLOR)
        ref_row.pack(pady=(0, 4))
        tk.Label(ref_row, text="Compare against:", bg=BG_COLOR,
                 fg=TEXT_COLOR, font=BODY_FONT).pack(side="left")
        names = sorted(self._references.keys())
        # Default to John (the MC) when present; otherwise the first name.
        default_ref = next((n for n in names if n.lower() == "john"),
                           names[0] if names else "")
        self._ref_var = tk.StringVar(value=default_ref)
        if names:
            self._ref_combo = ttk.Combobox(
                ref_row, textvariable=self._ref_var, values=names,
                state="readonly", width=24,
            )
            self._ref_combo.bind("<<ComboboxSelected>>",
                                 lambda e: self._redraw())
            self._ref_combo.pack(side="left", padx=(8, 0))

        slider_row = tk.Frame(controls, bg=BG_COLOR)
        slider_row.pack()
        tk.Label(slider_row, text="Scale:", bg=BG_COLOR, fg=TEXT_COLOR,
                 font=BODY_FONT).pack(side="left")
        self._scale_var = tk.DoubleVar(value=1.0)
        slider = tk.Scale(
            slider_row, from_=0.1, to=2.5, resolution=0.01,
            orient="horizontal", variable=self._scale_var, length=620,
            bg=BG_COLOR, fg=TEXT_COLOR, troughcolor=CARD_BG,
            highlightthickness=0, command=lambda v: self._redraw(),
        )
        slider.pack(side="left", padx=(8, 8))
        self._scale_label = tk.Label(slider_row, text="1.00", bg=BG_COLOR,
                                     fg=TEXT_COLOR, font=BODY_FONT_BOLD)
        self._scale_label.pack(side="left")

        # Canvases, centered as a pair
        canvases = tk.Frame(self, bg=BG_COLOR)
        canvases.pack(anchor="center")

        sw, sh, *_ = get_primary_screen(self.winfo_toplevel())
        self._canv_w = max(int((sw - 200) // 2.6), 320)
        if self._canvas_height_override:
            self._canv_h = max(self._canvas_height_override, 260)
        else:
            self._canv_h = max(int(sh * 0.48), 320)

        left = tk.Frame(canvases, bg=BG_COLOR)
        left.pack(side="left", padx=(0, 10))
        tk.Label(left, text="Reference (in-game size)", bg=BG_COLOR,
                 fg=TEXT_SECONDARY, font=SMALL_FONT).pack()
        self._ref_canvas = tk.Canvas(left, width=self._canv_w,
                                     height=self._canv_h, bg="#1a1a1a",
                                     highlightthickness=0)
        self._ref_canvas.pack()

        right = tk.Frame(canvases, bg=BG_COLOR)
        right.pack(side="left")
        tk.Label(right, text="Your character", bg=BG_COLOR,
                 fg=TEXT_SECONDARY, font=SMALL_FONT).pack()
        self._user_canvas = tk.Canvas(right, width=self._canv_w,
                                      height=self._canv_h, bg="#1a1a1a",
                                      highlightthickness=0)
        self._user_canvas.pack()

    # ------------------------------------------------------------------
    @property
    def scale_value(self) -> float:
        return float(self._scale_var.get())

    def load_user_image(self, img: Union[Image.Image, Path],
                        eye_line_ratio: Optional[float] = None) -> None:
        if isinstance(img, (str, Path)):
            img = Image.open(img)
        self._user_img = img.convert("RGBA")
        self._eye_line_ratio = eye_line_ratio

        # Auto-seed the slider so pixel heights match the reference
        ref = self._current_reference()
        if ref is not None:
            initial = ref["image"].height / max(1, self._user_img.height)
            self._scale_var.set(min(max(initial, 0.1), 2.5))
        self._redraw()

    # ------------------------------------------------------------------
    def _load_references(self) -> None:
        scale_refs_dir = REF_SPRITES_DIR / "scale_references"
        if not scale_refs_dir.is_dir():
            return
        for char_dir in scale_refs_dir.iterdir():
            if not char_dir.is_dir():
                continue
            name = char_dir.name
            img_path = char_dir / f"{name}.png"
            if not img_path.exists():
                pngs = list(char_dir.glob("*.png"))
                if not pngs:
                    continue
                img_path = pngs[0]
            ref_scale = 1.0
            yml_path = char_dir / "character.yml"
            if yml_path.exists():
                try:
                    with yml_path.open("r", encoding="utf-8") as f:
                        meta = yaml.safe_load(f) or {}
                    ref_scale = float(meta.get("scale", 1.0))
                except Exception:
                    pass
            try:
                img = Image.open(img_path).convert("RGBA")
                self._references[name] = {"image": img, "scale": ref_scale}
            except Exception:
                pass

    def _current_reference(self) -> Optional[dict]:
        return self._references.get(self._ref_var.get())

    # ------------------------------------------------------------------
    def _redraw(self, *_args) -> None:
        self._ref_canvas.delete("all")
        self._user_canvas.delete("all")

        current_scale = self.scale_value
        self._scale_label.configure(text=f"{current_scale:.2f}")
        if self._on_change:
            self._on_change(current_scale)

        ref = self._current_reference()
        if ref is None:
            return
        rimg, r_scale = ref["image"], ref["scale"]
        r_engine_w = rimg.width * r_scale
        r_engine_h = rimg.height * r_scale

        if self._user_img is not None:
            u_engine_w = self._user_img.width * current_scale
            u_engine_h = self._user_img.height * current_scale
        else:
            u_engine_w = u_engine_h = 0

        max_w = max(r_engine_w, u_engine_w, 1)
        max_h = max(r_engine_h, u_engine_h, 1)
        view_scale = min(self._canv_w / max_w, self._canv_h / max_h, 1.0)

        r_disp = rimg.resize((max(1, int(r_engine_w * view_scale)),
                              max(1, int(r_engine_h * view_scale))),
                             Image.LANCZOS)
        self._img_refs["ref"] = ImageTk.PhotoImage(r_disp)
        self._ref_canvas.create_image(self._canv_w // 2, self._canv_h,
                                      anchor="s", image=self._img_refs["ref"])

        if self._user_img is not None:
            u_disp_w = max(1, int(u_engine_w * view_scale))
            u_disp_h = max(1, int(u_engine_h * view_scale))
            u_disp = self._user_img.resize((u_disp_w, u_disp_h), Image.LANCZOS)
            self._img_refs["usr"] = ImageTk.PhotoImage(u_disp)
            self._user_canvas.create_image(self._canv_w // 2, self._canv_h,
                                           anchor="s",
                                           image=self._img_refs["usr"])

            if self._eye_line_ratio is not None:
                img_top = self._canv_h - u_disp_h
                y_canvas = img_top + int(u_disp_h * self._eye_line_ratio)
                for canvas in (self._user_canvas, self._ref_canvas):
                    canvas.create_line(0, y_canvas, self._canv_w, y_canvas,
                                       fill=LINE_COLOR, width=2)
