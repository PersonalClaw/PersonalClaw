"""Deterministic template lint — the conventions check (WF2-R15).

`validate_spec` answers "will the engine run this?". This answers a different question: "does
this template follow the conventions that keep a growing library coherent?" A spec can be
perfectly valid and still be a bad template — no description to choose it by, an input with no
help text, a Finding record written by hand instead of cited, no mid-flight steering example.

Why a lint rather than more validation rules: these are **authoring** standards, not run
requirements. Making them validation errors would refuse a user's own half-finished workflow,
which is theirs to leave rough. Making them nothing at all is how a six-template library becomes
a twelve-template library where four templates each define severity differently.

Deterministic on purpose — no model call. Every finding is a structural fact about the spec, so
the lint gives the same answer in CI as on a laptop, and the CI failure names the exact template
and node.

Severity here means:

* **error** — would mislead or block a user of the template (a duplicated convention that has
  already drifted, a reference that cannot resolve);
* **warning** — a real gap worth fixing before shipping (no steering example, thin description).

The `bundled` library is held to `error`-free AND `warning`-free by test. A user's own template is
only ever advised.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from personalclaw.workflows import blocks

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

#: A description shorter than this cannot distinguish one template from another in a picker.
MIN_DESCRIPTION = 40

#: Conventions that MUST be cited rather than restated. The pattern is what a hand-written copy
#: looks like; the block is what should have been referenced instead. Keyed by the convention so a
#: finding can name both.
_DUPLICATED_CONVENTIONS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "the Finding record",
        re.compile(r"A Finding is\s*\{|severity:\s*Critical\|Major\|Minor\|Nit", re.I),
        "finding-record",
    ),
)


@dataclass
class LintFinding:
    """One convention deviation, addressed to a node where it has one."""

    code: str
    message: str
    path: str = ""
    severity: str = SEVERITY_ERROR

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "severity": self.severity,
        }


@dataclass
class LintResult:
    findings: list[LintFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing at ERROR level was found. Warnings do not fail a lint — the
        bundled-library test asserts on `clean` instead."""
        return not any(f.severity == SEVERITY_ERROR for f in self.findings)

    @property
    def clean(self) -> bool:
        """True when nothing at all was found. What the shipped library is held to."""
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "clean": self.clean,
            "findings": [f.to_dict() for f in self.findings],
        }


def lint_template(spec: dict[str, Any], *, bundled: bool = False) -> LintResult:
    """Lint one template spec. Never raises — a lint that crashed on a malformed spec would
    hide every finding it had already collected.

    ``bundled`` enables the checks that only make sense for a SHIPPED template: a user's own
    workflow does not need steering examples or a picker-ready description, and reporting those
    would be noise on something they are still writing.
    """
    res = LintResult()
    if not isinstance(spec, dict):
        res.findings.append(LintFinding("WFL_NOT_AN_OBJECT", "a template must be an object"))
        return res

    _check_block_refs(res, spec)
    _check_duplicated_conventions(res, spec)
    if bundled:
        _check_shipping_metadata(res, spec)
    return res


def _check_block_refs(res: LintResult, spec: dict[str, Any]) -> None:
    """Every `{{block:…}}` must name a block that exists.

    The drift this catches: a block renamed or removed while a template still cites it. The save
    path raises on it, but a lint reports EVERY broken reference at once instead of the first —
    which is what you want when you have just renamed a block.
    """
    available = set(blocks.block_names())
    for name in sorted(blocks.refs_in(spec)):
        if name not in available:
            res.findings.append(
                LintFinding(
                    "WFL_UNKNOWN_BLOCK",
                    f"references shared block {name!r}, which does not exist "
                    f"(available: {', '.join(sorted(available)) or 'none'})",
                )
            )


def _check_duplicated_conventions(res: LintResult, spec: dict[str, Any]) -> None:
    """A convention written by hand where a shared block exists.

    This is the rule the plan states as "repeated boilerplate moves to shared", and it is an
    ERROR rather than a warning because the copies do not stay identical. Three hand-written
    Finding records is how a gate predicate like "no open Critical" quietly stops meaning the
    same thing in two stages.
    """
    for label, pattern, block in _DUPLICATED_CONVENTIONS:
        for path, text in _prompts(spec):
            if pattern.search(text):
                res.findings.append(
                    LintFinding(
                        "WFL_INLINE_CONVENTION",
                        f"{label} is written inline — cite {{{{block:{block}}}}} instead, or the "
                        f"copies drift apart",
                        path,
                    )
                )


def _check_shipping_metadata(res: LintResult, spec: dict[str, Any]) -> None:
    """The metadata a SHIPPED template needs to be findable and drivable."""
    description = str(spec.get("description", "") or "").strip()
    if len(description) < MIN_DESCRIPTION:
        res.findings.append(
            LintFinding(
                "WFL_THIN_DESCRIPTION",
                f"the description is {len(description)} chars — the picker shows this line and "
                f"nothing else, so under {MIN_DESCRIPTION} makes templates hard to tell apart",
                severity=SEVERITY_WARNING,
            )
        )

    for key, param in (spec.get("inputs") or {}).items():
        if not isinstance(param, dict):
            continue
        if not str(param.get("help", "")).strip():
            res.findings.append(
                LintFinding(
                    "WFL_UNDOCUMENTED_INPUT",
                    f"input {key!r} has no `help` — the run dialog shows a bare field name",
                    severity=SEVERITY_WARNING,
                )
            )
        if param.get("required") and param.get("default") not in (None, ""):
            res.findings.append(
                LintFinding(
                    "WFL_REQUIRED_WITH_DEFAULT",
                    f"input {key!r} is required AND has a default — a default means it can be "
                    f"omitted, so the two contradict each other",
                )
            )

    examples = (spec.get("metadata") or {}).get("steering_examples") or []
    events = {str(e.get("event", "")) for e in examples if isinstance(e, dict)}
    if "kickoff" not in events:
        res.findings.append(
            LintFinding(
                "WFL_NO_KICKOFF_EXAMPLE",
                "no `kickoff` steering example — the widget surfaces these and `workflow_plan` "
                "uses them as few-shot, so without one a model guesses how to drive it",
                severity=SEVERITY_WARNING,
            )
        )
    if "mutation" not in events:
        res.findings.append(
            LintFinding(
                "WFL_NO_MUTATION_EXAMPLE",
                "no mid-flight `mutation` steering example — that is the one that teaches a model "
                "editing a RUNNING workflow is normal",
                severity=SEVERITY_WARNING,
            )
        )


def _prompts(spec: dict[str, Any]) -> list[tuple[str, str]]:
    """Every prompt string in a spec, paired with its node path.

    Walks the raw dict rather than a parsed `Node`, so the lint works on a spec the validator
    would reject — which is exactly when an author most wants to see every problem at once.
    """
    out: list[tuple[str, str]] = []

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        node_id = str(node.get("id", "") or "?")
        here = f"{path}.{node_id}" if path else node_id
        cfg = node.get("config") or {}
        for key in ("prompt", "expr", "criteria", "guidance"):
            value = cfg.get(key)
            if isinstance(value, str) and value:
                out.append((here, value))
        # A macro's lens prompts live outside `config.prompt`, so a hand-written convention there
        # would otherwise be invisible to the lint.
        for lens in cfg.get("lenses") or []:
            if isinstance(lens, dict) and isinstance(lens.get("prompt"), str):
                out.append((here, lens["prompt"]))
        for child in node.get("children") or []:
            walk(child, here)
        walk(node.get("body"), here)
        for case in (node.get("cases") or {}).values():
            walk(case, here)
        walk(node.get("default"), here)

    walk(spec.get("root"), "")
    return out
