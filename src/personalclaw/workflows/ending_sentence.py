"""The sentence a run ends with when a step's failure is the reason.

Two endings have one. A gate the run STOPS at — a decline, an approval nobody gave, a check that
failed — is `gate_answers.end_at_gate`'s: "“approve” was declined by Keyur, so nothing after it
ran." A failure the run CONTINUES PAST is `for_failures`'s, and it is the commoner of the two:
`on_error: null_continue` is the default, so the steps after a failed step still run and the run
ends `failed`. That ending had no sentence at all — the completion path's terminal write takes none
unless it is given one — so the run page read "Failed" over an empty line, the Workflows list read
"Failed" and nothing else, and a person had to open every step to learn which one broke and that the
rest had run anyway.

Both endings are built from the same parts, so they read alike: a step named by its label (and, in
a fan-out, by its item), its failure as one clause (`clause`), and the steps after it in each
sequence that holds it (`followers`).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterator

from personalclaw.workflows.models import (
    SUCCESS_STATES,
    InstanceState,
    Node,
    NodeKind,
    spec_path,
    walk,
)
from personalclaw.workflows.tick import derive_state, tolerate_failures

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController


def clause(text: str) -> str:
    """A failure's cause as a clause of the run's one-line ending: one line, no closing period —
    a judge's reasoning arrives as prose, and the sentence around it carries its own stop."""
    return " ".join(str(text or "").split()).rstrip(" .")


#: The last segment of an instance path, and what it says about the step's parent: a sequence or
#: parallel child (`.children[i]`), a branch case, or a container body (`.body`, a `foreach`
#: item's `.body#i`, a loop iteration's `.body@i`).
_LAST_SEGMENT = re.compile(r"\.(children\[(\d+)\]|cases\[[^\]]*\]|default|body(?:[#@]\d+)?)$")


def followers(ctl: RunController, path: str, nodes: dict[str, Any]) -> list[str]:
    """Every step after `path` in each SEQUENCE that holds it, innermost first — the steps a
    stopping gate stops, and the steps a failure the run continued past let run. Instance paths,
    so a step inside a `foreach` item is followed by what follows it in THAT item, and then by what
    follows the fan-out in the sequence around it."""
    out: list[str] = []
    cursor = path
    while True:
        match = _LAST_SEGMENT.search(cursor)
        if match is None:
            return out
        parent = cursor[: match.start()]
        container = nodes.get(spec_path(parent))
        if (
            match.group(2) is not None
            and container is not None
            and container.kind == NodeKind.SEQUENCE
        ):
            index = int(match.group(2))
            out.extend(f"{parent}.children[{i}]" for i in range(index + 1, len(container.children)))
        cursor = parent


def step_name(node: Any, inst: Any, path: str) -> str:
    """How a sentence names one step: its label (else its id), quoted, and a fan-out item's own
    label beside it — twelve items of one step are twelve different pieces of work."""
    label = str((getattr(node, "label", "") or getattr(node, "id", "") or "")) or path
    item = str(getattr(inst, "item_label", "") or "") if inst is not None else ""
    return f"“{label}” ({item})" if item else f"“{label}”"


#: The states a step ends in that make its run not succeed — the ones `_ROOT_TO_RUN` reads as a
#: failed or an escalated run. A decline is not one: it ends the run at its gate (`end_at_gate`)
#: and never reaches the completion path.
FAULTS = frozenset(
    {
        InstanceState.FAILED,
        InstanceState.SCOPE_VIOLATION,
        InstanceState.BLOCKED,
        InstanceState.ESCALATED,
    }
)

#: A step in one of these states ran: it did its work, or failed doing it.
_RAN = SUCCESS_STATES | FAULTS

#: How many of the other failed steps a sentence names before it counts the rest.
_NAMED_MAX = 3


def for_failures(ctl: RunController) -> str:
    """The ending of a run that ran to its end with steps that failed, or "" for one that did not.

    When a step after a failed one ran — which is what `on_error: null_continue` did — it says so
    FIRST, then names the first failed step and its cause, then every other one:

        The run continued past “summarize”, which failed: model output was not valid JSON.

        The run continued past “summarize” and “store”. “summarize” failed: model output was not
        valid JSON. “store” failed too.

    First, because the line is read cut short: the Workflows list shows it on one line, and a
    failure's cause can be a model's whole reply, so a clause after it is the part nobody sees.

    A step whose failure the run tolerated is never named, because the failures are followed down
    from the run's own outcome (`_failed_steps`). "Continued past" is claimed only for the steps
    something ran after: a failure in a `parallel` leg had siblings running BESIDE it, and a failed
    last step had nothing after it.
    """
    paths = _failed_steps(ctl)
    if not paths:
        return ""
    nodes = dict(walk(ctl.root))
    went_on = [p for p in paths if _ran_after(ctl, p, nodes)]
    first, rest = paths[0], paths[1:]
    if went_on == [first] and not rest:
        return f"The run continued past {_first_part(ctl, nodes, first, joined=', which')}"
    parts = []
    if went_on:
        parts.append(f"The run continued past {_listed([_name(ctl, nodes, p) for p in went_on])}.")
    parts.append(_first_part(ctl, nodes, first))
    for kind in _KINDS:
        named = [_name(ctl, nodes, p) for p in rest if _kind(ctl.instances[p].state) == kind]
        if named:
            parts.append(f"{_listed(named)} {_verb(kind, plural=len(named) > 1)} too.")
    return " ".join(parts)


def _first_part(ctl: RunController, nodes: dict[str, Node], path: str, *, joined: str = "") -> str:
    """“name” failed: its cause. — or, `joined` to a lead clause, “name”, which failed: …"""
    inst = ctl.instances[path]
    cause = clause(inst.failure.cause_plain if inst.failure else "")
    head = f"{_name(ctl, nodes, path)}{joined} {_verb(_kind(inst.state))}"
    return f"{head}: {cause}." if cause else f"{head}."


def _name(ctl: RunController, nodes: dict[str, Node], path: str) -> str:
    return step_name(nodes.get(spec_path(path)), ctl.instances.get(path), path)


#: How a sentence says each way a step did not succeed, in the order the rest are listed. A step
#: that ESCALATED is a judge or a loop that would not decide (`surface_loop`), and a BLOCKED one
#: was refused a redo of work it had already committed, or lost its worker — neither failed.
_KINDS = ("failed", "escalated", "blocked")


def _kind(state: InstanceState) -> str:
    if state == InstanceState.ESCALATED:
        return "escalated"
    if state == InstanceState.BLOCKED:
        return "blocked"
    return "failed"


def _verb(kind: str, *, plural: bool = False) -> str:
    if kind == "blocked":
        return "were blocked" if plural else "was blocked"
    return kind


def _listed(names: list[str]) -> str:
    """“a”, “a” and “b”, “a”, “b” and “c”, or “a”, “b”, “c” and 2 more."""
    if len(names) > _NAMED_MAX:
        shown, extra = names[:_NAMED_MAX], len(names) - _NAMED_MAX
        return f"{', '.join(shown)} and {extra} more"
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _ran_after(ctl: RunController, path: str, nodes: dict[str, Node]) -> bool:
    """Did any step after `path`, in a sequence that holds it, run? A container follower ran when
    anything inside it did."""
    for later in followers(ctl, path, nodes):
        prefix = f"{later}."
        for other, inst in ctl.instances.items():
            if (other == later or other.startswith(prefix)) and inst.state in _RAN:
                return True
    return False


def _failed_steps(ctl: RunController) -> list[str]:
    """The failed steps the run's outcome comes from, in the order the spec declares them.

    Followed DOWN from the root through the derivation the scheduler itself uses
    (`tick.derive_state`), never collected by state alone: a failure the run tolerated is not a
    reason it failed. An `allow_failure` step is degraded to its parent, a `skip` fan-out with
    failed items is degraded, a `parallel` that joins on `any` is done once one leg is, a loop that
    went on to finish is done whatever an early iteration did, and an untaken branch case was
    skipped — so none of those is followed into.
    """
    states = {path: inst.state for path, inst in ctl.instances.items()}

    def derived(node: Node, path: str) -> InstanceState:
        return derive_state(
            node,
            path,
            states,
            declined_edges=ctl._declined_edges,
            outputs=ctl._outputs,
            inputs=ctl.run.inputs,
            iterations=ctl._iterations,
        )

    found: list[str] = []

    def visit(node: Node, path: str, state: InstanceState) -> None:
        if state not in FAULTS:
            return
        if not node.is_container or states.get(path) in FAULTS:
            # A step — or a container whose failure is its own: a loop the engine handed to a
            # person, a branch that could not route.
            found.append(path)
            return
        for child, child_path, child_state in _parts(ctl, node, path, derived):
            visit(child, child_path, child_state)

    visit(ctl.root, "root", derived(ctl.root, "root"))
    return found


def _parts(
    ctl: RunController, node: Node, path: str, derived: Any
) -> Iterator[tuple[Node, str, InstanceState]]:
    """A container's parts as instance paths, each with the state its parent reads for it."""
    if node.kind in (NodeKind.SEQUENCE, NodeKind.PARALLEL):
        paths = [f"{path}.children[{i}]" for i in range(len(node.children))]
        # The mask the parent applies (`allow_failure` → degraded), so a tolerated step is not
        # followed into.
        masked = tolerate_failures(
            node.children, [derived(child, p) for child, p in zip(node.children, paths)]
        )
        yield from zip(node.children, paths, masked)
    elif node.kind == NodeKind.FOREACH and node.body is not None:
        prefix = f"{path}.body#"
        items: set[int] = set()
        for other in ctl.instances:
            head = other[len(prefix) :].split(".", 1)[0] if other.startswith(prefix) else ""
            if head.isdigit():
                items.add(int(head))
        for index in sorted(items):
            item_path = f"{prefix}{index}"
            yield node.body, item_path, derived(node.body, item_path)
    elif node.kind == NodeKind.BRANCH:
        for label, case in node.cases.items():
            case_path = f"{path}.cases[{label}]"
            yield case, case_path, derived(case, case_path)
        if node.default_case is not None:
            default_path = f"{path}.default"
            yield node.default_case, default_path, derived(node.default_case, default_path)
