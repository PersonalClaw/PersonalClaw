"""A download too large to buffer streams through the egress guard: ``net.open_url``.

The bundled chat model's weights and an app's model files were fetched with the standard
library's ``urlopen``, which no guard stood in front of: a host on Denied hosts was reached all the
same, a redirect went wherever it pointed, and nothing was audited. ``open_url`` is that opener
with every request asked of the guard first, each redirect hop included, under the connector
policy and the owner's Network egress settings. These drive it against a source on this machine,
which the guard refuses until the owner allows it, as it refuses any address of this computer.
"""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from personalclaw.config.loader import config_dir
from personalclaw.net import EgressBlocked, open_url
from personalclaw.net.client import DOWNLOAD_AGAIN
from personalclaw.sel import sel

#: The cloud metadata address: no setting allows it.
METADATA = "http://169.254.169.254/latest/meta-data/"


class _Source(http.server.ThreadingHTTPServer):
    """A download source on this machine that records each request it is sent."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _SourceHandler)
        self.paths: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _SourceHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802 — http.server's name
        self.server.paths.append(self.path)  # type: ignore[attr-defined]
        moved = {"/moved": "/model.bin", "/away": METADATA}.get(self.path)
        if moved:
            self.send_response(302)
            self.send_header("Location", moved)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"the model's bytes"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def source(monkeypatch):
    # The opener reads the proxy settings from the environment; a developer's proxy would carry
    # these requests off the machine, past the source they are meant for.
    for name in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    server = _Source()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def _egress(**settings: list[str]) -> None:
    """This test home's Network egress settings."""
    (config_dir() / "config.json").write_text(
        json.dumps({"security": {"egress": settings}}), encoding="utf-8"
    )


def _audited() -> list[tuple[str, str]]:
    return [
        (row.get("outcome", ""), row.get("resources", ""))
        for row in reversed(sel().recent(200))
        if row.get("operation") == "egress_fetch"
    ]


def test_an_allowed_source_is_read_and_each_hop_audited(source):
    _egress(allow_hosts=["127.0.0.1"])

    with open_url(f"{source.url}/moved", timeout_s=10) as response:
        body = response.read()

    assert body == b"the model's bytes"
    assert source.paths == ["/moved", "/model.bin"]
    assert _audited() == [
        ("allowed", f"{source.url}/moved"),
        ("allowed", f"{source.url}/model.bin"),
    ]


def test_this_computer_is_refused_until_the_owner_allows_it(source):
    with pytest.raises(EgressBlocked) as refused:
        open_url(f"{source.url}/model.bin", timeout_s=10)

    said = str(refused.value)
    assert source.paths == [], "a refused source was contacted"
    assert "Allowed hosts" in said and said.endswith(f"{DOWNLOAD_AGAIN}."), said
    assert _audited() == [("denied", f"{source.url}/model.bin")]


def test_a_host_on_denied_hosts_is_never_contacted(source):
    _egress(allow_hosts=["127.0.0.1"], deny_hosts=["127.0.0.1"])

    with pytest.raises(EgressBlocked) as refused:
        open_url(f"{source.url}/model.bin", timeout_s=10)

    assert source.paths == []
    assert "is on Denied hosts" in str(refused.value)
    assert _audited() == [("denied", f"{source.url}/model.bin")]


def test_a_redirect_is_asked_before_it_is_followed(source):
    """The source the owner allowed sends the request on to the metadata address: the hop is
    refused before it is sent, and the refusal names no setting, because none allows it."""
    _egress(allow_hosts=["127.0.0.1"])

    with pytest.raises(EgressBlocked) as refused:
        open_url(f"{source.url}/away", timeout_s=10)

    said = str(refused.value)
    assert source.paths == ["/away"]
    assert METADATA in said and "Allowed hosts" not in said and DOWNLOAD_AGAIN not in said, said
    assert _audited() == [("allowed", f"{source.url}/away"), ("denied", METADATA)]


def test_a_url_the_guard_does_not_speak_is_refused(tmp_path):
    """The opener can read files and other schemes; the guard asks about those too."""
    secret = tmp_path / "file.txt"
    secret.write_text("on this machine", encoding="utf-8")

    with pytest.raises(EgressBlocked) as refused:
        open_url(secret.as_uri(), timeout_s=10)

    assert "scheme 'file' not allowed" in str(refused.value)
    assert _audited() == [("denied", secret.as_uri())]
