"""Shared preparation helpers for workflow node dispatch."""

from __future__ import annotations

from typing import Any

from personalclaw.workflows.bindings import BindingContext, BindingError, resolve
from personalclaw.workflows.failure_taxonomy import binding_failure
from personalclaw.workflows.models import Failure, Node, NodeKind

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

    Interpolating one can only do harm. `resolve` reads a value that both starts and ends
    with braces as ONE whole reference (`bindings._WHOLE_RE`), so a two-term
    `{{a}} && {{b}}` resolves as a single path named `a}} && {{b` and fails — a gate would
    report a broken binding for an expression that is perfectly well formed. The dispatcher
    re-reads the raw value anyway, so nothing is lost by leaving these alone.

    `expr` is a condition ONLY on a gate: on a `transform` it is the value-producing
    expression, and skipping resolution there would hand the next node a template instead
    of data. `success_when` is a condition on every kind, and is evaluated after the node
    ran — its `output.*` root does not exist at config-resolution time at all.
    """
    if node.kind is NodeKind.GATE:
        return frozenset({"expr", "success_when"})
    return frozenset({"success_when"})


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
    try:
        resolved = resolve(raw, ctx)
        resolved.update(held)
        return resolved, None
    except BindingError as exc:
        return {}, binding_failure(exc)


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

    Absent/invalid → 1, which is the pre-S145 behaviour: a gate that never asked for sampling must
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
