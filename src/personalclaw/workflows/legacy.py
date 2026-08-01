"""Archive the pre-v2 SOP files so an upgrade doesn't look like data loss.

The old feature stored one `<name>/WORKFLOW.md` directory per SOP under
`~/.personalclaw/workflows/`. Those files are the user's own writing, and the v2 engine
cannot read them — its definitions are graph specs, not ordered checklists. Deleting
them would be the wrong call, and leaving them in place is worse than it sounds: Slice 0
puts the v2 def store in the same parent directory, so a stray `WORKFLOW.md` tree would
sit alongside real definitions looking like something the engine ignores for no reason.

So they move once, to `workflows/_legacy_sops/`, and the leading underscore keeps them
out of the def store's scan. Nothing reads them again — they are there so the user can.

Idempotent by inspection rather than by a schema version (the house pattern, and there
is no lifecycle migration runner): a directory containing a `WORKFLOW.md` is legacy, and
an already-archived tree has none left to find.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_ARCHIVE_DIRNAME = "_legacy_sops"
_SOP_FILENAME = "WORKFLOW.md"


def archive_legacy_sops(workflows_dir: Path) -> list[str]:
    """Move any pre-v2 SOP dirs into ``_legacy_sops/``. Returns the names moved.

    Never raises: this runs at startup, and a permissions problem on one stale
    directory must not stop the gateway from booting.
    """
    if not workflows_dir.is_dir():
        return []

    archive = workflows_dir / _ARCHIVE_DIRNAME
    moved: list[str] = []

    for entry in sorted(workflows_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        if not (entry / _SOP_FILENAME).is_file():
            continue  # not a legacy SOP — leave it alone
        try:
            archive.mkdir(parents=True, exist_ok=True)
            target = archive / entry.name
            if target.exists():
                # A previous run archived this name; keep both rather than clobber
                # the user's file. Suffix until free — bounded, names are few.
                for n in range(2, 100):
                    candidate = archive / f"{entry.name}-{n}"
                    if not candidate.exists():
                        target = candidate
                        break
                else:
                    logger.warning("legacy SOP %s: too many archived copies, skipped", entry.name)
                    continue
            shutil.move(str(entry), str(target))
            moved.append(entry.name)
        except OSError:
            logger.warning("Could not archive legacy SOP %s", entry.name, exc_info=True)

    if moved:
        logger.info(
            "Archived %d pre-v2 workflow SOP(s) to %s/: %s",
            len(moved),
            _ARCHIVE_DIRNAME,
            ", ".join(moved),
        )
    return moved
