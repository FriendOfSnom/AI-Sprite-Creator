"""
Step 6, Summary: table of created characters with verification status,
plus an Open Output Folder action.
"""

import os
import platform
import shutil
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from ....config import (
    BG_COLOR,
    BG_SECONDARY,
    CARD_BG,
    TEXT_COLOR,
    TEXT_SECONDARY,
    SUCCESS_COLOR,
    WARNING_TEXT,
    PAGE_TITLE_FONT,
    SECTION_FONT,
    BODY_FONT,
    SMALL_FONT,
)
from ....ui.screens.base import WizardStep
from ....ui.tk_common import create_secondary_button
from .. import workspace


def open_in_file_manager(path: Path) -> None:
    system = platform.system()
    if system == "Windows":
        os.startfile(path)          # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.run(["open", str(path)])
    else:
        subprocess.run(["xdg-open", str(path)])


class ImportSummaryStep(WizardStep):
    STEP_ID = "imp_summary"
    STEP_TITLE = "Summary"
    STEP_NUMBER = 6
    STEP_HELP = """Your imported characters are ready.

Each row shows one created character with its pose/outfit/expression
counts and the verification result:

- A green check means every outfit + expression combination that existed
  in the original download was recomposited from the ST folder and
  matched the source pixel-for-pixel.
- A warning lists images whose recomposition differed, usually a chin
  line that cut through a blush or an accessory. The character still
  works; check those expressions in the Sprite Tester.

Your finished characters are in the import's output/ folder:
~/.sprite_creator/imports/<game name>/output/  (hidden folder, Ctrl+H
in your file manager). "Open Output Folder" opens it directly. Copy the
character folders into your game's characters/ directory when ready."""
    STEP_TIP = ""
    OVERVIEW = ("Your characters are ready. Try them in the Sprite Tester, "
                "then copy the folders into your game's characters directory.")

    def build_ui(self, parent: tk.Frame) -> None:
        parent.configure(bg=BG_COLOR)
        tk.Label(
            parent, text="Import Complete", bg=BG_COLOR, fg=TEXT_COLOR,
            font=PAGE_TITLE_FONT,
        ).pack(pady=(0, 4))
        tk.Label(
            parent, text=self.OVERVIEW, bg=BG_COLOR, fg=TEXT_SECONDARY,
            font=BODY_FONT, wraplength=900, justify="center",
        ).pack(pady=(0, 8))

        # Keep the action button up top so it is always visible: the per
        # character list below can be long enough to scroll past the viewport,
        # which would otherwise bury this button against the footer.
        btn_row = tk.Frame(parent, bg=BG_COLOR)
        btn_row.pack(pady=(0, 8))
        create_secondary_button(
            btn_row, "Open Output Folder", self._open_output, width=18,
        ).pack()

        self._rows_frame = tk.Frame(parent, bg=BG_COLOR)
        self._rows_frame.pack(fill="both", expand=True, pady=(6, 0))

    def on_enter(self) -> None:
        # Returning here always means a redo run finished; clear the flag.
        self.state.redo_focus_pile = None
        # Back is disabled on this screen: a per-character redo replaces data,
        # and stepping backward into a half-finished earlier screen would
        # scramble it. The only ways off are Finish or a per-character redo.
        try:
            self.wizard._back_btn.configure(state="disabled")
        except Exception:
            pass

        for child in self._rows_frame.winfo_children():
            child.destroy()

        groups = self.state.groups
        done_piles = [(pid, p) for pid, p in groups.piles.items()
                      if p.finalize.done]
        if not done_piles:
            tk.Label(
                self._rows_frame, text="No characters were finalized.",
                bg=BG_COLOR, fg=TEXT_SECONDARY, font=BODY_FONT,
            ).pack(pady=20)
            return

        for pid, pile in done_piles:
            fin = pile.finalize
            card = tk.Frame(self._rows_frame, bg=CARD_BG, padx=14, pady=10)
            card.pack(fill="x", pady=4)

            head = tk.Frame(card, bg=CARD_BG)
            head.pack(fill="x")
            tk.Label(head, text=fin.display_name, bg=CARD_BG, fg=TEXT_COLOR,
                     font=SECTION_FONT).pack(side="left")

            verification = fin.verification or {"total": 0, "mismatches": []}
            n_bad = len(verification.get("mismatches", []))
            if n_bad == 0:
                status = f"all {verification.get('total', 0)} combinations verified"
                color = SUCCESS_COLOR
            else:
                status = (f"{n_bad} of {verification.get('total', 0)} "
                          f"combinations mismatched")
                color = WARNING_TEXT
            tk.Label(head, text=status, bg=CARD_BG, fg=color,
                     font=BODY_FONT).pack(side="right")

            stacks = [groups.stacks[sid] for sid in pile.stack_ids
                      if sid in groups.stacks]
            n_outfits = sum(len(s.outfits) for s in stacks)
            detail = (f"{n_outfits} pose{'s' if n_outfits != 1 else ''} · "
                      f"voice {fin.voice} · "
                      f"scale {fin.scale:.2f} · {fin.output_folder}")
            tk.Label(card, text=detail, bg=CARD_BG, fg=TEXT_SECONDARY,
                     font=SMALL_FONT, anchor="w").pack(fill="x", pady=(4, 0))

            for mismatch in verification.get("mismatches", [])[:6]:
                tk.Label(
                    card,
                    text=(f"   pose {mismatch['pose']} / "
                          f"{mismatch['outfit']} / {mismatch['image']} "
                          f"(diff {mismatch['diff_frac']:.3%})"),
                    bg=CARD_BG, fg=WARNING_TEXT, font=SMALL_FONT, anchor="w",
                ).pack(fill="x")
            if n_bad > 6:
                tk.Label(card, text=f"   ... and {n_bad - 6} more",
                         bg=CARD_BG, fg=WARNING_TEXT, font=SMALL_FONT,
                         anchor="w").pack(fill="x")

            actions = tk.Frame(card, bg=CARD_BG)
            actions.pack(fill="x", pady=(8, 0))
            create_secondary_button(
                actions, "Fix Sorting (redo)",
                lambda p=pid: self._fix_sorting(p), width=18,
            ).pack(side="left")
            create_secondary_button(
                actions, "Test in Sprite Tester",
                lambda pl=pile: self._test_character(pl), width=20,
            ).pack(side="left", padx=(8, 0))

    def _step_index(self, step_id: str):
        for i, step in enumerate(self.wizard._steps):
            if step.STEP_ID == step_id:
                return i
        return None

    def _fix_sorting(self, pile_id: str) -> None:
        """Redo ONE character starting from grouping. Deletes its output and
        clears its finalize result, focuses the Review step on just this
        character, then the Finalize step rebuilds it and returns here."""
        groups = self.state.groups
        pile = groups.piles.get(pile_id)
        if pile is None:
            return
        fin = pile.finalize
        if not messagebox.askyesno(
                "Fix Sorting",
                f"Redo \"{fin.display_name}\" from the grouping step?\n\n"
                f"Its current output folder is replaced. You will re-check its "
                f"pose grouping (split/merge), then step through its crop, eye, "
                f"scale, and name screens again. Other characters are untouched.",
                parent=self.wizard.root):
            return
        if fin.output_folder:
            old = self.state.workspace / fin.output_folder
            if old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
        fin.done = False
        fin.output_folder = None
        fin.verification = None
        self.state.redo_focus_pile = pile_id
        workspace.save_groups(self.state)

        review_idx = self._step_index("imp_review")
        if review_idx is not None:
            # The focused Review step disables Back itself; no need to touch it
            # here. Summary re-disables Back when the redo returns.
            self.wizard.go_to_step(review_idx)

    def _test_character(self, pile) -> None:
        """Open the (single-character) Sprite Tester for this character. Runs
        Ren'Py as a separate program; this window stays open (inert until
        Ren'Py closes)."""
        fin = pile.finalize
        if not fin.output_folder:
            return
        char_dir = self.state.workspace / fin.output_folder
        if not (char_dir / "character.yml").is_file():
            messagebox.showerror(
                "Not Found",
                f"Could not find {char_dir}. Try redoing this character.",
                parent=self.wizard.root)
            return
        try:
            from tools.tester import launch_sprite_tester
        except Exception as e:
            messagebox.showerror(
                "Tester Unavailable",
                f"The Sprite Tester module is not available:\n{e}",
                parent=self.wizard.root)
            return
        try:
            launch_sprite_tester(char_dir)
        except Exception as e:
            messagebox.showerror(
                "Tester Error",
                f"Could not launch the Sprite Tester:\n{e}",
                parent=self.wizard.root)

    def _open_output(self) -> None:
        out = workspace.output_dir(self.state.workspace)
        try:
            open_in_file_manager(out)
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder:\n{e}",
                                 parent=self.wizard.root)

    def validate(self) -> bool:
        return True
