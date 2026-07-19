"""Tests for the favicon handler (serves /claw.svg from the dist root)."""

from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web


@pytest.mark.asyncio
async def test_favicon_serves_through_symlinked_dist(tmp_path):
    """In dev, static/dist is a symlink to web/dist (via
    ensure_dev_dist_symlink). The favicon handler must serve the real file
    through the symlink (dist-root files have no static route; without this
    handler the request falls through to the SPA fallback, which serves
    index.html as text/html — a broken favicon)."""
    from personalclaw.dashboard.handlers import core

    real_dist = tmp_path / "real-dist"
    real_dist.mkdir()
    (real_dist / "claw.svg").write_text("<svg/>")

    link = tmp_path / "linked-dist"
    link.symlink_to(real_dist)

    req = MagicMock()
    with patch.object(core, "_DIST_DIR", link):
        resp = await core.favicon(req)
    assert isinstance(resp, web.FileResponse)


@pytest.mark.asyncio
async def test_favicon_404_when_missing(tmp_path):
    """No claw.svg in dist → clean 404 (not a SPA-fallback HTML response)."""
    from personalclaw.dashboard.handlers import core

    dist = tmp_path / "dist"
    dist.mkdir()

    req = MagicMock()
    with patch.object(core, "_DIST_DIR", dist):
        with pytest.raises(web.HTTPNotFound):
            await core.favicon(req)
