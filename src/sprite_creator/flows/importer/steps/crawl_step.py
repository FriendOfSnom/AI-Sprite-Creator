"""
Step 2, Download: run the gallery crawler with live progress and cancel.
Skipped for local-folder imports and already-downloaded resumes.
"""

import threading
import tkinter as tk
from urllib.parse import urlparse

from ....config import (
    BG_COLOR,
    TEXT_COLOR,
    TEXT_SECONDARY,
    PAGE_TITLE_FONT,
    BODY_FONT,
    load_site_cookies,
)
from ....ui.screens.base import WizardStep
from ....ui.widgets import ProgressLogPanel
from .. import workspace
from ..crawler import CancelToken, crawl_gallery
from ..gc_guard import guard_begin, guard_end


class CrawlStep(WizardStep):
    STEP_ID = "imp_crawl"
    STEP_TITLE = "Download"
    STEP_NUMBER = 2
    STEP_HELP = """The crawler walks the gallery page by page, downloading
every full-size image into the import workspace.

- Progress is saved continuously: if the crawl is interrupted (cancel,
  crash, network loss), resuming this import picks up where it left off
  without re-downloading anything.
- Failed images are retried automatically at the end.
- Cancel stops the crawl; you can either resume it later or continue to
  sorting with the images downloaded so far."""
    STEP_TIP = ""
    OVERVIEW = ("Downloading the gallery, a large one can take a few minutes, "
                "and you can cancel and resume anytime.")

    def __init__(self, wizard, state):
        super().__init__(wizard, state)
        self._panel: ProgressLogPanel = None
        self._cancel_token: CancelToken = None
        self._running = False
        self._crawl_prefix = ""

    def build_ui(self, parent: tk.Frame) -> None:
        parent.configure(bg=BG_COLOR)
        tk.Label(
            parent, text="Downloading Gallery", bg=BG_COLOR, fg=TEXT_COLOR,
            font=PAGE_TITLE_FONT,
        ).pack(pady=(0, 4))
        tk.Label(
            parent, text=self.OVERVIEW, bg=BG_COLOR, fg=TEXT_SECONDARY,
            font=BODY_FONT, wraplength=900, justify="center",
        ).pack(pady=(0, 8))

        self._panel = ProgressLogPanel(parent, on_cancel=self._cancel_crawl)
        self._panel.pack(fill="both", expand=True, pady=(6, 0))

    def should_skip(self) -> bool:
        if self.state.pending_source_url:
            return False        # follow-up source needs downloading
        return self.state.source_mode == "local" or self.state.crawl_complete

    def on_enter(self) -> None:
        if self._running:
            return
        if self.state.pending_source_url:
            self._start_crawl(self.state.pending_source_url,
                              self.state.pending_source_prefix)
            return
        if self.state.crawl_complete:
            return
        self._start_crawl(self.state.source_url, "")

    def on_leave(self) -> None:
        # Navigating away cancels the crawl; resume files make it lossless.
        if self._running and self._cancel_token:
            self._cancel_token.cancel()

    def _start_crawl(self, start_url: str, file_prefix: str) -> None:
        self._running = True
        self._cancel_token = CancelToken()
        self._panel.set_status("Starting crawl…")
        self._panel.set_cancel_enabled(True)
        self.wizard._next_btn.configure(state="disabled")

        netloc = urlparse(start_url).netloc
        cookies = load_site_cookies(netloc) or None
        raw_dir = self.state.raw_dir
        token = self._cancel_token
        self._crawl_prefix = file_prefix

        def progress(event):
            def apply():
                if event.index:
                    self._panel.set_progress(event.index, None)
                    self._panel.set_status(f"Downloading image {event.index}…")
                self._panel.append_log(event.message)
            self.schedule_callback(apply)

        def work():
            try:
                result = crawl_gallery(
                    raw_dir, start_url, cookies=cookies,
                    progress_cb=progress, cancel=token,
                    file_prefix=file_prefix,
                )
                self.schedule_callback(lambda: self._on_done(result))
            except Exception as e:
                self.schedule_callback(lambda msg=str(e): self._on_error(msg))

        guard_begin()
        threading.Thread(target=work, daemon=True).start()

    def _cancel_crawl(self) -> None:
        if self._cancel_token:
            self._cancel_token.cancel()
            self._panel.set_status("Cancelling…")

    def _on_done(self, result) -> None:
        guard_end()
        self._running = False
        self._panel.set_cancel_enabled(False)

        if self._crawl_prefix:
            # Follow-up source: record it and hand the new filenames to the
            # integration pass. The original crawl_complete flag is untouched.
            prefix = self._crawl_prefix
            source_url = self.state.pending_source_url
            self.state.pending_new_names = [
                n for n in workspace.list_raw_images(self.state.workspace)
                if n.startswith(prefix)
            ]
            self.state.pending_source_url = ""
            workspace._record_source(self.state, {
                "mode": "crawl", "start_url": source_url,
                "prefix": prefix, "added": len(self.state.pending_new_names),
            })
            workspace.save_import_meta(self.state)
        else:
            self.state.crawl_complete = result.completed
            workspace.save_import_meta(self.state)

        total = workspace.count_raw_images(self.state.workspace)
        if result.error:
            summary = result.error
            if total:
                summary += f" ({total} images downloaded so far.)"
                self.wizard._next_btn.configure(state="normal")
            self._panel.set_status(summary)
            self._panel.append_log(summary)
            return
        if result.completed:
            summary = (f"Download complete: {result.downloaded} new, "
                       f"{result.skipped} already present ({total} total).")
            if result.failed_pages:
                summary += f" {len(result.failed_pages)} pages failed permanently."
        else:
            summary = (f"Download stopped: {total} images in the workspace. "
                       f"You can continue with these, or resume the download "
                       f"later from the first screen.")
        self._panel.set_status(summary)
        self._panel.append_log(summary)
        self.wizard._next_btn.configure(state="normal")

    def _on_error(self, message: str) -> None:
        guard_end()
        self._running = False
        self._panel.set_cancel_enabled(False)
        self._panel.set_status(f"Download failed: {message}")
        self._panel.append_log(f"ERROR: {message}")
        total = workspace.count_raw_images(self.state.workspace)
        if total:
            self._panel.append_log(
                f"{total} images were downloaded before the error, "
                f"you can continue with those."
            )
            self.wizard._next_btn.configure(state="normal")

    def validate(self) -> bool:
        return workspace.count_raw_images(self.state.workspace) > 0
