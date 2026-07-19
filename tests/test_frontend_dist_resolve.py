"""Tests for ``personalclaw.frontend.ensure_dev_dist_symlink``.

Covers the runtime dist-resolution contract:

* pre-bundled real directory is left alone (pre-bundled build)
* valid symlink is kept
* dangling / empty symlink is replaced
* sibling ``web/dist`` is resolved and symlinked
* nothing-found returns ``None`` (caller logs a warning)
"""

from pathlib import Path

import pytest

from personalclaw import frontend


def _fake_package(root: Path) -> Path:
    """Build the minimal directory shape the resolver walks.

    The resolver computes ``repo_root = pkg_dir.parent.parent`` and looks for
    ``<repo_root>/web/dist`` (the single probe tier — the workspace-root
    sibling probe was deleted). This lays out ``<root>/src/repo/src/
    personalclaw`` as the package dir so the candidate resolves under ``root``.
    """
    pkg = root / "src" / "repo" / "src" / "personalclaw"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    return pkg


def _make_dist(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "index.html").write_text("<!doctype html><html></html>")
    return path


@pytest.fixture
def fake_pkg(tmp_path, monkeypatch):
    """Patch ``frontend.__file__`` to a throwaway filesystem layout.

    Returns the package dir. The resolver uses ``Path(__file__)`` from
    ``personalclaw.frontend`` to locate the package; monkeypatching that
    attribute redirects every probe to the temp-dir tree built per test.
    """
    pkg = _fake_package(tmp_path)
    monkeypatch.setattr(frontend, "__file__", str(pkg / "frontend.py"))
    return pkg


# ── Case 1: pre-bundled real directory ─────────────────────────────────────


def test_prebundled_real_dir_left_untouched(fake_pkg):
    """Packaged / manual install — real dir with index.html is a no-op."""
    tree_dist = fake_pkg / "static" / "dist"
    _make_dist(tree_dist)
    sentinel = tree_dist / "prebundled.marker"
    sentinel.write_text("bundled")

    result = frontend.ensure_dev_dist_symlink()

    assert result == tree_dist
    assert not tree_dist.is_symlink()
    assert sentinel.read_text() == "bundled"


# ── Case 2: existing symlinks ──────────────────────────────────────────────


def test_valid_symlink_is_kept(fake_pkg, tmp_path):
    """A symlink pointing at a valid dist stays as-is."""
    real_dist = _make_dist(tmp_path / "real-dist")
    tree_dist = fake_pkg / "static" / "dist"
    tree_dist.parent.mkdir(parents=True)
    tree_dist.symlink_to(real_dist)

    result = frontend.ensure_dev_dist_symlink()

    assert result == real_dist.resolve()
    assert tree_dist.is_symlink()
    assert tree_dist.resolve() == real_dist.resolve()


def test_dangling_symlink_is_replaced_when_candidate_exists(fake_pkg, tmp_path):
    """Stale link (target gone) gets repointed at a freshly-resolved dist."""
    dead_target = tmp_path / "gone"
    tree_dist = fake_pkg / "static" / "dist"
    tree_dist.parent.mkdir(parents=True)
    tree_dist.symlink_to(dead_target)  # dangling

    # Sibling checkout has a fresh dist — resolver should pick it up.
    sibling_dist = _make_dist(fake_pkg.parent.parent / frontend._DIR_NAME / "dist")

    result = frontend.ensure_dev_dist_symlink()

    assert result == sibling_dist.resolve()
    assert tree_dist.is_symlink()
    assert tree_dist.resolve() == sibling_dist.resolve()


def test_dangling_symlink_with_no_candidate_returns_none(fake_pkg, tmp_path):
    """Stale link + nothing to resolve → clean up and warn (returns None)."""
    tree_dist = fake_pkg / "static" / "dist"
    tree_dist.parent.mkdir(parents=True)
    tree_dist.symlink_to(tmp_path / "also-gone")

    assert frontend.ensure_dev_dist_symlink() is None
    assert not tree_dist.is_symlink()  # stale link was removed (exists() follows symlinks)


def test_symlink_to_empty_dir_is_replaced(fake_pkg, tmp_path):
    """Symlink target exists but has no index.html — treat as unusable."""
    empty_target = tmp_path / "empty-target"
    empty_target.mkdir()
    tree_dist = fake_pkg / "static" / "dist"
    tree_dist.parent.mkdir(parents=True)
    tree_dist.symlink_to(empty_target)

    sibling_dist = _make_dist(fake_pkg.parent.parent / frontend._DIR_NAME / "dist")

    result = frontend.ensure_dev_dist_symlink()

    assert result == sibling_dist.resolve()
    assert tree_dist.resolve() == sibling_dist.resolve()


# ── Case 3: fresh resolution ───────────────────────────────────────────────


def test_sibling_checkout_is_symlinked(fake_pkg):
    """Sibling web/dist is resolved and symlinked into the package tree."""
    sibling_dist = _make_dist(fake_pkg.parent.parent / frontend._DIR_NAME / "dist")

    result = frontend.ensure_dev_dist_symlink()
    tree_dist = fake_pkg / "static" / "dist"

    assert result == sibling_dist.resolve()
    assert tree_dist.is_symlink()
    assert tree_dist.resolve() == sibling_dist.resolve()


def test_no_candidate_returns_none(fake_pkg):
    """Fresh clone with nothing set up — caller sees None and warns."""
    assert frontend.ensure_dev_dist_symlink() is None
    assert not (fake_pkg / "static" / "dist").exists()


# ── Case 4: empty real directory fallback ──────────────────────────────────


def test_empty_real_dir_is_replaced_when_candidate_exists(fake_pkg):
    """A real dir with no index.html is unusable — replace with a symlink."""
    tree_dist = fake_pkg / "static" / "dist"
    tree_dist.mkdir(parents=True)  # empty — no index.html

    sibling_dist = _make_dist(fake_pkg.parent.parent / frontend._DIR_NAME / "dist")

    result = frontend.ensure_dev_dist_symlink()

    assert result == sibling_dist.resolve()
    assert tree_dist.is_symlink()


def test_resolver_produces_a_symlink_dist_root_files_resolve_through(fake_pkg):
    """Dist-root files (e.g. the favicon claw.svg) are served by dedicated
    handlers that read ``_DIST_DIR / name``. This verifies the resolver
    produces a symlink where ``resolve()`` on such a file stays under the
    tree-dist's resolved path.
    """
    sibling_dist = _make_dist(fake_pkg.parent.parent / frontend._DIR_NAME / "dist")
    (sibling_dist / "claw.svg").write_text("<svg/>")

    result = frontend.ensure_dev_dist_symlink()
    assert result is not None

    tree_dist = fake_pkg / "static" / "dist"
    asset = tree_dist / "claw.svg"

    assert asset.is_file()  # walked through the symlink
    assert tree_dist.resolve() in asset.resolve().parents
