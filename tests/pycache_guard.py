"""Bytecode-cache rail: no pytest run may read a ``.pyc`` an earlier run wrote.

Why this exists (#2659). CPython validates a cached ``.pyc`` against the source's
``(int(mtime), size)``. Mutation testing — this repo's primary evidence of test
adequacy, the "N mutations applied, N caught" claim that appears in atom reasons,
PR bodies and commit messages — collides with that validator twice over:

* many mutations are **same-length** edits (``_models_dir()`` → ``_legacy_dir()``,
  ``>=`` → ``<=``, ``and`` → ``or``, one identifier swapped for another of equal
  length), so ``size`` is unchanged; and
* mutate → run → revert → mutate happens inside one integer second, and the stored
  mtime is truncated to whole seconds, so ``int(mtime)`` is unchanged too.

When both hold the interpreter considers the cache valid and executes the
**previous** bytecode. The suite then reports a result for code that is not on
disk, and the failure direction is false confidence: the mutation reads as
*caught* when what actually ran was a different mutant. It does not surface as a
failure — in the case that prompted the issue it surfaced as an impossible
result (a download reporting success against an empty tree), and absent that
absurdity it would have been reported as a clean pass.

``python -B`` is NOT the fix, and that is the trap. ``-B`` sets
``sys.dont_write_bytecode``: it suppresses *writing* a cache and still happily
*reads* one that already exists, so anyone who adopts it believes they have fixed
this while the masking continues. ``-p no:cacheprovider``, which this repo already
passes to pytest, disables pytest's own ``.pytest_cache`` and has no bearing on
``__pycache__``. Both are covered by executable cases in
``tests/test_pycache_guard.py`` rather than left as prose.

**The mechanism: a per-run cache directory outside the tree.** ``activate()``
points ``PYTHONPYCACHEPREFIX`` / ``sys.pycache_prefix`` at a fresh temp directory
created at the start of each run. Nothing an earlier run wrote is *reachable*, so
there is no cache entry to validate incorrectly — the collision is removed by
construction rather than detected. It needs no cooperation from the author of a
mutation, costs no source change, and holds for every invocation of the suite:
``make test``, a targeted ``pytest tests/test_x.py::test_y``, CI.

Why not hash-based invalidation (``py_compile`` with ``CHECKED_HASH``), the other
candidate the issue names — three measured reasons, all on this interpreter
(CPython 3.13.14):

1. **It cannot cover a mutated test file.** pytest rewrites assertions and caches
   the rewritten bytecode itself, and ``_pytest.assertion.rewrite._read_pyc``
   validates on ``int(st_mtime)`` and ``st_size`` with no hash-based mode at all.
   A same-length, same-mtime edit to a *test* would still be masked — and "did
   the test catch it?" is exactly the question mutation testing asks. The same
   rewriter derives its cache directory from ``sys.pycache_prefix``
   (``rewrite.get_cache_dir``), so a per-run prefix covers it for free.
2. **A pre-pass does not displace the vulnerable cache.** ``compileall
   --invalidation-mode checked-hash`` without ``-f`` leaves an already-current
   timestamp ``.pyc`` in place (measured: flags stayed ``0``). Getting hash-based
   coverage therefore means forcing a full recompile before every run — the same
   cost as a cold prefix, minus the immunity.
3. **Coverage is only as wide as the file list you remember to pass.** Anything
   the interpreter compiles for the first time itself — a newly added module, a
   dependency under ``site-packages``, ``harness/``, ``scripts/`` — gets a
   timestamp ``.pyc``, and mutating it is unprotected. A prefix has no list.

What the cold cache costs, measured rather than assumed: compiling the whole of
``src/personalclaw`` plus ``tests`` is 1.6 s wall / 5.3 s CPU, and importing the
gateway's full graph cold is ~1.9 s slower than warm. ``activate()`` reuses a
prefix inherited from the process that spawned it, so the xdist controller sets
the variable once and every worker starts with it already in the environment —
the tree is compiled once per run, not once per worker.

The detector half (``cached_outside``) is a pure function over module objects so
the rail can be driven against a fake and proven to fire — see
``tests/test_pycache_guard.py``. A guard that can only ever be exercised by the
thing it guards is indistinguishable from one that never fires.

WHAT THIS DOES NOT COVER, because "mutation evidence is trustworthy now" would be
too broad a claim for it. There are two ways a mutation run leaves a false record,
and this fixes one:

* *stale bytecode* — the mutation is reverted on disk and the cache serves the old
  bytecode. That is this rail.
* *abandoned residue* — the run dies partway through (interrupt, timeout,
  ``pkill``) and the mutation is **still in the source**. No cache involved; every
  later run is honestly reporting on mutated code, and if that code is uncovered
  the suite is green. Tracked as #2710. It cannot be fixed from here: it needs the
  mutation to be applied by something that can restore the tree or flag an
  unfinished run, and this repo has no mutation harness at all.

Three files are outside this rail by construction, and they are named rather than
glossed: ``conftest.py``, this module, and ``real_home_guard.py`` are imported
before the prefix can exist, so their bytecode does land in ``tests/__pycache__``
(measured: those three ``.pyc`` files and nothing else). Something has to run
first and cannot protect itself. ``real_home_guard`` in particular must stay an
eager import — it resolves ``Path.home()`` at import time on purpose, before a test
can patch it. Everything else is covered: after a full run there is no
``__pycache__`` anywhere under ``src/personalclaw`` and none for any
``tests/test_*.py``.

Scope of the doubt, stated plainly: this makes no claim that any past "N caught"
result was wrong. It says that nothing enforced the invariant, so no past claim
is self-certifying. From here on the invariant is enforced by the run itself.
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Iterable

#: The interpreter-level knob. Set in ``os.environ`` as well as on ``sys`` so that
#: subprocesses (xdist workers above all) inherit it at *startup* — before they
#: import anything — instead of having to re-derive it after the fact.
ENV_VAR = "PYTHONPYCACHEPREFIX"

#: Directory-name marker identifying a prefix as one of *ours*, i.e. created for
#: one run and thrown away with it. The check matters: a developer who exports a
#: STABLE ``PYTHONPYCACHEPREFIX`` in their shell has relocated the cache without
#: making it fresh, which masks exactly as well as ``__pycache__`` does (there is
#: a case for that in the test file). Inheriting is only safe when the inherited
#: directory carries this marker.
DIR_MARKER = "personalclaw-pycache-"


def activate() -> Path:
    """Point this interpreter's bytecode cache at a directory no earlier run wrote.

    Idempotent, and inheritance-aware in one direction only: an inherited prefix is
    reused when its directory name carries :data:`DIR_MARKER` (this run's controller
    already made one — share it, so the tree compiles once per run rather than once
    per worker), and overridden otherwise (someone's stable cache directory is not a
    per-run one, and reusing it would reintroduce the collision).

    Returns the active prefix.
    """
    inherited = os.environ.get(ENV_VAR, "")
    if inherited and Path(inherited).name.startswith(DIR_MARKER):
        prefix = Path(inherited)
        prefix.mkdir(parents=True, exist_ok=True)
        sys.pycache_prefix = str(prefix)
        return prefix

    prefix = Path(tempfile.mkdtemp(prefix=DIR_MARKER))
    os.environ[ENV_VAR] = str(prefix)
    sys.pycache_prefix = str(prefix)
    # Only the process that created the directory removes it. A process that
    # inherited one returns above without registering anything, so no worker can
    # delete the cache its siblings are still reading.
    atexit.register(shutil.rmtree, prefix, ignore_errors=True)
    return prefix


def cached_outside(prefix: Path, modules: Iterable[ModuleType]) -> list[str]:
    """Names of ``modules`` whose loaded bytecode did not come from ``prefix``.

    ``__cached__`` records where the import system looked for (and would write) a
    module's cache, resolved at import time against whatever ``sys.pycache_prefix``
    was then — so this reports the modules that were imported *before* the prefix
    was in force, which is the one way the rail can be live and still be bypassed.
    A module with no ``__cached__`` (built-in, frozen, namespace package) has no
    bytecode cache to be stale and is not reported.
    """
    outside: list[str] = []
    for module in modules:
        cached = getattr(module, "__cached__", None)
        if not cached:
            continue
        if not Path(cached).is_relative_to(prefix):
            outside.append(module.__name__)
    return sorted(outside)


def format_report(prefix: Path | None, outside: list[str]) -> str:
    """Render the rail's verdict. Always states which case it is in."""
    if prefix is None:
        return (
            "bytecode-cache rail DISARMED: sys.pycache_prefix is unset, so this run is "
            "reading __pycache__ beside the source. A same-length edit made inside one "
            "second is masked — mutation results from this run are not evidence. "
            "tests/conftest.py must call pycache_guard.activate() before it imports "
            "anything under test (see #2659)."
        )
    if not outside:
        return f"bytecode-cache rail: every loaded module read its bytecode from {prefix}."
    return "\n".join(
        [
            f"bytecode-cache rail FAILED: {len(outside)} modules were imported before the "
            f"per-run prefix {prefix} took effect, so they may have been loaded from a "
            "stale __pycache__ written by an earlier run:",
            "",
            *(f"  {name}" for name in outside),
            "",
            "Move the pycache_guard.activate() call earlier — it has to run before the "
            "first import of anything a mutation could touch.",
        ]
    )
