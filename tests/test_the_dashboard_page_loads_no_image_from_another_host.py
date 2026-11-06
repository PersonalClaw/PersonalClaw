"""The dashboard's page loads no image from another host.

An image in text the page shows is a request to a host the text's author picked, carrying
whatever its address holds. So the page's own policy (``server.dashboard_csp``) lets it load
images from this gateway and from data the page holds, and from nowhere else: a renderer that
slips, now or later, cannot reopen the request. A remote image is fetched by the gateway, on a
press, through the egress guard (``handlers/remote_image.py``).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.dashboard.handlers import api_file_raw
from personalclaw.dashboard.server import SECURITY_HEADERS, dashboard_csp


def _directive(policy: str, name: str) -> list[str]:
    for part in policy.split(";"):
        words = part.split()
        if words and words[0] == name:
            return words[1:]
    raise AssertionError(f"{name} is missing from {policy!r}")


def test_the_page_loads_images_from_itself_and_its_own_data_only() -> None:
    for policy in (dashboard_csp(), dashboard_csp(port=10000)):
        assert _directive(policy, "img-src") == ["'self'", "data:", "blob:"], policy


def test_the_security_headers_turn_dns_prefetch_off() -> None:
    """A browser may look up the host of each link on a page before anyone opens it. This pins
    the value the gateway sets to turn that off; test_dashboard_frame_headers's door test checks
    it on real answers."""
    assert SECURITY_HEADERS["X-DNS-Prefetch-Control"] == "off"


@pytest.mark.asyncio
async def test_an_svg_file_opened_as_a_page_loads_no_image_and_runs_no_script(tmp_path) -> None:
    """An SVG served from a workspace file can be opened as a page of its own ("Open directly").
    Its policy then decides what it loads: nothing but its own data images and fonts."""
    svg = tmp_path / "chart.svg"
    svg.write_bytes(
        b"<svg xmlns='http://www.w3.org/2000/svg'>"
        b"<image href='https://images.example.com/p.png'/></svg>"
    )
    app = web.Application()
    app.router.add_get("/api/file-raw", api_file_raw)
    with (
        patch("personalclaw.sel.sel", return_value=MagicMock()),
        patch("personalclaw.security.is_sensitive_path", return_value=False),
        patch("personalclaw.dashboard.handlers._validate_dashboard_path", return_value=str(svg)),
    ):
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/api/file-raw?path={svg}")
            assert resp.status == 200
            assert resp.headers["Content-Security-Policy"] == (
                "default-src 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'; "
                "sandbox"
            )


@pytest.mark.asyncio
async def test_an_xhtml_file_with_an_svg_inside_is_not_served_as_an_svg(tmp_path) -> None:
    """Only a file whose first element is an SVG is one: an XHTML page holding an SVG is not."""
    page = tmp_path / "page.svg"
    page.write_bytes(
        b"<?xml version='1.0'?><!-- made by hand -->"
        b"<html xmlns='http://www.w3.org/1999/xhtml'><body><svg></svg></body></html>"
    )
    app = web.Application()
    app.router.add_get("/api/file-raw", api_file_raw)
    with (
        patch("personalclaw.sel.sel", return_value=MagicMock()),
        patch("personalclaw.security.is_sensitive_path", return_value=False),
        patch("personalclaw.dashboard.handlers._validate_dashboard_path", return_value=str(page)),
    ):
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(f"/api/file-raw?path={page}")
            assert resp.status == 403
            assert "not a recognized format" in await resp.text()
