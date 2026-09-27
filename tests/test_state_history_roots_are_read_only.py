"""`state_history.roots()` is a read: it creates nothing, and it answers for the home asked about.

It resolved the memory root through `config.loader.workspace_root()`, which (a) reads the ACTIVE
home — so `roots(home=other)` described the wrong home's memory — and (b) CREATES the folder it
returns. The time-travel debouncer resolves the roots on every write of every store
(`root_for_path`), so every write in the process made `<config_dir>/workspace`. And
`workspace_root()` is the folder new SESSIONS start in, which the owner may point anywhere
(`PERSONALCLAW_WORKSPACE`, or the folder `personalclaw setup` saves), while every memory tree is
written under the home's own `workspace` folder (`memory.workspace_dir`, `memory_dir_for_cwd`) —
so with a folder of their own chosen, time travel tracked that folder's `memory`/`_ext`, not the
memory.
"""

from __future__ import annotations

import pytest

from personalclaw.durability import history_debounce
from personalclaw.durability import state_history as sh


@pytest.fixture(autouse=True)
def _homes(tmp_path, monkeypatch):
    """The ACTIVE home, another home to ask about, and a folder new sessions start in."""
    active, other, sessions = tmp_path / "active", tmp_path / "other", tmp_path / "sessions"
    for path in (active, other):
        path.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(active))
    monkeypatch.delenv("PERSONALCLAW_WORKSPACE", raising=False)
    history_debounce.uninstall(flush=False)
    yield active, other, sessions
    history_debounce.uninstall(flush=False)


def _memory(home=None) -> sh.HistoryRoot:
    return next(r for r in sh.roots(home) if r.id == "memory")


def test_the_memory_root_is_the_home_asked_about(_homes):
    """🔴 Red on main: it was the ACTIVE home's workspace, whatever home was named."""
    active, other, _sessions = _homes
    assert _memory(other).worktree == other / "workspace"
    assert _memory().worktree == active / "workspace"


def test_resolving_the_roots_creates_nothing(_homes):
    """🔴 Red on main: resolving them made `<config_dir>/workspace`."""
    active, other, _sessions = _homes
    before = {p for p in active.rglob("*")} | {p for p in other.rglob("*")}
    sh.roots()
    sh.roots(other)
    after = {p for p in active.rglob("*")} | {p for p in other.rglob("*")}
    assert after == before, f"resolving the roots created {sorted(map(str, after - before))}"


def test_a_folder_chosen_for_sessions_does_not_move_the_memory_root(_homes, monkeypatch):
    """🔴 Red on main: the memory root followed `PERSONALCLAW_WORKSPACE`, where no memory is."""
    active, _other, sessions = _homes
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(sessions))
    from personalclaw.config.loader import memory_dir_for_cwd

    assert _memory().worktree == active / "workspace"
    # ...which is where memory is written, in both of its forms.
    assert memory_dir_for_cwd(None).is_relative_to(_memory().worktree)
    assert not sessions.exists(), "resolving the roots must not create the sessions folder either"


def test_a_write_elsewhere_creates_no_workspace(_homes):
    """🔴 Red on main: the debouncer's classification of ANY write created the workspace."""
    active, other, _sessions = _homes
    if not sh.git_available():
        pytest.fail("time travel needs git; this test must not pass without exercising the seam")
    debouncer = history_debounce.install(home=other, start=False)
    assert debouncer is not None
    from personalclaw.atomic_write import atomic_write

    atomic_write(other / "notes.txt", "untracked\n")
    atomic_write(other / "config.json", "{}\n")  # tracked: the config root
    assert debouncer.pending_roots() == ("config",), "the seam must have classified the writes"
    assert not (active / "workspace").exists()
    assert not (other / "workspace").exists()
