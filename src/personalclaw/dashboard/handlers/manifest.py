"""Self-description endpoint — serves the generated manifest (:mod:`personalclaw.manifest`)
that lets an agent driving PersonalClaw read the tool/route/provider surface instead
of guessing signatures. One source, two renderings: this live handler walks the
running route table; the build-time offline reference (S3) renders the same
``build_manifest()`` output."""

import logging

from aiohttp import web

from personalclaw.manifest import build_manifest

logger = logging.getLogger(__name__)


async def api_manifest(request: web.Request) -> web.Response:
    """GET /api/manifest — the machine-readable self-description of this instance.

    Generated live from the registries that own each part (tools, the running
    aiohttp route table, the extension-provider registry) — never a hand-kept
    inventory. ``tests.test_api_manifest_drift`` fails the build if a tool or route
    ships without a description here.
    """
    manifest = await build_manifest(request.app)
    return web.json_response(manifest)
