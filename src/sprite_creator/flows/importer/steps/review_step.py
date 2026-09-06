"""
Step 4, Review & Group: thumbnail grid of pose stacks; the user assembles
stacks into character piles, fixes any grouping mistakes, and discards
junk. Every mutation is saved to groups.json immediately.
"""

import gc
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Dict, List, Optional

from PIL import Image

from ....config import (
    BG_COLOR,
    TEXT_COLOR,
    TEXT_SECONDARY,
    PAGE_TITLE_FONT,
    SECTION_FONT,
    BODY_FONT,
    SMALL_FONT,
    SMALL_FONT_BOLD,
    BORDER_COLOR,
)
from ....ui.screens.base import WizardStep
from ....ui.tk_common import (
    create_primary_button,
    create_secondary_button,
    create_danger_button,
    create_segmented_control,
)
from ....ui.widgets import ThumbnailGrid, ThumbItem
from .. import workspace
from ..state import OutfitGroup, PoseStack

# Distinct accent colors for character piles (cycled)
PILE_COLORS = [
    "#4A90D9", "#D94A8C", "#4AD98C", "#D9A54A", "#9C4AD9",
    "#4AD1D9", "#D94A4A", "#8CD94A", "#D94AD1", "#7A8CD9",
]

THUMB_SIZE = 150      # HARDCODED card width (scaled by the window scale factor).
                      # This is the CANONICAL importer card size; every card
                      # screen (Sort, inspector, chooser, naming, redo) derives
                      # from it so all cards are the same size everywhere. Also
                      # mirrored in character_step.py (keep the two in sync).
                      # It is safe at this larger size because ThumbnailGrid is
                      # virtualized: only the cards near the viewport are realized,
                      # so pixmap memory is bounded by the viewport, not by the
                      # image/stack count (no more X BadAlloc on big datasets).


class ReviewStep(WizardStep):
    STEP_ID = "imp_review"
    STEP_TITLE = "Sort Characters"
    STEP_NUMBER = 4
    STEP_HELP = """Tell the app which poses belong to the same character.

Each card is one POSE: a single pose+outfit render with its own set of
expression variants. Every distinct pose+outfit combination is its own
card (poses are never nested by outfit). The sorter found them
automatically, but it can't know that two different poses show the same
character, only you can see that. So:

1. Click every card that shows the same character
   (Ctrl-click to add more, Shift-click for a range).
2. Press "Group as New Character".
3. Repeat for each character. Each character gets a color, and its
   cards show a colored border with the character's name.

Each card shows its image count. That count is a useful clue: if one
pose has roughly double the images of the others, the sorter probably
merged two sets into one. This happens when a gallery shoots the same
pose twice with a small difference near the head (a hand raised, a head
accessory toggled): the two sets differ only up near the head, so they
read as one set. Open that card and check whether half the images are a
different pose or have an accessory the others do not.

Fixing sorter mistakes:
- "Merge into One Pose" combines cards that are really the same pose
  into one flat set of expressions.
- Double-click a card to open it: see its expression variants. Click
  images to select them, then "Split Selected to New Pose" to break a
  wrongly merged set apart, or move stray images to Unsorted. A split
  pose stays with the same character and keeps its crop lines.
- The UNSORTED view (top-left switch) holds images that matched
  nothing, usually CGs and title screens. Discard the junk. If some
  actually belong to a character, either "Add to Existing Pose" or
  "Make New Pose from Selection", then group that pose into the
  character.

Nothing is moved or modified on disk, you can close the app anytime
and this screen comes back exactly as you left it."""
    STEP_TIP = ""

    def __init__(self, wizard, state):
        super().__init__(wizard, state)
        self._grid: ThumbnailGrid = None
        self._view = "stacks"           # stacks | unsorted | discarded
        self._thumb_cache: Dict[str, Image.Image] = {}
        self._pile_combo: ttk.Combobox = None
        self._banner_var: tk.StringVar = None
        self._status_var: tk.StringVar = None
        self._focus_active = False      # redo-from-Summary single-char mode
        self._focus_buttons: tk.Frame = None
        # Thumbnail size scaled by the window's UI scale (fonts scale via tk
        # scaling; images must be scaled explicitly to stay proportional).
        self._thumb_px = max(1, round(THUMB_SIZE * getattr(
            self.wizard, "ui_scale", 1.0)))
        # Cache the source thumbnails WIDTH-driven and a bit larger than the
        # target card width, so the grid can render a card up to its computed
        # (fill-the-row) width without upscaling a too-small image.
        self._src_px = max(1, round(self._thumb_px * 1.6))

    # ------------------------------------------------------------------
    def build_ui(self, parent: tk.Frame) -> None:
        parent.configure(bg=BG_COLOR)
        tk.Label(
            parent, text="Sort Characters", bg=BG_COLOR,
            fg=TEXT_COLOR, font=PAGE_TITLE_FONT,
        ).pack(pady=(0, 4))

        # Dynamic instruction banner: always tells the user the next action
        self._banner_var = tk.StringVar(value="")
        tk.Label(
            parent, textvariable=self._banner_var, bg=BG_COLOR,
            fg=TEXT_COLOR, font=BODY_FONT, wraplength=1100, justify="center",
        ).pack(pady=(0, 8))

        # View switcher
        top_row = tk.Frame(parent, bg=BG_COLOR)
        top_row.pack(fill="x", pady=(0, 6))
        self._view_control = create_segmented_control(
            top_row, ["Poses", "Unsorted", "Discarded"],
            default="Poses", on_change=lambda v: self._switch_view(v),
        )
        self._view_control.pack(side="left")

        # Toolbar, wrapped so it can scroll sideways when the window is too
        # narrow to show every button (otherwise the rightmost buttons clip
        # off-screen with no way to reach them).
        tb_holder = tk.Frame(parent, bg=BG_COLOR)
        tb_holder.pack(fill="x", pady=(0, 8))
        self._toolbar_canvas = tk.Canvas(tb_holder, bg=BG_COLOR,
                                         highlightthickness=0, height=34)
        self._toolbar_hbar = tk.Scrollbar(
            tb_holder, orient="horizontal",
            command=self._toolbar_canvas.xview, width=10)
        self._toolbar_canvas.configure(xscrollcommand=self._toolbar_hbar.set)
        self._toolbar_canvas.pack(side="top", fill="x")
        self._toolbar = tk.Frame(self._toolbar_canvas, bg=BG_COLOR)
        self._toolbar_canvas.create_window((0, 0), window=self._toolbar,
                                           anchor="nw")
        self._toolbar.bind("<Configure>", self._on_toolbar_configure)
        self._toolbar_canvas.bind("<Configure>", self._on_toolbar_configure)
        self._build_toolbar()

        # Grid. Plain click toggles a card in/out of the selection (so you can
        # select several, or deselect, with just the mouse); Ctrl-click and
        # Shift-click still work for power users.
        self._grid = ThumbnailGrid(
            parent, thumb_size=self._thumb_px, click_toggles=True,
            on_selection_change=lambda ids: self._update_toolbar_state(),
            on_double_click=self._open_inspector,
        )
        self._grid.pack(fill="both", expand=True)

        # Status line at the bottom
        self._status_var = tk.StringVar(value="")
        tk.Label(
            parent, textvariable=self._status_var, bg=BG_COLOR,
            fg=TEXT_SECONDARY, font=SMALL_FONT,
        ).pack(fill="x", pady=(6, 0))

    def _toolbar_separator(self, parent) -> None:
        tk.Frame(parent, bg=BORDER_COLOR, width=2).pack(
            side="left", fill="y", padx=10, pady=2)

    def _on_toolbar_configure(self, event=None) -> None:
        """Match the canvas to the toolbar height and show a horizontal
        scrollbar only when the buttons are wider than the window. The
        overflow check is deferred so it reads final (not mid-resize) widths."""
        canvas = self._toolbar_canvas
        canvas.configure(scrollregion=canvas.bbox("all"))
        req_h = self._toolbar.winfo_reqheight()
        if req_h > 1:
            canvas.configure(height=req_h)
        if not getattr(self, "_tb_sync_pending", False):
            self._tb_sync_pending = True
            canvas.after_idle(self._sync_toolbar_hbar)

    def _sync_toolbar_hbar(self) -> None:
        self._tb_sync_pending = False
        canvas = self._toolbar_canvas
        try:
            req_w = self._toolbar.winfo_reqwidth()
            avail = canvas.winfo_width()
        except tk.TclError:
            return
        if req_w > avail + 2:
            if not self._toolbar_hbar.winfo_ismapped():
                self._toolbar_hbar.pack(side="bottom", fill="x")
        else:
            if self._toolbar_hbar.winfo_ismapped():
                self._toolbar_hbar.pack_forget()
            canvas.xview_moveto(0)

    def _build_toolbar(self) -> None:
        bar = self._toolbar

        # Poses-view buttons, grouped: character actions | pose fixes | cleanup
        self._stack_buttons = tk.Frame(bar, bg=BG_COLOR)

        self._btn_new_pile = create_primary_button(
            self._stack_buttons, "Group as New Character",
            self._new_pile, width=20)
        self._btn_new_pile.pack(side="left", padx=(0, 6))

        self._pile_combo = ttk.Combobox(self._stack_buttons, state="readonly",
                                        width=20)
        self._pile_combo.set("Add to Character…")
        self._pile_combo.bind("<<ComboboxSelected>>", self._on_pile_combo)
        self._pile_combo.pack(side="left", padx=(0, 6))

        self._btn_remove_pile = create_secondary_button(
            self._stack_buttons, "Remove from Character",
            self._remove_from_pile, width=18)
        self._btn_remove_pile.pack(side="left", padx=(0, 6))

        self._btn_rename = create_secondary_button(
            self._stack_buttons, "Rename Character", self._rename_pile,
            width=15)
        self._btn_rename.pack(side="left")

        self._toolbar_separator(self._stack_buttons)

        self._btn_merge = create_secondary_button(
            self._stack_buttons, "Merge into One Pose", self._merge_stacks,
            width=17)
        self._btn_merge.pack(side="left")

        self._toolbar_separator(self._stack_buttons)

        self._btn_discard = create_danger_button(
            self._stack_buttons, "Discard", self._discard_selection, width=9)
        self._btn_discard.pack(side="left", padx=(0, 6))

        self._btn_restack = create_danger_button(
            self._stack_buttons, "Re-run Auto-Sort", self._rerun_autostack,
            width=15)
        self._btn_restack.pack(side="left")

        # Unsorted-view buttons
        self._unsorted_buttons = tk.Frame(bar, bg=BG_COLOR)
        self._btn_add_to_pose = create_primary_button(
            self._unsorted_buttons, "Add to Existing Pose…",
            self._add_unsorted_to_pose, width=19)
        self._btn_add_to_pose.pack(side="left", padx=(0, 6))
        self._btn_make_stack = create_secondary_button(
            self._unsorted_buttons, "Make New Pose from Selection",
            self._make_stack_from_unsorted, width=24)
        self._btn_make_stack.pack(side="left", padx=(0, 6))
        self._btn_discard_unsorted = create_danger_button(
            self._unsorted_buttons, "Discard", self._discard_selection, width=9)
        self._btn_discard_unsorted.pack(side="left", padx=(0, 6))

        # Discarded-view buttons
        self._discarded_buttons = tk.Frame(bar, bg=BG_COLOR)
        self._btn_restore = create_primary_button(
            self._discarded_buttons, "Restore to Unsorted",
            self._restore_selection, width=17)
        self._btn_restore.pack(side="left", padx=(0, 6))

        self._stack_buttons.pack(side="left")

    # ------------------------------------------------------------------
    def on_enter(self) -> None:
        self._apply_focus_mode()
        self._refresh()

    def _apply_focus_mode(self) -> None:
        """When redoing one character from Summary, show only that character's
        poses and a minimal toolbar (split via double-click, merge). Otherwise
        restore the normal global view."""
        groups = self.state.groups
        focus = self.state.redo_focus_pile
        active = bool(focus and groups and focus in groups.piles)
        if not active and not self._focus_active:
            return                      # normal stays normal
        self._focus_active = active
        if active:
            self._view = "stacks"
            self._view_control.pack_forget()
            for frame in (self._stack_buttons, self._unsorted_buttons,
                          self._discarded_buttons):
                frame.pack_forget()
            self._ensure_focus_toolbar()
            self._focus_buttons.pack(side="left")
            try:
                self.wizard._next_btn.configure(state="normal")
                # Back is dead on the focused Sort step: a redo must not walk
                # back into the crawl/auto-stack steps. The Finalize step after
                # this re-enables Back so you can return here.
                self.wizard._back_btn.configure(state="disabled")
            except Exception:
                pass
        else:
            if self._focus_buttons is not None:
                self._focus_buttons.pack_forget()
            if not self._view_control.winfo_ismapped():
                self._view_control.pack(side="left")
            self._view = "stacks"
            self._stack_buttons.pack(side="left")

    def _ensure_focus_toolbar(self) -> None:
        if self._focus_buttons is not None:
            return
        self._focus_buttons = tk.Frame(self._toolbar, bg=BG_COLOR)
        create_secondary_button(
            self._focus_buttons, "Merge Selected Poses", self._merge_stacks,
            width=20).pack(side="left")
        tk.Label(
            self._focus_buttons,
            text="Double-click a pose to split its expressions into a new pose.",
            bg=BG_COLOR, fg=TEXT_SECONDARY, font=SMALL_FONT,
        ).pack(side="left", padx=(12, 0))

    def _switch_view(self, label: str) -> None:
        self._view = {"Poses": "stacks", "Unsorted": "unsorted",
                      "Discarded": "discarded"}[label]
        for frame in (self._stack_buttons, self._unsorted_buttons,
                      self._discarded_buttons):
            frame.pack_forget()
        {"stacks": self._stack_buttons,
         "unsorted": self._unsorted_buttons,
         "discarded": self._discarded_buttons}[self._view].pack(side="left")
        self._refresh()

    # ------------------------------------------------------------------
    # Thumbnails
    # ------------------------------------------------------------------
    def _thumb(self, image_name: str) -> Image.Image:
        cached = self._thumb_cache.get(image_name)
        if cached is not None:
            return cached

        # RGBA (not RGB): converting to RGB flattens transparent padding to
        # light pixels, which shows as a pale "halo" box behind the sprite.
        # Keeping alpha lets the transparent area show the card background.
        thumb_path = (workspace.thumbs_dir(self.state.workspace)
                      / f"w{self._src_px}rgba" / (image_name + ".png"))
        if thumb_path.is_file():
            img = Image.open(thumb_path).copy()
        else:
            img = Image.open(self.state.raw_dir / image_name).convert("RGBA")
            # Width-driven (huge height bound) so portrait sprites keep enough
            # width for the grid to render a full-width card.
            img.thumbnail((self._src_px, self._src_px * 3), Image.LANCZOS)
            thumb_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(thumb_path, "PNG")
        self._thumb_cache[image_name] = img
        return img

    # ------------------------------------------------------------------
    # Refresh / model -> view
    # ------------------------------------------------------------------
    def _pile_color(self, pile_id: str) -> str:
        index = sorted(self.state.groups.piles.keys()).index(pile_id)
        return PILE_COLORS[index % len(PILE_COLORS)]

    def _refresh(self) -> None:
        groups = self.state.groups
        if groups is None:
            return

        # In redo-focus mode, show only the focused character's poses.
        focus_sids = None
        if self._focus_active and self.state.redo_focus_pile in groups.piles:
            focus_sids = set(groups.piles[self.state.redo_focus_pile].stack_ids)

        items: List[ThumbItem] = []
        if self._view == "stacks":
            # Insertion order (not numeric): a pose split off from another is
            # inserted right after its source, so it appears next to it.
            for sid in groups.stacks:
                if focus_sids is not None and sid not in focus_sids:
                    continue
                stack = groups.stacks[sid]
                rep = stack.rep_image
                if rep is None:
                    continue
                pile = groups.pile_for_stack(sid)
                badge = f"{stack.expression_count()} images"
                items.append(ThumbItem(
                    id=sid,
                    image=self._thumb(rep),
                    badge=badge,
                    accent_color=self._pile_color(pile.id) if pile else "",
                    accent_label=pile.name if pile else "",
                ))
        elif self._view == "unsorted":
            for name in groups.unsorted:
                items.append(ThumbItem(id=name, image=self._thumb(name),
                                       caption=name))
        else:
            for name in groups.discarded:
                try:
                    thumb = self._thumb(name)
                except Exception:
                    continue        # unreadable files can't be shown
                items.append(ThumbItem(id=name, image=thumb, caption=name))

        self._grid.set_items(items)
        self._update_pile_menu()
        self._update_toolbar_state()
        self._update_status()

    def _update_status(self) -> None:
        groups = self.state.groups
        assigned = sum(len(p.stack_ids) for p in groups.piles.values())
        self._status_var.set(
            f"{len(groups.stacks)} poses found · {assigned} assigned to "
            f"{len(groups.piles)} character(s) · {len(groups.unsorted)} unsorted "
            f"· {len(groups.discarded)} discarded"
        )

    def _update_banner(self) -> None:
        groups = self.state.groups
        n_selected = len(self._grid.selected_ids) if self._grid else 0
        if self._focus_active and self.state.redo_focus_pile in groups.piles:
            name = groups.piles[self.state.redo_focus_pile].finalize.display_name
            self._banner_var.set(
                f"Fixing \"{name}\": double-click a pose to split its "
                f"expressions into a new pose, or select poses and merge them. "
                f"Press Next to rebuild this character.")
            return
        if self._view == "unsorted":
            text = ("These images matched nothing automatically (usually CGs "
                    "and title screens). Discard the junk, or if some belong "
                    "to a character, add them to one of its poses or make a "
                    "new pose from them.")
        elif self._view == "discarded":
            text = "Discarded images. Select any and restore them to Unsorted."
        elif not groups.piles:
            text = ("Step 1 of 2:  Click every card that shows the SAME "
                    "character (Ctrl-click to add more), then press "
                    "\"Group as New Character\". Repeat for each character.")
        else:
            unassigned = sum(1 for sid in groups.stacks
                             if groups.pile_for_stack(sid) is None)
            if unassigned:
                text = (f"{unassigned} pose(s) still need a character. Select "
                        f"them and group them, or add them to an existing "
                        f"character.")
            elif n_selected:
                text = (f"{n_selected} selected. Group, move, merge, or "
                        f"discard using the buttons below.")
            else:
                text = ("All poses are assigned!  Double-click any card to "
                        "peek inside it, or press Next to start creating the "
                        "characters.")
        self._banner_var.set(text)

    def _update_pile_menu(self) -> None:
        names = [self.state.groups.piles[pid].name
                 for pid in sorted(self.state.groups.piles.keys())]
        self._pile_combo["values"] = names
        self._pile_combo.set("Add to Character…")

    def _on_pile_combo(self, _event=None) -> None:
        name = self._pile_combo.get()
        for pid in sorted(self.state.groups.piles.keys()):
            if self.state.groups.piles[pid].name == name:
                self._add_to_pile(pid)
                break
        self._pile_combo.set("Add to Character…")

    def _update_toolbar_state(self) -> None:
        n = len(self._grid.selected_ids) if self._grid else 0
        state_multi = "normal" if n >= 1 else "disabled"
        if self._view == "stacks":
            self._btn_new_pile.configure(state=state_multi)
            self._btn_remove_pile.configure(state=state_multi)
            self._btn_discard.configure(state=state_multi)
            self._btn_merge.configure(state="normal" if n >= 2 else "disabled")
            self._btn_rename.configure(
                state="normal" if n == 1 else "disabled")
        elif self._view == "unsorted":
            self._btn_make_stack.configure(state=state_multi)
            self._btn_add_to_pose.configure(state=state_multi)
            self._btn_discard_unsorted.configure(state=state_multi)
        else:
            self._btn_restore.configure(state=state_multi)
        self._update_banner()

    def _save(self) -> None:
        workspace.save_groups(self.state)

    # ------------------------------------------------------------------
    # Pile actions
    # ------------------------------------------------------------------
    def _new_pile(self) -> None:
        ids = self._grid.selected_ids
        if not ids:
            return
        groups = self.state.groups
        pid = groups.next_pile_id()
        from ..state import CharacterPile
        pile = CharacterPile(id=pid, name=f"Character {len(groups.piles) + 1}",
                             stack_ids=[])
        groups.piles[pid] = pile
        self._assign_stacks(pile, ids)
        self._save()
        self._grid.clear_selection()    # done with this character's cards
        self._refresh()

    def _add_to_pile(self, pile_id: str) -> None:
        ids = self._grid.selected_ids
        if not ids:
            return
        self._assign_stacks(self.state.groups.piles[pile_id], ids)
        self._save()
        self._grid.clear_selection()    # done with this character's cards
        self._refresh()

    def _assign_stacks(self, pile, stack_ids: List[str]) -> None:
        for sid in stack_ids:
            for other in self.state.groups.piles.values():
                if sid in other.stack_ids:
                    other.stack_ids.remove(sid)
            pile.stack_ids.append(sid)
        self._prune_empty_piles()

    def _remove_from_pile(self) -> None:
        for sid in self._grid.selected_ids:
            for pile in self.state.groups.piles.values():
                if sid in pile.stack_ids:
                    pile.stack_ids.remove(sid)
        self._prune_empty_piles()
        self._save()
        self._refresh()

    def _prune_empty_piles(self) -> None:
        groups = self.state.groups
        for pid in [p for p, pile in groups.piles.items() if not pile.stack_ids]:
            del groups.piles[pid]

    def _rename_pile(self) -> None:
        ids = self._grid.selected_ids
        if len(ids) != 1:
            return
        pile = self.state.groups.pile_for_stack(ids[0])
        if pile is None:
            messagebox.showinfo(
                "No Character",
                "That pose isn't part of a character yet, group it first.",
                parent=self.wizard.root)
            return
        name = simpledialog.askstring("Rename Character", "Character name:",
                                      initialvalue=pile.name,
                                      parent=self.wizard.root)
        if name and name.strip():
            pile.name = name.strip()
            self._save()
            self._refresh()

    # ------------------------------------------------------------------
    # Stack actions
    # ------------------------------------------------------------------
    def _merge_stacks(self) -> None:
        ids = self._grid.selected_ids
        if len(ids) < 2:
            return
        groups = self.state.groups
        widths = {groups.stacks[sid].size[0] for sid in ids}
        if len(widths) > 1:
            messagebox.showwarning(
                "Different Image Widths",
                "These poses have different image WIDTHS, so face slices "
                "can't line up horizontally and they can't merge.\n\n"
                "If they're the same character, that's fine, just group "
                "them into the same character instead.",
                parent=self.wizard.root)
            return
        ids = sorted(ids, key=lambda s: int(s.split("_")[1]))
        target = groups.stacks[ids[0]]

        # Flat model: merging combines EVERY image into ONE group (one pose),
        # not separate outfits under a pose.
        all_images = [n for sid in ids
                      for o in groups.stacks[sid].outfits for n in o.images]

        for sid in ids[1:]:
            other = groups.stacks[sid]
            if other.size[1] > target.size[1]:
                target.size = (target.size[0], other.size[1])
            target.duplicates.update(other.duplicates)
            if other.face_box and target.face_box:
                a, b = target.face_box, other.face_box
                target.face_box = (min(a[0], b[0]), min(a[1], b[1]),
                                   max(a[2], b[2]), max(a[3], b[3]))
            elif other.face_box:
                target.face_box = other.face_box
            if other.chin_y and (not target.chin_y
                                 or other.chin_y > target.chin_y):
                target.chin_y = other.chin_y
            # remove from piles and model
            for pile in groups.piles.values():
                if sid in pile.stack_ids:
                    pile.stack_ids.remove(sid)
            del groups.stacks[sid]

        target.outfits = [OutfitGroup(id="outfit_0", images=all_images,
                                      label="1")]

        self._prune_empty_piles()
        self._save()
        self._refresh()

    def _discard_selection(self) -> None:
        ids = self._grid.selected_ids
        if not ids:
            return
        groups = self.state.groups
        if self._view == "stacks":
            total = sum(len(groups.stacks[sid].all_images())
                        + len(groups.stacks[sid].duplicates)
                        for sid in ids)
            if not messagebox.askyesno(
                    "Discard Poses",
                    f"Discard {len(ids)} pose(s) ({total} images)? They move "
                    f"to the Discarded view and can be restored to Unsorted.",
                    parent=self.wizard.root):
                return
            for sid in ids:
                stack = groups.stacks[sid]
                groups.discarded.extend(stack.all_images())
                groups.discarded.extend(stack.duplicates.keys())
                for pile in groups.piles.values():
                    if sid in pile.stack_ids:
                        pile.stack_ids.remove(sid)
                del groups.stacks[sid]
            self._prune_empty_piles()
        else:   # unsorted view
            for name in ids:
                if name in groups.unsorted:
                    groups.unsorted.remove(name)
                    groups.discarded.append(name)
        self._save()
        self._refresh()

    def _restore_selection(self) -> None:
        groups = self.state.groups
        for name in self._grid.selected_ids:
            if name in groups.discarded:
                groups.discarded.remove(name)
                groups.unsorted.append(name)
        self._save()
        self._refresh()

    def _make_stack_from_unsorted(self) -> None:
        ids = self._grid.selected_ids
        if not ids:
            return
        groups = self.state.groups
        names = sorted(ids)
        size = None
        for name in names:
            rec = groups.images.get(name)
            if rec:
                if size is None:
                    size = rec.size
                elif rec.size[0] != size[0]:
                    messagebox.showwarning(
                        "Width Mismatch",
                        "Selected images have different WIDTHS, variants of "
                        "one pose must match horizontally. (Different "
                        "heights are fine.)",
                        parent=self.wizard.root)
                    return
                elif rec.size[1] > size[1]:
                    size = (size[0], rec.size[1])
        sid = groups.next_stack_id()
        stack = PoseStack(
            id=sid, size=size or (0, 0),
            outfits=[OutfitGroup(id="outfit_0", images=names, label="1")],
        )
        groups.stacks[sid] = stack
        for name in names:
            groups.unsorted.remove(name)
        self._save()
        self._refresh()

    def _add_unsorted_to_pose(self) -> None:
        """Attach selected unsorted images to an existing pose as more
        expression variants, via a thumbnail chooser."""
        ids = self._grid.selected_ids
        if not ids:
            return
        groups = self.state.groups
        if not groups.stacks:
            messagebox.showinfo("No Poses", "There are no poses yet.",
                                parent=self.wizard.root)
            return

        names = sorted(ids)
        sel_size = None
        for name in names:
            rec = groups.images.get(name)
            if rec:
                if sel_size is None:
                    sel_size = rec.size
                elif rec.size[0] != sel_size[0]:
                    messagebox.showwarning(
                        "Width Mismatch",
                        "Selected images have different WIDTHS, they can't "
                        "all join the same pose.",
                        parent=self.wizard.root)
                    return

        win = tk.Toplevel(self.wizard.root)
        win.title("Choose the pose to add them to")
        win.configure(bg=BG_COLOR)
        win.transient(self.wizard.root)
        s = getattr(self.wizard, "ui_scale", 1.0)
        win.geometry(f"{int(860 * s)}x{int(600 * s)}")

        matching = [sid for sid in sorted(groups.stacks,
                                          key=lambda s: int(s.split("_")[1]))
                    if sel_size is None
                    or groups.stacks[sid].size[0] == sel_size[0]]
        other_count = len(groups.stacks) - len(matching)

        note = (f"Poses matching the selected images' width "
                f"({sel_size[0]}px). The images are added to the chosen pose "
                f"as more expression variants.")
        if other_count:
            note += f" ({other_count} pose(s) hidden, different width.)"
        tk.Label(win, text=note, bg=BG_COLOR, fg=TEXT_SECONDARY,
                 font=SMALL_FONT, wraplength=800).pack(pady=(10, 4))

        if not matching:
            tk.Label(win, text="No pose shares this canvas size. Use "
                               "\"Make New Pose from Selection\" instead.",
                     bg=BG_COLOR, fg=TEXT_COLOR, font=BODY_FONT).pack(pady=20)
            create_secondary_button(win, "Close", win.destroy, width=10).pack()
            return

        chooser = ThumbnailGrid(win, thumb_size=self._thumb_px)
        chooser.pack(fill="both", expand=True, padx=10)
        items = []
        for sid in matching:
            stack = groups.stacks[sid]
            pile = groups.pile_for_stack(sid)
            items.append(ThumbItem(
                id=sid, image=self._thumb(stack.rep_image),
                badge=f"{stack.expression_count()} images",
                accent_color=self._pile_color(pile.id) if pile else "",
                accent_label=pile.name if pile else "",
            ))
        chooser.set_items(items)

        def do_add():
            sel = chooser.selected_ids
            if len(sel) != 1:
                messagebox.showwarning("Pick One", "Select exactly one pose.",
                                       parent=win)
                return
            stack = groups.stacks[sel[0]]
            # Flat: add the images to the pose's single group as more variants.
            if stack.outfits:
                stack.outfits[0].images.extend(names)
            else:
                stack.outfits = [OutfitGroup(id="outfit_0", images=list(names),
                                             label="1")]
            if sel_size and sel_size[1] > stack.size[1]:
                stack.size = (stack.size[0], sel_size[1])
            for name in names:
                groups.unsorted.remove(name)
            self._save()
            win.destroy()
            self._refresh()

        btn_row = tk.Frame(win, bg=BG_COLOR)
        btn_row.pack(fill="x", pady=10, padx=10)
        create_secondary_button(btn_row, "Cancel", win.destroy, width=10
                                ).pack(side="left")
        create_primary_button(btn_row, "Add to This Pose", do_add, width=16
                              ).pack(side="right")

        def _grab_when_visible():
            try:
                win.grab_set()
            except tk.TclError:
                pass
        win.after(100, _grab_when_visible)

    def _rerun_autostack(self) -> None:
        """Throw away all grouping (piles included) and re-run the sorter."""
        if not messagebox.askyesno(
                "Re-run Auto-Stack",
                "This clears ALL stacks, piles, and manual sorting for this "
                "import and re-runs the automatic sorter from scratch.\n\n"
                "Finalized characters in output/ are kept. Continue?",
                parent=self.wizard.root):
            return
        self.state.groups = None
        groups_path = self.state.workspace / "groups.json"
        if groups_path.exists():
            groups_path.unlink()
        workspace.save_import_meta(self.state)
        # AutoStackStep.should_skip() is now False; navigate back into it
        self.wizard.go_to_step(2)

    # ------------------------------------------------------------------
    # Stack inspector (double-click)
    # ------------------------------------------------------------------
    def _open_inspector(self, stack_id: str) -> None:
        if self._view != "stacks":
            return
        groups = self.state.groups
        stack = groups.stacks.get(stack_id)
        if stack is None:
            return

        # Free the main grid's image pixmaps while the (modal) inspector is
        # open, so both grids' images are never allocated at once. Rebuilt on
        # close. Prevents X pixmap exhaustion (BadAlloc) on large stacks / 4K.
        self._grid.set_items([])
        gc.collect()

        win = tk.Toplevel(self.wizard.root)
        win.title(f"Pose, {stack.size[0]}x{stack.size[1]}px")
        win.configure(bg=BG_COLOR)
        win.transient(self.wizard.root)
        s = getattr(self.wizard, "ui_scale", 1.0)
        # Width sized so ~6 same-size cards fit (same card size as the main grid,
        # just fewer per row in this narrower window).
        win.geometry(f"{int(1200 * s)}x{int(760 * s)}")
        win.resizable(False, False)     # fixed size like the main window
        # grab_set is deferred until the window is mapped: grabbing an
        # unmapped window raises TclError on Wayland and would abort the
        # rest of the construction, leaving a blank popup.

        tk.Label(
            win, text="These are the expression variants of this pose (first "
                      "image = the representative). Click images to select them, "
                      "then split them into their own pose or move them to "
                      "Unsorted.",
            bg=BG_COLOR, fg=TEXT_SECONDARY, font=SMALL_FONT, wraplength=1040,
            justify="center",
        ).pack(pady=(10, 4))

        # Header: a single count line (this pose is one flat set of expression
        # variants; there is no outfit nesting).
        header = tk.Frame(win, bg=BG_COLOR)
        header.pack(fill="x", padx=10)

        # Buttons pinned at the BOTTOM first, so they stay on screen no matter
        # how short the window is; the grid then fills the space above them.
        btn_row = tk.Frame(win, bg=BG_COLOR)
        btn_row.pack(fill="x", side="bottom", pady=10, padx=10)

        # Same selectable card grid as the other screens: fills width, wraps,
        # reflows on resize, and scrolls with the mouse wheel.
        grid = ThumbnailGrid(win, thumb_size=self._thumb_px, click_toggles=True)
        grid.pack(fill="both", expand=True, padx=10, pady=(4, 0))

        # The main grid was emptied on open; rebuild it once here, after freeing
        # the inspector's own pixmaps. Split/move only rebuild the inspector
        # (build_rows) while it is open, never the main grid, so pixmaps never
        # pile up.
        def close_inspector():
            grid.set_items([])      # free the inspector's pixmaps first
            win.destroy()
            self._refresh()         # restore the main grid
            gc.collect()

        def build_rows():
            for child in header.winfo_children():
                child.destroy()
            imgs = [n for group in stack.outfits for n in group.images]
            n_dupes = len(stack.duplicates)
            txt = f"{len(imgs)} expression variants"
            if n_dupes:
                txt += (f"  ,  {n_dupes} byte-identical duplicate"
                        f"{'s' if n_dupes != 1 else ''} folded")
            tk.Label(header, text=txt, bg=BG_COLOR, fg=TEXT_COLOR,
                     font=SMALL_FONT_BOLD, anchor="w").pack(fill="x")
            grid.set_items([ThumbItem(id=name, image=self._thumb(name))
                            for name in imgs])

        def split_selected_to_new_pose() -> None:
            """Move the selected images into their own new pose in the same
            character (for when the sorter grouped two different poses
            together). Crop lines are inherited since it is the same render."""
            if not grid.selected_ids:
                messagebox.showinfo(
                    "Nothing Selected",
                    "Click the images you want to split off first, then split.",
                    parent=win)
                return
            sel = set(grid.selected_ids)
            new_images = [n for group in stack.outfits
                          for n in group.images if n in sel]
            total = sum(len(g.images) for g in stack.outfits)
            if len(new_images) >= total:
                messagebox.showinfo(
                    "Split",
                    "Leave at least one image in the original pose.",
                    parent=win)
                return

            new_id = groups.next_stack_id()
            new_stack = PoseStack(
                id=new_id, size=stack.size,
                outfits=[OutfitGroup(id="outfit_0", images=list(new_images),
                                     label=stack.outfits[0].label)],
                face_box=stack.face_box, chin_y=stack.chin_y,
                accessory_split_from=stack.id,
            )
            moved = set(new_images)
            new_stack.duplicates = {d: k for d, k in stack.duplicates.items()
                                    if k in moved}
            for d in new_stack.duplicates:
                stack.duplicates.pop(d, None)
            for group in list(stack.outfits):
                group.images = [n for n in group.images if n not in moved]
                if not group.images:
                    stack.outfits.remove(group)
            self._insert_stack_after(stack_id, new_id, new_stack)
            self._join_source_pile(stack_id, new_id)
            self._save()
            if not stack.outfits:
                self._dissolve_stack(stack_id)
                close_inspector()
                return
            build_rows()

        def move_selected_to_unsorted() -> None:
            if not grid.selected_ids:
                return
            for name in list(grid.selected_ids):
                for group in list(stack.outfits):
                    if name in group.images:
                        group.images.remove(name)
                        groups.unsorted.append(name)
                        # duplicates of a removed keeper follow it to unsorted
                        for d, keeper in list(stack.duplicates.items()):
                            if keeper == name:
                                del stack.duplicates[d]
                                groups.unsorted.append(d)
                        if not group.images:
                            stack.outfits.remove(group)
            self._save()
            if not stack.outfits:
                self._dissolve_stack(stack_id)
                close_inspector()
                return
            build_rows()

        create_secondary_button(btn_row, "Split Selected to New Pose",
                                split_selected_to_new_pose, width=24
                                ).pack(side="left")
        create_secondary_button(btn_row, "Move Selected to Unsorted",
                                move_selected_to_unsorted, width=24
                                ).pack(side="left", padx=(8, 0))
        create_primary_button(btn_row, "Close", close_inspector, width=10
                              ).pack(side="right")
        win.protocol("WM_DELETE_WINDOW", close_inspector)

        build_rows()

        def _grab_when_visible():
            try:
                win.grab_set()
            except tk.TclError:
                pass    # window closed or grab unavailable, non-fatal
        win.after(100, _grab_when_visible)

    def _dissolve_stack(self, stack_id: str) -> None:
        groups = self.state.groups
        for pile in groups.piles.values():
            if stack_id in pile.stack_ids:
                pile.stack_ids.remove(stack_id)
        groups.stacks.pop(stack_id, None)
        self._prune_empty_piles()
        self._save()

    def _join_source_pile(self, source_id: str, new_id: str) -> None:
        """Put a newly split/peeled pose into the same character as the pose
        it came from, so it stays grouped instead of falling to Unsorted."""
        for pile in self.state.groups.piles.values():
            if source_id in pile.stack_ids and new_id not in pile.stack_ids:
                # Place it right after the source in the character too.
                i = pile.stack_ids.index(source_id)
                pile.stack_ids.insert(i + 1, new_id)
                return

    def _insert_stack_after(self, source_id: str, new_id: str,
                            new_stack) -> None:
        """Add new_stack to the model right AFTER source_id in display order,
        so a split pose appears next to the pose it came from (the grid renders
        stacks in dict order)."""
        stacks = self.state.groups.stacks
        rebuilt = {}
        for sid, st in stacks.items():
            rebuilt[sid] = st
            if sid == source_id:
                rebuilt[new_id] = new_stack
        if new_id not in rebuilt:        # source not found (shouldn't happen)
            rebuilt[new_id] = new_stack
        self.state.groups.stacks = rebuilt

    # ------------------------------------------------------------------
    def validate(self) -> bool:
        groups = self.state.groups
        # Redo-focus mode: grouping is already done; just proceed to rebuild
        # this one character.
        if self._focus_active:
            return True
        if not groups.piles:
            messagebox.showwarning(
                "No Characters Yet",
                "Select the cards that show the same character, then press "
                "\"Group as New Character\" first.",
                parent=self.wizard.root,
            )
            return False

        unassigned = [sid for sid in groups.stacks
                      if groups.pile_for_stack(sid) is None]
        if unassigned:
            return messagebox.askyesno(
                "Unassigned Poses",
                f"{len(unassigned)} pose(s) don't belong to any character and "
                f"will NOT be created. Continue anyway?",
                parent=self.wizard.root,
            )
        return True
