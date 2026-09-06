"""
State model for the Game Sprite Importer flow.

Two layers:
- GroupsModel and its children mirror groups.json exactly (the persistent
  sort state: stacks, outfit groups, character piles, discards).
- ImporterState is the live wizard state object passed to every step.

Images are always referenced by their filename within the workspace raw/
directory; nothing in the model stores absolute paths, so a workspace folder
can be moved or synced between machines.
"""

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class ImageRecord:
    """Per-image facts computed once during auto-stacking."""
    name: str                              # filename within raw/ (canonical ID)
    size: Tuple[int, int] = (0, 0)         # (width, height)
    dhash: int = 0                         # 64-bit difference hash

    def to_dict(self) -> dict:
        return {"size": list(self.size), "dhash": f"{self.dhash:016x}"}

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "ImageRecord":
        return cls(
            name=name,
            size=tuple(d.get("size", (0, 0))),
            dhash=int(d.get("dhash", "0"), 16),
        )


@dataclass
class OutfitGroup:
    """One outfit within a pose stack: a set of expression variant images."""
    id: str                                # "outfit_0"
    images: List[str] = field(default_factory=list)  # [0] is the representative
    label: str = ""                        # user-editable display label

    def to_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "images": list(self.images)}

    @classmethod
    def from_dict(cls, d: dict) -> "OutfitGroup":
        return cls(id=d["id"], images=list(d.get("images", [])),
                   label=d.get("label", ""))


@dataclass
class PoseStack:
    """One character-pose: outfit groups sharing an identical base render."""
    id: str                                # "stack_0"
    size: Tuple[int, int] = (0, 0)
    outfits: List[OutfitGroup] = field(default_factory=list)
    face_box: Optional[Tuple[int, int, int, int]] = None  # full-res (x0,y0,x1,y1)
    chin_y: Optional[int] = None           # suggested chin line (full-res y)
    duplicates: Dict[str, str] = field(default_factory=dict)  # dropped -> kept
    accessory_split_from: Optional[str] = None  # parent stack id if auto-split

    @property
    def rep_image(self) -> Optional[str]:
        if self.outfits and self.outfits[0].images:
            return self.outfits[0].images[0]
        return None

    def all_images(self) -> List[str]:
        return [name for group in self.outfits for name in group.images]

    def expression_count(self) -> int:
        return sum(len(group.images) for group in self.outfits)

    def to_dict(self) -> dict:
        return {
            "size": list(self.size),
            "outfits": [g.to_dict() for g in self.outfits],
            "face_box": list(self.face_box) if self.face_box else None,
            "chin_y": self.chin_y,
            "duplicates": dict(self.duplicates),
            "accessory_split_from": self.accessory_split_from,
        }

    @classmethod
    def from_dict(cls, stack_id: str, d: dict) -> "PoseStack":
        face_box = d.get("face_box")
        return cls(
            id=stack_id,
            size=tuple(d.get("size", (0, 0))),
            outfits=[OutfitGroup.from_dict(g) for g in d.get("outfits", [])],
            face_box=tuple(face_box) if face_box else None,
            chin_y=d.get("chin_y"),
            duplicates=dict(d.get("duplicates", {})),
            accessory_split_from=d.get("accessory_split_from"),
        )


@dataclass
class PileFinalize:
    """Per-character finalization inputs and results."""
    mid_thigh_y: Dict[str, int] = field(default_factory=dict)  # stack_id -> y
    chin_y: Dict[str, int] = field(default_factory=dict)       # stack_id -> y
    pose_scale: Dict[str, float] = field(default_factory=dict)  # stack_id -> normalization factor
    eye_line_ratio: Optional[float] = None
    name_color: Optional[str] = None
    scale: float = 1.0
    display_name: str = ""
    voice: str = "girl"
    done: bool = False
    output_folder: Optional[str] = None    # relative to workspace
    verification: Optional[dict] = None    # recomposite report

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PileFinalize":
        return cls(
            mid_thigh_y=dict(d.get("mid_thigh_y", {})),
            chin_y=dict(d.get("chin_y", {})),
            pose_scale={k: float(v) for k, v in d.get("pose_scale", {}).items()},
            eye_line_ratio=d.get("eye_line_ratio"),
            name_color=d.get("name_color"),
            scale=d.get("scale", 1.0),
            display_name=d.get("display_name", ""),
            voice=d.get("voice", "girl"),
            done=d.get("done", False),
            output_folder=d.get("output_folder"),
            verification=d.get("verification"),
        )


@dataclass
class CharacterPile:
    """A user-built group of pose stacks that form one character."""
    id: str                                # "pile_0"
    name: str = ""                         # working label shown in the grid
    stack_ids: List[str] = field(default_factory=list)
    finalize: PileFinalize = field(default_factory=PileFinalize)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "stack_ids": list(self.stack_ids),
            "finalize": self.finalize.to_dict(),
        }

    @classmethod
    def from_dict(cls, pile_id: str, d: dict) -> "CharacterPile":
        return cls(
            id=pile_id,
            name=d.get("name", ""),
            stack_ids=list(d.get("stack_ids", [])),
            finalize=PileFinalize.from_dict(d.get("finalize", {})),
        )


@dataclass
class GroupsModel:
    """The complete sort state, persisted as groups.json."""
    version: int = 1
    stacker_config: dict = field(default_factory=dict)
    images: Dict[str, ImageRecord] = field(default_factory=dict)
    stacks: Dict[str, PoseStack] = field(default_factory=dict)
    piles: Dict[str, CharacterPile] = field(default_factory=dict)
    unsorted: List[str] = field(default_factory=list)
    discarded: List[str] = field(default_factory=list)

    def next_stack_id(self) -> str:
        return _next_id(self.stacks, "stack_")

    def next_pile_id(self) -> str:
        return _next_id(self.piles, "pile_")

    def pile_for_stack(self, stack_id: str) -> Optional[CharacterPile]:
        for pile in self.piles.values():
            if stack_id in pile.stack_ids:
                return pile
        return None

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "stacker_config": self.stacker_config,
            "images": {name: rec.to_dict() for name, rec in self.images.items()},
            "stacks": {sid: s.to_dict() for sid, s in self.stacks.items()},
            "piles": {pid: p.to_dict() for pid, p in self.piles.items()},
            "unsorted": list(self.unsorted),
            "discarded": list(self.discarded),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GroupsModel":
        return cls(
            version=d.get("version", 1),
            stacker_config=dict(d.get("stacker_config", {})),
            images={name: ImageRecord.from_dict(name, rec)
                    for name, rec in d.get("images", {}).items()},
            stacks={sid: PoseStack.from_dict(sid, s)
                    for sid, s in d.get("stacks", {}).items()},
            piles={pid: CharacterPile.from_dict(pid, p)
                   for pid, p in d.get("piles", {}).items()},
            unsorted=list(d.get("unsorted", [])),
            discarded=list(d.get("discarded", [])),
        )


def _next_id(existing: dict, prefix: str) -> str:
    n = 0
    while f"{prefix}{n}" in existing:
        n += 1
    return f"{prefix}{n}"


@dataclass
class ImporterState:
    """Live wizard state for the importer flow (not persisted directly;
    workspace.py mirrors the relevant parts into import.json/groups.json)."""
    # Identity / workspace
    workspace: Optional[Path] = None       # ~/.sprite_creator/imports/<slug>
    game_name: str = ""
    game_slug: str = ""
    source_mode: str = ""                  # "crawl" | "local"
    source_url: str = ""
    local_source_dir: Optional[Path] = None
    resuming: bool = False

    # Progress flags (mirrored into import.json)
    crawl_complete: bool = False
    stack_complete: bool = False

    # Pending additional source (multi-post galleries): when set, CrawlStep
    # crawls it with the given prefix and AutoStackStep integrates the new
    # images into the existing model instead of re-sorting.
    pending_source_url: str = ""
    pending_source_prefix: str = ""
    pending_new_names: List[str] = field(default_factory=list)

    # Redo-from-Summary: when set, the Review step focuses on this one pile
    # (split/merge only) and the Finalize step rebuilds just it, then returns
    # to Summary. Not persisted (transient UI routing only).
    redo_focus_pile: Optional[str] = None

    # Data
    groups: Optional[GroupsModel] = None

    # Results
    finalized_folders: List[Path] = field(default_factory=list)

    @property
    def raw_dir(self) -> Optional[Path]:
        return self.workspace / "raw" if self.workspace else None

    @property
    def output_dir(self) -> Optional[Path]:
        return self.workspace / "output" if self.workspace else None
