"""The model-root rail must not fire on the fixture it tells you to use (#2475 part 1).

`real_model_root_guard` refuses a real model root so a test cannot delete a developer's
downloaded weights. Its bare-``REAL_HOME`` catch-all matched on ``parents``, so EVERY absolute
path under ``$HOME`` was offending — including pytest's own ``tmp_path`` whenever the OS temp
directory lives under the home directory.

Measured on such a machine, same tree and same commit, only ``TMPDIR`` differing:

    TMPDIR under $HOME     ->  55 failed, 188 passed
    TMPDIR=/private/tmp/…  -> 243 passed

The host had real weights installed in BOTH runs, so the weights were never the trigger — and
every failure said "was called with a REAL model root", pointing readers at their model cache
instead of at ``$TMPDIR``. A rail that fires on the sanctioned fixture is a rail that gets
switched off, which is the outcome the module's own docstring set out to avoid.

These tests pin both directions: the carve-out is narrow enough that every real root is still
refused, and wide enough that a tmp path is not.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import real_model_root_guard as G


class TestEveryRealRootIsStillRefused:
    """The carve-out must cost the rail nothing. This is the half that matters."""

    def test_the_bare_home_is_still_refused(self):
        assert G.offending_root(str(G.REAL_HOME)) == G.REAL_HOME

    @pytest.mark.parametrize("sub", G.FORBIDDEN_SUBPATHS)
    def test_every_named_root_is_still_refused(self, sub):
        """Parametrized over the live tuple, so a root added later is covered automatically
        rather than needing this test edited — the drift that makes a rail decorative."""
        target = G.REAL_HOME / sub / "some-model"
        assert G.offending_root(str(target)) is not None, f"{sub} must stay forbidden"

    def test_a_named_root_is_refused_even_if_it_sits_under_the_temp_dir(self, monkeypatch):
        """The ordering guarantee. Named roots are checked BEFORE the carve-out, so a real
        model root would stay refused even in the pathological case where the temp directory
        contains it. Without that ordering the exemption would be a hole."""
        monkeypatch.setattr(G, "REAL_HOME", Path(tempfile.gettempdir()).resolve())
        target = G.REAL_HOME / ".ollama" / "models"
        assert G.offending_root(str(target)) is not None

    def test_the_carve_out_switches_itself_off_when_the_temp_dir_is_the_home(self, monkeypatch):
        """If ``tempfile.gettempdir()`` IS the real home, "under the temp dir" would exempt the
        whole of ``$HOME`` and the catch-all would mean nothing. The rail keeps its original
        strictness instead of silently widening."""
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(G.REAL_HOME))
        assert G._test_path_root() is None
        assert G.offending_root(str(G.REAL_HOME / "anything")) == G.REAL_HOME


class TestATmpPathIsNotAModelRoot:
    def test_a_path_under_the_temp_dir_is_allowed(self):
        target = Path(tempfile.gettempdir()).resolve() / "pytest-of-x" / "test_a0" / "models"
        assert G.offending_root(str(target)) is None

    def test_pytests_own_tmp_path_is_allowed(self, tmp_path):
        """The end-to-end statement of the bug: the fixture the rail's own error message tells
        you to use must not be what the rail rejects. This is the assertion that was failing 55
        times on a machine with a home-scoped TMPDIR."""
        assert G.offending_root(str(tmp_path)) is None
        assert G.offending_root(str(tmp_path / "models" / "some-model")) is None

    def test_the_temp_dir_is_actually_under_the_home_on_this_machine_or_this_is_vacuous(self):
        """The vacuity floor, and it is honest about being conditional.

        On a machine whose temp directory is ALREADY outside ``$HOME`` (stock macOS
        ``/var/folders/…``, stock Linux ``/tmp``) the two tests above pass without the
        carve-out existing at all — they would have passed before the fix. So this records
        which regime the run is in rather than pretending to prove something it cannot: it
        skips with the reason, instead of reporting a green that means nothing."""
        tmp = Path(tempfile.gettempdir()).resolve()
        under_home = tmp == G.REAL_HOME or G.REAL_HOME in tmp.parents
        if not under_home:
            pytest.skip(
                f"temp dir {tmp} is not under {G.REAL_HOME}, so the tests above cannot "
                "distinguish the fixed rail from the broken one — they pass either way. The "
                "carve-out is exercised on a machine with a home-scoped TMPDIR (and by "
                "test_a_named_root_is_refused_even_if_it_sits_under_the_temp_dir, which "
                "constructs the condition rather than depending on the host)."
            )
        assert G.offending_root(str(tmp / "pytest-of-x")) is None
