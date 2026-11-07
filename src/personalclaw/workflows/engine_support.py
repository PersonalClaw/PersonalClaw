"""Shared preparation helpers for workflow node dispatch."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from personalclaw import filled_secrets
from personalclaw.workflows.bindings import (
    _REF_RE,
    BindingContext,
    BindingError,
    refs_in,
    resolve,
)
from personalclaw.workflows.failure_taxonomy import binding_failure
from personalclaw.workflows.models import Failure, FailureClass, Node, NodeKind

#: node `model_tier` → model use case. A tier is an INTENT; the use-case bridge owns the
#: mapping to a real provider, so a template stays portable across provider setups.
DEFAULT_MODEL_TIERS = {
    "reasoning": "reasoning",
    "standard": "orchestration",
    "fast": "background",
}

#: Ceiling on `judge_samples`. Each sample is a full reasoning-tier completion, so an author typo
#: (`judge_samples: 30`) would quietly cost 30× on a gate that runs every loop iteration. Five is
#: past any real use — the shipped template asks for 3 — so hitting this bound means a mistake.
MAX_JUDGE_SAMPLES = 5


def _condition_keys(node: Node) -> frozenset[str]:
    """Config keys holding a CONDITION — parsed by `conditions`, never interpolated.

    Interpolating one can only do harm. `resolve` would write each operand's value into the
    expression's text, so a two-term `{{a}} && {{b}}` would reach the condition parser as
    `True && some text` rather than as the two references it is written in. The dispatcher
    re-reads the raw value anyway, so nothing is lost by leaving these alone.

    `expr` is a condition ONLY on a gate: on a `transform` it is the value-producing
    expression, and skipping resolution there would hand the next node a template instead
    of data. `success_when` is a condition on every kind, and is evaluated after the node
    ran — its `output.*` root does not exist at config-resolution time at all.
    """
    if node.kind is NodeKind.GATE:
        return frozenset({"expr", "success_when"})
    return frozenset({"success_when"})


def provider_names_a_secret(key: str) -> tuple[str, str]:
    """The cause and the remedy for a step whose ``provider`` names the secret *key*: a provider
    chooses where the step's config goes, so no secret may choose it."""
    return (
        f"a step's provider can't name the secret {key!r}",
        "write the provider by name, or bind it to an input that holds no secret",
    )


class ProviderNamesASecret(BindingError):
    """A step's ``provider`` read the secret :attr:`key` (:func:`no_secret`)."""

    def __init__(self, key: str) -> None:
        cause, remedy = provider_names_a_secret(key)
        super().__init__(cause, remediation=remedy, caller_supplied=True)
        self.key = key


def no_secret(key: str) -> str:
    """The resolver a step's ``provider`` is read with: it names no secret. The provider chooses
    where the step's config goes, so a value filled in there would choose by the secret."""
    raise ProviderNamesASecret(key)


def _secret_a_provider_reads(written: Any, ctx: BindingContext) -> str | None:
    """The secret a step's provider *written* reads, piped or through a run input that holds one:
    read against *ctx*'s run state as the dispatch reads it, with :func:`no_secret`. None when it
    reads none, or doesn't resolve for another reason (the resolved name answers that)."""
    if not isinstance(written, str) or "{{" not in written:
        return None
    try:
        resolve(written, replace(ctx, secret_resolver=no_secret, filled=[]))
    except ProviderNamesASecret as named:
        return named.key
    except BindingError:
        return None
    return None


def action_provider(
    node: Node, cfg: dict[str, Any], ctx: BindingContext, get_provider: Any
) -> tuple[Any, Failure | None]:
    """The provider the action step *node* dispatches to, its ``provider`` as *cfg* resolved it,
    looked up with *get_provider* (the dispatch's ``_provider_lookup``, which names the registry);
    or the failure that refuses the step: a provider that names a secret, as it resolved or as it is
    written (:func:`provider_names_a_secret`), no provider at all, a name nothing registers,
    arguments that aren't a mapping (``step_arguments.arguments``) or one named by a binding
    (``step_arguments.bound_name``), a start page its provider refuses
    (:func:`_start_page_refused`), or a resolved config *cfg* that wasn't resolved for that
    provider (``input_secrets.not_kept_for``)."""
    from personalclaw.workflows import input_secrets, step_arguments
    from personalclaw.workflows.secrets import SECRET_BINDING_RE

    name = str(cfg.get("provider", "") or "")
    named = SECRET_BINDING_RE.search(name)
    key = named.group(1) if named else _secret_a_provider_reads(node.config.get("provider"), ctx)
    if key is not None:
        cause, remedy = provider_names_a_secret(key)
        return None, Failure(failure_class=FailureClass.USER, cause_plain=cause, remediation=remedy)
    if not name:
        return None, Failure(
            failure_class=FailureClass.USER,
            cause_plain="action node has no `provider`",
            remediation="set `config.provider` to a registered action provider",
        )
    provider = get_provider(name)
    if provider is None:
        return None, Failure(
            failure_class=FailureClass.USER,
            cause_plain=f"unknown action provider {name!r}",
            remediation="install the app that provides it, or pick a registered provider",
        )
    if step_arguments.arguments(cfg) is None:
        return None, Failure(
            failure_class=FailureClass.USER,
            cause_plain="this step's arguments aren't a mapping of names to values",
            remediation="write `with` as a mapping, or a binding to one",
        )
    if step_arguments.bound_name(node.config or {}, step_arguments.holder(cfg)) is not None:
        what, fix = step_arguments.BOUND_NAME
        return None, Failure(failure_class=FailureClass.USER, cause_plain=what, remediation=fix)
    # A start page refused is refused as such, even beside a value filled in where it shouldn't
    # be (the backstop's PERMISSION refusal, `handed`): either way nothing opens and the run keeps
    # no value. The text the provider's model reads is read only when no value was filled in.
    handed = input_secrets.not_kept_for(provider, cfg, ctx)
    refused = _start_page_refused(provider, node, cfg, ctx) or (
        None if handed else _model_text_refused(provider, node, cfg, ctx)
    )
    if refused is not None:
        return None, Failure(
            failure_class=FailureClass.USER, cause_plain=refused[0], remediation=refused[1]
        )
    return provider, handed


def _start_page_refused(
    provider: Any, node: Node, cfg: dict[str, Any], ctx: BindingContext
) -> tuple[str, str] | None:
    """Why the step *node*'s start page is refused for *provider* (``start_page_key``: Browse's),
    as filled, a run input's included: read from the holder the dispatch picks from *cfg*
    (``step_arguments.arguments``), a secret read under a pipe, however it is reached
    (:func:`piped_secret`); as written and on its own with each reference it reads kept as its
    name (``browse.credentials.start_page_refusal``), which it has to resolve to be checked; and,
    filled as *cfg* holds it, opening another site than the one it names
    (``browse.credentials.start_page_moved``). None when it isn't."""
    key = getattr(provider, "start_page_key", None)
    if not key:
        return None
    from personalclaw.browse.credentials import start_page_moved, start_page_refusal
    from personalclaw.workflows.step_arguments import arguments

    written = _written(node, cfg, key)
    if piped_secret(written, ctx) is not None:
        return (
            "a browse start page can't pipe a secret, since no mask follows what the pipe makes of "
            "it",
            "take the {{secret:…}} out of the pipe",
        )
    try:
        kept = _kept(node, cfg, ctx, key)
    except BindingError:  # nothing could check it, so it fails closed
        return (
            "a browse start page that doesn't resolve with its secrets kept as references can't be "
            "checked, so it isn't opened",
            "take the pipe off what it reads",
        )
    for text in (written, kept):
        if (refused := start_page_refusal(text)) is not None:
            return refused
    return start_page_moved(kept, (arguments(cfg) or {}).get(key))


def _written(node: Node, cfg: dict[str, Any], key: str) -> Any:
    """The argument *key* of the step *node* as written, from the holder its dispatch picks from the
    resolved *cfg* (``step_arguments.holder``), a mapping or pairs; None when it is bound whole."""
    from personalclaw.workflows.step_arguments import arguments, holder

    return (arguments(node.config or {}, where=holder(cfg)) or {}).get(key)


def _kept(node: Node, cfg: dict[str, Any], ctx: BindingContext, key: str) -> Any:
    """The argument *key* of the step *node*, from the holder its dispatch picks from the resolved
    *cfg*, on its own with each reference it reads kept as its name (a run input's included): the
    holder whole when it is bound whole. Raises ``BindingError`` when it doesn't resolve so, as when
    a pipe needs what a secret's value is."""
    from personalclaw.workflows.step_arguments import arguments, holder

    where, keeping = holder(cfg), _keeping(ctx)
    written = arguments(node.config or {}, where=where)
    if written is not None:
        return resolve(written.get(key), keeping)
    return (
        arguments(node.config or {}, lambda held: resolve(held, keeping), where=where) or {}
    ).get(key)


def _keeping(ctx: BindingContext) -> BindingContext:
    """*ctx* reading each secret as its reference, ``{{secret:NAME}}``, and filling nothing in."""
    return replace(ctx, secret_resolver=lambda name: "{{secret:" + name + "}}", filled=[])


def kept_arguments(node: Node, where: str, ctx: BindingContext) -> Any:
    """The step *node*'s arguments under the holder *where*, each on its own, as a refusal of what
    the step runs quotes them (``guardrails.denylist.enforce_action``'s ``written``): with each
    reference it reads kept as its name, a run input's included (``step_arguments.arguments``), and
    one that pipes a secret as written, naming the secret (:func:`_naming_piped_secrets`): a pipe
    run on a kept reference can cut its ``{{secret:`` head, so the refusal would quote the filled
    text. One that doesn't resolve so is as written, and so is a holder bound whole that
    doesn't."""
    from personalclaw.workflows.step_arguments import arguments

    keeping = _keeping(ctx)
    written = arguments(node.config or {}, where=where)
    if written is None:
        return _kept_or_written((node.config or {}).get(where), keeping)
    return {
        name: (
            _naming_piped_secrets(value, ctx)
            if piped_secret(value, ctx) is not None
            else _kept_or_written(value, keeping)
        )
        for name, value in written.items()
    }


def _naming_piped_secrets(value: Any, ctx: BindingContext) -> Any:
    """*value* as written, each expression in it that pipes a secret (:func:`piped_secret`) naming
    that secret: ``{{secret:NAME | …}}``, the pipe as written."""
    if isinstance(value, list):
        return [_naming_piped_secrets(item, ctx) for item in value]
    if isinstance(value, dict):
        return {key: _naming_piped_secrets(item, ctx) for key, item in value.items()}
    if not isinstance(value, str):
        return value

    def _named(match: re.Match[str]) -> str:
        _head, bar, pipes = match.group(1).partition("|")
        key = piped_secret(match.group(0), ctx) if bar else None
        return "{{secret:" + key + " |" + pipes + "}}" if key else match.group(0)

    return _REF_RE.sub(_named, value)


def _kept_or_written(value: Any, keeping: BindingContext) -> Any:
    """*value* resolved by *keeping* (:func:`_keeping`); as written when it doesn't resolve so."""
    try:
        return resolve(value, keeping)
    except BindingError:
        return value


def _model_text_refused(
    provider: Any, node: Node, cfg: dict[str, Any], ctx: BindingContext
) -> tuple[str, str] | None:
    """Why the step *node*'s text its model reads is refused for *provider*, whose model may be
    handed no secret (``model_text_refusal``: Browse's goal): it names one, as written or through a
    run input, or pipes one (:func:`piped_secret`), however its holder is written, or doesn't
    resolve with each reference kept, which a pipe that needs a secret's value makes it. None when
    it isn't, and for any other provider."""
    refusal = getattr(provider, "model_text_refusal", None)
    if not refusal:
        return None
    for key in sorted(getattr(provider, "model_text_keys", None) or ()):
        try:
            refused = piped_secret(_written(node, cfg, key), ctx) is not None or names_a_secret(
                _kept(node, cfg, ctx, key)
            )
        except BindingError:  # nothing could check it, so it fails closed
            refused = True
        if refused:
            return f"{refusal[0]}: {refusal[1]}", refusal[2]
    return None


def names_a_secret(text: Any) -> bool:
    """Whether *text* reads a ``{{secret:…}}``, piped or not."""
    return any(
        expr.partition("|")[0].strip().lower().startswith("secret:") for expr in refs_in(text)
    )


def piped_secret(written: Any, ctx: BindingContext) -> str | None:
    """The secret *written* reads under a pipe, as written or through a run input that holds one
    (a nested one's included), read against *ctx* as the dispatch reads it with each read marked;
    None when no pipe runs on one. What a pipe makes of a value (a slice, a slug) no mask follows.
    """
    reads: dict[str, str] = {}

    def _marked(key: str) -> str:
        mark = f"pclaw-secret-read-{len(reads)}-"
        reads[mark] = key
        return mark

    marking = replace(ctx, secret_resolver=_marked, filled=[])
    for expr in refs_in(written):
        head, bar, _ = expr.partition("|")
        if not bar:
            continue
        try:
            value = resolve("{{" + head.strip() + "}}", marking)
        except BindingError:
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if found := next((key for mark, key in reads.items() if mark in text), None):
            return found
    return None


def resolve_config(node: Node, ctx: BindingContext) -> tuple[dict[str, Any], Failure | None]:
    """Resolve every binding in a node's config, except its conditions.

    A `BindingError` becomes a typed failure rather than an exception: the spec is wrong,
    the run should say so precisely, and a traceback in a run log tells a non-developer
    nothing actionable.

    **Filed by who can fix it** (`failure_taxonomy.binding_failure`): USER only for what the
    caller supplied, an `inputs.*` value or a `{{secret:KEY}}`, and INTERNAL for everything the
    definition reads on its own. Measured on a real escalated run (`general-project`, 26
    minutes, four failed iterations): `binding failed: unresolved reference at 'summary'` was
    filed `class: user`, and `EscalationPanel.tsx` renders the class verbatim, so the card said
    "user error" to someone who chose a model and typed a task. A `{{nodes.…}}` typo, a pipe
    misuse and a loop root read out of place are the same kind of fault: the definition's.
    **Routing is unaffected**: `needs_input.classify_block` sends `user` and `internal` both to
    `NEEDS_INPUT`, and `RETRYABLE_CLASSES` holds neither.

    The error's OWN `remediation` wins when it carries one. The fallback is generic by
    necessity and was actively misleading on the commonest failure: it asked for a
    `| default(...)` pipe that six bundled templates already had, on a class of failure no
    pipe can rescue. Only the raise site knows which mode it is, so that is where the specific
    advice comes from.
    """
    raw = dict(node.config or {})
    held = {key: raw.pop(key) for key in _condition_keys(node) if key in raw}
    kept = _taken(raw, ctx.model_text) if ctx.kept_resolver is not None else {}
    try:
        resolved = resolve(raw, ctx)
        keeping = replace(ctx, secret_resolver=ctx.kept_resolver) if kept else ctx
        for path, value in kept.items():
            # The text a model is sent keeps each reference (`BindingContext.model_text`).
            *outer, leaf = path.split(".")
            holder = resolved
            for key in outer:
                holder = holder.setdefault(key, {})
            holder[leaf] = resolve(value, keeping)
        resolved.update(held)
        return resolved, None
    except BindingError as exc:
        return {}, binding_failure(exc)


def _taken(raw: dict[str, Any], paths: frozenset[str]) -> dict[str, Any]:
    """Each of *paths* (``prompt``, ``with.goal``) taken out of *raw*, whose dicts along the way are
    copied first so the node's own config is never changed: path → what it held."""
    taken: dict[str, Any] = {}
    for path in sorted(paths):
        *outer, leaf = path.split(".")
        holder: Any = raw
        for key in outer:
            inner = holder.get(key) if isinstance(holder, dict) else None
            if not isinstance(inner, dict):
                holder = None
                break
            holder[key] = holder = dict(inner)
        if isinstance(holder, dict) and leaf in holder:
            taken[path] = holder.pop(leaf)
    return taken


#: What a judge gate's model is sent: its rubric and the evidence it is shown.
JUDGE_TEXT = frozenset({"prompt", "evidence"})


def judge_handed_a_value(cfg: dict[str, Any], ctx: BindingContext) -> Failure | None:
    """🔴 The refusal of a judge gate whose ``prompt`` or ``evidence``, resolved to *cfg*, holds a
    secret's value its bindings filled in (``BindingContext.filled``): that text is its model's,
    so it keeps references (``node_bindings.context_for``), and a value there means it wasn't
    resolved for a model. The model is never called. ``None`` otherwise."""
    filled = ctx.filled
    if not any(filled_secrets.masked(cfg.get(key), filled) != cfg.get(key) for key in JUDGE_TEXT):
        return None
    from personalclaw.workflows.models import REFUSED_TO_START

    return Failure(
        failure_class=FailureClass.PERMISSION,
        cause_plain=(
            "this judge's prompt or evidence is sent to a model, and a secret's value was filled "
            "into it instead of its reference, so the judge was not asked"
        ),
        remediation="run the workflow again",
        terminal_reason=REFUSED_TO_START,
    )


#: What a step's result says that its run keeps and shows: its output, its reason and the prompt
#: it was given; and in its failure (``Failure``), the cause, the remedy and the suggestion.
_STEP_TEXT = ("output", "degraded_reason", "resolved_prompt")
_FAILURE_TEXT = ("cause_plain", "remediation", "suggestion")


def masked(result: Any, ctx: BindingContext) -> Any:
    """*result*, what a step's dispatcher returned (``engine.NodeResult``), with every secret value
    its bindings filled in (``BindingContext.filled``) masked out of what its run keeps and shows:
    its output, its reason, its failure and the question it asks (``filled_secrets``). A command
    the step ran, or the text it built, can hold what it was handed; the run's record must not."""
    filled = ctx.filled
    if not filled:
        return result
    shown = filled_secrets.masked_fields(result, _STEP_TEXT, filled)
    failure = getattr(shown, "failure", None)
    if failure is not None:
        told = filled_secrets.masked_fields(failure, _FAILURE_TEXT, filled)
        shown = shown if told is failure else replace(shown, failure=told)
    ask = getattr(shown, "ask", None)
    if isinstance(ask, dict) and "prompt" in ask:
        shown = replace(shown, ask={**ask, "prompt": filled_secrets.masked(ask["prompt"], filled)})
    return shown


def journalled_prompt(wire: Any, composed: str) -> dict[str, Any]:
    """The three `NodeResult` prompt fields, from what the wire recorder captured (#3166).

    Returned as kwargs because every `return NodeResult(...)` on a model-calling path has to carry
    all three together: a stored body without its `prompt_redacted` flag is the half-fix that
    leaves a reader unable to tell a substituted prompt from a verbatim one.

    `captured` False means no `ModelCallGuard` was in the path — a test injecting `completion`, or a
    provider resolved without the wrap. Then the composed prompt IS what went out, because the
    scan is the only thing that would have changed it, so journaling it is exact rather than lax.

    A BLOCKED call is the one case with no body at all: nothing reached a provider, and the
    composed prompt is the text that was refused for carrying a credential. Journaling that would
    persist to disk precisely the secret the block existed to stop — so the body is dropped and the
    categories carry why. (No path writes it today: the controller only stores a prompt on the
    SUCCESS branch. This keeps it safe if that ever changes.)
    """
    if wire is None or not getattr(wire, "captured", False):
        return {
            "resolved_prompt": composed,
            "prompt_redacted": False,
            "prompt_scan_categories": (),
        }
    return {
        "resolved_prompt": "" if wire.blocked else wire.text,
        "prompt_redacted": bool(wire.redacted),
        "prompt_scan_categories": tuple(wire.categories),
    }


def resolve_use_case(node: Node, tiers: dict[str, str] | None = None) -> str:
    """Map a node's declared tier to a model use case."""
    table = dict(DEFAULT_MODEL_TIERS)
    table.update({str(k): str(v) for k, v in (tiers or {}).items()})
    tier = str((node.config or {}).get("model_tier", "standard") or "standard")
    return table.get(tier, "background")


def resolve_axis_model(use_case: str) -> str:
    """The concrete ``"Provider:model_id"`` ref the engine WOULD resolve for one axis.

    Reads the head of the active-selection CHAIN — the exact model
    `one_shot_completion` resolves for this use case — so a `cross_model` judge is
    validated against the model it will ACTUALLY run on, not a guess. Returns ``""``
    when nothing is bound (which the caller treats as an undeterminable family, so a
    cross-model gate fails closed rather than certifying against an unknown).

    Injected into `dispatch_gate` as `judge_model_resolver` so a test can pin a
    candidate family with no live provider.
    """
    try:
        from personalclaw.providers.use_cases import active_model_refs

        refs = active_model_refs(use_case)
    except Exception:  # noqa: BLE001 — an unresolvable axis is an undeterminable family
        return ""
    return str(refs[0]) if refs else ""


def _judge_sample_count(cfg: dict[str, Any]) -> int:
    """How many independent samples this judge gate takes. Always ≥ 1.

    Absent/invalid → 1, the behaviour from before sampling: a gate that never asked for it must
    not start paying for it. Clamped at `MAX_JUDGE_SAMPLES` — see that constant for why a typo
    here is expensive rather than merely wrong.
    """
    raw = cfg.get("judge_samples", 1)
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return 1
    if count < 1:
        return 1
    return min(count, MAX_JUDGE_SAMPLES)
