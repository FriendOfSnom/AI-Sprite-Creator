"""
Selectable thumbnail grid.

A scrollable grid of image cards with click / Ctrl-click / Shift-click
selection, per-item caption, badge, and accent color (used to show pile
membership). Thumbnails are provided as PIL images; the grid keeps the
PhotoImage references alive.

The grid is VIRTUALISED: only the cards near the visible scroll viewport are
realised as widgets/PhotoImages at any moment, and cards scrolled out of view
are destroyed (freeing their X pixmaps). This keeps pixmap/memory use flat no
matter how many items the grid holds, so large datasets (hundreds/thousands of
stacks) render without exhausting X server pixmap memory. Cards are positioned
at absolute canvas coordinates on a fixed row pitch so the scrollable height is
known without building every cell.
"""

import gc
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageTk

from ...config import (
    BG_COLOR,
    CARD_BG,
    CARD_BG_HOVER,
    TEXT_COLOR,
    TEXT_SECONDARY,
    SMALL_FONT,
    SMALL_FONT_BOLD,
)


# Selection border color: deliberately not a pile/accent color so a selected
# card is never confused with a character-grouped one.
SELECTION_COLOR = "#FFFFFF"


@dataclass
class ThumbItem:
    """One grid cell."""
    id: str
    image: Image.Image          # already thumbnail-sized (or will be fitted)
    caption: str = ""
    badge: str = ""
    accent_color: str = ""      # border/caption color, e.g. pile color
    accent_label: str = ""      # small colored label above caption (pile name)


class ThumbnailGrid(tk.Frame):
    """Scrollable, selectable, virtualised grid of thumbnail cards."""

    def __init__(
        self,
        parent: tk.Widget,
        thumb_size: int = 150,
        columns: Optional[int] = None,
        on_selection_change: Optional[Callable[[List[str]], None]] = None,
        on_double_click: Optional[Callable[[str], None]] = None,
        centered: bool = False,
        click_toggles: bool = False,
    ):
        super().__init__(parent, bg=BG_COLOR)
        self._thumb_size = thumb_size
        self._fixed_columns = columns
        self._on_selection_change = on_selection_change
        self._on_double_click = on_double_click
        self._centered = centered
        self._click_toggles = click_toggles

        self._items: List[ThumbItem] = []
        self._id_to_index: Dict[str, int] = {}
        # Realised cells only (keyed by item INDEX): a card exists as a widget
        # only while it is near the viewport. Everything else is virtual.
        self._realized: Dict[int, tk.Frame] = {}
        self._win_ids: Dict[int, int] = {}       # index -> canvas window id
        self._photos: Dict[int, ImageTk.PhotoImage] = {}
        self._caption_labels: Dict[int, tk.Label] = {}

        self._selected: List[str] = []      # ordered selection (by id)
        self._anchor_index: Optional[int] = None

        # Layout, recomputed on width change.
        self._n_cols = 1
        self._cell_w = thumb_size
        self._cell_h = int(thumb_size * self._CARD_ASPECT)
        self._row_pitch = self._cell_h
        self._col_pitch = thumb_size
        self._last_width = -1

        self._canvas = tk.Canvas(self, bg=BG_COLOR, highlightthickness=0)
        # The scrollbar drives yview through a wrapper so a drag also refreshes
        # which cells are realised.
        self._scrollbar = tk.Scrollbar(self, orient="vertical",
                                       command=self._on_scrollbar, width=10)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._canvas.pack(fill="both", expand=True)

        self._canvas.bind("<Configure>", self._on_canvas_configure)
        self._canvas.bind("<MouseWheel>", self._on_mousewheel)
        self._canvas.bind("<Button-4>", self._on_mousewheel)
        self._canvas.bind("<Button-5>", self._on_mousewheel)

    # ------------------------------------------------------------------
    @property
    def selected_ids(self) -> List[str]:
        return list(self._selected)

    def set_items(self, items: List[ThumbItem]) -> None:
        """Replace grid contents; selection is preserved for surviving ids."""
        self._items = list(items)
        self._id_to_index = {item.id: i for i, item in enumerate(self._items)}
        keep = set(self._id_to_index)
        self._selected = [i for i in self._selected if i in keep]
        self._anchor_index = None
        self._canvas.yview_moveto(0)
        self._relayout(force_full=True)

    def clear_selection(self) -> None:
        self._selected = []
        self._anchor_index = None
        self._refresh_highlights()
        self._notify()

    def select_ids(self, ids: List[str]) -> None:
        valid = set(self._id_to_index)
        self._selected = [i for i in ids if i in valid]
        self._refresh_highlights()
        self._notify()

    def set_caption(self, item_id: str, text: str) -> None:
        """Update one card's caption in place (no rebuild). Updates the model so
        a re-realised card shows the new text; updates the live label if the
        card is currently on-screen."""
        idx = self._id_to_index.get(item_id)
        if idx is None:
            return
        self._items[idx].caption = text
        lbl = self._caption_labels.get(idx)
        if lbl is not None:
            lbl.configure(text=text)

    _GAP = 12               # space around each card
    _CHROME = 16            # card padx (6*2) + border (2*2)
    _CARD_ASPECT = 2.2      # card box is width x (width * this); fixed so every
                            # card is the SAME size regardless of the dataset
    _SCROLLBAR_PAD = 14     # right gutter reserved for the overlay scrollbar
    _TEXT_LINES = 2         # worst-case text rows under the image (badge +
                            # accent label, or a wrapped caption)
    _BUFFER_ROWS = 2        # extra rows realised above/below the viewport

    # ------------------------------------------------------------------
    def _compute_layout(self) -> Tuple[int, int]:
        """Return (n_columns, cell_width). Fixed columns if given, else fit as
        many hardcoded thumb_size-wide cards as the width allows."""
        available = max(1, self._canvas.winfo_width() - self._SCROLLBAR_PAD)
        if self._fixed_columns:
            n = max(1, self._fixed_columns)
            cell_w = max(40, available // n - self._CHROME - self._GAP)
        else:
            card_w = self._thumb_size
            n = max(1, available // (card_w + self._CHROME + self._GAP))
            cell_w = card_w
        return n, cell_w

    def _text_height(self) -> int:
        line = tkfont.Font(font=SMALL_FONT).metrics("linespace")
        return line * self._TEXT_LINES + 6

    def _relayout(self, force_full: bool = False) -> None:
        """Recompute geometry and the virtual scroll height, then realise the
        currently-visible cells."""
        n, cell_w = self._compute_layout()
        self._n_cols = n
        self._cell_w = cell_w
        self._cell_h = max(1, round(cell_w * self._CARD_ASPECT))
        self._col_pitch = cell_w + self._CHROME + self._GAP
        self._row_pitch = self._cell_h + self._CHROME + self._text_height() + self._GAP

        if force_full:
            self._destroy_all_cells()

        total_rows = (len(self._items) + n - 1) // n if self._items else 0
        content_h = self._GAP // 2 + total_rows * self._row_pitch
        width = max(1, self._canvas.winfo_width() - self._SCROLLBAR_PAD)
        self._canvas.configure(scrollregion=(0, 0, width, content_h))

        self._update_visible()
        self._sync_scrollbar()

    # ------------------------------------------------------------------
    def _visible_indices(self) -> List[int]:
        if not self._items:
            return []
        top = self._canvas.canvasy(0)
        view_h = max(1, self._canvas.winfo_height())
        bottom = top + view_h
        rp = max(1, self._row_pitch)
        total_rows = (len(self._items) + self._n_cols - 1) // self._n_cols
        first = max(0, int(top // rp) - self._BUFFER_ROWS)
        last = min(total_rows - 1, int(bottom // rp) + self._BUFFER_ROWS)
        indices: List[int] = []
        for row in range(first, last + 1):
            base = row * self._n_cols
            for col in range(self._n_cols):
                idx = base + col
                if idx < len(self._items):
                    indices.append(idx)
        return indices

    def _update_visible(self) -> None:
        """Realise cells inside the viewport (plus buffer); destroy the rest."""
        needed = set(self._visible_indices())
        # Free cells that scrolled away, releasing their pixmaps.
        for idx in [i for i in self._realized if i not in needed]:
            self._destroy_cell(idx)
        freed_any = False
        for idx in needed:
            if idx not in self._realized:
                self._create_cell(idx)
                freed_any = True
        if freed_any:
            # Reap the just-destroyed PhotoImages' pixmaps promptly.
            gc.collect()

    def _cell_xy(self, idx: int) -> Tuple[int, int]:
        row, col = divmod(idx, self._n_cols)
        x = self._GAP // 2 + col * self._col_pitch
        y = self._GAP // 2 + row * self._row_pitch
        return x, y

    def _destroy_cell(self, idx: int) -> None:
        win = self._win_ids.pop(idx, None)
        if win is not None:
            self._canvas.delete(win)
        frame = self._realized.pop(idx, None)
        if frame is not None:
            frame.destroy()
        self._photos.pop(idx, None)
        self._caption_labels.pop(idx, None)

    def _destroy_all_cells(self) -> None:
        for idx in list(self._realized):
            self._destroy_cell(idx)
        self._realized.clear()
        self._win_ids.clear()
        self._photos.clear()
        self._caption_labels.clear()
        gc.collect()

    # ------------------------------------------------------------------
    def _create_cell(self, index: int) -> None:
        item = self._items[index]
        selected = item.id in self._selected
        accent = SELECTION_COLOR if selected else (item.accent_color or CARD_BG)
        thick = 3 if selected else 2
        bg = CARD_BG_HOVER if selected else CARD_BG
        cell = tk.Frame(self._canvas, bg=bg, padx=6, pady=6,
                        highlightthickness=thick, highlightbackground=accent)

        cw, ch = self._cell_w, self._cell_h
        # Fixed card box: scale the image down by its LONGEST side to fit inside
        # (cw x ch), then center it on a transparent box that size.
        fitted = item.image.copy()
        fitted.thumbnail((cw, ch), Image.LANCZOS)
        box = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        box.paste(fitted, ((cw - fitted.width) // 2, (ch - fitted.height) // 2),
                  fitted if fitted.mode == "RGBA" else None)
        photo = ImageTk.PhotoImage(box)
        self._photos[index] = photo

        img_label = tk.Label(cell, image=photo, bg=CARD_BG)
        img_label.pack()

        if item.badge:
            tk.Label(cell, text=item.badge, bg=CARD_BG, fg=TEXT_SECONDARY,
                     font=(SMALL_FONT[0], 9)).pack()
        if item.accent_label:
            tk.Label(cell, text=item.accent_label, bg=CARD_BG,
                     fg=item.accent_color or TEXT_COLOR,
                     font=SMALL_FONT_BOLD).pack()
        if item.caption:
            cap = tk.Label(cell, text=item.caption, bg=CARD_BG, fg=TEXT_COLOR,
                           font=SMALL_FONT, wraplength=cw, justify="center")
            cap.pack()
            self._caption_labels[index] = cap

        for widget in [cell, img_label] + list(cell.winfo_children()):
            widget.bind("<Button-1>", lambda e, i=index: self._on_click(i, e))
            widget.bind("<Double-Button-1>",
                        lambda e, iid=item.id: self._handle_double(iid))
            widget.bind("<MouseWheel>", self._on_mousewheel)
            widget.bind("<Button-4>", self._on_mousewheel)
            widget.bind("<Button-5>", self._on_mousewheel)
            widget.configure(cursor="hand2")

        x, y = self._cell_xy(index)
        win = self._canvas.create_window((x, y), window=cell, anchor="nw")
        self._realized[index] = cell
        self._win_ids[index] = win

    # ------------------------------------------------------------------
    def _on_click(self, index: int, event) -> None:
        item_id = self._items[index].id
        ctrl = bool(event.state & 0x0004)
        shift = bool(event.state & 0x0001)

        if shift and self._anchor_index is not None:
            lo, hi = sorted((self._anchor_index, index))
            range_ids = [self._items[i].id for i in range(lo, hi + 1)]
            if ctrl:
                for rid in range_ids:
                    if rid not in self._selected:
                        self._selected.append(rid)
            else:
                self._selected = range_ids
        elif ctrl or self._click_toggles:
            if item_id in self._selected:
                self._selected.remove(item_id)
            else:
                self._selected.append(item_id)
            self._anchor_index = index
        else:
            self._selected = [item_id]
            self._anchor_index = index

        self._refresh_highlights()
        self._notify()

    def _handle_double(self, item_id: str) -> None:
        if self._on_double_click:
            self._on_double_click(item_id)

    def _refresh_highlights(self) -> None:
        selected = set(self._selected)
        for idx, cell in self._realized.items():
            item = self._items[idx]
            if item.id in selected:
                cell.configure(highlightbackground=SELECTION_COLOR,
                               highlightthickness=3, bg=CARD_BG_HOVER)
            else:
                accent = item.accent_color or CARD_BG
                cell.configure(highlightbackground=accent,
                               highlightthickness=2, bg=CARD_BG)

    def _notify(self) -> None:
        if self._on_selection_change:
            self._on_selection_change(list(self._selected))

    # ------------------------------------------------------------------
    def _sync_scrollbar(self) -> None:
        """Show the overlay scrollbar only when the content overflows."""
        try:
            x0, y0, x1, y1 = self._canvas.cget("scrollregion").split()
            content_h = float(y1)
            canvas_h = self._canvas.winfo_height()
        except (tk.TclError, ValueError):
            return
        if content_h > canvas_h + 2:
            if not self._scrollbar.winfo_ismapped():
                self._scrollbar.place(relx=1.0, rely=0.0, relheight=1.0,
                                      anchor="ne")
        elif self._scrollbar.winfo_ismapped():
            self._scrollbar.place_forget()
            self._canvas.yview_moveto(0)

    def _on_canvas_configure(self, event=None) -> None:
        # Relayout only when the width actually changed (the window is fixed
        # size, so this normally fires once after mapping); a height change just
        # needs a visibility refresh.
        width = self._canvas.winfo_width()
        if width != self._last_width:
            self._last_width = width
            self._relayout(force_full=True)
        else:
            self._update_visible()
            self._sync_scrollbar()

    def _on_scrollbar(self, *args) -> None:
        self._canvas.yview(*args)
        self._update_visible()

    def _on_mousewheel(self, event) -> str:
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if event.delta > 0 else 1
        self._canvas.yview_scroll(delta, "units")
        self._update_visible()
        return "break"
