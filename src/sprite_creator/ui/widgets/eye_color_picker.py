"""
Two-stage eye-line + name-color picker.

Stage 1: a horizontal guide follows the mouse; clicking the eyes records
eye_line_ratio (y / image height). Stage 2: a crosshair follows the
mouse; clicking the hair samples the name color (transparent pixels fall
back to the default brown). Clicking again re-picks the color.

Extracted from EyeLineStep in ui/screens/finalization_steps.py.
"""

import tkinter as tk
from pathlib import Path
from typing import Callable, Optional, Union

from PIL import Image, ImageTk

from ...config import (
    BG_COLOR,
    CARD_BG,
    TEXT_SECONDARY,
    ACCENT_COLOR,
    BODY_FONT,
    LINE_COLOR,
)
from ..tk_common import get_primary_screen, compute_display_size

DEFAULT_NAME_COLOR = "#915f40"


class EyeLineNameColorPicker(tk.Frame):
    """Canvas widget yielding .eye_line_ratio and .name_color."""

    def __init__(
        self,
        parent: tk.Widget,
        on_complete: Optional[Callable[[float, str], None]] = None,
        max_w_ratio: float = 0.45,
        max_h_ratio: float = 0.55,
    ):
        super().__init__(parent, bg=BG_COLOR)
        self._on_complete = on_complete
        self._max_w_ratio = max_w_ratio
        self._max_h_ratio = max_h_ratio

        self._instruction = tk.Label(
            self, text="Step 1: Click on the character's eyes to set the eye line.",
            bg=BG_COLOR, fg=TEXT_SECONDARY, font=BODY_FONT,
        )
        self._instruction.pack(pady=(0, 8))

        container = tk.Frame(self, bg=CARD_BG, padx=4, pady=4)
        container.pack()
        self._canvas = tk.Canvas(container, width=400, height=500, bg="black",
                                 highlightthickness=0)
        self._canvas.pack()
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Button-1>", self._on_click)

        status_row = tk.Frame(self, bg=BG_COLOR)
        status_row.pack(pady=(8, 0))
        self._status = tk.Label(status_row, text="", bg=BG_COLOR,
                                fg=ACCENT_COLOR, font=BODY_FONT)
        self._status.pack(side="left")
        self._color_preview = tk.Label(status_row, text="   ", width=4,
                                       bg=BG_COLOR, relief="solid",
                                       borderwidth=1)

        self._image: Optional[Image.Image] = None
        self._tk_img: Optional[ImageTk.PhotoImage] = None
        self._disp_w = 0
        self._disp_h = 0
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._stage = 1
        self._guide_id: Optional[int] = None
        self._reticle_ids = (None, None)

        self.eye_line_ratio: Optional[float] = None
        self.name_color: Optional[str] = None

    # ------------------------------------------------------------------
    def load_image(self, img: Union[Image.Image, Path]) -> None:
        if isinstance(img, (str, Path)):
            img = Image.open(img)
        self._image = img.convert("RGBA")
        w, h = self._image.size

        sw, sh, *_ = get_primary_screen(self.winfo_toplevel())
        self._disp_w, self._disp_h = compute_display_size(
            sw, sh, w, h,
            max_w_ratio=self._max_w_ratio, max_h_ratio=self._max_h_ratio,
        )
        self._scale_x = w / max(1, self._disp_w)
        self._scale_y = h / max(1, self._disp_h)

        self._canvas.configure(width=self._disp_w, height=self._disp_h)
        disp = self._image.resize((self._disp_w, self._disp_h), Image.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(disp)
        self._canvas.delete("all")
        self._canvas.create_image(0, 0, anchor="nw", image=self._tk_img)

        self._stage = 1
        self._guide_id = None
        self._reticle_ids = (None, None)
        self.eye_line_ratio = None
        self.name_color = None
        self._color_preview.pack_forget()
        self._status.configure(text="")
        self._instruction.configure(
            text="Step 1: Click on the character's eyes to set the eye line.")

    # ------------------------------------------------------------------
    def _draw_guide(self, y: int) -> None:
        y = max(0, min(int(y), self._disp_h))
        if self._guide_id is None:
            self._guide_id = self._canvas.create_line(
                0, y, self._disp_w, y, fill=LINE_COLOR, width=3)
        else:
            self._canvas.coords(self._guide_id, 0, y, self._disp_w, y)

    def _draw_reticle(self, x: int, y: int, arm: int = 16) -> None:
        x = max(0, min(int(x), self._disp_w))
        y = max(0, min(int(y), self._disp_h))
        h_id, v_id = self._reticle_ids
        if h_id is None:
            h_id = self._canvas.create_line(x - arm, y, x + arm, y,
                                            fill=LINE_COLOR, width=2)
            v_id = self._canvas.create_line(x, y - arm, x, y + arm,
                                            fill=LINE_COLOR, width=2)
            self._reticle_ids = (h_id, v_id)
        else:
            self._canvas.coords(h_id, x - arm, y, x + arm, y)
            self._canvas.coords(v_id, x, y - arm, x, y + arm)

    def _on_motion(self, event) -> None:
        if self._image is None:
            return
        if self._stage == 1:
            self._draw_guide(event.y)
        else:
            self._draw_reticle(event.x, event.y)

    def _on_click(self, event) -> None:
        if self._image is None:
            return
        if self._stage == 1:
            real_y = event.y * self._scale_y
            self.eye_line_ratio = real_y / self._image.size[1]
            if self._guide_id is not None:
                self._canvas.delete(self._guide_id)
                self._guide_id = None
            self._stage = 2
            self._draw_reticle(event.x, event.y)
            self._status.configure(text=f"Eye line: {self.eye_line_ratio:.3f}")
            self._instruction.configure(
                text="Step 2: Click on the hair to pick the name color.")
        else:
            rx = min(max(int(event.x * self._scale_x), 0), self._image.size[0] - 1)
            ry = min(max(int(event.y * self._scale_y), 0), self._image.size[1] - 1)
            px = self._image.getpixel((rx, ry))
            if len(px) == 4 and px[3] < 10:
                color = DEFAULT_NAME_COLOR
            else:
                color = f"#{px[0]:02x}{px[1]:02x}{px[2]:02x}"
            self.name_color = color
            self._color_preview.configure(bg=color)
            self._color_preview.pack(side="left", padx=(8, 0))
            self._status.configure(
                text=f"Eye line: {self.eye_line_ratio:.3f}, Color: {color}")
            self._instruction.configure(
                text="Done, click again to change the color.")
            if self._on_complete:
                self._on_complete(self.eye_line_ratio, color)
