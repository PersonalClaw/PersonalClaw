"""Per-model context-budget derivation from the local-model catalog (LMMV §2.2).

A local model's real context window is a **catalog fact**, not a provider constant. A
4k-context GGUF and a 200k-context hosted model are the same object to every caller that
asks "how much room do I have?", and until this module existed the answer came from a
hardcoded table of *hosted* model ids (:mod:`personalclaw.model_windows`, whose
absent-model fallback is 200k) plus each adapter's own hardcoded output cap. For a local
model neither number is a fact: the model is not in the hosted table, so it inherited a
200k window it does not have, and it inherited a 4096-token output cap nobody declared.

This module derives the number instead. :class:`LocalModel` already carries
``context_tokens`` / ``output_tokens`` off the model card (LMMV §2.1), so the catalog is
the authority when it speaks and the existing shared window table remains the fallback
when it does not. ``0`` is a legitimate, expected card value meaning **"unknown"** — it
is never treated as a window of zero and is never divided by.

Scope, deliberately: this makes the *number* available and nothing more. It does not
decide what to drop when a prompt exceeds the budget — that is compaction's job
(:mod:`personalclaw.workflows.compaction`) and it is untouched here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: The output cap used when NEITHER the catalog nor the window table declares one. This
#: is the same number the provider adapters hardcoded before this module existed
#: (``llm/anthropic.py``'s ``max_tokens: int = 4096``, and the anthropic model app's
#: ``options.pop("max_tokens", 4096)``), kept here as the single named floor of last
#: resort rather than as the value every model silently receives.
DEFAULT_OUTPUT_TOKENS = 4096

#: A declared (or defaulted) output cap never eats more than this fraction of the
#: window: a 4k-context model with a 4096-token output cap would leave literally no room
#: for the prompt, which is worse than a smaller answer.
MAX_OUTPUT_FRACTION = 0.5

#: Floor for every derived number. A budget is a positive count of tokens or it is a
#: bug: a zero would make a caller's ``prompt // budget`` divide by zero and a negative
#: would read as "unbounded" at exactly the call that needs a bound most.
_FLOOR = 1


@dataclass(frozen=True)
class ContextBudget:
    """The derived budget for one model — total window, output cap, and input room.

    ``source`` names the authority the numbers came from so a caller (and a test) can
    tell a catalog-declared window from the fallback table:

    * ``"catalog"`` — the local-model card declared ``context_tokens``
    * ``"window-table"`` — no catalog fact; :func:`personalclaw.model_windows.
      model_context_window` answered (its own per-provider default included)
    """

    context_tokens: int
    output_tokens: int
    input_tokens: int
    source: str

    def to_dict(self) -> dict[str, object]:
        return {
            "context_tokens": self.context_tokens,
            "output_tokens": self.output_tokens,
            "input_tokens": self.input_tokens,
            "source": self.source,
        }


def _bare_id(model_ref: str) -> str:
    """``"Ollama:qwen3:4b"`` → ``"qwen3:4b"`` when ``Ollama`` is a registered local
    provider, else the ref unchanged. Only a qualifier that names a REAL provider is
    stripped, because a bare ollama id legitimately contains a colon (``qwen3:4b``) and
    splitting on it unconditionally would ask the catalog for a model called ``4b``."""
    from personalclaw.local_models.registry import get_provider

    for sep in (":", "/"):
        if sep in model_ref:
            head, tail = model_ref.split(sep, 1)
            if get_provider(head) is not None:
                return tail
    return model_ref


async def catalog_window(model_ref: str) -> tuple[int, int]:
    """``(context_tokens, output_tokens)`` the LOCAL-MODEL CATALOG declares for a ref.

    ASYNC because the management contract is: :meth:`LocalModelProvider.list_models` is a
    coroutine (it reaches a filesystem, or localhost for ollama). Deriving the budget
    synchronously would silently receive a coroutine object, find no model in it, and
    make this whole module inert — so the derivation is awaited, all the way up to the
    ``one_shot_completion`` call site that consumes it.

    ``(0, 0)`` means "no catalog fact" — either no registered local provider offers the
    model, or its card leaves the fields at their ``0`` = unknown default. Fail-soft by
    construction: ``list_models()`` reaches a provider (a filesystem scan, or localhost
    for ollama), and a budget lookup must never be the thing that breaks a completion.

    A qualified ref (``"Ollama:qwen3:4b"``) is resolved against that one provider; an
    unqualified id is looked for across every registered local provider.
    """
    from personalclaw.local_models.registry import get_provider, list_providers

    ref = (model_ref or "").strip()
    if not ref:
        return (0, 0)

    providers = []
    for sep in (":", "/"):
        if sep in ref:
            head = ref.split(sep, 1)[0]
            qualified = get_provider(head)
            if qualified is not None:
                providers = [qualified]
                break
    if not providers:
        providers = list_providers()

    wanted = _bare_id(ref).strip().lower()
    for provider in providers:
        try:
            models = await provider.list_models()
        except Exception:  # noqa: BLE001 — a budget lookup never breaks a completion
            logger.debug(
                "local-model catalog lookup failed for %r on %r",
                model_ref,
                getattr(provider, "name", provider),
                exc_info=True,
            )
            continue
        for model in models or []:
            name = str(getattr(model, "name", "") or "").strip().lower()
            if name and name == wanted:
                return (
                    max(0, int(getattr(model, "context_tokens", 0) or 0)),
                    max(0, int(getattr(model, "output_tokens", 0) or 0)),
                )
    return (0, 0)


async def model_budget(model_ref: str) -> ContextBudget:
    """The context budget for ``model_ref``, catalog first, window table second.

    Derivation, in order:

    1. ``context_tokens`` from the local-model catalog when the card declares it
       (``source="catalog"``); otherwise the shared hosted-model window table
       (``source="window-table"``), which carries its own conservative default.
    2. ``output_tokens`` from the card when declared, else :data:`DEFAULT_OUTPUT_TOKENS`.
    3. The output cap is clamped to :data:`MAX_OUTPUT_FRACTION` of the window, so a small
       local model is not handed an output cap that consumes its whole context.
    4. ``input_tokens`` is what is left. Every number is floored at 1 — no zero to divide
       by, no negative to read as "unbounded".
    """
    from personalclaw.model_windows import model_context_window

    ref = (model_ref or "").strip()
    declared_context, declared_output = await catalog_window(ref)

    if declared_context > 0:
        context = declared_context
        source = "catalog"
    else:
        context = max(_FLOOR, int(model_context_window(ref or None)))
        source = "window-table"

    output = declared_output if declared_output > 0 else DEFAULT_OUTPUT_TOKENS
    output = min(output, max(_FLOOR, int(context * MAX_OUTPUT_FRACTION)))
    output = max(_FLOOR, min(output, context))
    return ContextBudget(
        context_tokens=context,
        output_tokens=output,
        input_tokens=max(_FLOOR, context - output),
        source=source,
    )


async def output_budget(model_ref: str) -> int:
    """Just the derived output cap — the number a provider's ``max_tokens`` wants.

    The narrow accessor the reasoning-axis ``one_shot_completion`` path consumes, so the
    call site reads as one number rather than unpacking a dataclass it only partly uses.
    """
    return (await model_budget(model_ref)).output_tokens
