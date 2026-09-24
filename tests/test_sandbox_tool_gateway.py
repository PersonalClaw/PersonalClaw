"""EI-7 (EXECUTION-ISOLATION §5.2) — the loopback-free exec-channel tool gateway.

Success criterion 7 has four clauses. Three are host-side and are verified here against the REAL
shim over a REAL pipe pair (no mock channel — the shim is executed as a subprocess exactly as a
sandboxed agent would run it):

* **(a) zero listening sockets** — asserted by construction over the shipped shim source and the
  gateway module: neither contains a socket, a bind/listen/accept, an HTTP client or a URL. The
  vacuity floor is that the same scanner DOES flag a string containing those tokens, so a scanner
  that matches nothing cannot pass for a clean result.
* **(b) no credential material in the sandbox** — the environment the shim needs is exactly two
  fd numbers plus an advertised tool list. Asserted as a whole-env check (no key or value carries
  a secret-shaped name), with a floor proving the same check catches a planted token.
* **(c) a research-profile sandbox is refused write-class tools host-side** — the same tool, in
  the same surface, over the same channel: refused under ``REVIEW_ONLY`` (``tool_grants="read"``)
  and served under ``CODING`` (``read_write``). Both directions, because a gateway that refuses
  everything would pass the refusal alone.
* **the docker/lima clause is NOT verified here.** The container tiers are EI-2/EI-4 and do not
  exist in this tree (``sandbox_providers`` ships ``none`` only), so "inside a docker/lima
  sandbox" cannot be honestly claimed. What IS claimed is the transport and the policy the
  container tier will use unchanged: the channel is a pipe pair the host created, which is the
  same object a ``docker exec`` gives, and the refusal is taken host-side after the request
  arrives.

Nothing here reaches the real home: ``PERSONALCLAW_HOME`` is redirected and the redirect is
asserted to have bound before any tool runs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from personalclaw.guardrails.policy import CODING, INTERACTIVE, REVIEW_ONLY, SafetyProfile
from personalclaw.sandbox_providers.pclaw_tool import (
    DEFAULT_REQUEST_FD,
    DEFAULT_RESPONSE_FD,
    ENV_OFFERED,
    SHIM_SOURCE,
    install_shim,
    shim_env,
)
from personalclaw.sandbox_providers.tool_gateway import (
    DEFAULT_SURFACE,
    ERR_REFUSED,
    ERR_UNKNOWN_TOOL,
    ToolContext,
    ToolGateway,
    ToolSpec,
)

#: Tokens that would mean a network hop exists somewhere in the transport.
_NETWORK_TOKENS = (
    "import socket",
    "socket.socket",
    ".bind(",
    ".listen(",
    ".accept(",
    "http://",
    "https://",
    "urllib",
    "requests.",
    "aiohttp",
    "127.0.0.1",
    "localhost",
)

#: Env key/value shapes that would mean a credential crossed into the sandbox.
_SECRET_SHAPES = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|credential|bearer|cookie|session[_-]?id)",
    re.IGNORECASE,
)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "home"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PERSONALCLAW_HOME", str(target))
    from personalclaw.config.loader import config_dir

    assert str(target) in str(config_dir()), (
        f"PERSONALCLAW_HOME redirect did not bind (config_dir={config_dir()}) — the gateway's "
        "tool handlers would write to the real home"
    )
    return target


def _ctx(workspace: Path, *, sandbox: str = "none") -> ToolContext:
    return ToolContext(workspace=str(workspace), session_key="sandbox:test", sandbox=sandbox)


# ── (a) zero listening sockets, by construction ───────────────────────────────


def _network_hits(text: str) -> list[str]:
    return [token for token in _NETWORK_TOKENS if token in text]


def test_the_shim_contains_no_network_transport_at_all() -> None:
    """No socket, no bind/listen/accept, no HTTP client, no URL, no loopback address."""
    assert _network_hits(SHIM_SOURCE) == [], _network_hits(SHIM_SOURCE)
    # Vacuity floor: the scanner is not blind. A shim that DID open a loopback socket is caught.
    planted = SHIM_SOURCE + "\nimport socket\ns = socket.socket()\ns.bind(('127.0.0.1', 0))\n"
    assert set(_network_hits(planted)) >= {"import socket", ".bind(", "127.0.0.1"}


def test_the_host_gateway_never_binds_or_listens() -> None:
    """The host end is a pipe reader/writer. A bind here would be a port on the host side of the
    same channel — no better than the in-container proxy §5.2 rejects."""
    source = Path(
        str(__import__("personalclaw.sandbox_providers.tool_gateway", fromlist=["x"]).__file__)
    ).read_text(encoding="utf-8")
    # Strip the module docstring: it DISCUSSES 127.0.0.1 and HTTP, and a text scanner reads
    # comments as code unless the scope is narrowed to the code.
    body = source.split('"""', 2)[-1]
    hits = [t for t in _NETWORK_TOKENS if t in body]
    assert hits == [], hits
    # Floor: the scanner still fires on the same body with a bind planted in it.
    assert ".listen(" in [t for t in _NETWORK_TOKENS if t in body + "\nsock.listen(5)\n"]


# ── (b) no credential material inside the sandbox ──────────────────────────────


def test_the_shims_environment_carries_no_credential_material() -> None:
    env = shim_env(offered=("memory_recall", "memory_read"))
    for key, value in env.items():
        assert not _SECRET_SHAPES.search(key), key
        assert not _SECRET_SHAPES.search(str(value)), f"{key}={value}"
    # It carries exactly what it needs and nothing else.
    assert set(env) == {"PCLAW_TOOL_REQUEST_FD", "PCLAW_TOOL_RESPONSE_FD", ENV_OFFERED}
    # Vacuity floor: the same check catches a planted secret.
    planted = {**env, "PERSONALCLAW_API_KEY": "abc123"}
    assert any(_SECRET_SHAPES.search(k) for k in planted)


def test_the_shim_reads_no_environment_beyond_the_channel_and_the_offered_list() -> None:
    """A shim that read a token out of the environment would defeat clause (b) even with a clean
    launch env, so the source is pinned to the three names it may read."""
    read_names = set(re.findall(r'os\.environ\.get\(\s*"([^"]+)"', SHIM_SOURCE))
    assert read_names == {
        "PCLAW_TOOL_REQUEST_FD",
        "PCLAW_TOOL_RESPONSE_FD",
        "PCLAW_TOOL_OFFERED",
    }, read_names


# ── (c) the host-side write-class refusal ─────────────────────────────────────


def test_a_research_profile_is_refused_a_write_class_tool_and_a_coding_profile_is_not(
    home: Path, tmp_path: Path
) -> None:
    """The load-bearing clause, both directions over the SAME surface and the SAME tool."""
    ws = tmp_path / "ws"
    ws.mkdir()
    request = {
        "protocol": 1,
        "id": "1",
        "tool": "memory_remember",
        "args": {"text": "prefers tabs"},
    }

    refused = ToolGateway(profile=REVIEW_ONLY, context=_ctx(ws)).handle_request(request)
    assert not refused["ok"], refused
    assert refused["code"] == ERR_REFUSED, refused
    assert "write-class" in refused["error"]

    # Vacuity floor: the identical request under a read_write profile is SERVED, so the refusal
    # above is the profile doing work rather than the tool being broken.
    served = ToolGateway(profile=CODING, context=_ctx(ws)).handle_request(request)
    assert served["ok"], served
    assert "prefers tabs" in served["result"]


def test_a_read_tool_is_served_under_the_research_profile(home: Path, tmp_path: Path) -> None:
    """Read-only does not mean useless: the same profile that refuses the write serves the read."""
    ws = tmp_path / "ws"
    ws.mkdir()
    gateway = ToolGateway(profile=REVIEW_ONLY, context=_ctx(ws))
    response = gateway.handle_request({"protocol": 1, "id": "1", "tool": "memory_read", "args": {}})
    assert response["ok"], response


def test_the_advertised_surface_matches_the_enforced_one(home: Path, tmp_path: Path) -> None:
    """A tool advertised but refused (or refused but advertised) is the drift that makes an agent
    burn a turn discovering the policy. ``offered`` is derived from the same refusal function."""
    ws = tmp_path / "ws"
    ws.mkdir()
    research = ToolGateway(profile=REVIEW_ONLY, context=_ctx(ws))
    coding = ToolGateway(profile=CODING, context=_ctx(ws))
    assert "memory_remember" not in research.offered
    assert "memory_recall" in research.offered
    assert "memory_remember" in coding.offered
    for name in research.offered:
        got = research.handle_request({"protocol": 1, "id": "x", "tool": name, "args": {}})
        assert got.get("code") != ERR_REFUSED, (name, got)


def test_a_custom_grant_profile_is_bounded_by_its_allowlist(home: Path, tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    profile = SafetyProfile(name="narrow", tool_grants="custom", tool_allowlist=("memory_read",))
    gateway = ToolGateway(profile=profile, context=_ctx(ws))
    assert gateway.offered == ("memory_read",)
    denied = gateway.handle_request({"protocol": 1, "id": "1", "tool": "memory_recall", "args": {}})
    assert denied["code"] == ERR_REFUSED, denied


def test_a_tool_not_on_the_surface_is_refused_by_name(home: Path, tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    gateway = ToolGateway(profile=INTERACTIVE, context=_ctx(ws))
    got = gateway.handle_request({"protocol": 1, "id": "1", "tool": "bash", "args": {}})
    assert got["code"] == ERR_UNKNOWN_TOOL, got


def test_a_protocol_mismatch_is_refused_rather_than_guessed(home: Path, tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    gateway = ToolGateway(profile=INTERACTIVE, context=_ctx(ws))
    got = gateway.handle_request({"protocol": 99, "id": "1", "tool": "memory_read", "args": {}})
    assert not got["ok"] and "protocol" in got["error"]


def test_every_gateway_call_writes_a_sel_row(home: Path, tmp_path: Path) -> None:
    """ "every call SEL-audited under the owning session key" — read back out of the log."""
    from personalclaw.sel import sel

    ws = tmp_path / "ws"
    ws.mkdir()
    before = len(sel().recent(limit=500))
    ToolGateway(profile=REVIEW_ONLY, context=_ctx(ws)).handle_request(
        {"protocol": 1, "id": "7", "tool": "memory_remember", "args": {"text": "x"}}
    )
    rows = [r for r in sel().recent(limit=500) if r.get("operation") == "memory_remember"]
    assert len(sel().recent(limit=500)) > before
    assert rows, "the refused call wrote no SEL row"
    assert rows[-1]["outcome"] == "denied", rows[-1]
    assert rows[-1]["caller_identity"] == "sandbox:test"


# ── the real shim over a real pipe pair ───────────────────────────────────────


def _run_shim_against(
    gateway: ToolGateway, shim: Path, argv: list[str]
) -> subprocess.CompletedProcess[str]:
    """Execute the REAL shim with the host end of a REAL pipe pair, exactly as a sandbox would.

    The host creates both pipes before the child exists; the child inherits the two fds and holds
    no other channel. Nothing binds, so there is nothing for the child to connect to.
    """
    req_r, req_w = os.pipe()
    res_r, res_w = os.pipe()
    os.set_inheritable(req_w, True)
    os.set_inheritable(res_r, True)
    served: list[int] = []

    def _serve() -> None:
        with os.fdopen(req_r, "rb") as rfile, os.fdopen(res_w, "wb") as wfile:
            served.append(gateway.serve_fileobjs(rfile, wfile, max_requests=1))

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PERSONALCLAW_HOME": os.environ.get("PERSONALCLAW_HOME", ""),
        **shim_env(request_fd=req_w, response_fd=res_r, offered=gateway.offered),
    }
    try:
        proc = subprocess.run(
            [sys.executable, str(shim), *argv],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            pass_fds=(req_w, res_r),
        )
    finally:
        os.close(req_w)
        os.close(res_r)
        thread.join(timeout=10)
    return proc


def test_pclaw_tool_memory_recall_succeeds_through_the_exec_channel(
    home: Path, tmp_path: Path
) -> None:
    """Clause (a)'s companion: the tool call actually WORKS over the fd channel, with no socket
    anywhere. Driven through the installed shim as a subprocess, not through a mock."""
    from personalclaw.sqlite_compat import probe

    if not probe().fts5:
        pytest.skip("SQLite build has no FTS5, so memory_recall cannot return a hit here")

    ws = tmp_path / "ws"
    ws.mkdir()
    # Seed something recallable through the store's own writer.
    from personalclaw.memory import MemoryStore

    store = MemoryStore(ws)
    store.init()
    store.add_preference("the deploy runbook lives in docs/deploy.md")

    shim = install_shim(tmp_path / "bin")
    assert shim.is_file() and os.access(shim, os.X_OK), shim

    gateway = ToolGateway(profile=REVIEW_ONLY, context=_ctx(ws))
    proc = _run_shim_against(gateway, shim, ["memory_recall", "--query", "runbook"])
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    # NOT `"runbook" in stdout`: the empty-result sentence is "No memory matched 'runbook'.",
    # which contains the query and would read as a hit. Assert the SEEDED CONTENT instead, and
    # assert the empty sentence is absent — a recall that found nothing must not pass for one
    # that found something.
    assert "No memory matched" not in proc.stdout, proc.stdout
    assert "deploy.md" in proc.stdout, proc.stdout

    # Vacuity floor for the pairing: a query that matches nothing DOES produce the empty
    # sentence, so the assertion above is discriminating between two real outcomes.
    empty = _run_shim_against(
        ToolGateway(profile=REVIEW_ONLY, context=_ctx(ws)),
        shim,
        ["memory_recall", "--query", "zzzznothingmatchesthis"],
    )
    assert empty.returncode == 0, (empty.returncode, empty.stderr)
    assert "No memory matched" in empty.stdout, empty.stdout


def test_pclaw_tool_reports_the_hosts_refusal_verbatim(home: Path, tmp_path: Path) -> None:
    """A refusal reaches the agent as a coded, readable message rather than a hang or a crash."""
    ws = tmp_path / "ws"
    ws.mkdir()
    shim = install_shim(tmp_path / "bin")
    gateway = ToolGateway(profile=REVIEW_ONLY, context=_ctx(ws))
    proc = _run_shim_against(gateway, shim, ["memory_remember", "--text", "nope"])
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert ERR_REFUSED in proc.stderr, proc.stderr
    assert "write-class" in proc.stderr, proc.stderr


def test_the_shim_fails_cleanly_outside_a_sandbox(tmp_path: Path) -> None:
    """Run with no channel at all: a clear "only works inside a sandbox" message, not a traceback.
    This is also the proof that the shim has no fallback transport to reach for."""
    shim = install_shim(tmp_path / "bin")
    proc = subprocess.run(
        [sys.executable, str(shim), "memory_read"],
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 69, (proc.returncode, proc.stderr)
    assert "inside a PersonalClaw sandbox" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_the_shim_default_fds_start_after_the_agents_own_stdio() -> None:
    assert (DEFAULT_REQUEST_FD, DEFAULT_RESPONSE_FD) == (3, 4)


def test_the_default_surface_declares_both_read_and_write_class_tools() -> None:
    """A read-only default surface would make the write refusal unreachable in production — a
    control present but inert. Pinned so a later trim cannot quietly create that state."""
    kinds = {spec.name: spec.kind for spec in DEFAULT_SURFACE}
    assert "memory_remember" in kinds and kinds["memory_remember"] == "edit"
    assert any(k in ("read", "search") for k in kinds.values())


def test_a_tool_handler_failure_is_data_not_a_gateway_crash(home: Path, tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()

    def _boom(args: dict, ctx: ToolContext) -> str:
        raise RuntimeError("kaboom")

    surface = (ToolSpec(name="explode", kind="read", handler=_boom),)
    gateway = ToolGateway(profile=INTERACTIVE, context=_ctx(ws), surface=surface)
    got = gateway.handle_request({"protocol": 1, "id": "1", "tool": "explode", "args": {}})
    assert not got["ok"] and "kaboom" in got["error"]


def test_unparseable_input_on_the_channel_is_answered_not_fatal(home: Path, tmp_path: Path) -> None:
    import io

    ws = tmp_path / "ws"
    ws.mkdir()
    gateway = ToolGateway(profile=INTERACTIVE, context=_ctx(ws))
    rfile = io.BytesIO(b"not json\n")
    wfile = io.BytesIO()
    assert gateway.serve_fileobjs(rfile, wfile) == 1
    response = json.loads(wfile.getvalue().decode("utf-8").strip())
    assert not response["ok"] and "unparseable" in response["error"]
