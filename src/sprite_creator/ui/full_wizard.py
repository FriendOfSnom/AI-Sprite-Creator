"""
Full Wizard Controller for ST Sprite Creator.

Provides a unified wizard interface that handles the entire sprite creation
process from source selection to final output. All screens are wizard steps
with back/forward navigation.

Usage:
    wizard = FullWizard(output_root=Path("/path/to/output"), api_key="...")
    result = wizard.run()
    if result:
        print(f"Character created at: {result.character_folder}")
"""

import queue
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Callable, Dict, List, Optional, Type

from ..config import (
    BG_COLOR,
    BG_SECONDARY,
    CARD_BG,
    TEXT_COLOR,
    TEXT_SECONDARY,
    ACCENT_COLOR,
    TITLE_FONT,
    PAGE_TITLE_FONT,
    SECTION_FONT,
    BODY_FONT,
    SMALL_FONT,
    BUTTON_FONT,
)
from .tk_common import (
    apply_dark_theme,
    apply_window_size,
    get_primary_screen,
    create_primary_button,
    create_secondary_button,
    create_help_button,
    show_help_modal,
)
from .screens.base import WizardStep, WizardState
from ..logging_utils import log_info, log_warning


class FullWizard:
    """
    Main wizard controller that orchestrates all steps of sprite creation.

    The wizard maintains a list of steps and handles navigation between them.
    Each step is a WizardStep subclass that handles one phase of the process.
    """

    def __init__(
        self,
        output_root: Optional[Path] = None,
        api_key: Optional[str] = None,
        *,
        state=None,
        title: str = "ST Sprite Creator",
        loading_subtext: str = "Please wait while the AI generates content.",
        fixed_size: bool = False,
    ):
        """
        Initialize the wizard.

        Args:
            output_root: Root folder for character output. If None, will be
                prompted during wizard.
            api_key: Gemini API key. If None, will be obtained via ensure_api_key.
            state: Optional pre-built state object for flows that use their
                own state dataclass (steps duck-type state access). Defaults
                to a fresh WizardState.
            title: Window and header title.
            loading_subtext: Secondary line shown on the loading overlay.
        """
        self.root = tk.Tk()
        self.root.title(title)
        self.root.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._title = title
        self._loading_subtext = loading_subtext

        apply_dark_theme(self.root)

        # Fixed-size mode: one logical design at 1600x900, scaled by a single
        # factor S = monitor_height / 1080 (floored at 1, capped at 2). The
        # window, fonts, and (via ui_scale) the thumbnails all scale by S, so
        # the layout is identical on every monitor and never needs live-resize
        # handling. ui_scale is read by steps to size images.
        self.ui_scale: float = 1.0
        if fixed_size:
            self._apply_fixed_scaling()
        else:
            apply_window_size(self.root, "fullscreen")
            # Floor the window so controls never collapse off-screen; below
            # this the content area's scrollbars take over (drag-only).
            self.root.minsize(900, 640)

        # State
        if state is not None:
            self._state = state
        else:
            self._state = WizardState()
            self._state.output_root = output_root
            self._state.api_key = api_key

        # Steps
        self._steps: List[WizardStep] = []
        self._step_classes: List[Type[WizardStep]] = []
        self._current_step_index: int = 0

        # UI references
        self._step_labels: List[tk.Label] = []
        self._content_frame: Optional[tk.Frame] = None
        self._back_btn: Optional[tk.Button] = None
        self._next_btn: Optional[tk.Button] = None
        self._help_btn: Optional[tk.Button] = None
        self._loading_frame: Optional[tk.Frame] = None
        self._loading_label: Optional[tk.Label] = None

        # Result tracking
        self._cancelled = False
        self._completed = False

        # Thread-safe callback queue: background threads put callbacks here
        # instead of calling root.after() directly (which is not thread-safe
        # and can silently fail on some Windows systems).
        self._callback_queue: queue.Queue = queue.Queue()

        self._build_ui()

    # Logical design size (at scale 1.0, i.e. 1080p and below).
    DESIGN_W = 1600
    DESIGN_H = 900

    def _apply_fixed_scaling(self) -> None:
        """Non-resizable window at DESIGN_W x DESIGN_H * S, centered, with
        fonts scaled by S. Called before _build_ui so widgets pick up the
        scaled fonts."""
        self.root.update_idletasks()
        sw, sh, ox, oy = get_primary_screen(self.root)
        # Window is always about 83% of screen height (900/1080), so it never
        # fills the screen; S is that same ratio, floored at 1 and capped at 2.
        scale = max(1.0, min(2.0, sh / 1080.0))
        self.ui_scale = scale

        # Scale all point-based fonts (and thus text-driven widget sizes).
        try:
            base = float(self.root.tk.call("tk", "scaling"))
            self.root.tk.call("tk", "scaling", base * scale)
        except Exception:
            pass

        w = int(self.DESIGN_W * scale)
        h = int(self.DESIGN_H * scale)
        x = ox + (sw - w) // 2
        y = oy + max(0, (sh - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.resizable(False, False)

    @property
    def state(self) -> WizardState:
        """Get the wizard state."""
        return self._state

    @property
    def current_step(self) -> Optional[WizardStep]:
        """Get the current active step."""
        if 0 <= self._current_step_index < len(self._steps):
            return self._steps[self._current_step_index]
        return None

    def register_step(self, step_class: Type[WizardStep]) -> None:
        """
        Register a step class to be instantiated when wizard builds.

        Steps are shown in the order they are registered.

        Args:
            step_class: WizardStep subclass to register.
        """
        self._step_classes.append(step_class)

    def _build_ui(self) -> None:
        """Build the main wizard UI structure."""
        self._main_frame = tk.Frame(self.root, bg=BG_COLOR)
        self._main_frame.pack(fill="both", expand=True)

        # Header with step indicator
        self._build_header()

        # Content area (step frames go here) — wrapped in scrollable canvas
        # for small-screen support. Scrollbars are drag-only (no mousewheel)
        # to avoid conflicts with scroll-wheel usage inside individual steps.
        self._scroll_outer = tk.Frame(self._main_frame, bg=BG_COLOR)
        self._scroll_outer.pack(fill="both", expand=True)

        self._scroll_canvas = tk.Canvas(
            self._scroll_outer, bg=BG_COLOR, highlightthickness=0
        )
        self._scroll_canvas.pack(fill="both", expand=True)

        # The actual content frame lives inside the canvas
        self._content_frame = tk.Frame(self._scroll_canvas, bg=BG_COLOR, padx=30, pady=20)
        self._canvas_window = self._scroll_canvas.create_window(
            (0, 0), window=self._content_frame, anchor="nw"
        )

        # Thin scrollbars overlaid via place() — drag-only, auto-show/hide
        self._v_scrollbar = tk.Scrollbar(
            self._scroll_outer, orient="vertical",
            command=self._scroll_canvas.yview, width=10
        )
        self._h_scrollbar = tk.Scrollbar(
            self._scroll_outer, orient="horizontal",
            command=self._scroll_canvas.xview, width=10
        )
        self._scroll_canvas.configure(
            xscrollcommand=self._h_scrollbar.set,
            yscrollcommand=self._v_scrollbar.set,
        )

        # Track content size changes to update scroll region & auto-show bars
        self._content_frame.bind("<Configure>", self._on_content_configure)
        self._scroll_canvas.bind("<Configure>", self._on_canvas_configure)

        # Loading overlay (hidden by default)
        self._build_loading_overlay()

        # Footer with navigation buttons
        self._build_footer()

    def _build_header(self) -> None:
        """Build the header with step indicator."""
        header = tk.Frame(self._main_frame, bg=BG_SECONDARY, padx=30, pady=10)
        header.pack(fill="x")

        # Title
        tk.Label(
            header,
            text=self._title,
            bg=BG_SECONDARY,
            fg=TEXT_COLOR,
            font=TITLE_FONT,
        ).pack(side="left")

        # Step indicator (built when steps are registered)
        self._indicator_frame = tk.Frame(header, bg=BG_SECONDARY)
        self._indicator_frame.pack(side="right")

    def _build_step_indicator(self) -> None:
        """Build the step indicator based on registered steps."""
        # Clear existing
        for widget in self._indicator_frame.winfo_children():
            widget.destroy()
        self._step_labels.clear()

        for i, step in enumerate(self._steps):
            # Step label
            step_frame = tk.Frame(self._indicator_frame, bg=BG_SECONDARY)
            step_frame.pack(side="left")

            label = tk.Label(
                step_frame,
                text=f"({step.STEP_NUMBER}) {step.STEP_TITLE}",
                bg=BG_SECONDARY,
                fg=TEXT_SECONDARY,
                font=SMALL_FONT,
            )
            label.pack()
            self._step_labels.append(label)

            # Connector (except after last)
            if i < len(self._steps) - 1:
                connector = tk.Label(
                    self._indicator_frame,
                    text="  ─  ",
                    bg=BG_SECONDARY,
                    fg=TEXT_SECONDARY,
                    font=SMALL_FONT,
                )
                connector.pack(side="left")

    def _build_loading_overlay(self) -> None:
        """Build the loading overlay (hidden by default)."""
        self._loading_frame = tk.Frame(self._main_frame, bg=BG_COLOR)

        # Center the loading indicator
        inner = tk.Frame(self._loading_frame, bg=CARD_BG, padx=60, pady=40)
        inner.place(relx=0.5, rely=0.5, anchor="center")

        self._loading_label = tk.Label(
            inner,
            text="Loading...",
            bg=CARD_BG,
            fg=TEXT_COLOR,
            font=SECTION_FONT,
        )
        self._loading_label.pack()

        # Animated working indicator
        self._loading_dots_label = tk.Label(
            inner,
            text="●",
            bg=CARD_BG,
            fg=TEXT_SECONDARY,
            font=SECTION_FONT,
        )
        self._loading_dots_label.pack(pady=(6, 0))
        self._loading_dots_count = 1

        tk.Label(
            inner,
            text=self._loading_subtext,
            bg=CARD_BG,
            fg=TEXT_SECONDARY,
            font=SMALL_FONT,
            wraplength=420,
            justify="center",
        ).pack(pady=(10, 0))

    def _animate_loading_dots(self) -> None:
        """Cycle the working indicator while the overlay is visible."""
        if self._loading_frame and self._loading_frame.winfo_ismapped():
            self._loading_dots_count = self._loading_dots_count % 3 + 1
            self._loading_dots_label.configure(
                text="● " * self._loading_dots_count)
            self.root.after(400, self._animate_loading_dots)

    def _build_footer(self) -> None:
        """Build the footer with navigation buttons."""
        footer = tk.Frame(self._main_frame, bg=BG_COLOR, padx=30, pady=12)
        footer.pack(fill="x", side="bottom")

        # Left-center-right positioning. uniform= forces all three columns to
        # EQUAL width (weight alone only splits leftover space, so unequal
        # content widths — narrow "Cancel" vs "Back"+"Next" — would push the
        # centered Help button off true center).
        footer.columnconfigure(0, weight=1, uniform="footer")  # Left
        footer.columnconfigure(1, weight=1, uniform="footer")  # Center
        footer.columnconfigure(2, weight=1, uniform="footer")  # Right

        # Left side: Cancel button only
        left_frame = tk.Frame(footer, bg=BG_COLOR)
        left_frame.grid(row=0, column=0, sticky="w")

        self._cancel_btn = create_secondary_button(
            left_frame, "Cancel", self._on_cancel, width=10
        )
        self._cancel_btn.pack(side="left")

        # Center: Help button
        center_frame = tk.Frame(footer, bg=BG_COLOR)
        center_frame.grid(row=0, column=1)

        self._help_btn = create_help_button(center_frame, "?", "")
        self._help_btn.pack()

        # Right side: Navigation buttons - Next rightmost, Back to its left
        nav_frame = tk.Frame(footer, bg=BG_COLOR)
        nav_frame.grid(row=0, column=2, sticky="e")

        # Back/Next are a matched pair: same size (same large flag + width)
        # so the bottom-right corner reads as one consistent control group.
        self._next_btn = create_primary_button(
            nav_frame, "Next", self.go_next, width=11, large=True
        )
        self._next_btn.pack(side="right")

        self._back_btn = create_secondary_button(
            nav_frame, "Back", self.go_back, width=11, large=True
        )
        self._back_btn.pack(side="right", padx=(0, 12))

    # --- Scrollable content area helpers ---

    def _on_content_configure(self, event=None) -> None:
        """Update scroll region and canvas window size when content changes.

        Also updates the canvas window height so the wizard scrollbar detects
        overflow when step content (e.g. outfit cards) is taller than the viewport.
        """
        self._scroll_canvas.configure(scrollregion=self._scroll_canvas.bbox("all"))
        # Update canvas window to accommodate content that grew after initial layout
        canvas_h = self._scroll_canvas.winfo_height()
        content_h = self._content_frame.winfo_reqheight()
        canvas_w = self._scroll_canvas.winfo_width()
        # Width tracks the viewport so card grids REFLOW their columns to fit
        # (fixed card size, fluid layout). Using max(canvas_w, content_w) here
        # would pin the width to the grid's own laid-out size and stop it from
        # ever wrapping to fewer columns. Height still grows so vertical scroll
        # remains the "window too short" fallback.
        self._scroll_canvas.itemconfigure(
            self._canvas_window,
            width=canvas_w,
            height=max(canvas_h, content_h),
        )
        self._update_scrollbars()

    def _on_canvas_configure(self, event=None) -> None:
        """When the canvas resizes, stretch content to fill both dimensions.

        This is critical: without setting height, steps that use expand=True
        (like the sprite selector grid) won't get proper vertical space because
        the canvas window item only sizes to its content's requested height.
        """
        canvas_w = self._scroll_canvas.winfo_width()
        canvas_h = self._scroll_canvas.winfo_height()
        content_h = self._content_frame.winfo_reqheight()
        # Content width follows the viewport (see _on_content_configure) so
        # card grids reflow instead of forcing a horizontal scrollbar.
        self._scroll_canvas.itemconfigure(
            self._canvas_window,
            width=canvas_w,
            height=max(canvas_h, content_h),
        )
        self._update_scrollbars()

    def _update_scrollbars(self) -> None:
        """Show scrollbars only when content overflows the visible area."""
        try:
            canvas_w = self._scroll_canvas.winfo_width()
            canvas_h = self._scroll_canvas.winfo_height()
            content_w = self._content_frame.winfo_reqwidth()
            content_h = self._content_frame.winfo_reqheight()
        except tk.TclError:
            return  # Widget not ready yet

        # Vertical scrollbar — right edge overlay
        if content_h > canvas_h + 2:
            self._v_scrollbar.place(
                relx=1.0, rely=0, relheight=1.0, anchor="ne"
            )
        else:
            self._v_scrollbar.place_forget()

        # Horizontal scrollbar — bottom edge overlay
        if content_w > canvas_w + 2:
            self._h_scrollbar.place(
                relx=0, rely=1.0, relwidth=1.0, anchor="sw"
            )
        else:
            self._h_scrollbar.place_forget()

    def _scroll_content_to_top(self) -> None:
        """Reset scroll position to top-left when changing steps."""
        self._scroll_canvas.xview_moveto(0)
        self._scroll_canvas.yview_moveto(0)

    def _update_step_indicator(self) -> None:
        """Update step indicator to highlight current step."""
        for i, label in enumerate(self._step_labels):
            if i == self._current_step_index:
                label.configure(
                    fg=ACCENT_COLOR,
                    font=(SMALL_FONT[0], SMALL_FONT[1], "bold")
                )
            elif i < self._current_step_index:
                label.configure(fg=TEXT_COLOR, font=SMALL_FONT)
            else:
                label.configure(fg=TEXT_SECONDARY, font=SMALL_FONT)

    def _update_help_button(self) -> None:
        """Update help button for current step."""
        step = self.current_step
        if not step:
            return

        help_text = step.STEP_HELP

        # Substitute {bg_color} placeholder with configured background color name
        if "{bg_color}" in help_text:
            from ..config import load_background_color
            bg_color_name = load_background_color().split("(")[0].strip()
            help_text = help_text.replace("{bg_color}", bg_color_name)

        # Make help button prominent on all steps for better visibility
        use_prominent = True

        # Recreate help button with new text
        parent = self._help_btn.master
        self._help_btn.destroy()
        self._help_btn = create_help_button(
            parent,
            f"Help: {step.STEP_TITLE}",
            help_text,
            prominent=use_prominent
        )
        self._help_btn.pack(side="left")

    def _update_nav_buttons(self) -> None:
        """Update navigation button states and text."""
        # Back button: hidden on first step
        if self._current_step_index == 0:
            self._back_btn.pack_forget()
        else:
            self._back_btn.pack(side="left", padx=(0, 10))

        # Next button text
        if self._current_step_index == len(self._steps) - 1:
            self._next_btn.configure(text="Finish")
        else:
            self._next_btn.configure(text="Next")

    def _show_step(self, index: int) -> None:
        """
        Show the step at the given index.

        Handles hiding current step, showing new step, and updating UI.

        Args:
            index: Index of step to show (0-based).
        """
        if index < 0 or index >= len(self._steps):
            return

        # Leave current step
        try:
            if self.current_step:
                self.current_step.on_leave()
                if self.current_step.frame:
                    self.current_step.frame.pack_forget()
        except Exception as e:
            log_warning(f"Error leaving step: {e}")
            print(f"[WARN] Error leaving step: {e}")

        # Update index
        self._current_step_index = index

        # Show new step
        new_step = self._steps[index]

        # Skip if needed
        while new_step.should_skip():
            log_info(f"NAV: Skipping step {new_step.STEP_ID}")
            if self._current_step_index < len(self._steps) - 1:
                self._current_step_index += 1
                new_step = self._steps[self._current_step_index]
            else:
                break

        log_info(f"NAV: Entering step {new_step.STEP_ID} ({new_step.STEP_TITLE})")

        # Reset scroll position before showing new step
        self._scroll_content_to_top()

        if new_step.frame:
            new_step.frame.pack(fill="both", expand=True)

        # Re-enable Next button before entering the new step.
        # Steps that need it disabled (e.g. SetupStep waiting for crop accept)
        # will disable it themselves in on_enter().
        self._next_btn.configure(state="normal")

        try:
            new_step.on_enter()
        except Exception as e:
            log_warning(f"Error entering step {new_step.STEP_ID}: {e}")
            print(f"[ERROR] Error entering step {new_step.STEP_ID}: {e}")
            import traceback
            traceback.print_exc()
            messagebox.showerror(
                "Step Error",
                f"An error occurred entering step '{new_step.STEP_TITLE}':\n\n{e}\n\n"
                f"You can try going back and forward again."
            )

        # Update UI
        self._update_step_indicator()
        self._update_help_button()
        self._update_nav_buttons()

        # Deferred scroll region update: on_enter() may resize child canvases,
        # and the scroll region needs recalculating after layout settles.
        self.root.after(50, self._on_content_configure)

    def go_next(self) -> None:
        """Navigate to the next step if validation passes."""
        step = self.current_step
        if not step:
            return

        log_info(f"NAV: Next from {step.STEP_ID}")

        # Validate current step
        if not step.validate():
            return

        # Note: "Changes Detected" popup removed - warnings are now shown
        # only when user clicks a Regenerate button in the respective steps

        # Move to next step
        if self._current_step_index < len(self._steps) - 1:
            self._show_step(self._current_step_index + 1)
        else:
            # Last step - complete wizard
            self._completed = True
            self.root.quit()

    def go_back(self) -> None:
        """Navigate to the previous step."""
        if self._current_step_index > 0:
            log_info(f"NAV: Back from {self.current_step.STEP_ID if self.current_step else '?'}")
            # Leave current step
            if self.current_step:
                self.current_step.on_leave()

            # Find previous non-skipped step
            target = self._current_step_index - 1
            while target > 0 and self._steps[target].should_skip():
                target -= 1

            self._show_step(target)

    def go_to_step(self, index: int) -> None:
        """
        Navigate directly to a specific step.

        Args:
            index: Target step index (0-based).
        """
        if 0 <= index < len(self._steps):
            self._show_step(index)

    def show_loading(self, message: str = "Loading...") -> None:
        """
        Show loading overlay with message.

        Args:
            message: Text to display during loading.
        """
        if self._loading_label:
            self._loading_label.configure(text=message)
        if self._loading_frame:
            was_hidden = not self._loading_frame.winfo_ismapped()
            self._loading_frame.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._loading_frame.lift()
            if was_hidden:
                self.root.after(400, self._animate_loading_dots)
        self.root.update()

    def hide_loading(self) -> None:
        """Hide the loading overlay."""
        if self._loading_frame:
            self._loading_frame.place_forget()
        self.root.update()

    def schedule_callback(self, callback: Callable) -> None:
        """
        Schedule a callback to run on the main (UI) thread.

        Background threads must use this instead of root.after() to avoid
        tkinter thread-safety issues that can cause silent hangs on some
        Windows systems.

        Args:
            callback: Zero-argument callable to run on the main thread.
        """
        self._callback_queue.put(callback)

    def _process_callback_queue(self) -> None:
        """Process pending callbacks from background threads (runs on main thread)."""
        try:
            while True:
                callback = self._callback_queue.get_nowait()
                callback()
        except queue.Empty:
            pass
        # Re-schedule self every 100ms
        self.root.after(100, self._process_callback_queue)

    def _on_cancel(self) -> None:
        """Handle cancel button or window close."""
        # Force focus to root window to ensure messagebox appears properly
        # This helps when modal dialogs may have grab_set() active
        try:
            self.root.focus_force()
        except tk.TclError:
            pass  # Window may be in transition

        log_info("NAV: Cancel clicked")
        result = messagebox.askyesno(
            "Cancel",
            "Are you sure you want to cancel?\n\n"
            "Any unsaved progress will be lost.",
            icon="warning"
        )
        if result:
            log_info("NAV: Cancel confirmed")
            self._cancelled = True
            # Close any open Toplevel windows (modals) before quitting
            # This ensures no grabs are left active that could interfere with quit
            for widget in self.root.winfo_children():
                if isinstance(widget, tk.Toplevel):
                    try:
                        widget.grab_release()
                        widget.destroy()
                    except tk.TclError:
                        pass  # Widget may already be destroyed
            self.root.quit()

    def _initialize_steps(self) -> None:
        """Instantiate all registered step classes."""
        for step_class in self._step_classes:
            step = step_class(self, self._state)
            step.build(self._content_frame)
            self._steps.append(step)

        # Build step indicator now that we know the steps
        self._build_step_indicator()

    def run(self) -> Optional[WizardState]:
        """
        Run the wizard and return the final state.

        Returns:
            WizardState if completed successfully, None if cancelled.
        """
        # Initialize steps
        self._initialize_steps()

        if not self._steps:
            messagebox.showerror("Error", "No wizard steps registered.")
            self.root.destroy()
            return None

        # Show first step
        self._show_step(0)

        # Start processing thread-safe callback queue
        self._process_callback_queue()

        # Run main loop
        self.root.mainloop()

        # Cleanup
        if self._cancelled or not self._completed:
            self.root.destroy()
            return None

        result = self._state
        self.root.destroy()
        return result


def run_full_wizard(
    output_root: Optional[Path] = None,
    api_key: Optional[str] = None,
) -> Optional[WizardState]:
    """
    Run the full sprite creation wizard.

    Convenience function that creates a FullWizard with all steps registered.

    Args:
        output_root: Root folder for character output.
        api_key: Gemini API key.

    Returns:
        WizardState if completed successfully, None if cancelled.
    """
    # Import step classes here to avoid circular imports
    from .screens.setup_steps import (
        SourceStep, SetupStep, OptionsStep
    )
    from .screens.settings_step import SettingsStep
    from .screens.generation_steps import ReviewStep
    from .screens.outfit_steps import OutfitReviewStep
    from .screens.expression_steps import ExpressionReviewStep
    from .screens.finalization_steps import EyeLineStep, ScaleStep, SummaryStep

    wizard = FullWizard(output_root=output_root, api_key=api_key)

    # Register setup steps (1-3)
    wizard.register_step(SourceStep)      # Step 1: Choose image or prompt
    wizard.register_step(SettingsStep)    # Step 2: Voice, Name, Archetype (+ normalization for image mode)
    wizard.register_step(SetupStep)       # Step 3: Crop and modify
    wizard.register_step(OptionsStep)

    # Register generation step (unified for both image and prompt modes)
    wizard.register_step(ReviewStep)

    # Register outfit review (8)
    wizard.register_step(OutfitReviewStep)

    # Register expression review (10)
    wizard.register_step(ExpressionReviewStep)

    # Register finalization steps (11-13)
    wizard.register_step(EyeLineStep)
    wizard.register_step(ScaleStep)
    wizard.register_step(SummaryStep)

    return wizard.run()


def run_add_to_existing_wizard(
    existing_folder: Path,
    api_key: str,
    char_data: dict,
    existing_poses: list,
    next_pose_letter: str,
    sprite_creator_poses: list,
    display_name: str,
    existing_voice: str,
    existing_scale: float,
    existing_eye_line: float,
    existing_name_color: str,
    existing_archetype: str = "",
    existing_hair_length: str = "",
) -> Optional[WizardState]:
    """
    Run the add-to-existing wizard for adding content to an existing character.

    This wizard skips SourceStep (already have folder) and EyeLineStep (use existing).
    SettingsStep shows name as read-only, SetupStep shows sprite selector.

    Args:
        existing_folder: Path to existing character folder
        api_key: Gemini API key
        char_data: Parsed character.yml data
        existing_poses: List of existing pose letters
        next_pose_letter: Next available pose letter
        sprite_creator_poses: List of pose letters created by Sprite Creator (can add expressions to these)
        display_name: Character's display name
        existing_voice: Voice from character.yml (preserved at finalization)
        existing_scale: Scale from character.yml
        existing_eye_line: Eye line from character.yml
        existing_name_color: Name color from character.yml
        existing_archetype: Archetype from character.yml (empty if not created by this app)

    Returns:
        WizardState if completed successfully, None if cancelled.
    """
    # Import step classes here to avoid circular imports
    from .screens.setup_steps import (
        SourceStep, SetupStep, OptionsStep
    )
    from .screens.settings_step import SettingsStep
    from .screens.generation_steps import ReviewStep
    from .screens.outfit_steps import OutfitReviewStep
    from .screens.expression_steps import ExpressionReviewStep
    from .screens.finalization_steps import EyeLineStep, ScaleStep, SummaryStep

    wizard = FullWizard(output_root=existing_folder.parent, api_key=api_key)

    # Pre-configure state for add-to-existing mode
    wizard._state.is_adding_to_existing = True
    wizard._state.existing_character_folder = existing_folder
    wizard._state.existing_character_data = char_data
    wizard._state.existing_poses = existing_poses
    wizard._state.next_pose_letter = next_pose_letter
    wizard._state.sprite_creator_poses = sprite_creator_poses
    wizard._state.display_name = display_name
    wizard._state.existing_voice = existing_voice
    wizard._state.existing_scale = existing_scale
    wizard._state.existing_eye_line = existing_eye_line
    wizard._state.existing_name_color = existing_name_color
    wizard._state.backup_id = char_data.get('backup_id')

    # If character was created by this app (has archetype), pre-fill voice/archetype/hair
    # These will be shown as read-only in SettingsStep
    if existing_archetype:
        wizard._state.archetype_label = existing_archetype
        wizard._state.voice = existing_voice  # Pre-fill voice for AI prompts
        if existing_hair_length:
            wizard._state.hair_length = existing_hair_length

    # Create a TEMP working folder for new content - NOT the existing folder!
    # This prevents overwrites and allows proper merging at finalization.
    # The temp folder is created alongside the existing character folder.
    temp_name = f".{existing_folder.name}_new_content_temp"
    wizard._state.character_folder = existing_folder.parent / temp_name
    wizard._state.character_folder.mkdir(parents=True, exist_ok=True)

    # Register steps
    # Skip SourceStep - we already have the folder selected via launcher
    wizard.register_step(SettingsStep)    # Step 1: Voice, Archetype (name read-only)
    wizard.register_step(SetupStep)       # Step 2: Sprite selector (no crop)
    wizard.register_step(OptionsStep)     # Step 3: Outfits + existing outfit expressions

    # Generation steps (skip ReviewStep and OutfitReviewStep if no new outfits selected)
    wizard.register_step(ReviewStep)
    wizard.register_step(OutfitReviewStep)
    wizard.register_step(ExpressionReviewStep)

    # Skip EyeLineStep - use existing values
    # EyeLineStep would be registered here normally but we skip it

    wizard.register_step(ScaleStep)       # Scale to match existing character size
    wizard.register_step(SummaryStep)     # Merge mode finalization

    return wizard.run()


# For testing
if __name__ == "__main__":
    from .screens.setup_steps import (
        SourceStep, SetupStep, OptionsStep
    )
    from .screens.settings_step import SettingsStep
    from .screens.generation_steps import ReviewStep
    from .screens.outfit_steps import OutfitReviewStep
    from .screens.expression_steps import ExpressionReviewStep
    from .screens.finalization_steps import EyeLineStep, ScaleStep, SummaryStep

    # Create wizard with all implemented steps
    wizard = FullWizard()

    # Setup steps (1-3)
    wizard.register_step(SourceStep)      # Step 1: Choose image or prompt
    wizard.register_step(SettingsStep)    # Step 2: Voice, Name, Archetype
    wizard.register_step(SetupStep)       # Step 3: Crop and modify
    wizard.register_step(OptionsStep)

    # Generation step (unified for both modes)
    wizard.register_step(ReviewStep)

    # Outfit review (8)
    wizard.register_step(OutfitReviewStep)

    # Expression review (10)
    wizard.register_step(ExpressionReviewStep)

    # Finalization steps (11-13)
    wizard.register_step(EyeLineStep)
    wizard.register_step(ScaleStep)
    wizard.register_step(SummaryStep)

    result = wizard.run()
    if result:
        print("Wizard completed!")
        print(f"  Source mode: {result.source_mode}")
        print(f"  Name: {result.display_name}")
        print(f"  Voice: {result.voice}")
        print(f"  Archetype: {result.archetype_label}")
        print(f"  Outfits: {result.selected_outfits}")
        print(f"  Expressions: {len(result.expressions_sequence)}")
        print(f"  Eye line: {result.eye_line_ratio}")
        print(f"  Name color: {result.name_color}")
        print(f"  Scale: {result.scale_factor}")
        if result.character_folder:
            print(f"  Output: {result.character_folder}")
    else:
        print("Wizard cancelled.")
