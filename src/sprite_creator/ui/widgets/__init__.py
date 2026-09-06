"""
Shared, flow-agnostic UI widgets.

Plain tk.Frame subclasses with no imports from ui/screens/, usable by any
flow. These are the extracted building blocks of the future flow refactor;
existing wizard steps keep their own copies until they are migrated.
"""

from .progress_panel import ProgressLogPanel
from .thumb_grid import ThumbnailGrid, ThumbItem
from .y_line_picker import YLinePickerCanvas
from .eye_color_picker import EyeLineNameColorPicker
from .scale_compare import ScaleComparePanel

__all__ = [
    "ProgressLogPanel",
    "ThumbnailGrid",
    "ThumbItem",
    "YLinePickerCanvas",
    "EyeLineNameColorPicker",
    "ScaleComparePanel",
]
