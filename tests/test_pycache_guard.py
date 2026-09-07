"""Proof for the bytecode-cache rail (#2659).

The first four cases are the issue's repro, executable: a same-length edit whose
``(int(mtime), size)`` is byte-identical to the original is **masked** by the
default ``__pycache__``, is still masked under ``python -B``, is still masked by a
*relocated* cache that is reused across runs, and is **picked up** by a per-run
prefix. The mask is forced rather than raced — ``_same_length_edit`` restores the
original mtime and asserts the size is unchanged — so these are deterministic, not
timing-dependent.

The remaining cases are the rail's teeth: that this very run is reading its
bytecode through a per-run prefix, that no module under test slipped in before the
prefix took effect, and that the detector fires when driven against a module cached
elsewhere (a guard only ever exercised by the thing it guards cannot be
distinguished from one that never fires).
"""

import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pycache_guard
import pytest

# Same-length bodies. The literal is three characters in every variant, so the
# file's size is identical across edits — half of the collision the rail removes.
_SOURCE = 'def probe():\n    return "{token}"\n'

_PROBE = "probe_module.py"


def _tree(tmp_path: Path, token: str) -> Path:
    """A one-module importable directory whose ``probe()`` returns ``token``."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / _PROBE).write_text(_SOURCE.format(token=token))
    return root


def _same_length_edit(root: Path, token: str) -> None:
    """Rewrite the probe to ``token``, keeping ``(int(mtime), size)`` identical.

    This is the mutation-testing cycle in its worst shape, made deterministic: a
    real mutate/revert loop hits this collision by landing inside one integer
    second, which is likely but not certain; restoring the mtime explicitly makes
    it certain, so the case cannot pass by luck on a fast machine.
    """
    source = root / _PROBE
    before = source.stat()
    body = _SOURCE.format(token=token)
    assert len(body.encode()) == before.st_size, "the edit must not change the size"
    source.write_text(body)
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = source.stat()
    assert (int(after.st_mtime), after.st_size) == (int(before.st_mtime), before.st_size)
    assert token in source.read_text(), "the new body really is what is on disk"


def _run(root: Path, *, prefix: Path | None, dash_b: bool = False) -> str:
    """Import the probe in a fresh interpreter and return what it printed.

    ``prefix=None`` means "no ``PYTHONPYCACHEPREFIX``" — the plain, unprotected
    invocation. The parent environment is scrubbed of the three variables that
    would otherwise decide the answer for us: this pytest run has a prefix set (by
    the rail under test), and ``PYTHONDONTWRITEBYTECODE`` / ``PYTHONSAFEPATH``
    would suppress caching and the implicit ``cwd`` entry respectively.
    """
    env = dict(os.environ)
    for name in ("PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE", "PYTHONSAFEPATH"):
        env.pop(name, None)
    if prefix is not None:
        env["PYTHONPYCACHEPREFIX"] = str(prefix)
    argv = [sys.executable]
    if dash_b:
        argv.append("-B")
    argv += ["-c", "import probe_module; print(probe_module.probe())"]
    done = subprocess.run(
        argv, cwd=root, env=env, capture_output=True, text=True, timeout=60, check=True
    )
    return done.stdout.strip()


class TestTheMaskingIsReal:
    """Without the rail, the interpreter reports a result for code that is not on disk."""

    def test_the_default_cache_masks_a_same_length_same_mtime_edit(self, tmp_path):
        root = _tree(tmp_path, "AAA")
        assert _run(root, prefix=None) == "AAA"
        assert (root / "__pycache__").is_dir(), "the first run must have written a cache"

        _same_length_edit(root, "BBB")

        assert _run(root, prefix=None) == "AAA", (
            "the source on disk says BBB and the interpreter ran the cached AAA — this is "
            "the masking #2659 is about. If this case ever goes green on its own, the "
            "interpreter's default invalidation changed and the rail can be re-argued."
        )

    def test_dash_B_does_not_fix_it(self, tmp_path):
        """The trap: ``-B`` suppresses *writing* a cache and still reads one."""
        root = _tree(tmp_path, "AAA")
        assert _run(root, prefix=None) == "AAA"
        _same_length_edit(root, "BBB")

        assert _run(root, prefix=None, dash_b=True) == "AAA", (
            "-B sets sys.dont_write_bytecode; it does not stop the import system reading "
            "an existing .pyc. Adopting it as the remedy leaves the masking in place while "
            "believing it fixed."
        )

    def test_relocating_the_cache_without_making_it_fresh_masks_identically(self, tmp_path):
        """Freshness is the load-bearing property, not the location.

        This is why ``activate()`` refuses to inherit a prefix that is not marked as
        one of its own: a stable ``PYTHONPYCACHEPREFIX`` exported in a shell moves the
        cache out of the tree and masks exactly as well.
        """
        root = _tree(tmp_path, "AAA")
        stable = tmp_path / "stable-cache"
        assert _run(root, prefix=stable) == "AAA"
        _same_length_edit(root, "BBB")

        assert _run(root, prefix=stable) == "AAA"


class TestThePerRunPrefixRemovesIt:
    def test_a_fresh_prefix_per_run_reads_the_source_on_disk(self, tmp_path):
        root = _tree(tmp_path, "AAA")
        assert _run(root, prefix=tmp_path / "run-1") == "AAA"

        _same_length_edit(root, "BBB")

        assert _run(root, prefix=tmp_path / "run-2") == "BBB", (
            "with no cache from the previous run reachable, there is nothing to validate "
            "incorrectly — the mutation on disk is the code that runs"
        )
        assert not (root / "__pycache__").exists(), "nothing was written beside the source"

    def test_it_holds_for_a_second_same_length_edit(self, tmp_path):
        """Not a one-shot: the third cycle of a mutate/revert loop behaves too."""
        root = _tree(tmp_path, "AAA")
        assert _run(root, prefix=tmp_path / "run-1") == "AAA"
        _same_length_edit(root, "BBB")
        assert _run(root, prefix=tmp_path / "run-2") == "BBB"
        _same_length_edit(root, "AAA")
        assert _run(root, prefix=tmp_path / "run-3") == "AAA"
        _same_length_edit(root, "CCC")
        assert _run(root, prefix=tmp_path / "run-4") == "CCC"


class TestTheRailIsArmedForThisRun:
    """Teeth. Drop the ``activate()`` call from conftest and these go red."""

    def test_this_interpreter_has_a_per_run_prefix(self):
        assert sys.pycache_prefix, pycache_guard.format_report(None, [])
        prefix = Path(sys.pycache_prefix)
        assert prefix.name.startswith(pycache_guard.DIR_MARKER)
        assert prefix.is_dir()

    def test_no_module_under_test_was_imported_before_the_prefix_took_effect(self):
        assert sys.pycache_prefix, pycache_guard.format_report(None, [])
        prefix = Path(sys.pycache_prefix)
        under_test = [
            module
            for name, module in sorted(sys.modules.items())
            if module is not None and name.split(".")[0] in {"personalclaw", "harness"}
        ]
        assert under_test, "vacuous otherwise — nothing under test was loaded"
        outside = pycache_guard.cached_outside(prefix, under_test)
        assert outside == [], pycache_guard.format_report(prefix, outside)

    def test_the_worker_and_its_children_pass_the_prefix_on(self):
        """A subprocess a test spawns inherits the run's prefix, not a stale cache."""
        assert os.environ.get(pycache_guard.ENV_VAR) == sys.pycache_prefix


class TestTheDetectorFires:
    """The detector, driven against fakes rather than only against a healthy run."""

    def test_it_names_a_module_cached_outside_the_prefix(self, tmp_path):
        stale = ModuleType("stale_module")
        stale.__cached__ = str(tmp_path / "elsewhere" / "__pycache__" / "stale.pyc")
        assert pycache_guard.cached_outside(tmp_path / "prefix", [stale]) == ["stale_module"]

    def test_it_accepts_a_module_cached_under_the_prefix(self, tmp_path):
        prefix = tmp_path / "prefix"
        fresh = ModuleType("fresh_module")
        fresh.__cached__ = str(prefix / "some" / "path" / "__pycache__" / "fresh.pyc")
        assert pycache_guard.cached_outside(prefix, [fresh]) == []

    def test_a_module_with_no_bytecode_cache_is_not_reported(self, tmp_path):
        """Built-in, frozen and namespace modules have no ``.pyc`` to go stale."""
        builtin = ModuleType("builtin_module")
        namespaced = ModuleType("namespace_module")
        namespaced.__cached__ = None  # type: ignore[assignment]
        assert pycache_guard.cached_outside(tmp_path, [builtin, namespaced]) == []

    def test_the_report_states_which_case_it_is_in(self, tmp_path):
        assert "DISARMED" in pycache_guard.format_report(None, [])
        assert "FAILED" in pycache_guard.format_report(tmp_path, ["a.b"])
        assert "a.b" in pycache_guard.format_report(tmp_path, ["a.b"])
        healthy = pycache_guard.format_report(tmp_path, [])
        assert "FAILED" not in healthy and "DISARMED" not in healthy


class TestActivateIsIdempotentAndShareable:
    @pytest.fixture(autouse=True)
    def _restore_interpreter_state(self, monkeypatch):
        """Nothing here may leave the real prefix changed for the rest of the worker."""
        monkeypatch.setattr(sys, "pycache_prefix", sys.pycache_prefix)

    def test_it_reuses_a_prefix_this_run_already_created(self, monkeypatch, tmp_path):
        """How one run's xdist workers end up sharing one compiled tree."""
        inherited = tmp_path / f"{pycache_guard.DIR_MARKER}inherited"
        monkeypatch.setenv(pycache_guard.ENV_VAR, str(inherited))

        assert pycache_guard.activate() == inherited
        assert sys.pycache_prefix == str(inherited)
        assert inherited.is_dir(), "an inherited-but-absent directory is created, not assumed"

    def test_it_overrides_a_prefix_that_is_not_a_per_run_one(self, monkeypatch, tmp_path):
        """A developer's stable cache directory is not freshness — see the masking case."""
        stable = tmp_path / "my-stable-cache"
        monkeypatch.setenv(pycache_guard.ENV_VAR, str(stable))

        got = pycache_guard.activate()

        assert got != stable
        assert got.name.startswith(pycache_guard.DIR_MARKER)
        assert os.environ[pycache_guard.ENV_VAR] == str(got)
        assert sys.pycache_prefix == str(got)

    def test_it_creates_one_when_nothing_is_set(self, monkeypatch):
        monkeypatch.delenv(pycache_guard.ENV_VAR, raising=False)

        got = pycache_guard.activate()

        assert got.is_dir()
        assert got.name.startswith(pycache_guard.DIR_MARKER)

    def test_the_directory_it_creates_is_removed_when_that_process_exits(self):
        """Per-run means per-run: a cold cache is worth nothing if it accumulates."""
        code = (
            f"import sys; sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
            "import pycache_guard; print(pycache_guard.activate())\n"
        )
        env = dict(os.environ)
        env.pop(pycache_guard.ENV_VAR, None)
        done = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        created = Path(done.stdout.strip())

        assert created.name.startswith(pycache_guard.DIR_MARKER)
        assert not created.exists(), f"{created} outlived the process that created it"
