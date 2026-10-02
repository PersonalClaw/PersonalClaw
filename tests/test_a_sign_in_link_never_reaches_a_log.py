"""A sign-in link is shown to a person at a terminal or handed to a browser, never kept in a log.

A gateway started by a service manager writes its stdout to a file (launchd's
``~/Library/Logs/PersonalClaw/gateway.log``), to the journal (systemd), or, started by
``personalclaw restart`` with no service, to ``gateway-restart.log`` in the home. Nobody reads
those as the gateway prints: they are kept, and read later. The gateway printed its startup
sign-in link there, a live owner session lasting 30 days, and launchd's log and its folder were
created readable by every user of the machine.

The behaviour the code must have:

* Started at a terminal, the gateway prints the sign-in link, as it always has.
* Started anywhere else, it prints the dashboard's address with no credential in it, and says
  how to get a sign-in link. A link it opens in the default browser goes to the browser only.
  With no browser to open and no terminal to show it, it makes no link at all.
* The files a service manager or a detached restart write the gateway's output to are the
  owner's alone (0600, in a 0700 folder), and a link an older gateway left in one is taken out
  before the next gateway writes there.

Driven through the real start path (``GatewayOrchestrator.run``), the real service install and
the real detached restart, with launchctl, the browser and the spawned process faked. No value
here is or was ever a credential.
"""

from __future__ import annotations

import asyncio
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from personalclaw import cli_server, gateway
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard import session_store
from personalclaw.gateway import GatewayOrchestrator

#: What an older gateway printed into its log: the startup banner, its sign-in link included.
_OLD_LINK_VALUE = "eyJzdWIiOiJsb2NhbC1zdGFydHVwIn0.c3ludGhldGljLXBsYWNlaG9sZGVy"
_OLD_LOG = (
    "Checking for updates…\n"
    "Dashboard:\n"
    f"   http://personalclaw.localhost:19703?token={_OLD_LINK_VALUE}\n"
    f"Open PersonalClaw: http://personalclaw.localhost:19703?token={_OLD_LINK_VALUE}\n"
    "PersonalClaw gateway starting…\n"
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ── the start path ──────────────────────────────────────────────────────────────────────────


def _start_gateway(*, no_open: bool, opened: list[str] | None = None) -> None:
    """Run one gateway start up to its dashboard banner (what it prints, the test captures).

    Only what reaches outside the test is faked: the services' boot steps, the update check,
    the MCP probe and the browser. The banner, the link and the session store are real.
    """
    cfg = AppConfig()
    with patch.object(cfg, "load_credentials", return_value={}):
        orch = GatewayOrchestrator(cfg, no_open=no_open)
    orch._init_services = MagicMock()
    orch.vector_memory = MagicMock()
    orch._init_cron = AsyncMock()
    orch._init_heartbeat = AsyncMock()
    orch._init_inbox = AsyncMock()
    orch._init_mcp_discovery = MagicMock()
    orch._init_subagents = MagicMock()
    orch._init_autonudge = AsyncMock()
    orch._check_for_updates = AsyncMock()
    orch._shutdown = AsyncMock()
    orch.sessions = MagicMock()
    orch.sessions.start_pool = AsyncMock()

    async def _init_dashboard() -> None:
        orch._local_only = True
        orch._configured_host = None
        orch._dashboard_port = 19703

    orch._init_dashboard = _init_dashboard

    def _browser(url: str) -> bool:
        if opened is not None:
            opened.append(url)
        return True

    async def _drive() -> None:
        stopped = asyncio.Event()
        stopped.set()
        with (
            patch(
                "personalclaw.embedding_providers.registry.get_active_embed_fn", return_value=None
            ),
            patch("personalclaw.shutdown_event", stopped),
            patch("personalclaw.gateway.shutdown_event", stopped),
            patch("personalclaw.gateway.resolve_dashboard_host", return_value="127.0.0.1"),
            patch("personalclaw.gateway.browser_available", return_value=True),
            patch("personalclaw.gateway._is_wsl", return_value=False),
            patch("webbrowser.open", _browser),
            patch("personalclaw.session.cleanup_orphaned_sessions"),
            patch("personalclaw.dashboard.handlers._bg_mcp_probe", new_callable=AsyncMock),
            patch("os._exit"),
            patch("resource.getrlimit", return_value=(256, 10240)),
            patch("resource.setrlimit"),
        ):
            await orch.run()
            for _ in range(5):  # let the banner task finish
                await asyncio.sleep(0)

    asyncio.run(_drive())


def _startup_links() -> dict[str, session_store.SessionRecord]:
    return {
        nonce: record
        for nonce, record in session_store.load_session_records().items()
        if record.issuer == session_store.ISSUER_STARTUP
    }


@pytest.fixture
def at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A person reads the gateway's stdout at a terminal, as it prints."""
    monkeypatch.setattr(gateway, "_shown_at_a_terminal", lambda: True, raising=False)


def test_a_gateway_whose_stdout_is_not_a_terminal_prints_no_sign_in_link(capsys) -> None:
    """🔑 The service's case: stdout is a file. The banner gives the address and the way in."""
    _start_gateway(no_open=True)
    out = capsys.readouterr().out
    assert "Dashboard:" in out
    assert "http://127.0.0.1:19703" in out
    assert "token=" not in out, out
    assert "personalclaw token" in out, "it says how to get a sign-in link"


def test_in_a_container_the_banner_names_the_hosts_command(monkeypatch, capsys) -> None:
    """A container's stdout is the runtime's log: the way in is the host's exec command."""
    monkeypatch.setenv("PERSONALCLAW_INSTALL_KIND", "container")
    monkeypatch.delenv("PERSONALCLAW_CONTAINER_STARTED_BY", raising=False)
    _start_gateway(no_open=True)
    out = capsys.readouterr().out
    assert "token=" not in out, out
    assert "run `docker exec personalclaw personalclaw token` on the host" in out, out


def test_with_no_terminal_and_no_browser_no_link_is_made(capsys) -> None:
    """A link nobody can be shown is a credential with no holder: none is minted."""
    _start_gateway(no_open=True)
    capsys.readouterr()
    assert _startup_links() == {}


def test_the_link_opened_in_the_default_browser_goes_to_the_browser_only(capsys) -> None:
    """Opening the dashboard at start is kept. The link reaches the browser, not the log."""
    opened: list[str] = []
    _start_gateway(no_open=False, opened=opened)
    out = capsys.readouterr().out
    assert len(opened) == 1 and "?token=" in opened[0], opened
    assert "token=" not in out, out
    assert len(_startup_links()) == 1


def test_at_a_terminal_the_sign_in_link_is_printed_as_before(at_a_terminal, capsys) -> None:
    """The control arm: a foreground start in a terminal shows the link it always showed."""
    _start_gateway(no_open=True)
    out = capsys.readouterr().out
    assert "http://127.0.0.1:19703?token=" in out, out
    assert len(_startup_links()) == 1


def test_the_browser_opener_does_not_print_the_link_it_opens(monkeypatch, capsys) -> None:
    """The banner is the one place the address is printed; the opener only hands it on."""
    monkeypatch.setattr(gateway, "_is_wsl", lambda: False)
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    gateway._open_dashboard("http://127.0.0.1:19703?token=synthetic")
    assert "synthetic" not in capsys.readouterr().out


# ── the files a service manager writes the gateway's output to ────────────────────────────


@pytest.fixture
def launchd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The LaunchAgent paths in the test's own Library, and a launchctl that succeeds."""
    from personalclaw.service import macos as svc_macos

    library = tmp_path / "Library"
    monkeypatch.setattr(svc_macos, "PLIST_DIR", library / "LaunchAgents")
    monkeypatch.setattr(svc_macos, "PLIST_PATH", library / "LaunchAgents" / "agent.plist")
    monkeypatch.setattr(svc_macos, "LOG_DIR", library / "Logs" / "PersonalClaw")
    monkeypatch.setattr(svc_macos, "STDOUT_LOG", library / "Logs" / "PersonalClaw" / "gateway.log")
    monkeypatch.setattr(svc_macos, "STDERR_LOG", library / "Logs" / "PersonalClaw" / "gateway.err")
    ok = MagicMock(returncode=0, stdout="", stderr="")
    with (
        patch("personalclaw.service.common.shutil.which", return_value="/u/bin/personalclaw"),
        patch("personalclaw.service.macos.subprocess.run", return_value=ok),
    ):
        yield library


def test_service_install_makes_its_logs_the_owners_alone(launchd: Path) -> None:
    from personalclaw.service import macos as svc_macos

    svc_macos.install()

    assert _mode(svc_macos.LOG_DIR) == 0o700
    for log in (svc_macos.STDOUT_LOG, svc_macos.STDERR_LOG):
        assert log.is_file(), f"{log.name} is made before launchd opens it"
        assert _mode(log) == 0o600, f"{log.name} is {oct(_mode(log))}"


def test_service_install_takes_an_older_gateways_link_out_of_its_log(launchd: Path) -> None:
    """A log an older gateway wrote, readable by anyone, with its link in it: the link goes,
    every other line stays, and the folder and file become the owner's."""
    from personalclaw.service import macos as svc_macos

    svc_macos.LOG_DIR.mkdir(parents=True, mode=0o755)
    svc_macos.LOG_DIR.chmod(0o755)
    svc_macos.STDOUT_LOG.write_text(_OLD_LOG, encoding="utf-8")
    svc_macos.STDOUT_LOG.chmod(0o644)

    svc_macos.install()

    kept = svc_macos.STDOUT_LOG.read_text(encoding="utf-8")
    assert _OLD_LINK_VALUE not in kept, kept
    assert "Checking for updates…\nDashboard:\n" in kept and "gateway starting…" in kept
    assert _mode(svc_macos.STDOUT_LOG) == 0o600
    assert _mode(svc_macos.LOG_DIR) == 0o700


def test_a_detached_restarts_log_is_the_owners_alone_with_no_old_link(tmp_path: Path) -> None:
    """``personalclaw restart`` with no service writes the new gateway's output to the home's
    ``gateway-restart.log``: created 0600, and a link an older gateway left in it taken out."""
    log = cli_server.config_dir() / "gateway-restart.log"
    log.write_text(_OLD_LOG, encoding="utf-8")
    log.chmod(0o644)

    with patch.object(cli_server.subprocess, "Popen") as popen:
        cli_server._spawn_detached_gateway(19703)

    popen.assert_called_once()
    assert _mode(log) == 0o600
    kept = log.read_text(encoding="utf-8")
    assert _OLD_LINK_VALUE not in kept and "Dashboard:" in kept, kept


def test_a_first_detached_restart_makes_its_log_private(tmp_path: Path) -> None:
    log = cli_server.config_dir() / "gateway-restart.log"
    assert not log.exists()
    with patch.object(cli_server.subprocess, "Popen"):
        cli_server._spawn_detached_gateway(19703)
    assert _mode(log) == 0o600
