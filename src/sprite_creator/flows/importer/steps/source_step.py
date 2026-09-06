"""
Step 1, Source: resume an existing import, or start a new one from a
gallery URL (crawler) or a local folder of already-downloaded images.
"""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from urllib.parse import urlparse

from ....config import (
    BG_COLOR,
    CARD_BG,
    CARD_BG_HOVER,
    ACCENT_COLOR,
    TEXT_COLOR,
    TEXT_SECONDARY,
    PAGE_TITLE_FONT,
    SECTION_FONT,
    BODY_FONT,
    SMALL_FONT,
    load_site_cookies,
    save_site_cookies,
)
from ....ui.screens.base import WizardStep
from ....ui.tk_common import (
    create_primary_button,
    create_secondary_button,
    create_segmented_control,
)
from .. import workspace
from ..crawler import parse_cookie_header, needs_cookies


class SourceStep(WizardStep):
    STEP_ID = "imp_source"
    STEP_TITLE = "Source"
    STEP_NUMBER = 1
    STEP_HELP = """Choose where the sprite images come from.

Resume an import
Every import lives in its own workspace and is saved continuously, pick
one from the list to continue exactly where you left off (downloading,
sorting, or finalizing).

New import, Crawl gallery
Paste the URL of the FIRST IMAGE PAGE of an E-Hentai or ExHentai gallery
(open the first image, then copy that page's URL, not the gallery
overview). The crawler walks the gallery, downloading every full-size
image, and can resume if interrupted.

ExHentai requires login cookies. Open DevTools on an exhentai page →
Application → Cookies, and copy the values as: k=v; k2=v2; ...
Cookies are remembered for future crawls.

New import, Local folder
Point at a folder of images you already downloaded (any source). The
images are copied into the workspace; your originals are not touched.

Where everything lives
Each import gets its own workspace folder under:
  ~/.sprite_creator/imports/<game name>/
with the downloaded images in raw/ and the finished ST character
folders in output/. (The folder starts with a dot, so it's hidden by
default, press Ctrl+H in your file manager to see it.) The Summary
screen has buttons to open the output folder or export the characters
to your game."""
    STEP_TIP = ""
    OVERVIEW = ("Resume an import below, or start a new one: either download "
                "a gallery from a link or use sprite images you already have.")

    # Mode labels (also used as the segmented-control values)
    MODE_CRAWL = "Download from a link"
    MODE_LOCAL = "Use images I have"

    def __init__(self, wizard, state):
        super().__init__(wizard, state)
        self._selected_resume: Path = None
        self._resume_rows = []
        self._mode_control = None
        self._name_entry = None
        self._url_entry = None
        self._cookie_entry = None
        self._cookie_frame = None
        self._folder_var = None

    # ------------------------------------------------------------------
    def build_ui(self, parent: tk.Frame) -> None:
        parent.configure(bg=BG_COLOR)

        tk.Label(
            parent, text="Import Game Sprites", bg=BG_COLOR, fg=TEXT_COLOR,
            font=PAGE_TITLE_FONT,
        ).pack(pady=(0, 4))
        tk.Label(
            parent, text=self.OVERVIEW, bg=BG_COLOR, fg=TEXT_SECONDARY,
            font=BODY_FONT, wraplength=900, justify="center",
        ).pack(pady=(0, 8))

        # Capped-width centered column: on wide monitors a full-bleed form
        # scatters related controls; everything stays in one readable block.
        body = tk.Frame(parent, bg=BG_COLOR)
        body.pack(anchor="center")

        # ---- Resume section ------------------------------------------------
        self._resume_section = tk.Frame(body, bg=BG_COLOR)
        self._resume_section.pack(fill="x", pady=(4, 12))

        tk.Label(
            self._resume_section, text="Resume an import", bg=BG_COLOR,
            fg=TEXT_COLOR, font=SECTION_FONT, anchor="w",
        ).pack(fill="x")

        self._resume_list = tk.Frame(self._resume_section, bg=BG_COLOR)
        self._resume_list.pack(fill="x", pady=(6, 0))

        # Shown once a resume row is selected
        self._add_more_btn = create_secondary_button(
            self._resume_section, "Add More Images to This Import…",
            self._add_more_images, width=30)

        # ---- New import section -------------------------------------------
        new_section = tk.Frame(body, bg=BG_COLOR)
        new_section.pack(fill="x")

        tk.Label(
            new_section, text="New import", bg=BG_COLOR, fg=TEXT_COLOR,
            font=SECTION_FONT, anchor="w",
        ).pack(fill="x", pady=(4, 6))

        form = tk.Frame(new_section, bg=CARD_BG, padx=16, pady=12)
        form.pack(fill="x")

        # Game name
        tk.Label(
            form, text="Game name (used for the workspace and character.yml):",
            bg=CARD_BG, fg=TEXT_COLOR, font=BODY_FONT, anchor="w",
        ).pack(fill="x")
        self._name_entry = tk.Entry(form, font=BODY_FONT, width=44)
        self._name_entry.pack(anchor="w", pady=(2, 10))
        self._name_entry.bind("<Key>", lambda e: self._clear_resume_selection())

        # Mode selector, where do the images come from?
        tk.Label(
            form, text="Where are the sprite images?", bg=CARD_BG,
            fg=TEXT_COLOR, font=BODY_FONT, anchor="w",
        ).pack(fill="x", pady=(0, 4))
        self._mode_control = create_segmented_control(
            form, [self.MODE_CRAWL, self.MODE_LOCAL], default=self.MODE_CRAWL,
            on_change=lambda v: self._on_mode_change(),
        )
        self._mode_control.pack(anchor="w", pady=(0, 4))
        self._mode_desc = tk.Label(
            form, text="", bg=CARD_BG, fg=TEXT_SECONDARY, font=SMALL_FONT,
            anchor="w", wraplength=560, justify="left",
        )
        self._mode_desc.pack(fill="x", pady=(0, 10))

        # Crawl form
        self._crawl_frame = tk.Frame(form, bg=CARD_BG)
        tk.Label(
            self._crawl_frame,
            text="Starting image page URL (the first image's page, not the gallery cover):",
            bg=CARD_BG, fg=TEXT_COLOR, font=BODY_FONT, anchor="w",
        ).pack(fill="x")
        self._url_entry = tk.Entry(self._crawl_frame, font=BODY_FONT, width=70)
        self._url_entry.pack(anchor="w", pady=(2, 6))
        self._url_entry.bind("<KeyRelease>", lambda e: self._on_url_change())

        self._cookie_frame = tk.Frame(self._crawl_frame, bg=CARD_BG)
        tk.Label(
            self._cookie_frame,
            text="ExHentai cookies (k=v; k2=v2; ...). Saved for next time:",
            bg=CARD_BG, fg=TEXT_COLOR, font=BODY_FONT, anchor="w",
        ).pack(fill="x")
        self._cookie_entry = tk.Entry(self._cookie_frame, font=BODY_FONT,
                                      width=70, show="•")
        self._cookie_entry.pack(anchor="w", pady=(2, 0))

        # Local-folder form
        self._local_frame = tk.Frame(form, bg=CARD_BG)
        self._folder_var = tk.StringVar(value="")
        row = tk.Frame(self._local_frame, bg=CARD_BG)
        row.pack(fill="x")
        create_secondary_button(row, "Choose Folder…", self._browse_folder,
                                width=16).pack(side="left")
        tk.Label(
            row, textvariable=self._folder_var, bg=CARD_BG, fg=TEXT_SECONDARY,
            font=SMALL_FONT, anchor="w",
        ).pack(side="left", padx=(10, 0))

        self._on_mode_change()

    # ------------------------------------------------------------------
    def on_enter(self) -> None:
        self._populate_resume_list()

    def _populate_resume_list(self) -> None:
        for child in self._resume_list.winfo_children():
            child.destroy()
        self._resume_rows = []
        self._selected_resume = None
        self._add_more_btn.pack_forget()

        imports = workspace.list_imports()
        if not imports:
            self._resume_section.pack_forget()
            return
        self._resume_section.pack(fill="x", pady=(4, 12),
                                  before=self._resume_section.master.winfo_children()[-1])

        for summary in imports[:8]:
            row = tk.Frame(self._resume_list, bg=CARD_BG, padx=12, pady=6,
                           highlightthickness=1, highlightbackground=CARD_BG)
            row.pack(fill="x", pady=2)
            text = (f"{summary.game_name}  ,   {summary.raw_images} images, "
                    f"{summary.stage}")
            label = tk.Label(row, text=text, bg=CARD_BG, fg=TEXT_COLOR,
                             font=BODY_FONT, anchor="w")
            label.pack(side="left", fill="x", expand=True)

            delete_btn = tk.Label(row, text="✕", bg=CARD_BG, fg=TEXT_SECONDARY,
                                  font=BODY_FONT, cursor="hand2", padx=6)
            delete_btn.pack(side="right")
            delete_btn.bind(
                "<Button-1>",
                lambda e, ws=summary.workspace, name=summary.game_name:
                    self._delete_import(ws, name))

            for widget in (row, label):
                widget.bind("<Button-1>",
                            lambda e, ws=summary.workspace, r=row: self._select_resume(ws, r))
                widget.configure(cursor="hand2")
            self._resume_rows.append(row)

    def _delete_import(self, ws: Path, name: str) -> None:
        if messagebox.askyesno(
                "Delete Import",
                f"Delete the import \"{name}\" and ALL its files "
                f"(downloaded images, sorting, finalized characters in its "
                f"output folder)?\n\nThis cannot be undone.",
                parent=self.wizard.root):
            try:
                workspace.delete_import(ws)
            except Exception as e:
                messagebox.showerror("Delete Failed", str(e),
                                     parent=self.wizard.root)
            self._populate_resume_list()

    def _select_resume(self, ws: Path, row: tk.Frame) -> None:
        self._selected_resume = ws
        for r in self._resume_rows:
            r.configure(highlightbackground=CARD_BG, bg=CARD_BG)
            for c in r.winfo_children():
                c.configure(bg=CARD_BG)
        row.configure(highlightbackground=ACCENT_COLOR, bg=CARD_BG_HOVER)
        for c in row.winfo_children():
            c.configure(bg=CARD_BG_HOVER)
        self._add_more_btn.pack(fill="x", pady=(6, 0))

    def _add_more_images(self) -> None:
        """Add a second gallery/folder to the selected import, the new
        images get matched into the existing poses without re-sorting."""
        if self._selected_resume is None:
            return
        try:
            workspace.load_import(self._selected_resume, self.state)
        except Exception as e:
            messagebox.showerror("Load Failed", str(e), parent=self.wizard.root)
            return
        if self.state.groups is None:
            messagebox.showinfo(
                "Not Sorted Yet",
                "This import hasn't been sorted yet, just resume it "
                "normally and the new images can be part of the first sort.",
                parent=self.wizard.root)
            return

        win = tk.Toplevel(self.wizard.root)
        win.title("Add More Images")
        win.configure(bg=BG_COLOR)
        win.transient(self.wizard.root)
        win.geometry("640x300")

        tk.Label(win, text=f"Add another source to \"{self.state.game_name}\"",
                 bg=BG_COLOR, fg=TEXT_COLOR, font=SECTION_FONT).pack(pady=(14, 2))
        tk.Label(win, text="New poses appear as extra cards to group; images "
                           "matching existing poses join them automatically. "
                           "Your characters and sorting are untouched.",
                 bg=BG_COLOR, fg=TEXT_SECONDARY, font=SMALL_FONT,
                 wraplength=560).pack(pady=(0, 10))

        mode = create_segmented_control(win, ["Crawl gallery", "Local folder"],
                                        default="Crawl gallery")
        mode.pack()

        url_entry = tk.Entry(win, font=BODY_FONT, width=64)
        url_entry.pack(pady=(10, 0))
        url_entry.insert(0, "")
        tk.Label(win, text="Gallery: first image page URL · Folder: leave URL "
                           "empty and press Choose Folder",
                 bg=BG_COLOR, fg=TEXT_SECONDARY, font=SMALL_FONT).pack()

        def choose_folder():
            folder = filedialog.askdirectory(title="Folder of images",
                                             parent=win)
            if not folder:
                return
            win.destroy()
            self._start_local_addition(Path(folder))

        def start_crawl():
            url = url_entry.get().strip()
            netloc = urlparse(url).netloc.lower()
            if not url or ("e-hentai.org" not in netloc
                           and "exhentai.org" not in netloc):
                messagebox.showwarning("Invalid URL",
                                       "Enter a gallery image-page URL, or "
                                       "use Choose Folder.", parent=win)
                return
            if needs_cookies(url) and not load_site_cookies(netloc):
                messagebox.showwarning(
                    "Cookies Required",
                    "ExHentai needs saved cookies, start a normal ExHentai "
                    "import once to save them.", parent=win)
                return
            win.destroy()
            self.state.pending_source_url = url
            self.state.pending_source_prefix = \
                workspace.source_prefix_for_crawl(self.state.workspace)
            self.request_next()

        btn_row = tk.Frame(win, bg=BG_COLOR)
        btn_row.pack(pady=14)
        create_secondary_button(btn_row, "Choose Folder…", choose_folder,
                                width=15).pack(side="left", padx=(0, 8))
        create_primary_button(btn_row, "Start Download", start_crawl,
                              width=15).pack(side="left")

        def _grab():
            try:
                win.grab_set()
            except tk.TclError:
                pass
        win.after(100, _grab)

    def _start_local_addition(self, folder: Path) -> None:
        n_images = sum(1 for p in folder.iterdir()
                       if p.is_file() and p.suffix.lower() in workspace.IMAGE_EXTS)
        if n_images == 0:
            messagebox.showwarning("No Images", "That folder has no supported "
                                                "images.", parent=self.wizard.root)
            return
        self.show_loading(f"Copying {n_images} images…")
        try:
            added = workspace.add_local_source(self.state, folder)
        except Exception as e:
            self.hide_loading()
            messagebox.showerror("Copy Failed", str(e), parent=self.wizard.root)
            return
        self.hide_loading()
        self.state.pending_new_names = added
        self.request_next()

    def _clear_resume_selection(self) -> None:
        if self._selected_resume is not None:
            self._selected_resume = None
            for r in self._resume_rows:
                r.configure(highlightbackground=CARD_BG, bg=CARD_BG)
                for c in r.winfo_children():
                    c.configure(bg=CARD_BG)
        self._add_more_btn.pack_forget()

    # ------------------------------------------------------------------
    def _on_mode_change(self) -> None:
        self._clear_resume_selection()
        if self._mode_control.selected == self.MODE_CRAWL:
            self._local_frame.pack_forget()
            self._crawl_frame.pack(fill="x")
            self._mode_desc.configure(
                text="Downloads an E-Hentai / ExHentai gallery from its first "
                     "image page. Paste that URL below.")
            self._on_url_change()
        else:
            self._crawl_frame.pack_forget()
            self._local_frame.pack(fill="x")
            self._mode_desc.configure(
                text="Uses a folder of sprite images already on this computer. "
                     "They're copied into the workspace; your originals aren't "
                     "touched.")

    def _on_url_change(self) -> None:
        url = self._url_entry.get().strip()
        if url and needs_cookies(url):
            if not self._cookie_frame.winfo_ismapped():
                self._cookie_frame.pack(fill="x", pady=(4, 0))
                saved = load_site_cookies(urlparse(url).netloc)
                if saved and not self._cookie_entry.get():
                    header = "; ".join(f"{k}={v}" for k, v in saved.items())
                    self._cookie_entry.insert(0, header)
        else:
            self._cookie_frame.pack_forget()

    def _browse_folder(self) -> None:
        self._clear_resume_selection()
        folder = filedialog.askdirectory(title="Select folder of sprite images",
                                         parent=self.wizard.root)
        if folder:
            self._folder_var.set(folder)
            if not self._name_entry.get().strip():
                self._name_entry.insert(0, Path(folder).name)

    # ------------------------------------------------------------------
    def validate(self) -> bool:
        # Resume path
        if self._selected_resume is not None:
            try:
                workspace.load_import(self._selected_resume, self.state)
            except Exception as e:
                messagebox.showerror(
                    "Resume Failed",
                    f"Could not load that import:\n{e}",
                    parent=self.wizard.root,
                )
                return False
            return True

        # New import
        game_name = self._name_entry.get().strip()
        if not game_name:
            messagebox.showwarning("Missing Name", "Please enter a game name.",
                                   parent=self.wizard.root)
            return False

        if self._mode_control.selected == self.MODE_CRAWL:
            url = self._url_entry.get().strip()
            netloc = urlparse(url).netloc.lower()
            if not url or ("e-hentai.org" not in netloc
                           and "exhentai.org" not in netloc):
                messagebox.showwarning(
                    "Invalid URL",
                    "Please enter an image page URL from e-hentai.org or "
                    "exhentai.org (open the gallery's first image and copy "
                    "that page's address).",
                    parent=self.wizard.root,
                )
                return False
            if needs_cookies(url):
                cookies = parse_cookie_header(self._cookie_entry.get().strip())
                if not cookies:
                    messagebox.showwarning(
                        "Cookies Required",
                        "ExHentai requires login cookies. Paste your cookie "
                        "header (see Help for instructions).",
                        parent=self.wizard.root,
                    )
                    return False
                save_site_cookies(urlparse(url).netloc, cookies)

            workspace.create_import(self.state, game_name, "crawl", source_url=url)
            from ..crawler import write_download_meta
            write_download_meta(self.state.raw_dir, game_name, url)
            return True

        # Local folder
        folder = self._folder_var.get().strip()
        if not folder or not Path(folder).is_dir():
            messagebox.showwarning("Missing Folder",
                                   "Please choose a folder of images.",
                                   parent=self.wizard.root)
            return False

        src = Path(folder)
        n_images = sum(1 for p in src.iterdir()
                       if p.is_file() and p.suffix.lower() in workspace.IMAGE_EXTS)
        if n_images == 0:
            messagebox.showwarning(
                "No Images",
                "That folder contains no supported images "
                "(png / jpg / jpeg / webp).",
                parent=self.wizard.root,
            )
            return False

        self.show_loading(f"Copying {n_images} images into the workspace…")
        try:
            workspace.create_import(
                self.state, game_name, "local", local_source_dir=src,
                copy_progress_cb=lambda done, total: (
                    self.show_loading(f"Copying images… {done}/{total}")
                    if done % 25 == 0 else None
                ),
            )
        except Exception as e:
            self.hide_loading()
            messagebox.showerror("Copy Failed", f"Could not copy images:\n{e}",
                                 parent=self.wizard.root)
            return False
        self.hide_loading()
        return True
