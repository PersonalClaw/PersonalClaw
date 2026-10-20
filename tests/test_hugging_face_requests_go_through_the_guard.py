"""huggingface_hub's own requests go through the egress guard, every redirect included.

The library sends its requests, and follows its redirects, with an HTTP client of its own, and the
model and voice downloads apps start all go through it: past the guard, so a host on Denied hosts,
or this computer, or the cloud metadata service behind a redirect, was reached all the same, and
nothing was audited. Every PersonalClaw command now gives the library clients that ask the guard
before each request leaves (``net/libraries.py``), from the moment it is imported.

Each case runs in a fresh interpreter, as a ``personalclaw`` process starts, against a stand-in for
the Hub on this machine, so what it checks is what a gateway does: nothing here imports the library
into the suite's own process with the guard in it.
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import tool_homes

from personalclaw import cli
from personalclaw.net import libraries

#: The cloud metadata service, which no setting lets a request reach.
_METADATA = "http://169.254.169.254/latest/meta-data/"


class _Hub(http.server.ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _HubHandler)
        self.paths: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _HubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 — http.server's name
        self.server.paths.append(self.path)  # type: ignore[attr-defined]
        if self.path == "/elsewhere":
            self.send_response(302)
            self.send_header("location", _METADATA)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        body = json.dumps({"id": "example-org/example-model", "siblings": []}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def hub():
    server = _Hub()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


_PROBE = """
import json, os
from personalclaw.net import libraries
libraries.install()
import huggingface_hub
out = {"hooks": [h.__name__ for h in huggingface_hub.get_session().event_hooks["request"]]}
try:
    if os.environ.get("PROBE_API"):
        huggingface_hub.HfApi(endpoint=os.environ["PROBE_URL"]).model_info("example-org/example-model")
        out["answered"] = True
    else:
        out["status"] = huggingface_hub.get_session().get(os.environ["PROBE_URL"]).status_code
except Exception as exc:
    out["refused"] = f"{type(exc).__name__}: {exc}"
from personalclaw.sel import sel
out["audited"] = [
    [row["outcome"], row["resources"]]
    for row in reversed(sel().recent(50))
    if row.get("operation") == "egress_fetch"
]
print(json.dumps(out))
"""


def _probe(tmp_path: Path, url: str, *, api: bool = False, **egress: list[str]) -> dict:
    """What a fresh PersonalClaw process's huggingface_hub does with a request for *url*, under
    *egress* (this home's Network egress settings)."""
    home = tmp_path / "pclaw-home"
    home.mkdir(exist_ok=True)
    (home / "config.json").write_text(
        json.dumps({"security": {"egress": egress}}), encoding="utf-8"
    )
    user_home = tmp_path / "user-home"
    user_home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(user_home),
        "PERSONALCLAW_HOME": str(home),
        "PROBE_URL": url,
        "no_proxy": "127.0.0.1,localhost",
        "NO_PROXY": "127.0.0.1,localhost",
        **({"PROBE_API": "1"} if api else {}),
    }
    env.update(tool_homes.library_env_for(home))
    done = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_guard_is_in_from_the_librarys_first_import(tmp_path, hub):
    seen = _probe(tmp_path, f"{hub.url}/api/models/example-org/example-model")
    assert seen["hooks"][0] == "_before", seen["hooks"]


def test_a_request_to_this_computer_is_never_sent_unless_the_owner_allows_it(tmp_path, hub):
    url = f"{hub.url}/api/models/example-org/example-model"

    refused = _probe(tmp_path, url)

    assert "Allowed hosts in Settings" in refused.get("refused", ""), refused
    assert hub.paths == [], "the request reached the host before the guard answered"
    assert refused["audited"] == [["denied", url]]

    allowed = _probe(tmp_path, url, allow_hosts=["127.0.0.1"])

    assert allowed.get("status") == 200, allowed
    assert hub.paths == ["/api/models/example-org/example-model"]
    assert ["allowed", url] in allowed["audited"], allowed["audited"]


def test_a_host_on_denied_hosts_is_never_sent_a_request_from_the_librarys_own_calls(tmp_path, hub):
    """The library's own API, not only its client: ``model_info`` is refused before it is sent."""
    seen = _probe(tmp_path, hub.url, api=True, allow_hosts=["127.0.0.1"], deny_hosts=["127.0.0.1"])
    assert "is on Denied hosts" in seen.get("refused", ""), seen
    assert hub.paths == []


def test_a_redirect_is_checked_before_it_is_followed(tmp_path, hub):
    url = f"{hub.url}/elsewhere"

    seen = _probe(tmp_path, url, allow_hosts=["127.0.0.1"])

    assert "refused" in seen and _METADATA in seen["refused"], seen
    assert hub.paths == ["/elsewhere"], "control: the first request was allowed and answered"
    assert seen["audited"][:2] == [["allowed", url], ["denied", _METADATA]], seen["audited"]


def test_a_library_that_takes_no_client_is_switched_offline(monkeypatch):
    """A version of the library with no way to be handed a client cannot be held to the guard, so
    its downloads are refused rather than sent past it."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    old = SimpleNamespace(__version__="0.0", constants=SimpleNamespace(HF_HUB_OFFLINE=False))

    libraries.guard_the_hub(old)

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert old.constants.HF_HUB_OFFLINE is True


def test_every_command_holds_the_library_to_the_guard(monkeypatch):
    installed: list[bool] = []
    monkeypatch.setattr(libraries, "install", lambda: installed.append(True))
    monkeypatch.setattr(cli, "_doctor_paths", lambda: None)
    monkeypatch.setattr(sys, "argv", ["personalclaw", "doctor", "--paths"])

    cli.main()

    assert installed == [True]
