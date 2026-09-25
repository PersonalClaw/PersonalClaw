"""Repo-inner developer scripts — baseline generators, validators, and release helpers.

Not shipped in the wheel (``pyproject``'s ``packages.find`` scopes to ``src/``), but linted
to the same bar as core: ``make lint`` runs black, isort, flake8 and mypy over this tree.

**This file is the package marker, and it is load-bearing for mypy.** The tree is already
imported *as a package* from three places — ``scripts/gate_report.py`` pulls in four
``scripts.generate_*`` baseline modules, ``scripts/generate_inert_surface_baseline.py``
imports ``scripts.generate_config_baseline``, and ``tests/test_config_baseline.py`` does the
same — and those resolved only because the repo root happens to be on ``sys.path``, which
made ``scripts`` an implicit namespace package. mypy maps every file to a module name, so
without this marker it reached ``scripts/generate_config_baseline.py`` twice under two names
(``generate_config_baseline`` from the command line, ``scripts.generate_config_baseline``
through ``gate_report``) and aborted the whole run with "Source file found twice under
different module names" before type-checking anything. One name, one module, no collision.

Direct execution is unaffected: ``python scripts/<name>.py`` does not consult ``__init__``.
"""
