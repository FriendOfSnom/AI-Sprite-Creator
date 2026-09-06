"""
Finalize a character pile into a Student Transfer character folder.

Output format (verified against the original organizer and the current
app): one outfit per pose letter ,

    <char>/<letter>/outfits/outfit_<letter>.webp   full body, mid-thigh crop
    <char>/<letter>/faces/face/<i>.webp            full-width top slices
    <char>/character.yml

Faces are never cut-out regions: they are the top slab of the same
pixel-aligned canvas, so overlaying them on any outfit of the same stack
reproduces the original render exactly. The recomposite verification
proves that per image and reports any chin line that cut through real
variation (a blush below the chin, an accessory the guard missed).
"""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

# Decode + LANCZOS resize of big sprites releases the GIL, so loading a
# group's expressions in parallel scales across cores.
_FINALIZE_WORKERS = min(8, (os.cpu_count() or 4))

# WebP compression effort for the written faces/outfits. Still lossless (no
# quality loss), but method 4 encodes ~6x faster than 6 for ~3% larger files,
# which is the bulk of finalize time on big sprites.
_WEBP_METHOD = 4

from ...processing.image_utils import get_unique_folder_name, save_img_webp_or_png
from ...processing.pose_processor import write_character_yml
from .state import CharacterPile, GroupsModel, PoseStack
from .stacker import _morph_open
from . import workspace

# Verification thresholds: a recomposited image must match the original
# within re-encode tolerance. Galleries are routinely resampled/re-encoded
# (e-hentai serves webp/jpeg re-encodes), which shifts pixels along line-art
# edges by small amounts everywhere, measured on a real rip: ~6-9% of body
# pixels differ at delta 3, but ≤0.24% survive delta 20 + a 3x3 open.
# A genuine defect (blush below the chin, missed accessory) covers ≥0.5%.
VERIFY_CHANNEL_DELTA = 20
VERIFY_MAX_FRACTION = 0.005
VERIFY_MORPH_PASSES = 1

LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _outfit_slug(label: str, used: set) -> str:
    """Filesystem-safe outfit filename stem from the user's label; the stem
    becomes the outfit's in-game name (e.g. 'School Uniform' -> school_uniform)."""
    import re
    slug = re.sub(r"[^\w]+", "_", (label or "outfit").strip().lower()).strip("_")
    slug = slug or "outfit"
    candidate = slug
    n = 2
    while candidate in used:
        candidate = f"{slug}_{n}"
        n += 1
    return candidate


@dataclass
class CharacterResult:
    folder: Path
    poses: List[str]
    outfit_count: int
    expression_count: int
    verification: dict


def stack_trim_bbox(raw_dir: Path, stack: PoseStack) -> Optional[Tuple[int, int, int, int]]:
    """Uniform transparent-padding trim box for a stack, from its rep image.

    One box per stack, applied to every variant of every outfit. Faces are
    per-outfit and corner-aligned within an outfit, so the trim is safe.
    Returns None when the rep has no alpha padding to trim.
    """
    rep = stack.rep_image
    if rep is None:
        return None
    with Image.open(raw_dir / rep) as probe_img:
        rep_mode = probe_img.mode
    if rep_mode not in ("RGBA", "LA", "P"):
        return None
    img = Image.open(raw_dir / rep)
    bbox = img.convert("RGBA").getbbox()
    if bbox is None or bbox == (0, 0, img.width, img.height):
        return None
    return bbox


def _load_rgba(raw_dir: Path, name: str,
               trim: Optional[Tuple[int, int, int, int]]) -> Image.Image:
    img = Image.open(raw_dir / name).convert("RGBA")
    if trim:
        img = img.crop(trim)
    return img


def base_stack_id(pile: CharacterPile) -> Optional[str]:
    """The pile's BASE pose: the first stack, the one shown in the global
    scale comparison. Its normalization factor is locked at 1.0; every
    other pose is sized relative to it."""
    if not pile.stack_ids:
        return None
    return sorted(pile.stack_ids, key=lambda s: int(s.split("_")[1]))[0]


def finalize_pile(
    workspace_dir: Path,
    groups: GroupsModel,
    pile: CharacterPile,
    game_name: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> CharacterResult:
    """Write the ST character folder for one pile and verify it.

    Requires pile.finalize to be fully populated (mid_thigh_y and chin_y
    per stack, eye_line_ratio, name_color, scale, display_name, voice).
    """
    def report(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    raw_dir = workspace.raw_dir(workspace_dir)
    out_root = workspace.output_dir(workspace_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    fin = pile.finalize
    char_dir = out_root / get_unique_folder_name(out_root, fin.display_name)
    char_dir.mkdir(parents=True)

    poses_yaml: Dict[str, dict] = {}
    letters_used: List[str] = []
    outfit_count = 0
    expression_count = 0
    mismatches: List[dict] = []
    verified_total = 0
    original_size: Optional[List[int]] = None
    letter_index = 0

    # Each OUTFIT becomes its own pose letter with its OWN faces (derived
    # only from its own expression variants). Nothing is ever shared across
    # outfits, so no cross-outfit alignment is possible or needed, every
    # face slice sits on the exact render it was cut from. Like the AI flow.
    stack_ids = sorted(pile.stack_ids, key=lambda s: int(s.split("_")[1]))
    for stack_id in stack_ids:
        stack = groups.stacks[stack_id]
        mid_thigh = fin.mid_thigh_y.get(stack_id)
        chin = fin.chin_y.get(stack_id)
        if mid_thigh is None or chin is None:
            raise ValueError(f"Missing crop lines for {stack_id}")

        trim = stack_trim_bbox(raw_dir, stack)
        factor = fin.pose_scale.get(stack_id, 1.0)
        report(f"Processing {stack_id} ({len(stack.outfits)} outfits)…")

        def load_normalized(name: str) -> Image.Image:
            """Load, trim, and apply this pose's normalization factor.

            Resizing the WHOLE image before cropping keeps the face-slice
            boundary artifact-free (crop-of-resize, not resize-of-crop).
            """
            img = _load_rgba(raw_dir, name, trim)
            if abs(factor - 1.0) > 1e-4:
                img = img.resize(
                    (max(1, round(img.width * factor)),
                     max(1, round(img.height * factor))),
                    Image.LANCZOS)
            return img

        def norm_y(y: int) -> int:
            return max(1, round(y * factor))

        chin_n = norm_y(chin)
        mid_thigh_n = norm_y(mid_thigh)

        for group in stack.outfits:
            if letter_index >= len(LETTERS):
                report("Warning: more than 26 poses, extra outfits skipped.")
                break
            letter = LETTERS[letter_index]
            letter_index += 1
            letters_used.append(letter)
            outfit_count += 1

            pose_dir = char_dir / letter
            outfits_dir = pose_dir / "outfits"
            faces_dir = pose_dir / "faces" / "face"
            outfits_dir.mkdir(parents=True)
            faces_dir.mkdir(parents=True)

            # Load every expression of this outfit ONCE (decode + trim +
            # LANCZOS resize is the expensive part on big sprites), in
            # parallel. The face slices, the outfit body, and the recomposite
            # check all reuse these instead of re-decoding each image twice.
            names = list(dict.fromkeys(group.images))
            with ThreadPoolExecutor(max_workers=_FINALIZE_WORKERS) as ex:
                loaded = dict(zip(names, ex.map(load_normalized, names)))

            # Faces from THIS outfit's own expression variants only. Same
            # render, corner-aligned by construction (the sorter grouped
            # them because they match everywhere but a compact face region).
            face_slices: List[Image.Image] = []
            seen_bytes = set()
            for name in group.images:
                img = loaded[name]
                face = img.crop((0, 0, img.width, min(chin_n, img.height)))
                key = face.tobytes()
                if key in seen_bytes:
                    continue
                seen_bytes.add(key)
                face_slices.append(face)
            for i, face in enumerate(face_slices):
                save_img_webp_or_png(face, faces_dir / str(i),
                                     method=_WEBP_METHOD)
            expression_count += len(face_slices)

            slug = _outfit_slug(group.label, set())
            group_rep = loaded[group.images[0]]
            outfit_img = group_rep.crop(
                (0, 0, group_rep.width, min(mid_thigh_n, group_rep.height)))
            save_img_webp_or_png(outfit_img, outfits_dir / slug,
                                 method=_WEBP_METHOD)
            poses_yaml[letter] = {"facing": "right"}
            if original_size is None:
                original_size = [outfit_img.width, outfit_img.height]

            # Recomposite verification: this outfit's own face on its own
            # body reproduces each original expression exactly.
            outfit_arr = np.asarray(outfit_img.convert("RGB"), dtype=np.int16)
            for name in group.images:
                original = loaded[name]
                original_cropped = original.crop(
                    (0, 0, original.width, min(mid_thigh_n, original.height)))
                face = original.crop(
                    (0, 0, original.width, min(chin_n, original.height)))
                recomposited = outfit_arr.copy()
                face_arr = np.asarray(face.convert("RGB"), dtype=np.int16)
                recomposited[:face_arr.shape[0], :, :] = face_arr
                orig_arr = np.asarray(original_cropped.convert("RGB"),
                                      dtype=np.int16)
                diff = np.abs(recomposited - orig_arr).max(axis=2)
                mask = diff > VERIFY_CHANNEL_DELTA
                if mask.any():
                    mask = _morph_open(mask, VERIFY_MORPH_PASSES)
                frac = float(mask.mean())
                verified_total += 1
                if frac > VERIFY_MAX_FRACTION:
                    mismatches.append({
                        "pose": letter, "outfit": group.label,
                        "image": name, "diff_frac": round(frac, 5),
                        "kind": "recomposite",
                    })

    report("Writing character.yml…")
    write_character_yml(
        char_dir / "character.yml",
        fin.display_name,
        fin.voice,
        fin.eye_line_ratio,
        fin.name_color,
        fin.scale,
        poses_yaml,
        game=game_name,
        original_size=original_size,
    )

    report("Generating expression sheets…")
    try:
        from ...processing import generate_expression_sheets_for_root
        generate_expression_sheets_for_root(char_dir)
    except Exception as e:
        report(f"Expression sheets failed (non-fatal): {e}")

    report("Generating showChar files…")
    try:
        from ...processing.showchar_generator import generate_showchar_files
        generate_showchar_files(char_dir)
    except Exception as e:
        report(f"showChar generation failed (non-fatal): {e}")

    verification = {
        "total": verified_total,
        "mismatches": mismatches,
    }
    return CharacterResult(
        folder=char_dir,
        poses=letters_used,
        outfit_count=outfit_count,
        expression_count=expression_count,
        verification=verification,
    )
