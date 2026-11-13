"""A refusal goes to stderr, so stdout carries only a command's real output.

`$(personalclaw token)` with no gateway captured "❌ Gateway not running — start it with:
personalclaw gateway" as if it were the sign-in link: the refusal printed on stdout and only the
exit status said otherwise. `logout`, `stop`, `update`, `spawn`, the eval family (`judge-bench`,
`eval-harvest`, `study`, `ablation`, `eval-gate`, `retrieval-eval`), `auth`, `inbound token`,
`project`, `app new`, `snapshot`, `restore`, `backup validate`, `capture import` and `push test`
printed theirs the same way. Every one is on stderr now.

The rail walks the package for the shape of a refusal: a stdout print directly before a non-zero
exit. Where stdout IS the answer and the status says pass or fail (a verdict: `doctor`,
`security verify`, `inbound token show`, the app quality check), or stdout is a protocol another
program parses (the eval child, the optimize action), the function is named below, with why.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest
from a_cli_refusal_prints_on_stderr_shared import _refusals_on_stdout


def test_the_detector_finds_a_refusal_and_nothing_else():
    """Calibrated both ways, or the rail below passes over a walk that detects nothing."""
    source = textwrap.dedent("""
        import sys

        def refuses():
            print("Error: gateway not running")
            sys.exit(1)

        def refuses_with_a_hint():
            print("❌ Not a usable version")
            print("   Give a release version")
            return 1

        def refuses_on_stderr():
            print("Error: gateway not running", file=sys.stderr)
            sys.exit(1)

        def succeeds():
            print("done")
            sys.exit(0)

        def reports_then_works():
            print("progress")
            do_the_work()
            return 1

        def refuses_after_its_preflight():  # `study --run`
            print("Study s1 (A/B) on t1")
            if pre.refusal:
                raise SystemExit(1)

        def reports_then_refuses_on_stderr():  # `push test`
            print("backend=fake delivered=0")
            if not sent:
                print("Nothing was delivered", file=sys.stderr)
                return 1
            return 0

        def reports_either_way_then_refuses():  # `automation verify-migration`
            if as_json:
                print("{}")
            else:
                print("the report")
            if not ok:
                sys.exit(1)

        def reports_each_check_then_refuses():  # `doctor --crypto`
            for label in labels:
                print(f"{label} ok")
            if failed:
                raise SystemExit(1)

        def fails_by_its_status():
            print("Exported 3 stores")
            return 1 if left else 0

        def returns_before_the_refusal():
            if disable:
                print("turned off")
                return 0
            if not credentials:
                print("Set a password first", file=sys.stderr)
                return 1

        def succeeds_then_returns_zero():
            print("done")
            return 0
        """)
    found = {(s.function, s.text) for s in _refusals_on_stdout(source, "fixture.py")}
    assert found == {
        ("refuses", "print('Error: gateway not running')"),
        ("refuses_with_a_hint", "print('❌ Not a usable version')"),
        ("refuses_with_a_hint", "print('   Give a release version')"),
        ("refuses_after_its_preflight", "print('Study s1 (A/B) on t1')"),
        ("reports_then_refuses_on_stderr", "print('backend=fake delivered=0')"),
        ("reports_either_way_then_refuses", "print('{}')"),
        ("reports_either_way_then_refuses", "print('the report')"),
        ("reports_each_check_then_refuses", "print(f'{label} ok')"),
        ("fails_by_its_status", "print('Exported 3 stores')"),
    }


@pytest.mark.parametrize(
    "argv, said",
    [
        (["token"], "No gateway is running for this home"),
        (["token", "--bearer"], "No gateway is running for this home"),
        (["token", "--json"], "No gateway is running for this home"),
        (["token", "--remote"], "dashboard.url is not set"),
    ],
)
def test_token_refused_puts_nothing_on_stdout(tmp_path, argv, said):
    """`$(personalclaw token)` is a URL or nothing, in every form: the refusal is on stderr."""
    home = tmp_path / "user"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("PERSONALCLAW_")}
    env.update(
        PERSONALCLAW_HOME=str(tmp_path / "pc"),
        PERSONALCLAW_CREDENTIAL_BACKEND="dotenv",
        HOME=str(home),
        PYTHONIOENCODING="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, "-m", "personalclaw", *argv],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )

    assert proc.returncode == 1, proc
    assert proc.stdout == ""
    assert said in proc.stderr, proc.stderr
    if argv != ["token", "--remote"]:
        assert "Start it with: personalclaw gateway" in proc.stderr, proc.stderr


def test_logout_with_no_gateway_puts_nothing_on_stdout(tmp_path, monkeypatch, capsys, unset_env):
    from personalclaw import cli_server

    unset_env("PERSONALCLAW_PORT")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    with pytest.raises(SystemExit) as exited:
        cli_server._logout(None)

    assert exited.value.code == 1
    out, err = capsys.readouterr()
    assert out == "" and "No gateway is running for this home" in err


def test_a_refused_restore_names_the_components_on_stderr(tmp_path, monkeypatch, capsys):
    """The list after "Unknown component" belongs to the refusal, so it is on stderr with it;
    `--list-components` asked for it, so there it is the output, on stdout."""
    from personalclaw import snapshot

    archive = tmp_path / "snap.tar.gz"
    archive.write_bytes(b"")
    monkeypatch.setattr(snapshot, "_running_gateway", lambda: None)

    assert snapshot.restore_main([str(archive), "--components", "bogus"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "Unknown component: bogus" in err and "Available components:" in err

    assert snapshot.restore_main(["--list-components"]) == 0
    out, err = capsys.readouterr()
    assert "Available components:" in out and err == ""


def test_a_service_status_report_is_on_stdout_and_its_status_says_inactive(monkeypatch, capsys):
    """A verdict report the walk cannot see the exit of: its report is stdout, and 1 = inactive."""
    from personalclaw.service import controller

    monkeypatch.setattr(controller, "current_platform", lambda: controller.Platform.LAUNCHD)
    monkeypatch.setattr(controller.macos, "status", lambda: "io.personalclaw.gateway: not loaded")
    monkeypatch.setattr(controller.macos, "installed_environment", lambda: {})
    monkeypatch.setattr(controller.macos, "is_active", lambda: False)
    assert controller.service_status() == 1
    out, err = capsys.readouterr()
    assert "io.personalclaw.gateway: not loaded" in out and err == ""


def test_a_capture_import_that_staged_nothing_reports_on_stdout_and_exits_1(tmp_path, capsys):
    """The other: imported 0 from a file it could read is the verdict, on stdout, with exit 1."""
    from personalclaw.cli import build_parser
    from personalclaw.inbound.capture_import import capture_cmd

    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json\n", encoding="utf-8")
    args = build_parser().parse_args(["capture", "import", str(bad), "--format", "jsonl"])
    assert capture_cmd(args, stage=lambda *a, **k: {}) == 1
    out, err = capsys.readouterr()
    assert out.startswith("imported 0, skipped") and err == ""
