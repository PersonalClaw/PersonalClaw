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

A loop handed to a person ends with the sentence its escalation record carries, and when that
loop is the run's own root it names no step at all, because the loop is the run (its id there was
a template's internal name).

**Why a run stopped, and what to do next, is decided here once** (:func:`loop_stop`,
:func:`step_cause`): the escalation record carries the CAUSE, the sentence and the remedy
(`resilience.escalation_artifact`), and the run page, the runs list, the chat card, the Inbox row
and the loop list read them rather than each re-deriving a reason from a token. The causes are the
closed vocabulary :data:`CAUSES`. The order a loop's stop is read in is the order of what is most
true: a step that failed is the cause before anything else; then the judge's own ruling on the
cycle the loop ended on (a judge that could not decide is not a loop that ran out of budget); then
the budget it was given. The remedy follows the cause, and only a step that failed at its work is
told to change the workflow: a cycle budget is the run's to raise, an ask that timed out is
answered on the next run, a spend cap's refusal is lifted in Settings, and a decision the judge
handed over is the person's to make.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    Failure,
    FailureClass,
    InstanceState,
    Node,
    NodeKind,
    instance_order,
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


def _closed(text: str) -> str:
    """*text* as a whole sentence: its own closing mark kept (a quotation ends on its own), else a
    period added."""
    return text if text.endswith(("”", "!", "?")) else f"{text}."


# ── why a run stopped, and what the person can do next ──────────────────────

#: A loop used the cycle or token budget it was given (`resilience.BUDGET_TRIPS`).
BUDGET = "budget"
#: The judge would not decide whether the work is done, and said why.
JUDGE = "judge"
#: A step's time limit ran out while one of its calls waited for the owner's answer.
APPROVAL_TIMEOUT = "approval_timeout"
#: A step needed the owner's approval to start, and nobody gave it.
APPROVAL = "approval"
#: Every call a step made was refused by the tools the step was given.
REFUSAL = "refusal"
#: A spend cap refused the model call a step needed, before it was made (`FailureClass.BUDGET`).
SPEND_CAP = "spend_cap"
#: A step failed at its work, so the step is what to change.
STEP = "step"

#: Why a step or a loop handed the run to a person: the closed vocabulary an escalation record
#: carries as ``cause``. Mirrored by `web/src/pages/workflows/attentionMeta.ts`'s `CAUSE_HEADLINE`.
CAUSES = frozenset({BUDGET, JUDGE, APPROVAL_TIMEOUT, APPROVAL, REFUSAL, SPEND_CAP, STEP})

#: The causes a failure names itself, as its `terminal_reason` (stamped where the step settled,
#: `stage_settlement.reconcile_dispatched_stages`). Any other failure is the step's own.
_FAILURE_CAUSES = frozenset({APPROVAL_TIMEOUT, APPROVAL, REFUSAL})

#: The way forward a step that failed at its work has: it is the step that needs changing.
_CHANGE_THE_STEP = (
    "A new run of this workflow fails the same way until the step changes: change the step that "
    "gave up, then run the workflow again."
)

#: What a person can do about each cause. One sentence each, true whatever the run.
_REMEDY = {
    JUDGE: (
        "The decision is yours: read the work and the judge's reason. If it needed a folder the "
        "file tools do not reach, add that folder in Settings › Agent defaults › Allowed working "
        "directories, then fork the run and start it again."
    ),
    APPROVAL_TIMEOUT: (
        "Fork the run and start it again, then answer its asks while it waits: a step's time limit "
        "counts from when the step starts, and Subagent timeout in Settings › Agent defaults sets "
        "it."
    ),
    APPROVAL: "Fork the run and start it again, then answer the step's approval when it asks.",
    REFUSAL: (
        "Give that step the tools its task needs (a read-only step runs only what reads), or "
        "narrow its task to what its tools can do, then run the workflow again."
    ),
    SPEND_CAP: (
        "A spend cap refused the call a step needed, so the workflow needs no change: raise or "
        "remove the cap in Settings › Guardrails, or wait for it to reset, then retry the run."
    ),
    STEP: _CHANGE_THE_STEP,
}

#: A budget stop's way forward, by the budget: a cycle budget is the run's own to raise, a token
#: budget is the workflow's.
_MORE_CYCLES = (
    "It stopped at the budget it was given, so the workflow needs no change. For more cycles, fork "
    "it and set Max cycles before you start the new run."
)
_MORE_TOKENS = (
    "It stopped at the token budget its workflow sets. To give it more, raise that budget in the "
    "workflow, then run it again."
)


@dataclass(frozen=True)
class Stop:
    """Why a run stopped where it did, in the words every surface shows: the cause (one of
    :data:`CAUSES`), what happened as one true sentence, and what the person can do about it."""

    cause: str
    sentence: str
    remedy: str


def remedy_for(cause: str, reason: str = "") -> str:
    """What to do about *cause*; a budget's depends on which budget (*reason*, its token)."""
    if cause == BUDGET:
        return _MORE_TOKENS if reason == "token_cap" else _MORE_CYCLES
    return _REMEDY.get(cause, _CHANGE_THE_STEP)


def step_cause(failure: Failure | None) -> str:
    """Why a failed step stopped: the cause its failure names itself, a spend cap's refusal (its
    class says so, `failure_taxonomy`), else the step's own."""
    if failure is None:
        return STEP
    if failure.terminal_reason in _FAILURE_CAUSES:
        return failure.terminal_reason
    return SPEND_CAP if failure.failure_class is FailureClass.BUDGET else STEP


def step_stop(failure: Failure | None) -> Stop:
    """A step that gave up, as the record of its own escalation: its failure is the sentence."""
    cause = step_cause(failure)
    return Stop(cause, failure.cause_plain if failure is not None else "", remedy_for(cause))


#: How much of a judge's own words a stop quotes. A reason is a sentence or three; a judge that
#: pasted its whole reasoning would otherwise put a page into a one-line ending.
_QUOTED_MAX = 400


def _quoted(text: Any) -> str:
    said = clause(str(text or ""))
    if not said:
        return ""
    if len(said) > _QUOTED_MAX:
        said = said[:_QUOTED_MAX].rstrip() + "…"
    return f"“{said}”"


def judge_stop(ruling: Any) -> Stop | None:
    """The judge's ruling as the reason a loop stopped, when the judge would not decide; else None.

    *ruling* is the judge's settled output (`engine.apply_judge_contract`). "Would not decide" is
    the contract's own reading — ``escalated`` (it said what it could not check, or passed work the
    run's own check failed) — or a bare ESCALATE verdict. Its reason is quoted in its own words: it
    is the one account of what was missing, and a paraphrase could only lose some of it."""
    if not isinstance(ruling, dict):
        return None
    verdict = str(ruling.get("verdict") or "").upper()
    if ruling.get("escalated") is not True and verdict != "ESCALATE":
        return None
    could_not = _quoted(ruling.get("cannot_judge"))
    if could_not:
        sentence = f"The judge could not decide whether the work is done, and said why: {could_not}"
    elif ruling.get("fallback_result") is False:
        sentence = (
            "The judge passed the work, but the run's own check of it failed, so the two disagree"
        )
    else:
        said = _quoted(ruling.get("reasoning"))
        sentence = "The judge would not rule either way" + (f": {said}" if said else "")
    return Stop(JUDGE, _closed(sentence), remedy_for(JUDGE))


def judge_ruling(ctl: RunController, node: Node, parent_path: str, iteration: int) -> Any:
    """What this loop iteration's judge ruled — its settled output — or None when no judge ruled.

    The judge is the body stage that declares ``judge_contract``. Read only from a judge whose
    instance for THIS iteration succeeded; the last one in document order wins. An iteration in
    which any body node FAILED has no ruling: a judge that ruled on it was ruling on work that did
    not finish, and the failure is the stop (`loop_stop`)."""
    if node.body is None or cycle_failed(ctl, parent_path, iteration):
        return None
    base = f"{parent_path}.body@{iteration}"
    ruling: Any = None
    for sub, child in walk(node.body):
        if not child.id or not (child.config or {}).get("judge_contract"):
            continue
        inst = ctl.instances.get(base if sub == "root" else f"{base}{sub[len('root'):]}")
        if inst is None or inst.state not in SUCCESS_STATES:
            continue
        ruling = ctl._outputs.get(child.id)
    return ruling


def cycle_failed(ctl: RunController, parent_path: str, iteration: int) -> bool:
    """Whether a step of this loop iteration (``<parent_path>.body@<iteration>``) FAILED."""
    base = f"{parent_path}.body@{iteration}"
    return any(
        inst.state is InstanceState.FAILED
        for path, inst in ctl.instances.items()
        if path == base or path.startswith(f"{base}.")
    )


def loop_stop(
    ctl: RunController, parent_path: str, node: Node, *, reason: str, detail: str
) -> tuple[str, Stop]:
    """Why a loop is handed to a person, as ``(reason token, stop)``.

    Read in the order of what is most true (the module docstring):

    * **a cycle failed**: its first failed step is the stop (``iterations_failed``) — a budget the
      loop reached while failing says nothing about the work, and a reader told "the budget"
      shrinks a task that was never the problem;
    * **the judge would not decide** on the cycle the loop ended on (``judge_escalated``) — the
      ending reads its ruling, not the budget that happened to run out beside it;
    * **the budget** it was given (the breaker's own token, *detail* its sentence);
    * otherwise the loop stopped converging, and *detail* is the breaker's own account.
    """
    failed, attempted, first = _iteration_failures(ctl, parent_path)
    if failed:
        inst = ctl.instances[first]
        cause = step_cause(inst.failure)
        said = clause(inst.failure.cause_plain if inst.failure else "")
        # A step whose wait for the owner ran out, whose start nobody approved, or whose call a
        # spend cap refused, stopped: it did not fail at its work, and saying it did sends the
        # reader to change a step that is fine.
        verb = "stopped" if cause in (APPROVAL_TIMEOUT, APPROVAL, SPEND_CAP) else "failed"
        name = _name(ctl, dict(walk(ctl.root)), first)
        part = _closed(f"{name} {verb}: {said[:1].lower()}{said[1:]}" if said else f"{name} {verb}")
        lead = (
            ""
            if failed == attempted == 1
            else (
                f"{failed} of {attempted} cycles failed instead of finishing their work. "
                "The first: "
            )
        )
        return "iterations_failed", Stop(cause, f"{lead}{part}", remedy_for(cause))
    iteration = int(ctl._iterations.get(parent_path, 0))
    ruled = judge_stop(judge_ruling(ctl, node, parent_path, iteration))
    if ruled is not None:
        return "judge_escalated", ruled
    from personalclaw.workflows.resilience import BUDGET_TRIPS

    if reason in BUDGET_TRIPS:
        return reason, Stop(BUDGET, detail, remedy_for(BUDGET, reason))
    return reason, Stop(STEP, _closed(clause(detail)) if detail else "", remedy_for(STEP))


def _iteration_failures(ctl: RunController, loop_path: str) -> tuple[int, int, str]:
    """`(iterations with a failed body node, iterations attempted, the first failed instance)`.

    Derived from the instances rather than from a counter, because no counter distinguishes the
    two endings — `ctl._iterations` only says how far the loop got, which is identical for a loop
    that worked six times and one that failed six times. An iteration counts as attempted once any
    instance exists under its `body@<n>` prefix, so an iteration the scheduler never opened is not
    counted against the loop.
    """
    failed = attempted = 0
    first = ""
    for index in range(int(ctl._iterations.get(loop_path, 0)) + 1):
        prefix = f"{loop_path}.body@{index}"
        members = sorted(
            (path for path in ctl.instances if path == prefix or path.startswith(f"{prefix}.")),
            key=instance_order,
        )
        if not members:
            continue
        attempted += 1
        broken = [p for p in members if ctl.instances[p].state is InstanceState.FAILED]
        if not broken:
            continue
        failed += 1
        if not first:
            first = broken[0]
    return failed, attempted, first


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
    stops = _stops(ctl)
    went_on = [p for p in paths if _ran_after(ctl, p, nodes)]
    first, rest = paths[0], paths[1:]
    if went_on == [first] and not rest:
        return f"The run continued past {_first_part(ctl, nodes, first, stops, joined=', which')}"
    parts = []
    if went_on:
        parts.append(f"The run continued past {_listed([_name(ctl, nodes, p) for p in went_on])}.")
    parts.append(_first_part(ctl, nodes, first, stops))
    for kind in _KINDS:
        named = [_name(ctl, nodes, p) for p in rest if _kind(ctl, p, stops) == kind]
        if named:
            parts.append(f"{_listed(named)} {_verb(kind, plural=len(named) > 1)} too.")
    return " ".join(parts)


def _first_part(
    ctl: RunController,
    nodes: dict[str, Node],
    path: str,
    stops: dict[str, tuple[str, str]],
    *,
    joined: str = "",
) -> str:
    """“name” failed: its cause. — or, `joined` to a lead clause, “name”, which failed: …

    A loop handed to a person says why with its own sentence (its escalation record's), and the
    run's root loop — which is the whole run — is not named at all."""
    kind = _kind(ctl, path, stops)
    if path in stops:
        said = clause(stops[path][1])
        if path == ROOT:
            return _closed(said) if said else "The loop stopped before it finished."
        head = f"{_name(ctl, nodes, path)}{joined} {_verb(kind)}"
        return _closed(f"{head}: {said[:1].lower()}{said[1:]}") if said else f"{head}."
    inst = ctl.instances[path]
    cause = clause(inst.failure.cause_plain if inst.failure else "")
    head = f"{_name(ctl, nodes, path)}{joined} {_verb(kind)}"
    return _closed(f"{head}: {cause}") if cause else f"{head}."


#: The instance path of a run's root node: every other path starts with it.
ROOT = "root"


def _stops(ctl: RunController) -> dict[str, tuple[str, str]]:
    """The steps and loops of this run handed to a person, each with its escalation's cause and
    sentence: instance path → ``(cause, sentence)``.

    Read off the run's ledger, the record every reader of an escalation reads
    (`service._escalations`), and classified by the record itself (`cause`, written by
    `resilience.escalation_artifact`), so the run's ending and the run page cannot disagree about
    why it stopped. The LAST escalation of an instance speaks for it, and only an instance that is
    still escalated counts: a rewind that re-ran it has taken the stop back.
    """
    try:
        rows = journal_mod.ledger(ctl.run.id, kinds={journal_mod.STEP_ESCALATED})
    except Exception:  # noqa: BLE001 - a sentence helper never fails the run it describes
        return {}
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        path = str(row.get("instance_path") or "")
        if path:
            latest[path] = row
    return {
        path: (str(row.get("cause") or STEP), str(row.get("detail") or ""))
        for path, row in latest.items()
        if (inst := ctl.instances.get(path)) is not None and inst.state == InstanceState.ESCALATED
    }


def _name(ctl: RunController, nodes: dict[str, Node], path: str) -> str:
    return step_name(nodes.get(spec_path(path)), ctl.instances.get(path), path)


#: How a sentence says each way a step did not succeed, in the order the rest are listed. A step
#: that ESCALATED is a judge or a loop that would not decide (`surface_loop`), one that stopped at
#: its BUDGET used the room it was given, and a BLOCKED one was refused a redo of work it had
#: already committed, or lost its worker — none of them failed.
_KINDS = ("failed", "escalated", "budget", "blocked")


def _kind(ctl: RunController, path: str, stops: dict[str, tuple[str, str]]) -> str:
    if path in stops and stops[path][0] == BUDGET:
        return BUDGET
    state = ctl.instances[path].state
    if state == InstanceState.ESCALATED:
        return "escalated"
    if state == InstanceState.BLOCKED:
        return "blocked"
    return "failed"


def _verb(kind: str, *, plural: bool = False) -> str:
    if kind == BUDGET:
        return "stopped at their budgets" if plural else "stopped at its budget"
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
