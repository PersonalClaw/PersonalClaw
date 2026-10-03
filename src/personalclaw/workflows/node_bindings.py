"""What a node's `{{…}}` bindings resolve against: the `BindingContext` built for each dispatch.

Run inputs, settled outputs, offloaded artifacts, the loop's previous iteration (`last`), the
enclosing watcher's prior cycle (`previous`), a parallel's siblings, the project Session Brief and
the secret resolver. Each is read from durable run state — instances and the journal's stored
outputs — so a resumed run resolves the same values it would have before the restart.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from personalclaw.knowledge import session_brief
from personalclaw.workflows import deliverable, store
from personalclaw.workflows.bindings import BindingContext, BindingError
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
from personalclaw.workflows.tick import ReadyNode

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


def context_for(ctl: RunController, item: ReadyNode) -> BindingContext:
    watcher_path = _enclosing_watcher(ctl, item.path)
    seen = ctl._seen.get(watcher_path) if watcher_path else None
    last_output, has_last = _last_output(ctl, item.path)
    return BindingContext(
        inputs=dict(ctl.run.inputs),
        node_outputs=dict(ctl._outputs),
        node_artifacts=node_artifacts(ctl),
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
        secret_resolver=(
            _reference_kept if _config_reaches_a_model(ctl, item.node) else _secret_resolver
        ),
        run_document=deliverable.document_path(ctl.run, ctl.spec),
    )


#: Node kinds whose bound config is the text of a model call: a stage's agent task, an infer
#: prompt, a visualize hint.
_MODEL_FACING_KINDS = frozenset({NodeKind.STAGE, NodeKind.INFER, NodeKind.VISUALIZE})


def _config_reaches_a_model(ctl: RunController, node: Node) -> bool:
    """Whether *node*'s bound config becomes text a model is handed: a model-calling kind, or an
    action whose provider's action IS a model turn (``hands_config_to_a_model``), looked up the
    way ``engine.dispatch_action`` will look it up."""
    if node.kind in _MODEL_FACING_KINDS:
        return True
    if node.kind != NodeKind.ACTION:
        return False
    getter = ctl.services.get_provider
    if getter is None:
        from personalclaw.action_providers.registry import (
            _ensure_default_providers_registered,
            get_action_provider,
        )

        _ensure_default_providers_registered()
        getter = get_action_provider
    provider = getter(str((node.config or {}).get("provider", "") or ""))
    return bool(getattr(provider, "hands_config_to_a_model", False))


def _reference_kept(key: str) -> str:
    """A ``{{secret:KEY}}`` in text a model will be handed, left as the name.

    Filled in there, the value would be in the model's context. Left as the name, the agent the
    text reaches fills it when one of its tools runs (the ``bash`` tool does), so a stage can still
    use the credential and never see it."""
    return "{{secret:" + key + "}}"


def node_artifacts(ctl: RunController) -> dict[str, str] | None:
    """node id → artifact ref, for outputs the journal OFFLOADED (WV-11).

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


def _sibling_outputs(ctl: RunController, path: str) -> dict[str, list[Any]] | None:
    """Accumulated outputs of the node's siblings inside its enclosing `parallel`.

    A LIST per sibling and not just its current output: a watcher reads a sibling that is
    still producing, so "the output" is the wrong shape — the synthesizer needs the
    accumulation to see a trend, which is the whole reason the binding exists (§4.2).

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
    for spath in sorted(
        (p for p in ctl.instances if p == subtree or p.startswith(f"{subtree}.")),
        key=instance_order,
    ):
        inst = ctl.instances[spath]
        if inst.state not in SUCCESS_STATES or not inst.output_ref:
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
    produced = _accumulated_outputs(ctl, f"{loop_path}.body@{iteration}")
    if not produced:
        return None, False
    layered: Any = {}
    for value in produced:
        if isinstance(value, dict) and isinstance(layered, dict):
            layered.update(value)
        else:
            layered = value
    return layered, True


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
    view: dict[str, Any] = {dep: ctl._outputs.get(dep) for dep in sorted(deps)}
    if item.has_item:
        view["__item"] = item.item
    if item.iter_index is not None:
        view["__iter"] = item.iter_index
    return view


def store_prompt(ctl: RunController, path: str, prompt: str) -> str:
    """Persist the fully-resolved prompt and return its ref.

    Required for trajectory replay (§5): the acceptance bar is that prompt → tool
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


def _secret_resolver(key: str) -> str:
    """Resolve `{{secret:KEY}}` from the credential store Settings → Secrets writes.

    Injected rather than imported at the binding layer so unit tests never touch real
    credentials, and so the resolution point is a single auditable seam.

    An unknown name returns "" rather than raising: `resolve()` treats an empty secret as
    a resolution failure and reports it with the binding's own error message, which is
    more actionable than a bare `KeyError` from two layers down. An OWNED key (a provider's or
    an app's own `PCSECRET_…` key) raises instead, with the sentence that says why no step can
    read it: that key is set, so "is not set" would be false.
    """
    from personalclaw.config.loader import config_dir
    from personalclaw.llm.credentials import CredentialStore, OwnedCredentialRefused

    try:
        cred = CredentialStore(config_dir()).resolve(key)
    except OwnedCredentialRefused as refused:
        raise BindingError(
            refused.cause, remediation=refused.remedy, caller_supplied=True
        ) from None
    except KeyError:
        return ""
    return cred.secret or ""
