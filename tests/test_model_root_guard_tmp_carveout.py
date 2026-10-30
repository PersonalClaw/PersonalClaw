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


@pytest.fixture
def home_scoped_tmp(tmp_path, monkeypatch):
    """The regime the carve-out was written for, built on any host: a home with the OS temp
    directory inside it, as on a machine that exports ``TMPDIR=$HOME/...``.

    Read from the host, the regime is there only where the developer set it up. On stock macOS
    (``/var/folders/...``), stock Linux and CI (``/tmp``) the temp directory is outside the home,
    every path under it passes the catch-all whether or not the carve-out exists, and a test of
    it could not fail. So the home and its temp directory are a layout under ``tmp_path``, and
    the rail reads them where it reads the real ones: ``REAL_HOME`` and ``tempfile.gettempdir``.
    """
    home = (tmp_path / "home").resolve()
    tmp = home / "tmp"
    tmp.mkdir(parents=True)
    monkeypatch.setattr(G, "REAL_HOME", home)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp))
    return tmp


class TestATmpPathIsNotAModelRoot:
    """With the temp directory under the home, a test path under it is allowed and the rest of
    the home is not: each assertion here fails on a rail with no carve-out."""

    def test_a_path_under_the_temp_dir_is_allowed(self, home_scoped_tmp):
        target = home_scoped_tmp / "pytest-of-x" / "test_a0" / "models"
        assert G.offending_root(str(target)) is None

    def test_a_tmp_path_shaped_path_and_a_model_dir_in_it_are_allowed(self, home_scoped_tmp):
        """The end-to-end statement of the bug: the fixture the rail's own error message tells
        you to use must not be what the rail rejects. This is the assertion that was failing 55
        times on a machine with a home-scoped TMPDIR."""
        test_dir = home_scoped_tmp / "pytest-of-x" / "pytest-0" / "test_it0"
        assert G.offending_root(str(test_dir)) is None
        assert G.offending_root(str(test_dir / "models" / "some-model")) is None

    def test_the_rest_of_the_home_is_still_refused(self, home_scoped_tmp):
        """The carve-out is the temp directory, not the home: a sibling of it is a real path."""
        assert G.offending_root(str(G.REAL_HOME / "Documents" / "models")) == G.REAL_HOME

    def test_a_named_root_is_still_refused(self, home_scoped_tmp):
        assert G.offending_root(str(G.REAL_HOME / ".ollama" / "models")) is not None

    def test_the_shared_temp_base_itself_is_still_refused(self, home_scoped_tmp):
        """Strictly inside (#2999): the base every xdist worker shares is never a test's own."""
        assert G.offending_root(str(home_scoped_tmp)) == G.REAL_HOME
