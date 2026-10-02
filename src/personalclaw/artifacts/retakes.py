"""Regenerating an answer retakes the images it made: each lands as that image's next version.

Regenerate replays the turn, and the model generates its image again from a prompt it rewrites,
so ``image_generate`` would file the retake under a new name — a second image at v1 beside the
first, where the user asked for another take of THIS image. So before the replay starts, the
regenerate route records which images the replaced answer made (:func:`open_retakes`), and
``image_generate`` takes them in order (:func:`take_retake`): a generation in the replayed turn
becomes the next version of the image it replaces, and one past the last is a new image. The
route closes the record when the replayed turn ends (:func:`close_retakes`), however it ends.

**A file under the home, not process memory.** An agent that runs in its own process reaches
``image_generate`` through the tool server, which is a process of its own, so a record held in
the gateway's memory would be invisible to it. The record names the gateway process that opened
it, so one left behind by a gateway that stopped mid-regenerate is never taken by a later turn.
It is scratch for one turn, not state: nothing restores it, and a snapshot does not carry it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader
from personalclaw.gateway_base import pid_is_alive

logger = logging.getLogger(__name__)

#: The directory under the home the records live in, one file per session.
RETAKES_DIR = "retakes"

_LOCK = threading.Lock()


def _path(session_key: str) -> Path:
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:32]
    return config_loader.config_dir() / RETAKES_DIR / f"{digest}.json"


def open_retakes(session_key: str, slugs: list[str]) -> None:
    """Record that the turn now replaying in *session_key* retakes *slugs*, in order."""
    if not session_key:
        return
    path = _path(session_key)
    with _LOCK:
        if not slugs:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, json.dumps({"pid": os.getpid(), "slugs": list(slugs)}))


def take_retake(session_key: str, named: str = "") -> str:
    """The next image the replayed turn in *session_key* retakes, or ``""``. Each is taken once.

    A call that names the image it versions (*named*) takes that one wherever the record lists it,
    and ``""`` when the record does not: a later call in the turn that names no image would
    otherwise take it as well, and land a second take on it in place of the next image's.

    Reads tolerate a missing or unreadable record (no retake). A record whose gateway is gone
    is removed rather than taken.
    """
    if not session_key:
        return ""
    path = _path(session_key)
    with _LOCK:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ""
        except (OSError, ValueError):
            logger.warning("retake record for a session is unreadable; generating a new image")
            path.unlink(missing_ok=True)
            return ""
        if not isinstance(record, dict):
            record = {}
        pid, listed = record.get("pid"), record.get("slugs")
        slugs = [s for s in listed if isinstance(s, str) and s] if isinstance(listed, list) else []
        if not (isinstance(pid, int) and pid_is_alive(pid)) or not slugs:
            path.unlink(missing_ok=True)
            return ""
        if named and named not in slugs:
            return ""
        slug = named or slugs[0]
        rest = list(slugs)
        rest.remove(slug)
        if rest:
            atomic_write(path, json.dumps({**record, "slugs": rest}))
        else:
            path.unlink(missing_ok=True)
        return slug


def close_retakes(session_key: str) -> None:
    """The replayed turn in *session_key* ended: nothing is left to retake."""
    if not session_key:
        return
    with _LOCK:
        _path(session_key).unlink(missing_ok=True)
