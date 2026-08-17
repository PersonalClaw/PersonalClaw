"""Pack dashboard routes (AGENT-PACKS §3.4/§4/§9, AP-3 + AP-4).

The read + re-run surface behind the installed-pack ledger:

* ``GET /api/packs/installed`` — the durable ledger (:mod:`packs.installed`): each installed
  pack, its components, its connector resolutions + ``connector_missing:<name>`` markers, and
  whether a re-runnable setup interview is pending.
* ``POST /api/packs/{name}/finish-setup`` — the "Finish setup" chip's backend (§3.4). A
  pack's ``setup/SKILL.md`` is a normal skill; "finishing setup" is invoking it. This route
  confirms the pack has a setup skill and returns its committed id + the chat slash-command
  the FE opens — it never runs the skill server-side (the interview runs under normal tool
  approval in a chat), and it is re-runnable (the ledger keeps ``setup_pending`` true).

AP-4 adds the four pack KINDS' entry points, each one thin over a core function:

* ``GET /api/packs/bundled`` / ``POST /api/packs/bundled/{name}/install`` — the two shipped
  Domain OS packs (§4.1), built from their authored source tree and imported through §3.
* ``POST /api/packs/{name}/roster/deploy`` — one-click team deploy (§4.2). Deploys the
  ``always`` tier ONLY; the response names the dormant tiers so the caller can show what was
  deliberately not hired.
* ``POST /api/packs/{name}/bindings`` — record one setup-interview answer (§3.4/§4.1). A
  ``folder`` binding must be an existing directory.
* ``POST /api/packs/prompt-card`` — the prompt-card importer (§4.3). Files a proposal for
  review; writes no entity.
* ``POST /api/packs/one-link`` — import a one-link JSON document (§2.3/§4.4) through the same
  §3 pipeline.

Kept deliberately thin: the pack store cards + export UI are AP-7's scope. Errors use the
shared envelope (``{"error": {"code", "message"}}``) so a caller branches on a stable code.
"""

from __future__ import annotations

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


def _err(code: str, message: str, status: int) -> web.Response:
    """The shared error envelope. ``code`` is append-only and never reworded once shipped."""
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


async def api_packs_installed(request: web.Request) -> web.Response:
    """List installed packs with connector-resolution, roster + setup-binding state."""
    from personalclaw.packs.installed import load_installed

    packs = [p.to_view() for p in load_installed()]
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


async def api_packs_bundled(request: web.Request) -> web.Response:
    """List the Domain OS packs shipped in this build (§4.1)."""
    from personalclaw.packs.bundled import bundled_packs

    return web.json_response({"packs": [p.to_dict() for p in bundled_packs()]})


async def api_pack_bundled_install(request: web.Request) -> web.Response:
    """Build a shipped Domain OS pack and import it through the §3 pipeline.

    The archive is built into a SYSTEM tempdir and deleted afterwards: a bundled pack is
    reproducible from the wheel, so keeping the ZIP would be state nothing reads. Trust tier is
    BUILTIN (§3.5 ``_tier_for_origin`` — this pack came out of the installed package, not a
    URL), and `consent` is not a parameter: a BUILTIN pack that scanned DANGEROUS is a release
    defect, and the import refuses it regardless.
    """
    import shutil
    import tempfile
    from pathlib import Path

    from personalclaw.packs.bundled import BundledPackError, build_bundled, get_bundled
    from personalclaw.packs.import_ import PackImportRefused, import_pack
    from personalclaw.supply_chain import TrustTier

    name = request.match_info.get("name", "")
    if get_bundled(name) is None:
        return _err("pack_not_bundled", f"no bundled pack named {name!r}", 404)
    body = await _json_body(request)
    if body is None:
        return _err("invalid_json", "request body must be a JSON object", 400)
    staging = Path(tempfile.mkdtemp(prefix="pclaw-bundled-"))
    try:
        archive = build_bundled(name, staging / f"{name}.pclaw")
        plan = import_pack(
            archive,
            tier=TrustTier.BUILTIN,
            connector_choices=_connector_choices(body),
        )
    except BundledPackError as exc:
        logger.error("bundled pack %s failed to build: %s", name, exc)
        return _err("pack_build_failed", str(exc), 500)
    except PackImportRefused as exc:
        return _err(
            f"pack_refused_{exc.reason}",
            str(exc),
            409 if exc.reason == "needs_consent" else 400,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return web.json_response({"ok": True, "plan": plan.to_dict()})


async def api_pack_roster_deploy(request: web.Request) -> web.Response:
    """One-click team deploy: promote a pack's ``always`` roster tier (§4.2).

    Only the ``always`` tier is deployed — the response's ``dormant`` list names every staged
    member deliberately left un-hired, so a UI can say so rather than implying the whole roster
    went live.
    """
    from personalclaw.packs.installed import load_installed
    from personalclaw.packs.roster import deploy_roster, load_roster

    name = request.match_info.get("name", "")
    if not any(p.name == name for p in load_installed()):
        return _err("pack_not_installed", f"pack not installed: {name}", 404)
    entries, _ = load_roster(name)
    if not entries:
        return _err("pack_has_no_roster", f"pack {name!r} ships no roster", 404)
    result = deploy_roster(name)
    return web.json_response({"ok": True, "pack": name, **result})


async def api_pack_bindings(request: web.Request) -> web.Response:
    """Record one setup-interview answer (§3.4/§4.1) — the folder the pack will read."""
    from personalclaw.packs.installed import BindingError, bind_answer

    name = request.match_info.get("name", "")
    body = await _json_body(request)
    if body is None:
        return _err("invalid_json", "request body must be a JSON object", 400)
    key = str(body.get("key", "") or "")
    value = str(body.get("value", "") or "")
    if not key:
        return _err("binding_key_required", "a `key` is required", 400)
    try:
        pack = bind_answer(name, key, value)
    except BindingError as exc:
        message = str(exc)
        status = 404 if message.startswith("pack not installed") else 400
        return _err("binding_rejected", message, status)
    return web.json_response({"ok": True, "pack": pack.name, "unbound": pack.unbound})


async def api_pack_prompt_card(request: web.Request) -> web.Response:
    """Import a pasted prompt card (§4.3) — files a proposal, writes no entity."""
    from personalclaw.packs.prompt_cards import PromptCardError, import_prompt_card

    body = await _json_body(request)
    if body is None:
        return _err("invalid_json", "request body must be a JSON object", 400)
    try:
        result = await import_prompt_card(str(body.get("card", "") or ""))
    except PromptCardError as exc:
        return _err("prompt_card_rejected", str(exc), 400)
    except Exception as exc:  # noqa: BLE001 — a model/provider failure is the caller's answer
        logger.warning("prompt-card import failed: %s", exc, exc_info=True)
        return _err("prompt_card_failed", f"{type(exc).__name__}: {exc}", 502)
    return web.json_response({"ok": True, **result})


async def api_pack_one_link(request: web.Request) -> web.Response:
    """Import a one-link JSON document (§2.3/§4.4) through the same §3 pipeline."""
    from personalclaw.packs.import_ import PackImportRefused
    from personalclaw.packs.onelink import OneLinkError, import_onelink

    body = await _json_body(request)
    if body is None:
        return _err("invalid_json", "request body must be a JSON object", 400)
    doc = body.get("link")
    if not isinstance(doc, dict):
        return _err("one_link_required", "a `link` object is required", 400)
    try:
        plan = import_onelink(
            doc,
            consent=bool(body.get("consent", False)),
            connector_choices=_connector_choices(body),
        )
    except OneLinkError as exc:
        return _err("one_link_rejected", str(exc), 400)
    except PackImportRefused as exc:
        return _err(
            f"pack_refused_{exc.reason}",
            str(exc),
            409 if exc.reason == "needs_consent" else 400,
        )
    return web.json_response({"ok": True, "plan": plan.to_dict()})


async def _json_body(request: web.Request) -> dict | None:
    """The request's JSON object, or None when there isn't one. An EMPTY body is ``{}`` —
    every route here has usable defaults, so requiring a body would be ceremony."""
    if not request.can_read_body:
        return {}
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


def _connector_choices(body: dict) -> dict | None:
    """The ``connector_choices`` map (§3.3), or None when the caller supplied none."""
    raw = body.get("connector_choices")
    if not isinstance(raw, dict):
        return None
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def register_pack_routes(app: web.Application) -> None:
    """Mount the AP-3 ledger/finish-setup routes + the AP-4 pack-kind entry points."""
    app.router.add_get("/api/packs/installed", api_packs_installed)
    app.router.add_get("/api/packs/bundled", api_packs_bundled)
    app.router.add_post("/api/packs/bundled/{name}/install", api_pack_bundled_install)
    app.router.add_post("/api/packs/prompt-card", api_pack_prompt_card)
    app.router.add_post("/api/packs/one-link", api_pack_one_link)
    app.router.add_post("/api/packs/{name}/finish-setup", api_pack_finish_setup)
    app.router.add_post("/api/packs/{name}/roster/deploy", api_pack_roster_deploy)
    app.router.add_post("/api/packs/{name}/bindings", api_pack_bindings)
