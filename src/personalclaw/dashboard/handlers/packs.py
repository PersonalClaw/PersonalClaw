"""Pack dashboard routes (AGENT-PACKS §3.4/§9, AP-3).

The read + re-run surface behind the installed-pack ledger:

* ``GET /api/packs/installed`` — the durable ledger (:mod:`packs.installed`): each installed
  pack, its components, its connector resolutions + ``connector_missing:<name>`` markers, and
  whether a re-runnable setup interview is pending.
* ``POST /api/packs/{name}/finish-setup`` — the "Finish setup" chip's backend (§3.4). A
  pack's ``setup/SKILL.md`` is a normal skill; "finishing setup" is invoking it. This route
  confirms the pack has a setup skill and returns its committed id + the chat slash-command
  the FE opens — it never runs the skill server-side (the interview runs under normal tool
  approval in a chat), and it is re-runnable (the ledger keeps ``setup_pending`` true).

Kept deliberately thin: the pack export/import UI + store cards are AP-7. AP-3 wires only
the ledger reader and the re-run affordance the setup-skill done_when requires.
"""

from __future__ import annotations

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


async def api_packs_installed(request: web.Request) -> web.Response:
    """List installed packs with connector-resolution + setup state."""
    from personalclaw.packs.installed import load_installed

    packs = [p.to_dict() for p in load_installed()]
    return web.json_response({"packs": packs})


async def api_pack_finish_setup(request: web.Request) -> web.Response:
    """Return a pack's re-runnable setup interview (the "Finish setup" chip)."""
    name = request.match_info.get("name", "")
    from personalclaw.packs.installed import load_installed

    pack = next((p for p in load_installed() if p.name == name), None)
    if pack is None:
        return web.json_response({"error": f"pack not installed: {name}"}, status=404)
    if not pack.setup_skill:
        return web.json_response({"error": "pack has no setup skill"}, status=404)
    # The interview IS a skill invocation — re-runnable, under normal tool approval in chat.
    # We hand the FE the committed skill id + the slash-command that opens it; we never run
    # it server-side (no new execution surface, §3.4). setup_pending stays true.
    return web.json_response(
        {
            "pack": pack.name,
            "setup_skill": pack.setup_skill,
            "command": f"/{pack.setup_skill}",
            "pending": pack.setup_pending,
        }
    )


def register_pack_routes(app: web.Application) -> None:
    """Mount the AP-3 pack ledger + finish-setup routes."""
    app.router.add_get("/api/packs/installed", api_packs_installed)
    app.router.add_post("/api/packs/{name}/finish-setup", api_pack_finish_setup)
