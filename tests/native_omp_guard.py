"""Detector for the torch-free-core rail: no test may make ``torch`` resident in
the pytest worker, because a second OpenMP runtime in a process that also uses
``faiss`` aborts it (#3324).

The invariant is not new — it is written down twice and was simply never
enforced. ``embedding_providers/registry.py``'s ``native_provider`` says core
"stays torch-free and degrades gracefully when the app is absent", and the
``sentence-transformers`` app's own module docstring says it "owns that heavy
dependency ... so core stays torch-free". This module makes those sentences
executable.

Why it has to be a rail and not a one-line fix
----------------------------------------------
On macOS arm64 ``torch/lib/libomp.dylib`` and ``faiss/.dylibs/libomp.dylib`` are
two copies of the LLVM OpenMP runtime. Importing both packages is harmless;
*initializing* the second runtime is not, and ``faiss`` initializes its copy the
first time it enters an ``#pragma omp parallel`` region — which is its ``search``,
not its ``add``. So the pairing is invisible to an import-order probe and shows up
as::

    OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib already
    initialized.

followed by ``SIGABRT`` (rc 134) from inside ``faiss/swigfaiss.py:search``. Under
xdist that surfaces as ``worker 'gwN' crashed`` attributed to whichever test held
the worker, so the crash appears to wander between tests and shards while the
real cause — *any* earlier import in the same worker — is never named. Removing
one import site therefore cannot be self-certifying: the next ``import`` recreates
the pairing and the red comes back wearing a different test's name.

Why "it did not abort" is not evidence
--------------------------------------
``sklearn/__init__.py`` and ``threadpoolctl`` both run
``os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "True")`` at import time, and
``sentence_transformers`` imports ``sklearn``. That flag is exactly the workaround
LLVM documents as unsafe and able to "silently produce incorrect results", and it
suppresses the abort. Measured on this platform, each row its own subprocess, a
bare ``faiss`` search after the named import:

===================================  ===  ==========================
import                               rc   KMP_DUPLICATE_LIB_OK after
===================================  ===  ==========================
``torch``                            134  unset
``faster_whisper``                   134  unset
``sentence_transformers``              0  ``'True'`` (set by sklearn)
``sklearn``                            0  ``'True'``
``transformers``                       0  unset (no torch resident)
===================================  ===  ==========================

So a suite that happens to import ``sentence_transformers`` first is immune for
the wrong reason, and a passing run proves nothing about the pairing. The rail
watches the *residency*, which is the thing that is actually forbidden, rather
than the abort, which is maskable.

Detection shape — a per-test transition, not a per-test presence
----------------------------------------------------------------
``resident()`` is a plain mapping query, so ``conftest.py`` can call it before and
after every test and blame only the test that moved a hazard module from absent to
resident. A presence check would instead red every remaining test in the poisoned
worker and bury the culprit in the cascade; a session-end check would name no test
at all. ``pytest_sessionstart`` additionally hard-fails when a hazard module is
already resident before the first test, which is the one case no per-test
transition can attribute — an import performed by a plugin or by ``conftest``
itself.

Keeping the detector here rather than inline in ``conftest.py`` is what makes it
provable: ``tests/test_native_omp_guard.py`` drives it against a throwaway mapping
and asserts it fires. A detector that can only ever be exercised by the thing it
guards is indistinguishable from one that never fires.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

# Top-level packages that ship their own ``libomp.dylib`` *and* are not already
# masking the duplicate-runtime guard for everyone else. ``torch`` is the only
# entry deliberately: it is the runtime that pairs with faiss's, and checking the
# top-level name catches every route into it (``faster_whisper``,
# ``sentence_transformers``, ``transformers``' torch backend, an app's provider
# module) without this list having to enumerate them.
#
# ``sklearn`` is NOT listed. It also bundles a libomp copy, but it coexists with
# faiss — measured rc 0 — because its ``__init__`` sets KMP_DUPLICATE_LIB_OK
# before anything can trip the guard. Listing it would red on a hazard that does
# not exist and would make the rail's reds untrustworthy.
HAZARD_MODULES: tuple[str, ...] = ("torch",)

# The mask that makes the abort disappear without making the pairing safe. Only
# reported in the failure message — never asserted on. Core cannot stop sklearn
# from setting it, so failing on it would be a red nothing in this repo can fix.
_MASK_ENV = "KMP_DUPLICATE_LIB_OK"


def resident(modules: Mapping[str, Any]) -> tuple[str, ...]:
    """The hazard packages present in ``modules`` (normally ``sys.modules``)."""
    return tuple(name for name in HAZARD_MODULES if name in modules)


def masked(env: Mapping[str, str] | None = None) -> bool:
    """Whether the duplicate-OpenMP abort is currently suppressed by the env flag."""
    return (os.environ if env is None else env).get(_MASK_ENV) is not None


def explain(newly_resident: tuple[str, ...], where: str) -> str:
    """The failure message for a hazard module that became resident at ``where``."""
    names = ", ".join(newly_resident)
    lines = [
        f"torch-free-core rail: {where} made {names} resident in this pytest worker.",
        "",
        f"A second OpenMP runtime ({names}'s bundled libomp.dylib) in a process that also",
        "uses faiss aborts it the next time faiss enters a parallel region — i.e. the next",
        "faiss `search`, which is how every episodic write dedups. Under xdist that reads as",
        "`worker 'gwN' crashed` blamed on an unrelated test in the same worker (#3324).",
        "",
        "Fix the import, not this rail: answer a presence question with",
        "`importlib.util.find_spec(...)`, and keep an in-process torch-bearing provider out",
        "of core (the sentence-transformers / faster-whisper APPS own that dependency).",
    ]
    if masked():
        lines += [
            "",
            f"NOTE: {_MASK_ENV} is set in this environment, so the abort is currently",
            "SUPPRESSED rather than absent. LLVM documents that flag as unsafe and able to",
            "produce silently wrong results, so a green run here is not evidence the pairing",
            "is safe — which is exactly why this rail watches residency, not the crash.",
        ]
    return "\n".join(lines)
