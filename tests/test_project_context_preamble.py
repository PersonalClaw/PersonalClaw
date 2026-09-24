"""Tests for the project-bound chat context preamble (Slice 6).

A project-bound chat's first turn is fed `_project_context_preamble`, which must
tell the agent its project, workspace, context dir — AND list what's actually in
that context dir (so it can read shared loop/chat outcomes like `decisions.md`
without having to guess the directory's contents)."""

from unittest.mock import patch

import pytest

from personalclaw.dashboard.chat_utils import _project_context_preamble
from personalclaw.tasks.hierarchy import HierarchyStore


@pytest.fixture()
def store(tmp_path):
    # The preamble framing is now a bundled snippet (``project-context``) rendered
    # from PERSONALCLAW_HOME; the global autouse ``_isolate_personalclaw_home``
    # fixture (tests/conftest.py) already points it at a throwaway home.
    with patch("personalclaw.tasks.hierarchy.config_dir", return_value=tmp_path):
        yield HierarchyStore()


def test_preamble_unknown_project_is_empty(store):
    assert _project_context_preamble("p-does-not-exist") == ""


def test_preamble_names_project_workspace_and_context_dir(store):
    p = store.create_project("Website", workspace_dir="/tmp/ws")
    out = _project_context_preamble(p.id)
    assert "Website" in out
    assert "/tmp/ws" in out
    assert str(store.context_dir(p.id)) in out
    assert out.startswith("[PROJECT CONTEXT]")
    assert out.rstrip().endswith("[END PROJECT CONTEXT]")


def test_preamble_lists_context_dir_files(store):
    """The Slice-6 gap: the path alone wasn't enough — enumerate the files in it."""
    p = store.create_project("Website")
    cdir = store.context_dir(p.id)
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "decisions.md").write_text("# Decisions\nUse minimax.\n")
    (cdir / "conventions.md").write_text("kebab-case files\n")
    (cdir / ".hidden").write_text("ignored")  # dotfiles excluded

    out = _project_context_preamble(p.id)
    assert "decisions.md" in out
    assert "conventions.md" in out
    assert ".hidden" not in out
    # The listing is introduced so the agent knows these are readable for continuity.
    assert "Files in it" in out


def test_preamble_no_listing_when_context_dir_empty(store):
    """An empty/absent context dir must not emit a dangling 'Files in it' header."""
    p = store.create_project("Website")
    out = _project_context_preamble(p.id)
    assert "Files in it" not in out
