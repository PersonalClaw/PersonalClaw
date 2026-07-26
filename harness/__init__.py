"""PersonalClaw self-development harness — the harness, not the agent, owns verification.

This is repo-inner dev infrastructure (it lives beside ``src/``/``tests/``/``scripts/``
and is deliberately NOT part of the shipped wheel — ``pyproject`` finds packages only
under ``src/``). It mechanizes the project's existing verification *culture* into
machine-checked institutional knowledge:

- ``harness.specs`` — three spec kinds (architectural rules, triage scenarios, per-fix
  tasks) as markdown + YAML frontmatter, with shape validation.
- ``harness.profiles`` — profile → concrete command mapping (``fast``/``web``/``replay``/
  ``full``/``scan``) so ``explain``/``run`` can print/execute what a change owes.
- ``harness.scanner`` — static architectural-boundary scanner (§1.3); ``harness.selection``
  + ``harness.diff`` — diff-aware required-check selection (§1.4).
- ``harness.replay`` + ``harness.baselines`` — event-trace replay regression (§2). The
  recorder half lives in core (``personalclaw.trace_recorder``); core can't import here.
- ``harness.cli`` — ``python -m harness  validate | explain | run | scan | replay``.

The CLI runs on the repo venv (``.venv/bin/python`` at the repo root). See
``harness/README.md`` for layout and ``AGENT.md`` at the repo root for the machine-facing
gotcha list every coding agent needs.
"""

__all__ = ["__version__"]

# Tracks the harness's own contract, independent of the product version — bump when a
# spec-schema field or profile contract changes (spec files can then gate on it).
__version__ = "1"
