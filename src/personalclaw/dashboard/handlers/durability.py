"""Durability endpoints: schedule status, on-demand jobs (§3) + the DSAR surface (§6).

§6's four routes live here rather than under ``/api/portability`` because they are one
surface, not two: an export, its import, the archive it lands in and that archive's
restore are the same user story ("get my data out / put it back"). Shipping a second
``/api/portability/export`` beside ``POST /api/durability/export`` would be two answers
to one question, so the portability routes were RETIRED into these (DAS-10) — there is
one export endpoint, one import endpoint, one archive list and one restore.

**Who may call these.** All four refuse an app-scoped token, matching
``apps.api_app_token``'s precedent (``request["app"]`` present → 403). An export hands
the caller everything PersonalClaw knows about the user and an import/restore rewrites
it; neither is ever an installed app's business, and least privilege here is the owner's
own session or nothing. The plan does not name a caller for these routes, so this is the
*restrictive* reading of that silence, stated rather than assumed.

**Confirmation contract.** Both destructive verbs are two-step by construction:
omitting ``mode`` returns the PLAN and changes nothing (the shape
``api_durability_restore`` already established), and ``mode=replace`` additionally
requires ``confirm: true`` — the one verb that deletes a user's current state should not
be reachable by a single mistyped field.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path, PurePosixPath

from aiohttp import web
from aiohttp.multipart import BodyPartReader

from personalclaw.http_errors import json_error

logger = logging.getLogger(__name__)

# Jobs a caller may trigger by name. Closed set — this maps to real work on disk.
_RUNNABLE = ("export", "snapshot", "drill")


def _sel():
    from personalclaw.sel import sel

    return sel()


async def _read_upload_file(request: web.Request) -> tuple[Path | None, web.Response | None]:
    """Read a multipart ``file`` field into a temp file. Returns (path, None) or (None, error).

    Moved here from the retired ``handlers/portability.py`` — it was that module's only
    surviving part once its three routes folded into the §6 pair.
    """
    ctype = request.headers.get("Content-Type", "")
    if not ctype.lower().startswith("multipart/"):
        return None, web.json_response(
            {
                "error": {
                    "code": "multipart_required",
                    "message": "multipart/form-data with a 'file' field is required",
                }
            },
            status=400,
        )
    try:
        reader = await request.multipart()
    except (ValueError, AssertionError, RuntimeError) as exc:
        return None, web.json_response(
            {
                "error": {
                    "code": "bad_multipart",
                    "message": f"failed to parse multipart body: {exc}",
                }
            },
            status=400,
        )
    part = await reader.next()
    if part is None or not isinstance(part, BodyPartReader) or part.name != "file":
        return None, web.json_response(
            {"error": {"code": "file_required", "message": "file field required"}}, status=400
        )

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    try:
        while True:
            chunk = await part.read_chunk(65536)
            if not chunk:
                break
            tmp.write(chunk)
        tmp.close()
        return Path(tmp.name), None
    except Exception:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise


def _reject_app(request: web.Request) -> web.Response | None:
    """403 an app-scoped caller. See the module docstring's least-privilege note."""
    if request.get("app", ""):
        return web.json_response(
            {
                "error": {
                    "code": "owner_only",
                    "message": "apps may not export, import or restore whole-home state",
                }
            },
            status=403,
        )
    return None


async def api_durability_status(request: web.Request) -> web.Response:
    """GET /api/durability/status — schedule state + what's due."""
    from personalclaw.durability import service

    status = await asyncio.get_event_loop().run_in_executor(None, service.status)
    return web.json_response(status)


async def api_durability_archive(request: web.Request) -> web.Response:
    """GET /api/durability/archive — the archive browser's list (§6).

    Each row carries what the plan asks a browser to show: date, size, whether the
    retention tiers currently KEEP or PRUNE it (so the policy is inspectable before it
    deletes anything), the **per-domain counts read from that archive's own manifest**,
    and the **validate status from the last restore drill** for the archive the drill
    actually exercised. A row whose manifest predates the per-domain block reports
    ``domains: null`` rather than zeros — "no counts recorded" and "an empty archive"
    must not render identically.

    Replaces ``GET /api/durability/snapshots``: same list, the fields §6 requires.
    """
    from personalclaw.config.loader import AppConfig
    from personalclaw.durability import archive as arch
    from personalclaw.durability import retention, service
    from personalclaw.snapshot import _default_snapshot_dir

    def _collect() -> dict:
        directory = Path(_default_snapshot_dir())
        snapshots = retention.list_snapshots(directory)
        try:
            cfg = AppConfig.load().durability
            daily, weekly, monthly = cfg.keep_daily, cfg.keep_weekly, cfg.keep_monthly
        except Exception:  # noqa: BLE001
            daily, weekly, monthly = (
                retention.DEFAULT_DAILY,
                retention.DEFAULT_WEEKLY,
                retention.DEFAULT_MONTHLY,
            )
        keep, prune = retention.plan_retention(
            snapshots, daily=daily, weekly=weekly, monthly=monthly
        )
        keep_names = {s.name for s in keep}
        drill = service.last_drill()
        return {
            "directory": str(directory),
            "archives": [
                {
                    "id": s.name,
                    "name": s.name,
                    "taken_at": s.taken_at.isoformat(),
                    "size": s.size,
                    "retained": s.name in keep_names,
                    "domains": arch.domain_counts(s.path),
                    "validate": drill if drill.get("archive") == s.name else None,
                }
                for s in snapshots
            ],
            "would_prune": [s.name for s in prune],
            "tiers": {"daily": daily, "weekly": weekly, "monthly": monthly},
            "last_drill": drill,
        }

    payload = await asyncio.get_event_loop().run_in_executor(None, _collect)
    return web.json_response(payload)


async def api_durability_export(request: web.Request) -> web.Response:
    """POST /api/durability/export {domains?} — the DSAR export (§6).

    Body ``{"domains": ["memory"]}`` scopes the zip to those inventory domains;
    omitting it (or an empty body) is the full "give me everything PersonalClaw knows
    about me" export. ``secret ∪ derived`` is excluded on every path and the zip's
    MANIFEST v3 names the excluded entries, so the exclusion is auditable from the
    artifact rather than only from this code.

    POST rather than GET because the domain selection is a body, and because an export
    is not a cacheable idempotent read of a URL — it reads the user's entire home.
    """
    denied = _reject_app(request)
    if denied is not None:
        return denied
    from personalclaw.portability import create_export_zip

    body: dict = {}
    if request.can_read_body:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response(
                {"error": {"code": "bad_body", "message": "body must be JSON"}}, status=400
            )
    if not isinstance(body, dict):
        return web.json_response(
            {"error": {"code": "bad_body", "message": "body must be a JSON object"}}, status=400
        )
    domains = body.get("domains")
    if domains is not None:
        if not isinstance(domains, list) or not all(isinstance(d, str) for d in domains):
            return web.json_response(
                {"error": {"code": "bad_domains", "message": "domains must be a list of strings"}},
                status=400,
            )

    try:
        zip_bytes, manifest = await asyncio.to_thread(create_export_zip, domains)
    except ValueError as exc:
        # An unknown domain. Naming the valid set beats a bare 400 — the caller is a
        # settings panel or a script, and both can act on the list.
        return web.json_response(
            {"error": {"code": "unknown_domain", "message": str(exc)}}, status=400
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("durability export failed")
        _audit_api(request, "durability.export", "error", str(exc))
        return web.json_response(
            {"error": {"code": "export_failed", "message": "export failed"}}, status=500
        )

    scope = manifest.get("scope", "full")
    _audit_api(
        request,
        "durability.export",
        "allowed",
        f"scope={scope},domains={','.join(manifest.get('domains') or [])},bytes={len(zip_bytes)}",
    )
    stamp = str(manifest.get("created_at", "")).replace(":", "").replace("-", "")
    tag = "" if scope == "full" else "-" + "-".join(manifest.get("domains") or [])
    return web.Response(
        body=zip_bytes,
        content_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="personalclaw-export{tag}-{stamp}.zip"',
            "Content-Length": str(len(zip_bytes)),
        },
    )


async def api_durability_import(request: web.Request) -> web.Response:
    """POST /api/durability/import — validate, then apply, an export zip (§6).

    Multipart with a ``file`` field. ``mode`` (query or form) is ``merge`` | ``replace``;
    **omitting it validates and returns the manifest, changing nothing** — the same
    plan-first contract ``api_durability_restore`` uses, because both verbs can rewrite
    a home. ``mode=replace`` additionally requires ``confirm=true``.

    Accepts MANIFEST v1, v2 and v3. A v3 archive is checksum-VERIFIED before anything is
    written (its manifest declares per-member sha256); v1/v2 carry no hashes, so they
    import unverified and the response says so via ``manifest.verified``.
    """
    denied = _reject_app(request)
    if denied is not None:
        return denied
    from personalclaw.portability import apply_import_zip, validate_import_zip

    mode = request.query.get("mode")
    confirm = str(request.query.get("confirm", "")).lower() in ("1", "true", "yes")
    if mode is not None:
        mode = mode.strip().lower()
        if mode not in ("merge", "replace"):
            return web.json_response(
                {"error": {"code": "bad_mode", "message": "mode must be merge or replace"}},
                status=400,
            )
        if mode == "replace" and not confirm:
            return web.json_response(
                {
                    "error": {
                        "code": "confirm_required",
                        "message": ("mode=replace overwrites this home; resend with confirm=true"),
                    }
                },
                status=409,
            )

    zip_path, err_resp = await _read_upload_file(request)
    if err_resp is not None:
        return err_resp
    assert zip_path is not None

    try:
        ok, error, manifest = await asyncio.to_thread(validate_import_zip, zip_path)
        if not ok:
            _audit_api(request, "durability.import", "denied", error)
            return web.json_response(
                {"ok": False, "error": {"code": "invalid_archive", "message": error}}, status=400
            )
        if mode is None:
            # Plan-only: the caller sees what the archive claims and asks again.
            _audit_api(request, "durability.import", "allowed", "plan")
            return web.json_response({"ok": True, "applied": False, "manifest": manifest})

        summary = await asyncio.to_thread(apply_import_zip, zip_path, mode)
        _audit_api(
            request,
            "durability.import",
            "allowed",
            f"mode={mode},items={len(summary.get('items', []))}",
        )
        return web.json_response(
            {"ok": True, "applied": True, "summary": summary, "manifest": manifest}
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("durability import failed")
        _audit_api(request, "durability.import", "error", str(exc))
        # The exception text is internals (a traceback's last words), not user copy — it
        # lives in the log line and audit row above; the wire carries the way forward.
        return json_error(
            "import_failed",
            message="The archive could not be imported. "
            "Check the gateway log for the failure detail.",
            status=500,
            ok=False,
        )
    finally:
        zip_path.unlink(missing_ok=True)


def _audit_api(request: web.Request, operation: str, outcome: str, resources: str) -> None:
    """One audit call shape for the §6 routes. Never raises — an audit failure must not
    turn a successful export into a 500, and the SEL write is already best-effort."""
    try:
        _sel().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=operation,
            outcome=outcome,
            resources=resources[:200],
        )
    except Exception:  # noqa: BLE001
        logger.debug("durability: audit failed", exc_info=True)


async def api_durability_run(request: web.Request) -> web.Response:
    """POST /api/durability/run {job} — run one backup job now.

    For "back up before I do something risky" and for verifying the schedule works
    without waiting a month for the drill. Each job is single-flighted, so a
    concurrent scheduled run reports a skip rather than colliding.
    """
    from personalclaw.durability import service

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "body must be JSON"}, status=400)
    job = str(body.get("job", "") or "").strip().lower()
    if job not in _RUNNABLE:
        return web.json_response(
            {"error": f"job must be one of: {', '.join(_RUNNABLE)}"}, status=400
        )

    state = request.app.get("state")
    notifier = getattr(state, "notify", None) if state is not None else None
    runners = {
        "export": service.run_incremental_export,
        "snapshot": service.run_nightly_snapshot,
        "drill": lambda: service.run_restore_drill(notifier=notifier),
    }
    result = await asyncio.get_event_loop().run_in_executor(None, runners[job])
    if job == "drill" and not result.skipped:
        # An on-demand drill is a real drill: its verdict must reach the archive browser
        # exactly as the scheduled tick's does, or "run drill now" would show a stale
        # pass next to an archive that just failed.
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: service.persist_drill_result(result)
        )
    _audit_api(
        request,
        f"durability_run:{job}",
        "allowed" if result.ok else "denied",
        result.detail,
    )
    # 200 even on a failed job: the request succeeded and the report IS the answer.
    # A 500 would imply the endpoint broke rather than the backup.
    return web.json_response(result.to_dict())


async def api_durability_archive_restore(request: web.Request) -> web.Response:
    """POST /api/durability/archive/{id}/restore {mode?, components?, confirm?} — §6.

    🔴 WHY THIS EXISTS. T2-M3 names it and it was absent: the API had `status`, `snapshots`
    and `run`, so a user could take a backup from the dashboard and could not restore one.
    Backup without restore
    is the shape this plan exists to remove ("recoverable through first-class restore endpoints
    — not
    archaeology").

    **Omitting `mode` returns the PLAN and changes nothing.** That is the safe default for an
    endpoint that can overwrite a home: a caller must see what would happen and then ask again
    with an explicit
    mode. `mode=replace` is therefore always deliberate, never inferred — and it now also
    requires ``confirm: true``, so the one verb that deletes the user's current state takes two
    independent signals, not one field.

    🔴 **`mode=replace` IS REFUSED HERE, UNCONDITIONALLY** — not delegated to
    `snapshot.restore_apply`'s guard. DISCOVERED BY DRIVING IT (DAS-10): a
    `mode=replace&confirm=true` request to a gateway on ``--port 10188`` returned **200 and
    performed the replace**, over the live home, while serving the request. Cause:
    `snapshot._is_gateway_running()` probes ``DASHBOARD_PORT`` — the *configured* port — so on
    any non-default port the guard probes a socket nobody is listening on and reports "not
    running". The docstring it inherited claimed the opposite.

    A socket probe is the wrong instrument from inside the process anyway: this handler
    executing IS proof the gateway is up, so the answer is known without asking the network.
    Refusing here is exact and cannot be defeated by a port. There is no ``--force`` mirror on
    purpose: overriding it is a local operator decision at a terminal (`personalclaw restore
    --replace --force`), never an HTTP parameter.

    Replaces ``POST /api/durability/restore``: the archive id moves into the path, which is
    where §6 puts it and what makes the archive browser's rows addressable.
    """
    denied = _reject_app(request)
    if denied is not None:
        return denied
    from personalclaw import snapshot as snap_mod

    body: dict = {}
    if request.can_read_body:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response(
                {"error": {"code": "bad_body", "message": "body must be JSON"}}, status=400
            )
    if not isinstance(body, dict):
        return web.json_response(
            {"error": {"code": "bad_body", "message": "body must be a JSON object"}}, status=400
        )

    raw = str(request.match_info.get("id", "") or "").strip()
    if not raw:
        return web.json_response(
            {"error": {"code": "archive_required", "message": "archive id is required"}}, status=400
        )

    mode = body.get("mode")
    if mode is not None:
        mode = str(mode).strip().lower()
        if mode not in ("merge", "replace"):
            return web.json_response(
                {"error": {"code": "bad_mode", "message": "mode must be merge or replace"}},
                status=400,
            )
        if mode == "replace":
            # See the docstring: the gateway is provably up (we are it), so a replace here
            # would rewrite state this process holds open. Refused before the confirm check
            # so the message names the real reason rather than sending the caller to add a
            # flag that still cannot work.
            _audit_api(request, "durability_restore:replace", "denied", "gateway_running")
            return web.json_response(
                {
                    "error": {
                        "code": "gateway_running",
                        "message": (
                            "a replace restore rewrites state this gateway holds open; stop "
                            "the gateway and run `personalclaw restore --replace` instead"
                        ),
                    }
                },
                status=409,
            )
        if body.get("confirm") is not True:
            # `merge` is non-destructive (copy-if-missing) but still writes into the live
            # home, so it is confirmed too — one signal for "look", two for "write".
            return web.json_response(
                {
                    "error": {
                        "code": "confirm_required",
                        "message": (
                            "a restore writes into this home; resend with confirm: true "
                            "(omit mode to see the plan first)"
                        ),
                    }
                },
                status=409,
            )

    components = body.get("components")
    if components is not None:
        if not isinstance(components, list) or not all(isinstance(c, str) for c in components):
            return web.json_response(
                {
                    "error": {
                        "code": "bad_components",
                        "message": "components must be a list of strings",
                    }
                },
                status=400,
            )
        unknown = [c for c in components if c not in snap_mod.VALID_COMPONENTS]
        if unknown:
            return web.json_response(
                {
                    "error": {
                        "code": "unknown_component",
                        "message": f"unknown component(s): {', '.join(sorted(unknown))}",
                    }
                },
                status=400,
            )

    # Path containment: the archive must be one WE produced, named from the snapshot directory.
    # Accepting an arbitrary path over HTTP would let a caller point a restore at any tar on disk.
    from personalclaw.snapshot import _default_snapshot_dir

    snap_dir = Path(_default_snapshot_dir()).resolve()
    candidate = (snap_dir / Path(raw).name).resolve()
    if candidate.parent != snap_dir or not candidate.is_file():
        return web.json_response(
            {
                "error": {
                    "code": "archive_not_found",
                    "message": "archive not found in the snapshot directory",
                }
            },
            status=404,
        )

    def _run() -> dict:
        if mode is None:
            return snap_mod.restore_plan(candidate, components)
        return snap_mod.restore_apply(candidate, mode, components)

    try:
        result = await asyncio.get_event_loop().run_in_executor(None, _run)
    except Exception as exc:  # noqa: BLE001
        # A raise here is a CRASH, not a refusal: restore_apply reports its designed
        # refusals (gateway running, bad archive shape) as ok:false VALUES on the result
        # path below. So this answers 500 with guidance, and the exception text stays in
        # the log and audit row — internals are not user copy.
        logger.warning("durability restore failed", exc_info=True)
        _audit_api(request, "durability.restore", "error", str(exc))
        return json_error(
            "restore_failed",
            message="The restore attempt failed. "
            "Check the gateway log for the failure detail before retrying.",
            status=500,
        )

    _audit_api(
        request,
        f"durability_restore:{mode or 'plan'}",
        "allowed" if result.get("ok", True) else "denied",
        candidate.name,
    )
    return web.json_response(result, status=200 if result.get("ok", True) else 409)


# ── the conflict review queue (§4.2 item 2/3, DAS-10) ────────────────────────
#
# `durability/conflicts.py` shipped the detector and the durable queue with no route and no
# screen, so a both-sides-edited divergence was recorded, held the local row, and then had no
# way to be seen or decided. These two routes are that surface's back end.
#
# Owner-only and confirm-gated for the same reasons as the §6 four: the payload is the user's
# own rows on both machines, and a resolve WRITES one of them into the live store.

#: Cap on rows returned in one page. A conflict is rare by construction (§4.2), but a record
#: carries two whole rows plus a proposal, so an unbounded list could be megabytes.
_CONFLICT_LIMIT = 50


async def api_durability_conflicts(request: web.Request) -> web.Response:
    """GET /api/durability/conflicts?surface=&status=&limit= — the review queue (§4.2).

    Returns the records themselves plus the COUNTS the surface needs to be honest:

    * ``counts.by_surface`` — how the §4.2 item-3 routing actually landed. The Durability
      panel reviews its own surface; memory- and knowledge-domain conflicts route to theirs,
      and a panel that showed only its own slice with no count for the others would read as
      "no conflicts" while two waited elsewhere.
    * ``sync`` — whether a transport is even configured. Zero conflicts on an unconfigured
      instance means "sync has never run", not "sync is healthy", and those must not render
      identically.
    """
    denied = _reject_app(request)
    if denied is not None:
        return denied
    from personalclaw.durability import conflicts as conflicts_mod
    from personalclaw.durability import service

    surface = str(request.query.get("surface", "") or "").strip()
    status = str(request.query.get("status", "") or "").strip()
    try:
        limit = max(1, min(_CONFLICT_LIMIT, int(request.query.get("limit", _CONFLICT_LIMIT))))
    except (TypeError, ValueError):
        limit = _CONFLICT_LIMIT

    def _collect() -> dict:
        home = Path(service.active_home())
        queue = conflicts_mod.ConflictQueue(home)
        everything = queue.items()
        by_surface: dict[str, int] = {}
        for rec in everything:
            if rec.status == conflicts_mod.STATUS_NEEDS_REVIEW:
                by_surface[rec.surface] = by_surface.get(rec.surface, 0) + 1
        selected = queue.items(surface=surface, status=status)
        # The PUBLIC status projection, not the config object: it already resolves the
        # encryption tri-state and is the one shape the panel's sync section reads.
        sync = dict(service.status().get("sync") or {})
        return {
            "conflicts": [rec.to_dict() for rec in selected[-limit:]],
            "truncated": len(selected) > limit,
            "counts": {
                "total": len(everything),
                "needs_review": sum(
                    1 for r in everything if r.status == conflicts_mod.STATUS_NEEDS_REVIEW
                ),
                "by_surface": by_surface,
                "selected": len(selected),
            },
            "surfaces": {
                "memory": conflicts_mod.SURFACE_MEMORY,
                "knowledge": conflicts_mod.SURFACE_KNOWLEDGE,
                "durability": conflicts_mod.SURFACE_DURABILITY,
            },
            "sync": {
                "enabled": bool(sync.get("enabled", False)),
                "transport": str(sync.get("transport", "") or ""),
                "configured": bool(sync.get("transport", "")),
            },
        }

    payload = await asyncio.get_event_loop().run_in_executor(None, _collect)
    return web.json_response(payload)


async def api_durability_conflict_resolve(request: web.Request) -> web.Response:
    """POST /api/durability/conflicts/{id}/resolve {choice, confirm} — apply one decision.

    ``confirm: true`` is required for EVERY choice, including ``keep_local``: each one closes
    a held divergence, and two of the three overwrite a row the other machine also edited.
    One signal for "look" (the GET above), two for "write" — the same contract the §6
    import/restore verbs use.

    Refusals are typed (:func:`durability.conflict_resolve.resolve_conflict` owns the codes)
    and carry HTTP status by class: 400 for a malformed ask, 404 for an unknown record, 409
    for a state that makes the ask impossible, 500 only when the write itself failed.
    """
    denied = _reject_app(request)
    if denied is not None:
        return denied
    from datetime import datetime, timezone

    from personalclaw.durability import conflict_resolve as resolver
    from personalclaw.durability import service

    body: dict = {}
    if request.can_read_body:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response(
                {"error": {"code": "bad_body", "message": "body must be JSON"}}, status=400
            )
    if not isinstance(body, dict):
        return web.json_response(
            {"error": {"code": "bad_body", "message": "body must be a JSON object"}}, status=400
        )

    record_id = str(request.match_info.get("id", "") or "").strip()
    if not record_id:
        return web.json_response(
            {"error": {"code": "conflict_required", "message": "conflict id is required"}},
            status=400,
        )
    choice = str(body.get("choice", "") or "").strip()
    if body.get("confirm") is not True:
        _audit_api(request, "durability_conflict_resolve", "denied", f"{record_id}:unconfirmed")
        return web.json_response(
            {
                "ok": False,
                "error": {
                    "code": "confirm_required",
                    "message": (
                        "resolving a conflict writes the chosen version into this home; "
                        "resend with confirm: true"
                    ),
                },
            },
            status=409,
        )

    now = datetime.now(timezone.utc).isoformat()
    outcome = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: resolver.resolve_conflict(Path(service.active_home()), record_id, choice, now=now),
    )
    if not outcome.ok:
        status = {
            "unknown_choice": 400,
            "not_found": 404,
            "already_resolved": 409,
            "unknown_entry": 409,
            "unsupported_kind": 409,
            "no_version": 409,
            "write_failed": 500,
        }.get(outcome.code, 400)
        _audit_api(request, "durability_conflict_resolve", "denied", f"{record_id}:{outcome.code}")
        return web.json_response(
            {"ok": False, "error": {"code": outcome.code, "message": outcome.message}},
            status=status,
        )
    _audit_api(request, "durability_conflict_resolve", "allowed", f"{record_id}:{choice}")
    return web.json_response(
        {
            "ok": True,
            "choice": outcome.choice,
            "id": outcome.record_id,
            "written": outcome.written,
            "removed": outcome.removed,
            "conflict": outcome.record,
        }
    )


# ── §5 time-travel ─────────────────────────────────────────────────────────
#
# Three routes, one confirmation contract. Rollback and revert share ONE endpoint
# per operation and are **two-phase by construction**: a request without
# ``confirm`` returns the preview and changes nothing, and the preview is what
# hands back the ``expected_head`` a confirming request must echo. So "preview is
# mandatory" is not a convention the UI is trusted to follow — a caller cannot
# produce a valid confirm without having received a preview, and a preview that
# went stale (the history moved underneath it) is refused rather than applied to
# a tree the user never saw.


def _history_root(request: web.Request):
    """Resolve ``{root}`` to a declared root, or return an error response."""
    from personalclaw.durability import service, state_history

    raw = str(request.match_info.get("root", "") or "").strip()
    root = state_history.root_by_id(raw, home=service.active_home())
    if root is None:
        known = ", ".join(r.id for r in state_history.roots(service.active_home()))
        return None, web.json_response(
            {
                "error": {
                    "code": "unknown_root",
                    "message": f"unknown history root {raw!r}; known roots: {known}",
                }
            },
            status=404,
        )
    return root, None


async def _history_body(request: web.Request) -> tuple[dict, web.Response | None]:
    if not request.can_read_body:
        return {}, None
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}, web.json_response(
            {"error": {"code": "bad_body", "message": "body must be JSON"}}, status=400
        )
    if not isinstance(body, dict):
        return {}, web.json_response(
            {"error": {"code": "bad_body", "message": "body must be a JSON object"}}, status=400
        )
    return body, None


def _history_paths(raw: object, *, field: str) -> tuple[list[str], web.Response | None]:
    """Normalize a repo-relative path subset from a request body.

    ``None`` and ``[]`` both mean "the whole root" and normalize to ``[]``, so the
    two-phase path-set comparison below cannot be tripped by a client that omits
    the field where another sends an empty array.

    A non-list, or a non-string element, is a typed 400 rather than a coercion: a
    caller that sent ``{"paths": "config.json"}`` may have meant one path or may
    have meant a bug, and guessing on a destructive verb is how a user ends up
    restoring something they never selected. Shape problems answer ``bad_paths``;
    a path that cannot be a repo-relative selector answers ``invalid_path`` and
    NAMES the offending value, because "one of your paths is wrong" is not an
    actionable message.

    The result is sorted and de-duplicated: order must not matter to the request,
    and ``["a", "a"]`` is the same selection as ``["a"]``.
    """
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return [], web.json_response(
            {
                "error": {
                    "code": "bad_paths",
                    "message": f"{field} must be an array of repo-relative path strings",
                }
            },
            status=400,
        )
    out: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            return [], web.json_response(
                {
                    "error": {
                        "code": "bad_paths",
                        "message": (
                            f"{field} must contain only strings; "
                            f"got {type(item).__name__} in {field}"
                        ),
                    }
                },
                status=400,
            )
        # Backslashes fold to "/" exactly as the history module folds them, so the
        # two normalizations cannot disagree about what set the user named.
        raw_value = item.replace("\\", "/").strip()
        if raw_value.startswith(("~", "/")) or "\0" in raw_value:
            return [], _invalid_path(field, item)
        pure = PurePosixPath(raw_value)
        normalized = pure.as_posix()
        if pure.is_absolute() or ".." in pure.parts or normalized in ("", "."):
            return [], _invalid_path(field, item)
        out.add(normalized)
    return sorted(out), None


def _invalid_path(field: str, value: str) -> web.Response:
    """The one shape for "that path cannot be a selector", naming the value."""
    return web.json_response(
        {
            "error": {
                "code": "invalid_path",
                "message": (
                    f"{field} entry {value!r} is not a repo-relative path inside this "
                    "root; paths must be relative and may not contain '..'"
                ),
            },
            "path": value,
        },
        status=400,
    )


def _commit_previews(root, sha: str, *, operation: str, home: Path) -> bool:
    """Whether *sha* previews at all for the WHOLE root.

    Classification only: the module reports an unresolvable commit and an
    unacceptable path subset through the same exception type, and the two are
    different mistakes with different fixes. If the whole-root preview succeeds,
    the subset is what was refused.
    """
    from personalclaw.durability import state_history

    try:
        state_history.preview(root, sha, operation=operation, home=home)
    except state_history.HistoryError:
        return False
    return True


async def api_durability_history(request: web.Request) -> web.Response:
    """GET /api/durability/history — per-root repo status for the Time Travel panel."""
    denied = _reject_app(request)
    if denied is not None:
        return denied
    from personalclaw.durability import service, state_history

    cfg = service._cfg()
    return web.json_response(
        {
            "enabled": bool(getattr(cfg, "time_travel", True)),
            **state_history.status(home=service.active_home()),
        }
    )


async def api_durability_history_timeline(request: web.Request) -> web.Response:
    """GET /api/durability/history/{root}/timeline?limit=&unattended= — the timeline."""
    denied = _reject_app(request)
    if denied is not None:
        return denied
    from personalclaw.durability import service, state_history

    root, err = _history_root(request)
    if err is not None:
        return err
    try:
        limit = max(1, min(500, int(request.query.get("limit", "50"))))
    except (TypeError, ValueError):
        limit = 50
    unattended = str(request.query.get("unattended", "")).lower() in ("1", "true", "yes")
    home = service.active_home()
    try:
        entries = state_history.timeline(root, limit=limit, unattended_only=unattended, home=home)
        forward = state_history.forward_refs(root, home=home)
        commits = state_history.commit_count(root, home=home)
    except state_history.HistoryError as exc:
        return web.json_response(
            {"error": {"code": "history_unavailable", "message": str(exc)}}, status=503
        )
    return web.json_response(
        {
            "root": root.id,
            "label": root.label,
            "commits": commits,
            "entries": entries,
            "forward_refs": forward,
        }
    )


async def api_durability_history_operate(request: web.Request) -> web.Response:
    """POST /api/durability/history/{root}/{op} {sha, paths?, confirm?, expected_head?,
    expected_paths?}.

    ``op`` is ``rollback`` or ``revert``. Without ``confirm`` this returns the
    preview and touches nothing; with ``confirm`` it requires ``expected_head`` to
    match the root's current HEAD so a preview the user read cannot be applied to
    a tree that moved since.

    ``paths`` restricts the operation to a repo-relative subset (omitted, ``null``
    or ``[]`` all mean the whole root, which is the shipped behaviour). A subset
    makes ``expected_head`` alone an insufficient binding between a preview and
    its confirm: the same HEAD would happily accept a whole-root confirm behind a
    two-file preview, or a ten-file confirm behind a two-file one, and the user
    would be shown one thing while another was applied. So phase one also returns
    ``expected_paths`` and a confirming request must echo it — the normalized SETS
    must match, or the call is refused with ``preview_paths_mismatch``. That is
    what keeps "the preview is mandatory by construction" true for a subset.
    """
    denied = _reject_app(request)
    if denied is not None:
        return denied
    from personalclaw.durability import service, state_history

    root, err = _history_root(request)
    if err is not None:
        return err
    operation = str(request.match_info.get("op", "") or "").strip()
    if operation not in ("rollback", "revert"):
        return web.json_response(
            {
                "error": {
                    "code": "unknown_operation",
                    "message": "operation must be 'rollback' or 'revert'",
                }
            },
            status=404,
        )
    body, err = await _history_body(request)
    if err is not None:
        return err
    sha = str(body.get("sha", "") or "").strip()
    if not sha:
        return web.json_response(
            {"error": {"code": "sha_required", "message": "a commit id is required"}}, status=400
        )
    paths, err = _history_paths(body.get("paths"), field="paths")
    if err is not None:
        return err
    expected_paths, err = _history_paths(body.get("expected_paths"), field="expected_paths")
    if err is not None:
        return err
    home = service.active_home()
    try:
        prev = state_history.preview(root, sha, operation=operation, paths=paths, home=home)
    except state_history.HistoryError as exc:
        # Two failures arrive through one exception type, and they are not the same
        # user's mistake: an unresolvable commit is a 404, while a path the module
        # will not accept is a 400 the user fixes by fixing the path. Classify by
        # re-previewing WITHOUT the subset — one extra call, only on the error path.
        if paths and _commit_previews(root, sha, operation=operation, home=home):
            _audit_api(
                request,
                f"durability.history_{operation}",
                "denied",
                f"{root.id}:bad-paths:paths={len(paths)}",
            )
            return web.json_response(
                {
                    "error": {
                        "code": "invalid_path",
                        "message": f"{exc} (requested: {', '.join(paths)})",
                    },
                    "paths": paths,
                },
                status=400,
            )
        _audit_api(request, f"durability.history_{operation}", "denied", f"{root.id}:{exc}")
        return web.json_response(
            {"error": {"code": "unknown_commit", "message": str(exc)}}, status=404
        )

    if not body.get("confirm"):
        # Phase one. The preview IS the response, and it carries both tokens phase
        # two must echo: the head it was taken at and the path set it describes.
        return web.json_response(
            {
                "confirmed": False,
                "expected_head": prev["head"],
                "expected_paths": list(prev.get("paths", paths)),
                "preview": prev,
            }
        )

    expected = str(body.get("expected_head", "") or "").strip()
    if expected != prev["head"]:
        _audit_api(
            request,
            f"durability.history_{operation}",
            "denied",
            f"{root.id}:stale:paths={len(paths)}",
        )
        return web.json_response(
            {
                "error": {
                    "code": "preview_stale",
                    "message": (
                        "the history moved since this preview was taken; "
                        "review the new preview before confirming"
                    ),
                },
                "expected_head": prev["head"],
                "expected_paths": list(prev.get("paths", paths)),
                "preview": prev,
            },
            status=409,
        )

    if set(expected_paths) != set(paths):
        # The head matched, so the tree did not move — but the SELECTION did. Applying
        # this would act on a path set the user was never shown, which is the same
        # defect as a stale head wearing a different hat. Refused, nothing touched.
        _audit_api(
            request,
            f"durability.history_{operation}",
            "denied",
            f"{root.id}:paths-mismatch:paths={len(paths)}:expected={len(expected_paths)}",
        )
        return web.json_response(
            {
                "error": {
                    "code": "preview_paths_mismatch",
                    "message": (
                        "this confirm names a different set of paths than the preview it "
                        "cites; re-take the preview for the paths you mean to restore"
                    ),
                },
                "expected_head": prev["head"],
                "expected_paths": list(prev.get("paths", paths)),
                "preview": prev,
            },
            status=409,
        )

    try:
        if operation == "rollback":
            result = state_history.rollback(root, sha, paths=paths, home=home)
        else:
            result = state_history.revert(root, sha, paths=paths, home=home)
    except state_history.OverlapError as exc:
        _audit_api(
            request, "durability.history_revert", "denied", f"{root.id}:overlap:paths={len(paths)}"
        )
        return web.json_response(
            {
                "error": {
                    "code": "revert_overlap",
                    "message": str(exc),
                },
                "files": exc.files,
            },
            status=409,
        )
    except state_history.HistoryError as exc:
        _audit_api(request, f"durability.history_{operation}", "error", f"{root.id}:{exc}")
        return web.json_response(
            {"error": {"code": "history_failed", "message": str(exc)}}, status=500
        )
    # The COUNT, never the names: an audit line is not a place to dump a user's file
    # names, but a reader still has to be able to tell a subset restore from a
    # whole-root one, and ``paths=0`` says "whole root".
    _audit_api(
        request,
        f"durability.history_{operation}",
        "allowed",
        f"{root.id}:{sha[:12]}:paths={len(paths)}",
    )
    # Config and skills live in process memory too, so the caller must be told a
    # reload is needed rather than being left with a stale runtime.
    result["reload_required"] = root.id in ("config", "skills", "prompts")
    result["ok"] = True
    return web.json_response(result)
