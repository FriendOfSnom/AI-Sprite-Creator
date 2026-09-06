"""
Generic progress panel: status line, optional progress counter, scrolling
log, and a cancel button. Used by long-running background operations
(crawling, auto-stacking). All methods are main-thread only, marshal
background-thread updates through the wizard's schedule_callback.
"""

import tkinter as tk
from typing import Callable, Optional

from ...config import (
    BG_COLOR,
    BG_SECONDARY,
    TEXT_COLOR,
    TEXT_SECONDARY,
    BODY_FONT_BOLD,
    SMALL_FONT,
)
from ..tk_common import create_danger_button

MAX_LOG_LINES = 500


class ProgressLogPanel(tk.Frame):
    """Status + log + cancel, styled for the dark theme."""

    def __init__(self, parent: tk.Widget, on_cancel: Optional[Callable] = None):
        super().__init__(parent, bg=BG_COLOR)
        self._on_cancel = on_cancel

        self._status_label = tk.Label(
            self, text="", bg=BG_COLOR, fg=TEXT_COLOR, font=BODY_FONT_BOLD,
            anchor="w",
        )
        self._status_label.pack(fill="x", pady=(0, 4))

        self._progress_label = tk.Label(
            self, text="", bg=BG_COLOR, fg=TEXT_SECONDARY, font=SMALL_FONT,
            anchor="w",
        )
        self._progress_label.pack(fill="x", pady=(0, 8))

        log_frame = tk.Frame(self, bg=BG_SECONDARY)
        log_frame.pack(fill="both", expand=True)

        self._log = tk.Text(
            log_frame,
            bg=BG_SECONDARY,
            fg=TEXT_SECONDARY,
            font=(SMALL_FONT[0], 9),
            relief="flat",
            state="disabled",
            wrap="none",
            height=12,
        )
        scrollbar = tk.Scrollbar(log_frame, command=self._log.yview, width=10)
        self._log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._log.pack(side="left", fill="both", expand=True, padx=8, pady=8)

        if on_cancel is not None:
            btn_row = tk.Frame(self, bg=BG_COLOR)
            btn_row.pack(fill="x", pady=(10, 0))
            self._cancel_btn = create_danger_button(
                btn_row, "Cancel", self._handle_cancel, width=12
            )
            self._cancel_btn.pack(side="left")
        else:
            self._cancel_btn = None

    def set_status(self, text: str) -> None:
        self._status_label.configure(text=text)

    def set_progress(self, done: int, total: Optional[int]) -> None:
        if total:
            self._progress_label.configure(text=f"{done} / {total}")
        else:
            self._progress_label.configure(text=str(done) if done else "")

    def append_log(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line + "\n")
        # Trim old lines to keep the widget snappy on long crawls
        line_count = int(self._log.index("end-1c").split(".")[0])
        if line_count > MAX_LOG_LINES:
            self._log.delete("1.0", f"{line_count - MAX_LOG_LINES}.0")
        self._log.see("end")
        self._log.configure(state="disabled")

    def set_cancel_enabled(self, enabled: bool) -> None:
        if self._cancel_btn is not None:
            self._cancel_btn.configure(state="normal" if enabled else "disabled")

    def _handle_cancel(self) -> None:
        if self._on_cancel:
            self.set_cancel_enabled(False)
            self._on_cancel()
