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

import errno
import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from personalclaw.apps import app_manager, manager

# Git's own directory inside the app's data — the app writes real history there, so it is
# part of what must SURVIVE, but it is git's to write and never the product's. Named once
# because the census oracles below exclude it and the two git-level oracles read it; see
# `_content_files` for why counting files inside it is a race rather than a measurement.
_GIT_DIRNAME = ".git"

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


def _staged(name: str) -> Path:
    """Where the keep-data uninstall stages ``data/`` before parking it."""
    return app_manager._quarantine_dir() / f"{name}{app_manager._DATA_STAGE_SUFFIX}"


def _notes_at(book: Path) -> dict[str, str]:
    """The notes in a notebook dir wherever it lives — the app's, or a copy of it.

    Path-based so a surviving copy can be read back as NOTES, not merely counted as a
    directory that exists: "the dir is there" is not evidence the user's work is in it.
    """
    if not book.is_dir():
        return {}
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(book.glob("*.md"))}


def _notes(name: str) -> dict[str, str]:
    return _notes_at(_notebook(name))


def _git_log_at(book: Path) -> list[str]:
    if not (book / _GIT_DIRNAME).is_dir():
        return []
    proc = subprocess.run(
        ["git", "log", "--format=%s"], cwd=str(book), capture_output=True, text=True, timeout=60
    )
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def _git_log(name: str) -> list[str]:
    return _git_log_at(_notebook(name))


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

    # There is exactly ONE copy: the successful ``shutil.move`` consumed the quarantine
    # stage, so the success path leaves no orphan behind it. A second copy of the user's
    # data nobody asked for is a disk leak, and it goes stale the moment they reinstall.
    assert not _staged(name).exists(), "the keep-data uninstall left its staging copy behind"

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
    assert not _staged(name).exists(), "the empty-data/ park left its staging copy behind"


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
    # A name that could never be minted as a directory has no slot at all — and so cannot
    # hold an unconsumed copy under one either (#2585).
    assert app_manager.describe_app_data("Not A Kebab Name") == {
        "present": False,
        "entries": 0,
        "path": "",
        "unconsumed": [],
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


def test_a_failed_park_keeps_the_last_copy_and_says_where_it_is(tmp_path, monkeypatch):
    """The SECOND failure point: the park fails AFTER the app tree is already gone (#2574).

    The ``copytree`` above is fail-closed because nothing has been removed yet. The park
    is the other end: ``force_uninstall`` has run, the app's tree (and its ``data/``) are
    off disk, and the staged copy in quarantine is the ONLY copy of the user's work left
    on the machine. So it must be LEFT there — the policy ``_restore_preserved_data``
    already applies to a failed restore, "recoverable rather than lost" — and the audit
    line has to name it, or the record is a diagnosis with no recovery in it.

    Driven through the real failure: one raising rename on the real park. Not by planting
    or deleting a directory, which would assert about a state the product cannot reach,
    and not by checking the return value, which was already ``False`` while the data was
    being destroyed.

    The injection point moved from ``shutil.move`` to ``os.rename`` when the park became a
    single atomic rename (#2585) — the failure it drives is the same one, and it now also
    covers the case that used to send ``shutil.move`` into its ``copytree`` fallback. Left
    pointed at ``shutil.move`` this test would have gone quietly VACUOUS: the patch would
    never fire, the park would succeed, and the assertions below would run against a state
    where nothing was ever at risk.
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "alpha", "the first note")
    _write_note(name, "beta", "the second note")
    before_notes = _notes(name)
    before_log = _git_log(name)
    assert set(before_notes) == {"alpha", "beta"}, before_notes
    assert before_log == ["note: beta", "note: alpha"], before_log

    staged = _staged(name)
    records: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        app_manager,
        "_audit",
        lambda op, outcome, nm, **kw: records.append(
            (op, outcome, kw.get("detail", ""), str(kw.get("error", "")))
        ),
    )
    real_rename = os.rename
    fired: list[str] = []

    def _fail_park(s, d, *a, **k):
        if Path(s) == staged:
            fired.append("rename")
            raise OSError(errno.ENOSPC, "injected: could not park data/")
        return real_rename(s, d, *a, **k)

    monkeypatch.setattr(os, "rename", _fail_park)

    assert app_manager.uninstall_keep_data(name) is False
    assert fired == ["rename"], "the injected park failure never fired; the test is vacuous"

    # The removal has already happened. That is what makes this the dangerous branch, and
    # asserting it keeps the test honest: if the fixture ever stops reaching the park, the
    # survival assertion below would pass on a machine where nothing was ever at risk.
    assert not manager.app_dir(name).exists(), "the fixture no longer drives the park branch"
    assert not app_manager._preserved_data_dir(name).exists(), "the park was meant to fail"

    # So the stage is the last copy — and the user's notes and git history have to read
    # back OUT of it. A dir that merely exists is not evidence their work is in it.
    assert staged.is_dir(), (
        "the failed park deleted the only remaining copy of the user's data: the app tree "
        "is gone and the stage was GC'd behind it (#2574)"
    )
    assert (
        _notes_at(staged / "notebook") == before_notes
    ), "the surviving copy does not read back as the notes that went in"
    assert (
        _git_log_at(staged / "notebook") == before_log
    ), "the surviving copy lost the git history the app wrote"

    # And the record says WHERE it survived, so the fact is recovery information.
    detail, error = next(
        (d, e)
        for op, outcome, d, e in records
        if op == "uninstall_keep_data" and outcome == "error"
    )
    assert "data=park_failed" in detail, f"the fact token changed: {detail!r}"
    assert (
        str(staged) in detail or str(staged) in error
    ), f"the audit line does not say where the surviving copy is: {detail!r} / {error!r}"


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


# ── #2585: two unlabelled copies, and the four ways a site guessed which was which ──


def _content_files(root: Path) -> list[Path]:
    """Every file under *root* that is the USER's, walked with pathlib — never ``.git``.

    ``.git`` is excluded deliberately, and that exclusion is what makes the two census
    oracles below a measurement rather than a race. Git writes inside its own directory
    on its own schedule and for its own reasons — a ``*.lock`` it holds for the duration
    of a write, a ``tmp_obj_*`` placeholder, whatever an auto-maintenance run that
    ``git commit`` detached is doing — so a file census that walks a live ``.git``
    reports a number that depends on WHEN it was taken rather than on what the product
    did, and comparing two such numbers compares two moments of git's bookkeeping.

    Measured: main's run 35366025552, ``matrix-shard (3.12, macos-latest, 4)``, reported
    ``live data/ was touched`` with 28 files before and 27 after, IDENTICAL bytes
    (28 003), identical notes and identical history — a zero-byte git artifact present at
    the first reading and gone by the second, in a window whose only product code was a
    refusal that writes nothing at all. 27 is the settled count for the fixture's repo, so
    the reading that was wrong is the FIRST one. 23 of that run's 24 legs passed.

    Nothing is conceded by dropping ``.git`` from the count, because the repository is
    measured at the git level instead and more strictly than a count can manage:
    :func:`_git_log_at` reads the history back and :func:`_git_fsck_ok` proves the object
    store is COMPLETE — a truncated or missing object has the same file count as a whole
    one, and #2585's "non-empty is not complete" is exactly that trap.
    """
    if not root.is_dir():
        return []
    return [
        p for p in root.rglob("*") if p.is_file() and _GIT_DIRNAME not in p.relative_to(root).parts
    ]


def _files_at(root: Path) -> int:
    """User-file count under *root* — not asked of the product."""
    return len(_content_files(root))


def _bytes_at(root: Path) -> int:
    return sum(p.stat().st_size for p in _content_files(root))


def _git_fsck_ok(book: Path) -> bool | None:
    """Is *book*'s object store COMPLETE and readable? ``None`` when there is no repo.

    The oracle that replaces counting files inside ``.git``, and a stronger one: a copy
    that lost or truncated an object keeps its file count and fails here. ``None`` rather
    than ``False`` for "no repo there" keeps absent and broken apart — the same
    distinction the rung itself refuses to merge.
    """
    if not (book / _GIT_DIRNAME).is_dir():
        return None
    proc = subprocess.run(
        ["git", "fsck", "--no-progress", "--no-dangling"],
        cwd=str(book),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode == 0


def _shape(root: Path) -> tuple[int, int, dict[str, str], list[str], bool | None]:
    """A copy's whole observable shape: files, bytes, notes read back, history, integrity.

    Five independent oracles, none of them ``app_manager``: a directory that merely
    exists, or one whose entry COUNT matches, is not evidence the user's work is in it —
    which is precisely the trap #2585 names ("non-empty is not complete").
    """
    return (
        _files_at(root),
        _bytes_at(root),
        _notes_at(root / "notebook"),
        _git_log_at(root / "notebook"),
        _git_fsck_ok(root / "notebook"),
    )


def _capture_audit(monkeypatch) -> list[tuple[str, str, str, str]]:
    records: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(
        app_manager,
        "_audit",
        lambda op, outcome, nm, **kw: records.append(
            (op, outcome, kw.get("detail", ""), str(kw.get("error", "")))
        ),
    )
    return records


def _keep_data_failure(records: list[tuple[str, str, str, str]]) -> tuple[str, str]:
    """The ``(detail, error)`` of the keep-data rung's refusal-or-error record."""
    return next(
        (detail, error)
        for op, outcome, detail, error in records
        if op == "uninstall_keep_data" and outcome in ("error", "refused")
    )


def test_a_failed_park_leaves_no_partial_copy_at_the_parked_path(tmp_path, monkeypatch):
    """The park is ATOMIC, so a partial parked copy is unreachable rather than detected.

    #2585's first residual: ``shutil.move`` falls back to ``copytree`` + ``rmtree(src)``
    on ANY ``os.rename`` failure, and a ``copytree`` that cannot write one file copies the
    rest and raises at the end — leaving a PARTIAL copy at the parked path beside an
    intact stage. The next ``install`` restored the partial one, because "the parked dir
    exists" was being read as "the parked copy is finished".

    Measured before the fix, with one file failing mid-copy: parked 34 files / 28 044 B,
    stage 35 files / 28 060 B, and the reinstall handed the user the 34. Those two counts
    were taken with a census that walked ``.git`` as well; ``_content_files`` no longer
    does, so they will not reproduce as ``_files_at`` numbers today — the incompleteness
    they recorded is now caught by ``_git_fsck_ok`` instead, which a count could not
    distinguish from a truncated object anyway.

    The fix is not a completeness check. The destination is provably absent (the rung
    refuses otherwise), both paths are under ``apps/``, so the park is one
    ``Path.rename`` — it happens or it does not. So the assertion is that the parked path
    does not exist AT ALL, and that no ``copytree`` of the stage was ever attempted: the
    fallback that produced the partial copy is not merely survivable now, it is not
    reached.
    """
    name = "notes-fixture"
    assert app_manager.install(_bundle(tmp_path), confirm=True).ok
    _write_note(name, "alpha", "the first note")
    _write_note(name, "beta", "the second note")
    before = _shape(manager.app_dir(name) / "data")
    assert before[2] and before[3], f"the fixture wrote nothing to measure: {before}"

    staged = _staged(name)
    parked = app_manager._preserved_data_dir(name)
    records = _capture_audit(monkeypatch)
    fired: list[str] = []
    copytree_of_stage: list[str] = []
    real_rename, real_copytree = os.rename, app_manager.shutil.copytree

    def _cross_device(srcp, dstp, *a, **k):
        # EXDEV is what USED to route the park through copytree + rmtree(src). Any
        # os.rename failure did; this is simply the one the issue names.
        if Path(srcp) == staged:
            fired.append("rename")
            raise OSError(errno.EXDEV, "injected: cross-device link")
        return real_rename(srcp, dstp, *a, **k)

    def _watch_copytree(srcp, dstp, *a, **k):
        if Path(srcp) == staged:
            copytree_of_stage.append(str(dstp))
        return real_copytree(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(os, "rename", _cross_device)
        m.setattr(app_manager.shutil, "copytree", _watch_copytree)
        # Captured, not asserted yet, deliberately: the DISK claims are checked first, so a
        # regression that brings the fallback back reds on the copy it left behind rather
        # than on a return code, which says nothing about where the user's data went.
        outcome = app_manager.uninstall_keep_data(name)

    assert fired == ["rename"], "the injection never ran, so this test proved nothing"
    assert not manager.app_dir(name).exists(), "the fixture no longer drives the park branch"
    assert not copytree_of_stage, (
        "the park fell back to copying the stage — the very path that leaves a partial "
        f"copy behind: {copytree_of_stage}"
    )

    # The claim: nothing at all at the parked path. Not a partial copy, not an empty dir.
    assert not parked.exists(), (
        f"a failed park left {_files_at(parked)} files / {_bytes_at(parked)} bytes at the "
        "parked path; a later install would restore that as if it were the whole thing"
    )
    # And the stage is still the complete last copy, byte for byte.
    assert (
        _shape(staged) == before
    ), f"the surviving copy is not what went in: {_shape(staged)} vs {before}"
    assert outcome is False, "a park that did not happen must not report success"
    detail, error = _keep_data_failure(records)
    assert "data=park_failed" in detail, detail
    assert str(staged) in detail or str(staged) in error, (detail, error)


def test_a_second_keep_data_uninstall_refuses_instead_of_deleting_the_survivor(
    tmp_path, monkeypatch
):
    """#2585's second residual, and the worst of the family: it returned ``True``.

    After a failed park the stage IS the user's surviving copy (#2574) and the audit line
    tells them where it is. The next keep-data uninstall of the same app then ``rmtree``'d
    it at the top as leftover garbage. Measured before the fix: survivor 35 files with
    both notes going in, ``0`` files and ``[]`` notes coming out, return ``True``, outcome
    ``ok``. Nothing anywhere said the data was gone.

    Only this function ever writes that path, and it leaves one behind in exactly one
    case — so the single state the sweep could ever find was the one it must not touch.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "alpha", "the first note")
    _write_note(name, "beta", "the second note")
    staged = _staged(name)

    real_rename = os.rename

    def _fail_park(srcp, dstp, *a, **k):
        if Path(srcp) == staged:
            raise OSError(errno.EIO, "injected: could not park data/")
        return real_rename(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(os, "rename", _fail_park)
        assert app_manager.uninstall_keep_data(name) is False
    survivor = _shape(staged)
    assert survivor[2] == {"alpha": "the first note\n", "beta": "the second note\n"}, survivor

    # The user reinstalls (the app is gone, so the rung is unreachable until they do) and
    # writes more. Their earlier copy is still sitting in quarantine, unread.
    assert app_manager.install(src, confirm=True).ok
    assert _shape(staged) == survivor, "the reinstall touched the quarantined survivor"
    _write_note(name, "gamma", "written after the reinstall")
    live_before = _shape(manager.app_dir(name) / "data")

    records = _capture_audit(monkeypatch)
    assert app_manager.uninstall_keep_data(name) is False, (
        "the second keep-data uninstall reported success while deleting the copy the "
        "first one told the user to go and recover"
    )

    assert _shape(staged) == survivor, (
        f"the survivor was destroyed: {_shape(staged)} vs {survivor} — this is the "
        "return-True data loss #2585 reports"
    )
    # FAIL-CLOSED, so the refusal came before anything was removed.
    assert manager._read_installed(name) is not None, "the app was removed by a refusal"
    assert _shape(manager.app_dir(name) / "data") == live_before, "live data/ was touched"
    detail, error = _keep_data_failure(records)
    assert "data=unconsumed_copy" in detail, f"the new fact token is missing: {detail!r}"
    assert str(staged) in detail, f"the refusal does not name the copy it protected: {detail!r}"
    assert "Nothing was removed" in error, error
    # Second endpoint — the one the removal-confirm dialog reads.
    assert app_manager.describe_app_data(name)["unconsumed"] == [str(staged)]


def test_a_keep_data_uninstall_refuses_while_an_unconsumed_park_is_still_on_disk(
    tmp_path, monkeypatch
):
    """The two shapes the issue does NOT report, both silent, both ``True``.

    A park coexists with an installed app only when an earlier RESTORE failed, so that
    copy is data the user has never seen. Proceeding destroyed it two ways:

    * ``force_uninstall`` — which this rung calls to do its removal — discards any park
      for the name. Measured before the fix: park holding ``old`` going in, park holding
      only ``new`` coming out, return ``True``, outcome ``ok``.
    * and had it survived that, the park's own ``rmtree(target, ignore_errors=True)``
      swallows a real permissions failure, after which ``shutil.move`` finds a DIRECTORY
      at the destination and moves the stage INSIDE it. Measured: the reinstall restored
      the stale ``old`` copy plus a nested ``notes-fixture.data.staged`` directory, with
      the user's current work buried inside it — and that one returned ``True`` too.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "old", "round one, never restored")
    assert app_manager.uninstall_keep_data(name) is True
    parked = app_manager._preserved_data_dir(name)

    # A real failed restore — the only route to "live app beside an unconsumed park".
    real_copytree = app_manager.shutil.copytree

    def _fail_restore(srcp, dstp, *a, **k):
        if Path(srcp) == parked:
            raise OSError("injected: restore failed")
        return real_copytree(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(app_manager.shutil, "copytree", _fail_restore)
        assert app_manager.install(src, confirm=True).ok
    unconsumed = _shape(parked)
    assert unconsumed[2] == {"old": "round one, never restored\n"}, unconsumed
    assert _notes(name) == {}, "the restore was supposed to fail"

    _write_note(name, "new", "round two")
    live_before = _shape(manager.app_dir(name) / "data")
    records = _capture_audit(monkeypatch)

    assert app_manager.uninstall_keep_data(name) is False

    assert (
        _shape(parked) == unconsumed
    ), f"the unconsumed park was destroyed or overwritten: {_shape(parked)} vs {unconsumed}"
    assert not (parked / f"{name}{app_manager._DATA_STAGE_SUFFIX}").exists(), (
        "the stage was moved INSIDE the older park — shutil.move's "
        "destination-is-a-directory case"
    )
    assert not _staged(name).exists(), "the refusal came after staging; it must come first"
    assert manager._read_installed(name) is not None, "the app was removed by a refusal"
    assert _shape(manager.app_dir(name) / "data") == live_before, "live data/ was touched"
    detail, _error = _keep_data_failure(records)
    assert "data=unconsumed_copy" in detail and str(parked) in detail, detail
    assert app_manager.describe_app_data(name)["unconsumed"] == [str(parked)]


def test_the_refusal_clears_by_the_route_it_names_rather_than_wedging_the_app(
    tmp_path, monkeypatch
):
    """Fail-closed has to leave a way forward, or it is just a different way to lose.

    Both leftovers are cleared here by the routes the refusal names, and the rung then
    succeeds — otherwise the fix would have traded silent loss for a permanently stuck app.

    Note which route is NOT available, because the first draft of the refusal advised it:
    "reinstall the app and it will restore the park" is false HERE. This rung only runs
    while the app is installed, and ``install`` refuses an installed app ("use update"), so
    from inside this refusal a reinstall is not reachable. The routes are: move the copy
    aside, remove it, or press force-uninstall, which discards a park on purpose. Both
    hand routes are driven below; the force-uninstall one is
    ``test_force_uninstall_drops_a_park_a_failed_restore_left_behind``.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "old", "round one")
    assert app_manager.uninstall_keep_data(name) is True
    parked = app_manager._preserved_data_dir(name)

    real_copytree = app_manager.shutil.copytree

    def _fail_restore(srcp, dstp, *a, **k):
        if Path(srcp) == parked:
            raise OSError("injected: restore failed")
        return real_copytree(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(app_manager.shutil, "copytree", _fail_restore)
        assert app_manager.install(src, confirm=True).ok
    assert app_manager.uninstall_keep_data(name) is False  # refused; park unconsumed
    _write_note(name, "current", "the live copy")

    # Route 1: the unconsumed PARK moved aside by hand. Both copies then survive, and the
    # rung proceeds — the park it writes is the live data, not the resurrected old one.
    kept_by_hand = parked.parent / "kept-by-hand"
    parked.rename(kept_by_hand)
    assert app_manager.describe_app_data(name)["unconsumed"] == []
    assert app_manager.uninstall_keep_data(name) is True, "the rung stayed wedged"
    assert _notes_at(parked / "notebook") == {"current": "the live copy\n"}
    assert _notes_at(kept_by_hand / "notebook") == {"old": "round one\n"}, "route 1 lost it"

    # Route 2: a survivor STAGE moved aside by hand — same predicate, other path.
    assert app_manager.install(src, confirm=True).ok
    assert _notes(name) == {"current": "the live copy\n"}, "the park did not come back"
    _write_note(name, "later", "round two")
    staged = _staged(name)
    real_rename = os.rename

    def _fail_park(srcp, dstp, *a, **k):
        if Path(srcp) == staged:
            raise OSError(errno.EIO, "injected")
        return real_rename(srcp, dstp, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(os, "rename", _fail_park)
        assert app_manager.uninstall_keep_data(name) is False
    assert app_manager.install(src, confirm=True).ok
    assert app_manager.uninstall_keep_data(name) is False, "the survivor is still there"
    aside = staged.parent / "recovered-by-hand"
    staged.rename(aside)
    assert app_manager.describe_app_data(name)["unconsumed"] == []
    assert app_manager.uninstall_keep_data(name) is True
    assert _notes_at(aside / "notebook") == {
        "current": "the live copy\n",
        "later": "round two\n",
    }, "moving the survivor aside lost it"


def test_describe_app_data_reports_no_unconsumed_copies_on_the_ordinary_path(tmp_path):
    """The confirm dialog must not cry wolf: the normal cycle reports an empty list.

    Paired with the two assertions above that it reports a NON-empty one, because a key
    that is always ``[]`` would satisfy those by being broken.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    assert app_manager.describe_app_data(name)["unconsumed"] == []
    _write_note(name, "one", "body")
    assert app_manager.describe_app_data(name)["unconsumed"] == []
    assert app_manager.uninstall_keep_data(name) is True
    # The app is gone, so the park is now the only copy — and it IS reported, because the
    # next keep-data uninstall would be refused on account of it.
    assert app_manager.describe_app_data(name)["unconsumed"] == [
        str(app_manager._preserved_data_dir(name))
    ]
    assert app_manager.install(src, confirm=True).ok  # consumes it
    assert app_manager.describe_app_data(name)["unconsumed"] == []


# ── every False names itself: the rung returns a bool, so the log IS the diagnosis ──

_APP_MANAGER_LOGGER = "personalclaw.apps.app_manager"


def _loud_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Messages this rung logged at WARNING or worse. The whole diagnosis surface."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.name == _APP_MANAGER_LOGGER
    ]


def _raise_enospc(*_a, **_k):
    raise OSError(errno.ENOSPC, "no space left on device")


def test_the_keep_data_rung_never_refuses_an_installed_app_in_silence(
    tmp_path, monkeypatch, caplog
):
    """Each refusal/error path of an INSTALLED app leaves a WARNING+ line naming the app.

    This rung answers with a bare ``bool``. So for a user reading ``gateway.log`` — and for
    CI reading a red — the log line is the ONLY place the reason exists: the return value
    cannot carry one, and the SEL audit is not on either surface. Two branches used to
    answer with nothing at all, and the one that fires on an unexplained filesystem fault
    (the preservation copy) was one of them. Measured: main's ``Full`` run 35248405420
    (``matrix-shard (3.13, macos-latest, 2)``) recorded a real failure of this rung as
    ``assert False is True`` with no captured log, no cause and nothing to act on — the
    branch had deleted its own evidence.

    Held as an INVARIANT over the branches rather than one assertion per branch, because
    the next branch added here is exactly the one that would be missed: a per-branch test
    passes while the new path stays mute. Each case drives the real code path (an injected
    failure at the real call site, or a genuinely unmintable on-disk name), and the pairing
    below is what keeps the invariant from being satisfiable by logging unconditionally.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "precious", "must not be lost")

    with caplog.at_level(logging.WARNING, logger=_APP_MANAGER_LOGGER):
        # 1. The preservation copy fails — the branch that produced the red. Injected at
        #    the real call site (`shutil.copytree`), the same lever
        #    `test_preservation_failure_removes_nothing` uses to prove nothing is removed.
        caplog.clear()
        with monkeypatch.context() as m:
            m.setattr(app_manager.shutil, "copytree", _raise_enospc)
            assert app_manager.uninstall_keep_data(name) is False
        said = _loud_records(caplog)
        assert said, (
            "the preservation copy failed and the rung logged NOTHING — a bare False was "
            "the user's and CI's entire record of it (main's Full run 35248405420)"
        )
        assert any(name in m for m in said), said
        assert any(f"errno={errno.ENOSPC}" in m for m in said), (
            "the log must carry the errno: ENOSPC ('free some disk and retry') and EACCES "
            f"are different next actions, and `data=preserve_failed` cannot tell them "
            f"apart — saw {said}"
        )

        # 2. An on-disk app whose dir name can never hold a parked copy. Not injected at
        #    all: `list_apps` walks real directory names, so this state is reachable, and
        #    this rung stays refused for that app forever — which it has to say out loud.
        weird = "Not Kebab"
        d = manager.apps_dir() / weird
        d.mkdir(parents=True)
        (d / "installed.json").write_text(
            json.dumps({"name": weird, "version": "1.0.0", "enabled": True}), encoding="utf-8"
        )
        caplog.clear()
        assert app_manager.uninstall_keep_data(weird) is False
        said = _loud_records(caplog)
        assert said and any(
            weird in m for m in said
        ), f"a refusal that leaves the app installed forever said nothing: {said}"

        # 3. THE PAIRING. The ordinary success path must stay SILENT, or every assertion
        #    above is satisfied by a rung that logs an error on every call — which is worse
        #    than the silence it replaced, because then no line means anything.
        caplog.clear()
        assert app_manager.uninstall_keep_data(name) is True
        assert _loud_records(caplog) == [], (
            "the success path logs at WARNING+, so the diagnosis lines above carry no "
            "information: every keep-data uninstall now reads as a failure"
        )


# ── a live data/ that changes UNDER the copy (#3324) ───────────────────────────


def _racing_copytree(lock: Path, *, forever: bool, calls: list[int]):
    """A real ``copytree`` whose ``copy_function`` makes *lock* vanish mid-walk.

    The instrumentation is one file's disappearance; everything else — the ``scandir``,
    the per-entry ``OSError`` capture, the ``shutil.Error`` aggregation — is the real
    machinery, so what these tests exercise is the production failure and not a
    hand-built exception that merely resembles it.

    Faithful to what was measured: ``git commit`` ends by spawning ``git maintenance run
    --auto --quiet --detach``, that DETACHED child outlives the commit the app waited
    for, and it holds ``.git/objects/maintenance.lock`` — listed by our walk, gone by the
    time the walk copies it. ``_content_files`` above documents the same writer from the
    other side, where it corrupts a file census.
    """
    real_copytree = app_manager.shutil.copytree
    real_copy2 = app_manager.shutil.copy2

    def _copy(fsrc, fdst, *ca, **ck):
        if Path(fsrc) == lock:
            Path(fsrc).unlink(missing_ok=True)  # the detached child finishing
        return real_copy2(fsrc, fdst, *ca, **ck)

    # `copytree` RECURSES through itself once per subdirectory, passing `copy_function`
    # positionally. So the wrapper mirrors the real signature (a `**kwargs` passthrough
    # re-supplies an argument the recursion already gave positionally), and `copy_function
    # is None` is what tells a top-level call from a recursive one — without it, `calls`
    # would count directories instead of attempts.
    def _copytree(src, dst, symlinks=False, ignore=None, copy_function=None, *rest, **k):
        if copy_function is None:
            calls.append(1)
            if forever:
                # A NEW lock before every walk: the tree never settles, so retries run out.
                lock.parent.mkdir(parents=True, exist_ok=True)
                lock.write_text("", encoding="utf-8")
        return real_copytree(src, dst, symlinks, ignore, _copy, *rest, **k)

    return _copytree


def test_a_lock_file_that_vanishes_mid_copy_does_not_refuse_the_keep_data_uninstall(
    tmp_path, monkeypatch
):
    """The measured red: a transient file under ``.git`` failed the WHOLE uninstall.

    Main's ``Full`` run 35764976454 failed two macOS legs here — ``assert False is True``,
    with ``shutil.Error`` on ``.git/objects/maintenance.lock`` as the cause. Nothing about
    it is test-only: any app whose ``data/`` holds a git checkout, an SQLite WAL or its own
    lockfile refuses the same way, for a reason the user did not cause and cannot act on.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "one", "body")
    lock = _notebook(name) / _GIT_DIRNAME / "objects" / "maintenance.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")

    calls: list[int] = []
    with monkeypatch.context() as m:
        m.setattr(
            app_manager.shutil, "copytree", _racing_copytree(lock, forever=False, calls=calls)
        )
        assert app_manager.uninstall_keep_data(name) is True, (
            "a lock file that vanished inside the walk refused the whole keep-data "
            "uninstall — the user's app stays installed over a file that was never theirs"
        )
    assert len(calls) == 2, f"the copy was not retried once and only once: {len(calls)} attempts"

    # PRESERVED, not merely "returned True": the notes and the git history are the point.
    parked = app_manager._preserved_data_dir(name)
    assert _notes_at(parked / "notebook") == {"one": "body\n"}, "the retry lost the notes"
    assert _git_log_at(parked / "notebook") == ["note: one"], "the retry lost the git history"
    assert not (
        parked / "notebook" / _GIT_DIRNAME / "objects" / "maintenance.lock"
    ).exists(), "the vanished lock was resurrected into the parked copy"


def test_a_copy_failure_whose_source_is_still_there_still_fails_closed(tmp_path, monkeypatch):
    """The GUARD on the fix. Retrying a settling tree must not soften a real fault.

    Paired with the test above deliberately: a ``_copy_live_tree`` that retried every
    ``shutil.Error`` would satisfy that one while quietly turning ENOSPC, EACCES and EIO
    into "we tried four times and gave up" — same refusal, four times the latency, and a
    predicate that no longer means anything. So this asserts the discrimination itself:
    the source is still on disk, therefore the tree did not settle under us, therefore it
    fails closed on the FIRST attempt with nothing removed.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "one", "body")

    real_copytree = app_manager.shutil.copytree
    real_copy2 = app_manager.shutil.copy2
    calls: list[int] = []
    victim = _notebook(name) / "one.md"

    def _copy(fsrc, fdst, *ca, **ck):
        if Path(fsrc) == victim:
            raise OSError(errno.EACCES, "permission denied")
        return real_copy2(fsrc, fdst, *ca, **ck)

    def _copytree(srcp, dstp, symlinks=False, ignore=None, copy_function=None, *rest, **k):
        if copy_function is None:  # top-level call, not `copytree`'s own recursion
            calls.append(1)
        return real_copytree(srcp, dstp, symlinks, ignore, _copy, *rest, **k)

    with monkeypatch.context() as m:
        m.setattr(app_manager.shutil, "copytree", _copytree)
        assert app_manager.uninstall_keep_data(name) is False
    assert len(calls) == 1, (
        "a fault whose source is STILL on disk was retried — the settle predicate is "
        f"matching every shutil.Error, not just a vanished source ({len(calls)} attempts)"
    )
    # Nothing removed: this rung's whole promise on a failed copy.
    assert _notes(name) == {"one": "body\n"}, "the app's live data/ was touched by a refusal"
    assert app_manager._read_installed(name) is not None, "the app was removed over a failed copy"


def test_a_tree_that_never_settles_fails_closed_and_names_the_file(tmp_path, monkeypatch, caplog):
    """Retries are BOUNDED, and running out is the old loud refusal, not a swallow.

    "Add a retry" is only an honest fix if exhausting it is indistinguishable from never
    having retried: same ``False``, same ``logger.error`` naming the paths, same audit.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "one", "body")
    lock = _notebook(name) / _GIT_DIRNAME / "objects" / "maintenance.lock"

    calls: list[int] = []
    slept: list[float] = []
    with monkeypatch.context() as m:
        m.setattr(app_manager.shutil, "copytree", _racing_copytree(lock, forever=True, calls=calls))
        m.setattr(app_manager.time, "sleep", slept.append)  # the backoff, not its wall clock
        with caplog.at_level(logging.WARNING, logger=_APP_MANAGER_LOGGER):
            caplog.clear()
            assert app_manager.uninstall_keep_data(name) is False

    assert (
        len(calls) == app_manager._LIVE_COPY_ATTEMPTS
    ), f"the copy did not run exactly {app_manager._LIVE_COPY_ATTEMPTS} attempts: {len(calls)}"
    assert slept == [0.25, 0.5, 1.0], f"the backoff did not double between attempts: {slept}"
    said = _loud_records(caplog)
    assert said and any(
        "maintenance.lock" in msg for msg in said
    ), f"exhausting the retries hid the fault instead of naming the file: {said}"
    assert _notes(name) == {"one": "body\n"}, "the app's live data/ was touched by a refusal"
    assert app_manager._read_installed(name) is not None


def test_an_update_preserves_a_live_data_tree_that_is_changing(tmp_path, monkeypatch):
    """The SIBLING call site, and the one with worse consequences.

    ``update`` copies a live app's ``data/`` into the staged tree and then SWAPS, so that
    copy becomes the surviving one. A ``shutil.Error`` there aborted the update over a
    transient lock; the same settle-and-retry covers it, and the notes have to come out the
    other side of the swap.
    """
    name = "notes-fixture"
    src = _bundle(tmp_path)
    assert app_manager.install(src, confirm=True).ok
    _write_note(name, "one", "body")
    lock = _notebook(name) / _GIT_DIRNAME / "objects" / "maintenance.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")

    newer = _bundle(tmp_path / "v2", name=name)
    (newer / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.1.0",
                "displayName": "Notes Fixture",
                "description": "A git-backed notebook fixture",
            }
        ),
        encoding="utf-8",
    )
    calls: list[int] = []
    with monkeypatch.context() as m:
        m.setattr(
            app_manager.shutil, "copytree", _racing_copytree(lock, forever=False, calls=calls)
        )
        result = app_manager.update(newer, name=name, confirm=True)
    assert result.ok, f"a vanished lock file aborted the update: {result.error}"
    assert _notes(name) == {"one": "body\n"}, "the update lost the user's notes"
    assert _git_log_at(_notebook(name)) == ["note: one"], "the update lost the git history"
