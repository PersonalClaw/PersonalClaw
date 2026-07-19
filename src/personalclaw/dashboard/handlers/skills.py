"""Skills marketplace API handlers.

Routes:
    GET  /api/skills                             — list locally installed skills
    GET  /api/skills/marketplaces                — list registered marketplaces
    GET  /api/skills/search?q=...&marketplace=.. — search a marketplace
    POST /api/skills/install                     — install a skill
    DELETE /api/skills/:name                     — remove a local skill
"""

import fnmatch
import logging
import shutil
from pathlib import Path
from typing import Any

from aiohttp import web

from personalclaw.skills.marketplace import DEFAULT_SKILLS_INSTALL_PATH

logger = logging.getLogger(__name__)

# Skill-file read caps (responsiveness / DoS guard for the directory browser).
SKILL_FILE_MAX_BYTES = 1_048_576  # 1 MiB per returned file
SKILL_FILES_MAX = 500             # max tree entries returned


def _sel_log(op: str, outcome: str, resources: str, request: web.Request) -> None:
    try:
        from personalclaw.sel import sel as _s
        _s().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=op, outcome=outcome,
            source="skills", resources=resources,
        )
    except Exception:
        pass


def _loaded_by_agents(skill_keys: list[str]) -> dict[str, list[str]]:
    """Map each known skill key → the agent names that load it (Agent entity).

    Two sources, unioned:
      (a) ``AgentProfile.skills`` lists — the native per-agent skill list
          (config ``agents`` section). Primary, cheap, no filesystem walk.
      (b) ``resources`` ``skill://`` globs on agent JSON files (the ACP-agent
          layout) — fnmatched against each known skill key, guarding each
          agent-file read with ``is_sensitive_path``.

    Empty list for a skill means "loaded via triggers/always only" — honest, not
    an error.
    """
    out: dict[str, list[str]] = {key: [] for key in skill_keys}

    def _add(skill_key: str, agent_name: str) -> None:
        bucket = out.get(skill_key)
        if bucket is not None and agent_name not in bucket:
            bucket.append(agent_name)

    # (a) AgentProfile.skills — the native primary path.
    try:
        from personalclaw.config import AppConfig
        cfg = AppConfig.load()
        for agent_name, profile in (cfg.agents or {}).items():
            for skill_key in getattr(profile, "skills", None) or []:
                if isinstance(skill_key, str) and skill_key:
                    _add(skill_key, agent_name)
    except Exception:
        logger.debug("loaded_by_agents: AppConfig scan failed", exc_info=True)

    # (b) resources skill:// globs on agent JSON files (ACP-agent layout).
    try:
        import json

        from personalclaw.agent import AGENTS_DIR
        from personalclaw.security import is_sensitive_path

        if AGENTS_DIR.is_dir():
            for agent_file in sorted(AGENTS_DIR.glob("*.json")):
                if is_sensitive_path(str(agent_file)):
                    continue
                try:
                    data = json.loads(agent_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                agent_name = str(data.get("name") or agent_file.stem)
                resources = data.get("resources")
                if not isinstance(resources, list):
                    continue
                globs = [
                    r[len("skill://"):]
                    for r in resources
                    if isinstance(r, str) and r.startswith("skill://")
                ]
                if not globs:
                    continue
                for skill_key in skill_keys:
                    if any(fnmatch.fnmatch(skill_key, g) for g in globs):
                        _add(skill_key, agent_name)
    except Exception:
        logger.debug("loaded_by_agents: agent-resources scan failed", exc_info=True)

    return {k: sorted(v) for k, v in out.items()}


def _parse_always(skill_md: Path) -> bool:
    """Extract the 'always' field from SKILL.md frontmatter."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    import re
    for line in text[3:end].splitlines():
        m = re.match(r"^always:\s*(.+)$", line)
        if m:
            return m.group(1).strip().lower() in ("true", "yes", "1")
    return False


async def api_skills_list(request: web.Request) -> web.Response:
    """GET /api/skills — list locally installed skills from all discovery paths.

    Each skill carries an ``integrity`` field from the S6 lint (``verify_skill_integrity``):
    ``intact`` (on-disk hashes match the install-time ``.pclaw-lock.json`` baseline),
    ``tampered`` (a locked file changed/went missing or an unexpected file appeared), or
    ``unverified`` (no lock — a bundled or hand-placed skill, not a failure)."""
    from personalclaw.agent import _all_skill_paths
    from personalclaw.skills.marketplace import (
        _SKILL_FILENAME,
        _parse_description,
        verify_skill_integrity,
    )
    from personalclaw.skills.native import _bundled_root

    bundled_path = str(_bundled_root())

    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base_str in _all_skill_paths():
        base = Path(base_str)
        if not base.is_dir():
            continue
        is_bundled = base_str == bundled_path
        for entry in sorted(base.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / _SKILL_FILENAME
            if not skill_md.is_file():
                continue
            name = entry.name
            if name in seen:
                continue
            seen.add(name)
            rep = verify_skill_integrity(entry)
            integrity = "unverified" if rep.unlocked else ("intact" if rep.ok else "tampered")
            skills.append({
                "key": name,
                "name": name,
                "description": _parse_description(skill_md),
                "always": _parse_always(skill_md),
                "path": str(skill_md),
                "source": "bundled" if is_bundled else "local",
                "type": "bundled" if is_bundled else "installed",
                "integrity": integrity,
            })
    # Annotate which agents load each skill (PersonalClaw Agent entity).
    by_agent = _loaded_by_agents([s["key"] for s in skills])
    for s in skills:
        s["loaded_by_agents"] = by_agent.get(s["key"], [])

    # Agent-local tier (skill-agent-local-tier): each configured agent may carry
    # its own skills under ~/.personalclaw/agents/<slug>/skills/ that override
    # global for that agent only. They're per-agent (a slug can repeat across
    # agents), so they're listed separately with source="agent-local" + the owner,
    # NOT merged into the global-dedup set above.
    try:
        from personalclaw.agents.defaults import DEFAULT_NATIVE_AGENT_NAME
        from personalclaw.config.loader import AppConfig
        from personalclaw.skills.loader import agent_skills_dir

        # The default agent's runtime name (what build_message passes) is the
        # canonical DEFAULT_NATIVE_AGENT_NAME, not its config key ('default'), so
        # include it explicitly alongside the configured agents — the listing must
        # match the dir the injection path actually resolves.
        agent_names = [DEFAULT_NATIVE_AGENT_NAME, *AppConfig.load().agents.keys()]
        seen_agent_dirs: set[str] = set()
        for ag_name in agent_names:
            if not ag_name:
                continue
            adir = agent_skills_dir(ag_name)
            if str(adir) in seen_agent_dirs:
                continue  # dedup: distinct names may normalize to one slug
            seen_agent_dirs.add(str(adir))
            if not adir.is_dir():
                continue
            for entry in sorted(adir.iterdir()):
                if not entry.is_dir():
                    continue
                skill_md = entry / _SKILL_FILENAME
                if not skill_md.is_file():
                    continue
                rep = verify_skill_integrity(entry)
                integrity = "unverified" if rep.unlocked else ("intact" if rep.ok else "tampered")
                skills.append({
                    "key": f"{ag_name}/{entry.name}",
                    "name": entry.name,
                    "description": _parse_description(skill_md),
                    "always": _parse_always(skill_md),
                    "path": str(skill_md),
                    "source": "agent-local",
                    "type": "agent-local",
                    "integrity": integrity,
                    "agent": ag_name,
                    "loaded_by_agents": [ag_name],
                })
    except Exception:
        logger.debug("agent-local skill listing failed", exc_info=True)
    return web.json_response(skills)


async def api_skills_marketplaces(request: web.Request) -> web.Response:
    """GET /api/skills/marketplaces — list registered skill marketplaces."""
    from personalclaw.skills.marketplace import get_default_skills_registry
    return web.json_response(get_default_skills_registry().info())


async def api_skills_search(request: web.Request) -> web.Response:
    """GET /api/skills/search — search across all registered skill providers.

    Fans out the query to every registered marketplace and merges results.
    An optional ``marketplace`` param restricts to a single provider.
    """
    query = request.rel_url.query.get("q", "").strip()
    if not query:
        return web.json_response({"error": "q parameter required"}, status=400)
    marketplace_name = request.rel_url.query.get("marketplace", "")
    limit = min(int(request.rel_url.query.get("limit", "20")), 200)

    from personalclaw.skills.marketplace import get_default_skills_registry
    registry = get_default_skills_registry()

    if marketplace_name:
        try:
            mp = registry.get(marketplace_name)
        except KeyError:
            return web.json_response(
                {"error": f"Marketplace '{marketplace_name}' not registered"}, status=404
            )
        try:
            results = mp.search(query, limit=limit)
            return web.json_response({"results": [r.to_dict() for r in results]})
        except Exception as exc:
            logger.warning("skills search failed for %s: %s", marketplace_name, exc)
            return web.json_response({"error": str(exc)[:500]}, status=500)

    return web.json_response({"results": [r.to_dict() for r in search_marketplaces(query, limit=limit)]})


def search_marketplaces(query: str, limit: int = 20) -> list:
    """Fan a query out to every registered marketplace, drop already-installed
    skills, and return ``SkillEntry`` objects sorted by install count.

    Shared by the skills-search endpoint and the goal-loop intake (the planner
    auto-searches for installable skills during classify). Never raises — a
    failing marketplace is logged and skipped.
    """
    from personalclaw.skills.marketplace import get_default_skills_registry
    from personalclaw.skills.loader import SkillsLoader

    registry = get_default_skills_registry()
    installed_names = {s["key"] for s in SkillsLoader(install_builtins=False).list_skills()}

    all_results = []
    for name in registry.list():
        if name == "installed":
            continue
        try:
            mp = registry.get(name)
            all_results.extend(mp.search(query, limit=limit))
        except Exception as exc:
            logger.warning("skills search failed for %s: %s", name, exc)

    filtered = [r for r in all_results if r.id not in installed_names and r.name not in installed_names]
    filtered.sort(key=lambda r: r.installs, reverse=True)
    return filtered[:limit]


async def api_skills_marketplace_detail(request: web.Request) -> web.Response:
    """GET /api/skills/marketplace/detail?id=...&marketplace=...

    Fetch the full marketplace skill detail — SKILL.md content, parsed
    frontmatter, audit status, and any other metadata the marketplace exposes.
    Used by the dashboard to render a rich preview before install.
    """
    skill_id = request.rel_url.query.get("id", "").strip()
    if not skill_id:
        return web.json_response({"error": "id parameter required"}, status=400)
    marketplace_name = request.rel_url.query.get("marketplace", "skills.sh").strip()

    from personalclaw.skills.marketplace import get_default_skills_registry
    try:
        mp = get_default_skills_registry().get(marketplace_name)
    except KeyError:
        return web.json_response(
            {"error": f"Marketplace '{marketplace_name}' not registered"}, status=404
        )

    try:
        detail = mp.fetch(skill_id)
    except Exception as exc:
        logger.warning("skills detail fetch failed for %s/%s: %s", marketplace_name, skill_id, exc)
        return web.json_response({"error": str(exc)[:500]}, status=500)

    skill_md = detail.skill_md() or ""
    # Parse the SKILL.md frontmatter so the dashboard can render structured fields
    # (name, description, triggers, tags, version, author, license).
    frontmatter: dict[str, Any] = {}
    body = skill_md
    if skill_md.startswith("---"):
        end = skill_md.find("\n---", 3)
        if end != -1:
            fm_text = skill_md[3:end].strip()
            body = skill_md[end + 4:].lstrip("\n")
            for line in fm_text.split("\n"):
                if ":" in line:
                    key, _, value = line.partition(":")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key:
                        frontmatter[key] = value

    # JSON-safe file view: paths + a binary marker only. Raw ``data`` bytes from a binary
    # entry aren't JSON-serializable, and the preview UI only needs the tree — file
    # contents are fetched on demand via the per-file browser endpoint.
    file_view = [
        {"path": f.get("path", ""), "binary": "data" in f}
        for f in detail.files
    ]
    return web.json_response({
        "id": detail.id,
        "name": detail.name,
        "audit_status": detail.audit_status,
        "files": file_view,
        "frontmatter": frontmatter,
        "body": body,
        "marketplace": marketplace_name,
    })


def _safe_skill_name(name: str) -> bool:
    """Reject path-traversal / absolute skill keys."""
    return bool(name) and ".." not in name and "\\" not in name and not name.startswith("/")


def _resolve_skill_root(name: str) -> Path | None:
    """Return the discovery dir that owns ``<name>/SKILL.md``, or None.

    Mirrors ``api_skills_list``/``api_skills_delete`` — first match across
    ``_all_skill_paths()`` wins (project > user > agents > bundled).
    """
    from personalclaw.agent import _all_skill_paths
    for base_str in _all_skill_paths():
        base = Path(base_str)
        if (base / name / "SKILL.md").is_file():
            return base
    return None


async def api_skill_files(request: web.Request) -> web.Response:
    """GET /api/skills/{name}/files[?path=<rel>] — provider-backed file browser.

    No ``path``: return the skill's file tree ``{name, files: [{path, size}]}``
    (contents omitted). With ``path``: return that one file ``{name, path,
    content}``. Reads go through the Skills entity provider's ``fetch()``, which
    only ever enumerates under one resolved skill root — that single-root read
    is the containment boundary. ``is_sensitive_path`` + size/entry caps are the
    defense-in-depth backstop. Every access (incl. rejections) is SEL-audited.
    """
    from personalclaw.security import is_sensitive_path
    from personalclaw.skills.native import NativeSkillsMarketplace

    name = request.match_info["name"]
    rel = request.rel_url.query.get("path", "").strip()

    if not _safe_skill_name(name) or (rel and (".." in rel or rel.startswith("/") or "\\" in rel)):
        _sel_log("skills.files", "denied", f"unsafe:{name}:{rel}", request)
        return web.json_response({"error": "invalid skill or path"}, status=400)

    root = _resolve_skill_root(name)
    if root is None:
        _sel_log("skills.files", "denied", f"notfound:{name}", request)
        return web.json_response({"error": f"Skill '{name}' not found"}, status=404)

    try:
        detail = NativeSkillsMarketplace(root=root).fetch(name)
    except Exception:
        _sel_log("skills.files", "denied", f"fetch-failed:{name}", request)
        return web.json_response({"error": f"Skill '{name}' not found"}, status=404)

    skill_dir = root / name

    def _is_sensitive(entry_path: str) -> bool:
        return is_sensitive_path(str(skill_dir / entry_path))

    if not rel:
        # Tree view — paths + sizes, sensitive entries dropped, capped.
        files: list[dict[str, Any]] = []
        for f in detail.files:
            p = f.get("path", "")
            if not p or _is_sensitive(p):
                continue
            files.append({"path": p, "size": len(f.get("contents", "").encode("utf-8"))})
            if len(files) >= SKILL_FILES_MAX:
                break
        _sel_log("skills.files", "ok", f"tree:{name}:{len(files)}", request)
        return web.json_response({"name": name, "files": files})

    # Single-file view.
    match = next((f for f in detail.files if f.get("path") == rel), None)
    if match is None:
        _sel_log("skills.files", "denied", f"file-notfound:{name}:{rel}", request)
        return web.json_response({"error": f"File '{rel}' not found"}, status=404)
    if _is_sensitive(rel):
        _sel_log("skills.files", "denied", f"sensitive:{name}:{rel}", request)
        return web.json_response({"error": "access denied"}, status=403)
    content = match.get("contents", "")
    if len(content.encode("utf-8")) > SKILL_FILE_MAX_BYTES:
        _sel_log("skills.files", "denied", f"too-large:{name}:{rel}", request)
        return web.json_response({"error": "file too large"}, status=413)
    _sel_log("skills.files", "ok", f"file:{name}:{rel}", request)
    return web.json_response({"name": name, "path": rel, "content": content})


async def api_skills_install(request: web.Request) -> web.Response:
    """POST /api/skills/install — install a skill from a marketplace.

    Body: ``{id: "<skill-id>", marketplace: "skills.sh", target?: "..."}``.
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    skill_id = str(body.get("id", "")).strip()
    if not skill_id:
        return web.json_response({"error": "id is required"}, status=400)
    marketplace_name = str(body.get("marketplace", "skills.sh"))
    target_str = body.get("target", "")
    target = Path(target_str) if target_str else DEFAULT_SKILLS_INSTALL_PATH
    # Explicit override for an overridable WARNING verdict — a calculated risk the user
    # takes. A DANGEROUS verdict is NEVER overridable, force or not (the security floor).
    force = bool(body.get("force", False))

    from personalclaw.skills.marketplace import (
        SkillInstallRefused,
        get_default_skills_registry,
    )
    registry = get_default_skills_registry()
    try:
        registry.get(marketplace_name)
    except KeyError:
        return web.json_response(
            {"error": f"Marketplace '{marketplace_name}' not registered"}, status=404
        )
    try:
        result = registry.install_guarded(marketplace_name, skill_id, target, force=force)
        _sel_log("skills.install", "ok", f"{marketplace_name}/{skill_id}", request)
        return web.json_response(
            {"ok": True, "path": str(result.path), "scan": result.report.to_dict()},
            status=201,
        )
    except SkillInstallRefused as exc:
        # 409 = a WARNING the user can re-attempt with force=true; 403 = a DANGEROUS
        # verdict that no force overrides. The findings power the "N warnings" UX.
        status = 403 if exc.dangerous else 409
        return web.json_response(
            {
                "error": str(exc),
                "verdict": exc.report.verdict.value,
                "tier": exc.report.tier.value,
                "overridable": not exc.dangerous,
                "scan": exc.report.to_dict(),
            },
            status=status,
        )
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception as exc:
        logger.warning("skills install failed: %s", exc)
        return web.json_response({"error": str(exc)[:500]}, status=500)


async def api_skills_delete(request: web.Request) -> web.Response:
    """DELETE /api/skills/:name — remove a locally installed skill."""
    name = request.match_info["name"]
    # Reject path-traversal before any rmtree — ``name`` is a single URL segment
    # but ``..`` alone still resolves to a skill dir's parent.
    if not _safe_skill_name(name):
        _sel_log("skills.delete", "denied", f"unsafe:{name}", request)
        return web.json_response({"error": "invalid skill name"}, status=400)
    from personalclaw.agent import _all_skill_paths

    removed: str | None = None
    for base_str in _all_skill_paths():
        skill_dir = Path(base_str) / name
        if skill_dir.is_dir():
            shutil.rmtree(skill_dir)
            removed = str(skill_dir)
            break

    if removed is None:
        return web.json_response({"error": f"Skill '{name}' not found"}, status=404)

    _sel_log("skills.delete", "ok", name, request)
    return web.json_response({"ok": True, "removed": removed})


async def api_skill_verify(request: web.Request) -> web.Response:
    """POST /api/skills/:name/verify — S6 integrity lint for one installed skill.

    Compares on-disk file hashes against the install-time ``.pclaw-lock.json`` baseline
    and returns the drift (``mutated``/``missing``/``added``) so a tamper is visible from
    the dashboard, not just the CLI. ``unlocked`` = no baseline (bundled/hand-placed)."""
    name = request.match_info["name"]
    if not _safe_skill_name(name):
        _sel_log("skills.verify", "denied", f"unsafe:{name}", request)
        return web.json_response({"error": "invalid skill name"}, status=400)

    root = _resolve_skill_root(name)
    if root is None:
        _sel_log("skills.verify", "denied", f"notfound:{name}", request)
        return web.json_response({"error": f"Skill '{name}' not found"}, status=404)

    from personalclaw.skills.marketplace import verify_skill_integrity

    rep = verify_skill_integrity(root / name)
    status = "unverified" if rep.unlocked else ("intact" if rep.ok else "tampered")
    _sel_log("skills.verify", "ok" if rep.ok or rep.unlocked else "tampered", f"{name}:{status}", request)
    return web.json_response({
        "name": name,
        "integrity": status,
        "ok": rep.ok,
        "unlocked": rep.unlocked,
        "mutated": rep.mutated,
        "missing": rep.missing,
        "added": rep.added,
        "summary": rep.summary(),
    })


# ── Ephemeral session skills (skill-ephemeral-promotion) ─────────────────────

async def api_ephemeral_skills_list(request: web.Request) -> web.Response:
    """GET /api/skills/ephemeral/{session} — the session-live drafts awaiting a
    promote/forget decision (drives the end-of-session modal)."""
    from personalclaw.skills import ephemeral

    session = request.match_info.get("session", "")
    drafts = [
        {"slug": d.slug, "title": d.title, "body": d.body, "created_at": d.created_at}
        for d in ephemeral.list_drafts(session)
    ]
    return web.json_response({"drafts": drafts})


async def api_ephemeral_skill_promote(request: web.Request) -> web.Response:
    """POST /api/skills/ephemeral/{session}/promote — promote ONE draft to a tier.

    Body: {slug, scope: "agent"|"global", agent?, title?, body?}. Edits (title/body)
    override the draft. Refuses the bundled/read-only tier. Clears the draft on success."""
    from personalclaw.skills import ephemeral

    session = request.match_info.get("session", "")
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be an object"}, status=400)
    slug = str(body.get("slug", "")).strip()
    scope = str(body.get("scope", "")).strip()
    if not slug or scope not in ("agent", "global"):
        return web.json_response({"error": "slug + scope('agent'|'global') required"}, status=400)
    try:
        name = ephemeral.promote(
            session, slug, scope,
            agent=(str(body.get("agent", "")).strip() or None),
            title=(str(body["title"]).strip() if body.get("title") else None),
            body=(str(body["body"]) if body.get("body") else None),
        )
    except ephemeral.PromotionError as exc:
        _sel_log("skills.ephemeral_promote", "rejected", f"{slug}:{exc}", request)
        return web.json_response({"error": str(exc)}, status=409)
    _sel_log("skills.ephemeral_promote", "ok", f"{scope}:{name}", request)
    return web.json_response({"ok": True, "name": name, "scope": scope})


async def api_ephemeral_skill_discard(request: web.Request) -> web.Response:
    """DELETE /api/skills/ephemeral/{session}/{slug} — forget one draft, or
    (slug='*') clear the whole session's drafts (the modal's 'forget all')."""
    from personalclaw.skills import ephemeral

    session = request.match_info.get("session", "")
    slug = request.match_info.get("slug", "")
    if slug == "*":
        n = ephemeral.clear_session(session)
        return web.json_response({"ok": True, "cleared": n})
    ok = ephemeral.discard(session, slug)
    return web.json_response({"ok": ok})


# ── Skill proposals inbox (skill-evolution-proposal-only) ────────────────────

async def api_skill_proposals_list(request: web.Request) -> web.Response:
    """GET /api/skills/proposals — the pending autonomous-synthesis proposals
    awaiting human review (propose-only; nothing here is installed)."""
    from personalclaw.skills import proposals

    return web.json_response({"proposals": [p.summary() for p in proposals.list_pending()]})


async def api_skill_proposal_detail(request: web.Request) -> web.Response:
    """GET /api/skills/proposals/{id} — full proposal incl. procedure + fenced source."""
    from personalclaw.skills import proposals

    prop = proposals.get(request.match_info.get("id", ""))
    if prop is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(prop.to_dict())


async def api_skill_proposal_accept(request: web.Request) -> web.Response:
    """POST /api/skills/proposals/{id}/accept — install into the live auto/ tier
    (with optional reviewer edits) + clear the proposal."""
    from personalclaw.skills import proposals

    pid = request.match_info.get("id", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body if isinstance(body, dict) else {}
    try:
        name = proposals.accept(
            pid,
            description=(str(body["description"]) if body.get("description") else None),
            procedure_md=(str(body["procedure_md"]) if body.get("procedure_md") else None),
        )
    except proposals.AcceptError as exc:
        _sel_log("skills.proposal_accept", "rejected", f"{pid}:{exc}", request)
        return web.json_response({"error": str(exc)}, status=409)
    _sel_log("skills.proposal_accept", "ok", name, request)
    return web.json_response({"ok": True, "name": name})


async def api_skill_proposal_reject(request: web.Request) -> web.Response:
    """DELETE /api/skills/proposals/{id} — drop a proposal (never installed)."""
    from personalclaw.skills import proposals

    pid = request.match_info.get("id", "")
    ok = proposals.reject(pid)
    _sel_log("skills.proposal_reject", "ok" if ok else "rejected", pid, request)
    return web.json_response({"ok": ok})
