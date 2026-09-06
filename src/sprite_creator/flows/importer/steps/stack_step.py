"""
Step 3, Auto-Stack: run the deterministic pixel-diff grouping over the
downloaded images and show a summary. Skipped when a resumed import
already has a groups.json (re-stacking is offered from the Review step).
"""

import threading
import tkinter as tk

from ....config import (
    BG_COLOR,
    CARD_BG,
    TEXT_COLOR,
    TEXT_SECONDARY,
    PAGE_TITLE_FONT,
    SECTION_FONT,
    BODY_FONT,
)
from ....ui.screens.base import WizardStep
from ....ui.widgets import ProgressLogPanel
from .. import workspace
from ..gc_guard import guard_begin, guard_end
from ..stacker import build_groups, integrate_new_images, CancelledError

PHASE_LABELS = {
    "verify": "Checking images",
    "hash": "Fingerprinting images",
    "compare": "Comparing image pairs",
    "confirm": "Confirming duplicates",
    "face": "Locating face regions",
    "integrate": "Matching new images against existing poses",
}


class AutoStackStep(WizardStep):
    STEP_ID = "imp_stack"
    STEP_TITLE = "Auto-Stack"
    STEP_NUMBER = 3
    STEP_HELP = """The auto-stacker groups the downloaded images with pure
pixel comparison, no AI involved:

- Images that are identical except for the face become EXPRESSIONS of one
  outfit.
- Outfits whose head/hair pixels match become one POSE stack.
- Exact duplicates are folded away.
- Anything that matches nothing (CGs, title screens, event art) lands in
  the Unsorted bin for you to review or discard.

It also finds each pose's face region automatically (from what changes
between expressions) and pre-computes the chin-line suggestion used later
for the expression sheets.

This works because sprite rips are composited variants of one base
render. If a gallery contains loose art instead, most of it will end up
Unsorted, that's expected."""
    STEP_TIP = ""
    OVERVIEW = ("Grouping the downloads into poses, outfits, and expressions, "
                "nothing on disk is moved, and you can adjust the grouping next.")

    def __init__(self, wizard, state):
        super().__init__(wizard, state)
        self._panel: ProgressLogPanel = None
        self._summary_frame: tk.Frame = None
        self._running = False
        self._cancelled = False

    def build_ui(self, parent: tk.Frame) -> None:
        parent.configure(bg=BG_COLOR)
        tk.Label(
            parent, text="Sorting Images", bg=BG_COLOR, fg=TEXT_COLOR,
            font=PAGE_TITLE_FONT,
        ).pack(pady=(0, 4))
        tk.Label(
            parent, text=self.OVERVIEW, bg=BG_COLOR, fg=TEXT_SECONDARY,
            font=BODY_FONT, wraplength=900, justify="center",
        ).pack(pady=(0, 8))

        self._panel = ProgressLogPanel(parent, on_cancel=self._cancel)
        self._panel.pack(fill="both", expand=True, pady=(6, 0))

        self._summary_frame = tk.Frame(parent, bg=CARD_BG, padx=20, pady=14)

    def should_skip(self) -> bool:
        if self.state.pending_new_names:
            return False        # new source needs integrating
        return self.state.groups is not None

    def on_enter(self) -> None:
        if self._running:
            return
        if self.state.pending_new_names and self.state.groups is not None:
            self._start_integration()
            return
        if self.state.groups is not None:
            return
        self._start()

    def _start(self) -> None:
        self._running = True
        self._cancelled = False
        # Reset UI in case this is a re-run (summary shown from a prior pass)
        self._summary_frame.pack_forget()
        if not self._panel.winfo_ismapped():
            self._panel.pack(fill="both", expand=True, pady=(6, 0))
        self._panel.set_status("Preparing…")
        self._panel.set_cancel_enabled(True)
        self.wizard._next_btn.configure(state="disabled")

        raw_dir = self.state.raw_dir
        last = {"phase": None, "done": 0}

        def progress(phase, done, total):
            # Throttle: the compare phase fires tens of thousands of times.
            if phase == last["phase"] and done - last["done"] < 250 and done != total:
                return
            last["phase"], last["done"] = phase, done

            def apply():
                label = PHASE_LABELS.get(phase, phase)
                self._panel.set_status(f"{label}…")
                self._panel.set_progress(done, total)
            self.schedule_callback(apply)

        def work():
            try:
                model = build_groups(
                    raw_dir, progress_cb=progress,
                    cancel=lambda: self._cancelled,
                    disk_cache_dir=workspace.stacker_cache_dir(
                        self.state.workspace),
                )
                self.schedule_callback(lambda: self._on_done(model))
            except CancelledError:
                self.schedule_callback(self._on_cancelled)
            except Exception as e:
                self.schedule_callback(lambda msg=str(e): self._on_error(msg))

        guard_begin()
        threading.Thread(target=work, daemon=True).start()

    def _start_integration(self) -> None:
        """Match a new source's images into the existing model, existing
        poses and character piles are never touched."""
        self._running = True
        self._cancelled = False
        self._summary_frame.pack_forget()
        if not self._panel.winfo_ismapped():
            self._panel.pack(fill="both", expand=True, pady=(6, 0))
        self._panel.set_status("Integrating new images…")
        self._panel.set_cancel_enabled(True)
        self.wizard._next_btn.configure(state="disabled")

        raw_dir = self.state.raw_dir
        model = self.state.groups
        new_names = list(self.state.pending_new_names)
        last = {"phase": None, "done": 0}

        def progress(phase, done, total):
            if phase == last["phase"] and done - last["done"] < 100 and done != total:
                return
            last["phase"], last["done"] = phase, done

            def apply():
                label = PHASE_LABELS.get(phase, phase)
                self._panel.set_status(f"{label}…")
                self._panel.set_progress(done, total)
            self.schedule_callback(apply)

        def work():
            try:
                summary = integrate_new_images(
                    model, raw_dir, new_names,
                    progress_cb=progress,
                    cancel=lambda: self._cancelled,
                    disk_cache_dir=workspace.stacker_cache_dir(
                        self.state.workspace),
                )
                self.schedule_callback(lambda: self._on_integrated(summary))
            except CancelledError:
                self.schedule_callback(self._on_cancelled)
            except Exception as e:
                self.schedule_callback(lambda msg=str(e): self._on_error(msg))

        guard_begin()
        threading.Thread(target=work, daemon=True).start()

    def _on_integrated(self, summary: dict) -> None:
        guard_end()
        self._running = False
        self.state.pending_new_names = []
        workspace.save_groups(self.state)

        self._panel.pack_forget()
        for child in self._summary_frame.winfo_children():
            child.destroy()
        tk.Label(
            self._summary_frame, text="New images integrated", bg=CARD_BG,
            fg=TEXT_COLOR, font=SECTION_FONT,
        ).pack(anchor="w")
        lines = [
            f"{summary['added_images']} new images processed",
            f"{summary['joined']} matched into existing poses",
            f"{summary['new_stacks']} new poses to group into characters",
            f"{summary['unsorted']} unsorted",
        ]
        for line in lines:
            tk.Label(
                self._summary_frame, text="•  " + line, bg=CARD_BG,
                fg=TEXT_SECONDARY, font=BODY_FONT, anchor="w",
            ).pack(fill="x", pady=1)
        self._summary_frame.pack(pady=(10, 0))
        self.wizard._next_btn.configure(state="normal")

    def _cancel(self) -> None:
        self._cancelled = True
        self._panel.set_status("Cancelling…")

    def _on_done(self, model) -> None:
        guard_end()
        self._running = False
        self.state.groups = model
        workspace.save_groups(self.state)

        n_stacks = len(model.stacks)
        n_outfits = sum(len(s.outfits) for s in model.stacks.values())
        n_expr = sum(s.expression_count() for s in model.stacks.values())
        n_dupes = sum(len(s.duplicates) for s in model.stacks.values())

        self._panel.pack_forget()
        for child in self._summary_frame.winfo_children():
            child.destroy()
        tk.Label(
            self._summary_frame, text="Sorting complete", bg=CARD_BG,
            fg=TEXT_COLOR, font=SECTION_FONT,
        ).pack(anchor="w")
        lines = [
            f"{n_stacks} pose stacks found",
            f"{n_outfits} outfit groups",
            f"{n_expr} expression images",
            f"{n_dupes} exact duplicates folded away",
            f"{len(model.unsorted)} unsorted images (CGs, misc art)",
        ]
        if model.discarded:
            lines.append(f"{len(model.discarded)} unreadable files skipped")
        for line in lines:
            tk.Label(
                self._summary_frame, text="•  " + line, bg=CARD_BG,
                fg=TEXT_SECONDARY, font=BODY_FONT, anchor="w",
            ).pack(fill="x", pady=1)

        if n_stacks == 0:
            tk.Label(
                self._summary_frame,
                text="No sprite variant sets were detected. This gallery may "
                     "not contain composited sprite rips. You can still sort "
                     "the images manually on the next screen.",
                bg=CARD_BG, fg=TEXT_SECONDARY, font=BODY_FONT,
                wraplength=600, justify="left",
            ).pack(fill="x", pady=(8, 0))

        self._summary_frame.pack(pady=(10, 0))
        self.wizard._next_btn.configure(state="normal")

    def _on_cancelled(self) -> None:
        guard_end()
        self._running = False
        self._panel.set_status("Sorting cancelled. Go back, or press Next to retry.")
        self._panel.set_cancel_enabled(True)
        # Re-entering the step restarts the run
        self.wizard._next_btn.configure(state="disabled")
        self._start_retry_hint()

    def _start_retry_hint(self) -> None:
        self._panel.append_log("Tip: use Back and Next to re-run sorting.")

    def _on_error(self, message: str) -> None:
        guard_end()
        self._running = False
        self._panel.set_cancel_enabled(False)
        self._panel.set_status(f"Sorting failed: {message}")
        self._panel.append_log(f"ERROR: {message}")

    def validate(self) -> bool:
        return self.state.groups is not None
