"""
Workspace management for the Game Sprite Importer.

One workspace folder per imported game under IMPORTS_BASE_DIR:

    <slug>/
      import.json    metadata + progress (this module owns it)
      raw/           downloaded/copied source images, never modified
      thumbs/        cached review-grid thumbnails
      groups.json    serialized GroupsModel (the sort state)
      output/        finalized ST character folders

groups.json is written atomically (temp file + os.replace) after every
mutation, so a crash or quit never loses more than the in-flight edit.
"""

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlparse

from ...config import IMPORTS_BASE_DIR
from .state import GroupsModel, ImporterState

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def slugify(name: str) -> str:
    """Make a filesystem-safe folder slug from a game name."""
    slug = re.sub(r"[^\w\- ]+", "", name).strip().lower()
    slug = re.sub(r"[\s_]+", "_", slug)
    return slug or "untitled"


def raw_dir(workspace: Path) -> Path:
    return workspace / "raw"


def output_dir(workspace: Path) -> Path:
    return workspace / "output"


def thumbs_dir(workspace: Path) -> Path:
    return workspace / "thumbs"


def stacker_cache_dir(workspace: Path) -> Path:
    """Persisted comparison arrays, lets re-sorts skip image decoding."""
    return workspace / "cache"


@dataclass
class ImportSummary:
    """One row in the SourceStep resume list."""
    workspace: Path
    game_name: str
    source_mode: str
    stage: str            # human-readable progress description
    raw_images: int
    updated_at: str


def _unique_workspace_dir(slug: str) -> Path:
    base = IMPORTS_BASE_DIR / slug
    if not base.exists():
        return base
    i = 2
    while (IMPORTS_BASE_DIR / f"{slug}_{i}").exists():
        i += 1
    return IMPORTS_BASE_DIR / f"{slug}_{i}"


def create_import(
    state: ImporterState,
    game_name: str,
    source_mode: str,
    source_url: str = "",
    local_source_dir: Optional[Path] = None,
    copy_progress_cb: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Create a new workspace and populate the state object.

    For local mode, copies images from local_source_dir into raw/ (flat,
    original filenames, collisions get a numeric suffix) and writes a
    synthetic download_meta.json so downstream code has one metadata path.
    """
    slug = slugify(game_name)
    workspace = _unique_workspace_dir(slug)
    raw = raw_dir(workspace)
    raw.mkdir(parents=True, exist_ok=True)
    output_dir(workspace).mkdir(parents=True, exist_ok=True)
    thumbs_dir(workspace).mkdir(parents=True, exist_ok=True)

    state.workspace = workspace
    state.game_name = game_name
    state.game_slug = workspace.name
    state.source_mode = source_mode
    state.source_url = source_url
    state.local_source_dir = local_source_dir
    state.resuming = False
    state.crawl_complete = False
    state.groups = None

    if source_mode == "local" and local_source_dir is not None:
        _copy_local_images(local_source_dir, raw, copy_progress_cb)
        state.crawl_complete = True
        meta = {
            "source_game": game_name,
            "start_url": "",
            "created_at_unix": int(time.time()),
        }
        with (raw / "download_meta.json").open("w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    save_import_meta(state)
    _record_source(state, {
        "mode": source_mode,
        "start_url": source_url,
        "dir": str(local_source_dir) if local_source_dir else "",
        "prefix": "",
    })


def _copy_local_images(
    src: Path, dest: Path, progress_cb: Optional[Callable[[int, int], None]]
) -> int:
    files = sorted(
        p for p in src.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )
    for i, f in enumerate(files):
        target = dest / f.name
        if target.exists():
            k = 1
            while (dest / f"{f.stem}_{k}{f.suffix}").exists():
                k += 1
            target = dest / f"{f.stem}_{k}{f.suffix}"
        shutil.copy2(f, target)
        if progress_cb:
            progress_cb(i + 1, len(files))
    return len(files)


def count_raw_images(workspace: Path) -> int:
    raw = raw_dir(workspace)
    if not raw.is_dir():
        return 0
    return sum(
        1 for p in raw.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )


def list_raw_images(workspace: Path) -> List[str]:
    raw = raw_dir(workspace)
    if not raw.is_dir():
        return []
    return sorted(p.name for p in raw.iterdir()
                  if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def add_local_source(
    state: ImporterState,
    local_source_dir: Path,
    copy_progress_cb: Optional[Callable[[int, int], None]] = None,
) -> List[str]:
    """Copy a second folder of images into an existing workspace's raw/,
    prefixed so filenames can't collide with earlier sources. Returns the
    list of newly added filenames."""
    raw = raw_dir(state.workspace)
    prefix = _next_source_prefix(state.workspace)
    files = sorted(
        p for p in local_source_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )
    added = []
    for i, f in enumerate(files):
        target = raw / f"{prefix}{f.name}"
        k = 1
        while target.exists():
            target = raw / f"{prefix}{f.stem}_{k}{f.suffix}"
            k += 1
        shutil.copy2(f, target)
        added.append(target.name)
        if copy_progress_cb:
            copy_progress_cb(i + 1, len(files))
    _record_source(state, {"mode": "local", "dir": str(local_source_dir),
                           "prefix": prefix, "added": len(added)})
    return added


def source_prefix_for_crawl(workspace: Path) -> str:
    """Filename prefix for a follow-up crawl into an existing workspace."""
    return _next_source_prefix(workspace)


def _next_source_prefix(workspace: Path) -> str:
    """s2_, s3_, … based on how many sources already exist. The first
    source uses no prefix (bare 00001.webp), matching single-source
    workspaces."""
    meta_path = workspace / "import.json"
    n = 1
    if meta_path.is_file():
        try:
            with meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)
            n = len(meta.get("sources", [])) + 1
        except (json.JSONDecodeError, IOError):
            pass
    return "" if n <= 1 else f"s{n}_"


def _record_source(state: ImporterState, source: dict) -> None:
    meta_path = state.workspace / "import.json"
    meta = {}
    if meta_path.is_file():
        try:
            with meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, IOError):
            meta = {}
    meta.setdefault("sources", []).append(source)
    _atomic_write_json(meta_path, meta)


def _stage_description(meta: dict) -> str:
    progress = meta.get("progress", {})
    total = progress.get("total_piles", 0)
    done = progress.get("finalized_piles", 0)
    if total and done >= total:
        return "complete"
    if done:
        return f"finalizing ({done}/{total} characters)"
    if progress.get("review") == "in_progress":
        return "sorting"
    if progress.get("stack") == "done":
        return "sorting"
    if progress.get("crawl") == "done":
        return "downloaded"
    return "downloading"


def save_import_meta(state: ImporterState) -> None:
    """Write import.json from the current state."""
    if state.workspace is None:
        return
    meta_path = state.workspace / "import.json"
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    meta = {}
    if meta_path.exists():
        try:
            with meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f) or {}
        except (json.JSONDecodeError, IOError):
            meta = {}

    groups = state.groups
    total_piles = len(groups.piles) if groups else 0
    finalized = (
        sum(1 for p in groups.piles.values() if p.finalize.done) if groups else 0
    )

    meta.update({
        "version": 1,
        "game_name": state.game_name,
        "game_slug": state.game_slug,
        "source": {
            "mode": state.source_mode,
            "start_url": state.source_url,
            "netloc": urlparse(state.source_url).netloc if state.source_url else "",
        },
        "created_at": meta.get("created_at", now),
        "updated_at": now,
        "progress": {
            "crawl": "done" if state.crawl_complete else "in_progress",
            "stack": "done" if state.groups is not None else "pending",
            "review": "in_progress" if (groups and groups.piles) else "pending",
            "finalized_piles": finalized,
            "total_piles": total_piles,
        },
        "counts": {"raw_images": count_raw_images(state.workspace)},
    })

    _atomic_write_json(meta_path, meta)


def load_import(workspace: Path, state: ImporterState) -> None:
    """Populate the state object from an existing workspace."""
    meta_path = workspace / "import.json"
    with meta_path.open("r", encoding="utf-8") as f:
        meta = json.load(f)

    state.workspace = workspace
    state.game_name = meta.get("game_name", workspace.name)
    state.game_slug = meta.get("game_slug", workspace.name)
    source = meta.get("source", {})
    state.source_mode = source.get("mode", "local")
    state.source_url = source.get("start_url", "")
    state.resuming = True
    state.crawl_complete = meta.get("progress", {}).get("crawl") == "done"
    state.groups = load_groups(workspace)


def list_imports() -> List[ImportSummary]:
    """Scan IMPORTS_BASE_DIR for resumable workspaces, newest first."""
    if not IMPORTS_BASE_DIR.is_dir():
        return []
    summaries = []
    for ws in sorted(IMPORTS_BASE_DIR.iterdir()):
        meta_path = ws / "import.json"
        if not meta_path.is_file():
            continue
        try:
            with meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue
        summaries.append(ImportSummary(
            workspace=ws,
            game_name=meta.get("game_name", ws.name),
            source_mode=meta.get("source", {}).get("mode", "?"),
            stage=_stage_description(meta),
            raw_images=meta.get("counts", {}).get("raw_images", 0),
            updated_at=meta.get("updated_at", ""),
        ))
    summaries.sort(key=lambda s: s.updated_at, reverse=True)
    return summaries


def delete_import(workspace: Path) -> None:
    """Delete an import workspace entirely."""
    if workspace.is_dir() and (workspace / "import.json").is_file():
        shutil.rmtree(workspace)


def save_groups(state: ImporterState) -> None:
    """Atomically persist the GroupsModel to groups.json."""
    if state.workspace is None or state.groups is None:
        return
    _atomic_write_json(state.workspace / "groups.json", state.groups.to_dict())
    save_import_meta(state)


def load_groups(workspace: Path) -> Optional[GroupsModel]:
    groups_path = workspace / "groups.json"
    if not groups_path.is_file():
        return None
    try:
        with groups_path.open("r", encoding="utf-8") as f:
            return GroupsModel.from_dict(json.load(f))
    except (json.JSONDecodeError, IOError, KeyError):
        return None


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
