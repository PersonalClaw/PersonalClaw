"""Which model the PAIRED evals score against — resolved once, recorded always.

Three callers run a matrix over arms that are supposed to differ: the loop-2
:mod:`~personalclaw.evals.gate` (``before`` vs ``after``), the
:mod:`~personalclaw.evals.ablation` report (``full`` vs ``ablated``) and
:mod:`~personalclaw.evals.skills_bench` (``with`` vs ``without``). Every one of them called
``run_matrix`` without a ``provider_binding``, so every cell fell through to the offline
``scripted`` replay — which returns the SAME bytes for the same scenario no matter what the
arm staged. Two arms that differ could therefore only ever produce the same score, and the
gate reported a delta of ``0.0`` it had not measured. A zero it never measured is the
failure this module exists to remove.

The fix is not to teach the runner to inherit the operator's credentials — its
name-allowlist spawn discipline is deliberate (see
:mod:`~personalclaw.evals.cell_provider`) and routing around it would be the same defect
one layer down. It is to make the *caller* express a model, the way
``scripts/learning_benchmark.py --bind-provider`` already does, and to record what it got.

**Three outcomes, and only three.** Every one of them is written into the run's artifact
under ``provider``, so a reader can tell them apart after the fact:

``declared``
    ``evals.benchmark_model_ref`` named a ref and that ref resolved. The run scored
    against the model the operator chose.
``default_chain``
    Nothing was declared, so the use case's own resolution chain supplied one
    (:func:`~personalclaw.providers.use_cases.active_model_refs`, position 0 first). The
    artifact records ``chain_position`` as well as the ref, so a fallen-back run is never
    mistaken for a directly-bound one.
``unresolved``
    Nothing resolved. The caller must REFUSE to score and say which precondition is
    unmet — never emit a zero.

**A declared ref that does not resolve does NOT fall back.** It is ``unresolved`` with the
reason the binding gave. Falling back there would score against a model the operator did
not pick while their own choice sat broken in config — silently substituting the model is
the whole shape of the bug, and doing it *after* being told which one to use is worse than
doing it by omission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from personalclaw.evals import cell_provider

#: ``evals.benchmark_model_ref`` named it and it resolved.
SOURCE_DECLARED = "declared"
#: Nothing was declared; the use case's resolution chain supplied a ref.
SOURCE_DEFAULT_CHAIN = "default_chain"
#: Nothing resolved. The caller refuses to score.
SOURCE_UNRESOLVED = "unresolved"

#: The config field a caller points a user at when nothing resolves.
CONFIG_FIELD = "evals.benchmark_model_ref"

#: Nothing declared AND an empty resolution chain — the home has no model at all.
NO_MODEL_AT_ALL = (
    f"no model is bound for {{use_case}} and {CONFIG_FIELD} is empty — bind one in "
    "Settings → Models, or name one under Settings → Evaluations"
)

#: 🪤 A DECLARED ref that did not resolve. The inner sentence is ``cell_provider``'s own
#: diagnosis and is kept verbatim — it names the missing ``providers[]`` entry, and creating
#: one really is a Settings → Models fix. What it cannot know is the half a user needs: that
#: the ref came from a field they typed, and which field. Driving the refusal through the
#: dashboard showed the consequence — a mistyped ref in Settings → Evaluations produced a
#: sentence whose only instruction pointed at Settings → Models, a panel that does not
#: contain the value they got wrong. So the wrapper names the owner and both ways out.
DECLARED_UNRESOLVED = (
    f"{CONFIG_FIELD} names {{ref!r}}, which did not resolve: {{detail}}; correct or clear "
    "that ref under Settings → Evaluations (clearing it falls back to your default chat model)"
)


@dataclass(frozen=True)
class BenchmarkBinding:
    """What resolved, where it came from, and — when nothing did — why not.

    ``binding`` is what a caller hands ``run_matrix`` as ``provider_binding``; ``None``
    means nothing resolved and the caller must not score. Everything else on here exists
    to be RECORDED: :meth:`to_dict` goes into the run artifact unconditionally, including
    on the refusal path, because "which model answered" is not a question a reader should
    have to reconstruct from a log line.
    """

    source: str
    use_case: str
    ref: str = ""
    detail: str = ""
    chain_position: int | None = None
    binding: cell_provider.CellProviderBinding | None = field(default=None, compare=False)

    @property
    def is_bound(self) -> bool:
        return self.binding is not None

    def to_dict(self) -> dict[str, Any]:
        """The artifact block. Same KEYS in all three outcomes, so a reader can switch on
        ``source`` without probing for the presence of a field."""
        return {
            "source": self.source,
            "use_case": self.use_case,
            "ref": self.ref,
            "detail": self.detail,
            "chain_position": self.chain_position,
        }


def _declared_ref() -> str:
    """``evals.benchmark_model_ref``, or ``""``. An unreadable config reads as undeclared —
    the chain below is then tried, and a home with nothing bound refuses honestly."""
    from personalclaw.config.loader import AppConfig

    try:
        return str(getattr(AppConfig.load().evals, "benchmark_model_ref", "") or "").strip()
    except Exception:  # pragma: no cover - a corrupt config is the loader's problem
        return ""


def resolve_benchmark_binding(
    *, use_case: str = "chat", declared: str | None = None
) -> BenchmarkBinding:
    """Resolve the model the paired evals score against, recording which path won.

    ``declared`` overrides the config read (the CLI's ``--bind-provider`` equivalent); pass
    ``""`` to force the default-chain path. Never raises: an unresolvable ref becomes
    :data:`SOURCE_UNRESOLVED` carrying the binding error's own sentence, because each of the
    three callers has a legible "did not score, and here is why" state and an exception
    would bypass it.
    """
    ref = _declared_ref() if declared is None else str(declared or "").strip()
    if ref:
        try:
            binding = cell_provider.resolve_binding(ref, use_case=use_case)
        except (cell_provider.CellBindingError, ValueError) as exc:
            return BenchmarkBinding(
                source=SOURCE_UNRESOLVED,
                use_case=use_case,
                ref=ref,
                detail=DECLARED_UNRESOLVED.format(ref=ref, detail=exc),
            )
        return BenchmarkBinding(
            source=SOURCE_DECLARED,
            use_case=use_case,
            ref=binding.model_ref(),
            binding=binding,
        )

    from personalclaw.providers.use_cases import active_model_refs

    try:
        chain = active_model_refs(use_case)
    except Exception:  # pragma: no cover - an unreadable active_models.json is an empty chain
        chain = []
    if not chain:
        return BenchmarkBinding(
            source=SOURCE_UNRESOLVED,
            use_case=use_case,
            detail=NO_MODEL_AT_ALL.format(use_case=use_case),
        )

    # Walk the chain in its own declared order — it IS the fallback order, so honouring only
    # position 0 would refuse a home whose default is unbuildable but whose fallback is fine.
    why: list[str] = []
    for position, candidate in enumerate(chain):
        try:
            binding = cell_provider.resolve_binding(candidate, use_case=use_case)
        except (cell_provider.CellBindingError, ValueError) as exc:
            why.append(f"{candidate}: {exc}")
            continue
        return BenchmarkBinding(
            source=SOURCE_DEFAULT_CHAIN,
            use_case=use_case,
            ref=binding.model_ref(),
            chain_position=position,
            binding=binding,
        )
    return BenchmarkBinding(
        source=SOURCE_UNRESOLVED,
        use_case=use_case,
        ref=chain[0],
        detail=(
            f"no model in the {use_case} chain resolves to a providers[] entry in this home "
            f"({'; '.join(why)}) — bind one in Settings → Models, or name one under "
            f"{CONFIG_FIELD}"
        ),
    )


__all__ = [
    "CONFIG_FIELD",
    "DECLARED_UNRESOLVED",
    "NO_MODEL_AT_ALL",
    "SOURCE_DECLARED",
    "SOURCE_DEFAULT_CHAIN",
    "SOURCE_UNRESOLVED",
    "BenchmarkBinding",
    "resolve_benchmark_binding",
]
