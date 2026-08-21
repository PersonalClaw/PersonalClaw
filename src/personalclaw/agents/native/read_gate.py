"""The pre-edit read gate (AG-14): a modifying write is admitted only when the target's
CURRENT content was actually OBSERVED by the model first.

**The failure this closes.** An edit computed against a stale or imagined version of a
file silently reverts someone else's change: the tool reports "Edited", the diff looks
plausible, and a concurrent write is gone. So the gate is closed, not advisory — a write
that cannot prove observation is *refused*, never applied optimistically.

**Why "observed", not "a read tool was called".** A call-count check ("did this turn call
``read_file``?") is exactly the shape that makes a gate look enforced while admitting the
write it was built to stop: a read of a *different* file satisfies it, and so does a read
whose result was truncated long before the region being edited. This ledger therefore
records *the bytes that were actually handed to the model* — the projected tool output —
and the gate asks whether the specific region being changed lies inside it.

Three checks, all on content:

1. **Observed at all** — an :class:`Observation` exists for that resolved path.
2. **Still current** — the sha256 of the file's *full* bytes at read time equals its
   sha256 now. This is stronger than any turn window: a read taken one tool call ago is
   already stale if another process wrote in between, and this catches that.
3. **Covers the region** — for an ``edit``, the ``old_str`` occurs in an observed
   fragment; for an ``overwrite`` of an existing file, the observation must be
   *complete* (an overwrite's "region" is the whole file, so a partial read licenses
   nothing).

**Truncation is honoured, twice.** ``read_file`` caps at ``_MAX_READ_BYTES`` bytes and
then projects to ``DEFAULT_TOOL_OUTPUT_CAP`` chars, so a large file's middle is not
observed by one read. Because the projection retains the raw and names
``tool_result_get`` as the recovery affordance, a slice pulled through that affordance is
recorded as an additional observed fragment (:func:`record_retrieval`) — so the refusal's
next action actually works instead of being a dead end.

**Create-new is not gated.** Writing a path that does not exist destroys nothing, so it
needs no prior read. Overwriting a path that does exist is an edit in every respect that
matters and gets the same gate.

The gate itself is expressed ONCE, at ``BuiltinTools.invoke`` — neither ``write_file``
nor ``edit_file`` re-implements it, and ``tests/test_pre_edit_read_gate.py`` fails if a
write path appears that the seam does not cover.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: How long an observation stays usable. The substantive check is content-currency
#: (check 2), so this is a context bound, not a correctness one: past it the model has
#: plausibly compacted the text out of its context, making the "observation" imagined.
OBSERVATION_TTL_SECS = 3600.0

#: Per-session path cap. Reading thousands of files must not grow the ledger without
#: bound; the oldest observations are dropped first (they are the least likely to be
#: edited next, and dropping one only costs a re-read).
MAX_PATHS_PER_SESSION = 512

#: Sessionless callers (a directly-constructed provider, the ``/api/tools`` catalog
#: probe) all share this bucket. The gate still holds for them — it is keyed on content,
#: not on identity — they just cannot be isolated from each other.
_NO_SESSION = "\x00no-session"


@dataclass(frozen=True)
class Observation:
    """What the model actually saw for one path.

    ``fragments`` are the exact output strings handed to the model (the first read's
    projected text, plus any ``tool_result_get`` slices of the same read). ``complete``
    is True only when nothing was dropped on either truncation axis — the byte cap and
    the output projection — so it means "fragments[0] IS the file".
    """

    content_sha256: str
    complete: bool
    at: float
    fragments: tuple[str, ...] = field(default=())

    def expired(self, *, now: float | None = None) -> bool:
        return (now or time.time()) - self.at > OBSERVATION_TTL_SECS

    def covers(self, text: str) -> bool:
        """Whether ``text`` occurs in something the model was actually shown."""
        return any(text in frag for frag in self.fragments)


@dataclass(frozen=True)
class Refusal:
    """A closed refusal, phrased as the next action the model should take.

    ``reason`` is a stable machine tag for tests/telemetry; ``error`` and ``hint`` are
    the model-facing text and both name the path and the read to perform, so the model
    self-corrects in one step rather than retrying the same blind write.
    """

    reason: str
    error: str
    hint: str


# session -> resolved path -> Observation. Process-local and deliberately not persisted:
# an observation is a claim about what is in the model's context right now.
_LEDGER: dict[str, dict[str, Observation]] = {}
# raw_ref (tool-result id) -> (resolved path, digest the read observed), so a retrieval
# slice can be attributed back to the file it came from AND rejected once that snapshot no
# longer matches the file. Bounded with the ledger it serves.
_RAW_REFS: dict[str, dict[str, tuple[str, str]]] = {}
_LOCK = threading.Lock()


def _bucket(session_key: str) -> str:
    return session_key or _NO_SESSION


def file_sha256(path: Path) -> str | None:
    """sha256 of *path*'s full bytes, or None if it cannot be read.

    None is a refusal, never an admission (fail closed): "I could not determine the
    current content" is precisely the state the gate exists to stop a write in.
    """
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def record_read(
    session_key: str,
    path: Path | str,
    *,
    observed_text: str,
    content_sha256: str,
    complete: bool,
    raw_ref: str = "",
) -> None:
    """Record that ``observed_text`` was handed to the model as the content of *path*.

    Called from ``read_file`` with the text it is about to return — not with the file's
    bytes — because the whole point is to record what was *observed*.
    """
    key = str(Path(path))
    obs = Observation(
        content_sha256=content_sha256,
        complete=complete,
        at=time.time(),
        fragments=(observed_text,),
    )
    with _LOCK:
        paths = _LEDGER.setdefault(_bucket(session_key), {})
        paths[key] = obs
        if len(paths) > MAX_PATHS_PER_SESSION:
            for stale in sorted(paths, key=lambda k: paths[k].at)[
                : len(paths) - MAX_PATHS_PER_SESSION
            ]:
                paths.pop(stale, None)
        if raw_ref:
            refs = _RAW_REFS.setdefault(_bucket(session_key), {})
            refs[raw_ref] = (key, content_sha256)
            if len(refs) > MAX_PATHS_PER_SESSION:
                refs.clear()  # coarse but bounded; a lost ref only costs a re-read


def record_retrieval(session_key: str, raw_ref: str, *, observed_text: str) -> None:
    """Extend the observation for whichever file ``raw_ref`` came from.

    A projected read names ``tool_result_get`` as the way to pull the dropped slice; when
    the model takes that route it HAS observed those bytes, so the gate must credit them.
    Without this, the refusal for a truncated read would name a next action that cannot
    succeed on a file larger than the output cap.
    """
    if not raw_ref or not observed_text:
        return
    with _LOCK:
        ref = _RAW_REFS.get(_bucket(session_key), {}).get(raw_ref)
        if not ref:
            return
        key, sha_at_read = ref
        paths = _LEDGER.get(_bucket(session_key), {})
        obs = paths.get(key)
        if obs is None:
            return
        # The retrieval store holds the bytes of the READ, which may no longer be the
        # bytes on disk (our own later edit, or a third party's). Crediting a slice of a
        # superseded snapshot would license an edit against content that is gone, so a
        # slice is only credited while the snapshot it came from is still the file.
        if obs.content_sha256 != sha_at_read:
            return
        paths[key] = Observation(
            content_sha256=obs.content_sha256,
            complete=obs.complete,
            at=time.time(),
            fragments=(*obs.fragments, observed_text),
        )


def record_overwrite(session_key: str, path: Path | str, *, content: str) -> None:
    """Credit the model with observing a file it just wrote whole.

    A ``write_file`` supplies the ENTIRE content, so after it lands the model knows the
    file exactly — a complete observation, not a courtesy. Without this, the agent's own
    write would immediately invalidate its own observation and the very next edit would be
    refused with "changed on disk" blaming a third party for our own change. Third-party
    staleness is still caught: the digest recorded is of what WE left on disk.
    """
    target = Path(path)
    sha = file_sha256(target)
    if sha is None:
        return
    with _LOCK:
        _LEDGER.setdefault(_bucket(session_key), {})[str(target)] = Observation(
            content_sha256=sha, complete=True, at=time.time(), fragments=(content,)
        )


def record_edit(
    session_key: str,
    path: Path | str,
    *,
    old: str,
    new: str,
    replace_all: bool = False,
) -> None:
    """Carry the observation forward across an in-place edit the model just made.

    Each fragment the model was shown, with the same substitution applied, is what it now
    knows to be there — so completeness is INHERITED, not upgraded: an edit inside a
    partly-observed file leaves it partly observed, and a later whole-file overwrite is
    still refused. If there is no prior observation there is nothing to carry (the gate
    would have refused the edit), so this is a no-op.
    """
    target = Path(path)
    key = str(target)
    sha = file_sha256(target)
    if sha is None:
        return
    n = -1 if replace_all else 1
    with _LOCK:
        paths = _LEDGER.setdefault(_bucket(session_key), {})
        obs = paths.get(key)
        if obs is None:
            return
        paths[key] = Observation(
            content_sha256=sha,
            complete=obs.complete,
            at=time.time(),
            fragments=tuple(f.replace(old, new, n) for f in obs.fragments),
        )


def begin_turn(session_key: str) -> None:
    """Drop every observation for *session_key* — a new turn observes for itself.

    Called where a turn is already declared (the chat runner's
    ``turn_checkpoints.begin_turn`` site), so the two turn notions cannot drift apart.
    """
    with _LOCK:
        _LEDGER.pop(_bucket(session_key), None)
        _RAW_REFS.pop(_bucket(session_key), None)


def forget_session(session_key: str) -> None:
    begin_turn(session_key)


def reset_all() -> None:
    """Test hook: clear the process-global ledger (see test-isolation discipline)."""
    with _LOCK:
        _LEDGER.clear()
        _RAW_REFS.clear()


def observation(session_key: str, path: Path | str) -> Observation | None:
    with _LOCK:
        return _LEDGER.get(_bucket(session_key), {}).get(str(Path(path)))


def admit_write(
    session_key: str,
    path: Path,
    *,
    operation: str,
    display_path: str,
    required_text: str | None = None,
) -> Refusal | None:
    """None to admit the write; a :class:`Refusal` to refuse it.

    ``operation`` is ``"edit"`` (in-place, ``required_text`` = the ``old_str`` being
    replaced) or ``"overwrite"`` (whole-file ``write_file``). ``display_path`` is the
    path as the model spelled it, so the refusal names something it can copy back.
    """
    try:
        exists = path.is_file()
    except OSError:
        # Cannot even classify create-vs-overwrite → cannot determine observation.
        return Refusal(
            reason="undetermined",
            error=f"cannot determine the current state of {display_path} — refusing to write it",
            hint=(
                f"Call read_file with path={display_path!r} to establish what is there now, "
                "then retry the write."
            ),
        )

    if not exists:
        # Create-new destroys nothing, so no prior read is owed. An `edit` of a missing
        # file is not a create — let the tool's own "not a file" error say so, rather
        # than blaming the model for a read it could not have done.
        return None

    obs = observation(session_key, path)
    if obs is None:
        return Refusal(
            reason="not_observed",
            error=(
                f"{display_path} has not been read in this turn — refusing to "
                f"{'edit' if operation == 'edit' else 'overwrite'} a file whose current "
                "content was never observed"
            ),
            hint=(
                f"Call read_file with path={display_path!r} first, then retry this "
                f"{'edit' if operation == 'edit' else 'write'} against the text you just read."
            ),
        )

    if obs.expired():
        return Refusal(
            reason="expired",
            error=f"the read of {display_path} is too old to rely on — refusing to write it",
            hint=f"Call read_file with path={display_path!r} again, then retry the write.",
        )

    current = file_sha256(path)
    if current is None:
        return Refusal(
            reason="undetermined",
            error=f"cannot read {display_path} to confirm it is unchanged — refusing to write it",
            hint=(
                f"Call read_file with path={display_path!r} to see its current content, "
                "then retry."
            ),
        )
    if current != obs.content_sha256:
        return Refusal(
            reason="changed_on_disk",
            error=(
                f"{display_path} changed on disk since you read it — refusing to write "
                "an edit computed against the old content (it would revert that change)"
            ),
            hint=(
                f"Call read_file with path={display_path!r} to see the new content, "
                "then recompute the edit against it."
            ),
        )

    if operation == "overwrite":
        if not obs.complete:
            return Refusal(
                reason="partial_observation",
                error=(
                    f"only part of {display_path} was shown to you (the read was truncated), "
                    "so an overwrite would discard content you never saw — refusing"
                ),
                hint=(
                    f"Use edit_file for a targeted change, or read the whole of {display_path!r} "
                    "(tool_result_get on the projected read) before overwriting it."
                ),
            )
        return None

    if operation == "edit":
        if required_text and not obs.covers(required_text):
            if obs.complete:
                # The whole file was observed and the text is not in it. That is the
                # tool's own "old_str not found" case — admit and let it say so
                # precisely, rather than mislabelling it as an unread file.
                return None
            return Refusal(
                reason="region_not_observed",
                error=(
                    f"the text you are replacing is not in the part of {display_path} you were "
                    "shown (that read was truncated) — refusing an edit to a region you never "
                    "observed"
                ),
                hint=(
                    f"Observe the region first: grep for it in {display_path!r}, or call "
                    "tool_result_get with the read's result_id and a grep/line range, then "
                    "retry the edit."
                ),
            )
        return None

    # An operation kind the gate does not know how to reason about is refused, not waved
    # through: fail closed is the whole contract.
    return Refusal(
        reason="unknown_operation",
        error=f"unrecognized write operation {operation!r} for {display_path} — refusing",
        hint=f"Call read_file with path={display_path!r} and use write_file or edit_file.",
    )
