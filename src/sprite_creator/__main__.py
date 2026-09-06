#!/usr/bin/env python3
"""
AI Sprite Creator - Main Entry Point

Run with: python -m sprite_creator

Provides a graphical launcher for:
1. Character Sprite Creator - Full AI-powered sprite generation pipeline
2. Expression Sheet Generator - Create sheets from existing character folders
3. Sprite Tester - Preview sprites in Ren'Py environment
"""

import io
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

# Add project root to sys.path so 'tools/' is importable
if getattr(sys, 'frozen', False):
    _root = Path(sys._MEIPASS)
else:
    _root = Path(__file__).resolve().parent.parent.parent  # src/sprite_creator/__main__.py -> repo root
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Force UTF-8 stdout/stderr on Windows so print() doesn't crash on non-ASCII
# characters (e.g. \u202f narrow no-break space from macOS filenames or
# unicode in Gemini API responses).
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    elif sys.stdout is None or not hasattr(sys.stdout, "write"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer if sys.stdout else open("NUL", "w"), encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    elif sys.stderr is None or not hasattr(sys.stderr, "write"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer if sys.stderr else open("NUL", "w"), encoding="utf-8", errors="replace")

from .logging_utils import setup_logging, log_info, log_error, log_exception


def main():
    """Main entry point for the standalone launcher."""
    from .ui.disclaimer import show_disclaimer_if_needed
    from .ui.launcher import LauncherWindow
    from .ui.api_setup import ensure_api_key
    from .config import APP_NAME, APP_VERSION

    # Initialize logging first thing
    setup_logging()

    print(f"\n{'=' * 60}")
    print(f"  {APP_NAME} v{APP_VERSION}")
    print(f"{'=' * 60}\n")

    # Step 1: Show disclaimer on first run
    if not show_disclaimer_if_needed():
        print("[INFO] User declined terms. Exiting.")
        return

    # Step 1.5: Show welcome/README on first launch
    from .ui.welcome import show_welcome_if_needed
    show_welcome_if_needed()

    # Step 2: Show launcher to select tool
    def on_sprite_creator():
        pass  # Callback not used, we check result

    def on_expression_sheets():
        pass

    def on_sprite_tester():
        pass

    def on_add_to_existing():
        pass

    def on_gemini_workshop():
        pass

    launcher = LauncherWindow(on_sprite_creator, on_expression_sheets, on_sprite_tester, on_add_to_existing, on_gemini_workshop)
    selected_tool = launcher.run()

    if not selected_tool:
        log_info("LAUNCHER: No tool selected, exiting")
        print("[INFO] No tool selected. Exiting.")
        return

    log_info(f"LAUNCHER: Selected mode: {selected_tool}")
    print(f"[INFO] Selected tool: {selected_tool}")

    # Handle each tool
    if selected_tool == "sprite_creator":
        run_sprite_creator()
    elif selected_tool == "expression_sheets":
        run_expression_sheets()
    elif selected_tool == "sprite_tester":
        run_sprite_tester()
    elif selected_tool == "add_to_existing":
        run_add_to_existing()
    elif selected_tool == "gemini_workshop":
        run_gemini_workshop()
    elif selected_tool == "sprite_importer":
        run_sprite_importer_tool()


def run_sprite_creator():
    """Run the full character sprite creator wizard."""
    from .ui.api_setup import ensure_api_key
    from .ui.full_wizard import run_full_wizard

    print("\n[INFO] Starting Character Sprite Creator...")

    # Ensure API key is configured
    api_key = ensure_api_key()
    if not api_key:
        print("[INFO] API key setup cancelled. Returning to launcher.")
        # Re-run main to show launcher again
        main()
        return

    # Ask for output folder
    root = tk.Tk()
    root.withdraw()
    output_folder = filedialog.askdirectory(
        title="Choose output folder for character sprites"
    )
    root.destroy()

    if not output_folder:
        print("[INFO] No output folder selected. Returning to launcher.")
        main()
        return

    output_path = Path(output_folder)
    print(f"[INFO] Output folder: {output_path}")

    # Run the unified wizard
    try:
        result = run_full_wizard(output_root=output_path, api_key=api_key)
        if result:
            log_info(f"Character sprite creation complete: {result.display_name}")
            print("\n[INFO] Character sprite creation complete!")
            print(f"[INFO] Character created: {result.display_name}")
            if result.character_folder:
                log_info(f"Output folder: {result.character_folder}")
                print(f"[INFO] Output folder: {result.character_folder}")
        else:
            log_info("Wizard cancelled by user")
            print("[INFO] Wizard cancelled by user.")
    except SystemExit:
        log_info("Wizard exited via SystemExit")
        print("[INFO] Wizard exited.")
    except Exception as e:
        log_exception(f"Error in sprite creator wizard: {e}")
        messagebox.showerror("Error", f"An error occurred:\n{e}")
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()


def run_expression_sheets():
    """Run the expression sheet generator for existing characters."""
    from tools.expression_sheets import main as run_sheet_generator
    from tools.expression_sheets import get_all_pose_paths

    print("\n[INFO] Starting Expression Sheet Generator...")

    # Ask for folder containing character(s)
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(
        title="Select folder containing character(s)"
    )
    root.destroy()

    if not folder:
        print("[INFO] No folder selected. Returning to launcher.")
        main()
        return

    folder_path = Path(folder)

    # Check if valid character folder structure
    pose_paths = get_all_pose_paths(str(folder_path))
    if not pose_paths:
        messagebox.showerror(
            "Invalid Folder",
            "No valid character structure found.\n\n"
            "Expected structure:\n"
            "  <character>/<pose>/faces/face/*.png\n\n"
            "Example:\n"
            "  John/a/faces/face/0.png"
        )
        main()
        return

    # Run the generator
    try:
        log_info(f"Running expression sheet generator on: {folder_path}")
        # Temporarily override sys.argv for the expression sheet main()
        original_argv = sys.argv
        sys.argv = ["expression_sheets", str(folder_path)]
        run_sheet_generator()
        sys.argv = original_argv
        log_info("Expression sheets generated successfully")
        print("\n[INFO] Expression sheets generated successfully!")
        messagebox.showinfo("Complete", "Expression sheets generated successfully!")
    except Exception as e:
        log_exception(f"Error in expression sheet generator: {e}")
        messagebox.showerror("Error", f"An error occurred:\n{e}")
        print(f"[ERROR] {e}")

    # Return to launcher
    main()


def run_sprite_tester():
    """Run the sprite tester for existing characters."""
    print("\n[INFO] Starting Sprite Tester...")

    # Ask for character folder
    root = tk.Tk()
    root.withdraw()
    folder = filedialog.askdirectory(
        title="Select character folder to test"
    )
    root.destroy()

    if not folder:
        print("[INFO] No folder selected. Returning to launcher.")
        main()
        return

    folder_path = Path(folder)

    # Check if valid character folder (has character.yml)
    char_yml = folder_path / "character.yml"
    if not char_yml.exists():
        messagebox.showerror(
            "Invalid Character Folder",
            "No character.yml found in selected folder.\n\n"
            "Please select a valid character folder created by\n"
            "the Character Sprite Creator."
        )
        main()
        return

    # Run the tester
    try:
        log_info(f"Running sprite tester on: {folder_path}")
        from tools.tester import launch_sprite_tester
        launch_sprite_tester(folder_path)
        log_info("Sprite tester finished")
        print("\n[INFO] Sprite tester finished.")
    except ImportError as e:
        log_error(f"Sprite tester module not available: {e}")
        messagebox.showerror(
            "Tester Not Available",
            "The sprite tester module is not available.\n"
            "Make sure all dependencies are installed."
        )
    except Exception as e:
        log_exception(f"Error in sprite tester: {e}")
        messagebox.showerror("Error", f"An error occurred:\n{e}")
        print(f"[ERROR] {e}")

    # Return to launcher
    main()


def run_add_to_existing():
    """Run the 'Add to Existing Character' wizard."""
    import yaml
    from .ui.api_setup import ensure_api_key
    from .ui.full_wizard import run_add_to_existing_wizard

    print("\n[INFO] Starting Add to Existing Character...")

    # Ensure API key is configured
    api_key = ensure_api_key()
    if not api_key:
        print("[INFO] API key setup cancelled. Returning to launcher.")
        main()
        return

    # Explain what folder to select before opening the dialog
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "Select Character Folder",
        "Select the CHARACTER FOLDER that contains the character you want to add to.\n\n"
        "This should be a folder with character.yml inside it.",
        parent=root,
    )
    folder = filedialog.askdirectory(
        title="Select existing character folder to add content to"
    )
    root.destroy()

    if not folder:
        print("[INFO] No folder selected. Returning to launcher.")
        main()
        return

    folder_path = Path(folder)

    # Validate: must have character.yml
    char_yml_path = folder_path / "character.yml"
    if not char_yml_path.exists():
        messagebox.showerror(
            "Invalid Character Folder",
            "No character.yml found in selected folder.\n\n"
            "Please select a valid character folder."
        )
        main()
        return

    # Parse character.yml
    try:
        with open(char_yml_path, 'r', encoding='utf-8') as f:
            char_data = yaml.safe_load(f) or {}
    except Exception as e:
        messagebox.showerror(
            "Error Reading Character",
            f"Failed to read character.yml:\n{e}"
        )
        main()
        return

    # Validate: must have at least one pose directory with expressions
    pose_dirs = []
    for letter in "abcdefghijklmnopqrstuvwxyz":
        pose_dir = folder_path / letter
        if pose_dir.is_dir():
            # Check for faces/face/ with at least one image
            face_dir = pose_dir / "faces" / "face"
            if face_dir.is_dir():
                expr_files = list(face_dir.glob("*.png")) + list(face_dir.glob("*.webp"))
                if expr_files:
                    pose_dirs.append(letter)

    if not pose_dirs:
        messagebox.showerror(
            "Invalid Character Structure",
            "No valid poses found in character folder.\n\n"
            "Expected structure:\n"
            "  <character>/a/faces/face/*.png\n"
            "  <character>/b/faces/face/*.png\n"
            "  etc."
        )
        main()
        return

    # Determine next available pose letter
    last_pose = max(pose_dirs)
    next_letter = chr(ord(last_pose) + 1)
    if next_letter > 'z':
        messagebox.showerror(
            "Cannot Add More Outfits",
            "This character already has poses a-z.\n"
            "Cannot add more outfits (letter limit reached)."
        )
        main()
        return

    # Extract key data from character.yml
    sprite_creator_poses = char_data.get('sprite_creator_poses', [])
    display_name = char_data.get('display_name', folder_path.name)
    existing_voice = char_data.get('voice', 'girl')
    existing_scale = char_data.get('scale', 1.0)
    existing_eye_line = char_data.get('eye_line', 0.0)
    existing_name_color = char_data.get('name_color', '#ffffff')
    existing_archetype = char_data.get('archetype', '')  # Empty if not created by this app
    existing_hair_length = char_data.get('hair_length', '')  # Hair length for female archetypes

    print(f"[INFO] Character: {display_name}")
    print(f"[INFO] Existing poses: {', '.join(pose_dirs)}")
    print(f"[INFO] Next pose letter: {next_letter}")
    print(f"[INFO] Sprite Creator poses: {sprite_creator_poses}")

    # Run the add-to-existing wizard
    try:
        result = run_add_to_existing_wizard(
            existing_folder=folder_path,
            api_key=api_key,
            char_data=char_data,
            existing_poses=pose_dirs,
            next_pose_letter=next_letter,
            sprite_creator_poses=sprite_creator_poses,
            display_name=display_name,
            existing_voice=existing_voice,
            existing_scale=existing_scale,
            existing_eye_line=existing_eye_line,
            existing_name_color=existing_name_color,
            existing_archetype=existing_archetype,
            existing_hair_length=existing_hair_length,
        )
        if result:
            log_info(f"Add to existing complete: {display_name}")
            print("\n[INFO] Add to existing character complete!")
            print(f"[INFO] Character: {display_name}")
        else:
            log_info("Add to existing wizard cancelled by user")
            print("[INFO] Wizard cancelled by user.")
    except SystemExit:
        log_info("Add to existing wizard exited via SystemExit")
        print("[INFO] Wizard exited.")
    except Exception as e:
        log_exception(f"Error in add to existing wizard: {e}")
        messagebox.showerror("Error", f"An error occurred:\n{e}")
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()

    # Return to launcher
    main()


def run_gemini_workshop():
    """Run the Gemini Workshop standalone tool."""
    from .ui.api_setup import ensure_api_key
    from .ui.gemini_workshop import GeminiWorkshop

    print("\n[INFO] Starting Gemini Workshop...")

    # Ensure API key is configured
    api_key = ensure_api_key()
    if not api_key:
        print("[INFO] API key setup cancelled. Returning to launcher.")
        main()
        return

    try:
        log_info("Starting Gemini Workshop")
        workshop = GeminiWorkshop(api_key)
        workshop.run()
        log_info("Gemini Workshop closed")
    except Exception as e:
        log_exception(f"Error in Gemini Workshop: {e}")
        messagebox.showerror("Error", f"An error occurred:\n{e}")
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()

    # Return to launcher
    main()


def run_sprite_importer_tool():
    """Run the Game Sprite Importer flow (no API key needed)."""
    from .flows.importer import run_sprite_importer

    print("\n[INFO] Starting Game Sprite Importer...")
    try:
        log_info("Starting Game Sprite Importer")
        run_sprite_importer()
        log_info("Game Sprite Importer closed")
    except Exception as e:
        log_exception(f"Error in Game Sprite Importer: {e}")
        messagebox.showerror("Error", f"An error occurred:\n{e}")
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()

    # Bury dead Tk objects NOW, on the main thread. If they linger as
    # garbage, the cyclic GC may collect them later inside a worker
    # thread — Tcl is single-threaded and aborts the process (SIGILL,
    # Tcl_AsyncDelete panic).
    import gc
    gc.collect()

    # Return to launcher
    main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_info("Interrupted by user.")
        print("\n[INFO] Interrupted by user.")
        sys.exit(0)
    except Exception as e:
        log_exception(f"Unhandled exception in main: {e}")
        print(f"\n[ERROR] Unhandled exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
