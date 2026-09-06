"""
Click-to-set horizontal line picker.

A canvas showing an image with a horizontal guide line that follows the
mouse; clicking records the Y position in full-resolution image
coordinates. Supports a dashed pre-seeded suggestion line (e.g. the
auto-detected chin position).

Extracted from the crop logic in ui/screens/crop_step.py.
"""

import tkinter as tk
from pathlib import Path
from typing import Callable, Optional, Union

from PIL import Image, ImageTk

from ...config import BG_COLOR, CARD_BG, LINE_COLOR
from ..tk_common import get_primary_screen, compute_display_size

SUGGESTION_COLOR = "#FFB347"    # dashed orange for the pre-seeded suggestion


class YLinePickerCanvas(tk.Frame):
    """Image canvas with a click-to-set horizontal line."""

    def __init__(
        self,
        parent: tk.Widget,
        on_pick: Optional[Callable[[int], None]] = None,
        line_color: str = LINE_COLOR,
        max_w_ratio: float = 0.45,
        max_h_ratio: float = 0.55,
    ):
        super().__init__(parent, bg=CARD_BG, padx=4, pady=4)
        self._on_pick = on_pick
        self._line_color = line_color
        self._max_w_ratio = max_w_ratio
        self._max_h_ratio = max_h_ratio

        self._canvas = tk.Canvas(self, width=400, height=500, bg="black",
                                 highlightthickness=0)
        self._canvas.pack()
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Button-1>", self._on_click)

        self._image: Optional[Image.Image] = None
        self._tk_img: Optional[ImageTk.PhotoImage] = None
        self._disp_w = 0
        self._disp_h = 0
        self._scale_y = 1.0
        self._line_id: Optional[int] = None
        self._picked_line_id: Optional[int] = None
        self._suggestion_id: Optional[int] = None
        self._suggestion_y: Optional[int] = None    # full-res
        self.selected_y: Optional[int] = None       # full-res

    # ------------------------------------------------------------------
    def load_image(self, img: Union[Image.Image, Path],
                   initial_y: Optional[int] = None) -> None:
        """Show an image; optionally pre-select a Y (full-res coords)."""
        if isinstance(img, (str, Path)):
            img = Image.open(img)
        self._image = img.convert("RGBA")
        w, h = self._image.size

        sw, sh, *_ = get_primary_screen(self.winfo_toplevel())
        self._disp_w, self._disp_h = compute_display_size(
            sw, sh, w, h,
            max_w_ratio=self._max_w_ratio, max_h_ratio=self._max_h_ratio,
        )
        self._scale_y = h / max(1, self._disp_h)

        self._canvas.configure(width=self._disp_w, height=self._disp_h)
        disp = self._image.resize((self._disp_w, self._disp_h), Image.LANCZOS)
        self._tk_img = ImageTk.PhotoImage(disp)
        self._canvas.delete("all")
        self._canvas.create_image(0, 0, anchor="nw", image=self._tk_img)
        self._line_id = None
        self._picked_line_id = None
        self._suggestion_id = None
        self._suggestion_y = None
        self.selected_y = None

        if initial_y is not None:
            self._set_selection(initial_y, notify=False)

    def set_suggestion(self, y: int) -> None:
        """Draw a dashed suggestion line at full-res Y (does not select)."""
        if self._image is None:
            return
        self._suggestion_y = y
        disp_y = int(y / self._scale_y)
        disp_y = max(0, min(disp_y, self._disp_h))
        if self._suggestion_id is not None:
            self._canvas.delete(self._suggestion_id)
        self._suggestion_id = self._canvas.create_line(
            0, disp_y, self._disp_w, disp_y,
            fill=SUGGESTION_COLOR, width=2, dash=(6, 4),
        )

    def accept_suggestion(self) -> None:
        """Select the suggested Y, if one was set."""
        if self._suggestion_y is not None:
            self._set_selection(self._suggestion_y)

    # ------------------------------------------------------------------
    def _on_motion(self, event) -> None:
        if self._image is None:
            return
        y = max(0, min(event.y, self._disp_h))
        if self._line_id is None:
            self._line_id = self._canvas.create_line(
                0, y, self._disp_w, y, fill=self._line_color, width=2,
            )
        else:
            self._canvas.coords(self._line_id, 0, y, self._disp_w, y)

    def _on_click(self, event) -> None:
        if self._image is None:
            return
        real_y = int(event.y * self._scale_y)
        real_y = max(1, min(real_y, self._image.size[1]))
        self._set_selection(real_y)

    def _set_selection(self, real_y: int, notify: bool = True) -> None:
        self.selected_y = real_y
        disp_y = int(real_y / self._scale_y)
        disp_y = max(0, min(disp_y, self._disp_h))
        if self._picked_line_id is None:
            self._picked_line_id = self._canvas.create_line(
                0, disp_y, self._disp_w, disp_y,
                fill=self._line_color, width=3,
            )
        else:
            self._canvas.coords(self._picked_line_id,
                                0, disp_y, self._disp_w, disp_y)
        if notify and self._on_pick:
            self._on_pick(real_y)
