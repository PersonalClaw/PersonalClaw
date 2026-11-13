"""An action keeps its transcript on stdout, and says what went wrong on stderr.

``project export`` printed "⚠️ artifact metadata unavailable" among its transcript, ``backup
export`` its "could not export" lines, ``project import`` the whole plan before an import that then
refused, ``update`` its header and pin lines before a check that then refused. A script reading
stdout got a warning or a plan as if it were the result, and a refusal that left stdout non-empty.
Now a command that refuses before it starts prints nothing on stdout, a warning an action prints is
on stderr, and a value an action makes that a script needs comes from ``--json``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from personalclaw import cli


def _args(*argv: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(list(argv))


# ── cron add ──────────────────────────────────────────────────────────────────


def test_cron_add_json_is_the_row_cron_list_json_lists(capsys):
    from personalclaw.cli_commands import _cron

    _cron(_args("cron", "add", "Nightly report", "summarise", "--every", "3600", "--yes", "--json"))
    made = capsys.readouterr()
    assert "Created automation 'Nightly report' (clock:nightly-report)" in made.err
    _cron(_args("cron", "list", "--json"))
    listed = json.loads(capsys.readouterr().out)
    assert json.loads(made.out) in listed
    assert made.out == json.dumps(json.loads(made.out), indent=2) + "\n"


# ── backup export ─────────────────────────────────────────────────────────────


def test_backup_export_names_what_it_could_not_export_on_stderr(monkeypatch, tmp_path, capsys):
    from personalclaw.durability import shards

    monkeypatch.setattr(shards, "machine_id", lambda home: "m1")
    monkeypatch.setattr(shards, "clear_shards", lambda out_dir: None)
    monkeypatch.setattr(
        shards,
        "export_shards",
        lambda home, out_dir, entries=None: SimpleNamespace(
            entries=1,
            shards=["a"],
            rows=2,
            skipped={"knowledge": "no table"},
            left_out={"memory.db": "locked"},
        ),
    )
    code = shards.backup_cmd(_args("backup", "export", str(tmp_path / "out")))
    out, err = capsys.readouterr()
    assert code == 1
    assert "could not export memory.db: locked" in err and "could not export" not in out
    assert "skipped knowledge: no table" in err and "skipped" not in out
    assert "Exported 1 store(s)" in out


# ── project export ────────────────────────────────────────────────────────────


def test_project_export_says_a_store_it_could_not_read_on_stderr(monkeypatch, capsys):
    from personalclaw import cli_project
    from personalclaw.artifacts import registry

    def unreadable():
        raise RuntimeError("the artifact registry is down")

    monkeypatch.setattr(registry, "get_provider", unreadable)
    cli_project._gather("p1")
    out, err = capsys.readouterr()
    assert "artifact metadata unavailable" in err
    assert "unavailable" not in out


# ── project import ────────────────────────────────────────────────────────────


@pytest.fixture
def an_archive(monkeypatch, tmp_path):
    """An archive whose plan is *plan*, read from a home with no links in the way."""
    from personalclaw import cli_project
    from personalclaw.tasks import hierarchy

    archive = tmp_path / "a.pcproj"
    archive.write_bytes(b"x")
    monkeypatch.setattr(cli_project.pa, "projects_folder", lambda home: None)
    monkeypatch.setattr(
        hierarchy, "HierarchyStore", lambda: SimpleNamespace(list_projects=lambda: [])
    )

    def with_plan(plan):
        monkeypatch.setattr(cli_project.pa, "read_archive_plan", lambda *a, **kw: (plan, None))
        return archive

    return with_plan


def _plan(**fields):
    base = dict(
        project_name="Garden", accepted=["a", "b"], refused=[], secrets_expected=[], ok=True
    )
    return SimpleNamespace(**{**base, **fields})


def test_project_import_with_nothing_importable_prints_nothing_on_stdout(an_archive, capsys):
    from personalclaw import cli_project

    plan = _plan(
        accepted=[], refused=[SimpleNamespace(path="x.json", message="not a project")], ok=False
    )
    archive = an_archive(plan)
    code = cli_project._import(
        argparse.Namespace(archive=str(archive), passphrase="", dry_run=False)
    )
    out, err = capsys.readouterr()
    assert (code, out) == (1, "")
    assert "x.json: not a project" in err and "Nothing importable in this archive." in err


def test_project_import_says_what_it_wrote_once_it_has(an_archive, monkeypatch, capsys):
    from personalclaw import cli_project
    from personalclaw.workflows.project_export import import_summary

    plan = _plan()
    archive = an_archive(plan)
    monkeypatch.setattr(
        cli_project.pa,
        "import_project",
        lambda read, extracted, store, home: (
            SimpleNamespace(name="Garden", id="p9"),
            ["a", "b"],
            ["the link at x"],
        ),
    )
    code = cli_project._import(
        argparse.Namespace(archive=str(archive), passphrase="", dry_run=False)
    )
    out, err = capsys.readouterr()
    assert code == 0
    assert out.startswith(import_summary(plan, written=2, left=["the link at x"]) + "\n"), out
    assert "would be imported" not in out
    assert "Left unchanged: the link at x" in err


# ── restore ───────────────────────────────────────────────────────────────────


@pytest.fixture
def a_snapshot(tmp_path, capsys):
    """A snapshot of this test's home, made by the command itself."""
    from personalclaw.config import config_dir
    from personalclaw.snapshot import snapshot_main

    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "crons.json").write_text("[]")
    folder = tmp_path / "snaps"
    assert snapshot_main(parsed=_args("snapshot", str(folder))) == 0
    capsys.readouterr()
    (made,) = folder.glob("personalclaw-snapshot-*.tar.gz")
    return made


def test_a_refused_replace_restore_prints_nothing_on_stdout(a_snapshot, monkeypatch, capsys):
    from personalclaw import snapshot
    from personalclaw.gateway_base import LiveGateway

    monkeypatch.setattr(snapshot, "_running_gateway", lambda: LiveGateway(port=10123, pid=4242))
    code = snapshot.restore_main(parsed=_args("restore", str(a_snapshot), "--mode", "replace"))
    out, err = capsys.readouterr()
    assert (code, out) == (1, "")
    assert "pid 4242, port 10123" in err


def test_a_failed_integrity_check_is_said_on_stderr_only(a_snapshot, monkeypatch, capsys):
    from personalclaw import snapshot

    monkeypatch.setattr(snapshot, "_running_gateway", lambda: None)

    class _Broken:
        def execute(self, sql):
            return SimpleNamespace(fetchone=lambda: ("*** database disk image is malformed",))

        def close(self):
            return None

    real_connect = snapshot.sqlite3.connect

    def connect(path, *args, **kwargs):
        if str(path).endswith("memory.db"):
            return _Broken()
        return real_connect(path, *args, **kwargs)

    from personalclaw.config import config_dir

    (config_dir() / "memory.db").write_bytes(b"")
    monkeypatch.setattr(snapshot.sqlite3, "connect", connect)
    monkeypatch.setattr(snapshot, "_do_replace", lambda snap, pc, components: {})
    code = snapshot.restore_main(parsed=_args("restore", str(a_snapshot), "--mode", "replace"))
    out, err = capsys.readouterr()
    assert code == 1
    assert "memory.db integrity check failed" in err and "integrity check failed" not in out


def test_the_held_automations_question_is_asked_on_stderr(monkeypatch, capsys):
    from personalclaw import snapshot
    from personalclaw.triggers import restore_hold

    monkeypatch.setattr(snapshot.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *prompt: (_ for _ in ()).throw(EOFError()))
    snapshot._offer_to_resume(
        Path("/nonexistent-home"),
        {"automations_held": ["clock:a"], "held_from": restore_hold.THIS_HOME},
    )
    out, err = capsys.readouterr()
    assert restore_hold.resume_question(1) in err
    assert restore_hold.resume_question(1) not in out


# ── the words an action says ──────────────────────────────────────────────────


def test_auth_disable_names_only_the_command_that_changes_the_credential(capsys):
    """It named a `--clear` no credential command has."""
    from personalclaw.auth.cli import auth_cmd

    assert auth_cmd(_args("auth", "disable")) == 0
    out = capsys.readouterr().out
    assert "Use `personalclaw auth set-password` to change it." in out
    assert "--clear" not in out


def test_cron_pause_refused_for_a_job_that_is_there_says_why(monkeypatch, capsys):
    """It said "Job not found" for a job that is there and was not paused."""
    from personalclaw.cli_commands import _cron
    from personalclaw.config import config_dir
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=config_dir())
    store.upsert(
        Trigger(
            id="clock:ops",
            name="Ops",
            kind="clock",
            created_by="user",
            spec={"kind": "cron", "expr": "0 9 * * *"},
            workflow={"inline": {"provider": "notify", "config": {"title_template": "x"}}},
        )
    )
    kept = store.get("clock:ops").trigger
    monkeypatch.setattr(TriggerStore, "set_enabled", lambda self, tid, enabled: kept)
    with pytest.raises(SystemExit) as exited:
        _cron(_args("cron", "pause", "clock:ops"))
    assert exited.value.code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "clock:ops was not paused: the app that keeps it did not keep the change" in err
    assert "Job not found" not in err


def test_learn_add_echoes_no_category_it_did_not_store(capsys):
    from personalclaw.cli_commands import _learn

    _learn(_args("learn", "add", "Prefer short replies", "--category", "preference"))
    assert capsys.readouterr().out == "Saved: Prefer short replies\n"


def test_doctor_tells_a_phone_how_to_sign_in_at_the_tailnet_address(monkeypatch, capsys):
    """It said to run `personalclaw token` for the link, whose 127.0.0.1 address a phone cannot
    open."""
    from personalclaw import cli_doctor

    monkeypatch.setattr(cli_doctor, "tailnet_ip", lambda: "100.101.102.103")
    monkeypatch.setattr(cli_doctor, "auth_is_off", lambda: False)
    assert cli_doctor._doctor_remote(local=True, port=10000) == []
    out = capsys.readouterr().out
    assert "open http://100.101.102.103:10000 on your phone" in out
    assert "paste the `personalclaw token` link into its token box" in out
    assert "`personalclaw token --remote` once dashboard.url names that address" in out
