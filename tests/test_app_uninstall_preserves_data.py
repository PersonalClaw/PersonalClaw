"""The middle removal rung: uninstall removes the app and KEEPS its ``data/`` (#2541).

Before this, ``app_manager``'s removal ladder had two rungs and a hole between them:
``uninstall()`` is DEACTIVATE (nothing leaves disk) and ``force_uninstall()`` removes
everything including ``data/``. A user who wanted the app gone but the notes they wrote
with it kept had no operation at all, and the force-uninstall confirm dialog told them to
"use Uninstall instead" — a control that did not exist.

These tests drive the REAL cycle the roadmap gate (`PEP-16`) asks about rather than
asserting on a stub: a fixture app is installed into an isolated home, data is written
**through the app's own tool** (a real subprocess that writes markdown and makes real
``git`` commits into ``data/``), the new uninstall runs, and the assertions are that the
app is gone AND every note plus its git history survived — then it is reinstalled and the
notes are read back.

Both directions are proven, because a preservation test that only shows preservation
cannot tell a working preserve from a broken wipe:

* the preserving rung PRESERVES — notes, git history, an empty ``data/``, absence;
* the wiping rung STILL WIPES — ``force_uninstall`` takes ``data/`` with it, takes a
  parked copy from an earlier keep-data uninstall with it, and a reinstall after it
  comes back empty.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager

# The app's own tool. A real script, run as a real subprocess, doing real `git` work in
# `data/` — the same shape as the `notes` bundle whose measured cycle opened #2541. If
# this were a `Path.write_text` in the test body it would prove the test can write files,
# not that an app's data survives the lifecycle.
NOTE_TOOL = """\
import subprocess, sys
from pathlib import Path

book = Path(__file__).resolve().parent / "data" / "notebook"
book.mkdir(parents=True, exist_ok=True)
if not (book / ".git").is_dir():
    subprocess.run(["git", "init", "-q"], cwd=book, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=book, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=book, check=True)

title, body = sys.argv[1], sys.argv[2]
(book / f"{title}.md").write_text(body + "\\n", encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=book, check=True)
subprocess.run(["git", "commit", "-q", "-m", f"note: {title}"], cwd=book, check=True)
print("wrote", title)
"""


@pytest.fixture(autouse=True)
def _isolate_apps(tmp_path, monkeypatch):
    """Point config_dir at a tmp dir so the whole cycle runs in a throwaway home."""
    import personalclaw.config.loader as loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    return tmp_path


def _bundle(tmp_path: Path, *, name: str = "notes-fixture", ships_data: bool = False) -> Path:
    """A minimal, real app bundle carrying the note tool."""
    src = tmp_path / "src" / name
    src.mkdir(parents=True, exist_ok=True)
    (src / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": "Notes Fixture",
                "description": "A git-backed notebook fixture",
            }
        ),
        encoding="utf-8",
    )
    (src / "note.py").write_text(NOTE_TOOL, encoding="utf-8")
    if ships_data:
        # Seed content the BUNDLE ships. The user's parked data must win over it.
        (src / "data").mkdir(exist_ok=True)
        (src / "data" / "welcome.md").write_text("shipped by the bundle\n", encoding="utf-8")
    return src


def _write_note(name: str, title: str, body: str) -> None:
    """Run the app's own tool, in the installed app's own dir, as the app would."""
    app = manager.app_dir(name)
    proc = subprocess.run(
        ["python3", str(app / "note.py"), title, body],
        cwd=str(app),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"the app's note tool failed: {proc.stderr or proc.stdout}"


def _notebook(name: str) -> Path:
    return manager.app_dir(name) / "data" / "notebook"


def _notes(name: str) -> dict[str, str]:
    book = _notebook(name)
    if not book.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(book.glob("*.md"))}


def _git_log(name: str) -> list[str]:
    book = _notebook(name)
    if not (book / ".git").is_dir():
        return []
    proc = subprocess.run(
        ["git", "log", "--format=%s"], cwd=str(book), capture_output=True, text=True, timeout=60
    )
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


# ── the real cycle ────────────────────────────────────────────────────────────


def test_real_cycle_notes_and_git_history_survive_uninstall_and_reinstall(tmp_path):
    """install → write notes through the tool → uninstall → reinstall → notes readable.

    This is the transcript of the clause `PEP-16` measured FAILING on 2026-09-06, run in
    a temp home with the real app tool and real git, and it now has to pass end to end.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok

    # 1. Real data, written through the app's own tool: two notes, two git commits.
    _write_note(name, "alpha", "the first note")
    _write_note(name, "beta", "the second note")
    before_notes = _notes(name)
    before_log = _git_log(name)
    assert set(before_notes) == {"alpha", "beta"}, before_notes
    assert before_log == ["note: beta", "note: alpha"], before_log
    assert (_notebook(name) / ".git").is_dir(), "the fixture must be writing REAL git history"

    # 2. The new middle rung.
    assert app_manager.uninstall_keep_data(name) is True

    # 3. The app is GONE — not merely deactivated. Every surface agrees.
    assert not manager.app_dir(name).exists(), "the app's tree survived a removal"
    assert manager._read_installed(name) is None
    assert name not in {a["name"] for a in manager.list_apps()}, (
        "a parked data copy is being reported as an installed app; the app must be gone "
        "from discovery, not lingering as a ghost"
    )

    # ...and the data is not. It is parked where the panel says it is.
    parked = app_manager._preserved_data_dir(name)
    assert parked.is_dir(), "uninstall did not keep the app's data/"
    assert (parked / "notebook" / "alpha.md").is_file()
    assert (parked / "notebook" / ".git").is_dir(), "the git history was not preserved"

    # 4. Reinstall — and read the notes back through the same paths as before.
    assert app_manager.install(src, confirm=True).ok
    assert _notes(name) == before_notes, "notes did not survive uninstall → reinstall"
    assert _git_log(name) == before_log, "git history did not survive uninstall → reinstall"

    # 5. The parked copy is consumed by the successful reinstall, so a LATER
    #    force-uninstall cannot leave a resurrectable copy behind.
    assert not parked.exists(), "the parked copy outlived the reinstall that consumed it"


def test_parked_user_data_wins_over_data_the_bundle_ships(tmp_path):
    """A reinstall restores the USER's ``data/`` OVER the bundle's shipped seed content.

    Same precedence the update path already applies: the incoming tree's ``data/`` is
    dropped and the preserved one replaces it wholesale. Proven where it is observable
    — on a file the bundle ships AND the user has since edited. The other way round,
    every reinstall would silently reset the user's edits to the shipped defaults.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path, ships_data=True)
    assert app_manager.install(src, confirm=True).ok
    shipped = manager.app_dir(name) / "data" / "welcome.md"
    assert shipped.read_text(encoding="utf-8") == "shipped by the bundle\n"
    shipped.write_text("edited by the user\n", encoding="utf-8")  # the user's own version
    _write_note(name, "mine", "my own note")

    assert app_manager.uninstall_keep_data(name) is True
    assert app_manager.install(src, confirm=True).ok

    assert "mine" in _notes(name), "the user's note lost to the bundle's shipped data/"
    assert shipped.read_text(encoding="utf-8") == "edited by the user\n", (
        "the bundle's shipped data/ overwrote the user's restored copy — a reinstall "
        "just reset their edits to the defaults"
    )


# ── the wiping rung still wipes ───────────────────────────────────────────────


def test_force_uninstall_still_takes_the_data_with_it(tmp_path):
    """The negative half: nothing that deleted has stopped deleting.

    ``force_uninstall`` is deliberately unchanged. If this ever preserves, the two
    rungs have collapsed into one and the destructive control is lying to the user.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "doomed", "this is meant to be destroyed")
    assert (_notebook(name) / "doomed.md").is_file()

    assert app_manager.force_uninstall(name) is True

    assert not manager.app_dir(name).exists()
    assert not app_manager._preserved_data_dir(
        name
    ).exists(), "force_uninstall parked a copy of the data it was asked to destroy"
    # And a reinstall comes back EMPTY — the observable difference from the rung above.
    assert app_manager.install(src, confirm=True).ok
    assert _notes(name) == {}, "force_uninstall's data came back on reinstall"


def test_a_successful_reinstall_consumes_the_park_so_none_can_go_stale(tmp_path):
    """The ordinary route leaves nothing behind: the reinstall consumes the parked copy.

    install → keep-data uninstall (parked) → install (restores AND consumes) → force
    uninstall. This is the common path, and it must not depend on the stale-park sweep
    below: a park that outlived the install that used it is a second copy of the user's
    data nobody asked for.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "first", "round one")
    assert app_manager.uninstall_keep_data(name) is True
    assert app_manager._preserved_data_dir(name).is_dir()

    assert app_manager.install(src, confirm=True).ok
    assert "first" in _notes(name)  # the park did its job
    assert not app_manager._preserved_data_dir(name).exists(), "the park outlived its use"

    assert app_manager.force_uninstall(name) is True
    assert app_manager.install(src, confirm=True).ok
    assert _notes(name) == {}, "data came back after a force uninstall"


def test_force_uninstall_drops_a_park_a_failed_restore_left_behind(tmp_path, monkeypatch):
    """ "Removes everything" has to include a park the install could not consume.

    A park is normally consumed by the install that restores it. The one way it can
    coexist with an installed app is a restore that FAILED — the park is deliberately
    left on disk then, so the data is recoverable rather than lost. That leaves a live
    app with a parked copy beside it, and a force uninstall that skipped the park would
    let the NEXT install resurrect the very data the destructive button was pressed to
    destroy.

    Driven through the real failure path rather than by planting a directory, because a
    planted one would prove the sweep works on a state the product cannot reach.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "first", "round one")
    assert app_manager.uninstall_keep_data(name) is True
    parked = app_manager._preserved_data_dir(name)
    assert parked.is_dir()

    # Fail ONLY the restore copy (parked → the new tree's data/); staging still works,
    # so the install itself succeeds and lands an app next to an unconsumed park.
    #
    # A `monkeypatch.context()`, NOT `monkeypatch.undo()`: undo() drops every patch on
    # the shared function-scoped monkeypatch — INCLUDING the autouse fixture's
    # `config_dir` isolation — so the rest of the test would run against the real
    # ~/.personalclaw. On a test whose next call is a force uninstall, that is not a
    # style point.
    real_copytree = app_manager.shutil.copytree

    def _fail_restore(srcp, dstp, *a, **k):
        if Path(srcp) == parked:
            raise OSError("simulated restore failure")
        return real_copytree(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(app_manager.shutil, "copytree", _fail_restore)
        assert app_manager.install(src, confirm=True).ok

    assert _notes(name) == {}, "the restore was supposed to fail"
    assert parked.is_dir(), (
        "a failed restore discarded the user's only copy; it must be LEFT so the data "
        "is recoverable"
    )

    # Now the destructive rung has to take it.
    assert app_manager.force_uninstall(name) is True
    assert (
        not parked.exists()
    ), "force uninstall left a parked copy of the data it was asked to destroy"
    assert app_manager.install(src, confirm=True).ok
    assert _notes(name) == {}, (
        "data survived a force uninstall via a stale parked copy — the destructive rung "
        "has a hole in it"
    )


def test_a_failed_restore_is_recorded_not_silent(tmp_path, monkeypatch):
    """A restore that fails says so in the audit; the install still completes.

    The user asked for the app, so the install proceeds — but "your data came back" and
    "your data is still parked and did not come back" must not be the same log line.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "first", "round one")
    assert app_manager.uninstall_keep_data(name) is True
    parked = app_manager._preserved_data_dir(name)

    records: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        app_manager,
        "_audit",
        lambda op, outcome, nm, **kw: records.append((op, outcome, kw.get("detail", ""))),
    )
    real_copytree = app_manager.shutil.copytree
    monkeypatch.setattr(
        app_manager.shutil,
        "copytree",
        lambda s, d, *a, **k: (
            (_ for _ in ()).throw(OSError("nope"))
            if Path(s) == parked
            else real_copytree(s, d, *a, **k)
        ),
    )
    assert app_manager.install(src, confirm=True).ok

    detail = [d for op, outcome, d in records if op == "install" and outcome == "ok"][-1]
    assert "preserved_data=restore_failed" in detail, detail


# ── absent vs empty: two different facts, two different code paths ────────────


def test_an_empty_data_dir_is_preserved_as_an_empty_data_dir(tmp_path):
    """An EMPTY ``data/`` is parked (as an empty dir), and reported as ``empty``.

    "the app had no data dir" and "the app had a data dir and it held nothing" are
    different facts about the user's state. install() creates ``data/`` for every app,
    so this is the common case for an app the user never wrote through — and it must
    still take the preserving path, not fall through to "nothing to keep".
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    data = manager.app_dir(name) / "data"
    assert data.is_dir() and not any(data.iterdir()), "install should mint an empty data/"

    facts = app_manager.describe_app_data(name)
    assert facts["present"] is True and facts["entries"] == 0, facts
    assert app_manager._data_fact("data", data) == "data=empty"

    assert app_manager.uninstall_keep_data(name) is True
    parked = app_manager._preserved_data_dir(name)
    assert parked.is_dir(), "an empty data/ was treated as 'no data/' and was not parked"
    assert not any(parked.iterdir())


def test_an_absent_data_dir_parks_nothing_and_reports_absent(tmp_path):
    """No ``data/`` at all ⇒ no parked dir is minted, and the fact says ``absent``.

    The other side of the same distinction: a name with nothing parked must not grow an
    empty parked dir, or the next install would "restore" a directory that never
    existed and the two facts would be indistinguishable afterwards.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    # Remove the data/ install minted, so the app genuinely has none.
    import shutil

    shutil.rmtree(manager.app_dir(name) / "data")
    assert not (manager.app_dir(name) / "data").exists()

    facts = app_manager.describe_app_data(name)
    assert facts["present"] is False and facts["entries"] == 0, facts
    assert app_manager._data_fact("data", None) == "data=absent"
    assert app_manager._data_fact("data", manager.app_dir(name) / "data") == "data=absent"

    assert app_manager.uninstall_keep_data(name) is True
    assert not app_manager._preserved_data_dir(
        name
    ).exists(), "an app with NO data/ grew a parked dir; absent and empty have collapsed"


def test_describe_app_data_separates_present_from_entries(tmp_path):
    """``present`` is about existence, ``entries`` about content — never one truthiness.

    The removal dialogs make a different promise in each case, so a single "has data"
    boolean would make the wrong promise for an app with an empty data dir.
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "one", "content")
    facts = app_manager.describe_app_data(name)
    assert facts["present"] is True
    assert facts["entries"] == 1, facts  # notebook/
    assert facts["path"].endswith(f".{name}.data"), facts["path"]
    # An app that is not installed has no data and nothing counted. `path` is still the
    # slot its data WOULD occupy — it is a computed location, never a claim that
    # anything is there, and `present` is the field that carries that claim.
    ghost = app_manager.describe_app_data("ghost")
    assert ghost["present"] is False and ghost["entries"] == 0, ghost
    # A name that could never be minted as a directory has no slot at all.
    assert app_manager.describe_app_data("Not A Kebab Name") == {
        "present": False,
        "entries": 0,
        "path": "",
    }


# ── gates, refusals, and the audit channel ────────────────────────────────────


def test_unknown_app_is_false_and_parks_nothing(tmp_path):
    assert app_manager.uninstall_keep_data("ghost") is False
    assert not (manager.apps_dir() / ".ghost.data").exists()


def test_a_name_that_cannot_hold_a_parked_copy_is_refused_not_guessed(tmp_path):
    """An on-disk app whose dir name is not a mintable app id refuses this rung.

    ``list_apps`` iterates REAL directory names, so such an app is reachable. Both paths
    this rung derives (the quarantine stage and the parked dir) embed the name as a path
    segment and are rmtree/move targets, so the choice is refuse or guess a directory for
    the user's data — and the refusal has to come BEFORE anything is removed. The app
    stays intact and force_uninstall remains available for it.
    """
    weird = "Not Kebab"
    d = manager.apps_dir() / weird
    d.mkdir(parents=True)
    (d / "installed.json").write_text(
        json.dumps({"name": weird, "version": "1.0.0", "enabled": True}), encoding="utf-8"
    )
    (d / "data").mkdir()
    (d / "data" / "note.md").write_text("still here\n", encoding="utf-8")
    assert manager._read_installed(weird) is not None, "the fixture must be reachable"

    assert app_manager.uninstall_keep_data(weird) is False
    assert (d / "data" / "note.md").is_file(), "the refusal happened after a removal"
    assert d.is_dir()


def test_native_app_refuses_the_middle_rung_too(tmp_path):
    """A native app is locked on: every removal rung refuses, this one included.

    A new rung that skipped the lock would be a way to delete a Tier-1 app.
    """
    src = _bundle(tmp_path, name="native-fixture")
    mani = json.loads((src / "app.json").read_text(encoding="utf-8"))
    mani["native"] = True
    (src / "app.json").write_text(json.dumps(mani), encoding="utf-8")
    assert app_manager.install(src, confirm=True).ok

    assert app_manager.uninstall_keep_data("native-fixture") is False
    assert (manager.app_dir("native-fixture") / "app.json").is_file(), "files were removed"
    assert manager._read_installed("native-fixture") is not None


def test_preservation_failure_removes_nothing(tmp_path, monkeypatch):
    """FAIL-CLOSED: if ``data/`` cannot be copied out, the app is NOT removed.

    The rung's whole promise is "your data survives this". Proceeding to the delete
    having failed to keep it is the exact bug #2541 reports, arrived at by accident.
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "precious", "must not be lost")

    import shutil as _shutil

    def _boom(*a, **k):
        raise OSError("no space left on device")

    monkeypatch.setattr(_shutil, "copytree", _boom)

    assert app_manager.uninstall_keep_data(name) is False
    assert manager.app_dir(name).is_dir(), "the app was removed after preservation failed"
    assert (_notebook(name) / "precious.md").is_file(), "the note was destroyed anyway"
    assert manager._read_installed(name) is not None, "the app was deregistered anyway"


def test_every_outcome_reaches_the_audit_channel(tmp_path, monkeypatch):
    """Each rung emits an ``app.*`` SEL record with a REAL outcome, not a bare 'ok'.

    A data-affecting lifecycle operation that leaves no auditable trace of WHAT it did
    to the data is indistinguishable afterwards from one that did the other thing.
    """
    records: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        app_manager,
        "_audit",
        lambda op, outcome, name, **kw: records.append((op, outcome, kw.get("detail", ""))),
    )

    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "audited", "body")
    assert app_manager.uninstall_keep_data(name) is True
    assert app_manager.install(src, confirm=True).ok

    ops = [(op, outcome) for op, outcome, _ in records]
    assert ("uninstall_keep_data", "ok") in ops, ops
    # The removal it delegates to is audited on its own terms too, so the log shows the
    # files being removed AND the data being kept rather than only the friendlier half.
    assert ("force_uninstall", "ok") in ops, ops

    keep = next(d for op, _, d in records if op == "uninstall_keep_data")
    assert keep == "data=1", f"the record must say what happened to data/, saw {keep!r}"

    # The reinstall's record says the data went back, and how much of it.
    reinstall = [d for op, outcome, d in records if op == "install" and outcome == "ok"][-1]
    assert "preserved_data=1" in reinstall, reinstall
    first_install = [d for op, outcome, d in records if op == "install" and outcome == "ok"][0]
    assert "preserved_data=absent" in first_install, (
        "a first install must record that nothing was parked, distinctly from an empty "
        f"park; saw {first_install!r}"
    )


def test_deactivate_rung_is_unchanged_and_still_keeps_the_files(tmp_path):
    """``uninstall()`` still DEACTIVATES. The middle rung was added, not swapped in.

    Repointing this name would have converted every existing caller — the unflagged
    ``DELETE /api/apps/{name}`` among them — from "turn it off" to "delete it".
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "kept", "still here")

    assert app_manager.uninstall(name) is True

    assert manager.app_dir(name).is_dir(), "uninstall() started removing files"
    assert (_notebook(name) / "kept.md").is_file()
    meta = manager._read_installed(name)
    assert meta is not None and meta.enabled is False
    assert not app_manager._preserved_data_dir(
        name
    ).exists(), "deactivate parked a copy; nothing left disk, so there is nothing to park"
