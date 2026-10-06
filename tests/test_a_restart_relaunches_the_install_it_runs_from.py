"""A restart starts the gateway again with a command the install it runs from accepts.

The dashboard's Restart, an applied update and a staged auto-update all end in one re-launch
(``restart_request.request_restart``). It was ``<sys.executable> -m personalclaw <the gateway's
arguments>`` for every install. In the desktop app ``sys.executable`` is the frozen bundle's own
executable, whose entry script is the CLI, and that parser refuses ``-m``: the re-launched
gateway exited at once with a usage error, and the app had no gateway until it was quit and
opened again.

The re-launch is now this install's own CLI (``self_update.cli_argv``) with the gateway's own
arguments, and ``--no-open`` when they do not say it already, since a restart opens no browser:
in the bundle, exactly the command line the desktop shell started it with. These drive it for
each install kind (the frozen bundle, a checkout, a wheel and a tool install) through the
request every restart makes and the stop that starts the new image, and parse what a frozen
restart starts with the real CLI parser. A restart whose program cannot be run is refused before
anything stops, in words that are true for the install; one that still cannot start its image
says why and exits non-zero, so whatever started the gateway knows it did not come back.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import patch

import pytest

from personalclaw import restart_request, self_update, shutdown_event
from personalclaw.cli import build_parser
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.handlers import updates
from personalclaw.gateway import GatewayOrchestrator

#: The command line the desktop shell starts the bundled gateway with (``desktop/main.js``).
SHELL_ARGS = ("gateway", "--port", "auto", "--json-ready", "--no-open")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """No restart request and no shutdown signal leaking in or out: both are process-wide."""
    monkeypatch.setattr(restart_request, "_pending", None)
    shutdown_event.clear()
    yield
    shutdown_event.clear()


def _program(path: Path) -> str:
    """An executable file at *path*, standing in for a real program."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def frozen_bundle(monkeypatch, tmp_path):
    """This process is the desktop app's gateway: the frozen bundle, started by the shell."""
    bundle = _program(
        tmp_path / "PersonalClaw.app/Contents/Resources/backend-dist/personalclaw-backend"
        "/personalclaw-backend"
    )
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys._MEIPASS", str(Path(bundle).parent / "_internal"), raising=False)
    monkeypatch.setattr("sys.executable", bundle)
    monkeypatch.setattr("sys.argv", [bundle, *SHELL_ARGS])
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "desktop")
    return bundle


def _relaunched() -> tuple[str, ...]:
    """The command the restart every surface asks for would start."""
    request = restart_request.request_restart()
    assert request.executable == request.argv[0]
    return request.argv


class TestTheRelaunchForEachInstallKind:
    def test_the_frozen_bundle_relaunches_with_the_shells_own_command_line(self, frozen_bundle):
        """🔴 The regression: it was ``<bundle> -m personalclaw gateway …``, which the bundle's
        parser refuses, so the re-launched gateway exited at once."""
        assert self_update.detect_install_kind() == "desktop"
        assert _relaunched() == (frozen_bundle, *SHELL_ARGS)

    def test_a_checkout_relaunches_its_interpreter_with_dash_m(
        self, monkeypatch, tmp_path, package_in_checkout
    ):
        root = package_in_checkout(tmp_path / "checkout")
        python = _program(root / ".venv" / "bin" / "python")
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
        monkeypatch.setattr("sys.executable", python)
        monkeypatch.setattr("sys.argv", [str(root / ".venv/bin/personalclaw"), "gateway"])
        assert self_update.detect_install_kind() == "git"
        assert _relaunched() == (python, "-m", "personalclaw", "gateway", "--no-open")

    def test_a_wheel_install_relaunches_its_interpreter_with_dash_m(
        self, monkeypatch, tmp_path, environment_made_by
    ):
        environment_made_by("pip")
        python = _program(tmp_path / "venv" / "bin" / "python")
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
        monkeypatch.setattr("sys.executable", python)
        monkeypatch.setattr(
            "sys.argv", [str(tmp_path / "venv/bin/personalclaw"), "gateway", "--port", "10000"]
        )
        assert self_update.detect_install_kind() == "pip"
        assert _relaunched() == (
            python,
            "-m",
            "personalclaw",
            "gateway",
            "--port",
            "10000",
            "--no-open",
        )

    def test_a_tool_install_relaunches_its_tool_interpreter_not_the_script_on_path(
        self, monkeypatch, tmp_path, environment_made_by
    ):
        """``uv tool`` (or ``pipx``): the console script on PATH and the interpreter that runs it
        live in different places, and the interpreter is the one that is this install."""
        environment_made_by("uv")
        python = _program(tmp_path / ".local/share/uv/tools/personalclaw/bin/python")
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
        monkeypatch.setattr("sys.executable", python)
        monkeypatch.setattr("sys.argv", [str(tmp_path / ".local/bin/personalclaw"), "gateway"])
        assert self_update.detect_install_kind() == "pip"
        assert _relaunched() == (python, "-m", "personalclaw", "gateway", "--no-open")


def _orchestrator() -> GatewayOrchestrator:
    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        return GatewayOrchestrator(cfg)


class _NewImage(Exception):
    """Raised where ``os.execve`` would have replaced this process."""


@pytest.mark.asyncio
async def test_a_frozen_restart_starts_a_command_the_frozen_cli_accepts(frozen_bundle):
    """The Restart the dashboard asks for (``_graceful_reexec``, what ``POST /api/system/restart``
    runs), then the gateway's own stop, which starts the new image: what it starts is parsed by
    the CLI parser the bundle runs, and it is the shell's gateway again."""
    started: list[tuple[str, list[str]]] = []

    def _execve(path, argv, env):  # noqa: ANN001
        started.append((path, list(argv)))
        raise _NewImage

    pushed: list[tuple[str, str]] = []
    state = types.SimpleNamespace(
        push_update_progress=lambda step, detail: pushed.append((step, detail))
    )
    await updates._graceful_reexec(state, auth_mode="local_token")  # type: ignore[arg-type]
    assert shutdown_event.is_set(), f"the restart was not asked for: {pushed}"
    with (
        patch("os.execve", _execve),
        patch("os._exit", side_effect=AssertionError("a restart must not exit")),
        patch("personalclaw.session.cleanup_orphaned_sessions"),
        pytest.raises(_NewImage),
    ):
        await _orchestrator()._finish()

    path, argv = started[0]
    assert path == frozen_bundle
    assert argv[0] == frozen_bundle
    args = build_parser().parse_args(argv[1:])  # a usage error raises SystemExit(2) here
    assert args.command == "gateway"
    assert (args.port, args.json_ready, args.no_open) == ("auto", True, True)


class TestARestartThatCannotStart:
    @pytest.mark.asyncio
    async def test_a_program_that_cannot_be_run_is_refused_before_the_gateway_stops(
        self, frozen_bundle, monkeypatch
    ):
        """The bundle's executable can no longer be run (measured on a built bundle with its mode
        changed): the restart would stop the gateway for good, so it is refused while the gateway
        still serves, and the owner reads why and what to do. It used to say "invalid Python
        executable path", which is not true of the app."""
        Path(frozen_bundle).chmod(0o644)
        pushed: list[tuple[str, str]] = []
        state = types.SimpleNamespace(
            push_update_progress=lambda step, detail: pushed.append((step, detail))
        )
        await updates._graceful_reexec(state)  # type: ignore[arg-type]
        assert not shutdown_event.is_set(), "the gateway was asked to stop with no way back"
        assert restart_request.pending() is None
        assert pushed == [
            (
                "error",
                f"PersonalClaw can't restart: {frozen_bundle} is missing or can't be run, so it "
                "keeps running as it is. Quit PersonalClaw and open it again to restart it.",
            )
        ]

    def test_outside_the_app_it_says_to_stop_and_start_it(self, monkeypatch, tmp_path):
        monkeypatch.delenv("PERSONALCLAW_INSTALL_KIND", raising=False)
        monkeypatch.setattr("sys.executable", str(tmp_path / "gone" / "python"))
        with pytest.raises(restart_request.RestartUnavailable) as refused:
            restart_request.request_restart()
        assert str(refused.value).endswith("Stop PersonalClaw and start it again to restart it.")
        assert not shutdown_event.is_set()

    def test_an_image_that_cannot_start_says_why_and_exits_non_zero(self, capsys):
        """Past the check, the gateway has already stopped: it cannot carry on, so it says why
        where whatever started it reads (the desktop shell shows this line to the owner), and it
        never exits 0, which would read as a stop someone asked for."""
        request = restart_request.RestartRequest(
            "/Applications/PersonalClaw.app/backend",
            ("/Applications/PersonalClaw.app/backend",),
            {},
        )

        class _Exited(Exception):
            pass

        def _exit(code: int) -> None:
            raise _Exited(code)

        with (
            patch("os.execve", side_effect=FileNotFoundError(2, "No such file or directory")),
            patch("os._exit", _exit),
            pytest.raises(_Exited) as exited,
        ):
            restart_request.start(request)
        assert exited.value.args == (restart_request.RESTART_FAILED,)
        assert restart_request.RESTART_FAILED != 0
        assert capsys.readouterr().err.strip() == (
            "PersonalClaw stopped to restart and could not start again "
            "(/Applications/PersonalClaw.app/backend: No such file or directory). Start it again."
        )
