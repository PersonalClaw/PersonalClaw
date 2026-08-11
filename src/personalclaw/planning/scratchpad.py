"""Watched-scratchpad intake — a jotted line becomes a PROPOSED plan (UP-R18 / crit 9).

The barrier to starting work here is "create a plan"; the barrier to writing something down in a
notes file is nothing. This closes that gap in the only direction that is safe: a periodic scan of
one configured local file turns each actionable line into a **proposal in the needs-input inbox**,
never into a run. Nothing on this path can start a workflow — a human accepts first.

**Why the checked/struck filter is the substance, not a detail.** A markdown scratchpad's `- [x]`
items are DONE and its `~~struck~~` lines are ABANDONED. Proposing either is the single most
visible way this feature could be wrong: it would hand the user back the work they already finished
or deliberately dropped, every scan, forever. So the filter is structural (it runs before anything
else) and it is tested per-form.

**Two dedup tiers, because one is not enough.** `emit_attention_item`'s `dedup_key` only matches
PENDING/SEEN rows (`inbox.py` ``_find_open_by_dedup``), which is exactly right for its own job — a
DISMISSED request is genuinely new when it recurs — and exactly wrong here: a user who dismisses
"plan the nursery renovation" is saying no, and a line they never edit would re-propose on the next
scan. So this module keeps its OWN persisted seen-set keyed by content hash, and the inbox key is
the second tier that catches a same-scan/same-process double emit. The seen-set is what makes a
restart quiet; the inbox key is what makes a re-entrant scan quiet.

**Content hash, not line number.** Inserting a line at the top of a notes file renumbers everything
below it. Keying the seen-set on the line's own normalized text means editing line 3 re-proposes
only line 3, and reordering the file proposes nothing. The line NUMBER still rides along as the
backlink, because that is what a human needs to go read the source.

**What plays the triage gate here.** `triage_line` is it: a structural verdict (checked, struck,
heading, fence, too short) plus the shipped pre-LLM injection screen (`triggers.screen.screen`) plus
an actionability test. A declined line is recorded in the seen-set with its reason, so a line that
is not work is asked about once and never re-tried — an un-recorded decline is a silent retry loop.
The intent classifier is `workflows.intent.classify`, the same deterministic no-LLM classifier the
chat planner routes on, so a scratchpad line and a typed intent classify identically.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

#: A scratchpad is a notes file, not a corpus. A file this size is either not a scratchpad or has
#: become an archive; scanning it every minute would be a real cost for no gain.
MAX_SCAN_BYTES = 512 * 1024

#: Lines past this are not read. Same reasoning as the byte cap, and it bounds the per-scan screen
#: cost (one `screen()` call per candidate line).
MAX_SCAN_LINES = 5_000

#: A single scan proposes at most this many lines. A user who pastes fifty todos at once should get
#: a handful of proposals, not fifty inbox rows — the rest are picked up by later scans (they stay
#: unrecorded in the seen-set, so nothing is lost).
MAX_PROPOSALS_PER_SCAN = 5

#: An unchecked markdown task: `- [ ] thing`, `* [ ] thing`, `1. [ ] thing`.
_UNCHECKED_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[\s\]\s*(.+)$")

#: A CHECKED task, in every casing markdown renderers accept (`x`, `X`, and the `✓`/`✔` some
#: editors write). Matched explicitly rather than inferred from "not unchecked", because a
#: not-unchecked default would treat a malformed checkbox as work.
_CHECKED_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\[\s*(?:x|X|✓|✔)\s*\]")

#: A plain list item (no checkbox). The bullet itself is the user's statement of intent, which is
#: why a bullet does not additionally need a verb cue below.
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$")

_HEADING_RE = re.compile(r"^\s*#{1,6}\s")
_RULE_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_QUOTE_RE = re.compile(r"^\s*>")
_COMMENT_RE = re.compile(r"^\s*<!--")
_TABLE_RE = re.compile(r"^\s*\|")
_FRONT_MATTER_RE = re.compile(r"^\s*---\s*$")

#: `~~text~~`. A line is struck when, after removing every struck span, nothing but punctuation and
#: list markup is left — so `~~old plan~~ new plan` is NOT struck (the survivor is the work) while
#: `- ~~old plan~~` is.
_STRIKE_RE = re.compile(r"~~(.+?)~~")

#: Deterministic verb cues that make a bare prose line actionable. Prose without one of these is a
#: note, not a request — "the API is slow" is an observation, "fix the slow API" is work. Bullets
#: and checkboxes bypass this entirely: writing `- ` is already the user saying "this is a todo".
_ACTION_CUES: tuple[str, ...] = (
    "add",
    "audit",
    "book",
    "build",
    "buy",
    "call",
    "cancel",
    "check",
    "clean",
    "compare",
    "create",
    "delete",
    "design",
    "draft",
    "email",
    "figure out",
    "file",
    "find",
    "finish",
    "fix",
    "follow up",
    "implement",
    "improve",
    "investigate",
    "learn",
    "look into",
    "make",
    "migrate",
    "move",
    "organize",
    "organise",
    "plan",
    "prepare",
    "publish",
    "read",
    "refactor",
    "remove",
    "rename",
    "renew",
    "replace",
    "research",
    "review",
    "rewrite",
    "schedule",
    "set up",
    "ship",
    "sort out",
    "start",
    "switch",
    "test",
    "try",
    "update",
    "upgrade",
    "write",
)

#: Minimum words in the cleaned line. A one-word line ("taxes") carries no intent to plan against,
#: and classifying it would produce an UNCLASSIFIED tuple and a useless proposal.
MIN_WORDS = 2


@dataclass
class Candidate:
    """One line the parser judged structurally eligible, with its backlink coordinates.

    ``line_no`` is 1-based, matching what an editor shows — a 0-based backlink sends the user to
    the wrong line, which is worse than no backlink at all.
    """

    line_no: int
    raw: str
    text: str

    @property
    def content_hash(self) -> str:
        return line_hash(self.text)


@dataclass
class Verdict:
    """The triage gate's answer for one line.

    ``reason`` is mandatory on a decline and is persisted with the seen-set entry: a skip nobody
    can read is indistinguishable from a bug, which is the same argument
    `Outcome.SKIPPED_TRIAGE` makes for fire ledger rows.
    """

    ok: bool
    reason: str = ""


@dataclass
class Proposal:
    """A line that earned a proposal, plus everything the inbox row needs."""

    line_no: int
    text: str
    content_hash: str
    path: str
    rigor: str = ""
    intent_reason: str = ""

    @property
    def backlink(self) -> str:
        """``<path>:<line_no>`` — the form every editor and the dashboard both understand."""
        return f"{self.path}:{self.line_no}"

    def refs(self) -> dict[str, Any]:
        return {
            "scratchpad_path": self.path,
            "scratchpad_line": self.line_no,
            "scratchpad_hash": self.content_hash,
            "backlink": self.backlink,
        }


@dataclass
class ScanResult:
    """What one scan did — proposals raised, plus the declines it recorded."""

    proposals: list[Proposal] = field(default_factory=list)
    declined: list[tuple[int, str]] = field(default_factory=list)
    skipped_seen: int = 0
    unchanged: bool = False


def line_hash(text: str) -> str:
    """A stable hash of a line's meaning, not its formatting.

    Case and interior whitespace are folded so re-indenting a bullet or capitalizing a word does
    not read as a new line and re-propose it. Markdown emphasis is NOT folded — `**ship it**` and
    `ship it` are the same words, but stripping markup here would need the same parser twice; the
    normalization below is deliberately the cheap, obvious one.
    """
    folded = " ".join(text.strip().casefold().split())
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()[:16]


def _strip_strikes(text: str) -> str:
    return _STRIKE_RE.sub("", text)


def is_struck(text: str) -> bool:
    """True when everything meaningful on the line is inside `~~…~~`.

    Partial strikes survive on purpose: `~~call the vet~~ book the vet` is a user REVISING a todo,
    and dropping the whole line would lose the revision.
    """
    if "~~" not in text:
        return False
    remainder = _strip_strikes(text)
    # Drop list markup and punctuation; whatever is left is the surviving work.
    remainder = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", remainder)
    remainder = re.sub(r"^\s*\[\s*[xX✓✔ ]?\s*\]\s*", "", remainder)
    remainder = re.sub(r"[^\w]+", "", remainder)
    return not remainder


def candidates(text: str) -> tuple[list[Candidate], list[tuple[int, str]]]:
    """Split a scratchpad into structurally eligible lines and structural declines.

    Fenced code blocks are skipped wholesale: a shell snippet pasted into a notes file is reference
    material, and `rm -rf ./build` is emphatically not a plan to propose. Front matter is skipped
    for the same reason (it is metadata about the file, not work in it).

    Returns ``(candidates, declines)``. Declines carry a reason so the caller can record them; a
    blank line is NOT a decline — there is nothing there to remember, and recording every empty
    line would grow the seen-set without bound.
    """
    out: list[Candidate] = []
    declines: list[tuple[int, str]] = []
    in_fence = False
    in_front_matter = False
    lines = text.splitlines()[:MAX_SCAN_LINES]

    for idx, raw in enumerate(lines, start=1):
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if idx == 1 and _FRONT_MATTER_RE.match(raw):
            in_front_matter = True
            continue
        if in_front_matter:
            if _FRONT_MATTER_RE.match(raw):
                in_front_matter = False
            continue

        stripped = raw.strip()
        if not stripped:
            continue
        if _RULE_RE.match(raw) or _COMMENT_RE.match(raw) or _TABLE_RE.match(raw):
            continue
        if _HEADING_RE.match(raw):
            declines.append((idx, "heading"))
            continue
        if _QUOTE_RE.match(raw):
            declines.append((idx, "quote"))
            continue
        if _CHECKED_RE.match(raw):
            # DONE. The most important skip in this module.
            declines.append((idx, "checked"))
            continue
        if is_struck(raw):
            # ABANDONED. The second most important skip.
            declines.append((idx, "struck"))
            continue

        unchecked = _UNCHECKED_RE.match(raw)
        bullet = _BULLET_RE.match(raw)
        if unchecked:
            body = unchecked.group(1)
        elif bullet:
            body = bullet.group(1)
        else:
            body = stripped
        body = _strip_strikes(body).strip()
        if not body:
            declines.append((idx, "struck"))
            continue
        out.append(Candidate(line_no=idx, raw=raw, text=body))
    return out, declines


def _has_action_cue(text: str) -> bool:
    lowered = f" {text.casefold()} "
    return any(
        f" {cue} " in lowered or lowered.lstrip().startswith(f"{cue} ") for cue in _ACTION_CUES
    )


def triage_line(cand: Candidate) -> Verdict:
    """The triage gate: may this line become a proposal at all?

    Three independent refusals, in cost order:

    1. **Too short** — under `MIN_WORDS` there is no intent to classify.
    2. **The injection screen** (`triggers.screen.screen`) — a scratchpad is a local file, but it
       is also the easiest thing on the machine for another program to append to, and its lines are
       about to be summarized into an inbox row a model may later read. A BLOCKED verdict declines
       the line and names the matched group, exactly as the fire path does.
    3. **Not actionable** — a bullet or checkbox is self-evidently a todo; bare prose needs a verb
       cue. "the API is slow" is a note; "fix the slow API" is work.
    """
    words = cand.text.split()
    if len(words) < MIN_WORDS:
        return Verdict(ok=False, reason="too_short")

    from personalclaw.triggers.screen import screen

    result = screen(cand.text)
    if result.blocked:
        return Verdict(ok=False, reason=f"blocked_injection:{result.matched_group or 'unknown'}")

    listish = bool(_UNCHECKED_RE.match(cand.raw) or _BULLET_RE.match(cand.raw))
    if not listish and not _has_action_cue(cand.text):
        return Verdict(ok=False, reason="not_actionable")
    return Verdict(ok=True)


# ── the persisted seen-set (tier one of two) ──


def seen_path(base_dir: Path | str | None = None) -> Path:
    """``<config_dir>/planning/scratchpad-seen.json``.

    A sidecar, not `entity_settings/`: this is high-churn runtime state that grows on every scan,
    while `entity_settings/*.json` is a small allowlisted user-settings document served by a PUT
    (`providers/entity_routes.INBOX_DEFAULTS`). Writing a growing hash map through that surface
    would make it PATCHable by a client and rewrite a settings file on every poll — the same
    argument `file_poll` records for `trigger-watch/`.
    """
    from personalclaw.config.loader import config_dir

    root = Path(base_dir) if base_dir else config_dir()
    return root / "planning" / "scratchpad-seen.json"


@dataclass
class SeenState:
    """What the scan has already decided about each line, keyed by content hash.

    ``fingerprint`` short-circuits an unchanged file so a 60-second poll over a notes file that
    nobody touched costs one `stat`, not a parse plus a screen per line.
    """

    #: content_hash -> {"line": int, "reason": str}. `reason` is "" for a proposed line and the
    #: triage reason for a declined one, so a decline is remembered and never re-tried.
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: `<size>:<mtime_ns>` of the file at the last scan.
    fingerprint: str = ""

    def knows(self, content_hash: str) -> bool:
        return content_hash in self.entries

    def record(self, content_hash: str, *, line_no: int, reason: str = "") -> None:
        self.entries[content_hash] = {"line": line_no, "reason": reason}

    def to_dict(self) -> dict[str, Any]:
        return {"entries": self.entries, "fingerprint": self.fingerprint}

    @classmethod
    def from_dict(cls, raw: Any) -> "SeenState":
        if not isinstance(raw, dict):
            return cls()
        entries = raw.get("entries")
        clean: dict[str, dict[str, Any]] = {}
        if isinstance(entries, dict):
            for key, val in entries.items():
                if isinstance(key, str) and isinstance(val, dict):
                    clean[key] = val
        fingerprint = raw.get("fingerprint")
        return cls(entries=clean, fingerprint=fingerprint if isinstance(fingerprint, str) else "")


def load_seen(base_dir: Path | str | None = None) -> SeenState:
    """Revive the seen-set, or an empty one. Never raises.

    A corrupt sidecar degrades to empty, which re-proposes — the honest failure direction here is a
    duplicate proposal the user dismisses once, not a silent stop that loses ambient capture
    entirely.
    """
    try:
        raw = json.loads(seen_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SeenState()
    return SeenState.from_dict(raw)


def save_seen(state: SeenState, base_dir: Path | str | None = None) -> None:
    """Persist the seen-set atomically. A half-written map read back as empty re-proposes."""
    path = seen_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(state.to_dict(), indent=2) + "\n")


def _fingerprint(path: Path) -> str:
    try:
        st = path.stat()
    except OSError:
        return ""
    return f"{st.st_size}:{st.st_mtime_ns}"


def configured_path() -> str:
    """`planning.scratchpad_path`, or "" when the feature is off (the default).

    Read through the loader on every call so a test that repoints config, or a user who edits the
    setting while the gateway runs, is honoured — a module-level bind would freeze the first value.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return str(AppConfig.load().planning.scratchpad_path or "").strip()
    except Exception:  # pragma: no cover - a config failure must not kill the poll loop
        logger.debug("scratchpad: config unreadable", exc_info=True)
        return ""


def scan(
    path: Path | str,
    *,
    base_dir: Path | str | None = None,
    persist: bool = True,
) -> ScanResult:
    """Scan the scratchpad once and return the lines that earned a proposal.

    Pure of the inbox on purpose: this decides WHAT to propose, `propose` decides how it surfaces,
    and the split is what lets every dedup and filter rule be tested without an inbox at all.

    Every examined line — proposed or declined — is recorded in the seen-set before returning, so
    the next scan (and the next process) skips it. A declined line is remembered WITH its reason:
    re-screening "the API is slow" every minute forever is the nag this module exists to avoid.
    """
    target = Path(path).expanduser()
    state = load_seen(base_dir)
    result = ScanResult()

    fingerprint = _fingerprint(target)
    if fingerprint and fingerprint == state.fingerprint:
        result.unchanged = True
        return result

    try:
        size = target.stat().st_size
        if size > MAX_SCAN_BYTES:
            logger.info("scratchpad %s is too large to scan (%d bytes)", target, size)
            return result
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        logger.debug("scratchpad %s unreadable", target, exc_info=True)
        return result

    cands, declines = candidates(text)
    for line_no, reason in declines:
        result.declined.append((line_no, reason))

    for cand in cands:
        digest = cand.content_hash
        if state.knows(digest):
            result.skipped_seen += 1
            continue
        verdict = triage_line(cand)
        if not verdict.ok:
            state.record(digest, line_no=cand.line_no, reason=verdict.reason)
            result.declined.append((cand.line_no, verdict.reason))
            continue
        if len(result.proposals) >= MAX_PROPOSALS_PER_SCAN:
            # Deliberately NOT recorded: an unrecorded line is picked up by the next scan, so a
            # fifty-todo paste arrives over several minutes instead of being silently dropped.
            break

        from personalclaw.workflows.intent import classify

        intent = classify(cand.text)
        state.record(digest, line_no=cand.line_no)
        result.proposals.append(
            Proposal(
                line_no=cand.line_no,
                text=cand.text,
                content_hash=digest,
                path=str(target),
                rigor=str(intent.rigor.value),
                intent_reason=intent.reason,
            )
        )

    state.fingerprint = fingerprint
    if persist:
        save_seen(state, base_dir)
    return result


def propose(state: Any, proposal: Proposal) -> str:
    """Land one proposal in the needs-input inbox. Returns the inbox item id.

    PROPOSED, never run: this raises an inbox row and nothing else. There is no code path from a
    scratchpad line to a workflow start — accepting the proposal is a separate, human action on the
    inbox row, which is the whole guardrail of criterion 9.

    Routed through `emit_attention_item` rather than `store.add` + `state.notify` because that
    helper is the only writer that reaches the LIVE store the API serves; a hand-rolled pair writes
    a row the dashboard cannot see.
    """
    from personalclaw.inbox import ItemKind, emit_attention_item

    return emit_attention_item(
        state,
        source="planning",
        kind="proposal",
        item_kind=ItemKind.PROPOSAL.value,
        title="Plan this jotted line?",
        body=f"{proposal.text}\n\nFrom {proposal.backlink} (rigor: {proposal.rigor})",
        refs=proposal.refs(),
        dedup_key=f"scratchpad_proposal:{proposal.content_hash}",
    )


def scan_and_propose(state: Any, *, base_dir: Path | str | None = None) -> list[str]:
    """The poll-loop entry point: scan the configured scratchpad and raise its proposals.

    Returns the inbox item ids raised (empty when the feature is unconfigured, which is the
    default — an unset `planning.scratchpad_path` reads no files at all).
    """
    configured = configured_path()
    if not configured:
        return []
    result = scan(configured, base_dir=base_dir)
    ids: list[str] = []
    for proposal in result.proposals:
        try:
            ids.append(propose(state, proposal))
        except Exception:  # noqa: BLE001 - one bad row must not strand the rest of the scan
            logger.warning(
                "scratchpad proposal failed for line %d", proposal.line_no, exc_info=True
            )
    if result.declined:
        logger.debug("scratchpad declines: %s", result.declined)
    return ids
