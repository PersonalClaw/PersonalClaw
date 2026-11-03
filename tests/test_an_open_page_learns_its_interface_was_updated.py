"""An open page learns that the gateway now serves a newer interface (#5901).

An update builds a new SPA and restarts the gateway, and a page that was open kept running the old
build until a lazy chunk it had never loaded was gone. The gateway now says which build it serves
twice: in the document a page boots from (a meta tag) and in a ``spa_build`` frame on every socket
open. A page that reconnects after a restart compares the two and offers Reload
(``web/src/app/UpdatedInterfaceNotice.tsx``). The frame's gate is in ``test_ws_app_event_gate.py``.

The build is the served ``index.html`` itself. It names every hashed entry asset, so it changes
whenever the build's output does, and it needs nothing from the build. ``PERSONALCLAW_SPA_DIST``
points a gateway at another build folder, and only the gateway's own environment can name one. The
browser spec that restarts a gateway into a changed build serves a copy through it
(``web/e2e/updatedInterface.spec.ts``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web

import personalclaw
from personalclaw.dashboard import owner_token_url
from personalclaw.frontend import (
    SPA_BUILD_META,
    SPA_DIST_ENV,
    served_spa_dist,
    spa_build_id,
)

_INDEX = "<!doctype html><html><head><title>PersonalClaw</title></head><body></body></html>"
_PACKAGED = Path(personalclaw.__file__).resolve().parent / "static" / "dist"


def _dist(root: Path, html: str = _INDEX) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(html, encoding="utf-8")
    return root


def _request(query: dict | None = None) -> MagicMock:
    req = MagicMock(spec=web.Request)
    req.path = "/"
    req.query = query or {}
    req.cookies = {}
    req.remote = "127.0.0.1"
    req.headers = {}
    req.method = "GET"
    return req


def test_the_build_is_the_served_index_and_follows_it(tmp_path: Path) -> None:
    dist = _dist(tmp_path / "dist")
    first = spa_build_id(dist)
    assert re.fullmatch(r"[0-9a-f]{16}", first), first
    assert spa_build_id(dist) == first, "the same file must answer the same build"
    assert spa_build_id(_dist(tmp_path / "copy")) == first, "the build is the file's bytes"

    index = dist / "index.html"
    index.write_text(_INDEX.replace("</body>", "<!-- rebuilt --></body>"), encoding="utf-8")
    stat = index.stat()
    os.utime(index, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert spa_build_id(dist) not in ("", first), "a rebuilt index is another build"


def test_a_folder_with_no_index_serves_no_build(tmp_path: Path) -> None:
    assert spa_build_id(tmp_path / "missing") == ""
    (tmp_path / "empty").mkdir()
    assert spa_build_id(tmp_path / "empty") == ""
    (tmp_path / "odd" / "index.html").mkdir(parents=True)
    assert spa_build_id(tmp_path / "odd") == "", "a folder named index.html is no build"


def test_the_gateway_serves_the_packaged_build_unless_its_environment_names_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import personalclaw.dashboard.handlers.core as core
    import personalclaw.dashboard.server as server

    assert SPA_DIST_ENV == "PERSONALCLAW_SPA_DIST"
    assert SPA_DIST_ENV not in os.environ, f"{SPA_DIST_ENV} is set in the environment of this run"
    # Both module constants: `server` routes /assets, /icons and the rest from its own, and
    # `handlers.core` serves the document, the worker and the fonts from its own.
    assert core._DIST_DIR == server._DIST_DIR == served_spa_dist() == _PACKAGED

    monkeypatch.setenv(SPA_DIST_ENV, str(tmp_path / "a-copy"))
    assert served_spa_dist() == (tmp_path / "a-copy").resolve()
    monkeypatch.setenv(SPA_DIST_ENV, "   ")
    assert served_spa_dist() == _PACKAGED, "a blank value names no folder"


@pytest.mark.asyncio
async def test_the_page_boots_knowing_the_build_it_was_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import personalclaw.dashboard.handlers.core as core

    dist = _dist(tmp_path / "dist")
    monkeypatch.setattr(core, "_DIST_DIR", dist)
    meta = f'<meta name="{SPA_BUILD_META}" content="{spa_build_id(dist)}">'

    html = (await core.index(_request())).text
    assert html == _INDEX.replace("<head>", f"<head>{meta}"), html

    # The token scrub still runs first in <head>, ahead of the meta as of every other tag.
    scrubbed = (await core.index(_request({"token": "OWNER-SECRET"}))).text
    assert scrubbed.startswith(f"<!doctype html><html><head><script {owner_token_url.SCRUB_ATTR}>")
    assert scrubbed.count(meta) == 1


@pytest.mark.asyncio
async def test_a_document_with_no_head_is_served_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import personalclaw.dashboard.handlers.core as core

    bare = "<!doctype html><title>PersonalClaw</title>"
    monkeypatch.setattr(core, "_DIST_DIR", _dist(tmp_path / "dist", bare))
    assert (await core.index(_request())).text == bare
