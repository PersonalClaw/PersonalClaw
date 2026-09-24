"""Fire-wait-verify orchestration for the second-opinion handoff (EXECUTION-ISOLATION §4.1).

One entry point, :func:`run_second_opinion`, doing exactly what §4.1 specifies and in this order:

1. **package** — build the brief with a FRESH diff and write it to a unique file under the
   run/loop dir;
2. **select** — a DIFFERENT cataloged runner than the one that stalled (§3 catalog, health
   evidence, capabilities, the user's binding order for ties), degrading to the ``subagent``
   backend when the exclusion leaves nothing eligible;
3. **fire** — one shot, headless, hard timeout, in the SAME sandbox class as the stalled run;
4. **verify** — re-diff disk; and
5. **decide** — accept only when the re-diff confirms the claimed edits.

Step 5 is the reason this module exists as a single function rather than a helper the consumers
compose themselves: three consumers (loop watchdog, gate node, the cockpit button) must not be
able to hold three different opinions about what "accepted" means. Every outcome — accepted or
not — writes a SEL row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from personalclaw.proposer.backends import (
    ProposerUnavailable,
    RunnerProposerBackend,
    SubagentProposerBackend,
)
from personalclaw.proposer.brief import HandoffBrief, build_brief
from personalclaw.proposer.contract import ProposerBackend, ProposerResult
from personalclaw.proposer.selection import Selection, select_target

#: SEL operation names. Two rows per handoff on purpose: the FIRE is a security-relevant act
#: (an outside brain got a brief and write access to a workspace) whether or not it is later
#: accepted, and the VERDICT is what changed state. One combined row would lose the fire when
#: the collect step crashed.
SEL_OP_FIRE = "second_opinion.fire"
SEL_OP_VERDICT = "second_opinion.verdict"


@dataclass(frozen=True)
class SecondOpinionOutcome:
    """The handoff's whole story: what fired, what it claimed, and whether we took it."""

    accepted: bool
    brief_path: str
    backend: str
    runner_id: str
    origin_runner: str
    result: ProposerResult | None = None
    selection: Selection | None = None
    rejection: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "brief_path": self.brief_path,
            "backend": self.backend,
            "runner_id": self.runner_id,
            "origin_runner": self.origin_runner,
            "rejection": self.rejection,
            "result": self.result.to_dict() if self.result else None,
            "considered": [
                {"runner_id": c.runner_id, "eligible": c.eligible, "reason": c.reason}
                for c in (self.selection.considered if self.selection else ())
            ],
        }


def choose_backend(
    brief: HandoffBrief,
    *,
    required_capabilities: tuple[str, ...] = (),
    binding_order: tuple[str, ...] = (),
    require_health: bool = True,
    timeout_secs: float = 300.0,
) -> tuple[ProposerBackend, Selection]:
    """The eligible runner's backend, or the ``subagent`` fallback. Never the origin runner.

    The fallback triggers on either of two facts, and both are degradations rather than errors:
    no eligible runner survived the exclusion, or the eligible runner has no declared
    non-interactive form (:class:`ProposerUnavailable` from ``prepare`` is checked lazily by the
    caller; here we only pre-screen the dialect so the choice is made once).
    """
    selection = select_target(
        exclude_runner=brief.origin_runner,
        required_capabilities=required_capabilities,
        binding_order=binding_order,
        require_health=require_health,
    )
    if selection.target is not None:
        return RunnerProposerBackend(selection.target, timeout_secs=timeout_secs), selection
    return SubagentProposerBackend(timeout_secs=timeout_secs), selection


async def run_second_opinion(
    *,
    goal: str,
    stuck_at: str,
    ask: str = "",
    workspace: str = "",
    origin_runner: str = "",
    sandbox: str = "none",
    session_key: str = "",
    consumer: str = "",
    attempts: tuple[str, ...] = (),
    files_touched: tuple[str, ...] = (),
    brief_dir: str = "",
    required_capabilities: tuple[str, ...] = (),
    binding_order: tuple[str, ...] = (),
    require_health: bool = True,
    timeout_secs: float = 300.0,
) -> SecondOpinionOutcome:
    """Package → select → fire → verify → decide. SEL-audited at the fire and at the verdict."""
    from personalclaw.sel import sel

    brief = build_brief(
        goal=goal,
        stuck_at=stuck_at,
        ask=ask,
        workspace=workspace,
        origin_runner=origin_runner,
        sandbox=sandbox,
        session_key=session_key,
        consumer=consumer,
        attempts=attempts,
        files_touched=files_touched,
    )
    brief_path = brief.write(brief_dir or brief.workspace)
    backend, selection = choose_backend(
        brief,
        required_capabilities=required_capabilities,
        binding_order=binding_order,
        require_health=require_health,
        timeout_secs=timeout_secs,
    )

    def _audit(operation: str, outcome: str, error: str = "", **extra: Any) -> None:
        sel().log_tool_invocation(
            session_key=session_key or "second-opinion",
            tool_name=operation,
            tool_kind="handoff",
            outcome=outcome,
            downstream_service=backend.name,
            resources=brief_path,
            error=error,
            metadata={
                "origin_runner": origin_runner,
                "target_runner": getattr(backend, "runner_id", backend.name),
                "sandbox": brief.sandbox,
                "consumer": consumer,
                **{k: str(v) for k, v in extra.items()},
            },
        )

    try:
        prepared = await backend.prepare(brief)
    except ProposerUnavailable as exc:
        # The eligible runner cannot be fired one-shot. Fall back rather than fail: a second
        # opinion from a fresh PClaw brain still satisfies "not the runner that stalled".
        _audit(SEL_OP_FIRE, "refused", error=str(exc))
        backend = SubagentProposerBackend(timeout_secs=timeout_secs)
        prepared = await backend.prepare(brief)

    _audit(SEL_OP_FIRE, "allowed", claimed="pending")
    ref = await backend.invoke(prepared)
    result = await backend.collect(ref)

    accepted = bool(result.ok and result.diff_verified)
    rejection = ""
    if not accepted:
        if not result.ok:
            rejection = result.error or "the proposer did not complete"
        else:
            rejection = (
                result.error
                or "the disk re-diff did not confirm the claimed edits, so the handoff was rejected"
            )
    _audit(
        SEL_OP_VERDICT,
        "allowed" if accepted else "denied",
        error=rejection,
        diff_verified=result.diff_verified,
        claimed=",".join(result.claimed_paths),
        verified=",".join(result.verified_paths),
        missing=",".join(result.missing_paths),
    )
    return SecondOpinionOutcome(
        accepted=accepted,
        brief_path=brief_path,
        backend=backend.name,
        runner_id=result.runner_id,
        origin_runner=origin_runner,
        result=result,
        selection=selection,
        rejection=rejection,
    )
