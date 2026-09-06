#!/usr/bin/env python3
"""
constants.py

All global paths, constants, and static tables for the Gemini sprite pipeline.
"""

import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════════════════════
# APPLICATION INFO
# ═══════════════════════════════════════════════════════════════════════════════
APP_NAME = "ST Sprite Creator"
APP_VERSION = "1.5.0"


def get_resource_path(relative_path: str = "") -> Path:
    """
    Get the path to a resource, handling both development and frozen (PyInstaller) modes.

    In development: Returns path relative to the sprite_creator package directory.
    When frozen: Returns path relative to PyInstaller's _MEIPASS temp directory.

    Args:
        relative_path: Path relative to the base directory (e.g., "data/names.csv")

    Returns:
        Absolute Path to the resource
    """
    if getattr(sys, 'frozen', False):
        # Running as compiled .exe - PyInstaller extracts to _MEIPASS
        base_path = Path(sys._MEIPASS)
    else:
        # Running in development - use the package directory
        base_path = Path(__file__).resolve().parent

    if relative_path:
        return base_path / relative_path
    return base_path


# Base directory for this package (or frozen bundle)
SPRITE_CREATOR_DIR = get_resource_path()

# Data directory (contains CSVs and reference sprites)
DATA_DIR = get_resource_path("data")

# Paths for configuration and data files
CONFIG_PATH = Path.home() / ".st_gemini_config.json"
# OUTFIT_CSV_PATH deprecated - outfit prompts now in individual files per archetype+outfit
NAMES_CSV_PATH = DATA_DIR / "names.csv"
REF_SPRITES_DIR = DATA_DIR / "reference_sprites"

# External backup storage (keeps character folders clean for ST game compatibility)
BACKUPS_BASE_DIR = Path.home() / ".sprite_creator" / "backups"

# Game Sprite Importer workspaces (one folder per imported game)
IMPORTS_BASE_DIR = Path.home() / ".sprite_creator" / "imports"

# Legacy cookie cache from the old standalone pipeline tool (migrated on first read)
LEGACY_COOKIE_CACHE_PATH = Path.home() / ".st_sprite_tool" / "auth.json"


def get_backup_dir(backup_id: str) -> Path:
    """Get external backup directory for a character's backup_id (from character.yml)."""
    return BACKUPS_BASE_DIR / backup_id


def generate_backup_id() -> str:
    """Generate a new unique backup ID for a character."""
    return uuid.uuid4().hex[:12]


# ═══════════════════════════════════════════════════════════════════════════════
# SAVED CUSTOM OUTFITS
# ═══════════════════════════════════════════════════════════════════════════════

def load_saved_outfits() -> List[Dict[str, str]]:
    """Load saved custom outfits from config file.

    Returns:
        List of {"name": str, "description": str} dicts.
    """
    import json
    if not CONFIG_PATH.exists():
        return []
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("saved_outfits", [])
    except (json.JSONDecodeError, IOError):
        return []


def save_outfit(name: str, description: str) -> None:
    """Add a new saved outfit to config file."""
    import json
    config = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    outfits = config.get("saved_outfits", [])
    outfits.append({"name": name, "description": description})
    config["saved_outfits"] = outfits

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def delete_saved_outfit(index: int) -> None:
    """Remove a saved outfit by index."""
    import json
    if not CONFIG_PATH.exists():
        return
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, IOError):
        return

    outfits = config.get("saved_outfits", [])
    if 0 <= index < len(outfits):
        outfits.pop(index)
        config["saved_outfits"] = outfits
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# UPLOAD USERNAME
# ═══════════════════════════════════════════════════════════════════════════════

def load_upload_username() -> Optional[str]:
    """Load the saved upload username from config file.

    Returns:
        The saved username string, or None if not set.
    """
    import json
    if not CONFIG_PATH.exists():
        return None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("upload_username")
    except (json.JSONDecodeError, IOError):
        return None


def save_upload_username(username: str) -> None:
    """Save the upload username to config file."""
    import json
    config = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    config["upload_username"] = username

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# SITE COOKIES (Game Sprite Importer crawler)
# ═══════════════════════════════════════════════════════════════════════════════

def load_site_cookies(netloc: str) -> Dict[str, str]:
    """Load saved cookies for a crawler site host (e.g. 'exhentai.org').

    Performs a one-time migration from the legacy ~/.st_sprite_tool/auth.json
    cache used by the old standalone pipeline tool.

    Returns:
        Cookie name -> value dict, empty if none saved.
    """
    import json
    config = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            config = {}

    site_cookies = config.get("site_cookies")
    if site_cookies is None and LEGACY_COOKIE_CACHE_PATH.exists():
        # One-time migration from the old tool's cookie cache
        try:
            with open(LEGACY_COOKIE_CACHE_PATH, "r", encoding="utf-8") as f:
                legacy = json.load(f) or {}
            migrated = legacy.get("cookies") or {}
            if migrated:
                for host, jar in migrated.items():
                    save_site_cookies(host, jar)
                site_cookies = migrated
        except (json.JSONDecodeError, IOError):
            site_cookies = None

    return (site_cookies or {}).get(netloc, {})


def save_site_cookies(netloc: str, cookies: Dict[str, str]) -> None:
    """Save cookies for a crawler site host to the config file."""
    import json
    config = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    config.setdefault("site_cookies", {})[netloc] = cookies

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND COLOR
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_BACKGROUND_COLOR = "black (#000000)"

# Preset options for the settings dialog
BACKGROUND_COLOR_PRESETS = [
    "black (#000000)",
    "light grey (#C0C0C0)",
    "lime green (#00FF00)",
    "blue (#0000FF)",
]


def load_background_color() -> str:
    """Load the saved background color from config file.

    Returns:
        The saved color string (e.g. 'light grey (#C0C0C0)'), or default if not set.
    """
    import json
    if not CONFIG_PATH.exists():
        return DEFAULT_BACKGROUND_COLOR
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("background_color", DEFAULT_BACKGROUND_COLOR)
    except (json.JSONDecodeError, IOError):
        return DEFAULT_BACKGROUND_COLOR


def save_background_color(color: str) -> None:
    """Save the background color to config file."""
    import json
    config = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    config["background_color"] = color

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# Gemini API constants
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
GEMINI_API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_IMAGE_MODEL}:generateContent"
)

# ═══════════════════════════════════════════════════════════════════════════════
# DARK THEME COLOR SCHEME
# ═══════════════════════════════════════════════════════════════════════════════
BG_COLOR = "#2B2B2B"              # Main window background
BG_SECONDARY = "#1E1E1E"          # Darker areas (header, footer)
CARD_BG = "#3C3C3C"               # Card/panel backgrounds
CARD_BG_HOVER = "#4A4A4A"         # Card hover state
CARD_BG_SELECTED = "#4A90D9"      # Selected card background

TEXT_COLOR = "#FFFFFF"            # Primary text
TEXT_SECONDARY = "#A0A0A0"        # Secondary/muted text
TEXT_DISABLED = "#888888"         # Disabled text (readable on dark backgrounds)

ACCENT_COLOR = "#4A90D9"          # Blue accent for primary buttons
ACCENT_HOVER = "#5BA0E9"          # Primary button hover
PRIMARY_COLOR = "#4A90D9"         # Alias for accent (backwards compatibility)
SECONDARY_COLOR = "#666666"       # Secondary button background
SECONDARY_HOVER = "#777777"       # Secondary button hover
DANGER_COLOR = "#D94A4A"          # Red for destructive actions
DANGER_HOVER = "#E95A5A"          # Danger button hover
SUCCESS_COLOR = "#4AD94A"         # Green for success states

# Status text colors (high contrast on dark backgrounds)
ERROR_TEXT = "#FF6B6B"            # Red for inline error messages
WARNING_TEXT = "#FFB347"          # Orange for warnings and tips
CAUTION_TEXT = "#FFD700"          # Yellow for important cautions

# Status background tints
ERROR_BG = "#4A2020"              # Dark red tint for error states
OVERLAY_BG = "#1E1E2E"           # Dark overlay for loading states

LINE_COLOR = "#00E5FF"            # Cyan accent for highlights
HIGHLIGHT_COLOR = "#00E5FF"       # Interactive element highlights
BORDER_COLOR = "#555555"          # Subtle borders

# ═══════════════════════════════════════════════════════════════════════════════
# FONT DEFINITIONS (Segoe UI for modern Windows look)
# ═══════════════════════════════════════════════════════════════════════════════
FONT_FAMILY = "Segoe UI"

# Title fonts (large, prominent)
TITLE_FONT = (FONT_FAMILY, 24, "bold")       # Main screen titles
PAGE_TITLE_FONT = (FONT_FAMILY, 20, "bold")  # Page/wizard step titles
SECTION_FONT = (FONT_FAMILY, 16, "bold")     # Section headers

# Body fonts (medium, readable)
BODY_FONT = (FONT_FAMILY, 12)                # Regular body text
BODY_FONT_BOLD = (FONT_FAMILY, 12, "bold")   # Emphasized body text

# Small fonts (compact, secondary)
SMALL_FONT = (FONT_FAMILY, 10)               # Labels, hints, secondary text
SMALL_FONT_BOLD = (FONT_FAMILY, 10, "bold")  # Small emphasized text

# Button fonts
BUTTON_FONT = (FONT_FAMILY, 11)              # Standard buttons
BUTTON_FONT_LARGE = (FONT_FAMILY, 13, "bold") # Large action buttons

# Legacy aliases (backwards compatibility)
INSTRUCTION_FONT = BODY_FONT

# Layout constants
WINDOW_MARGIN = 10
WRAP_PADDING = 40
CARD_PADDING = 16
CARD_RADIUS = 8  # For styling reference (tkinter doesn't support border-radius natively)

# Outfit keys:
# Original is always included by the pipeline, but you can choose which additional
# outfits to generate. These keys should match outfit_key values in the CSV.
ALL_OUTFIT_KEYS: List[str] = ["formal", "casual", "uniform", "athletic", "swimsuit", "underwear"]

# Default subset used when the user does not change anything.
OUTFIT_KEYS: List[str] = ["formal", "casual"]

# Default ordered list of expressions we actually use per outfit.
# The first entry is always neutral.
EXPRESSIONS_SEQUENCE: List[Tuple[str, str]] = [
    # CORE (0-7) - Must-haves for any VN
    ("0",  "neutral with a soft smile"),
    ("1",  "talking"),
    ("2",  "happy"),
    ("3",  "sad"),
    ("4",  "angry"),
    ("5",  "surprised"),
    ("6",  "embarrassed and blushing"),
    ("7",  "confused"),
    # EXTENDED (8-12) - Common additions
    ("8",  "laughing"),
    ("9",  "scared"),
    ("10", "crying"),
    ("11", "skeptical"),
    ("12", "pensive, deep in thought"),
    # PERSONALITY (13-14)
    ("13", "confident and smug"),
    ("14", "playful, giving a wink"),
    # SITUATIONAL (15-16)
    ("15", "sleepy"),
    ("16", "aroused and blushing"),
]


# Archetypes and their gender style codes (used by UI dropdowns)
GENDER_ARCHETYPES: List[Tuple[str, str]] = [
    ("young woman", "f"),
    ("adult woman", "f"),
    ("motherly woman", "f"),
    ("young man", "m"),
    ("adult man", "m"),
    ("fatherly man", "m"),
]

# Extended archetype data for prompt generation (used by fusion and prompt-based creation)
# The prompt_phrase is what Gemini sees - includes age and style descriptors
ARCHETYPES: Dict[str, Dict[str, str]] = {
    "young woman": {
        "gender": "f",
        "prompt_phrase": "cute young woman aged 18-22 with a slim figure and youthful face",
    },
    "adult woman": {
        "gender": "f",
        "prompt_phrase": "attractive woman aged 25-32 with a curvy figure, full bust, wide hips, and a confident alluring expression",
    },
    "motherly woman": {
        "gender": "f",
        "prompt_phrase": "beautiful mature woman aged 35-45 with a voluptuous figure, very large bust, wide pronounced hips, and a warm seductive MILF appeal",
    },
    "young man": {
        "gender": "m",
        "prompt_phrase": "handsome young man aged 18-22 with a lean athletic build, youthful features, and a fresh clean look",
    },
    "adult man": {
        "gender": "m",
        "prompt_phrase": "attractive man aged 25-32 with an athletic muscular build, defined jawline, and confident rugged appeal",
    },
    "fatherly man": {
        "gender": "m",
        "prompt_phrase": "handsome mature man aged 35-45 with a broad solid build, distinguished features, and warm DILF appeal",
    },
}

# ═══════════════════════════════════════════════════════════════════════════════
# UNDERWEAR TIER SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════
# Underwear prompts bypass Gemini text generation entirely due to high safety filter rates.
# Uses a tiered fallback system: Tier 0 (random with variety) -> Tier 1-N (ordered fallback)

# Female archetypes that get color variety in Tier 0
FEMALE_ARCHETYPES: List[str] = ["young woman", "adult woman", "motherly woman"]

# Color pool for female Tier 0 variety
UNDERWEAR_COLORS: List[str] = [
    "White", "Black", "Pink", "Cream", "Light blue", "Lavender",
    "Gray", "Yellow", "Mint", "Coral", "Red", "Navy"
]

# Non-bottom tier prompts for Tier 0 random selection (excludes most reliable fallback)
UNDERWEAR_TIER0_PROMPTS: Dict[str, List[str]] = {
    "young woman": ["Trendy underwear", "Trendy undergarments", "Cute undergarments"],
    "adult woman": ["Chic underwear", "Stylish underwear", "Elegant underwear"],
    "motherly woman": ["Comfortable undergarments", "Practical undergarments"],
    "young man": ["Simple undergarments", "Basic undergarments"],
    "adult man": ["Simple undergarments", "Basic undergarments"],
    "fatherly man": ["Simple undergarments", "Basic undergarments"],
}

# Ordered fallback tiers (Tier 1 through N) - tried in order when Tier 0 fails
UNDERWEAR_FALLBACK_TIERS: Dict[str, List[str]] = {
    "young woman": [
        "Trendy underwear",
        "Trendy undergarments",
        "Cute undergarments",
        "Pink undergarments",
        "Modern undergarments",
        "Lace trim bikini",
    ],
    "adult woman": [
        "Chic underwear",
        "Stylish underwear",
        "Elegant underwear",
        "Pink undergarments",
        "Modern undergarments",
        "Lace trim bikini",
    ],
    "motherly woman": [
        "Comfortable undergarments",
        "Practical undergarments",
        "Modest undergarments",
        "Modern undergarments",
        "Lace trim bikini",
    ],
    "young man": [
        "Simple undergarments",
        "Basic undergarments",
        "Cotton underwear",
    ],
    "adult man": [
        "Simple undergarments",
        "Basic undergarments",
        "Cotton underwear",
    ],
    "fatherly man": [
        "Simple undergarments",
        "Basic undergarments",
        "Cotton underwear",
    ],
}

# Safety fallback prompts for expressions that may trigger safety filters
# Maps expression key to a safer alternative description
SAFETY_FALLBACK_EXPRESSION_PROMPTS: Dict[str, str] = {
    "6": "embarrassed with a soft pink blush on the cheeks",
    "16": "flustered and blushing with a shy, warm expression",
}
