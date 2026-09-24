"""Spec model — the three spec kinds and their frontmatter parsing/validation.

A spec is a markdown file with a YAML frontmatter block (delimited by ``---`` lines)
followed by a free-text body written FOR a coding agent. Three kinds live under
``harness/specs/{rules,scenarios,tasks}/``:

- **rule** (``type: ai-coding-rule``) — one architectural invariant each.
- **scenario** (``type: triage-scenario``) — a diagnosis playbook for a symptom family.
- **task** (``type: task``) — one fix/feature's intent + acceptance contract.

**Design rule that prevents spec rot:** specs reference *stable anchors* only — pytest/
vitest node-ids, path globs, and scanner check-ids — never source line numbers. Line
numbers drift on every edit (they were already all stale in the plan that spawned this
harness); node-ids and globs do not. ``validate`` enforces that the anchors resolve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# The three spec kinds and the ``type:`` frontmatter value each declares. The directory
# a spec lives in must match its declared type (validate enforces this) — a rule file
# dropped in scenarios/ is a mistake, not a silent reclassification.
KIND_RULE = "ai-coding-rule"
KIND_SCENARIO = "triage-scenario"
KIND_TASK = "task"

_TYPE_TO_SUBDIR = {KIND_RULE: "rules", KIND_SCENARIO: "scenarios", KIND_TASK: "tasks"}
_SUBDIR_TO_TYPE = {v: k for k, v in _TYPE_TO_SUBDIR.items()}

# id shape: kebab/underscore slug (rules), or T<session>.<n> / V<session> for task-ish
# ids the roadmap already uses. We keep it permissive but non-empty and space-free.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


class SpecError(Exception):
    """A spec file is malformed (bad frontmatter, wrong type, missing required field).

    Carries the offending path so the CLI can print ``file: message`` without the caller
    threading the path through every call site.
    """

    def __init__(self, path: Path | str, message: str) -> None:
        self.path = Path(path)
        self.message = message
        super().__init__(f"{self.path}: {message}")


@dataclass
class Spec:
    """A parsed spec of any kind. ``meta`` is the raw frontmatter dict; typed accessors
    below pull the fields each kind requires. ``body`` is the post-frontmatter markdown.

    Kept as one dataclass (rather than a class per kind) because the CLI treats specs
    uniformly — load all, validate each by ``type``, index by ``id`` — and the per-kind
    required-field logic is small enough to live in :func:`validate_spec`.
    """

    path: Path
    kind: str  # one of KIND_*
    meta: dict[str, Any]
    body: str

    @property
    def id(self) -> str:
        return str(self.meta.get("id", "")).strip()

    def get_list(self, key: str) -> list[str]:
        """Frontmatter list field as a list of strings (``[]`` when absent).

        Tolerates a scalar written where a list belongs (``requiredTests: foo`` →
        ``["foo"]``) so a single-item spec need not use YAML list syntax.
        """
        val = self.meta.get(key)
        if val is None:
            return []
        if isinstance(val, str):
            return [val]
        if isinstance(val, list):
            return [str(v) for v in val]
        return [str(val)]


def parse_spec(path: str | Path) -> Spec:
    """Parse a single spec file. Raises :class:`SpecError` on malformed frontmatter.

    Does NOT validate field completeness — that is :func:`validate_spec`, so ``explain``/
    ``run`` can load a spec and report *why* it is invalid rather than failing to load.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise SpecError(p, "missing YAML frontmatter (expected a leading '---' block)")
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        raise SpecError(p, f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise SpecError(p, "frontmatter must be a mapping (key: value pairs)")

    kind = str(meta.get("type", "")).strip()
    if kind not in _TYPE_TO_SUBDIR:
        raise SpecError(
            p, f"unknown or missing type {kind!r}; expected one of {sorted(_TYPE_TO_SUBDIR)}"
        )
    return Spec(path=p, kind=kind, meta=meta, body=m.group(2))


def specs_root(repo_root: Path | None = None) -> Path:
    """Absolute path to ``harness/specs`` (repo-root relative)."""
    root = repo_root if repo_root is not None else Path(__file__).resolve().parent.parent
    return root / "harness" / "specs"


def load_specs(root: Path | None = None) -> list[Spec]:
    """Load every ``*.md`` spec under ``harness/specs/{rules,scenarios,tasks}/``.

    Sorted by path for deterministic output. Malformed files raise (surfaced by the CLI)
    rather than being silently skipped — a spec that won't parse is a defect to see, not
    to ignore.
    """
    base = root if root is not None else specs_root()
    out: list[Spec] = []
    for subdir in _TYPE_TO_SUBDIR.values():
        d = base / subdir
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            out.append(parse_spec(f))
    return out


# ── Validation ────────────────────────────────────────────────────────────────

# Required frontmatter keys per kind. Body is required for rules/scenarios (they are
# written for the coding agent); task specs are frontmatter-only by design (§1.1).
_REQUIRED: dict[str, tuple[str, ...]] = {
    KIND_RULE: ("id", "type", "statement", "appliesTo", "source"),
    KIND_SCENARIO: ("id", "type", "symptom", "appliesTo", "acceptance"),
    KIND_TASK: ("id", "type", "title", "intent", "touchedAreas", "acceptance"),
}


@dataclass
class ValidationIssue:
    """One problem found in a spec set. ``level`` is ``error`` (fails validate) or
    ``warning`` (reported, does not fail)."""

    path: Path
    level: str  # "error" | "warning"
    message: str


def validate_spec(spec: Spec, known_ids: set[str] | None = None) -> list[ValidationIssue]:
    """Shape-validate one already-parsed spec. Returns issues (empty == clean).

    Checks: required keys present + non-empty; ``id`` well-formed; the file lives in the
    subdir matching its ``type``; cross-references (``requiredRules``) resolve against
    ``known_ids`` when provided; task ``acceptance`` carries a ``negative`` clause
    (mandatory per §1.1 — the half prose LEDGER entries always drop it).

    Does NOT check that ``requiredTests`` node-ids collect or ``scanner`` check-ids exist
    — those need pytest/the scanner and live in :mod:`harness.validate_refs`, so this
    function stays import-light and unit-testable in isolation.
    """
    issues: list[ValidationIssue] = []

    def err(msg: str) -> None:
        issues.append(ValidationIssue(spec.path, "error", msg))

    for key in _REQUIRED.get(spec.kind, ()):
        val = spec.meta.get(key)
        if val is None or (isinstance(val, (str, list, dict)) and len(val) == 0):
            err(f"missing required field {key!r} for {spec.kind}")

    if spec.id and not _ID_RE.match(spec.id):
        err(f"malformed id {spec.id!r} (no spaces; slug or T<n>.<m>/V<n>)")

    # Directory ↔ type coherence: a rule dropped in scenarios/ is a filing mistake.
    parent = spec.path.parent.name
    expected_type = _SUBDIR_TO_TYPE.get(parent)
    if expected_type and spec.kind != expected_type:
        err(f"type {spec.kind!r} but filed under {parent}/ (expected {expected_type!r})")

    if spec.kind in (KIND_RULE, KIND_SCENARIO) and not spec.body.strip():
        err(f"{spec.kind} must have a body written for the coding agent (why + how)")

    # Task acceptance must be a mapping with a mandatory negative clause.
    if spec.kind == KIND_TASK:
        acc = spec.meta.get("acceptance")
        if isinstance(acc, dict):
            if not acc.get("negative"):
                err("task acceptance is missing the mandatory 'negative' clause (§1.1)")
        elif acc is not None:
            err("task 'acceptance' must be a mapping with 'positive'/'negative' lists")

    # Cross-reference resolution (when the caller supplies the id universe).
    if known_ids is not None:
        for ref in spec.get_list("requiredRules"):
            if ref not in known_ids:
                err(f"requiredRules references unknown spec id {ref!r}")
        scenario_ref = spec.meta.get("scenario")
        if scenario_ref and str(scenario_ref) not in known_ids:
            err(f"scenario references unknown spec id {str(scenario_ref)!r}")

    return issues


def validate_all(specs: list[Spec]) -> list[ValidationIssue]:
    """Validate a whole spec set: per-spec shape + duplicate-id detection across the set.

    ``known_ids`` is the union of all ids, so cross-references resolve set-wide.
    """
    issues: list[ValidationIssue] = []
    ids_seen: dict[str, Path] = {}
    known_ids = {s.id for s in specs if s.id}

    for spec in specs:
        issues.extend(validate_spec(spec, known_ids=known_ids))
        if spec.id:
            if spec.id in ids_seen:
                issues.append(
                    ValidationIssue(
                        spec.path,
                        "error",
                        f"duplicate id {spec.id!r} (also in {ids_seen[spec.id].name})",
                    )
                )
            else:
                ids_seen[spec.id] = spec.path

    return issues
