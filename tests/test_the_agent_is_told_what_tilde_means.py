"""The agent's request says what ``~`` means, so a path the user writes with it resolves.

A saved prompt said "Run my weekly review from ~/Notes/Garden". The request named one folder, the
working directory, so the model called the notes tool with the path under it, and the tool, allowed
the real ``~/Notes/Garden`` only, refused: the review never ran. The home directory now rides beside
the date at the end of the session context, which the assembly cap never cuts, for every agent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.context import ContextBuilder
from personalclaw.memory import MemoryStore
from personalclaw.skills import SkillsLoader


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "noor"
    (home / "Notes" / "Garden").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _builder(tmp_path: Path) -> ContextBuilder:
    return ContextBuilder(
        memory=MemoryStore(workspace=tmp_path / "ws"),
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )


def test_the_request_names_the_home_a_tilde_path_starts_from(tmp_path, home):
    msg, _ = _builder(tmp_path).build_message(
        "Run my weekly review from ~/Notes/Garden.",
        is_new_session=True,
        session_key="dashboard:chat-1",
    )
    assert (
        f"[HOME DIRECTORY] ~ is the user's home folder, {home}. Name a file or folder in it from "
        "~, as ~/Notes/today.md, when you tell the user about it or give a tool its path: the file "
        "tools, the shell and the file viewer all read ~ as this folder.\n"
    ) in msg


def test_it_survives_a_context_cut_to_the_cap_and_the_date_stays_last(tmp_path, home, monkeypatch):
    import personalclaw.context as ctx_mod

    monkeypatch.setattr(ctx_mod, "_MAX_CONTEXT_CHARS", 200)
    store = MemoryStore(workspace=tmp_path / "ws")
    store.write("# Memory\n\n" + "Noor keeps her notes in a vault. " * 200)
    builder = ContextBuilder(
        memory=store,
        skills=SkillsLoader(skills_path=tmp_path / "skills", install_builtins=False),
    )
    ctx = builder.build_session_context(session_key="s1", cwd=str(tmp_path / "ws"))
    assert ctx.count("[HOME DIRECTORY]") == 1
    assert f"[HOME DIRECTORY] ~ is the user's home folder, {home}. " in ctx
    assert ctx.index("[HOME DIRECTORY]") < ctx.rindex("[CURRENT DATE]")
    assert "\n\n" not in ctx[ctx.rindex("[CURRENT DATE]") :].rstrip("\n")


def test_a_custom_agent_is_told_too(tmp_path, home):
    ctx = _builder(tmp_path).build_session_context(session_key="s1", agent="reviewer")
    assert f"[HOME DIRECTORY] ~ is the user's home folder, {home}. " in ctx
