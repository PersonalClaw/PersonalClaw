"""What the gateway's start finishes of an app update a stop cut short.

``app_manager.update`` writes a note of the version it is putting live (:func:`write_note`), moves
the live app aside to ``apps/.{name}.rollback``, moves the new version in, hands the old version's
state over, drops the rollback, writes the installed record, and then removes the note. A stop
anywhere in that leaves something for the next start, :func:`recover_interrupted_updates`:

* A rollback with no live app beside it: the stop came between the two moves. The old version is
  put back.
* A rollback beside a live app: the swap completed. The rollback is dropped, once the live version
  has whatever of the app's state the update had not handed over yet.
* A note naming the version now live: the record was never written, so the version shown was the
  old one and a rename the update declared was never moved (the next update compares against the
  files' manifest, which already has the new id). The record is written, each rename moved and
  each dropped id kept as a tombstone, as the update would have. The movers are idempotent, so
  finishing twice moves nothing again.
* A note naming a version that is not live: the update never went live, and the note is dropped.

Its own module so ``app_manager`` stays under the size watch band; it reads the manager's helpers
at call time, so the two import no cycle.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from personalclaw.apps.manager import APP_MANIFEST_FILENAME, _validate_app_name, app_dir, apps_dir
from personalclaw.record_ids import record_path

logger = logging.getLogger(__name__)

#: ``apps/.{name}.update.json``: an update's note of the version it puts live.
NOTE_SUFFIX = ".update.json"


def note_path(name: str) -> Path:
    """Where an update writes its note: the app's name, kebab-checked and contained."""
    return record_path(
        apps_dir(), _validate_app_name(name), prefix=".", suffix=NOTE_SUFFIX, kind="app name"
    )


def write_note(name: str, version: str, tier: str) -> None:
    """Write down, before the swap, the *version* an update puts live, the *tier* it was reviewed
    at and the manifest it replaces, so a stop after the swap is finished at the next start.
    Raises ``AppLifecycleError`` when it cannot be written, so the update stops before anything
    has changed."""
    from personalclaw.apps.app_manager import AppLifecycleError
    from personalclaw.atomic_write import atomic_json_write

    was: Any = None
    try:
        was = json.loads((app_dir(name) / APP_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("app %s: the manifest being replaced cannot be read", name, exc_info=True)
    try:
        atomic_json_write(note_path(name), {"version": version, "tier": tier, "was": was})
    except OSError as exc:
        raise AppLifecycleError(f"could not write the update down before its swap: {exc}") from exc


def drop_note(name: str) -> None:
    try:
        note_path(name).unlink(missing_ok=True)
    except OSError:
        logger.warning("app %s: could not remove the update's note", name, exc_info=True)


def recover_interrupted_updates() -> list[str]:
    """Finish what a stop left of each app update (called at startup). Returns the names whose old
    version was put back."""
    from personalclaw.apps import app_manager as am

    recovered: list[str] = []
    root = apps_dir()
    if not root.is_dir():
        return recovered
    for entry in root.iterdir():
        if not (
            entry.is_dir()
            and entry.name.startswith(".")
            and entry.name.endswith(am._ROLLBACK_SUFFIX)
        ):
            continue
        name = entry.name[1 : -len(am._ROLLBACK_SUFFIX)]
        live = app_dir(name)
        try:
            if not live.exists() or not any(live.iterdir()):
                # Crash between "move live→rollback" and "move new→live": restore.
                if live.exists():
                    shutil.rmtree(live, ignore_errors=True)
                shutil.move(str(entry), str(live))
                recovered.append(name)
                am._audit("update_recover", "restored", name)
            else:
                carried = am._drop_stale_rollback(entry, live)
                am._audit(
                    "update_recover",
                    "dropped_stale",
                    name,
                    detail=f"carried={','.join(carried)}" if carried else "",
                )
        except OSError:
            logger.warning("failed to reconcile rollback dir %s", entry, exc_info=True)
    finish_noted_updates()
    return recovered


def finish_noted_updates() -> list[str]:
    """Finish each noted update whose version is live, and drop each note whose version is not.
    Returns the names finished."""
    from personalclaw.apps import app_manager as am
    from personalclaw.apps.manifest import AppManifest

    finished: list[str] = []
    root = apps_dir()
    if not root.is_dir():
        return finished
    for note in sorted(root.glob(f".*{NOTE_SUFFIX}")):
        name = note.name[1 : -len(NOTE_SUFFIX)]
        try:
            _validate_app_name(name)
        except ValueError:
            # No app could have written it: dropped, or it would warn at every start.
            logger.warning("dropped an update note that names no app: %s", note.name)
            note.unlink(missing_ok=True)
            continue
        try:
            data = json.loads(note.read_text(encoding="utf-8"))
            version, tier = str(data["version"]), str(data["tier"])
        except OSError:
            logger.warning("could not read the update noted at %s", note, exc_info=True)
            continue
        except (ValueError, KeyError, TypeError):
            # Not a note this core wrote: nothing can be finished from it, so it would stay forever.
            logger.warning("dropped an update note that cannot be read as one: %s", note.name)
            note.unlink(missing_ok=True)
            continue
        try:
            live = am._manifest_of(name)
            if live is not None and live.version == version:
                was_data = data.get("was")
                was = AppManifest.from_dict(was_data) if isinstance(was_data, dict) else None
                am._record_update(name, was, live, tier)
                finished.append(name)
                am._audit("update_recover", "recorded", name, detail=f"version={version}")
            else:
                am._audit("update_recover", "never_live", name, detail=f"version={version}")
        except Exception:  # noqa: BLE001 — one app's note must not stop the others' at start
            logger.warning("failed to finish the update noted at %s", note, exc_info=True)
            continue
        note.unlink(missing_ok=True)
    return finished
