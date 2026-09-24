"""EI-8 §6.2 — the localhost web preview for a run's dev server.

The claims here are measured against REAL listening sockets, not against a mocked scanner:
the whole feature is "did we correctly attribute a port to this run's workspace", and a fake
``lsof`` would only prove the parser agrees with the fixture that fed it. The two ``-F``
parsers are ALSO tested from fixture strings, because the ``ss`` tier cannot be driven on a
Darwin box and an unverified parser is where this kind of code rots.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from personalclaw.workflows import web_preview as wp


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def server(tmp_path: Path):
    """A real dev server listening on loopback with its cwd inside ``tmp_path/run``.

    Started as a subprocess rather than a thread on purpose: the scan attributes a port by
    the OWNING PROCESS's working directory, and a thread would share this interpreter's cwd
    (and its pid), which is exactly the attribution the test needs to exercise.
    """
    root = tmp_path / "run"
    (root / "sub").mkdir(parents=True)
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=str(root / "sub"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail("the fixture's dev server exited before it listened")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                break
        except OSError:
            time.sleep(0.1)
    else:  # pragma: no cover - only on a pathologically slow box
        proc.kill()
        pytest.fail("the fixture's dev server never began listening")
    try:
        yield root, port
    finally:
        proc.kill()
        proc.wait(timeout=10)


needs_lsof = pytest.mark.skipif(
    not (shutil.which("lsof") or shutil.which("ss")),
    reason="no port scanner on this host; the degradation path is covered separately",
)


# ── attribution: a port belongs to the run whose workspace owns the process ────────


@needs_lsof
def test_a_dev_server_in_the_runs_workspace_surfaces_as_an_openable_url(server):
    root, port = server
    scan = wp.discover_ports(root)

    assert scan.scanned
    # VACUITY FLOOR: this whole file is meaningless if the scan finds nothing, and a scan
    # that found nothing would satisfy every "is not present" assertion below forever.
    assert scan.ports, f"the fixture's server on {port} was not discovered ({scan.reason})"
    found = {p.port: p for p in scan.ports}
    assert port in found, sorted(found)
    hit = found[port]
    assert hit.url == f"http://localhost:{port}"
    assert hit.pid > 0
    # The port was found even though the process's cwd is a SUBDIRECTORY of the root — a
    # dev server is routinely started from a package subdir, not the worktree top.
    assert scan.reason == ""


@needs_lsof
def test_the_url_actually_serves_so_the_affordance_is_not_a_dead_link(server):
    """An "Open Preview" is only worth rendering if something answers on the other end."""
    import urllib.request

    root, port = server
    scan = wp.discover_ports(root)
    assert scan.ports, scan.reason
    url = scan.ports[0].url
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 — a loopback literal
        assert resp.status == 200


@needs_lsof
def test_a_port_is_not_attributed_to_an_unrelated_root(server, tmp_path):
    """Scoping in the direction that matters: another run must NOT see this run's server."""
    root, port = server
    other = tmp_path / "unrelated"
    other.mkdir()

    # Vacuity: prove the port IS discoverable from its own root before asserting it is not
    # discoverable from a different one. Without this the assertion below would also pass on
    # a scanner that never finds anything at all.
    assert port in {p.port for p in wp.discover_ports(root).ports}

    scan = wp.discover_ports(other)
    assert port not in {p.port for p in scan.ports}
    assert scan.reason, "an empty result must say why it is empty"


# ── honest degradation: "nothing running" and "nothing looked" differ ──────────────


def test_a_torn_down_workspace_reports_that_rather_than_an_empty_list(tmp_path):
    """§6.2's lifecycle clause costs no code because nothing is persisted: a workspace that
    is gone has no processes under it, so it reports no ports and says why."""
    scan = wp.discover_ports(tmp_path / "never-existed")
    assert scan.ports == []
    assert not scan.scanned
    assert "gone" in scan.reason


def test_a_host_with_no_port_scanner_says_so_instead_of_reporting_no_servers(tmp_path, monkeypatch):
    """The difference a user acts on. An empty list with no reason would tell them their dev
    server is not running when the truth is that nothing was able to look."""
    monkeypatch.setattr(wp.shutil, "which", lambda _name: None)
    scan = wp.discover_ports(tmp_path)
    assert scan.ports == []
    assert not scan.scanned
    assert "no port scanner" in scan.reason


def test_an_inline_run_is_told_it_has_nothing_to_preview():
    """A run with no isolated workspace must not trigger a host-wide scan."""

    class _Run:
        extra: dict = {}

    scan = wp.preview_scan(_Run())
    assert scan.ports == []
    assert "no isolated workspace" in scan.reason


def test_preview_scan_reads_the_workspace_path_off_the_run_record(server):
    """End to end from a run record, through the one `workspace_state` reader, to a URL."""
    root, port = server
    if not (shutil.which("lsof") or shutil.which("ss")):
        pytest.skip("no port scanner on this host")

    class _Run:
        def __init__(self, path: str) -> None:
            self.extra = {"workspace": {"path": path, "isolated": True}}

    scan = wp.preview_scan(_Run(str(root)))
    assert port in {p.port for p in scan.ports}, scan.reason


# ── only a URL localhost can actually reach is offered ────────────────────────────


def test_a_lan_bound_listener_is_not_offered_as_a_localhost_link(tmp_path, monkeypatch):
    """A listener on a specific LAN address is a real listener and a DEAD localhost link.

    Offering it would be the "affordance built from a port nothing answers on" defect with
    extra steps: the URL resolves, the connection is refused, and the user concludes the
    preview is broken rather than that the server is not bound where they thought.
    """
    monkeypatch.setattr(wp.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(wp, "_run", lambda argv: "p999\nn192.168.1.20:3000\n")
    scan = wp.discover_ports(tmp_path)
    assert scan.scanned
    assert scan.ports == []
    assert "no dev server" in scan.reason

    # VACUITY FLOOR: the same harness with a loopback bind must find the port, otherwise the
    # assertion above would pass because the fake output is simply unparseable.
    monkeypatch.setattr(wp, "_run", lambda argv: "p999\nn127.0.0.1:3000\n")
    monkeypatch.setattr(wp, "_cwds", lambda pids: {999: str(tmp_path)})
    monkeypatch.setattr(wp, "_command", lambda pid: "node")
    assert [p.port for p in wp.discover_ports(tmp_path).ports] == [3000]


# ── the two -F parsers, from fixtures (the ss tier cannot run on Darwin) ───────────


def test_the_lsof_listener_parser_binds_each_socket_to_the_pid_that_precedes_it():
    out = "p894\nf10\nPTCP\nn*:58672\nf11\nPTCP\nn127.0.0.1:9222\np909\nf3\nPTCP\nn[::1]:5173\n"
    assert wp.parse_lsof_listeners(out) == [
        (894, "*", 58672),
        (894, "127.0.0.1", 9222),
        (909, "[::1]", 5173),
    ]


def test_the_lsof_cwd_parser_maps_each_pid_to_its_directory():
    assert wp.parse_lsof_cwds("p47709\nfcwd\nn/private/tmp/run-a/sub\np1\nfcwd\nn/\n") == {
        47709: "/private/tmp/run-a/sub",
        1: "/",
    }


def test_the_ss_listener_parser_reads_the_linux_format():
    out = (
        'LISTEN 0 511 127.0.0.1:5173 0.0.0.0:* users:(("node",pid=4242,fd=24))\n'
        'LISTEN 0 4096 *:8080 *:* users:(("python3",pid=77,fd=3))\n'
        'LISTEN 0 128 192.168.1.9:9000 0.0.0.0:* users:(("other",pid=88,fd=9))\n'
    )
    assert wp.parse_ss_listeners(out) == [
        (4242, "127.0.0.1", 5173),
        (77, "*", 8080),
        (88, "192.168.1.9", 9000),
    ]
    # The LAN row is PARSED (the parser reports what the host said) and filtered later, so
    # the filter stays one testable decision rather than a silent omission in the parser.
    assert "192.168.1.9" not in wp._LOOPBACK_ADDRS


def test_an_empty_pid_list_never_reaches_lsof(monkeypatch):
    """Measured hazard, not a hypothetical: ``lsof -a -p "" -d cwd`` does not select nothing,
    it selects EVERY process on the host — a scoped probe silently becoming host-wide."""
    called: list[list[str]] = []
    monkeypatch.setattr(wp, "_run", lambda argv: called.append(argv) or "")
    assert wp._cwds([]) == {}
    assert called == []
