"""Parity: the transport ceilings (aiohttp client_max_size + nginx body caps) must
track the upload policy so the per-filetype gate — not the transport — is the real
limit. These run without Docker (static config + module inspection)."""

import re
from pathlib import Path

from personalclaw.uploads.policy import max_category_limit, single_post_threshold

_REPO = Path(__file__).resolve().parent.parent
_NGINX = _REPO / "deploy" / "docker" / "nginx.conf.template"

_MB = 1024 * 1024
_GB = 1024**3


def _parse_size(tok: str) -> int:
    """nginx size token → bytes: '2g' / '96m' / '512k'."""
    tok = tok.strip().lower()
    mult = {"g": _GB, "m": _MB, "k": 1024}
    if tok and tok[-1] in mult:
        return int(float(tok[:-1]) * mult[tok[-1]])
    return int(tok)


def _nginx_text() -> str:
    return _NGINX.read_text()


class TestNginxUploadCeilings:
    def test_uploads_location_exists(self):
        text = _nginx_text()
        assert "location /api/uploads/" in text, "dedicated /api/uploads/ block missing"

    def test_uploads_body_cap_covers_video(self):
        # The /api/uploads/ location's client_max_body_size must cover the largest
        # category (video) so the resumable protocol isn't throttled by the proxy.
        text = _nginx_text()
        block = text.split("location /api/uploads/", 1)[1].split("location", 1)[0]
        m = re.search(r"client_max_body_size\s+(\S+?);", block)
        assert m, "no client_max_body_size in /api/uploads/ block"
        assert _parse_size(m.group(1)) >= max_category_limit()

    def test_uploads_has_long_timeouts(self):
        # A 2 GB upload on a slow link exceeds the default 300s — the upload block
        # must raise proxy_read/send_timeout well above it.
        text = _nginx_text()
        block = text.split("location /api/uploads/", 1)[1].split("location", 1)[0]
        for directive in ("proxy_read_timeout", "proxy_send_timeout"):
            m = re.search(rf"{directive}\s+(\d+)s;", block)
            assert m and int(m.group(1)) >= 3600, f"{directive} too low for a 2 GB upload"

    def test_uploads_request_buffering_off(self):
        # Stream the body to the gateway; don't spool the whole 2 GB to disk on nginx.
        text = _nginx_text()
        block = text.split("location /api/uploads/", 1)[1].split("location", 1)[0]
        assert "proxy_request_buffering off" in block

    def test_general_api_cap_covers_single_post(self):
        # The general /api/ body cap must cover a single-POST upload (threshold +
        # overhead) but need NOT reach the 2 GB video cap (large media → /api/uploads/).
        text = _nginx_text()
        # take the general /api/ block (the one that isn't /api/uploads/)
        after = text.split("location /api/uploads/", 1)[1]
        gen = after.split("location /api/ {", 1)[1].split("}", 1)[0]
        m = re.search(r"client_max_body_size\s+(\S+?);", gen)
        assert m, "general /api/ block missing client_max_body_size"
        cap = _parse_size(m.group(1))
        assert cap >= single_post_threshold()


class TestAiohttpCeiling:
    def test_single_post_ceiling_tracks_threshold(self):
        from personalclaw.dashboard.server import _single_post_ceiling

        ceiling = _single_post_ceiling()
        # Covers a single-POST upload + multipart overhead, but stays tight (not 2 GB).
        assert ceiling >= single_post_threshold()
        assert ceiling < max_category_limit()
