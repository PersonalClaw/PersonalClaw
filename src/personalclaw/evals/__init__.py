"""The shared eval substrate (EVALUATION-SUBSTRATE §1).

Everything the eval substrate produces is a FILE under ``~/.personalclaw/evals/``:
there is no daemon and no database of its own. Whole-file JSON is written through
``atomic_write``; the cross-run ``results.tsv`` ledger is append-only.

ES-1a is the PURE substrate — the store layout (:mod:`personalclaw.evals.store`)
and the experiment-matrix TYPES (:mod:`personalclaw.evals.matrix`). It spawns no
process, calls no model, and runs no matrix body — the ``run_matrix`` execution
body and its subprocess isolation are ES-1b.

The input corpus has two directions. :mod:`personalclaw.evals.scenarios` installs the
SHIPPED library (authored scenarios, upgraded by a release);
:mod:`personalclaw.evals.harvest` grows the corpus in the other direction, turning real
Run Ledger entries into library-shaped cases so a regression suite is harvested rather
than authored. Both land in one installed dir under one manifest, distinguished by that
manifest's ``origin``.

:mod:`personalclaw.evals.gate` reads a curated SUBSET of that one library — the scenarios
declaring ``"tiers": ["gate"]`` — and re-runs it twice, once over the home as it is and once
with a proposal's candidate artifact staged, so a self-modification proposal carries
``{before, after, pin}`` before a human accepts it. It adds no runner: every score comes from
``run_matrix`` in the same child process every other consumer uses.
"""
