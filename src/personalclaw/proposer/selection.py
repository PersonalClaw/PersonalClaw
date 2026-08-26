"""Target selection: a DIFFERENT cataloged runner than the one that stalled (§4.1).

The whole value of a second opinion is that it comes from somewhere else. So the exclusion is
structural, not advisory: :func:`select_target` removes the origin runner from the candidate set
before any other filter runs, and there is no argument, config value or tie-break that can put it
back. When the exclusion empties the set, the answer is ``None`` — the caller degrades to the
``subagent`` backend (a fresh PClaw brain, still not the one that stalled) rather than re-asking
the runner that already failed.

Filters, in order:

1. **exclusion** — the origin runner, matched on both catalog id and ``runtime_id`` so
   ``acp:gemini-cli`` and ``gemini-cli`` name the same exclusion.
2. **health evidence** — a runner with no recorded probe, a failing probe, or stale evidence is
   not a credible second opinion. ``None`` evidence means NOT MEASURED, never "fine".
3. **required capabilities** — persisted from the runner's own discovery handshake.
4. **binding order** — the user's preference breaks remaining ties; unlisted candidates follow
   in catalog order.
"""

from __future__ import annotations

from dataclasses import dataclass

from personalclaw.agents.runners import (
    HealthEvidence,
    RunnerDefinition,
    catalog,
    evidence_is_stale,
    load_capabilities,
    load_evidence,
)


@dataclass(frozen=True)
class Candidate:
    """One considered runner and why it was kept or dropped — the legibility half of selection."""

    runner_id: str
    eligible: bool
    reason: str
    evidence: HealthEvidence | None = None


@dataclass(frozen=True)
class Selection:
    """The chosen target (or none) plus the full considered set, for the cockpit and the SEL row."""

    target: RunnerDefinition | None
    considered: tuple[Candidate, ...] = ()
    excluded: str = ""

    @property
    def runner_id(self) -> str:
        return self.target.id if self.target else ""


def _aliases(name: str) -> set[str]:
    """Every spelling of one runner: bare id, ``acp:`` runtime id, and the reverse."""
    text = (name or "").strip().lower()
    if not text:
        return set()
    out = {text}
    if text.startswith("acp:"):
        out.add(text[4:])
    else:
        out.add(f"acp:{text}")
    return out


def is_same_runner(a: str, b: str) -> bool:
    """True when two spellings name the same runner — the exclusion's matching rule."""
    return bool(_aliases(a) & _aliases(b))


def _has_capabilities(runner_id: str, required: tuple[str, ...]) -> tuple[bool, str]:
    if not required:
        return True, ""
    caps = load_capabilities(runner_id) or {}
    missing = [c for c in required if not caps.get(c)]
    if missing:
        return False, "missing capability: " + ", ".join(missing)
    return True, ""


def select_target(
    *,
    exclude_runner: str,
    required_capabilities: tuple[str, ...] = (),
    binding_order: tuple[str, ...] = (),
    require_health: bool = True,
) -> Selection:
    """Pick the second opinion's runner, or ``None`` when nothing eligible remains.

    ``exclude_runner`` is dropped FIRST and unconditionally. ``require_health=False`` is for a
    manual, user-initiated handoff where the user picked the target themselves and an unprobed
    runner should not be silently refused — it still cannot reintroduce the excluded runner.
    """
    rows = catalog()
    considered: list[Candidate] = []
    eligible: list[RunnerDefinition] = []
    for runner_id, defn in rows.items():
        if is_same_runner(runner_id, exclude_runner) or is_same_runner(
            defn.runtime_id, exclude_runner
        ):
            considered.append(
                Candidate(
                    runner_id=runner_id,
                    eligible=False,
                    reason=(
                        "this is the runner that stalled — a second opinion must come "
                        "from somewhere else"
                    ),
                )
            )
            continue
        evidence = load_evidence(runner_id)
        if require_health:
            if evidence is None:
                considered.append(
                    Candidate(
                        runner_id=runner_id,
                        eligible=False,
                        reason="no health probe on record (not measured, not assumed healthy)",
                    )
                )
                continue
            if not evidence.ok:
                considered.append(
                    Candidate(
                        runner_id=runner_id,
                        eligible=False,
                        reason=f"last probe failed: {evidence.error or 'unknown error'}",
                        evidence=evidence,
                    )
                )
                continue
            if evidence_is_stale(evidence):
                considered.append(
                    Candidate(
                        runner_id=runner_id,
                        eligible=False,
                        reason="health evidence is stale — re-probe before trusting it",
                        evidence=evidence,
                    )
                )
                continue
        ok, why = _has_capabilities(runner_id, required_capabilities)
        if not ok:
            considered.append(
                Candidate(runner_id=runner_id, eligible=False, reason=why, evidence=evidence)
            )
            continue
        considered.append(
            Candidate(runner_id=runner_id, eligible=True, reason="eligible", evidence=evidence)
        )
        eligible.append(defn)

    order = {name.strip().lower(): i for i, name in enumerate(binding_order) if name.strip()}
    eligible.sort(key=lambda d: (order.get(d.id.lower(), len(order)), d.id))
    target = eligible[0] if eligible else None
    # Belt and braces: the sort above cannot resurrect the excluded runner, but this is the one
    # invariant whose violation would silently defeat the whole atom, so it is also checked here.
    if target is not None and is_same_runner(target.id, exclude_runner):  # pragma: no cover
        target = None
    return Selection(target=target, considered=tuple(considered), excluded=exclude_runner or "")
