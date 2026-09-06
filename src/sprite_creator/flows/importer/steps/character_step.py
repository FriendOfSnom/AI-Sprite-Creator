"""
Step 5, Finalize Characters: iterate over the character piles; for each,
collect crop lines (mid-thigh and chin, per stack), eye line + name
color, scale, and name/voice, then write the ST character folder.

Finished piles are skipped on re-entry, so a half-finalized import
resumes exactly where it stopped.
"""

import csv
import random
import threading
import tkinter as tk
from tkinter import messagebox
from typing import List, Optional, Tuple

from PIL import Image

from ....config import (
    BG_COLOR,
    TEXT_COLOR,
    TEXT_SECONDARY,
    PAGE_TITLE_FONT,
    SECTION_FONT,
    BODY_FONT,
    SMALL_FONT,
    LINE_COLOR,
    NAMES_CSV_PATH,
)
from ....ui.screens.base import WizardStep
from ....ui.tk_common import (
    create_primary_button,
    create_secondary_button,
    create_segmented_control,
)
from ....ui.widgets import (
    YLinePickerCanvas,
    EyeLineNameColorPicker,
    ScaleComparePanel,
    ThumbnailGrid,
    ThumbItem,
)
from .. import workspace
from ..gc_guard import guard_begin, guard_end
from ..finalize import (
    base_stack_id,
    finalize_pile,
    stack_trim_bbox,
    _load_rgba,
)

# Deliberately different colors per crop phase so it's unmistakable which
# line you're setting, mid-thigh (blue) vs chin (pink).
MIDTHIGH_CUE_COLOR = "#4A90D9"
CHIN_CUE_COLOR = "#FF5C8A"

# Per-pose scale nudges move in clean, even 0.02 steps (1.00, 1.02, ...).
NUDGE_STEP = 0.02


def load_name_pool() -> Tuple[List[str], List[str]]:
    """Load girl/boy name pools from the bundled names.csv."""
    girls, boys = [], []
    try:
        with open(NAMES_CSV_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = (row.get("name") or "").strip()
                gender = (row.get("gender") or "").strip().lower()
                if not name:
                    continue
                if gender == "girl":
                    girls.append(name)
                elif gender == "boy":
                    boys.append(name)
    except Exception:
        pass
    return (girls or ["Sakura", "Emily", "Yuki", "Hannah"],
            boys or ["Takashi", "Ethan", "Liam", "Alex"])


class CharacterStep(WizardStep):
    STEP_ID = "imp_character"
    STEP_TITLE = "Finalize"
    STEP_NUMBER = 5
    STEP_HELP = """Turn each character pile into an ST character folder.

For every character you'll set, in order:

1. MID-THIGH CROP (one click per pose), click at the character's
   mid-thigh; everything below is removed. ST sprites are typically
   cropped here.
2. CHIN CROP (one click per pose), sets the bottom of the face slices
   used for expressions. Click just under the chin. This line also
   measures the head for pose-size normalization, so place it with care.
3. OUTFIT NAMES, name this character's outfits ("uniform", "casual"…);
   quick-name chips fill several at once. These become the in-game
   outfit names (each outfit is its own pose in the final folder).
4. EYE LINE & NAME COLOR, click the eyes, then click the hair.
5. SCALE, other poses are size-matched to the BASE pose (the base is
   locked; nudge the rest if any look off), then the base is matched
   against a reference ST sprite for the in-game size.
6. NAME & VOICE, pick a voice; a random name is suggested (reroll or
   type your own).

Then "Create Character" writes the folder: pose letters with outfits/
and faces/, character.yml, expression sheets, and showChar files. Every
outfit/expression combination is verified pixel-for-pixel against the
original download; any mismatch is flagged on the summary."""
    STEP_TIP = ""

    PHASE_MIDTHIGH = "midthigh"
    PHASE_CHIN = "chin"
    PHASE_OUTFITS = "outfits"
    PHASE_EYE = "eye"
    PHASE_SCALE = "scale"
    PHASE_NAME = "name"

    def __init__(self, wizard, state):
        super().__init__(wizard, state)
        # HARDCODED card width (scaled by the window scale factor); matches the
        # Sort Characters grid (review_step.THUMB_SIZE) so every card is the same
        # fixed size everywhere. Keep this literal in sync with THUMB_SIZE there.
        self._scale = getattr(self.wizard, "ui_scale", 1.0)
        self._thumb_px = max(1, round(150 * self._scale))
        self._header_var: tk.StringVar = None
        self._content: tk.Frame = None
        self._pile_ids: List[str] = []
        self._pile_index = 0
        self._phases: List[Tuple[str, Optional[str]]] = []
        self._phase_index = 0
        self._girls, self._boys = load_name_pool()
        # per-phase widget refs
        self._picker: YLinePickerCanvas = None
        self._eye_picker: EyeLineNameColorPicker = None
        self._scale_panel: ScaleComparePanel = None
        self._name_entry: tk.Entry = None
        self._voice_control = None
        self._trim_cache = {}

    # ------------------------------------------------------------------
    def build_ui(self, parent: tk.Frame) -> None:
        parent.configure(bg=BG_COLOR)
        tk.Label(
            parent, text="Finalize Characters", bg=BG_COLOR, fg=TEXT_COLOR,
            font=PAGE_TITLE_FONT,
        ).pack(pady=(0, 4))

        self._header_var = tk.StringVar(value="")
        tk.Label(
            parent, textvariable=self._header_var, bg=BG_COLOR,
            fg=TEXT_COLOR, font=SECTION_FONT,
        ).pack(pady=(0, 8))

        self._content = tk.Frame(parent, bg=BG_COLOR)
        self._content.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    def on_enter(self) -> None:
        groups = self.state.groups
        # Back is live here so a redo run can return to the focused Sort step
        # (and normal runs can step back to Review).
        try:
            self.wizard._back_btn.configure(state="normal")
        except Exception:
            pass
        # Redo-from-Summary focuses on ONE character; otherwise all of them.
        focus = self.state.redo_focus_pile
        if focus and focus in groups.piles:
            self._pile_ids = [focus]
        else:
            self._pile_ids = sorted(groups.piles.keys())
        self._trim_cache = {}
        # first unfinished pile
        self._pile_index = 0
        while (self._pile_index < len(self._pile_ids)
               and groups.piles[self._pile_ids[self._pile_index]].finalize.done):
            self._pile_index += 1

        if self._pile_index >= len(self._pile_ids):
            self._finish_step()
        else:
            self.wizard._next_btn.configure(state="disabled")
            self._begin_pile()

    def _step_index(self, step_id: str):
        for i, step in enumerate(self.wizard._steps):
            if step.STEP_ID == step_id:
                return i
        return None

    def _finish_step(self) -> None:
        """After the last pile: in a redo run, jump straight back to Summary
        (which clears the focus flag and re-shows all characters); otherwise
        show the all-characters redo panel."""
        if self.state.redo_focus_pile:
            idx = self._step_index("imp_summary")
            if idx is not None:
                self.wizard.go_to_step(idx)
                return
        self._show_all_done()

    def _current_pile(self):
        return self.state.groups.piles[self._pile_ids[self._pile_index]]

    def _stack_ids(self) -> List[str]:
        return sorted(self._current_pile().stack_ids,
                      key=lambda s: int(s.split("_")[1]))

    def _trim(self, stack_id: str):
        if stack_id not in self._trim_cache:
            stack = self.state.groups.stacks[stack_id]
            self._trim_cache[stack_id] = stack_trim_bbox(
                self.state.raw_dir, stack)
        return self._trim_cache[stack_id]

    def _stack_rep_image(self, stack_id: str) -> Image.Image:
        stack = self.state.groups.stacks[stack_id]
        return _load_rgba(self.state.raw_dir, stack.rep_image,
                          self._trim(stack_id))

    # ------------------------------------------------------------------
    def _begin_pile(self) -> None:
        pile = self._current_pile()
        self._phases = []
        for sid in self._stack_ids():
            self._phases.append((self.PHASE_MIDTHIGH, sid))
        for sid in self._stack_ids():
            self._phases.append((self.PHASE_CHIN, sid))
        self._phases += [(self.PHASE_OUTFITS, None), (self.PHASE_EYE, None),
                         (self.PHASE_SCALE, None), (self.PHASE_NAME, None)]
        self._phase_index = 0
        self._show_phase()

    def _phase_header(self) -> str:
        pile = self._current_pile()
        total_done = sum(1 for pid in self._pile_ids
                         if self.state.groups.piles[pid].finalize.done)
        return (f"Character {self._pile_index + 1} of {len(self._pile_ids)}"
                f", {pile.name}")

    def _clear_content(self) -> None:
        for child in self._content.winfo_children():
            child.destroy()

    def _show_phase(self) -> None:
        self._clear_content()
        phase, stack_id = self._phases[self._phase_index]
        self._header_var.set(self._phase_header())

        if phase == self.PHASE_MIDTHIGH:
            self._build_midthigh(stack_id)
        elif phase == self.PHASE_CHIN:
            self._build_chin(stack_id)
        elif phase == self.PHASE_OUTFITS:
            self._build_outfit_names()
        elif phase == self.PHASE_EYE:
            self._build_eye()
        elif phase == self.PHASE_SCALE:
            self._build_scale()
        else:
            self._build_name()

    def _phase_badge(self, text: str, color: str) -> None:
        """A bold, full-width colored bar so it's obvious which crop line
        (mid-thigh vs chin) is being set right now."""
        bar = tk.Frame(self._content, bg=color)
        bar.pack(fill="x", pady=(0, 8))
        tk.Label(bar, text=text, bg=color, fg="#FFFFFF",
                 font=SECTION_FONT, pady=6).pack()

    def _phase_nav(self, parent, on_continue, continue_text="Continue") -> None:
        row = tk.Frame(parent, bg=BG_COLOR)
        row.pack(pady=(16, 0))
        if self._phase_index > 0:
            create_secondary_button(
                row, "Previous", self._prev_phase, width=16, large=True,
            ).pack(side="left", padx=(0, 12))
        create_primary_button(row, continue_text, on_continue, width=16,
                              large=True).pack(side="left")

    def _prev_phase(self) -> None:
        if self._phase_index > 0:
            self._phase_index -= 1
            self._show_phase()

    def _next_phase(self) -> None:
        self._phase_index += 1
        if self._phase_index < len(self._phases):
            self._show_phase()

    # ------------------------------------------------------------------
    # Phase builders
    # ------------------------------------------------------------------
    def _build_midthigh(self, stack_id: str) -> None:
        sids = self._stack_ids()
        pos = sids.index(stack_id) + 1
        self._phase_badge(f"MID-THIGH CROP  ·  pose {pos} of {len(sids)}",
                          MIDTHIGH_CUE_COLOR)
        tk.Label(
            self._content,
            text="Click at the character's MID-THIGH. Everything below the "
                 "line is cropped off.",
            bg=BG_COLOR, fg=TEXT_SECONDARY, font=BODY_FONT,
        ).pack(pady=(0, 6))

        self._picker = YLinePickerCanvas(self._content,
                                         line_color=MIDTHIGH_CUE_COLOR)
        self._picker.pack()

        img = self._stack_rep_image(stack_id)
        fin = self._current_pile().finalize
        initial = fin.mid_thigh_y.get(stack_id)
        if initial is None:
            # seed from a previously set stack's relative position
            for other_sid, y in fin.mid_thigh_y.items():
                other_img = self._stack_rep_image(other_sid)
                initial = int(y / other_img.height * img.height)
                break
        self._picker.load_image(img, initial_y=initial)

        def cont():
            if self._picker.selected_y is None:
                messagebox.showwarning(
                    "No Crop Set", "Click on the image to set the mid-thigh line.",
                    parent=self.wizard.root)
                return
            # Clamp to the SHORTEST image of the pose: variants may be
            # longer/shorter crops of the same render, and every outfit
            # must crop to the same height.
            stack = self.state.groups.stacks[stack_id]
            trim = self._trim(stack_id)
            trim_top = trim[1] if trim else 0
            min_h = min(
                (self.state.groups.images[n].size[1]
                 for n in stack.all_images()
                 if n in self.state.groups.images),
                default=10**9,
            ) - trim_top
            fin.mid_thigh_y[stack_id] = min(self._picker.selected_y, min_h)
            workspace.save_groups(self.state)
            self._next_phase()

        self._phase_nav(self._content, cont)

    def _build_chin(self, stack_id: str) -> None:
        sids = self._stack_ids()
        pos = sids.index(stack_id) + 1
        self._phase_badge(f"CHIN LINE  ·  pose {pos} of {len(sids)}",
                          CHIN_CUE_COLOR)
        tk.Label(
            self._content,
            text="Now the CHIN line. Click barely below the character's chin "
                 "(near the top, right under the head). This sets the bottom of "
                 "the face slices used for expressions.",
            bg=BG_COLOR, fg=TEXT_SECONDARY, font=BODY_FONT, wraplength=900,
        ).pack(pady=(0, 6))

        self._picker = YLinePickerCanvas(self._content,
                                         line_color=CHIN_CUE_COLOR)
        self._picker.pack()

        img = self._stack_rep_image(stack_id)
        fin = self._current_pile().finalize

        # Fully manual: no auto-suggestion. The chin line feeds face slices,
        # verification, AND pose-size normalization, so it must be a
        # deliberate click, only a previously saved value (redo) pre-fills.
        self._picker.load_image(img, initial_y=fin.chin_y.get(stack_id))

        def cont():
            if self._picker.selected_y is None:
                messagebox.showwarning(
                    "No Crop Set", "Click on the image to set the chin line.",
                    parent=self.wizard.root)
                return
            chin = self._picker.selected_y
            mid = fin.mid_thigh_y.get(stack_id)
            # Reject the whole lower body PLUS a 10%-of-height buffer above the
            # mid-thigh line, a click that low is almost always a leftover
            # mid-thigh reflex, not a chin.
            if mid is not None:
                guard = int(0.10 * img.height)
                if chin >= mid - guard:
                    messagebox.showwarning(
                        "That's Not the Chin",
                        "This is the CHIN line now, not the mid-thigh. Click "
                        "just under the character's chin, near the TOP of the "
                        "image, well above the mid-thigh crop.",
                        parent=self.wizard.root)
                    return
            fin.chin_y[stack_id] = chin
            workspace.save_groups(self.state)
            self._next_phase()

        self._phase_nav(self._content, cont)

    COMMON_OUTFIT_NAMES = [
        "uniform", "casual", "formal", "gym", "swimsuit", "underwear",
        "nude", "pajamas", "maid", "sport", "party", "winter",
    ]

    def _build_outfit_names(self) -> None:
        """Name this character's outfits, cosmetic in-game labels (each
        outfit is its own pose in the final ST folder).

        Works like the Sort Characters page: select cards below (plain click
        replaces, Ctrl-click adds, Shift-click ranges), then name them from
        the ONE input row up top (type a name or click a quick-name chip).
        All naming lives at the top; the cards are purely for selecting.
        """
        from ....config import CARD_BG, CARD_BG_HOVER, ACCENT_COLOR
        THUMB = self._thumb_px     # match the Sort Characters cards

        tk.Label(
            self._content,
            text="Give every outfit a name. Each becomes its own pose in the "
                 "game, so the numbers on the cards are just placeholders. "
                 "Select cards below, then type a name or click a quick name "
                 "up here to name them all at once.",
            bg=BG_COLOR, fg=TEXT_SECONDARY, font=BODY_FONT, wraplength=1100,
        ).pack(pady=(0, 6))

        # Map a stable string key -> outfit group (group ids repeat across
        # stacks, so combine with the stack id).
        self._outfit_by_key = {}
        sids = self._stack_ids()
        groups = self.state.groups

        # ---- Top naming bar: text entry + quick-name chips on one row ----
        bar = tk.Frame(self._content, bg=BG_COLOR)
        bar.pack(pady=(0, 4))

        tk.Label(bar, text="Outfit name:", bg=BG_COLOR, fg=TEXT_COLOR,
                 font=BODY_FONT).pack(side="left", padx=(0, 6))
        self._name_var = tk.StringVar(value="")
        name_entry = tk.Entry(bar, font=BODY_FONT, width=18,
                              textvariable=self._name_var)
        name_entry.pack(side="left", padx=(0, 12))

        tk.Label(bar, text="Quick names:", bg=BG_COLOR, fg=TEXT_SECONDARY,
                 font=BODY_FONT).pack(side="left", padx=(0, 8))
        for name in self.COMMON_OUTFIT_NAMES:
            chip = tk.Label(bar, text=name, bg=CARD_BG, fg=TEXT_COLOR,
                            font=BODY_FONT, padx=12, pady=5, cursor="hand2")
            chip.pack(side="left", padx=3)
            chip.bind("<Button-1>", lambda e, n=name: apply_name(n))
            chip.bind("<Enter>", lambda e, c=chip: c.configure(bg=CARD_BG_HOVER))
            chip.bind("<Leave>", lambda e, c=chip: c.configure(bg=CARD_BG))

        self._naming_hint = tk.StringVar(
            value="Select one or more cards below to name them.")
        tk.Label(self._content, textvariable=self._naming_hint, bg=BG_COLOR,
                 fg=TEXT_SECONDARY, font=SMALL_FONT).pack()

        # ---- Selectable card grid (same widget as Sort Characters) ----
        # Plain click toggles cards in/out; Ctrl/Shift still work.
        grid = ThumbnailGrid(
            self._content, thumb_size=THUMB, click_toggles=True,
            on_selection_change=lambda ids: on_select(ids),
        )
        grid.pack(fill="both", expand=True, pady=(6, 0))
        self._outfit_grid = grid

        def apply_name(value):
            """Apply `value` to every selected card, then clear the selection
            so the next set can be picked without deselecting by hand."""
            ids = grid.selected_ids
            if not ids:
                self._naming_hint.set(
                    "Select one or more cards first, then name them.")
                return
            value = value.strip()
            self._name_var.set(value)
            for key in ids:
                self._outfit_by_key[key].label = value
                grid.set_caption(key, value or "(unnamed)")
            workspace.save_groups(self.state)
            grid.clear_selection()      # fires on_select([]) -> resets the hint
            self._naming_hint.set(
                f"Named {len(ids)} outfit(s) \"{value}\". Select the next set.")

        def on_type(event=None):
            """Live-apply the top entry to the current selection as you type."""
            ids = grid.selected_ids
            if not ids:
                return
            value = self._name_var.get().strip()
            for key in ids:
                self._outfit_by_key[key].label = value
                grid.set_caption(key, value or "(unnamed)")
        name_entry.bind("<KeyRelease>", on_type)
        # Enter = commit + save (also lets the user "click away" cleanly).
        name_entry.bind("<Return>",
                        lambda e: apply_name(self._name_var.get()))

        def on_select(ids):
            if not ids:
                self._naming_hint.set(
                    "Select one or more cards below to name them.")
                self._name_var.set("")
                return
            labels = {self._outfit_by_key[k].label for k in ids}
            # Reflect the shared name (blank if the selection is mixed).
            self._name_var.set(next(iter(labels)) if len(labels) == 1 else "")
            self._naming_hint.set(
                f"{len(ids)} selected, type a name or click a quick name above.")

        # Build cards. Caption shows the current name (placeholder number).
        items = []
        for sid in sids:
            stack = groups.stacks[sid]
            trim = self._trim(sid)
            for group in stack.outfits:
                key = f"{sid}:{group.id}"
                self._outfit_by_key[key] = group
                try:
                    thumb = _load_rgba(self.state.raw_dir, group.images[0],
                                       trim)
                except Exception:
                    thumb = Image.new("RGBA", (THUMB, THUMB), (0, 0, 0, 0))
                items.append(ThumbItem(id=key, image=thumb,
                                       caption=group.label or "(unnamed)"))
        grid.set_items(items)

        def cont():
            unnamed = sum(1 for g in self._outfit_by_key.values()
                          if not g.label or g.label.strip().isdigit())
            if unnamed and not messagebox.askyesno(
                    "Unnamed Outfits",
                    f"{unnamed} outfit(s) still have a placeholder number "
                    f"instead of a name. Continue anyway?",
                    parent=self.wizard.root):
                return
            workspace.save_groups(self.state)
            self._next_phase()

        self._phase_nav(self._content, cont)

    def _pile_preview_image(self) -> Image.Image:
        """First stack's rep, mid-thigh cropped and normalized, matches
        what finalize will actually write, so the scale comparison and eye
        line are measured against the real output size."""
        fin = self._current_pile().finalize
        sid = self._stack_ids()[0]
        img = self._stack_rep_image(sid)
        mid = fin.mid_thigh_y.get(sid, img.height)
        img = img.crop((0, 0, img.width, min(mid, img.height)))
        factor = fin.pose_scale.get(sid, 1.0)
        if abs(factor - 1.0) > 1e-4:
            img = img.resize((max(1, round(img.width * factor)),
                              max(1, round(img.height * factor))),
                             Image.LANCZOS)
        return img

    def _build_eye(self) -> None:
        self._eye_picker = EyeLineNameColorPicker(self._content)
        self._eye_picker.pack()
        self._eye_picker.load_image(self._pile_preview_image())

        fin = self._current_pile().finalize

        def cont():
            if self._eye_picker.eye_line_ratio is None:
                messagebox.showwarning(
                    "Missing Eye Line",
                    "Click on the character's eyes first.",
                    parent=self.wizard.root)
                return
            if not self._eye_picker.name_color:
                messagebox.showwarning(
                    "Missing Name Color",
                    "Click on the hair to pick the name color.",
                    parent=self.wizard.root)
                return
            fin.eye_line_ratio = round(self._eye_picker.eye_line_ratio, 4)
            fin.name_color = self._eye_picker.name_color
            workspace.save_groups(self.state)
            self._next_phase()

        self._phase_nav(self._content, cont)

    def _build_scale(self) -> None:
        fin = self._current_pile().finalize
        sids = self._stack_ids()

        # Every pose starts at 1.00 (per user preference). The chin-line
        # head-height auto-estimate swung too much with hoods/hats and felt
        # random, so the user nudges only the poses that visibly look off in
        # the preview strip. If factors exist from an earlier anchoring,
        # re-anchor so the base stays exactly 1.0 (the global scale below is
        # measured against the base, so the base must never change size).
        base_sid = base_stack_id(self._current_pile())
        if not fin.pose_scale:
            fin.pose_scale = {sid: 1.0 for sid in sids}
        else:
            base_f = fin.pose_scale.get(base_sid, 1.0) or 1.0
            if abs(base_f - 1.0) > 1e-4:
                fin.pose_scale = {sid: round(f / base_f, 4)
                                  for sid, f in fin.pose_scale.items()}
                workspace.save_groups(self.state)

        # Vertical budget so the whole phase (preview row + controls +
        # canvases + nav) fits the viewport on anything from 1080p to 4K.
        self.wizard.root.update_idletasks()
        viewport_h = self.wizard._scroll_canvas.winfo_height()
        if viewport_h < 200:
            viewport_h = int(self.wizard.root.winfo_screenheight() * 0.70)
        self._preview_h = min(round(200 * self._scale),
                              max(round(120 * self._scale),
                                  int(viewport_h * 0.20)))

        # Pose-size preview row: every pose rep at its normalized size, so
        # mismatched poses are obvious and nudgeable before the global scale.
        if len(sids) > 1:
            tk.Label(
                self._content,
                text="Pose sizes, normalized to the BASE pose (shown in the "
                     "center). The base is locked; nudge any other pose that "
                     "looks off so every pose shows the character at the same "
                     "size. Scroll the strip sideways to see them all.",
                bg=BG_COLOR, fg=TEXT_SECONDARY, font=BODY_FONT, wraplength=1100,
            ).pack(pady=(0, 4))

            # Horizontally scrollable strip, many poses overflow the screen.
            strip = tk.Frame(self._content, bg=BG_COLOR)
            strip.pack(fill="x", pady=(0, 10))
            pcanvas = tk.Canvas(strip, bg=BG_COLOR, highlightthickness=0,
                                height=self._preview_h + 46)
            hbar = tk.Scrollbar(strip, orient="horizontal",
                                command=pcanvas.xview, width=10)
            pcanvas.configure(xscrollcommand=hbar.set)
            hbar.pack(side="bottom", fill="x")
            pcanvas.pack(side="top", fill="x")
            self._preview_canvas = pcanvas
            self._preview_row = tk.Frame(pcanvas, bg=BG_COLOR)
            pcanvas.create_window((0, 0), window=self._preview_row, anchor="nw")
            self._preview_row.bind(
                "<Configure>",
                lambda e: pcanvas.configure(scrollregion=pcanvas.bbox("all")))

            def _pwheel(event):
                fwd = (getattr(event, "num", None) == 5
                       or getattr(event, "delta", 0) < 0)
                pcanvas.xview_scroll(1 if fwd else -1, "units")
                return "break"
            self._preview_wheel = _pwheel
            for w in (pcanvas, self._preview_row):
                w.bind("<MouseWheel>", _pwheel)
                w.bind("<Button-4>", _pwheel)
                w.bind("<Button-5>", _pwheel)

            self._preview_photos = {}
            self._build_pose_preview_row()

        # Remaining height after header/tip/banner (~170), preview row (if
        # shown), compare controls (~110), and the nav row (~90).
        used = 170 + 110 + 90
        if len(sids) > 1:
            used += self._preview_h + 60
        canvas_h = max(280, viewport_h - used)

        self._scale_panel = ScaleComparePanel(self._content,
                                              canvas_height=canvas_h)
        self._scale_panel.pack()
        self._scale_panel.load_user_image(self._pile_preview_image(),
                                          eye_line_ratio=fin.eye_line_ratio)

        def cont():
            fin.scale = round(self._scale_panel.scale_value, 3)
            workspace.save_groups(self.state)
            self._next_phase()

        self._phase_nav(self._content, cont)

    def _normalized_pose_thumb(self, stack_id: str) -> Image.Image:
        """Pose rep, mid-thigh cropped, at its normalized relative size."""
        fin = self._current_pile().finalize
        img = self._stack_rep_image(stack_id)
        mid = fin.mid_thigh_y.get(stack_id, img.height)
        img = img.crop((0, 0, img.width, min(mid, img.height)))
        factor = fin.pose_scale.get(stack_id, 1.0)
        return img, factor

    def _build_pose_preview_row(self, recenter: bool = True) -> None:
        for child in self._preview_row.winfo_children():
            child.destroy()
        self._preview_photos.clear()
        fin = self._current_pile().finalize
        sids = self._stack_ids()

        # Common view scale: fit the tallest normalized pose into the
        # budgeted preview height (screen-size aware)
        preview_h = getattr(self, "_preview_h", 200)
        normalized = {}
        for sid in sids:
            img, factor = self._normalized_pose_thumb(sid)
            normalized[sid] = (img, factor,
                               img.width * factor, img.height * factor)
        tallest = max(h for (_, _, _, h) in normalized.values()) or 1
        view = min(preview_h / tallest, 1.0)

        from PIL import ImageTk
        base_sid = base_stack_id(self._current_pile())

        # Put the base pose in the VISUAL CENTER of the strip so it sits next
        # to the poses being nudged (no looking across the whole screen).
        others = [s for s in sids if s != base_sid]
        mid = len(others) // 2
        display_order = others[:mid] + [base_sid] + others[mid:]

        wheel = getattr(self, "_preview_wheel", None)
        base_cell = None
        base_img_lbl = None
        for sid in display_order:
            img, factor, norm_w, norm_h = normalized[sid]
            disp = img.resize((max(1, int(norm_w * view)),
                               max(1, int(norm_h * view))), Image.LANCZOS)
            photo = ImageTk.PhotoImage(disp)
            self._preview_photos[sid] = photo

            cell = tk.Frame(self._preview_row, bg=BG_COLOR)
            cell.pack(side="left", padx=6, anchor="s")
            img_lbl = tk.Label(cell, image=photo, bg=BG_COLOR)
            img_lbl.pack(side="top")
            nudge_row = tk.Frame(cell, bg=BG_COLOR)
            nudge_row.pack()
            if sid == base_sid:
                base_cell = cell
                base_img_lbl = img_lbl
                # The base anchors the global scale below, locked.
                tk.Label(nudge_row, text="base", bg=BG_COLOR,
                         fg=TEXT_SECONDARY,
                         font=(BODY_FONT[0], 9, "bold")).pack(side="left")
            else:
                # Editable factor: click and type a value (e.g. 1.4) to set it
                # directly instead of clicking +/- many times.
                entry = tk.Entry(nudge_row, width=4, justify="center",
                                 font=(BODY_FONT[0], 9))
                entry.insert(0, f"{factor:.2f}")
                entry.pack(side="left")
                tk.Label(nudge_row, text="×", bg=BG_COLOR, fg=TEXT_SECONDARY,
                         font=(BODY_FONT[0], 9)).pack(side="left")

                def commit(ev=None, s=sid, e=entry, cur=factor):
                    try:
                        v = round(min(3.0, max(0.1, float(e.get()))), 2)
                    except ValueError:
                        e.delete(0, "end")
                        e.insert(0, f"{cur:.2f}")
                        return
                    if abs(v - cur) > 1e-9:
                        self._set_pose_scale(s, v)
                entry.bind("<Return>", commit)
                entry.bind("<FocusOut>", commit)

                for label, delta in (("−", -NUDGE_STEP), ("＋", NUDGE_STEP)):
                    btn = tk.Label(nudge_row, text=label, bg=BG_COLOR,
                                   fg=TEXT_COLOR, font=BODY_FONT,
                                   cursor="hand2", padx=4)
                    btn.pack(side="left")
                    btn.bind("<Button-1>",
                             lambda e, s=sid, d=delta: self._nudge_pose(s, d))

            # Let the wheel scroll the strip while hovering any cell.
            if wheel is not None:
                for w in (cell, img_lbl, nudge_row, *nudge_row.winfo_children()):
                    w.bind("<MouseWheel>", wheel)
                    w.bind("<Button-4>", wheel)
                    w.bind("<Button-5>", wheel)

        # Thin eye line from the base pose, drawn across the whole strip so
        # you can see at a glance whether the other poses' eyes line up (the
        # same idea as the guide on the big comparison below).
        if base_img_lbl is not None:
            self._draw_preview_eyeline(base_img_lbl)

        # Scroll so the (centered) base cell lands in the middle of the view
        # (only on first build, nudges shouldn't yank the view around).
        if (recenter and base_cell is not None
                and getattr(self, "_preview_canvas", None)):
            self._center_preview_on(base_cell)

    def _draw_preview_eyeline(self, base_img_lbl: tk.Label) -> None:
        ratio = self._current_pile().finalize.eye_line_ratio
        if ratio is None:
            return
        row = self._preview_row

        def _do():
            try:
                row.update_idletasks()
                top = base_img_lbl.winfo_rooty() - row.winfo_rooty()
                h = base_img_lbl.winfo_height()
                if h <= 1:
                    return
                eye_y = int(top + ratio * h)
                # Placed (not gridded) so it floats across every pose cell and
                # scrolls with the strip; rebuilds destroy it with the row.
                line = tk.Frame(row, bg=LINE_COLOR, height=2)
                line.place(x=0, y=eye_y, relwidth=1.0, height=2)
            except tk.TclError:
                pass
        row.after_idle(_do)

    def _center_preview_on(self, cell: tk.Frame) -> None:
        canvas = self._preview_canvas

        def _do():
            canvas.update_idletasks()
            total = self._preview_row.winfo_width()
            view_w = canvas.winfo_width()
            if total <= view_w or total <= 0:
                return          # everything fits; no scrolling needed
            center_x = cell.winfo_x() + cell.winfo_width() / 2
            frac = (center_x - view_w / 2) / (total - view_w)
            canvas.xview_moveto(min(1.0, max(0.0, frac)))
        canvas.after_idle(_do)

    def _nudge_pose(self, stack_id: str, delta: float) -> None:
        if stack_id == base_stack_id(self._current_pile()):
            return      # the base is locked, it anchors the global scale
        fin = self._current_pile().finalize
        current = fin.pose_scale.get(stack_id, 1.0)
        # Snap to the NUDGE_STEP grid so values stay on clean even numbers
        # (1.00, 1.02, 1.04, ...) no matter what the seeded start was.
        snapped = round((current + delta) / NUDGE_STEP) * NUDGE_STEP
        fin.pose_scale[stack_id] = round(min(3.0, max(0.1, snapped)), 2)
        workspace.save_groups(self.state)
        self._build_pose_preview_row(recenter=False)

    def _set_pose_scale(self, stack_id: str, value: float) -> None:
        """Set a pose's scale directly to a typed value (clamped to 0.1-3.0)."""
        if stack_id == base_stack_id(self._current_pile()):
            return      # the base is locked, it anchors the global scale
        fin = self._current_pile().finalize
        fin.pose_scale[stack_id] = round(min(3.0, max(0.1, value)), 2)
        workspace.save_groups(self.state)
        self._build_pose_preview_row(recenter=False)

    def _build_name(self) -> None:
        fin = self._current_pile().finalize

        tk.Label(
            self._content, text="Voice:", bg=BG_COLOR, fg=TEXT_COLOR,
            font=BODY_FONT,
        ).pack(pady=(6, 2))
        self._voice_control = create_segmented_control(
            self._content, ["Girl", "Boy"],
            default="Girl" if fin.voice != "boy" else "Boy",
            on_change=lambda v: reroll(),
        )
        self._voice_control.pack()

        tk.Label(
            self._content, text="Character name:", bg=BG_COLOR,
            fg=TEXT_COLOR, font=BODY_FONT,
        ).pack(pady=(14, 2))
        name_row = tk.Frame(self._content, bg=BG_COLOR)
        name_row.pack()
        self._name_entry = tk.Entry(name_row, font=BODY_FONT, width=26,
                                    justify="center")
        self._name_entry.pack(side="left")
        create_secondary_button(name_row, "Reroll", lambda: reroll(),
                                width=8).pack(side="left", padx=(8, 0))

        def reroll():
            pool = (self._girls if self._voice_control.selected == "Girl"
                    else self._boys)
            self._name_entry.delete(0, "end")
            self._name_entry.insert(0, random.choice(pool))

        if fin.display_name:
            self._name_entry.insert(0, fin.display_name)
        else:
            reroll()

        def create_character():
            name = self._name_entry.get().strip()
            if not name:
                messagebox.showwarning("Missing Name",
                                       "Please enter a character name.",
                                       parent=self.wizard.root)
                return
            fin.display_name = name
            fin.voice = "girl" if self._voice_control.selected == "Girl" else "boy"
            workspace.save_groups(self.state)
            self._run_finalize()

        self._phase_nav(self._content, create_character,
                        continue_text="Create Character")

    # ------------------------------------------------------------------
    def _run_finalize(self) -> None:
        pile = self._current_pile()
        groups = self.state.groups
        ws = self.state.workspace
        game = self.state.game_name

        self.show_loading(f"Creating {pile.finalize.display_name}…")

        def progress(msg: str) -> None:
            self.schedule_callback(lambda: self.show_loading(msg))

        def work():
            try:
                result = finalize_pile(ws, groups, pile, game,
                                       progress_cb=progress)
                self.schedule_callback(lambda: self._on_finalized(result))
            except Exception as e:
                self.schedule_callback(lambda msg=str(e): self._on_error(msg))

        guard_begin()
        threading.Thread(target=work, daemon=True).start()

    def _on_finalized(self, result) -> None:
        guard_end()
        self.hide_loading()
        pile = self._current_pile()
        fin = pile.finalize
        fin.done = True
        fin.output_folder = str(result.folder.relative_to(self.state.workspace))
        fin.verification = result.verification
        self.state.finalized_folders.append(result.folder)
        workspace.save_groups(self.state)
        # NOTE: no modal here, verification results are shown on the summary
        # screen. A blocking dialog between characters can be lost off-screen
        # (monitor changes, session re-login) and freezes the whole flow.

        # advance to next unfinished pile
        self._pile_index += 1
        while (self._pile_index < len(self._pile_ids)
               and self.state.groups.piles[
                   self._pile_ids[self._pile_index]].finalize.done):
            self._pile_index += 1
        if self._pile_index >= len(self._pile_ids):
            self._finish_step()
        else:
            self._begin_pile()

    def _on_error(self, message: str) -> None:
        guard_end()
        self.hide_loading()
        messagebox.showerror("Finalize Failed",
                             f"Could not create the character:\n{message}",
                             parent=self.wizard.root)

    def _show_all_done(self) -> None:
        self._clear_content()
        done = sum(1 for pid in self._pile_ids
                   if self.state.groups.piles[pid].finalize.done)
        self._header_var.set("All characters created")
        tk.Label(
            self._content,
            text=f"{done} character(s) finalized. Select any you want to redo "
                 f"and press \"Redo Selected\" (crops, colors, and names are "
                 f"kept), or press Next for the summary.",
            bg=BG_COLOR, fg=TEXT_SECONDARY, font=BODY_FONT, wraplength=900,
        ).pack(pady=(10, 6))

        self._redo_btn = create_secondary_button(
            self._content, "Redo Selected", self._redo_selected, width=16)
        self._redo_btn.pack(pady=(0, 8))
        self._redo_btn.configure(state="disabled")

        from ....ui.widgets import ThumbnailGrid, ThumbItem
        grid = ThumbnailGrid(
            self._content, thumb_size=self._thumb_px, centered=False,
            click_toggles=True,
            on_selection_change=lambda ids: self._redo_btn.configure(
                state="normal" if ids else "disabled"),
        )
        grid.pack(fill="both", expand=True)
        self._redo_grid = grid

        items = []
        for pid in self._pile_ids:
            pile = self.state.groups.piles[pid]
            if not pile.finalize.done:
                continue
            rep = None
            for sid in pile.stack_ids:
                st = self.state.groups.stacks.get(sid)
                if st and st.rep_image:
                    rep = self._stack_rep_image(sid)
                    break
            if rep is None:
                continue
            items.append(ThumbItem(id=pid, image=rep,
                                   caption=pile.finalize.display_name))
        grid.set_items(items)

        self.wizard._next_btn.configure(state="normal")

    def _redo_selected(self) -> None:
        pids = self._redo_grid.selected_ids
        if not pids:
            return
        names = [self.state.groups.piles[p].finalize.display_name for p in pids]
        if not messagebox.askyesno(
                "Redo Characters",
                f"Redo {len(pids)} character(s), {', '.join(names)}? Their "
                f"folders in output/ are replaced; crop lines, eye line, "
                f"colors, scale, and names are kept as starting values.",
                parent=self.wizard.root):
            return
        import shutil
        for pid in pids:
            fin = self.state.groups.piles[pid].finalize
            if fin.output_folder:
                old = self.state.workspace / fin.output_folder
                if old.is_dir():
                    shutil.rmtree(old)
            fin.done = False
            fin.output_folder = None
            fin.verification = None
        workspace.save_groups(self.state)

        # Jump to the first selected pile; finished ones stay skipped so the
        # step walks through exactly the redo set.
        first = min(self._pile_ids.index(p) for p in pids)
        self._pile_index = first
        self.wizard._next_btn.configure(state="disabled")
        self._begin_pile()

    def validate(self) -> bool:
        groups = self.state.groups
        remaining = [p.name for p in groups.piles.values()
                     if not p.finalize.done]
        if remaining:
            return messagebox.askyesno(
                "Unfinished Characters",
                f"{len(remaining)} character(s) have not been created yet: "
                f"{', '.join(remaining)}. Continue to the summary anyway?",
                parent=self.wizard.root,
            )
        return True
