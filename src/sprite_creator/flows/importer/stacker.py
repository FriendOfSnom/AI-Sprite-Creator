"""
Deterministic auto-stacker for the Game Sprite Importer.

Groups a flat folder of downloaded sprite images into a hierarchy of
pose stacks -> outfit groups -> expression variants using pure pixel math
(no AI/ML models). It exploits the structure of VN sprite rips, which are
composited variants of one base render:

- same pose + outfit, different expression: pixels identical except a
  small compact region (the face)
- same pose, different outfit: head/hair rows identical, body differs
- different pose / unrelated art: images differ nearly everywhere

Pipeline: exact-dimension bucketing -> dHash prefilter -> tolerance-based
pairwise diff classification -> union-find. The face region of each stack
is then derived from the per-pixel variance across its expression variants
(no face detection), which also yields the suggested chin-crop line.

Note on glasses: an outfit variant whose only change is inside the face
region (e.g. glasses) is indistinguishable from an expression change by
pixel evidence, and is grouped as extra expressions, which ST renders
fine. The accessory guard only splits outfits that change pixels *above*
the face region (hats, hoods, hair accessories), where shared face slices
would genuinely break.

CLI test harness:
    python -m sprite_creator.flows.importer.stacker <folder> [--json out.json]
"""

import os
import pathlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from .state import GroupsModel, ImageRecord, OutfitGroup, PoseStack

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True)
class StackerConfig:
    """All thresholds in one place; persisted into groups.json."""
    downscale_max_side: int = 256        # legacy cap; smalls are width-scaled
    small_width: int = 176               # small arrays: scale = small_width / w
                                         # (same-width images share pixel scale,
                                         # so different crops stay comparable)
    pixel_diff_threshold: int = 14       # per-pixel max-channel |a-b| = changed (JPEG tolerance)
    morph_passes: int = 1                # 3x3 open passes on the change mask
    dhash_size: int = 8
    dhash_max_distance: int = 30         # of 64 bits
    dhash_prune_min_bucket: int = 80     # only prune inside buckets larger than this
    duplicate_max_fraction: float = 0.002  # small-scale ROUTER only (256px)
    duplicate_max_px: int = 36           # full-res fold budget, ABSOLUTE pixels.
                                         # Percentage-of-canvas budgets let subtle
                                         # expression changes hide on large sprites,
                                         # so this is absolute. Measured across 5
                                         # rips: true dupes cluster at 0 px, the
                                         # smallest REAL expression change seen is
                                         # 38 px (a mouth), so 36 folds byte-near
                                         # dupes with a 2 px margin under it. NOTE:
                                         # dupe and real-variant px OVERLAP across
                                         # rips (a noisy rip's dupe can exceed a
                                         # clean rip's real change), so this cannot
                                         # be pushed higher without eating real
                                         # expressions; bulk near-dupe cleanup is a
                                         # human-review job, not a threshold.
    expr_max_fraction: float = 0.10      # changed-fraction ceiling for expression pairs
    expr_max_bbox_area_frac: float = 0.05  # a facial expression touches a SMALL
                                         # region; measured on real data, face
                                         # changes cover <1% of the canvas while
                                         # an arm/gesture pose change covers 7%+,
                                         # so 5% cleanly separates expressions
                                         # from pose changes (was 0.25, which
                                         # merged distinct poses into one group)
    expr_max_bottom_frac: float = 0.45   # expression diffs must sit in the upper image
    outfit_head_band_frac: float = 0.10  # rows [0, H*frac) must be unchanged for outfit pairs
    unrelated_min_fraction: float = 0.50
    full_res_diff_threshold: int = 20    # stricter threshold for full-res face-box diffs
    chin_margin_frac: float = 0.02       # margin below face box for the suggested chin line
    accessory_guard_px: int = 6          # outfit diff above (chin_y - guard) triggers a split
    face_box_pad_frac: float = 0.02      # padding added around the raw face bbox
    workers: int = 0                     # decode/compare threads (0 = auto)


def _worker_count(cfg: "StackerConfig") -> int:
    if cfg.workers > 0:
        return cfg.workers
    return max(2, min(8, os.cpu_count() or 4))


class CancelledError(Exception):
    pass


# ----------------------------------------------------------------------
# Hashing and image loading
# ----------------------------------------------------------------------
def dhash(img: Image.Image, size: int = 8) -> int:
    """64-bit difference hash: gradient signs of a (size+1)x(size) grayscale."""
    small = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    arr = np.asarray(small, dtype=np.int16)
    bits = (arr[:, 1:] > arr[:, :-1]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _flatten(img: Image.Image) -> Image.Image:
    """Composite RGBA over white (JPEG-comparable), yielding RGB."""
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, img).convert("RGB")
    return img.convert("RGB")


def _load_flat(path: pathlib.Path) -> Image.Image:
    """Load an image with RGBA composited over white (JPEG-comparable)."""
    return _flatten(Image.open(path))


class _ImageCache:
    """Downscaled RGB arrays kept for all images; full-res RGB LRU-capped.

    Comparisons run in RGB (max channel difference), not grayscale: two
    different characters can have hair/skin of near-identical luminance,
    which makes them falsely identical in gray.

    Thread-safe (decode phases run in a thread pool). If disk_cache_dir is
    set, small arrays persist as .npy so re-sorts and incremental additions
    skip decoding entirely.
    """

    def __init__(self, raw_dir: pathlib.Path, cfg: StackerConfig,
                 full_res_cap: int = 16,
                 disk_cache_dir: Optional[pathlib.Path] = None):
        self.raw_dir = raw_dir
        self.cfg = cfg
        # v2: small arrays are width-scaled (crop-tolerant comparison)
        self.disk_cache_dir = (disk_cache_dir / "v2") if disk_cache_dir else None
        if self.disk_cache_dir is not None:
            self.disk_cache_dir.mkdir(parents=True, exist_ok=True)
        self._small: Dict[str, np.ndarray] = {}
        self._tiny: Dict[str, np.ndarray] = {}
        self._full: Dict[str, np.ndarray] = {}
        self._full_order: List[str] = []
        self._full_cap = full_res_cap
        self._lock = threading.Lock()

    def path(self, name: str) -> pathlib.Path:
        return self.raw_dir / name

    def rgb_tiny(self, name: str) -> np.ndarray:
        """~64px thumbnail used to cheaply reject clearly-unrelated pairs."""
        with self._lock:
            cached = self._tiny.get(name)
        if cached is not None:
            return cached
        small = self.rgb_small(name)
        w = small.shape[1]
        step = max(1, w // 64)      # width-based: same-width crops share scale
        tiny = small[::step, ::step]
        with self._lock:
            self._tiny[name] = tiny
        return tiny

    def _small_disk_path(self, name: str) -> Optional[pathlib.Path]:
        if self.disk_cache_dir is None:
            return None
        return self.disk_cache_dir / (name + ".npy")

    def rgb_small(self, name: str) -> np.ndarray:
        with self._lock:
            cached = self._small.get(name)
        if cached is not None:
            return cached

        disk_path = self._small_disk_path(name)
        arr = None
        if disk_path is not None and disk_path.is_file():
            try:
                arr = np.load(disk_path)
            except Exception:
                arr = None

        if arr is None:
            img = Image.open(self.path(name))
            # JPEG can decode directly at reduced scale nearly free; ask for
            # ~2x the target so the final LANCZOS resize stays high quality.
            if img.format == "JPEG":
                target = self.cfg.small_width * 2
                img.draft("RGB", (target, target * 4))
            img = _flatten(img)
            w, h = img.size
            # Width-based scale: all images of one width land on the same
            # pixel grid, so same-render-different-crop variants can be
            # compared over their overlapping rows.
            scale = self.cfg.small_width / max(1, w)
            if scale < 1.0:
                img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                                 Image.LANCZOS)
            arr = np.asarray(img, dtype=np.uint8)
            if disk_path is not None:
                try:
                    np.save(disk_path, arr)
                except Exception:
                    pass

        with self._lock:
            self._small[name] = arr
        return arr

    def rgb_full(self, name: str) -> np.ndarray:
        with self._lock:
            if name in self._full:
                self._full_order.remove(name)
                self._full_order.append(name)
                return self._full[name]
        arr = np.asarray(_load_flat(self.path(name)), dtype=np.uint8)
        with self._lock:
            self._full[name] = arr
            self._full_order.append(name)
            while len(self._full_order) > self._full_cap:
                evict = self._full_order.pop(0)
                self._full.pop(evict, None)
        return arr


# ----------------------------------------------------------------------
# Pair classification
# ----------------------------------------------------------------------
DUPLICATE = "duplicate"
EXPRESSION = "expression"
OUTFIT = "outfit"
UNRELATED = "unrelated"


@dataclass
class PairResult:
    kind: str
    changed_fraction: float = 0.0
    bbox: Optional[Tuple[int, int, int, int]] = None  # small-image coords (x0,y0,x1,y1)
    first_changed_row: int = -1


def _morph_open(mask: np.ndarray, passes: int) -> np.ndarray:
    """3x3 binary open via numpy shifts (erode then dilate), no scipy."""
    for _ in range(passes):
        mask = _erode3(mask)
    for _ in range(passes):
        mask = _dilate3(mask)
    return mask


def _erode3(m: np.ndarray) -> np.ndarray:
    p = np.pad(m, 1, mode="constant", constant_values=True)
    out = p[1:-1, 1:-1].copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            out &= p[1 + dy:p.shape[0] - 1 + dy, 1 + dx:p.shape[1] - 1 + dx]
    return out


def _dilate3(m: np.ndarray) -> np.ndarray:
    p = np.pad(m, 1, mode="constant", constant_values=False)
    out = p[1:-1, 1:-1].copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            out |= p[1 + dy:p.shape[0] - 1 + dy, 1 + dx:p.shape[1] - 1 + dx]
    return out


def _overlap(a: np.ndarray, b: np.ndarray):
    """Slice two same-width arrays to their shared top rows. Same-render
    variants cropped at different lengths are identical there; the extra
    rows are just crop difference and carry no signal."""
    rows = min(a.shape[0], b.shape[0])
    return a[:rows], b[:rows]


def _diff_mask(a: np.ndarray, b: np.ndarray, threshold: int, morph_passes: int) -> np.ndarray:
    a, b = _overlap(a, b)
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
    if diff.ndim == 3:
        diff = diff.max(axis=2)     # max channel difference (hue-sensitive)
    mask = diff > threshold
    if morph_passes > 0 and mask.any():
        mask = _morph_open(mask, morph_passes)
    return mask


def classify_pair(a: np.ndarray, b: np.ndarray, cfg: StackerConfig) -> PairResult:
    """Classify the relationship between two same-sized RGB image arrays."""
    mask = _diff_mask(a, b, cfg.pixel_diff_threshold, cfg.morph_passes)
    frac = float(mask.mean())

    if frac < cfg.duplicate_max_fraction:
        return PairResult(DUPLICATE, frac)

    ys, xs = np.nonzero(mask)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    first_row = int(ys.min())
    h, w = a.shape[:2]

    if frac >= cfg.unrelated_min_fraction:
        return PairResult(UNRELATED, frac, bbox, first_row)

    bbox_area_frac = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / float(h * w)
    if (frac <= cfg.expr_max_fraction
            and bbox_area_frac <= cfg.expr_max_bbox_area_frac
            and bbox[3] <= h * cfg.expr_max_bottom_frac):
        return PairResult(EXPRESSION, frac, bbox, first_row)

    if first_row >= h * cfg.outfit_head_band_frac:
        return PairResult(OUTFIT, frac, bbox, first_row)

    return PairResult(UNRELATED, frac, bbox, first_row)


# ----------------------------------------------------------------------
# Union-find
# ----------------------------------------------------------------------
class _UnionFind:
    def __init__(self, items):
        self.parent = {x: x for x in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb

    def components(self) -> Dict[str, List]:
        comps: Dict[str, List] = {}
        for x in self.parent:
            comps.setdefault(self.find(x), []).append(x)
        return comps


# ----------------------------------------------------------------------
# Main pipeline
# ----------------------------------------------------------------------
def build_groups(
    raw_dir: pathlib.Path,
    cfg: Optional[StackerConfig] = None,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
    disk_cache_dir: Optional[pathlib.Path] = None,
) -> GroupsModel:
    """Run the full auto-stack pipeline over a flat folder of images.

    Args:
        raw_dir: Folder of images (not descended into).
        cfg: Thresholds; defaults used if None.
        progress_cb: Called with (phase, done, total).
        cancel: Callable returning True to abort (raises CancelledError).
        disk_cache_dir: Optional folder for persisted comparison arrays ,
            re-sorts and incremental additions skip image decoding.

    Returns:
        A populated GroupsModel (no piles, those are user-built later).
    """
    cfg = cfg or StackerConfig()
    model = GroupsModel(stacker_config=asdict(cfg))
    cache = _ImageCache(raw_dir, cfg, disk_cache_dir=disk_cache_dir)

    def check_cancel():
        if cancel and cancel():
            raise CancelledError()

    def report(phase: str, done: int, total: int):
        if progress_cb:
            progress_cb(phase, done, total)

    # 1. Enumerate and verify
    names: List[str] = []
    files = sorted(
        p for p in raw_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )
    for i, path in enumerate(files):
        check_cancel()
        try:
            with Image.open(path) as im:
                im.verify()
            names.append(path.name)
        except Exception:
            model.discarded.append(path.name)
        report("verify", i + 1, len(files))

    # 2. Per-image facts: size, dhash, cached small array, decode-bound,
    # so parallelized (PIL decode and numpy release the GIL).
    done_counter = {"n": 0}
    counter_lock = threading.Lock()

    def fingerprint(name: str) -> ImageRecord:
        check_cancel()
        with Image.open(cache.path(name)) as im:
            size = im.size
        small = cache.rgb_small(name)
        # dhash from the cached small array (avoids a second decode)
        record = ImageRecord(name=name, size=size,
                             dhash=dhash(Image.fromarray(small), cfg.dhash_size))
        with counter_lock:
            done_counter["n"] += 1
            done = done_counter["n"]
        report("hash", done, len(names))
        return record

    with ThreadPoolExecutor(max_workers=_worker_count(cfg)) as pool:
        for record in pool.map(fingerprint, names):
            model.images[record.name] = record
    check_cancel()

    # 3. Bucket by WIDTH: same-width images at different heights are
    # usually the same render cropped at different lengths, comparable
    # over their shared top rows and mergeable into one pose.
    buckets: Dict[int, List[str]] = {}
    for name in names:
        buckets.setdefault(model.images[name].size[0], []).append(name)

    # 4. Pairwise classification within buckets (duplicate/expression edges).
    # Outfit linking happens afterwards at the group level, where each
    # group's face region is known and can be masked out, comparing raw
    # image pairs across outfits is unreliable because their faces differ too.
    dup_edges: List[Tuple[str, str]] = []
    expr_edges: List[Tuple[str, str]] = []
    dup_candidates: List[Tuple[str, str]] = []

    total_pairs = sum(len(b) * (len(b) - 1) // 2 for b in buckets.values())
    done_pairs = 0
    for bucket in buckets.values():
        use_prune = len(bucket) > cfg.dhash_prune_min_bucket
        for i in range(len(bucket)):
            check_cancel()
            for j in range(i + 1, len(bucket)):
                done_pairs += 1
                a, b = bucket[i], bucket[j]
                same_size = model.images[a].size == model.images[b].size
                if (use_prune and same_size
                        and hamming(model.images[a].dhash,
                                    model.images[b].dhash) > cfg.dhash_max_distance):
                    continue    # dhash is whole-image: only valid on equal sizes
                # Cheap reject: this pass only needs duplicate/expression
                # edges (small diffs), so any pair whose ~64px diff fraction
                # is well past the expression ceiling can't contribute an
                # edge. This kills the vast cross-character majority at a
                # fraction of the full compare cost; borderline pairs fall
                # through to the real classifier.
                tiny_a, tiny_b = _overlap(cache.rgb_tiny(a), cache.rgb_tiny(b))
                tiny_diff = np.abs(tiny_a.astype(np.int16) - tiny_b.astype(np.int16))
                if float((tiny_diff.max(axis=2) > cfg.pixel_diff_threshold).mean()) \
                        > cfg.expr_max_fraction * 2.0:
                    continue
                result = classify_pair(cache.rgb_small(a), cache.rgb_small(b), cfg)
                if result.kind == DUPLICATE:
                    dup_candidates.append((a, b))
                elif result.kind == EXPRESSION:
                    expr_edges.append((a, b))
            report("compare", done_pairs, total_pairs)

    # 4b. Confirm duplicate candidates at full resolution, in parallel
    # (decode-bound). An ABSOLUTE pixel budget is required: a subtle
    # expression change (eyes/mouth) can vanish at 256px AND hide inside
    # any percentage-of-canvas threshold on large sprites. Only truly
    # pixel-identical pairs fold; everything else stays as an expression
    # variant (safe direction: a rare re-encoded near-dupe surfacing as an
    # extra expression is harmless, a lost expression is not).
    def confirm_dup(pair: Tuple[str, str]) -> Tuple[Tuple[str, str], bool]:
        check_cancel()
        a, b = pair
        full_mask = _diff_mask(cache.rgb_full(a), cache.rgb_full(b),
                               cfg.pixel_diff_threshold, cfg.morph_passes)
        return pair, int(full_mask.sum()) <= cfg.duplicate_max_px

    if dup_candidates:
        with ThreadPoolExecutor(max_workers=_worker_count(cfg)) as pool:
            for i, (pair, is_dup) in enumerate(
                    pool.map(confirm_dup, dup_candidates)):
                (dup_edges if is_dup else expr_edges).append(pair)
                report("confirm", i + 1, len(dup_candidates))
    check_cancel()

    # 5. Union-find pass 1: duplicates folded, expression edges -> outfit groups
    dup_uf = _UnionFind(names)
    for a, b in dup_edges:
        dup_uf.union(a, b)
    kept_of: Dict[str, str] = {}       # any name -> kept representative
    dropped_dupes: Dict[str, str] = {}  # dropped -> kept
    for comp in dup_uf.components().values():
        # Prefer the tallest crop (keeps the most content); tiebreak by name
        keeper = min(comp, key=lambda n: (-model.images[n].size[1], n))
        for name in comp:
            kept_of[name] = keeper
            if name != keeper:
                dropped_dupes[name] = keeper

    kept_names = [n for n in names if n not in dropped_dupes]
    group_uf = _UnionFind(kept_names)
    for a, b in expr_edges:
        ka, kb = kept_of[a], kept_of[b]
        if ka != kb:
            group_uf.union(ka, kb)

    group_members: Dict[str, List[str]] = {}
    for root, members in group_uf.components().items():
        group_members[min(members)] = sorted(members)

    # 6-7. FLAT output: each outfit group becomes its OWN pose stack. We
    # deliberately do NOT nest multiple outfits under a shared pose. From flat,
    # fully-composited rips it is impossible to reliably tell pose vs outfit vs
    # accessory apart (a small arm move looks like an outfit change looks like
    # an expression), and a mix where some poses carry sub-outfits and others
    # do not is inconsistent to handle downstream. So every distinct
    # pose+outfit render, with its own expression variants, stands alone.
    # A group with a single image (no expression variants) is a lone render,
    # usually a CG or title card, and goes to Unsorted for the user to review.
    stack_idx = 0
    for gid in sorted(group_members, key=lambda g: group_members[g][0]):
        members = group_members[gid]
        if len(members) == 1:
            model.unsorted.append(members[0])
            continue
        stack_id = f"stack_{stack_idx}"
        stack_idx += 1
        width = model.images[members[0]].size[0]
        tallest = max(model.images[n].size[1] for n in members)
        stack = PoseStack(
            id=stack_id,
            size=(width, tallest),
            outfits=[OutfitGroup(id="outfit_0", images=list(members),
                                 label="1")],
        )
        stack_images = set(stack.all_images())
        stack.duplicates = {d: k for d, k in dropped_dupes.items()
                            if k in stack_images}
        model.stacks[stack_id] = stack

    # 8. Face boxes + chin suggestions (full resolution), decode-bound,
    # parallelized per stack. Each stack decodes its own images exactly
    # once (rep held locally, siblings streamed), so the shared LRU cannot
    # thrash across stacks.
    stack_list = list(model.stacks.values())
    face_counter = {"n": 0}

    def face_one(stack: PoseStack) -> None:
        check_cancel()
        compute_face_box(stack, cache, cfg)
        with counter_lock:
            face_counter["n"] += 1
            done = face_counter["n"]
        report("face", done, len(stack_list))

    with ThreadPoolExecutor(max_workers=_worker_count(cfg)) as pool:
        list(pool.map(face_one, stack_list))
    check_cancel()

    # 9. Accessory guard: peel outfits that change pixels above the chin line
    for stack in list(model.stacks.values()):
        check_cancel()
        apply_accessory_guard(model, stack, cache, cfg)

    return model


def integrate_new_images(
    model: GroupsModel,
    raw_dir: pathlib.Path,
    new_names: List[str],
    cfg: Optional[StackerConfig] = None,
    progress_cb: Optional[Callable[[str, int, int], None]] = None,
    cancel: Optional[Callable[[], bool]] = None,
    disk_cache_dir: Optional[pathlib.Path] = None,
) -> Dict[str, int]:
    """Fold a batch of new images into an EXISTING model without re-sorting.

    New images (e.g. from a second forum post) are first stacked among
    themselves, then each resulting group is matched against the existing
    stacks' representatives:
      - expression match to an existing outfit rep -> joins that outfit
      - masked group-level body match to an existing pose -> new outfit of
        that pose
      - otherwise -> a brand-new stack (badged NEW via accessory_split_from
        left None; caller distinguishes by id >= first_new_stack)

    Existing stacks, their face boxes, and all user piles are preserved.
    Returns a summary dict of counts. New images already present in the
    model are skipped.
    """
    if cfg is None:
        # Reuse the thresholds the model was built with; ignore stored keys
        # that no longer exist so old workspaces stay loadable.
        stored = {k: v for k, v in (model.stacker_config or {}).items()
                  if k in StackerConfig.__dataclass_fields__}
        cfg = StackerConfig(**stored)
    cache = _ImageCache(raw_dir, cfg, disk_cache_dir=disk_cache_dir)

    def check_cancel():
        if cancel and cancel():
            raise CancelledError()

    def report(phase, done, total):
        if progress_cb:
            progress_cb(phase, done, total)

    known = set(model.images) | set(model.unsorted) | set(model.discarded)
    for stack in model.stacks.values():
        known.update(stack.all_images())
        known.update(stack.duplicates)
    fresh = [n for n in new_names if n not in known]
    if not fresh:
        return {"added_images": 0, "new_stacks": 0, "joined": 0, "unsorted": 0}

    # 1. Stack the fresh images among themselves (scoped run, shared cache)
    sub = _build_groups_for_names(model, cache, cfg, fresh, check_cancel, report)

    # 2. Match each new pose against existing stacks' reps
    summary = {"added_images": len(fresh), "new_stacks": 0, "joined": 0,
               "unsorted": len(sub.unsorted)}
    existing_stacks = list(model.stacks.values())
    new_stack_items = sorted(sub.stacks.values(), key=lambda s: int(s.id.split("_")[1]))

    for i, ns in enumerate(new_stack_items):
        check_cancel()
        report("integrate", i + 1, len(new_stack_items))
        placed = False
        ns_width = ns.size[0]
        ns_rep_small = cache.rgb_small(ns.rep_image)
        ns_facebox = _small_face_box_for(ns, cache, cfg)

        for stack in existing_stacks:
            if stack.size[0] != ns_width:
                continue
            # Try expression-join to each existing outfit
            joined_outfit = None
            for outfit in stack.outfits:
                res = classify_pair(ns_rep_small,
                                    cache.rgb_small(outfit.images[0]), cfg)
                if res.kind in (DUPLICATE, EXPRESSION):
                    joined_outfit = outfit
                    break
            if joined_outfit is not None:
                for grp in ns.outfits:
                    joined_outfit.images.extend(grp.images)
                    joined_outfit.images = sorted(set(joined_outfit.images))
                summary["joined"] += 1
                placed = True
                break
            # Try body-match (new outfit of this pose): masked compare
            stack_rep_small = cache.rgb_small(stack.rep_image)
            mask = _diff_mask(ns_rep_small, stack_rep_small,
                              cfg.pixel_diff_threshold, cfg.morph_passes)
            for box in (ns_facebox, _small_face_box_for(stack, cache, cfg)):
                if box:
                    mask[box[1]:box[3], box[0]:box[2]] = False
            if mask.any():
                frac = float(mask.mean())
                first_row = int(np.nonzero(mask)[0].min())
                if not (frac < cfg.unrelated_min_fraction
                        and first_row >= ns_rep_small.shape[0] * cfg.outfit_head_band_frac):
                    continue  # not the same pose
            # same pose body -> append new outfit(s)
            for grp in ns.outfits:
                k = len(stack.outfits)
                stack.outfits.append(OutfitGroup(
                    id=f"outfit_{k}", images=list(grp.images),
                    label=f"{k + 1}"))
            summary["joined"] += 1
            placed = True
            break

        if not placed:
            # brand-new stack for the user to group
            new_id = model.next_stack_id()
            ns.id = new_id
            compute_face_box(ns, cache, cfg)
            model.stacks[new_id] = ns
            summary["new_stacks"] += 1

    # merge image records + unsorted/discarded
    model.images.update(sub.images)
    model.unsorted.extend(sub.unsorted)
    model.discarded.extend(sub.discarded)
    return summary


def _build_groups_for_names(model, cache, cfg, names, check_cancel, report):
    """Run the stacking pipeline over an explicit list of names sharing the
    same raw_dir/cache, used by integrate_new_images so the sub-run reuses
    the parent's decode cache."""
    # Minimal reimplementation over `names`: fingerprint, bucket, pair,
    # union-find, assemble. Mirrors build_groups but scoped to `names`.
    sub = GroupsModel(stacker_config=asdict(cfg))
    valid = []
    for i, name in enumerate(names):
        check_cancel()
        try:
            with Image.open(cache.path(name)) as im:
                size = im.size
            small = cache.rgb_small(name)
            sub.images[name] = ImageRecord(
                name=name, size=size,
                dhash=dhash(Image.fromarray(small), cfg.dhash_size))
            valid.append(name)
        except Exception:
            sub.discarded.append(name)
        report("hash", i + 1, len(names))

    buckets: Dict[int, List[str]] = {}
    for name in valid:
        buckets.setdefault(sub.images[name].size[0], []).append(name)

    dup_edges, expr_edges, dup_candidates = [], [], []
    for bucket in buckets.values():
        use_prune = len(bucket) > cfg.dhash_prune_min_bucket
        for i in range(len(bucket)):
            check_cancel()
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                if (use_prune and sub.images[a].size == sub.images[b].size
                        and hamming(sub.images[a].dhash,
                                    sub.images[b].dhash) > cfg.dhash_max_distance):
                    continue
                ta, tb = _overlap(cache.rgb_tiny(a), cache.rgb_tiny(b))
                td = np.abs(ta.astype(np.int16) - tb.astype(np.int16))
                if float((td.max(axis=2) > cfg.pixel_diff_threshold).mean()) \
                        > cfg.expr_max_fraction * 2.0:
                    continue
                res = classify_pair(cache.rgb_small(a), cache.rgb_small(b), cfg)
                if res.kind == DUPLICATE:
                    dup_candidates.append((a, b))
                elif res.kind == EXPRESSION:
                    expr_edges.append((a, b))
    for a, b in dup_candidates:
        full_mask = _diff_mask(cache.rgb_full(a), cache.rgb_full(b),
                               cfg.pixel_diff_threshold, cfg.morph_passes)
        (dup_edges if int(full_mask.sum()) <= cfg.duplicate_max_px
         else expr_edges).append((a, b))

    dup_uf = _UnionFind(valid)
    for a, b in dup_edges:
        dup_uf.union(a, b)
    kept_of, dropped = {}, {}
    for comp in dup_uf.components().values():
        keeper = min(comp, key=lambda n: (-sub.images[n].size[1], n))
        for n in comp:
            kept_of[n] = keeper
            if n != keeper:
                dropped[n] = keeper
    kept = [n for n in valid if n not in dropped]
    guf = _UnionFind(kept)
    for a, b in expr_edges:
        if kept_of[a] != kept_of[b]:
            guf.union(kept_of[a], kept_of[b])
    gmembers = {min(m): sorted(m) for m in guf.components().values()}
    # FLAT: each outfit group is its own pose stack (no outfit-under-pose
    # nesting; see build_groups steps 6-7 for the rationale).
    idx = 0
    for gid in sorted(gmembers, key=lambda g: gmembers[g][0]):
        members = gmembers[gid]
        if len(members) == 1:
            sub.unsorted.append(members[0])
            continue
        width = sub.images[members[0]].size[0]
        tallest = max(sub.images[n].size[1] for n in members)
        st = PoseStack(
            id=f"stack_{idx}", size=(width, tallest),
            outfits=[OutfitGroup(id="outfit_0", images=list(members),
                                 label="1")])
        imgs = set(st.all_images())
        st.duplicates = {d: k for d, k in dropped.items() if k in imgs}
        sub.stacks[st.id] = st
        idx += 1
    return sub


def _small_face_box(members: List[str], cache: "_ImageCache",
                    cfg: StackerConfig) -> Optional[Tuple[int, int, int, int]]:
    if len(members) < 2:
        return None
    rep = cache.rgb_small(members[0])
    box = None
    for sibling in members[1:]:
        mask = _diff_mask(rep, cache.rgb_small(sibling),
                          cfg.pixel_diff_threshold, cfg.morph_passes)
        if not mask.any():
            continue
        ys, xs = np.nonzero(mask)
        b = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                     max(box[2], b[2]), max(box[3], b[3]))
    return box


def _small_face_box_for(stack: PoseStack, cache: "_ImageCache",
                        cfg: StackerConfig) -> Optional[Tuple[int, int, int, int]]:
    """Small-scale face box for an assembled stack (across its outfits)."""
    box = None
    for outfit in stack.outfits:
        b = _small_face_box(outfit.images, cache, cfg)
        if b:
            box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]),
                                         max(box[2], b[2]), max(box[3], b[3]))
    return box


def compute_face_box(stack: PoseStack, cache: _ImageCache, cfg: StackerConfig) -> None:
    """Derive the face region from expression-variant diffs (full resolution).

    The union of diff bounding boxes across all expression pairs IS the face
    region; its bottom edge (plus margin) is the suggested chin line.
    """
    boxes: List[Tuple[int, int, int, int]] = []
    for group in stack.outfits:
        if len(group.images) < 2:
            continue
        # Hold the rep locally and stream siblings with direct loads: each
        # image is decoded exactly once and the shared LRU stays untouched,
        # so parallel stacks can't evict each other's data.
        rep = np.asarray(_load_flat(cache.path(group.images[0])), dtype=np.uint8)
        for sibling in group.images[1:]:
            sib = np.asarray(_load_flat(cache.path(sibling)), dtype=np.uint8)
            mask = _diff_mask(rep, sib,
                              cfg.full_res_diff_threshold, cfg.morph_passes)
            if not mask.any():
                continue
            ys, xs = np.nonzero(mask)
            boxes.append((int(xs.min()), int(ys.min()),
                          int(xs.max()) + 1, int(ys.max()) + 1))

    if not boxes:
        stack.face_box = None
        stack.chin_y = None
        return

    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)

    w, h = stack.size
    pad = round(h * cfg.face_box_pad_frac)
    stack.face_box = (max(0, x0 - pad), max(0, y0 - pad),
                      min(w, x1 + pad), min(h, y1 + pad))
    stack.chin_y = min(h, y1 + round(h * cfg.chin_margin_frac))


def apply_accessory_guard(model: GroupsModel, stack: PoseStack,
                          cache: _ImageCache, cfg: StackerConfig) -> None:
    """Split outfits whose changes reach above the chin line into own stacks.

    Compares each outfit rep against the stack's primary rep at full res with
    the face box masked out (reps may show different expressions). Remaining
    diff pixels above (chin_y - guard) mean the outfit alters the head region
    (hat/hood/hair accessory), so shared face slices would break, the outfit
    becomes its own pose stack with its own face box.
    """
    if stack.chin_y is None or len(stack.outfits) < 2:
        return

    primary_rep = cache.rgb_full(stack.outfits[0].images[0])
    fx0, fy0, fx1, fy1 = stack.face_box
    guard_y = max(0, stack.chin_y - cfg.accessory_guard_px)

    peeled: List[OutfitGroup] = []
    for group in stack.outfits[1:]:
        mask = _diff_mask(primary_rep, cache.rgb_full(group.images[0]),
                          cfg.full_res_diff_threshold, cfg.morph_passes)
        mask[fy0:fy1, fx0:fx1] = False
        if mask[:guard_y, :].any():
            peeled.append(group)

    if not peeled:
        return

    stack.outfits = [g for g in stack.outfits if g not in peeled]
    for group in peeled:
        new_id = model.next_stack_id()
        new_stack = PoseStack(
            id=new_id,
            size=stack.size,
            outfits=[OutfitGroup(id="outfit_0", images=list(group.images),
                                 label=group.label)],
            accessory_split_from=stack.id,
        )
        # move any duplicates that belong to the peeled images
        moved = set(group.images)
        new_stack.duplicates = {d: k for d, k in stack.duplicates.items() if k in moved}
        for d in new_stack.duplicates:
            stack.duplicates.pop(d, None)
        compute_face_box(new_stack, cache, cfg)
        if new_stack.chin_y is None:
            new_stack.chin_y = stack.chin_y     # fall back to parent's suggestion
            new_stack.face_box = stack.face_box
        model.stacks[new_id] = new_stack


# ----------------------------------------------------------------------
# CLI test harness (Phase B)
# ----------------------------------------------------------------------
def _cli() -> None:
    import argparse
    import json
    import time

    parser = argparse.ArgumentParser(description="Auto-stacker (test harness)")
    parser.add_argument("folder")
    parser.add_argument("--json", dest="json_out", default=None)
    args = parser.parse_args()

    start = time.time()
    last = {"phase": ""}

    def progress(phase, done, total):
        if phase != last["phase"]:
            print(f"[{phase}] ...", flush=True)
            last["phase"] = phase

    model = build_groups(pathlib.Path(args.folder), progress_cb=progress)
    elapsed = time.time() - start

    print(f"\n{len(model.stacks)} pose stacks, "
          f"{sum(len(s.outfits) for s in model.stacks.values())} outfit groups, "
          f"{sum(s.expression_count() for s in model.stacks.values())} expression images, "
          f"{sum(len(s.duplicates) for s in model.stacks.values())} duplicates folded, "
          f"{len(model.unsorted)} unsorted, {len(model.discarded)} discarded "
          f"({elapsed:.1f}s)\n")

    for sid in sorted(model.stacks, key=lambda s: int(s.split("_")[1])):
        stack = model.stacks[sid]
        origin = f"  (split from {stack.accessory_split_from})" if stack.accessory_split_from else ""
        print(f"{sid}  {stack.size[0]}x{stack.size[1]}  chin_y={stack.chin_y} "
              f"face_box={stack.face_box}{origin}")
        for group in stack.outfits:
            print(f"  {group.id} ({group.label}): {', '.join(group.images)}")
        for d, k in stack.duplicates.items():
            print(f"  dup: {d} -> {k}")
    if model.unsorted:
        print(f"unsorted: {', '.join(model.unsorted)}")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(model.to_dict(), f, indent=2)
        print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    _cli()
