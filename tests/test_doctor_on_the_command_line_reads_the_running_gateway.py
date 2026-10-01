"""``personalclaw doctor`` reports what the running gateway measures, and says so.

Measured in a container a gateway was serving: the Doctor page read "Channels · 5 transports ok" and
a health score of 55, and ``personalclaw doctor`` printed a score of 40 with a deficit "Channel
transports reachable". The command measured in its own process, where no channel receiver runs,
so every configured channel read as down. And its Provider Health printed "✅ registered" for an
Amazon Bedrock instance with no AWS credentials, the same mark as the instances that worked.

With a gateway of this home running, the command asks it, signed in with the home's local secret
for two minutes, and prints its measurements: the score the page shows, and each provider as its
connection test found it. With none running it measures here and says so, and a provider nobody
tested reads as not tested.

The gateway here is a stand-in on a loopback port, answering the three routes the command reads.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from personalclaw.config import loader as config_loader
from personalclaw.llm.registry import ProviderEntry, get_default_registry

SECRET = "local-secret-for-this-test"
TOKEN = "session-token-for-this-test"

REMEDIATION = {
    "score": 55.0,
    "target_score": 90.0,
    "deficits": [
        {
            "key": "check:crashes.recent",
            "title": "Recent crash artifacts",
            "count": 1,
            "penalty": 15.0,
            "reachable": False,
            "blocked_by": "No automatic fix — read the crash file named in this row's details.",
        },
        {
            "key": "orphan_locks",
            "title": "",
            "count": 0,
            "penalty": 0.0,
            "reachable": True,
            "blocked_by": "",
        },
    ],
    "plan": [],
    "recent_runs": [],
}

NO_CREDENTIALS = (
    "No AWS credentials were found: this Amazon Bedrock instance names no AWS profile. Set its "
    "AWS profile in Settings → Providers to a profile that has credentials."
)

PROVIDERS = {
    "providers": [
        {
            "name": "doctor-cloud",
            "type": "bedrock",
            "connection": {"state": "failed", "detail": NO_CREDENTIALS},
        },
        {
            "name": "doctor-local",
            "type": "ollama",
            "connection": {"state": "connected", "detail": "Connected — 3 model(s) available"},
        },
    ]
}


class _Gateway(BaseHTTPRequestHandler):
    """The running gateway's three routes: the local-secret token mint, and two owner reads."""

    asked: list[str] = []

    def log_message(self, *_args) -> None:
        pass

    def _send(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 - the http.server hook
        url = urlparse(self.path)
        type(self).asked.append(self.path)
        if url.path == "/api/token/local":
            if self.headers.get("X-Local-Secret") != SECRET:
                return self._send(403, {"error": "invalid secret"})
            return self._send(200, {"token": TOKEN, "ttl": parse_qs(url.query).get("ttl")})
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"error": "session_required"})
        if url.path == "/api/doctor/remediation":
            return self._send(200, REMEDIATION)
        if url.path == "/api/model-providers":
            return self._send(200, PROVIDERS)
        return self._send(404, {"error": "not_found"})


@pytest.fixture()
def providers():
    registry = get_default_registry()
    names = []
    for name, kind in (("doctor-cloud", "bedrock"), ("doctor-local", "ollama")):
        registry.register_entry(
            ProviderEntry(name=name, type=kind, model="", options={}, credential=None)
        )
        names.append(name)
    yield names
    for name in names:
        registry.unregister_entry(name)


@pytest.fixture()
def running_gateway():
    """A gateway of this home, as `live_gateway` finds one: a runtime record naming a live pid
    and a port that answers, and the local secret it wrote."""
    _Gateway.asked = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = config_loader.config_dir()
    home.mkdir(parents=True, exist_ok=True)
    (home / ".local_secret").write_text(SECRET, encoding="utf-8")
    (home / "gateway.runtime.json").write_text(
        json.dumps({"port": server.server_address[1], "pid": os.getpid()}), encoding="utf-8"
    )
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _sections(capsys, gateway_reading=None) -> str:
    from personalclaw.cli_doctor import (
        _doctor_maintenance,
        _doctor_providers,
        _read_running_gateway,
    )

    reading = gateway_reading or _read_running_gateway()
    _doctor_maintenance(reading)
    _doctor_providers(reading)
    return capsys.readouterr().out


def test_with_a_gateway_running_it_prints_the_gateways_own_score(
    running_gateway, providers, capsys
):
    out = _sections(capsys)

    assert f"measured:    by the gateway running on port {running_gateway}" in out, out
    assert "score 55 / target 90" in out, out
    assert "Recent crash artifacts ×1 (−15.0)" in out
    assert "Channel transports reachable" not in out, "measured in this process, not the gateway"
    token_mints = [p for p in _Gateway.asked if p.startswith("/api/token/local")]
    assert token_mints == ["/api/token/local?ttl=2m"], "one short-lived token, nothing longer"
    assert TOKEN not in out and SECRET not in out


def test_a_provider_that_cannot_be_used_says_why_and_one_that_works_says_so(
    running_gateway, providers, capsys
):
    out = _sections(capsys)

    assert f"doctor-cloud (bedrock): ⚠️  cannot be used: {NO_CREDENTIALS}" in out, out
    assert "doctor-local (ollama): ✅ Connected — 3 model(s) available" in out, out
    assert "✅ registered" not in out


def test_with_no_gateway_running_it_measures_here_and_says_so(providers, capsys):
    out = _sections(capsys)

    assert "measured:    here, with no gateway of this home running" in out, out
    assert "score " in out
    for name, kind in (("doctor-cloud", "bedrock"), ("doctor-local", "ollama")):
        assert (
            f"{name} ({kind}): ⏹  registered, connection not tested (no gateway of this home is "
            "running to test it)"
        ) in out, out


def test_a_gateway_it_cannot_sign_in_to_is_named_and_the_numbers_are_said_to_be_its_own(
    running_gateway, providers, capsys
):
    (config_loader.config_dir() / ".local_secret").write_text("another-secret", encoding="utf-8")

    out = _sections(capsys)

    assert f"measured:    here, not by the gateway running on port {running_gateway}" in out, out
    assert "connection not tested (the running gateway could not be asked)" in out, out


def test_the_whole_command_reads_the_running_gateway(running_gateway, providers, capsys):
    """Through `_doctor()` itself, so a section that stops being given the reading reds here."""
    from unittest.mock import MagicMock, patch

    from personalclaw.cli_doctor import _doctor

    config_loader.config_dir().joinpath("config.json").write_text(
        json.dumps({"dashboard": {"url": f"http://127.0.0.1:{running_gateway}"}}), encoding="utf-8"
    )
    with (
        patch("personalclaw.cli_doctor.shutil.which", side_effect=lambda b: f"/usr/local/bin/{b}"),
        patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="v22.12.0")),
        patch("personalclaw.cli_doctor.is_local_bind", return_value=True),
        patch("personalclaw.ffmpeg_binary.find_ffmpeg", return_value="/usr/local/bin/ffmpeg"),
    ):
        try:
            _doctor()
        except SystemExit:
            pass
    out = capsys.readouterr().out

    assert "score 55 / target 90" in out, out
    assert f"doctor-cloud (bedrock): ⚠️  cannot be used: {NO_CREDENTIALS}" in out, out
