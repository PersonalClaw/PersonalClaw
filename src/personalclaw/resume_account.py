"""CE2-10: the account of what a resumed or compacted session ALREADY DID.

The observed defect: a resumed turn cheerfully repeats yesterday's completed step. It happens
because a resume hands the model surviving PROSE and asks it to infer what is already done —
``build_message`` skips thread history on a resume (the ACP/native agent owns it) and
``context_compaction`` folds the middle of the conversation into a summary. Both leave "step 3
finished" as something to be re-derived from narrative, and re-derivation fails.

So the account here is **DERIVED, never composed**. Every line traces to a record that some other
code wrote:

* the workflow ledger — ``step_completed`` / ``step_failed`` / ``step_skipped`` / ``effect`` /
  ``decision``, read through :func:`personalclaw.ledger.reader.read_events`. Written by
  ``workflows/controller.py`` (``journal.step_completed`` at :2961, ``journal.step_failed`` at
  :2334, ``journal.step_skipped`` at :1596, ``journal.effect`` at :2292, ``journal.decision`` at
  :3690).
* the session's tool history — the native loop's own message list, whose shapes
  ``context_compaction`` documents: an assistant message carrying ``tool_calls`` and a
  ``{"role": "tool", "tool_call_id", "content"}`` result.
* the turn checkpoints — ``turn_checkpoints.recorded_file_entries``, the pre-edit manifests
  written by ``agents/native/builtin_tools.py:1304``. This is the only source that PERSISTS which
  files a session's turns touched, so it is what a resume after a restart has to work from.

**No model call composes this text.** There is no ``summarize_fn`` here and no prose input. If a
fact is not in the ledger or the tool history, it does not appear in the account — which is what
makes it impossible for the account to invent a completion that did not happen.

**FAILED stays FAILED.** A step recorded as attempted-and-failed is carried as ``failed``, never
folded into ``done``, because a false completion is strictly worse than a forgotten one: a
forgotten completion costs one redundant step, a false one silently skips work the user asked for.
The same rule gives ``attempted`` its meaning — a tool call whose RESULT was never recorded (the
interrupted case) is the one the model most wants to call done, and it is exactly the one that must
not be.

Status vocabulary, and where each one's authority comes from:

============  ==================================================================================
``done``      ledger ``step_completed``; effect ``committed``; or a tool result that is neither
              an error nor a denial — the discriminator the runtime itself uses
              (``agents/native/runtime.py:1143``: ``failed = result_str.startswith("Error:")``).
``failed``    ledger ``step_failed``; effect ``compensated`` (it was rolled back, so it does not
              stand); or a tool result starting with ``Error:``.
``denied``    a tool result :func:`personalclaw.security.is_denial_observation` recognises. A
              denial is NOT a failure (WF2LEA-13) and is certainly not a completion.
``skipped``   ledger ``step_skipped``; effect ``skipped``.
``attempted`` a recorded tool call with NO recorded result, or effect ``attempted``/``retried``
              ("unknown, possibly fired"). The honest label for interrupted work.
============  ==================================================================================

**Stated as fact, not instruction.** The block says "the record shows X"; it never says "do Y".
A resumed model that read an instruction block would treat the account as a task list, which is the
re-run defect wearing a different hat.

**Bounded.** :data:`MAX_ACCOUNT_CHARS` is a hard post-render cap and
:data:`MAX_ACCOUNT_TOKENS` is its declared token ceiling, measured with the allocator's counter
(``context_headroom.count_tokens``) — this module adds no second token counter. Truncation is
announced in the block, because a silently shortened account is a forgotten completion that
nothing says was forgotten.

**A source that was never read is not an empty source.** :data:`NOT_CONSULTED` is a distinct
sentinel object, not ``None``/``0``/``""``: ``sources["ledger"] == 0`` means "read it, it recorded
nothing" and ``sources["ledger"] is NOT_CONSULTED`` means "nobody read it". The rendered block says
which. No integer can collide with the sentinel, and :func:`derive_account` gives its source
parameters NO defaults, so a caller that forgets one fails loudly instead of silently reporting
"nothing happened".

**Inconsistent record vs working tree ⇒ refuse.** "Inconsistent" is one concrete, cheap claim:
**a path the record says exists must exist.** Two feeders, both immune to a failed write:

* a write-shaped tool call whose recorded result was a SUCCESS (``files_written``) — the write
  landed, so the file is there;
* a turn-checkpoint entry with ``existed: True`` (``files_touched_existing``) — the file was
  present when this session was about to edit it, and an edit does not delete its target. This
  feeder is deliberately restricted to ``existed: True``: a capture with ``existed: False`` records
  a CREATE, and if that write then failed the file is legitimately absent, so checking it would
  refuse a resume over work that correctly did not happen.

A recorded DELETE is never presence-checked, and a path resolving outside the tree root is skipped
rather than counted. When the check finds a contradiction the resume raises
:class:`ResumeStateInconsistent` rather than proceeding on a false premise.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from personalclaw.ledger.kinds import (
    DECISION,
    EFFECT,
    STEP_COMPLETED,
    STEP_FAILED,
    STEP_SKIPPED,
)

logger = logging.getLogger(__name__)

#: The fence. ``context_compaction`` matches on the opening line to carry the block through a
#: compaction verbatim, so the two constants are the contract between the two modules — not a
#: string literal repeated at both ends where they could drift.
#:
#: ASCII ONLY, and that is load-bearing rather than a style choice. ``build_message`` runs the
#: assembled prompt through ``context._MULTIBYTE_TABLE``, which rewrites an em dash to ``--``. A
#: fence containing one would arrive in the history in a form :func:`context_compaction.
#: is_resume_account` no longer matches, and the carry rule would silently never fire — a control
#: that is present and inert. Measured, not assumed: ``test_the_fence_survives_prompt_assembly``.
FENCE_START = "[RESUME ACCOUNT -- RECORDED FACTS]"
FENCE_END = "[END RESUME ACCOUNT]"

SOURCE_LEDGER = "workflow ledger"
SOURCE_TOOL_HISTORY = "session tool history"
SOURCE_CHECKPOINTS = "turn checkpoints"

Status = Literal["done", "failed", "denied", "skipped", "attempted"]

#: Statuses that assert the work STANDS. Only these feed the tree-consistency check, and only
#: ``done`` may ever be read as "no need to redo this".
_STANDS: frozenset[str] = frozenset({"done"})

# ── bounds (§4: bounded, and it must not evict the live task) ──
_MAX_FACTS = 30
_MAX_FILES = 20
_MAX_DECISIONS = 5
_MAX_SUBJECT_CHARS = 80
_MAX_DETAIL_CHARS = 120
_MAX_REASONS = 10
#: Hard post-render cap. Deliberately a small fraction of any model window: the account is a
#: sidecar fact block, and one that could grow to evict the live task would trade the re-run
#: defect for a worse one.
MAX_ACCOUNT_CHARS = 4000
#: The declared token ceiling for the rendered block, measured with the ALLOCATOR's counter
#: (``context_headroom.count_tokens``). Asserted by the bound test rather than assumed.
MAX_ACCOUNT_TOKENS = 1400

#: The ledger kinds the account reads. Narrow on purpose — every kind here has a closed status
#: vocabulary, so nothing in the account needs interpreting.
ACCOUNT_KINDS: frozenset[str] = frozenset(
    {STEP_COMPLETED, STEP_FAILED, STEP_SKIPPED, EFFECT, DECISION}
)

#: `effect_status` → account status. The enum is closed (``workflows/effects.py:44``), so this
#: mapping is total; ``attempted``/``retried`` stay ``attempted`` because the ledger's own comment
#: says an ATTEMPTED effect is "unknown, possibly fired", and ``compensated`` is a rollback, so it
#: does not stand.
_EFFECT_STATUS: dict[str, Status] = {
    "committed": "done",
    "attempted": "attempted",
    "retried": "attempted",
    "compensated": "failed",
    "skipped": "skipped",
}

#: Tool-name substrings (lowercased) that mean "this call WROTE a file". Matched on the name, and
#: the path comes from an explicit path ARGUMENT — never scraped out of free text, because a
#: scraped path that does not exist would refuse a resume over a filename mentioned in prose.
_WRITE_TOOL_HINTS = ("write", "edit", "create", "patch", "append")
#: Checked FIRST: a delete records a change but its file is legitimately absent afterwards.
_DELETE_TOOL_HINTS = ("delete", "remove", "unlink", "trash", "rmdir")
_PATH_ARG_KEYS = ("path", "file_path", "filename", "target_path", "output_path", "dest")


class _NotConsulted:
    """The "nobody read this source" sentinel — a distinct object, not a falsy value.

    ``0`` is a legitimate record count and ``[]`` a legitimate event list, so neither can carry
    this meaning. Truthiness is deliberately NOT overridden: ``if events:`` must not quietly treat
    an unread source as an empty one, so every consumer is forced into an explicit
    ``is NOT_CONSULTED`` test.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "NOT_CONSULTED"


NOT_CONSULTED = _NotConsulted()

#: What a source parameter accepts: the records, or the sentinel. No default anywhere.
Records = Sequence[Mapping[str, Any]] | _NotConsulted


class ResumeStateInconsistent(RuntimeError):
    """The record and the working tree disagree, so the resume refuses.

    Not a warning. The account exists to stop the model acting on a false premise; a resume that
    printed "these files are missing" and then continued would hand the model a record it has
    already been told is wrong.
    """

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons: tuple[str, ...] = tuple(reasons)
        super().__init__(
            "Resume refused: the recorded account of completed work contradicts the working "
            "tree. " + " ".join(self.reasons)
        )


@dataclass(frozen=True)
class StepFact:
    """One recorded outcome. Every field is required — a defaulted field would make a caller that
    forgot to supply provenance indistinguishable from one that had none to give."""

    status: Status
    subject: str
    source: str
    ref: str
    detail: str
    retried: bool


@dataclass(frozen=True)
class ResumeAccount:
    """The structured account. Rendered by :func:`render_account`; never hand-written."""

    facts: tuple[StepFact, ...]
    #: Write-shaped tool calls whose recorded result was a success.
    files_written: tuple[str, ...]
    #: Turn-checkpoint captures where the file ALREADY EXISTED. See the module docstring for why
    #: `existed: False` entries are excluded from this and therefore from the refusal.
    files_touched_existing: tuple[str, ...]
    files_deleted: tuple[str, ...]
    decisions: tuple[str, ...]
    #: source name → number of records read, or :data:`NOT_CONSULTED`.
    sources: Mapping[str, int | _NotConsulted]
    #: Facts dropped to stay inside the bound. Announced in the block, never silent.
    omitted_facts: int

    def has_content(self) -> bool:
        """True when the account states at least one recorded fact.

        A block with no facts is suppressed rather than injected: "nothing was recorded" is not
        worth prompt weight, and an always-present empty block trains the reader to skip it.
        """
        return bool(
            self.facts
            or self.files_written
            or self.files_touched_existing
            or self.files_deleted
            or self.decisions
        )

    def expected_present(self) -> tuple[str, ...]:
        """Paths the record asserts exist in the tree NOW — what the refusal checks."""
        return _dedupe([*self.files_written, *self.files_touched_existing], _MAX_FILES * 2)

    def by_status(self, status: str) -> tuple[StepFact, ...]:
        return tuple(f for f in self.facts if f.status == status)


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ledger_subject(rec: Mapping[str, Any]) -> str:
    return str(rec.get("instance_path") or rec.get("node_id") or "(unnamed step)")


def _parse_args(raw: Any) -> dict[str, Any]:
    """A tool call's arguments as a dict. Same two shapes ``context_compaction`` handles: the
    provider's JSON string, or an already-decoded dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _tool_paths(name: str, args: Mapping[str, Any]) -> tuple[str, ...]:
    lowered = name.lower()
    if not any(h in lowered for h in _WRITE_TOOL_HINTS) and not any(
        h in lowered for h in _DELETE_TOOL_HINTS
    ):
        return ()
    out: list[str] = []
    for key in _PATH_ARG_KEYS:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return tuple(out)


def _is_delete_tool(name: str) -> bool:
    """Delete wins over write when a name matches both (``remove_and_write`` is a delete for the
    purposes of the presence check — the safe direction, since a delete is never presence-checked
    and so can never manufacture a refusal)."""
    lowered = name.lower()
    return any(h in lowered for h in _DELETE_TOOL_HINTS)


def _facts_from_ledger(events: Sequence[Mapping[str, Any]]) -> tuple[list[StepFact], list[str]]:
    """Ledger records → facts + settled decisions, in recorded order.

    Last outcome per subject wins, which is correct: a later record came from a later attempt. The
    fact keeps ``retried=True`` when an EARLIER outcome for that subject failed, so a
    done-after-failure still reads as one — the alternative is an account that quietly erases the
    failure that made the retry necessary.
    """
    latest: dict[str, StepFact] = {}
    order: list[str] = []
    had_failure: set[str] = set()
    decisions: list[str] = []
    for rec in events:
        kind = rec.get("kind")
        ref = str(rec.get("event_id") or rec.get("seq") or "")
        if kind == DECISION:
            decisions.append(_render_decision(rec))
            continue
        subject = _ledger_subject(rec)
        status: Status
        detail = ""
        if kind == STEP_COMPLETED:
            status = "done"
        elif kind == STEP_FAILED:
            status = "failed"
            detail = str(rec.get("error") or "")
        elif kind == STEP_SKIPPED:
            status = "skipped"
            detail = f"actor={rec.get('actor') or 'engine'}"
        elif kind == EFFECT:
            raw = str(rec.get("effect_status") or "")
            # An UNKNOWN effect_status is carried as `attempted`, never as done: an unrecognised
            # status is precisely the case where guessing "it finished" invents a completion.
            status = _EFFECT_STATUS.get(raw, "attempted")
            detail = f"effect {raw or 'status unrecorded'}"
        else:
            continue
        if status == "failed":
            had_failure.add(subject)
        if subject not in latest:
            order.append(subject)
        latest[subject] = StepFact(
            status=status,
            subject=_clip(subject, _MAX_SUBJECT_CHARS),
            source=SOURCE_LEDGER,
            ref=ref,
            detail=_clip(detail, _MAX_DETAIL_CHARS),
            retried=subject in had_failure and status != "failed",
        )
    return [latest[s] for s in order], decisions


_DECISION_ENVELOPE = frozenset(
    {"kind", "ts", "seq", "event_id", "instance_path", "node_id", "epoch"}
)


def _render_decision(rec: Mapping[str, Any]) -> str:
    """A settled choice, as the record holds it.

    ``journal.decision`` splats a caller-supplied dict, so the payload keys are open. Preferred
    keys first, then whatever non-envelope fields remain — rendering the raw record rather than a
    sentence about it keeps this derived instead of narrated.
    """
    for key in ("summary", "decision", "choice", "chose"):
        value = rec.get(key)
        if isinstance(value, str) and value.strip():
            rejected = rec.get("rejected") or rec.get("alternatives")
            tail = f" (rejected: {_clip(str(rejected), 60)})" if rejected else ""
            return _clip(value, _MAX_DETAIL_CHARS) + tail
    payload = {k: v for k, v in rec.items() if k not in _DECISION_ENVELOPE}
    return _clip(json.dumps(payload, sort_keys=True, default=str), _MAX_DETAIL_CHARS)


def _facts_from_tool_history(
    messages: Sequence[Mapping[str, Any]],
) -> tuple[list[StepFact], list[str], list[str]]:
    """Tool-history messages → facts + the files a SUCCESSFUL write-shaped call touched.

    The success/failure discriminator is the runtime's own
    (``agents/native/runtime.py:1143``) and the denial recogniser is
    :func:`personalclaw.security.is_denial_observation` — reused rather than re-derived, because a
    second copy of "what counts as a failed tool call" is a second answer that would drift from the
    one the breaker and procedural memory already use.

    A call with no recorded result is ``attempted``. That is the interrupted case the whole atom is
    about, so it is the one place where saying less is the entire point.
    """
    from personalclaw import security

    calls: list[tuple[str, str, dict[str, Any]]] = []  # (call_id, name, args)
    results: dict[str, str] = {}
    for msg in messages:
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, Mapping):
                continue
            fn = call.get("function") if isinstance(call.get("function"), Mapping) else {}
            name = str((fn or {}).get("name") or "")
            if not name:
                continue
            calls.append(
                (str(call.get("id") or ""), name, _parse_args((fn or {}).get("arguments")))
            )
        if msg.get("role") == "tool":
            results[str(msg.get("tool_call_id") or "")] = str(msg.get("content") or "")

    facts: list[StepFact] = []
    written: list[str] = []
    deleted: list[str] = []
    for call_id, name, args in calls:
        content = results.get(call_id)
        status: Status
        detail = ""
        if content is None:
            status = "attempted"
            detail = "no result recorded — this call may not have finished"
        elif content.startswith("Error:"):
            if security.is_denial_observation(content):
                status = "denied"
                detail = "refused by policy or by the user"
            else:
                status = "failed"
                detail = content[len("Error:") :]
        else:
            status = "done"
        facts.append(
            StepFact(
                status=status,
                subject=_clip(name, _MAX_SUBJECT_CHARS),
                source=SOURCE_TOOL_HISTORY,
                ref=call_id or "(no call id)",
                detail=_clip(detail, _MAX_DETAIL_CHARS),
                retried=False,
            )
        )
        for path in _tool_paths(name, args):
            if _is_delete_tool(name):
                deleted.append(path)
            elif status in _STANDS:
                # Only a write the record says SUCCEEDED becomes a presence claim. A failed or
                # denied write never enters `files_written`, so a rolled-back attempt cannot
                # refuse a resume.
                written.append(path)
    return facts, written, deleted


def _facts_from_checkpoints(
    entries: Sequence[Mapping[str, Any]],
) -> tuple[list[StepFact], list[str]]:
    """Turn-checkpoint manifests → facts + the paths the record says already existed.

    A manifest entry is written BEFORE the mutation (``capture_pre_edit``), so it records an
    intended edit, not a landed one. That is why the fact is ``attempted`` and never ``done``: the
    store cannot say whether the write finished, and inventing a completion is the one thing this
    module must not do. What it CAN say — and this is the load-bearing part — is whether the file
    existed at capture time, which is a claim about the tree that survives a restart.
    """
    facts: list[StepFact] = []
    existing: list[str] = []
    for entry in entries:
        path = str(entry.get("path") or "")
        if not path:
            continue
        skipped = str(entry.get("skipped") or "")
        existed = entry.get("existed")
        detail = f"pre-edit capture, turn {entry.get('turn', '?')}"
        if skipped:
            detail += f", not captured ({skipped})"
        facts.append(
            StepFact(
                status="attempted",
                subject=_clip(path, _MAX_SUBJECT_CHARS),
                source=SOURCE_CHECKPOINTS,
                ref=str(entry.get("turn", "?")),
                detail=_clip(detail, _MAX_DETAIL_CHARS),
                retried=False,
            )
        )
        # `existed is True` explicitly, not truthiness: a manifest that omitted the key would
        # otherwise read as "existed: False" — an absent key is an unrecorded fact, not a negative
        # one, and treating it as negative would silently shrink the refusal's reach.
        if existed is True and not skipped:
            existing.append(path)
    return facts, existing


def _dedupe(values: Sequence[str], limit: int) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return tuple(list(seen)[:limit])


def derive_account(
    *,
    ledger_events: Records,
    tool_messages: Records,
    checkpoint_entries: Records,
) -> ResumeAccount:
    """Fold the recorded facts into an account. No model call, no prose input.

    Every source parameter is keyword-only with NO default: pass the records, or pass
    :data:`NOT_CONSULTED` to say the source was never read. A forgotten argument raises
    ``TypeError`` here instead of rendering a confident "nothing happened".
    """
    facts: list[StepFact] = []
    decisions: list[str] = []
    written: list[str] = []
    touched_existing: list[str] = []
    deleted: list[str] = []
    sources: dict[str, int | _NotConsulted] = {}

    if isinstance(ledger_events, _NotConsulted):
        sources[SOURCE_LEDGER] = NOT_CONSULTED
    else:
        sources[SOURCE_LEDGER] = len(ledger_events)
        ledger_facts, ledger_decisions = _facts_from_ledger(ledger_events)
        facts.extend(ledger_facts)
        decisions.extend(ledger_decisions)

    if isinstance(tool_messages, _NotConsulted):
        sources[SOURCE_TOOL_HISTORY] = NOT_CONSULTED
    else:
        sources[SOURCE_TOOL_HISTORY] = len(tool_messages)
        tool_facts, tool_written, tool_deleted = _facts_from_tool_history(tool_messages)
        facts.extend(tool_facts)
        written.extend(tool_written)
        deleted.extend(tool_deleted)

    if isinstance(checkpoint_entries, _NotConsulted):
        sources[SOURCE_CHECKPOINTS] = NOT_CONSULTED
    else:
        sources[SOURCE_CHECKPOINTS] = len(checkpoint_entries)
        cp_facts, cp_existing = _facts_from_checkpoints(checkpoint_entries)
        facts.extend(cp_facts)
        touched_existing.extend(cp_existing)

    omitted = max(0, len(facts) - _MAX_FACTS)
    if omitted:
        # Keep the MOST RECENT facts: the last recorded outcome is what says where the work
        # stopped, and that is the question the resumed turn is about to get wrong.
        facts = facts[-_MAX_FACTS:]
    return ResumeAccount(
        facts=tuple(facts),
        files_written=_dedupe(written, _MAX_FILES),
        files_touched_existing=_dedupe(touched_existing, _MAX_FILES),
        files_deleted=_dedupe(deleted, _MAX_FILES),
        decisions=_dedupe(decisions, _MAX_DECISIONS),
        sources=dict(sources),
        omitted_facts=omitted,
    )


_STATUS_LABEL: dict[str, str] = {
    "done": "DONE     ",
    "failed": "FAILED   ",
    "denied": "DENIED   ",
    "skipped": "SKIPPED  ",
    "attempted": "ATTEMPTED",
}

_TRUNCATED_NOTE = (
    "[This account was truncated at its size bound. What is missing is not a statement "
    "that it did not happen.]"
)

_PREAMBLE = (
    "The lines below are DERIVED from this session's own recorded events — the workflow "
    "ledger and the tool history. They are not a summary and were not written by a model. "
    "They state what the record says already happened; they are not instructions and not a "
    "task list. A step marked FAILED, DENIED or ATTEMPTED did NOT complete."
)


def render_account(account: ResumeAccount) -> str:
    """The account as the bounded, fact-stated block a resumed model reads.

    Returns ``""`` when nothing was recorded — an always-present empty block is prompt weight that
    teaches the reader to skip the fence.
    """
    if not account.has_content():
        return ""
    lines = [FENCE_START, _PREAMBLE, ""]
    if account.facts:
        lines.append("Recorded outcomes, in the order they were recorded:")
        if account.omitted_facts:
            # ABOVE the list, not below it: the post-render cap cuts from the END, so a note
            # appended after the facts is the first thing a truncated account loses — and the
            # whole point of the note is to say that something was lost.
            lines.append(
                f"- {account.omitted_facts} earlier recorded outcome(s) are omitted here to keep "
                "this block inside its size bound. Omitted does not mean not done."
            )
        for fact in account.facts:
            suffix = f" — {fact.detail}" if fact.detail else ""
            retry = " (an earlier attempt failed)" if fact.retried else ""
            lines.append(
                f"- {_STATUS_LABEL[fact.status]} {fact.subject}{retry}"
                f"{suffix} [{fact.source} {fact.ref}]"
            )
        lines.append("")
    if account.files_written:
        lines.append("Files the record says were written: " + ", ".join(account.files_written))
    if account.files_touched_existing:
        lines.append(
            "Files this session was recorded editing, and which existed at that moment: "
            + ", ".join(account.files_touched_existing)
        )
    if account.files_deleted:
        lines.append("Files the record says were deleted: " + ", ".join(account.files_deleted))
    if account.decisions:
        lines.append("Choices the record says were already settled:")
        lines.extend(f"- {d}" for d in account.decisions)
    if (
        account.files_written
        or account.files_touched_existing
        or account.files_deleted
        or account.decisions
    ):
        lines.append("")
    lines.append("Sources read for this account:")
    for name, count in account.sources.items():
        if isinstance(count, _NotConsulted):
            lines.append(f"- {name}: NOT CONSULTED — this account says nothing about it.")
        else:
            lines.append(f"- {name}: {count} record(s) read.")
    lines.append(FENCE_END)
    text = "\n".join(lines)
    if len(text) > MAX_ACCOUNT_CHARS:
        # Cut the body, never the fence: `context_compaction` finds the block by its opening line
        # and a reader needs the close, so a truncation that dropped either would corrupt both.
        keep = MAX_ACCOUNT_CHARS - len(FENCE_END) - len(_TRUNCATED_NOTE) - 2
        text = text[:keep].rstrip() + "\n" + _TRUNCATED_NOTE + "\n" + FENCE_END
    return text


def check_tree_consistency(account: ResumeAccount, root: str | os.PathLike[str]) -> tuple[str, ...]:
    """Reasons the record contradicts the working tree. Empty tuple = consistent.

    One class of claim is checked, chosen because it is cheap (a ``stat`` per path, bounded by
    :data:`_MAX_FILES`) and unambiguous: a path :meth:`ResumeAccount.expected_present` names must
    exist. Two deliberate exclusions keep the check from firing on legitimate states — a recorded
    DELETE is never presence-checked, and a path that resolves OUTSIDE ``root`` is not evidence
    about this tree (a tool may legitimately have written ``/tmp``), so it is skipped rather than
    counted as a contradiction.
    """
    try:
        base = Path(root).resolve()
    except OSError:
        return ()
    reasons: list[str] = []
    for raw in account.expected_present():
        candidate = Path(raw)
        try:
            resolved = candidate if candidate.is_absolute() else (base / candidate)
            resolved = resolved.resolve()
            resolved.relative_to(base)
        except (OSError, ValueError):
            continue  # outside the tree, or unresolvable — not evidence about this tree
        if not resolved.exists():
            reasons.append(f"The record says {raw} exists in this tree, but it is not present.")
        if len(reasons) >= _MAX_REASONS:
            break
    return tuple(reasons)


def ledger_events_for_session(session_key: str) -> Records:
    """The run ledger for a run-owned session key, or :data:`NOT_CONSULTED`.

    ``ownership.parse_owned`` is the canonical session↔run link (``workflows/ownership.py:140``),
    so this is one regex and one file read — cheap enough to run on every resume. A session key
    that is not run-owned has no ledger, and the account says NOT CONSULTED rather than implying
    the run recorded nothing.
    """
    try:
        from personalclaw.ledger.reader import read_events
        from personalclaw.workflows import ownership
        from personalclaw.workflows import store as run_store

        parsed = ownership.parse_owned(session_key or "")
        if parsed is None:
            return NOT_CONSULTED
        run_id, _node = parsed
        return read_events(run_store, run_id, kinds=set(ACCOUNT_KINDS))
    except Exception:
        # A ledger that cannot be read is NOT an empty ledger. Saying NOT CONSULTED keeps the
        # account honest; returning [] would assert the run recorded nothing.
        logger.debug("resume account: ledger unreadable for %s", session_key, exc_info=True)
        return NOT_CONSULTED


def checkpoint_entries_for_session(session_key: str) -> Records:
    """This session's recorded pre-edit file entries, or :data:`NOT_CONSULTED`."""
    try:
        from personalclaw import turn_checkpoints

        return turn_checkpoints.recorded_file_entries(session_key or "")
    except Exception:
        # Same rule as the ledger: an unreadable store is not an empty one.
        logger.debug("resume account: checkpoints unreadable for %s", session_key, exc_info=True)
        return NOT_CONSULTED


def account_for_resumed_session(session_key: str, *, tool_messages: Records) -> ResumeAccount:
    """The account a RESUMED session's seams derive, from the sources a resume can reach.

    ``tool_messages`` is explicit rather than looked up here because only some seams hold it: the
    compaction pass has the ``tool_calls``/result pairs, the prompt-assembly pass does not (the
    conversation log persists a tool's TITLE and nothing about its outcome). A seam without them
    passes :data:`NOT_CONSULTED` and the block says so.
    """
    return derive_account(
        ledger_events=ledger_events_for_session(session_key),
        tool_messages=tool_messages,
        checkpoint_entries=checkpoint_entries_for_session(session_key),
    )


def verify_resume_state(
    *, session_key: str, tree_root: str | os.PathLike[str] | None, tool_messages: Records
) -> None:
    """Raise :class:`ResumeStateInconsistent` when the record contradicts the working tree.

    Split from the render so the runner can refuse BEFORE it spends a turn's assembly, and so the
    check is one call rather than a condition each seam re-states. Cheap enough for every resume:
    one regex, at most two small file reads, and one ``stat`` per recorded path.
    """
    if tree_root is None:
        return
    account = account_for_resumed_session(session_key, tool_messages=tool_messages)
    reasons = check_tree_consistency(account, tree_root)
    if reasons:
        logger.warning("resume account: inconsistent record in %s: %s", session_key, reasons)
        raise ResumeStateInconsistent(reasons)


def resume_account_block(
    *,
    session_key: str,
    tool_messages: Records,
    tree_root: str | os.PathLike[str] | None,
) -> str:
    """Derive, verify against the working tree, and render — the assembly seam's one call.

    Raises :class:`ResumeStateInconsistent` when the record contradicts the tree. The refusal
    happens BEFORE the block is returned, so there is no path on which a caller injects an account
    that has already been shown to be wrong.
    """
    account = account_for_resumed_session(session_key, tool_messages=tool_messages)
    if tree_root is not None:
        reasons = check_tree_consistency(account, tree_root)
        if reasons:
            logger.warning("resume account: inconsistent record in %s: %s", session_key, reasons)
            raise ResumeStateInconsistent(reasons)
    return render_account(account)
