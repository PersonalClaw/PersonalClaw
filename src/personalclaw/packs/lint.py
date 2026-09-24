"""Referential-integrity + parse linter for an imported pack (AGENT-PACKS §3.2).

The import inverse of the export's closure walker: before ANY commit, every reference a
pack's components declare must resolve — to another component the pack carries, to a
``requirements`` row the pack names (the recipient satisfies/substitutes it), or to a
component already installed in THIS home. A pack that references a component it neither
bundles, declares as a requirement, nor finds locally would install a dependent whose
first run dies on a missing leaf nobody can name — so that pack fails the lint here,
BEFORE a single byte is written.

Two checks, both run at inspect time (dry-run) so a refusal costs no disk state:

* **Referential integrity** — each ``depends_on`` edge resolves in-pack / to a
  requirement / to a local component. An unresolved edge is an ERROR (blocks import).
* **Parse-lint** — every component's SHIPPED bytes parse in their declared format
  (JSON for templates/agent-json, YAML for prompts, frontmatter-fenced markdown for
  skills/agent personas). Anthropic's own repo once shipped a broken ``.mcp.json``;
  a component that will not parse is caught here, not at first use. An unparseable
  component is an ERROR.

Severity mirrors the supply-chain scanner's force semantics (:mod:`packs.import_`):
ERROR findings block import unconditionally; WARNING findings are consent-overridable.
The linter itself makes no policy decision — it reports; the importer decides.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence


class _Component(Protocol):
    """The component shape the linter reads — the parsed manifest row (see
    :class:`packs.build.PackComponent`), duck-typed so lint has no import cycle."""

    kind: str
    id: str
    path: str
    depends_on: list[str]

    @property
    def ref(self) -> str: ...


@dataclass
class LintFinding:
    """One integrity/parse problem, tied to the component ref it concerns."""

    severity: str  # "error" (blocks) | "warning" (consent-overridable)
    code: str  # stable id: "unresolved_ref" | "parse_error" | "duplicate_ref"
    ref: str  # the component this is about ("template:cfo-monthly")
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "ref": self.ref,
            "detail": self.detail,
        }


@dataclass
class LintReport:
    """The linter's verdict. ``ok`` is False iff any ERROR finding is present — the
    importer refuses on ``not ok`` (WARNING findings alone are consent-overridable)."""

    findings: list[LintFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[LintFinding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[LintFinding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "findings": [f.to_dict() for f in self.findings]}


def _parse_lint(path: str, raw: bytes) -> str:
    """Return an error string if ``raw`` does not parse in the format ``path`` declares;
    "" when it parses (or is a format we don't structurally parse).

    The format is chosen by the pack-relative path suffix — the same suffixes
    :mod:`packs.build` writes (``templates/*.json``, ``prompts/*.yaml``,
    ``skills/*/SKILL.md``, ``agents/*.md``). A component whose bytes are not valid
    UTF-8 is itself an error (no §1 component is binary text)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "component is not valid UTF-8 text"

    lower = path.lower()
    if lower.endswith(".json"):
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            return f"invalid JSON: {exc}"
        return ""
    if lower.endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore
        except ImportError:  # pragma: no cover - PyYAML is a runtime dep
            return ""  # can't parse-lint without the parser; don't false-fail
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return f"invalid YAML: {exc}"
        return ""
    if lower.endswith(".md"):
        # Skills and agent personas are frontmatter-fenced markdown; a body with no
        # opening/closing ``---`` fence is not a component shape either store can load.
        stripped = text.lstrip("﻿")
        if not stripped.startswith("---"):
            return "markdown component missing opening frontmatter fence (---)"
        if stripped.find("\n---", 3) == -1:
            return "markdown component frontmatter is not closed with ---"
        return ""
    return ""


def lint_pack(
    components: Sequence[_Component],
    requirements: Sequence[Any],
    payloads: dict[str, bytes],
    *,
    local_resolver: Callable[[str], bool] | None = None,
) -> LintReport:
    """Lint a parsed pack for referential integrity + component parseability.

    ``components`` are the parsed manifest rows (each carrying ``depends_on`` edges);
    ``requirements`` are the manifest's named-but-not-included rows; ``payloads`` maps
    each component's pack-relative path to its exact bytes. ``local_resolver`` — when
    given — answers "is this ``kind:id`` ref already installed in THIS home?"; a ref that
    resolves locally is satisfied even though the pack doesn't carry it.

    An edge resolves iff it is (1) a component in the pack, (2) a declared requirement,
    or (3) locally present. An edge that satisfies none is an unresolved-reference ERROR
    — the pack would install a dependent whose leaf is absent. Every component's bytes
    are parse-linted; an unparseable component is an ERROR. Returns a :class:`LintReport`
    (``ok`` False iff any ERROR)."""
    findings: list[LintFinding] = []

    in_pack: set[str] = set()
    for comp in components:
        ref = comp.ref
        if ref in in_pack:
            findings.append(
                LintFinding("error", "duplicate_ref", ref, "component ref appears more than once")
            )
        in_pack.add(ref)

    # A requirement is named as "kind:id" — a dependent may resolve to one (the recipient
    # satisfies or substitutes it), so it counts as a satisfiable target for integrity.
    req_refs: set[str] = {f"{getattr(r, 'kind', '')}:{getattr(r, 'id', '')}" for r in requirements}

    resolve = local_resolver or (lambda _ref: False)
    for comp in components:
        # Parse-lint the shipped bytes in the component's declared format.
        raw = payloads.get(comp.path)
        if raw is None:
            findings.append(
                LintFinding(
                    "error",
                    "missing_payload",
                    comp.ref,
                    f"manifest lists {comp.path!r} but the archive carries no such member",
                )
            )
        else:
            err = _parse_lint(comp.path, raw)
            if err:
                findings.append(LintFinding("error", "parse_error", comp.ref, err))

        # Referential integrity: every declared edge must resolve somewhere.
        for edge in comp.depends_on:
            if edge in in_pack or edge in req_refs:
                continue
            if resolve(edge):
                continue
            findings.append(
                LintFinding(
                    "error",
                    "unresolved_ref",
                    comp.ref,
                    f"depends on {edge!r} which the pack does not carry, does not declare "
                    "as a requirement, and is not installed locally",
                )
            )

    return LintReport(findings=findings)
