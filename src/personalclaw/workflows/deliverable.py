"""A run's DOCUMENT deliverable — the run-side answer to ``GET /api/loops/{id}/report``.

PP-16 unit 1. The loop cockpit has had this since loops shipped: ``loop/store.read_deliverable``
resolves the kind's declared document (``REPORT.md`` / ``MONITOR_LOG.md`` / ``DESIGN.md``) and
``read_log`` returns the worker's cumulative ``FINDINGS.md``, both redacted, both served off one
route. Measured on ``origin/main`` before this module, the run side answered NONE of it: of the 26
run routes, none served a document, and ``service.outbox`` — the closest thing — lists *published
artifacts*, which is a different noun (a run's document is a file its worker maintains in place; an
artifact is something it deliberately published).

Everything here is a READ over files the run already has. No store, no new event kind, no state
invented and none retired — the same posture the ledger-rails third took, and the reason this unit
was the one that was agent-closable while the other four wait on rulings.

**THE MAPPING IS DRIVEN, NOT ASSERTED.** The filename does not live in a constant here. It is
computed by walking ``loop_aliases`` FORWARD — every ``(kind, variant)`` pair the alias table
declares — and asking that kind's own strategy, through the same ``deliverable_name(loop)`` call
``loop/store.read_deliverable`` and ``loop/watchdog._deliverable_file`` make. So a kind that renames
its document renames it here too, and a kind added to the alias table appears here with no edit.
The direction stays the one ``loop_aliases`` mandates: a kind resolves to a template, never the
reverse. This module never asks "which kind was this template", it asks "which templates do the
kinds produce", and inverts its own forward answer.

**ABSENT IS NOT ZERO, AND ABSENT GETS NAMED.** Five different facts all render as "no document" if
you are careless, and only one of them is a worker that has not written yet:

* ``TEMPLATE_UNKNOWN`` — this run's template is not one a loop kind resolves to at all (26 of the
  33 bundled templates are not loop kinds). Its document name is UNKNOWN, which is not the same as
  it having none.
* ``KIND_HAS_NO_DOCUMENT`` — the template IS a loop kind's, and that kind declares ``""``:
  ``goal-pursuit-verifiable``, ``code-project`` and ``general-project`` produce a passing check or a
  diff, and the code IS the output. A named, declared absence.
* ``NOT_WRITTEN`` — the name is known, a readable root exists, and no such file is in it.
* ``NO_ROOT`` — the run has no directory to read at all (never launched, or swept).
* ``UNREADABLE`` — the file is there and the read failed. Reported, never silently blanked.

**Money is deliberately not here, and that is issue #2566.** ``ledger.reader.run_totals`` reports
``cost_usd 0.0`` / ``tokens 0`` for a LOOP, because ``LoopJournal.cycle`` writes no money keys at
all (loop money lives in ``usage/turns.jsonl`` via ``loop_spend``), and ``introspection.RunStats``
has the same shape. PP-16 retires the loop noun ONTO the run noun, so loop-backed runs will flow
through every run-side surface — and "what did this document cost" answered as ``$0.00`` on the one
page a user opens to find out is the worst place for that bug to land. Three packages consume that
contract and changing it needs an owner ruling, so this payload carries no money field rather than
carrying one that would read zero. The rail (``tests/test_pp16_run_deliverable.py``) asserts the
absence, so a later session cannot add one without meeting the finding.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── the named absences ───────────────────────────────────────────────────────

#: No loop kind resolves to this run's template, so no kind declares its document name. UNKNOWN,
#: not none — the distinction that keeps a bespoke template from being reported as documentless.
TEMPLATE_UNKNOWN = "template_unknown"

#: The template IS a loop kind's, and the kind declares no document: the check or the diff is the
#: output (verifiable goals, code, general). A DECLARED absence, so it reads differently from one
#: we merely failed to resolve.
KIND_HAS_NO_DOCUMENT = "kind_has_no_document"

#: The name is known and a readable root exists — the worker has not written it yet. The common
#: case, and the only one of the five that time alone resolves.
NOT_WRITTEN = "not_written"

#: The run has no directory to read from (never launched, or retention swept it).
NO_ROOT = "no_root"

#: The file exists and could not be read. Surfaced rather than folded into `not_written`, because a
#: permission problem that renders as "not written yet" is a bug a user will wait out forever.
UNREADABLE = "unreadable"

#: Every reason this module can report, so a typo is a test failure rather than an unhandled string
#: on the wire. The FE renders one sentence per member.
ABSENT_REASONS: frozenset[str] = frozenset(
    {TEMPLATE_UNKNOWN, KIND_HAS_NO_DOCUMENT, NOT_WRITTEN, NO_ROOT, UNREADABLE}
)

#: Where a document was found / would be looked for. The run's provisioned workspace comes FIRST,
#: mirroring ``loop/watchdog._deliverable_file``: the loop brief directs a worker to write its
#: deliverable into the BOUND workspace when there is one, and only an unbound loop writes into its
#: own dir. Resolving the run dir first would miss the file for every isolated run.
ROOT_WORKSPACE = "workspace"
ROOT_RUN_DIR = "run_dir"

#: Bytes of document served inline. A deliverable is a markdown document a human reads, and the
#: panel renders it in one response — but a runaway worker can append forever, and an unbounded read
#: into a JSON body is a memory bound the gateway does not otherwise have. Truncation is REPORTED
#: (``truncated`` plus the real ``bytes``), never silent: a document that quietly stops mid-sentence
#: is indistinguishable from a worker that stopped writing.
MAX_DOC_BYTES = 512 * 1024

#: Longest unbroken non-whitespace run served (and redacted) intact. A byte ceiling alone does NOT
#: bound the cost of this read, which is the measurement that put this constant here:
#: ``security.redact_credentials`` is roughly QUADRATIC in unbroken-TOKEN length and effectively
#: flat in document length. Measured on this machine, every input ~512 KB:
#:
#:   one unbroken token ................. 111.0 s
#:   1024-char tokens ...................   0.246 s
#:   512-char tokens ....................   0.149 s
#:   ordinary prose (spaces, newlines) ..   0.038 s
#:
#: So a worker that appended one 512 KB base64 blob to its REPORT.md would stall this route for
#: nearly two minutes — on a GET, which is an availability bug rather than a slow page. A run of 512
#: non-space characters is a blob and never prose (the longest URL a document plausibly carries
#: fits well inside it), so each is replaced by a marker naming its length BEFORE redaction runs.
#: Replaced whole rather than truncated: half of a credential is still half of a credential.
#: Reported as ``clipped_blobs``, never silent.
MAX_TOKEN_CHARS = 512

#: One unbroken run longer than :data:`MAX_TOKEN_CHARS`.
_BLOB_RE = re.compile(r"\S{%d,}" % (MAX_TOKEN_CHARS + 1))


# ── the driven kind → filename mapping ───────────────────────────────────────


@dataclass(frozen=True)
class NameSource:
    """Which loop kind (and variant) declared a template's document name.

    Carried on the wire so the panel can say WHERE the name came from. A filename with no
    provenance is a claim; one that names ``goal``/``monitor`` is a reading of the alias table.
    """

    kind: str
    variant: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "variant": self.variant, "name": self.name}


def template_deliverables() -> dict[str, NameSource]:
    """``{template_name: NameSource}`` for every template a loop kind resolves to.

    Built by walking :mod:`personalclaw.workflows.loop_aliases` FORWARD and asking each kind's
    strategy for ``deliverable_name`` — the same call the loop side makes. Templates whose kind
    declares no document are INCLUDED with ``name == ""``: "this kind produces no document" is an
    answer, and dropping the row would make it indistinguishable from a template we never saw.

    A variant row wins over the bare-kind row for the same template, because the bare kind resolves
    to the open-ended variant and the variant hint is the more specific statement. Deterministic:
    the bare kinds are walked first, then the variants, so the outcome does not depend on dict order
    across runs.
    """
    from personalclaw.loop import kinds as kinds_mod
    from personalclaw.loop.loop import Loop
    from personalclaw.workflows import loop_aliases

    try:
        kinds_mod.ensure_loaded()
    except Exception:  # pragma: no cover — a kind that fails to import must not 500 a read
        logger.warning("loop kinds unavailable; no template deliverable names", exc_info=True)
        return {}

    out: dict[str, NameSource] = {}
    pairs: list[tuple[str, str]] = [(k, "") for k in sorted(loop_aliases.KIND_TO_TEMPLATE)]
    pairs += sorted(loop_aliases.VARIANT_HINTS)
    for kind, variant in pairs:
        template = loop_aliases.resolve_kind(kind, variant=variant)
        if not template:
            continue
        name = _declared_name(kinds_mod, Loop, kind, variant)
        if name is None:
            continue
        # A variant row overwrites the bare-kind row it shares a template with; a bare row never
        # overwrites a variant one (variants are walked second, so this ordering is the rule).
        out[template] = NameSource(kind=kind, variant=variant, name=name)
    return out


def _declared_name(kinds_mod: Any, loop_cls: Any, kind: str, variant: str) -> str | None:
    """What ``kind``'s strategy calls its document for this variant, or None if it cannot answer.

    The variant IS the goal type on the loop side (``kind_config['goal_type']``), which is what
    ``goal.deliverable_name`` reads — so the synthetic loop handed to the strategy carries it there
    rather than anywhere new. None (not ``""``) when the strategy raised or is missing, because "we
    could not ask" and "it answered none" are different facts.
    """
    strategy = kinds_mod.get_or_none(kind)
    if strategy is None:
        return None
    namer = getattr(strategy, "deliverable_name", None)
    if namer is None:
        return ""
    try:
        return str(namer(loop_cls(id="", name="", kind=kind, task="", kind_config=_cfg(variant))))
    except Exception:
        logger.warning("deliverable_name failed for kind %r variant %r", kind, variant)
        return None


def _cfg(variant: str) -> dict[str, Any]:
    return {"goal_type": variant.replace("-", "_")} if variant else {}


# ── the resolved name for one run ────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedName:
    """The document name a run's template produces, or the named reason there is none."""

    #: The filename, or "" when there is none to look for.
    name: str
    #: A member of :data:`ABSENT_REASONS` when ``name`` is "", else "".
    reason: str
    #: Which kind declared it, when one did.
    source: NameSource | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name or None,
            "reason": self.reason or None,
            "declared_by": self.source.to_dict() if self.source else None,
        }


def resolve_name(workflow_name: str) -> ResolvedName:
    """The document name for a run of ``workflow_name``, with its provenance or its reason."""
    table = template_deliverables()
    source = table.get(workflow_name)
    if source is None:
        return ResolvedName(name="", reason=TEMPLATE_UNKNOWN)
    if not source.name:
        return ResolvedName(name="", reason=KIND_HAS_NO_DOCUMENT, source=source)
    return ResolvedName(name=source.name, reason="", source=source)


# ── reading a document out of a run's roots ──────────────────────────────────


@dataclass
class Document:
    """One document slot: present with content, or absent with a NAMED reason. Never both."""

    #: The filename looked for, or None when no name could be resolved.
    name: str | None
    present: bool = False
    #: The redacted body. ``None`` — never ``""`` — when absent: an empty string is a document
    #: someone wrote nothing into, which is a real (if useless) observation.
    content: str | None = None
    #: The file's real size on disk, even when ``content`` was truncated.
    bytes: int | None = None
    modified_at: float | None = None
    truncated: bool = False
    #: How many blob-shaped runs were replaced before redaction (:data:`MAX_TOKEN_CHARS`).
    clipped_blobs: int = 0
    #: Which root it was found under (:data:`ROOT_WORKSPACE` / :data:`ROOT_RUN_DIR`).
    found_in: str | None = None
    #: A member of :data:`ABSENT_REASONS` when ``present`` is False.
    absent_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "present": self.present,
            "content": self.content,
            "bytes": self.bytes,
            "modified_at": self.modified_at,
            "truncated": self.truncated,
            "clipped_blobs": self.clipped_blobs,
            "found_in": self.found_in,
            "absent_reason": self.absent_reason,
        }


@dataclass(frozen=True)
class Root:
    """One directory a document may live in, and whether it exists right now."""

    kind: str
    path: str
    exists: bool

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "path": self.path, "exists": self.exists}


@dataclass
class Roots:
    """The ordered roots for one run. Workspace first — see :data:`ROOT_WORKSPACE`."""

    entries: list[Root] = field(default_factory=list)

    @property
    def readable(self) -> list[Root]:
        return [r for r in self.entries if r.exists]

    def to_dict(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self.entries]


def run_roots(run: Any) -> Roots:
    """Where a run's documents can be: its provisioned workspace, then its own run dir.

    Workspace-first mirrors ``loop/watchdog._deliverable_file`` exactly, and for the same measured
    reason: the brief directs a worker to write the document into the bound workspace when there is
    one, so resolving the run dir first finds nothing for every isolated run.
    """
    from personalclaw.workflows import provisioning, store

    entries: list[Root] = []
    try:
        workspace = str((provisioning.workspace_state(run) or {}).get("path", "") or "")
    except Exception:  # pragma: no cover — a malformed record must not 500 a read
        logger.debug("workspace state unreadable", exc_info=True)
        workspace = ""
    if workspace:
        entries.append(Root(ROOT_WORKSPACE, workspace, _is_dir(workspace)))
    run_id = str(getattr(run, "id", "") or "")
    if run_id:
        path = str(store.run_dir(run_id))
        entries.append(Root(ROOT_RUN_DIR, path, _is_dir(path)))
    return Roots(entries)


def _is_dir(path: str) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:  # pragma: no cover — an unstattable path is simply not a root
        return False


def read_document(roots: Roots, name: str, *, reason: str = "") -> Document:
    """Read ``name`` out of the first root that holds it, redacted, or report a NAMED absence.

    ``reason`` short-circuits: pass the resolver's reason when there is no name to look for, and it
    is returned verbatim rather than re-derived — the resolver knows *why* better than a stat does.

    Redacted through ``ledger.redaction.redact``, the same recursive redactor the run journal
    writer uses and the same screens ``loop/files._redact_str`` applies to the loop side's copy.
    Reused rather than re-derived: a worker-authored document is prose about whatever it was working
    on, and a pasted token in a REPORT.md is exactly how a credential reaches a screenshot.

    Confined: the resolved file must sit inside the root it was reached through. ``name`` comes off
    a strategy declaration rather than a request today, so this is a floor rather than a fix — but a
    document name is one refactor away from being user-settable (``kind_config
    ['primary_deliverable']`` already is, on the loop side), and a traversal that becomes reachable
    later is a traversal.
    """
    if not name:
        return Document(name=None, absent_reason=reason or NOT_WRITTEN)
    readable = roots.readable
    if not readable:
        return Document(name=name, absent_reason=NO_ROOT)
    for root in readable:
        target = _confined(root.path, name)
        if target is None or not target.is_file():
            continue
        try:
            raw = target.read_bytes()
            stat = target.stat()
        except OSError:
            logger.warning("run document %s unreadable under %s", name, root.kind, exc_info=True)
            return Document(name=name, absent_reason=UNREADABLE, found_in=root.kind)
        body, clipped = _clip_blobs(raw[:MAX_DOC_BYTES].decode("utf-8", errors="replace"))
        return Document(
            name=name,
            present=True,
            content=_redact(body),
            bytes=len(raw),
            modified_at=stat.st_mtime,
            truncated=len(raw) > MAX_DOC_BYTES,
            clipped_blobs=clipped,
            found_in=root.kind,
        )
    return Document(name=name, absent_reason=NOT_WRITTEN)


def _confined(root: str, name: str) -> Path | None:
    """``root/name`` when it resolves INSIDE ``root``, else None."""
    try:
        base = Path(root).resolve()
        target = (base / name).resolve()
        target.relative_to(base)
    except (ValueError, OSError):
        return None
    return target


def _clip_blobs(text: str) -> tuple[str, int]:
    """Replace every unbroken run past :data:`MAX_TOKEN_CHARS` with a marker. Returns the count.

    Runs BEFORE redaction, and that order is the whole point: the redactor is quadratic in
    unbroken-token length, so clipping afterwards would already have paid the cost this exists to
    avoid. Replacing the whole run rather than truncating it also means a clipped blob cannot leave
    a credential's first 512 characters sitting on the page.
    """
    clipped = 0

    def _mark(match: re.Match[str]) -> str:
        nonlocal clipped
        clipped += 1
        return f"[clipped: {len(match.group(0))}-character run with no whitespace]"

    return _BLOB_RE.sub(_mark, text), clipped


def _redact(text: str) -> str:
    from personalclaw.ledger import redact

    result = redact(text)
    return result if isinstance(result, str) else text


# ── whether the run's own template ever asks for the document ────────────────


def instructed_by_spec(spec: Any, name: str) -> bool | None:
    """Does this run's OWN spec name the document its kind declares?

    The reason this exists: measured across the seven bundled templates the five loop kinds resolve
    to, NONE of them mentions ``REPORT.md``, ``MONITOR_LOG.md``, ``DESIGN.md`` or ``RESEARCH.md``
    anywhere. The loop side's brief does (``goal.build_brief`` writes the deliverable name into the
    DoD and the cycle nudge); the template side's prompts do not. So on the run side today, a
    document that is absent is usually absent because nothing ever asked for it — and reporting
    that as "the worker has not written it yet" would send a user to wait for something that is
    never coming.

    ``None`` when there is no name to look for, so "we did not check" stays distinct from "we
    checked and it is not there". A substring scan over the serialized spec rather than a walk of
    prompt fields: the name can legitimately appear in a node prompt, an action argument, a
    workspace setup step or a judge rubric, and a field-by-field walk would answer "no" for the
    ones it had not learned about yet.
    """
    if not name:
        return None
    try:
        import json

        return name in json.dumps(spec, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover — an unserializable spec answers "unknown"
        logger.debug("spec not serializable; cannot check for %s", name)
        return None
