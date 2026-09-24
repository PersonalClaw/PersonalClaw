"""Persistence for the template pipeline's state (UP-R9, WF2UNI-7).

`template_pipeline` is deliberately PURE — it decides, it does not remember. That split is what
makes the mining and anti-nag rules testable without a home directory, and it is also why the
module sat inert: a decision layer with no state writer never runs in production. This module is
that writer, and it is the whole of it.

Two pieces of state, both file-backed under ``workflows/``:

* **`NudgeState` per shape.** The anti-nag rules are only rules if the state outlives the process.
  A cooldown held in memory means every gateway restart re-offers a nudge the user just declined,
  which is precisely the nagging `should_nudge` exists to prevent — and a user who mutes a nagging
  nudge loses the useful ones too. A DECLINE in particular must be permanent for the shape.
* **`Candidate` templates.** Discover-then-freeze only stops plan drift if the frozen candidate is
  still there on the next similar intent. A candidate that lived in memory would mean two runs of
  the same request generate two different graphs, which is the failure mode the freeze exists to
  close.

Both stores are read-mostly and tiny (one JSON object each), so they are read fresh on every access
rather than cached: a cache would have to be invalidated by a writer in another process, and the
saved read is a few microseconds against a decision that is about to make a model call.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write
from personalclaw.workflows.store import workflows_dir
from personalclaw.workflows.template_pipeline import Candidate, NudgeState

logger = logging.getLogger(__name__)

_NUDGES_FILE = "template_nudges.json"
_CANDIDATES_FILE = "template_candidates.json"

#: The nudge clock's key inside the nudges object. Not a valid shape (shapes are user-facing prose),
#: so it cannot collide with one.
_TURN_KEY = "__turn__"


def _path(filename: str) -> Path:
    """Resolve a store path under the LIVE home.

    Resolved per call, never bound at import: `workflows_dir()` reads `config_dir()`, and a path
    captured at import time would point at whatever home was configured when this module was first
    imported — the real one, in any test that sets `PERSONALCLAW_HOME` afterwards.
    """
    return workflows_dir() / filename


def _read(filename: str) -> dict[str, Any]:
    """The stored object, or ``{}``. Never raises.

    A corrupt or truncated file reads as empty rather than propagating: this state is an
    optimization over "ask again", and a nudge store that raised would take down the chat turn it
    was supposed to decorate.
    """
    path = _path(filename)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(filename: str, data: dict[str, Any]) -> None:
    path = _path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True))


# ── nudge state ──────────────────────────────────────────────────────────────


def load_nudge(shape: str) -> NudgeState:
    """The persisted state for *shape*, or a fresh one.

    A fresh state for an unknown shape rather than None: "never seen" and "seen zero times" are the
    same fact here, and returning None would push that branch into every caller.
    """
    entry = _read(_NUDGES_FILE).get(shape)
    if not isinstance(entry, dict):
        return NudgeState(shape=shape)
    return NudgeState(
        shape=shape,
        occurrences=int(entry.get("occurrences") or 0),
        declined=bool(entry.get("declined")),
        last_offered_turn=int(entry.get("last_offered_turn", -1)),
        accepted=bool(entry.get("accepted")),
    )


def save_nudge(state: NudgeState) -> None:
    """Persist one shape's state, leaving every other shape untouched.

    Read-modify-write on the whole object, because a decline for one shape must not drop the
    occurrence counts for the others — "no, not for this" is per-shape by design.
    """
    if not state.shape:
        return
    data = _read(_NUDGES_FILE)
    data[state.shape] = state.to_dict()
    _write(_NUDGES_FILE, data)


def all_nudges() -> list[NudgeState]:
    """Every persisted nudge state, for the inspection surface."""
    return [load_nudge(shape) for shape in sorted(_read(_NUDGES_FILE)) if shape != _TURN_KEY]


def bump_turn() -> int:
    """Advance and return the nudge clock — the `turn` `should_nudge` measures cooldown against.

    A counter of NUDGE CONSIDERATIONS, not of chat turns. There is no per-session turn index a
    stdio MCP tool can read (it sees one call, not a conversation), and inventing one would mean
    reading and counting the transcript on every call. Counting considerations is the honest
    available clock: it advances once per `suggest_template` call, so `NUDGE_COOLDOWN` means "that
    many further shape observations", which is the same shape of guarantee — a decline is not
    re-offered until real activity has passed — and it survives restarts, which an in-memory turn
    index would not.

    Stored beside the per-shape entries under a key no shape can collide with: shapes are user
    prose and this key is not a valid one.
    """
    data = _read(_NUDGES_FILE)
    current = data.get(_TURN_KEY)
    turn = (int(current) if isinstance(current, (int, float)) else 0) + 1
    data[_TURN_KEY] = turn
    _write(_NUDGES_FILE, data)
    return turn


def current_turn() -> int:
    """The nudge clock without advancing it."""
    current = _read(_NUDGES_FILE).get(_TURN_KEY)
    return int(current) if isinstance(current, (int, float)) else 0


# ── candidates ───────────────────────────────────────────────────────────────


def _as_candidate(entry: dict[str, Any]) -> Candidate | None:
    if not isinstance(entry, dict) or not entry.get("name"):
        return None
    spec = entry.get("spec")
    return Candidate(
        name=str(entry["name"]),
        spec=spec if isinstance(spec, dict) else {},
        origin_goal=str(entry.get("origin_goal") or ""),
        scope=str(entry.get("scope") or "session"),
        reuses=int(entry.get("reuses") or 0),
        session_id=str(entry.get("session_id") or ""),
    )


def save_candidate(candidate: Candidate) -> None:
    """Persist a frozen candidate, keyed by name.

    Keyed by NAME, which `_candidate_name` derives deterministically from the goal — so a second
    generation of the same request updates the candidate in place instead of leaving a
    near-duplicate beside it for the matcher to pick between.
    """
    if not candidate.name:
        return
    data = _read(_CANDIDATES_FILE)
    data[candidate.name] = candidate.to_dict()
    _write(_CANDIDATES_FILE, data)


def load_candidates(*, session_id: str = "") -> list[Candidate]:
    """Persisted candidates, newest-name-first-sorted for determinism.

    When *session_id* is given, SESSION-scoped candidates are filtered to that session and
    higher-scoped ones always come along: a candidate promoted to agent/workspace/global scope has
    earned visibility outside the session that froze it, while a session-scoped one is that
    session's private guess and must not leak into another conversation's matches.
    """
    data = _read(_CANDIDATES_FILE)
    out: list[Candidate] = []
    for name in sorted(data):
        candidate = _as_candidate(data.get(name) or {})
        if candidate is None:
            continue
        if candidate.scope == "session" and session_id and candidate.session_id != session_id:
            continue
        out.append(candidate)
    return out


def get_candidate(name: str) -> Candidate | None:
    """One candidate by name, or None."""
    if not name:
        return None
    return _as_candidate(_read(_CANDIDATES_FILE).get(name) or {})
