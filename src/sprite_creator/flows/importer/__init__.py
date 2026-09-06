"""
Game Sprite Importer flow.

Crawls a gallery (or imports a local folder) of VN sprite rips, groups the
images into pose/outfit/expression stacks with deterministic pixel math,
lets the user assemble stacks into characters in a review grid, and
finalizes them into Student Transfer character folders.
"""

from typing import Optional

from .state import ImporterState


def run_sprite_importer() -> Optional[ImporterState]:
    """Run the Game Sprite Importer wizard. Returns the final state, or
    None if cancelled."""
    # Imported here to keep module import light (tkinter etc.)
    from ...ui.full_wizard import FullWizard
    from .steps import (SourceStep, CrawlStep, AutoStackStep, ReviewStep,
                        CharacterStep, ImportSummaryStep)

    state = ImporterState()
    wizard = FullWizard(
        state=state,
        title="ST Sprite Creator, Game Sprite Importer",
        loading_subtext="Progress is saved continuously, large galleries "
                        "can take a few minutes.",
        fixed_size=True,
    )
    for step_class in (SourceStep, CrawlStep, AutoStackStep, ReviewStep,
                       CharacterStep, ImportSummaryStep):
        wizard.register_step(step_class)
    return wizard.run()
