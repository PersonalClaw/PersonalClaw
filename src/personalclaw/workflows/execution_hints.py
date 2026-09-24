"""The `runtime_hints.execution` half — the engine-enforced execution invariants.

`runtime_hints` is stored opaque on a `WorkflowDef` and split in two by design
(LOOPS-EVOLUTION, Architecture §"Templates + Runtime Behavior Layer"): the `judge` group
is parsed by `judge_contract.hints_from_dict`, and this module owns the `execution` group.

**Only what has a live reader is parsed here.** `depth`, `escalation` and `breaker` are
declared by every loop-family template and read by NOTHING in the engine today — the
middleware that will consume them exists as a decision layer whose call sites are a
separate atom (WF2LOO-7). Parsing them into a dataclass now would build exactly the shape
this program keeps finding: a typed field with no consumer, which reads as enforced
because it parses. So they stay in the raw dict until something reads them, and this
module carries precisely one field.

**`single_active_feature` (R5b) is WIP=1, and the engine REFUSES to violate it** rather
than recording that it did. The refusal has two halves, because there are two ways to
break the invariant:

* *authoring* — a spec that declares WIP=1 and also declares a fan-out wider than one
  item is self-contradictory, and `validator` rejects it (`WF_WIP_CONTRADICTION`). A
  silent clamp would leave the template saying one thing and the engine doing another.
* *runtime* — `tick.frontier` caps every fan-out in the run to one in-flight item, and
  names the items it would not start in `Frontier.wip_held`, which the controller
  journals. Refusing without a record is indistinguishable from forgetting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExecutionHints:
    """The parsed `execution` group. One field, because one field has a reader."""

    #: WIP=1: at most one item of any fan-out is in flight at a time (R5b).
    single_active_feature: bool = False

    @classmethod
    def from_dict(cls, raw: Any) -> ExecutionHints:
        """Parse the `execution` group. A non-dict is no hints, not an error — the group
        is optional and a malformed one must not stop a run from starting."""
        if not isinstance(raw, dict):
            return cls()
        return cls(single_active_feature=_flag(raw.get("single_active_feature")))


def from_runtime_hints(runtime_hints: Any) -> ExecutionHints:
    """The execution hints of a whole `runtime_hints` block (or a spec's, or none)."""
    if not isinstance(runtime_hints, dict):
        return ExecutionHints()
    return ExecutionHints.from_dict(runtime_hints.get("execution"))


def _flag(raw: Any) -> bool:
    """A declared boolean. A string is accepted because a spec round-tripped through YAML
    or an API form can carry `"true"`, and reading that as False would silently disable an
    invariant the author declared."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "yes", "1", "on")
    return bool(raw) if isinstance(raw, (int, float)) else False
