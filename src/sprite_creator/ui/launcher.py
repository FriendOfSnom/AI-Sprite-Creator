"""
Main Launcher Window for ST Sprite Creator.

Provides a graphical launcher with three tool options:
1. Character Sprite Creator (primary) - Full AI-powered sprite generation pipeline
2. Expression Sheet Generator - Generate sheets from existing character folders
3. Sprite Tester - Preview sprites in Ren'Py environment
"""

import shutil
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox
from pathlib import Path
from typing import Optional, Callable

from ..config import (
    APP_NAME,
    APP_VERSION,
    BG_COLOR,
    BG_SECONDARY,
    CARD_BG,
    CARD_BG_HOVER,
    TEXT_COLOR,
    TEXT_SECONDARY,
    ACCENT_COLOR,
    TITLE_FONT,
    SECTION_FONT,
    BODY_FONT,
    SMALL_FONT,
    CARD_PADDING,
    BACKUPS_BASE_DIR,
    BACKGROUND_COLOR_PRESETS,
    load_upload_username,
    save_upload_username,
    load_background_color,
    save_background_color,
)
from .tk_common import (
    apply_dark_theme,
    apply_window_size,
    create_primary_button,
    create_secondary_button,
    create_help_button,
)
from .api_setup import show_api_setup, get_existing_api_key
from .welcome import show_welcome


# Google AI Studio usage dashboard URL
AI_STUDIO_USAGE_URL = "https://aistudio.google.com/usage"


# Help text for the launcher
LAUNCHER_HELP_TEXT = """Welcome to ST Sprite Creator!

Choose one of the tools:

Character Sprite Creator (Recommended)
The full character creation wizard. Start from a reference image, text
description, or fuse two characters together. Includes:
- AI-powered sprite generation with multiple outfits and expressions
- Fusion mode: merge two characters into one
- Automatic background removal and manual touch-up tools
- Scale comparison with reference sprites
- Expression sheet generation

Add to Character
Add new content to an existing character folder:
- Select a base sprite, then normalize it to match AI output resolution
- Add NEW outfits (becomes new pose letters: c, d, e...)
- Add NEW expressions to existing outfits
- REPLACE existing expressions that came out poorly (orange chips)
- Automatically matches scale to existing character
- Preserves all existing settings (voice, name, eye line, etc.)
- Full-size backup images are preferred for best quality

Gemini Workshop
A quick Gemini-powered image tool:
- Load an image + prompt for img2img editing (e.g. "remove cigarette")
- Or just type a prompt for text-to-image generation
- Optional Student Transfer style references for consistent art style
- Auto and manual background removal built in
- History gallery: keep results you like, then click to reuse as input
- Save input or result images directly to disk

Game Sprite Importer
Import existing VN sprite rips and turn them into ST characters:
- Download a gallery with the built-in crawler (or use a local folder)
- Automatic sorting into pose/outfit/expression groups (no AI, pure
  pixel comparison)
- Review grid to assemble poses into characters
- Produces standard ST character folders with expression sheets

Expression Sheet Generator
Create expression reference sheets from existing character folders.
Use this if you already have characters and want visual reference sheets.

Sprite Database
Browse and download community-created characters. Upload your own
characters directly from the finalize screen after creation.

Sprite Tester
Preview character sprites in a simulated Ren'Py environment. Tests outfit
switching, expressions, and the character loading system.

Footer Buttons:
- API Settings: Configure your Google Gemini API key
- View API Usage: Opens Google AI Studio usage dashboard
- View README: Re-read the welcome guide and documentation
- Upload Settings: Set or change your display name for database uploads
- Clear Backups: Delete full-size backup images to free disk space
  (reduces quality when adding to existing characters)

Note: You'll need a Google Cloud API key (not the basic AI Studio free
tier). New Google Cloud accounts get $300 in free credits."""


class ToolCard(tk.Frame):
    """
    A large clickable card for tool selection.
    """

    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        description: str,
        icon_text: str = "",
        primary: bool = False,
        on_click: Optional[Callable] = None,
    ):
        # Primary cards get accent border
        highlight_color = ACCENT_COLOR if primary else CARD_BG
        super().__init__(parent, bg=CARD_BG, highlightbackground=highlight_color,
                         highlightthickness=2 if primary else 1)

        self._on_click = on_click
        self._primary = primary
        self._base_bg = CARD_BG

        # Padding frame
        inner = tk.Frame(self, bg=CARD_BG, padx=12, pady=6)
        inner.pack(fill="both", expand=True)

        # Icon/emoji area (optional)
        if icon_text:
            icon_label = tk.Label(
                inner,
                text=icon_text,
                bg=CARD_BG,
                fg=TEXT_COLOR,
                font=(SECTION_FONT[0], 18),
            )
            icon_label.pack(pady=(0, 2))
            icon_label.bind("<Button-1>", self._handle_click)
            icon_label.bind("<Enter>", self._on_enter)
            icon_label.bind("<Leave>", self._on_leave)

        # Title
        title_label = tk.Label(
            inner,
            text=title,
            bg=CARD_BG,
            fg=TEXT_COLOR,
            font=SECTION_FONT,
        )
        title_label.pack(pady=(0, 4))

        # Description
        desc_label = tk.Label(
            inner,
            text=description,
            bg=CARD_BG,
            fg=TEXT_SECONDARY,
            font=SMALL_FONT,
            wraplength=180,
            justify="center",
        )
        desc_label.pack()

        # Primary badge
        if primary:
            badge = tk.Label(
                inner,
                text="Recommended",
                bg=ACCENT_COLOR,
                fg=TEXT_COLOR,
                font=(SMALL_FONT[0], 9),
                padx=8,
                pady=2,
            )
            badge.pack(pady=(6, 0))
            badge.bind("<Button-1>", self._handle_click)
            badge.bind("<Enter>", self._on_enter)
            badge.bind("<Leave>", self._on_leave)

        # Store references for hover effects
        self._inner = inner
        self._labels = [title_label, desc_label]
        if icon_text:
            self._labels.append(icon_label)

        # Bind click and hover
        self.bind("<Button-1>", self._handle_click)
        inner.bind("<Button-1>", self._handle_click)
        title_label.bind("<Button-1>", self._handle_click)
        desc_label.bind("<Button-1>", self._handle_click)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        inner.bind("<Enter>", self._on_enter)
        inner.bind("<Leave>", self._on_leave)
        title_label.bind("<Enter>", self._on_enter)
        title_label.bind("<Leave>", self._on_leave)
        desc_label.bind("<Enter>", self._on_enter)
        desc_label.bind("<Leave>", self._on_leave)

        self.configure(cursor="hand2")

    def _handle_click(self, event=None):
        if self._on_click:
            self._on_click()

    def _on_enter(self, event=None):
        self._set_bg(CARD_BG_HOVER)

    def _on_leave(self, event=None):
        self._set_bg(self._base_bg)

    def _set_bg(self, color: str):
        self.configure(bg=color)
        self._inner.configure(bg=color)
        for label in self._labels:
            label.configure(bg=color)


class LauncherWindow:
    """
    Main launcher window for the ST Sprite Creator application.
    """

    def __init__(self, on_sprite_creator: Callable, on_expression_sheets: Callable,
                 on_sprite_tester: Callable, on_add_to_existing: Optional[Callable] = None,
                 on_gemini_workshop: Optional[Callable] = None):
        """
        Initialize the launcher window.

        Args:
            on_sprite_creator: Callback when Character Sprite Creator is selected
            on_expression_sheets: Callback when Expression Sheet Generator is selected
            on_sprite_tester: Callback when Sprite Tester is selected
            on_add_to_existing: Callback when Add to Character is selected
            on_gemini_workshop: Callback when Gemini Workshop is selected
        """
        self._on_sprite_creator = on_sprite_creator
        self._on_expression_sheets = on_expression_sheets
        self._on_sprite_tester = on_sprite_tester
        self._on_add_to_existing = on_add_to_existing
        self._on_gemini_workshop = on_gemini_workshop

        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Apply dark theme and sizing
        apply_dark_theme(self.root)
        apply_window_size(self.root, "large")

        self._build_ui()
        self._result: Optional[str] = None

    def _build_ui(self):
        """Build the launcher UI."""
        # Scrollable container for small screens
        scroll_outer = tk.Frame(self.root, bg=BG_COLOR)
        scroll_outer.pack(fill="both", expand=True)

        self._scroll_canvas = tk.Canvas(scroll_outer, bg=BG_COLOR, highlightthickness=0)
        self._scroll_canvas.pack(fill="both", expand=True)

        self._v_scrollbar = tk.Scrollbar(
            scroll_outer, orient="vertical",
            command=self._scroll_canvas.yview, width=10
        )
        self._h_scrollbar = tk.Scrollbar(
            scroll_outer, orient="horizontal",
            command=self._scroll_canvas.xview, width=10
        )
        self._scroll_canvas.configure(
            xscrollcommand=self._h_scrollbar.set,
            yscrollcommand=self._v_scrollbar.set,
        )

        main_frame = tk.Frame(self._scroll_canvas, bg=BG_COLOR, padx=40, pady=20)
        self._canvas_window = self._scroll_canvas.create_window((0, 0), window=main_frame, anchor="nw")

        def _on_configure(event=None):
            self._scroll_canvas.configure(scrollregion=self._scroll_canvas.bbox("all"))
            # Stretch content to fill canvas when canvas is larger (for centering)
            # but don't shrink it below its natural size (allows horizontal scroll)
            canvas_w = self._scroll_canvas.winfo_width()
            content_w = main_frame.winfo_reqwidth()
            self._scroll_canvas.itemconfigure(self._canvas_window, width=max(canvas_w, content_w))
            self._update_scrollbar()

        main_frame.bind("<Configure>", _on_configure)
        self._scroll_canvas.bind("<Configure>", lambda e: _on_configure())
        self._main_frame = main_frame

        # Header with help button
        header_frame = tk.Frame(main_frame, bg=BG_COLOR)
        header_frame.pack(fill="x", pady=(0, 6))

        # Title area (left side)
        title_frame = tk.Frame(header_frame, bg=BG_COLOR)
        title_frame.pack(side="left", fill="x", expand=True)

        title_label = tk.Label(
            title_frame,
            text=APP_NAME,
            bg=BG_COLOR,
            fg=TEXT_COLOR,
            font=TITLE_FONT,
        )
        title_label.pack(anchor="w")

        version_label = tk.Label(
            title_frame,
            text=f"Version {APP_VERSION}",
            bg=BG_COLOR,
            fg=TEXT_SECONDARY,
            font=SMALL_FONT,
        )
        version_label.pack(anchor="w")

        # Help button (right side)
        help_btn = create_help_button(header_frame, "About This Tool", LAUNCHER_HELP_TEXT)
        help_btn.pack(side="right", anchor="ne")

        # Subtitle
        subtitle_label = tk.Label(
            main_frame,
            text="AI-powered character sprite generation for Student Transfer",
            bg=BG_COLOR,
            fg=TEXT_SECONDARY,
            font=BODY_FONT,
        )
        subtitle_label.pack(pady=(0, 10))

        # Tool cards container (3-column, 2-tier grid)
        cards_frame = tk.Frame(main_frame, bg=BG_COLOR)
        cards_frame.pack(fill="both", expand=True)

        cards_frame.columnconfigure(0, weight=1)
        cards_frame.columnconfigure(1, weight=1)
        cards_frame.columnconfigure(2, weight=1)

        # Row 0 (Creation tier): Sprite Creator + Add to Character + Gemini Workshop
        card1 = ToolCard(
            cards_frame,
            title="Character Sprite\nCreator",
            description="Generate complete character sprites with AI. Includes expressions, outfits, and automatic processing.",
            icon_text="\u2728",  # Sparkles emoji
            primary=True,
            on_click=self._select_sprite_creator,
        )
        card1.grid(row=0, column=0, padx=8, pady=6, sticky="nsew")

        card2 = ToolCard(
            cards_frame,
            title="Add to\nCharacter",
            description="Add new outfits or expressions to an existing character folder.",
            icon_text="\u2795",  # Plus sign emoji
            primary=False,
            on_click=self._select_add_to_existing,
        )
        card2.grid(row=0, column=1, padx=8, pady=6, sticky="nsew")

        card3 = ToolCard(
            cards_frame,
            title="Gemini\nWorkshop",
            description="Quick img2img editing or text-to-image generation with Gemini.",
            icon_text="\U0001F528",  # Hammer emoji
            primary=False,
            on_click=self._select_gemini_workshop,
        )
        card3.grid(row=0, column=2, padx=8, pady=6, sticky="nsew")

        # Row 1 (Tools tier): Expression Sheets + Sprite Tester + Sprite Database
        card4 = ToolCard(
            cards_frame,
            title="Expression Sheet\nGenerator",
            description="Create expression reference sheets from existing character folders.",
            icon_text="\U0001F5BC",  # Frame emoji
            primary=False,
            on_click=self._select_expression_sheets,
        )
        card4.grid(row=1, column=0, padx=8, pady=6, sticky="nsew")

        card5 = ToolCard(
            cards_frame,
            title="Sprite\nTester",
            description="Preview character sprites in a Ren'Py environment with outfit and expression switching.",
            icon_text="\U0001F3AE",  # Game controller emoji
            primary=False,
            on_click=self._select_sprite_tester,
        )
        card5.grid(row=1, column=1, padx=8, pady=6, sticky="nsew")

        card6 = ToolCard(
            cards_frame,
            title="Sprite\nDatabase",
            description="Browse characters uploaded by the community.",
            icon_text="\U0001F310",  # Globe emoji
            primary=False,
            on_click=self._open_sprite_database,
        )
        card6.grid(row=1, column=2, padx=8, pady=6, sticky="nsew")

        # Row 2 (Import tier): Game Sprite Importer
        card7 = ToolCard(
            cards_frame,
            title="Game Sprite\nImporter",
            description="Download sprite rips from a gallery, auto-sort them, and convert them into ST characters.",
            icon_text="\U0001F4E5",  # Inbox tray emoji
            primary=False,
            on_click=self._select_sprite_importer,
        )
        card7.grid(row=2, column=0, padx=8, pady=6, sticky="nsew")

        # Footer
        footer_frame = tk.Frame(main_frame, bg=BG_COLOR)
        footer_frame.pack(fill="x", pady=(10, 0))

        footer_text = tk.Label(
            footer_frame,
            text="Requires a Google Cloud API key (free $300 credits for new accounts)",
            bg=BG_COLOR,
            fg=TEXT_SECONDARY,
            font=SMALL_FONT,
        )
        footer_text.pack()

        # Button rows for footer actions (two rows to fit on small screens)
        btn_row1 = tk.Frame(footer_frame, bg=BG_COLOR)
        btn_row1.pack(pady=(10, 4))

        # API Settings button
        settings_btn = create_secondary_button(
            btn_row1,
            "API Settings",
            self._open_api_settings,
            width=14,
        )
        settings_btn.pack(side="left", padx=(0, 8))

        # View Usage button
        usage_btn = create_secondary_button(
            btn_row1,
            "View API Usage",
            self._open_usage_dashboard,
            width=14,
        )
        usage_btn.pack(side="left", padx=(0, 8))

        # View README button
        readme_btn = create_secondary_button(
            btn_row1,
            "View README",
            self._open_readme,
            width=14,
        )
        readme_btn.pack(side="left")

        btn_row2 = tk.Frame(footer_frame, bg=BG_COLOR)
        btn_row2.pack(pady=(0, 0))

        # Clear Backups button
        clear_btn = create_secondary_button(
            btn_row2,
            "Clear Backups",
            self._clear_backups,
            width=14,
        )
        clear_btn.pack(side="left", padx=(0, 8))

        # Upload Settings button
        upload_btn = create_secondary_button(
            btn_row2,
            "Upload Settings",
            self._open_upload_settings,
            width=14,
        )
        upload_btn.pack(side="left", padx=(0, 8))

        # Sprite Settings button
        sprite_settings_btn = create_secondary_button(
            btn_row2,
            "Sprite Settings",
            self._open_sprite_settings,
            width=14,
        )
        sprite_settings_btn.pack(side="left")

    def _unbind_scroll(self):
        """Unbind scroll events before closing."""
        try:
            self._scroll_canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass

    def _select_sprite_creator(self):
        """Handle Character Sprite Creator selection."""
        self._result = "sprite_creator"
        self._unbind_scroll()
        self.root.quit()

    def _select_expression_sheets(self):
        """Handle Expression Sheet Generator selection."""
        self._result = "expression_sheets"
        self._unbind_scroll()
        self.root.quit()

    def _select_sprite_tester(self):
        """Handle Sprite Tester selection."""
        self._result = "sprite_tester"
        self._unbind_scroll()
        self.root.quit()

    def _select_add_to_existing(self):
        """Handle Add to Character selection."""
        self._result = "add_to_existing"
        self._unbind_scroll()
        self.root.quit()

    def _select_gemini_workshop(self):
        """Handle Gemini Workshop selection."""
        self._result = "gemini_workshop"
        self._unbind_scroll()
        self.root.quit()

    def _select_sprite_importer(self):
        """Handle Game Sprite Importer selection."""
        self._result = "sprite_importer"
        self._unbind_scroll()
        self.root.quit()

    def _open_api_settings(self):
        """Open the API settings dialog to view/change the Gemini API key."""
        existing_key = get_existing_api_key() or ""
        # Show the API setup dialog as modal (passes parent so it works properly)
        show_api_setup(existing_key, parent=self.root)

    def _open_usage_dashboard(self):
        """Open the Google AI Studio usage dashboard in the default browser."""
        webbrowser.open(AI_STUDIO_USAGE_URL)

    def _open_readme(self):
        """Open the welcome/README window."""
        show_welcome(parent=self.root)

    def _open_sprite_database(self):
        """Open the SpriteBot database in the default browser."""
        webbrowser.open("http://thesaint.servebeer.com:8085/")

    def _clear_backups(self):
        """Clear all saved full-size character backups."""
        if not BACKUPS_BASE_DIR.exists() or not any(BACKUPS_BASE_DIR.iterdir()):
            messagebox.showinfo(
                "No Backups",
                "No backup files found. Nothing to clear.",
                parent=self.root,
            )
            return

        # Calculate current size
        total_size = sum(
            f.stat().st_size for f in BACKUPS_BASE_DIR.rglob("*") if f.is_file()
        )
        size_mb = total_size / (1024 * 1024)

        confirm = messagebox.askyesno(
            "Clear Backups",
            f"WARNING: This will delete all saved full-size character backups "
            f"({size_mb:.1f} MB).\n\n"
            f"If you add new content to characters created by this app, the results "
            f"will be of slightly lower quality without these backups.\n\n"
            f"The app will still work - it will use existing character images instead, "
            f"but the best quality comes from having these full-size backups.\n\n"
            f"This cannot be undone. Continue?",
            parent=self.root,
        )

        if confirm:
            try:
                shutil.rmtree(BACKUPS_BASE_DIR)
                BACKUPS_BASE_DIR.mkdir(parents=True, exist_ok=True)
                messagebox.showinfo(
                    "Backups Cleared",
                    f"Freed {size_mb:.1f} MB of backup data.",
                    parent=self.root,
                )
            except Exception as e:
                messagebox.showerror(
                    "Error",
                    f"Failed to clear backups:\n{e}",
                    parent=self.root,
                )

    def _open_upload_settings(self):
        """Open a dialog to view/change the upload username."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Upload Settings")
        dialog.configure(bg=BG_COLOR)
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.transient(self.root)

        # Center on parent
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 160
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 60
        dialog.geometry(f"320x150+{x}+{y}")

        tk.Label(
            dialog, text="Upload Display Name:",
            bg=BG_COLOR, fg=TEXT_COLOR, font=BODY_FONT,
        ).pack(pady=(16, 8), padx=16, anchor="w")

        entry = tk.Entry(dialog, font=BODY_FONT, width=32)
        entry.pack(padx=16)

        current = load_upload_username()
        if current:
            entry.insert(0, current)
        entry.focus_set()

        def on_save():
            val = entry.get().strip()
            if val:
                save_upload_username(val)
                messagebox.showinfo("Saved", f"Upload name set to \"{val}\".", parent=dialog)
                dialog.destroy()
            else:
                messagebox.showwarning("Empty Name", "Please enter a display name.", parent=dialog)

        btn_row = tk.Frame(dialog, bg=BG_COLOR)
        btn_row.pack(pady=(12, 0))
        create_primary_button(btn_row, "Save", on_save, width=8).pack(side="left", padx=4)
        create_secondary_button(btn_row, "Cancel", dialog.destroy, width=8).pack(side="left", padx=4)
        entry.bind("<Return>", lambda e: on_save())

    def _open_sprite_settings(self):
        """Open a dialog to configure sprite generation settings."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Sprite Settings")
        dialog.configure(bg=BG_COLOR)
        dialog.resizable(False, False)
        dialog.grab_set()
        dialog.transient(self.root)

        # Center on parent
        dialog.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 180
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 80
        dialog.geometry(f"360x180+{x}+{y}")

        tk.Label(
            dialog, text="Background Color:",
            bg=BG_COLOR, fg=TEXT_COLOR, font=BODY_FONT,
        ).pack(pady=(16, 4), padx=16, anchor="w")

        tk.Label(
            dialog,
            text="Color used behind the character in generated images.\n"
                 "Non-black colors may work better for dark-haired characters.",
            bg=BG_COLOR, fg=TEXT_SECONDARY, font=SMALL_FONT,
            justify="left",
        ).pack(padx=16, anchor="w")

        color_var = tk.StringVar(value=load_background_color())
        color_menu = tk.OptionMenu(dialog, color_var, *BACKGROUND_COLOR_PRESETS)
        color_menu.configure(width=28, bg="#1E1E1E", fg=TEXT_COLOR)
        color_menu.pack(padx=16, pady=(8, 0))

        def on_save():
            save_background_color(color_var.get())
            messagebox.showinfo(
                "Saved",
                f"Background color set to: {color_var.get()}",
                parent=dialog,
            )
            dialog.destroy()

        btn_row = tk.Frame(dialog, bg=BG_COLOR)
        btn_row.pack(pady=(12, 0))
        create_primary_button(btn_row, "Save", on_save, width=8).pack(side="left", padx=4)
        create_secondary_button(btn_row, "Cancel", dialog.destroy, width=8).pack(side="left", padx=4)

    def _update_scrollbar(self) -> None:
        """Show scrollbars only when content overflows."""
        try:
            canvas_w = self._scroll_canvas.winfo_width()
            canvas_h = self._scroll_canvas.winfo_height()
            content_w = self._main_frame.winfo_reqwidth()
            content_h = self._main_frame.winfo_reqheight()
        except tk.TclError:
            return
        if content_h > canvas_h + 2:
            self._v_scrollbar.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")
        else:
            self._v_scrollbar.place_forget()
        if content_w > canvas_w + 2:
            self._h_scrollbar.place(relx=0, rely=1.0, relwidth=1.0, anchor="sw")
        else:
            self._h_scrollbar.place_forget()

    def _on_close(self):
        """Handle window close."""
        self._result = None
        self._unbind_scroll()
        self.root.quit()

    def run(self) -> Optional[str]:
        """
        Run the launcher and return the selected tool.

        Returns:
            "sprite_creator", "expression_sheets", "sprite_tester", "add_to_existing", "gemini_workshop", "sprite_importer", or None if closed
        """
        self.root.mainloop()
        result = self._result
        self.root.destroy()
        return result


def select_character_folder(title: str = "Select Character Folder") -> Optional[Path]:
    """
    Show a folder selection dialog for choosing a character folder.

    Args:
        title: Dialog title

    Returns:
        Path to selected folder, or None if cancelled
    """
    root = tk.Tk()
    root.withdraw()

    folder = filedialog.askdirectory(title=title)

    root.destroy()

    if folder:
        return Path(folder)
    return None


def run_launcher() -> Optional[str]:
    """
    Create and run the launcher window.

    Returns:
        Selected tool name or None if closed
    """
    # Placeholder callbacks - the actual implementation will wire these up
    def on_sprite_creator():
        pass

    def on_expression_sheets():
        pass

    def on_sprite_tester():
        pass

    launcher = LauncherWindow(on_sprite_creator, on_expression_sheets, on_sprite_tester)
    return launcher.run()


# For testing
if __name__ == "__main__":
    result = run_launcher()
    print(f"Selected: {result}")
