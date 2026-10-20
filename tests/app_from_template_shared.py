"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_app_from_template.py``

Its checks:
``tests/rails/test_app_from_template_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import argparse
import http.server
import json
import threading

import pytest

from personalclaw.config.loader import config_dir


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves whatever ``self.server.reply`` says: (status, headers, body)."""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        status, headers, body = self.server.reply  # type: ignore[attr-defined]
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def _egress(**settings: list[str]) -> None:
    """This test home's Settings → Security → Network egress."""
    (config_dir() / "config.json").write_text(
        json.dumps({"security": {"egress": settings}}), encoding="utf-8"
    )


@pytest.fixture
def local_archive_server(monkeypatch: pytest.MonkeyPatch):
    """A loopback HTTP server the transport is allowed to talk to, for this test only: the
    template allowlists name it, and so does Allowed hosts, as an owner serving one would."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    server.reply = (200, {"Content-Type": "application/gzip"}, b"")  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr("personalclaw.cli_app_new.TEMPLATE_SCHEMES", frozenset({"http"}))
    monkeypatch.setattr("personalclaw.cli_app_new.TEMPLATE_HOSTS", frozenset({"127.0.0.1"}))
    _egress(allow_hosts=["127.0.0.1"])
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}/tar.gz"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _args(**kwargs: object) -> argparse.Namespace:
    base = dict(
        app_cmd="new",
        name=None,
        type=None,
        list_types=False,
        dest=".",
        display_name="",
        description="",
        author="",
        force=False,
        from_template=False,
        template_url="",
        template_archive="",
    )
    base.update(kwargs)
    return argparse.Namespace(**base)
