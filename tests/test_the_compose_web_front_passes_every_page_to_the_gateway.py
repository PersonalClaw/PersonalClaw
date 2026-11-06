"""The compose web front (https://127.0.0.1:3443) passes every path on, and serves no page.

A browser's sign-in needs a value the gateway hands to the page it served, kept in that origin's
storage (``dashboard/sign_in_pair.py``). A page nginx served from its own copy of the build could
never be handed one, so nginx is the TLS front and nothing more
(``deploy/docker/nginx.conf.template``).
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_NGINX = _REPO / "deploy" / "docker" / "nginx.conf.template"


def _tls_server() -> str:
    return _NGINX.read_text(encoding="utf-8").split("listen 443", 1)[1]


def test_every_path_goes_to_the_gateway() -> None:
    block = _tls_server().split("    location / {", 1)[1].split("}", 1)[0]
    assert "proxy_pass http://$gateway_upstream;" in block
    assert "proxy_set_header Upgrade $http_upgrade;" in block, "a socket outside /api/ is cut off"


def _locations(server: str) -> dict[str, str]:
    """Each ``location`` block of *server*, by its match, with its body."""
    blocks: dict[str, str] = {}
    for found in re.finditer(r"^\s*location\s+([^{]+)\{", server, re.MULTILINE):
        depth, at = 1, found.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(server[at], 0)
            at += 1
        blocks[found.group(1).strip()] = server[found.end() : at - 1]
    return blocks


def test_every_location_proxies_to_the_gateway_or_returns() -> None:
    blocks = _locations(_tls_server())
    assert "/" in blocks and "/api/" in blocks, sorted(blocks)
    for match, body in blocks.items():
        proxies = "proxy_pass http://$gateway_upstream;" in body
        assert proxies or re.search(
            r"^\s*return\s", body, re.MULTILINE
        ), f"location {match} answers from nginx itself"


def test_nginx_answers_no_file_of_its_own() -> None:
    server = _tls_server()
    assert "try_files" not in server, "nginx serves its own copy of the dashboard's page"
    assert not re.search(r"location\s+~\*?\s+\\\.\(", server), "nginx serves build files itself"
