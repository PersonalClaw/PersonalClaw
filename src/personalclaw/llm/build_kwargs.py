"""What a model provider's factory reads from the build kwargs core hands it.

``ProviderRegistry.build(name, **kwargs)`` passes a factory the per-call requests core makes of
the model it is building: a sampling ``temperature`` (best-of-N's ladder) and
an output ``max_tokens`` budget (``local_models.budgets.output_budget``, #3595). Each factory turns
them into its own wire field, and each read them with the same few lines: core's branded-app
factory, the bundled Ollama and bundled-chat apps, and — copied into every one of them by
PersonalClawApps #124 — five first-party model apps. A copy that forgets a clause drops the
request without a word, and best-of-N then samples one answer N times. Published on
``personalclaw.sdk.model``.
"""

from __future__ import annotations

from collections.abc import Mapping


def positive_count(value: object) -> int | None:
    """``value`` as a positive whole count, or ``None`` when it is not one.

    The one coercion for an operator-set count (a served context window, an output cap).
    ``bool`` is not a count although ``True`` is an ``int``; ``0``, ``None`` and anything
    uncoercible mean UNSET; and a numeric STRING counts, because the settings form stores what
    was typed (``"context_window": "32768"`` is what a real home carries).
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        count = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return count if count > 0 else None


def per_call_temperature(build_kwargs: Mapping[str, object]) -> float | None:
    """The sampling temperature this ``build`` call asked for, or ``None`` when it asked for none.

    It wins over an entry's own ``temperature``: the caller asking for THIS temperature is more
    specific than the instance default. ``True`` is not a temperature.
    """
    value = build_kwargs.get("temperature")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def output_cap(configured: object, per_call: object, default: int | None = None) -> int | None:
    """The request's output-token cap: the operator's configured ``max_tokens``, else the budget
    core derived for this call (the ``max_tokens`` build kwarg), else ``default``.

    Only a positive count is a cap (:func:`positive_count`).
    """
    return positive_count(configured) or positive_count(per_call) or default


#: The output cap of a request to a model that thinks, when neither the operator nor the call
#: set one. Its thinking is spent out of the same cap, and at 4,096 a model that reasons at length
#: spends it all before it writes a word or makes a call, and the turn ends with no answer. Every
#: model that thinks adaptively allows at least 64,000.
THINKING_OUTPUT_CAP = 32_000

#: The output cap of a request to any other model, when nothing set one.
DEFAULT_OUTPUT_CAP = 4096


def default_output_cap(model_id: str, ceiling: object = None) -> int:
    """The output cap of a request to *model_id* when neither the operator nor the call set one:
    :data:`THINKING_OUTPUT_CAP` for a model that thinks adaptively (``catalog.thinks_adaptively``:
    Opus 5.5 always thinks, and the newest Sonnet, Haiku and Fable do by default), else
    :data:`DEFAULT_OUTPUT_CAP`. Never above *ceiling*, the most a request to the model may ask
    for, when the caller knows it."""
    from personalclaw.llm.catalog import thinks_adaptively

    cap = THINKING_OUTPUT_CAP if thinks_adaptively(model_id) else DEFAULT_OUTPUT_CAP
    most = positive_count(ceiling)
    return min(cap, most) if most else cap
