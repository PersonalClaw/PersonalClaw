"""The shared eval substrate (EVALUATION-SUBSTRATE §1).

Everything the eval substrate produces is a FILE under ``~/.personalclaw/evals/``:
there is no daemon and no database of its own. Whole-file JSON is written through
``atomic_write``; the cross-run ``results.tsv`` ledger is append-only.

ES-1a is the PURE substrate — the store layout (:mod:`personalclaw.evals.store`)
and the experiment-matrix TYPES (:mod:`personalclaw.evals.matrix`). It spawns no
process, calls no model, and runs no matrix body — the ``run_matrix`` execution
body and its subprocess isolation are ES-1b.
"""
