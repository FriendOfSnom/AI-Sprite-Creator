#!/usr/bin/env python3
"""
Synthetic sprite-rip generator for testing the Game Sprite Importer stacker.

Builds a folder that mimics a real gallery crawl of composited VN sprite
variants, plus a ground-truth manifest:

- 2 base "renders" (distinct procedurally drawn bodies, 800x1400)
- per base: 2-3 outfits (torso repainted below the neck, identical above)
- per outfit: 3-4 expressions (only the face box repainted)
- one exact duplicate file
- one accessory outfit (adds a hat band above the face box) that the
  stacker must split into its own pose stack
- 3 unrelated CG images (one at a different canvas size)
- everything saved as JPEG quality 85 to inject real compression noise

Usage:
    python tools/gen_importer_testdata.py <output_dir>

Writes numbered images (00001.jpg ...) plus manifest.json describing the
ground truth: {filename: {"base": int, "outfit": int, "expr": int} | "cg"
| {"dup_of": filename}}, and the true face box / chin line per base.
"""

import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw

CANVAS = (800, 1400)
FACE_BOX = (310, 140, 490, 300)     # x0, y0, x1, y1 — repainted per expression
TORSO_TOP = 380                     # outfits repaint below this row
HAT_BAND = (280, 40, 520, 130)      # hat accessory zone (above the face box)


def _body_palette(rng):
    return {
        "skin": tuple(rng.randint(180, 240) for _ in range(3)),
        "hair": (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)),
        "bg": (255, 255, 255),
    }


def draw_base(rng) -> Image.Image:
    """One base render: head + hair + arms + legs, torso left blank."""
    pal = _body_palette(rng)
    img = Image.new("RGB", CANVAS, pal["bg"])
    d = ImageDraw.Draw(img)

    # hair blob + head
    d.ellipse((280, 60, 520, 340), fill=pal["hair"])
    d.ellipse((310, 120, 490, 330), fill=pal["skin"])
    # neck + shoulders reaching to torso top
    d.rectangle((370, 320, 430, TORSO_TOP), fill=pal["skin"])
    # arms and legs with a bit of per-base randomness
    arm_off = rng.randint(-40, 40)
    d.polygon([(250 + arm_off, 420), (300 + arm_off, 400), (270 + arm_off, 900),
               (230 + arm_off, 900)], fill=pal["skin"])
    d.polygon([(550 - arm_off, 420), (500 - arm_off, 400), (530 - arm_off, 900),
               (570 - arm_off, 900)], fill=pal["skin"])
    d.rectangle((330, 900, 390, 1360), fill=pal["skin"])
    d.rectangle((410, 900, 470, 1360), fill=pal["skin"])
    # unique per-base texture marks so bases never resemble each other
    for _ in range(30):
        x, y = rng.randint(0, CANVAS[0] - 20), rng.randint(950, CANVAS[1] - 20)
        d.ellipse((x, y, x + 15, y + 15),
                  fill=tuple(rng.randint(0, 255) for _ in range(3)))
    return img


def paint_outfit(img: Image.Image, rng) -> Image.Image:
    """Repaint the torso region below TORSO_TOP; rows above stay identical."""
    out = img.copy()
    d = ImageDraw.Draw(out)
    color = tuple(rng.randint(0, 255) for _ in range(3))
    accent = tuple(rng.randint(0, 255) for _ in range(3))
    d.rectangle((300, TORSO_TOP, 500, 900), fill=color)
    for k in range(rng.randint(2, 5)):
        y = TORSO_TOP + 60 + k * rng.randint(60, 110)
        d.rectangle((310, y, 490, y + 25), fill=accent)
    return out


def paint_expression(img: Image.Image, rng) -> Image.Image:
    """Repaint the FACE_BOX interior — the whole face layer, like real
    composited sprites (skin refill + eyes, brows, mouth, optional blush)."""
    out = img.copy()
    d = ImageDraw.Draw(out)
    x0, y0, x1, y1 = FACE_BOX
    skin = tuple(rng.randint(185, 235) for _ in range(3))
    d.rectangle((x0, y0, x1, y1), fill=skin)
    eye = tuple(rng.randint(0, 120) for _ in range(3))
    ew, eh = rng.randint(20, 45), rng.randint(12, 35)
    d.ellipse((x0 + 25, y0 + 45, x0 + 25 + ew, y0 + 45 + eh), fill=eye)
    d.ellipse((x1 - 25 - ew, y0 + 45, x1 - 25, y0 + 45 + eh), fill=eye)
    d.rectangle((x0 + 20, y0 + 20, x0 + 20 + ew + 10, y0 + 20 + rng.randint(4, 12)),
                fill=eye)  # brows
    d.rectangle((x1 - 30 - ew, y0 + 20, x1 - 20, y0 + 20 + rng.randint(4, 12)),
                fill=eye)
    mouth_y = y1 - rng.randint(35, 60)
    d.ellipse((x0 + 60, mouth_y - rng.randint(6, 22), x1 - 60, mouth_y + rng.randint(6, 22)),
              fill=(rng.randint(120, 200), 30, 30))
    if rng.random() < 0.4:  # blush patches
        blush = (rng.randint(220, 255), rng.randint(120, 170), rng.randint(120, 170))
        d.ellipse((x0 + 5, y1 - 70, x0 + 45, y1 - 40), fill=blush)
        d.ellipse((x1 - 45, y1 - 70, x1 - 5, y1 - 40), fill=blush)
    return out


def paint_hat(img: Image.Image, rng) -> Image.Image:
    """Add an accessory band above the face box (breaks face-slice sharing)."""
    out = img.copy()
    d = ImageDraw.Draw(out)
    d.rectangle(HAT_BAND, fill=tuple(rng.randint(0, 255) for _ in range(3)))
    return out


def draw_cg(rng, size=CANVAS) -> Image.Image:
    img = Image.new("RGB", size, tuple(rng.randint(0, 255) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(60):
        x, y = rng.randint(0, size[0] - 60), rng.randint(0, size[1] - 60)
        d.ellipse((x, y, x + rng.randint(20, 60), y + rng.randint(20, 60)),
                  fill=tuple(rng.randint(0, 255) for _ in range(3)))
    return img


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python tools/gen_importer_testdata.py <output_dir>")
        sys.exit(2)

    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)

    manifest = {"images": {}, "face_box": list(FACE_BOX), "chin_y": FACE_BOX[3],
                "bases": 2, "hat_base": 1}
    counter = [0]
    images = []   # (filename, PIL image)

    def emit(img, truth):
        counter[0] += 1
        name = f"{counter[0]:05d}.jpg"
        images.append((name, img))
        manifest["images"][name] = truth
        return name

    outfits_per_base = [2, 3]   # base 1 gets the hat as its 3rd outfit
    dup_target = None

    for base_idx in range(2):
        base = draw_base(rng)
        for outfit_idx in range(outfits_per_base[base_idx]):
            is_hat = (base_idx == 1 and outfit_idx == 2)
            outfit = paint_outfit(base, rng)
            if is_hat:
                outfit = paint_hat(outfit, rng)
            n_expr = rng.randint(3, 4)
            for expr_idx in range(n_expr):
                img = paint_expression(outfit, rng)
                name = emit(img, {"base": base_idx, "outfit": outfit_idx,
                                  "expr": expr_idx, "hat": is_hat})
                if base_idx == 0 and outfit_idx == 0 and expr_idx == 1:
                    dup_target = (name, img)

    # exact duplicate (same pixels re-emitted; JPEG encode is deterministic)
    emit(dup_target[1], {"dup_of": dup_target[0]})

    # crop variants: galleries often ship the same render cropped at
    # different lengths (same width). Both must fold/join, not fragment.
    crop_h = CANVAS[1] - 296          # 8-aligned so JPEG blocks match
    # (a) crop-duplicate of an existing expression -> folds, taller kept
    emit(dup_target[1].crop((0, 0, CANVAS[0], crop_h)),
         {"dup_of": dup_target[0]})
    # (b) a NEW expression of base 0 outfit 0, shipped only as a short crop
    #     -> joins that outfit despite the height difference. Reproduce
    #     outfit 0 exactly by replaying the main RNG sequence.
    rng42 = random.Random(42)
    base0 = draw_base(rng42)
    outfit0 = paint_outfit(base0, rng42)
    new_expr = paint_expression(outfit0, rng)
    emit(new_expr.crop((0, 0, CANVAS[0], crop_h)),
         {"base": 0, "outfit": 0, "expr": 99, "hat": False})

    # unrelated CGs, one at a different size
    emit(draw_cg(rng), "cg")
    emit(draw_cg(rng), "cg")
    emit(draw_cg(rng, size=(1024, 768)), "cg")

    # shuffle-save with sequential names already assigned (gallery order kept)
    for name, img in images:
        img.save(out_dir / name, "JPEG", quality=85)

    with (out_dir / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Wrote {len(images)} images + manifest.json to {out_dir}")


if __name__ == "__main__":
    main()
