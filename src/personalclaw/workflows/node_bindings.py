"""What a node's `{{…}}` bindings resolve against: the `BindingContext` built for each dispatch.

Run inputs, settled outputs, offloaded artifacts, the loop's previous iteration (`last`), the
enclosing watcher's prior cycle (`previous`), a parallel's siblings, the project Session Brief and
the secret resolver, which reads the run's project's secrets first and puts each secret a step uses
on the run's record. Each is read from durable run state — instances and the journal's stored
outputs — so a resumed run resolves the same values it would have before the restart.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from personalclaw.knowledge import session_brief
from personalclaw.workflows import deliverable, input_secrets, store
from personalclaw.workflows.bindings import BindingContext, BindingError, item_prefixes
from personalclaw.workflows.models import (
    SUCCESS_STATES,
    LoopMode,
    Node,
    NodeKind,
    instance_order,
    loop_parent,
    spec_path,
    walk,
)
from personalclaw.workflows.tick import ReadyNode, derive_state, taken_case

if TYPE_CHECKING:
    from personalclaw.workflows.controller import RunController

logger = logging.getLogger(__name__)


def _session_brief(ctl: RunController) -> Any:
    """The project brief, built lazily and cached for the run.

    Never raises and never blocks the run: a brief is an enhancement, so a store that cannot
    answer means the run starts without the digest rather than not starting.
    """
    if ctl._brief is not None:
        return ctl._brief
    project = str(getattr(ctl.run, "project_id", "") or "")
    if not project:
        # No project means no scope. An unscoped brief would be "everything the user knows",
        # injected into every run — expensive, and wrong about what the run is for.
        ctl._brief = session_brief.SessionBrief()
        return ctl._brief
    try:
        from personalclaw.config.loader import AppConfig
        from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

        path = knowledge_db_path()
        if not path.is_file():
            ctl._brief = session_brief.SessionBrief(project=project)
            return ctl._brief
        budget = int(
            getattr(
                AppConfig.load().knowledge,
                "session_brief_max_tokens",
                session_brief.DEFAULT_MAX_TOKENS,
            )
        )
        ctl._brief = session_brief.build(
            KnowledgeStore(db_path=str(path)), project_id=project, max_tokens=budget
        )
    except Exception:
        logger.debug("run %s: session brief unavailable", ctl.run.id, exc_info=True)
        ctl._brief = session_brief.SessionBrief(project=project)
    return ctl._brief


def record_output(ctl: RunController, path: str, node_id: str, value: Any) -> None:
    """Enter one step's settled output where bindings read it: under its node id, and, for a
    step inside a fan-out item, under its instance path too, so a sibling in the SAME item
    reads it rather than whichever item last wrote that id (`BindingContext.scoped_to`)."""
    if not node_id:
        return
    ctl._outputs[node_id] = value
    if item_prefixes(path):
        ctl._instance_outputs[path] = (node_id, value)


def forget_output(ctl: RunController, path: str, node_id: str) -> None:
    """Take a reset step's output out of where bindings read it, by id and by instance path,
    so nothing resolves a stale value between the reset and the re-run."""
    if node_id:
        ctl._outputs.pop(node_id, None)
    ctl._instance_outputs.pop(path, None)


def item_context(ctl: RunController, path: str, **fields: Any) -> BindingContext:
    """THE context a read made for the step at instance *path* resolves against: the run's
    outputs and offloaded artifacts, scoped to the fan-out items *path* is inside
    (`BindingContext.scoped_to`). Every read the engine makes on a step's behalf goes through
    here: its bindings, a loop's exit and convergence reads, the metric gate's `metric_from`, an
    approval gate's recorded inputs. A read that built its own context from the node-id maps
    read whichever item wrote last. *fields* are the rest of the context, as the read needs it.
    Outside every fan-out item the context is the node-id maps, unchanged."""
    return BindingContext(
        inputs=dict(ctl.run.inputs),
        node_outputs=dict(ctl._outputs),
        node_artifacts=node_artifacts(ctl),
        instance_outputs=dict(ctl._instance_outputs),
        instance_artifacts=_instance_artifacts(ctl),
        **fields,
    ).scoped_to(path)


def item_outputs(ctl: RunController, path: str) -> dict[str, Any]:
    """node id → output as the step at instance *path* reads them (`item_context`'s outputs)."""
    scoped = BindingContext(
        node_outputs=dict(ctl._outputs), instance_outputs=dict(ctl._instance_outputs)
    ).scoped_to(path)
    return scoped.node_outputs or {}


def item_output(ctl: RunController, path: str, node_id: str) -> Any:
    """*node_id*'s output as the step at instance *path* reads it (`item_context`), or None."""
    return item_outputs(ctl, path).get(node_id)


def context_for(ctl: RunController, item: ReadyNode) -> BindingContext:
    watcher_path = _enclosing_watcher(ctl, item.path)
    seen = ctl._seen.get(watcher_path) if watcher_path else None
    last_output, has_last = _last_output(ctl, item.path)
    # Scoped to the step's own fan-out item: a sibling it binds is that item's instance, not
    # whichever item last wrote the node id.
    return item_context(
        ctl,
        item.path,
        item=item.item,
        has_item=item.has_item,
        iter_index=item.iter_index,
        last_output=last_output,
        has_last=has_last,
        sibling_outputs=_sibling_outputs(ctl, item.path),
        previous_output=_previous_output(ctl, item.path),
        has_previous=_previous_output(ctl, item.path) is not None,
        seen_filter=seen.unseen if seen else None,
        brief=_session_brief(ctl),
        secret_resolver=_secrets_for(ctl, item),
        input_secrets=input_secrets.of_run(ctl.run),
        run_document=deliverable.document_path(ctl.run, ctl.spec),
    )


#: Node kinds whose bound config is the text of a model call: a stage's agent task, an infer
#: prompt, a visualize hint.
_MODEL_FACING_KINDS = frozenset({NodeKind.STAGE, NodeKind.INFER, NodeKind.VISUALIZE})


def _step_provider(ctl: RunController, node: Node) -> Any:
    """The provider an action *node* dispatches to, looked up the way ``engine.dispatch_action``
    will look it up, or ``None`` for a node that is no action."""
    if node.kind != NodeKind.ACTION:
        return None
    getter = ctl.services.get_provider
    if getter is None:
        from personalclaw.action_providers.registry import (
            _ensure_default_providers_registered,
            get_action_provider,
        )

        _ensure_default_providers_registered()
        getter = get_action_provider
    return getter(str((node.config or {}).get("provider", "") or ""))


def _config_reaches_a_model(node: Node, provider: Any) -> bool:
    """Whether *node*'s bound config becomes text a model is handed: a model-calling kind, or an
    action whose *provider*'s action IS a model turn (``hands_config_to_a_model``)."""
    if node.kind in _MODEL_FACING_KINDS:
        return True
    return bool(getattr(provider, "hands_config_to_a_model", False))


def _hands_a_run_its_inputs(node: Node, provider: Any) -> bool:
    """Whether *node*'s bound config is the inputs of a run it starts: a ``subworkflow``'s, or an
    action whose provider starts a run with its config (``hands_config_to_a_run``)."""
    if node.kind == NodeKind.SUBWORKFLOW:
        return True
    return bool(getattr(provider, "hands_config_to_a_run", False))


def _agent_works_for_the_run(node: Node, provider: Any) -> bool:
    """Whether the model *node*'s config reaches is an agent that works for this run's project, so
    PersonalClaw's bash tool there fills a reference from the run's project's secrets first: a
    stage's agent, which the engine starts with the run's project in its lineage
    (``engine.leaf_spawn_env``), and the agent an Invoke Agent or Run Prompt step starts, which
    its provider starts for the run's project (``ActionContext.project_id``). Any other model may
    have no tools that fill a reference (an infer step, a best-of-n sample) or work for no project
    (a second opinion, an app's provider)."""
    if node.kind == NodeKind.STAGE:
        return True
    from personalclaw.action_providers.invoke_agent_provider import InvokeAgentActionProvider
    from personalclaw.action_providers.run_prompt_provider import RunPromptActionProvider

    return isinstance(provider, (InvokeAgentActionProvider, RunPromptActionProvider))


def _secrets_for(ctl: RunController, item: ReadyNode) -> Callable[[str], str]:
    """How *item*'s ``{{secret:NAME}}`` references resolve in this run.

    A step whose config is text a model is handed (:func:`_config_reaches_a_model`) keeps each
    reference as the name: filled in there, the value would be in the model's context. Left as the
    name, PersonalClaw's ``bash`` tool fills it in when the agent the text reaches runs a command
    with it — with this run's project, for an agent that works for the run — so the agent can
    use the credential and never see it.

    A step that starts a run with its config (:func:`_hands_a_run_its_inputs`) keeps each as the
    reference too, and counts them (``input_secrets.Handed``): the run it starts keeps its inputs
    on its record, and fills the reference where one of its own steps uses it.

    Every other step gets the value, through the one resolver
    (``llm.credentials.resolve_secret``): the run's project's secret first, then the global one;
    a run with no project reads only the global one. An unknown name returns ``""``: ``resolve()``
    treats an empty secret as a resolution failure and reports it with the binding's own message,
    which names the key. A name nothing reads by name (a setting's own ``PCSECRET_…`` key, a
    project's stored ``PCPROJ_…`` key) raises instead, with the sentence that says why — it is
    set, so "is not set" would be false — and it is refused the same way when the step would hand
    it on.

    Each secret a step uses goes on the run's record once per dispatch (``Journal.secret_read``):
    its name and its scope. Never the value. A reference handed on to an agent that works for the
    run's project (:func:`_agent_works_for_the_run`) is recorded with the scope PersonalClaw's
    bash tool reads it from there (``secrets_vault.stored_scope``). One handed on to any other
    model is not: what reads it there, if anything, is not the run's, so the record claims nothing
    about it.
    """
    from personalclaw.llm.credentials import SecretNameRefused, name_refusal, resolve_secret
    from personalclaw.secrets_vault import stored_scope

    project = str(getattr(ctl.run, "project_id", "") or "")
    provider = _step_provider(ctl, item.node)
    if _hands_a_run_its_inputs(item.node, provider):
        return input_secrets.Handed()
    handed_on = _config_reaches_a_model(item.node, provider)
    records_handed_on = handed_on and _agent_works_for_the_run(item.node, provider)
    recorded: set[str] = set()

    def _refuse(refused: SecretNameRefused) -> BindingError:
        return BindingError(refused.cause, remediation=refused.remedy, caller_supplied=True)

    def _record(name: str, scope: str) -> None:
        if name in recorded:
            return
        recorded.add(name)
        ctl.journal.secret_read(
            item.path,
            item.node.id,
            epoch=ctl._instance(item.path).epoch,
            name=name,
            scope=scope,
            handed_on=handed_on,
        )
        logger.info(
            "run %s step %s: {{secret:%s}} %s %s",
            ctl.run.id,
            item.node.id or item.path,
            name,
            "handed on, read from" if handed_on else "read from",
            f"project {project}" if scope == "project" else (scope or "nowhere (not stored)"),
        )

    def _kept(key: str) -> str:
        refused = name_refusal(key)
        if refused is not None:
            raise _refuse(refused) from None
        if records_handed_on:
            _record(key, stored_scope(key, project))
        return "{{secret:" + key + "}}"

    def _filled(key: str) -> str:
        try:
            read = resolve_secret(key, project_id=project)
        except SecretNameRefused as refused:
            raise _refuse(refused) from None
        except KeyError:
            return ""
        _record(key, read.scope)
        return read.secret

    return _kept if handed_on else _filled


def node_artifacts(ctl: RunController) -> dict[str, str] | None:
    """node id → artifact ref, for outputs the journal OFFLOADED.

    This is the writer that closes the `node_artifacts` seam: `{{nodes.x.artifact}}`
    resolves to a live pointer only for nodes whose output spilled past
    `MAX_INLINE_OUTPUT_BYTES` (or was binary). An offloaded output's `output_ref` does not
    start with `outputs/` — that is exactly the distinction `store.store_output` records
    when it writes to `artifacts/` instead. Derived from instance refs (the durable record),
    not the in-memory preview map, so it survives a restart and a rewind the same way
    `node_outputs` does. Only SUCCESS states contribute — a failed node's leftover ref must
    not resolve as if it were a real artifact.
    """
    by_path = {path: node for path, node in walk(ctl.root)}
    artifacts: dict[str, str] = {}
    for path, inst in ctl.instances.items():
        if inst.state not in SUCCESS_STATES or not inst.output_ref:
            continue
        if inst.output_ref.startswith("outputs/"):
            continue
        node = by_path.get(spec_path(path))
        if node is None or not node.id:
            continue
        artifacts[node.id] = inst.output_ref
    return artifacts or None


def _instance_artifacts(ctl: RunController) -> dict[str, tuple[str, str]] | None:
    """instance path → (node id, artifact ref), for each offloaded output of a step inside a
    fan-out item: what `node_artifacts` holds by node id, kept per item so `scoped_to` can hand a
    step its own item's ref. Derived from the instances, as `node_artifacts` is, so a restart
    reads the same."""
    by_path = {path: node for path, node in walk(ctl.root)}
    found: dict[str, tuple[str, str]] = {}
    for path, inst in ctl.instances.items():
        if not item_prefixes(path) or inst.state not in SUCCESS_STATES:
            continue
        if not inst.output_ref or inst.output_ref.startswith("outputs/"):
            continue
        node = by_path.get(spec_path(path))
        if node is not None and node.id:
            found[path] = (node.id, inst.output_ref)
    return found or None


def _sibling_outputs(ctl: RunController, path: str) -> dict[str, list[Any]] | None:
    """Accumulated outputs of the node's siblings inside its enclosing `parallel`.

    A LIST per sibling and not just its current output: a watcher reads a sibling that is
    still producing, so "the output" is the wrong shape — the synthesizer needs the
    accumulation to see a trend, which is the whole reason the binding exists.

    Accumulated from the JOURNAL rather than from `ctl._outputs`, because a loop body
    overwrites its node-id output every iteration: reading the live map would show cycle
    50 and nothing before it, and the window/seen-set machinery would have nothing to
    bound.
    """
    tree = dict(walk(ctl.root))
    container = _enclosing_parallel(path, tree)
    if container is None:
        return None
    node = tree.get(spec_path(container))
    if node is None or not node.children:
        return None
    out: dict[str, list[Any]] = {}
    for index, child in enumerate(node.children):
        cpath = f"{container}.children[{index}]"
        if path == cpath or path.startswith(f"{cpath}."):
            continue  # a node is not its own sibling
        if not child.id:
            continue
        out[child.id] = _accumulated_outputs(ctl, cpath)
    return out or None


def _accumulated_outputs(ctl: RunController, subtree: str) -> list[Any]:
    """Every output a subtree has produced, oldest first.

    Read from the journal's stored outputs so loop iterations accumulate instead of the
    newest overwriting the rest.
    """
    acc: list[Any] = []
    # A fan-out's own record (`record_foreach_outputs`) is the list of its items' outputs, already
    # counted under each item: layered in, it would replace everything before it.
    fan_outs = {path for path, node in walk(ctl.root) if node.kind is NodeKind.FOREACH}
    for spath in sorted(
        (p for p in ctl.instances if p == subtree or p.startswith(f"{subtree}.")),
        key=instance_order,
    ):
        inst = ctl.instances[spath]
        if inst.state not in SUCCESS_STATES or not inst.output_ref:
            continue
        if spec_path(spath) in fan_outs:
            continue
        # By INSTANCE PATH, not by `output_ref`: the ref is already the run-relative file
        # path, and `read_output` derives the filename from what it is given — passing the
        # ref would hash a hash and read nothing, silently, forever.
        value = store.read_output(ctl.run.id, spath)
        if value is not None:
            acc.append(value)
    return acc


def _last_output(ctl: RunController, path: str) -> tuple[Any, bool]:
    """`{{last.output}}` — the previous ITERATION of the loop this node is in.

    Returns `(value, present?)`. `present` is separate from the value because `None` is a
    legitimate previous output and absence is not, the same reason `loop_iteration._progress_value`
    returns a pair: collapsing them would make a body that legitimately returned nothing
    indistinguishable from a body this engine never handed anything to.

    **What one ITERATION's output IS, when the body is a container.** The iteration's
    produced outputs, LAYERED in document order — each mapping's keys merge in, a later node
    wins a collision. That is the contract the bundled templates were written against and the
    only one that can serve them: `general-project`'s body prompt reads `summary` (its
    worker's key) and `verdict` (its judge's key) in one breath, so no single child's output
    is the answer. A one-node body layers exactly one mapping and is therefore identical to
    handing that node's output straight through, which is what keeps `design-project`
    unchanged.

    A NON-mapping output has nothing to merge into, so the latest word wins outright — the
    rule `loop_iteration.advance_loop` has always applied to the loop's own `condition`.

    Read through `_accumulated_outputs` (the journal's stored outputs) rather than
    `ctl._outputs`, which is keyed by NODE ID: a loop body overwrites its own entry every
    iteration, so reading that map would hand iteration 3 its own output as if it were
    iteration 2's.
    """
    loop_path, iteration = loop_parent(path)
    if loop_path is None or iteration <= 0:
        # No enclosing loop, or the first iteration — there is no previous one either way.
        # `bindings._first_cycle_miss` turns the second case into the documented
        # `| default(...)`; the first still raises, because `last` outside a loop names
        # nothing.
        return None, False
    return iteration_output(ctl, loop_path, iteration - 1)


def iteration_output(ctl: RunController, loop_path: str, iteration: int) -> tuple[Any, bool]:
    """One iteration's layered output. See `_last_output` for the contract it implements."""
    return subtree_output(ctl, f"{loop_path}.body@{iteration}")


def subtree_output(ctl: RunController, subtree: str) -> tuple[Any, bool]:
    """What the steps of one subtree produced, LAYERED in document order: `(value, present?)`.

    The one reading of "what this part of the run produced", for a loop's cycle (`_last_output`)
    and a branch's taken case (`record_branch_outputs`) alike: each mapping's keys merge in and a
    later step wins a collision, a non-mapping output replaces what came before, and a single
    step's output is exactly that output.
    """
    produced = _accumulated_outputs(ctl, subtree)
    if not produced:
        return None, False
    layered: Any = {}
    for value in produced:
        if isinstance(value, dict) and isinstance(layered, dict):
            layered.update(value)
        else:
            layered = value
    return layered, True


def record_branch_outputs(ctl: RunController) -> None:
    """Add to each branch's record what the case it took produced, once that case has ended in
    success: `{"case": <label>, "produced": <its output>}`, which a step after the branch reads.

    A branch's own step only routes, recording `{"case": label}` (`engine.dispatch_branch`), and
    its taken case runs after it, so what that case produced exists only once it has run. Until
    this, the routing was all a step after a branch could read, and no step could reach the case's
    work at all: a reader of a case that was not taken is skipped, so naming the case's own step
    works only when it is the one taken. `produced` is the case's output read the way a loop's
    cycle is (`subtree_output`), and the record is stored the way a finished loop's is
    (`loop_convergence.finish_loop`): with the branch's step and under its id, so the run page, a
    resumed run (`RunController._load_outputs`) and every binding read the same value.

    Called before each tick's frontier, so a step the frontier then admits after the branch reads
    it, and at a loop's cycle boundary, before the cycle's output is read. A case that has not
    ended, or ended without success, adds nothing, and a step that reads the branch is held or
    skipped for it by the frontier. Each branch is recorded once per ending of its case:
    `ctl._branch_recorded` forgets it the moment a tick sees that case unfinished (a rewind is
    applied before the tick's frontier, a re-route resets the branch), so the next ending records
    again.

    Inner branches first (reverse document order), so a case holding a branch reads that branch's
    whole record. Where one branch has several instances (a loop's cycles, a fan-out's items), its
    id names the latest instance whose case has ended.
    """
    tree = dict(walk(ctl.root))
    branches = {
        path: node
        for path in ctl.instances
        if (node := tree.get(spec_path(path))) is not None and node.kind is NodeKind.BRANCH
    }
    if not branches:
        return
    states = {path: inst.state for path, inst in ctl.instances.items()}
    base = BindingContext(
        inputs=ctl.run.inputs,
        node_outputs=ctl._outputs,
        instance_outputs=dict(ctl._instance_outputs),
    )
    latest: dict[str, str] = {}
    recorded = False
    for path in sorted(branches, key=instance_order, reverse=True):
        node = branches[path]
        # The case a branch inside a fan-out item took is read from ITS item's selector value, as
        # the frontier routed it (`tick._visit_branch`), not from whichever item wrote it last.
        ctx = base.scoped_to(path)
        taken = taken_case(node, path, ctx) if states[path] in SUCCESS_STATES else None
        # Whether the taken case has ended is judged on this item's own outputs too: a router
        # inside the case routes on its item's steps, not on whichever item wrote them last.
        if taken is None or (
            derive_state(
                taken[2],
                taken[1],
                states,
                declined_edges=ctl._declined_edges,
                outputs=ctl._outputs,
                inputs=ctl.run.inputs,
                iterations=ctl._iterations,
                instance_outputs=ctl._instance_outputs,
            )
            not in SUCCESS_STATES
        ):
            ctl._branch_recorded.pop(path, None)
            continue
        if path not in ctl._branch_recorded:
            label, case_path, _case = taken
            produced, _present = subtree_output(ctl, case_path)
            # The routing-only record goes to the attic rather than staying beside the new one:
            # an output too large to keep inline is stored apart from it, and a read by path
            # would find the old record first.
            store.archive_output(ctl.run.id, path, ctl.run.spec_version)
            inst = ctl.instances[path]
            inst.output_ref, ctl._branch_recorded[path] = ctl.journal.store_output(
                path, {"case": label, "produced": produced}
            )
            recorded = True
            # Each instance's own record, by instance path, for a step in the SAME fan-out item
            # that reads the branch; the latest-instance-per-id entry is set below.
            ctl.record_output(path, node.id, ctl._branch_recorded[path])
        if node.id and node.id not in latest:
            latest[node.id] = path
    for node_id, path in latest.items():
        ctl._outputs[node_id] = ctl._branch_recorded[path]
    if recorded:
        ctl._persist_state()


def _fan_out_paths(
    ctl: RunController, tree: dict[str, Node], fan_outs: dict[str, Node]
) -> set[str]:
    """Every instance path of a `foreach` the run has reached. One outside every iterated container
    is its spec path; one inside a loop's cycle or a fan-out's item is that path under each
    instance of the body around it the run has started, so a fan-out over no items there is
    recorded too."""
    iterated = [p for p, n in tree.items() if n.kind in (NodeKind.LOOP, NodeKind.FOREACH)]
    started: dict[str, set[str]] = {}
    for path in ctl.instances:
        for match in re.finditer(r"\.body[@#]\d+", path):
            prefix = path[: match.end()]
            started.setdefault(spec_path(prefix), set()).add(prefix)
    found: set[str] = set()
    for spec in fan_outs:
        around = [f"{p}.body" for p in iterated if spec.startswith(f"{p}.body.")]
        if not around:
            found.add(spec)
            continue
        body = max(around, key=len)
        found.update(prefix + spec[len(body) :] for prefix in started.get(body, ()))
    return found


def _left_out(ran: int, total: int, why: dict[str, int]) -> str:
    """The caption of a fan-out some of whose items gave no result: `2 of 4 items gave a result:
    1 failed, 1 skipped`. "" when every item did."""
    if ran == total:
        return ""
    noun = "item" if total == 1 else "items"
    parts = ", ".join(f"{count} {state}" for state, count in sorted(why.items()))
    return f"{ran} of {total} {noun} gave a result: {parts}"


def record_foreach_outputs(ctl: RunController) -> None:
    """Record each `foreach` that has ended in success: its output is the list of what each of its
    items produced, in item order, read the way a loop's cycle is (`subtree_output`), so a step
    after the fan-out reads every item's result as `{{nodes.<foreach>.output}}`.

    Until this a fan-out recorded nothing, so no step could read what its items produced except
    the last item to finish, through the body's own step id (which still reads that way). An item
    that FAILED or was SKIPPED produced no result and is left out; how many were left out, and
    why, is the fan-out step's own caption (`degraded_reason`), so "3 of 40 gave a result" is not
    read as "3 ran". Stored with the fan-out's instance and entered under its id the way a
    finished loop's output is (`loop_convergence.finish_loop`), so the run page and a resumed run
    read the same list. Recorded once per ending: `ctl._foreach_recorded` forgets a fan-out a tick
    sees unfinished (a rewind), and its next ending records again.
    """
    from personalclaw.workflows.tick import _resolve_items

    tree = dict(walk(ctl.root))
    fan_outs = {path: node for path, node in tree.items() if node.kind is NodeKind.FOREACH}
    if not fan_outs:
        return
    states = {path: inst.state for path, inst in ctl.instances.items()}
    recorded = False
    for path in sorted(_fan_out_paths(ctl, tree, fan_outs), key=instance_order, reverse=True):
        node = fan_outs[spec_path(path)]
        state = derive_state(
            node,
            path,
            states,
            declined_edges=ctl._declined_edges,
            outputs=ctl._outputs,
            inputs=ctl.run.inputs,
            iterations=ctl._iterations,
            instance_outputs=ctl._instance_outputs,
        )
        if state not in SUCCESS_STATES or node.body is None:
            if ctl._foreach_recorded.pop(path, None) is not None or (
                path in ctl.instances and ctl.instances[path].output_ref
            ):
                # Its items were reset (a rewind): what it recorded no longer stands.
                ctl.forget_output(path, node.id)
                ctl.instances.pop(path, None)
                recorded = True
            continue
        if path in ctl._foreach_recorded and ctl._instance(path).output_ref:
            continue
        items = _resolve_items(node, item_context(ctl, path)) or []
        produced: list[Any] = []
        why: dict[str, int] = {}
        for index in range(len(items)):
            ipath = f"{path}.body#{index}"
            item_state = derive_state(
                node.body,
                ipath,
                states,
                declined_edges=ctl._declined_edges,
                outputs=ctl._outputs,
                inputs=ctl.run.inputs,
                iterations=ctl._iterations,
                instance_outputs=ctl._instance_outputs,
            )
            if item_state in SUCCESS_STATES:
                produced.append(subtree_output(ctl, ipath)[0])
            else:
                why[item_state.value] = why.get(item_state.value, 0) + 1
        store.archive_output(ctl.run.id, path, ctl.run.spec_version)
        inst = ctl._instance(path)
        inst.state = state
        inst.degraded_reason = _left_out(len(produced), len(items), why)
        inst.output_ref, ctl._foreach_recorded[path] = ctl.journal.store_output(path, produced)
        ctl.record_output(path, node.id, ctl._foreach_recorded[path])
        recorded = True
    if recorded:
        ctl._persist_state()


def _previous_output(ctl: RunController, path: str) -> Any:
    """The prior successful cycle of the enclosing loop, for diff-aware synthesis.

    Distinct from `{{last.output}}`, which is the previous iteration of the loop the node
    is IN. `previous` is the prior cycle of the whole watcher body, which for a `sequence`
    body is what a synthesis stage actually wants: its own last report, not the output of
    whichever node happened to run before it.
    """
    watcher = _enclosing_watcher(ctl, path)
    if watcher is None:
        return None
    current = int(ctl._iterations.get(watcher, 0))
    if current <= 0:
        return None
    node = dict(walk(ctl.root)).get(watcher)
    if node is None or node.body is None:
        return None
    prior = _accumulated_outputs(ctl, f"{watcher}.body@{current - 1}")
    return prior[-1] if prior else None


def _enclosing_watcher(ctl: RunController, path: str) -> str | None:
    """The nearest enclosing `until_cancelled` loop path, or None."""
    for candidate, node in walk(ctl.root):
        if node.kind != NodeKind.LOOP:
            continue
        if str((node.config or {}).get("mode", "") or "") != LoopMode.UNTIL_CANCELLED.value:
            continue
        if path == candidate or path.startswith(f"{candidate}."):
            return candidate
    return None


def resolved_inputs(ctl: RunController, item: ReadyNode, ctx: BindingContext) -> dict[str, Any]:
    """What actually reached this node — the cache's `inputs_hash` input.

    Only the node's declared dependencies, not the whole output map: hashing every
    output would invalidate the cache whenever any unrelated node finished, making
    resume useless.
    """
    from personalclaw.workflows.bindings import node_deps

    deps = node_deps(item.node.config or {})
    # From the step's own (item-scoped) context, so a fan-out item's cache key hashes the values
    # its bindings actually resolved, not another item's.
    outputs = ctx.node_outputs or {}
    view: dict[str, Any] = {dep: outputs.get(dep) for dep in sorted(deps)}
    if item.has_item:
        view["__item"] = item.item
    if item.iter_index is not None:
        view["__iter"] = item.iter_index
    return view


def store_prompt(ctl: RunController, path: str, prompt: str) -> str:
    """Persist the fully-resolved prompt and return its ref.

    Required for trajectory replay: the bar is that prompt → tool
    calls → output is reconstructable from ledger events alone.
    """
    if not prompt:
        return ""
    return store.write_output(ctl.run.id, f"{path}::prompt", prompt)


def _enclosing_parallel(path: str, tree: dict[str, Node]) -> str | None:
    """The path of the nearest enclosing `parallel`, walking OUTWARD.

    Not just the nearest `.children[N]` prefix: a watcher's synthesize stage sits at
    `…children[1].body@3.children[0]`, whose nearest prefix is the BODY SEQUENCE. Stopping
    there returned no siblings for the one node in the whole template that needs them —
    measured, and silent, because a missing `siblings` root reads as "this node has no
    siblings" rather than as an error.

    Nearest-first among genuine parallels, so a nested parallel resolves to the inner one: a
    node's siblings are the legs of ITS parallel, not an outer one's.
    """
    matches = list(re.finditer(r"\.children\[\d+\]", path))
    for match in reversed(matches):
        candidate = path[: match.start()]
        if not candidate:
            continue
        node = tree.get(spec_path(candidate))
        if node is not None and node.kind == NodeKind.PARALLEL:
            return candidate
    return None
