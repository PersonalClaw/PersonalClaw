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

from personalclaw.http_errors import json_error
from personalclaw.providers.failure_copy import relayed_failure_copy
from personalclaw.request_validation import json_object_body, require_string, string_field
from personalclaw.skills.marketplace import DEFAULT_SKILLS_INSTALL_PATH

logger = logging.getLogger(__name__)

# Skill-file read caps (responsiveness / DoS guard for the directory browser).
SKILL_FILE_MAX_BYTES = 1_048_576  # 1 MiB per returned file
SKILL_FILES_MAX = 500  # max tree entries returned


def _sel_log(op: str, outcome: str, resources: str, request: web.Request) -> None:
    try:
        from personalclaw.sel import sel as _s

        _s().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=op,
            outcome=outcome,
            source="skills",
            resources=resources,
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

        from personalclaw.agent import agents_dir
        from personalclaw.security import is_sensitive_path

        if agents_dir().is_dir():
            for agent_file in sorted(agents_dir().glob("*.json")):
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
                    r[len("skill://") :]
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
    """Extract the 'always' field from SKILL.md frontmatter.

    Delegates to the one parser rather than re-deriving it: this copy lacked the
    loader's BOM/leading-blank tolerance, so a BOM'd skill reported
    ``always: false`` regardless of what it actually declared.
    """
    from personalclaw.skills.loader import SkillsLoader

    try:
        text = skill_md.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    value = SkillsLoader._parse_frontmatter_text(text).get("always", "")
    return value.strip().lower() in ("true", "yes", "1")


def _parse_provenance(skill_md: Path) -> str:
    """Extract HOW the skill came to exist from SKILL.md frontmatter's ``source:``.

    This is deliberately NOT the listing's ``source`` field (#576). That one is derived
    from the directory the skill lives in — ``bundled`` / ``local`` / ``agent-local`` —
    and every surface that decides whether a skill is editable or deletable reads it, so
    letting frontmatter override it would make a taught skill read as a tier it is not.
    Provenance is the orthogonal fact the tier cannot carry: the auto-extractor writes
    ``source: auto``, promotion writes ``source: taught`` and the dashboard's create route
    writes ``source: dashboard``, and before this the first two were write-only — a skill the
    user explicitly taught the agent was indistinguishable from one dropped into the same
    directory by hand, on every surface.

    The returned vocabulary is CLOSED to the three values their writers actually produce.
    Frontmatter is free-form text a hand-authored skill can put anything in, and this
    value reaches the UI; passing it through would let an arbitrary string render as
    provenance. An unrecognized or absent value returns ``""`` — nothing recorded where the
    skill came from, which is what the absence of the marker has always meant.
    """
    from personalclaw.skills.ephemeral import TAUGHT_SKILL_SOURCE_VALUE
    from personalclaw.skills.loader import (
        AUTO_SKILL_SOURCE_VALUE,
        DASHBOARD_SKILL_SOURCE_VALUE,
        SkillsLoader,
    )

    try:
        text = skill_md.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    value = SkillsLoader._parse_frontmatter_text(text).get("source", "").strip().lower()
    known = (AUTO_SKILL_SOURCE_VALUE, TAUGHT_SKILL_SOURCE_VALUE, DASHBOARD_SKILL_SOURCE_VALUE)
    return value if value in known else ""


def _synthesis_producer(key: str, provenance: str) -> dict[str, str] | None:
    """Feedback-Signal producer meta for a SYNTHESIZED skill, or None (#1783).

    ``skills.surfacing.surface_skills`` withholds a matched skill whose identity
    ``("skill_synthesis", <key>)`` is in :func:`feedback.suppressed_producers` — the one
    membership in :data:`feedback.ENFORCED_SUPPRESSION_KINDS` that has a real surfacing
    gate behind it. Nothing ever stamped that identity onto a surface a user could thumb,
    so the pair could never enter the withholding set and a persistently-wrong synthesized
    skill kept surfacing forever. This is the missing writer's provenance half; the
    ``synthesized_skill`` thumbs in the skill inspector are the verdict half.

    ``auto`` ONLY, deliberately. ``taught`` shares the machinery but not the attribution:
    the user reviewed that draft and promoted it themselves, so a 👎 on it is a verdict on
    their own curation, not on the synthesizer — folding the two together would retire the
    extractor over skills it never chose. ``key`` is the loader-relative name
    (``auto/<slug>``), which is exactly what ``surface_skills`` matches on; the agent-local
    tier below is keyed ``<agent>/<name>`` for the listing, so it does NOT get a stamp —
    that string is not the identity the gate sees.
    """
    from personalclaw.skills.loader import AUTO_SKILL_SOURCE_VALUE

    if provenance != AUTO_SKILL_SOURCE_VALUE:
        return None
    return {"producer_kind": "skill_synthesis", "producer_id": key}


async def api_skills_list(request: web.Request) -> web.Response:
    """GET /api/skills — list locally installed skills from all discovery paths.

    Each skill carries an ``integrity`` field from the S6 lint (``verify_skill_integrity``):
    ``intact`` (on-disk hashes match the install-time ``.pclaw-lock.json`` baseline),
    ``tampered`` (a locked file changed/went missing or an unexpected file appeared), or
    ``unverified`` (no lock — any skill not installed from a marketplace, not a failure).

    Each also carries ``provenance`` — ``auto`` (extracted), ``taught`` (promoted from a
    session draft), ``dashboard`` (created with New skill), or ``""`` (nothing recorded) —
    read from the file's own frontmatter and kept separate from the directory-derived
    ``source``; see ``_parse_provenance``.

    An ``auto`` skill additionally carries ``feedback_producer`` (see
    :func:`_synthesis_producer`) so the inspector's ``synthesized_skill`` thumbs attribute a
    verdict to the synthesizer with no lookup — the identity Feedback-Signal's one enforced
    suppression gate keys on."""
    from personalclaw.agent import _all_skill_paths
    from personalclaw.skills.loader import iter_skill_files
    from personalclaw.skills.marketplace import _parse_description, verify_skill_integrity
    from personalclaw.skills.native import _bundled_root

    bundled_path = str(_bundled_root())

    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    for base_str in _all_skill_paths():
        base = Path(base_str)
        if not base.is_dir():
            continue
        is_bundled = base_str == bundled_path
        # 🔴 `iter_skill_files` — the LOADER's own enumeration, shared rather than re-derived.
        # This walked ONE level with `iterdir()`, so a NAMESPACE directory — `auto/`, which holds
        # every accepted skill proposal and has no `SKILL.md` of its own — was skipped whole.
        # Measured on a live instance: three `auto/*` skills were loaded into every agent's context
        # while `GET /api/skills` reported none of them, so they were un-inspectable and
        # un-deletable from the UI (#302). The loader and the listing must agree about what a skill
        # is; they were two answers to that question and only one of them decided what the user
        # could see. Calling the loader's function is what makes them agree by construction — a
        # second recursive copy here would just be a divergence waiting to happen.
        #
        # `name` is the path RELATIVE to the base, so `auto/loop-worker` keeps its namespace — which
        # is what `SkillsLoader` calls it, what the delete route takes, and what
        # `_loaded_by_agents` matches on. A bare basename would collide `auto/x` with a
        # top-level `x` and make the dedup set drop one of them.
        for name, skill_md in iter_skill_files(base):
            entry = skill_md.parent
            if name in seen:
                continue
            seen.add(name)
            rep = verify_skill_integrity(entry)
            integrity = "unverified" if rep.unlocked else ("intact" if rep.ok else "tampered")
            provenance = _parse_provenance(skill_md)
            row: dict[str, Any] = {
                "key": name,
                "name": name,
                "description": _parse_description(skill_md),
                "always": _parse_always(skill_md),
                "path": str(skill_md),
                "source": "bundled" if is_bundled else "local",
                "provenance": provenance,
                "type": "bundled" if is_bundled else "installed",
                "integrity": integrity,
            }
            producer = _synthesis_producer(name, provenance)
            if producer is not None:
                row["feedback_producer"] = producer
            skills.append(row)
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
        from personalclaw.skills.loader import agent_skills_dir, iter_skill_files

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
            # Recursive, via the loader's own enumeration — the agent-local tier is the same
            # directory shape as the global one, so a one-level walk hid a nested agent-local
            # skill exactly the way it hid `auto/*` above (#302's fourth site).
            for skill_name, skill_md in iter_skill_files(adir):
                entry = skill_md.parent
                rep = verify_skill_integrity(entry)
                integrity = "unverified" if rep.unlocked else ("intact" if rep.ok else "tampered")
                skills.append(
                    {
                        "key": f"{ag_name}/{skill_name}",
                        "name": skill_name,
                        "description": _parse_description(skill_md),
                        "always": _parse_always(skill_md),
                        "path": str(skill_md),
                        "source": "agent-local",
                        "provenance": _parse_provenance(skill_md),
                        "type": "agent-local",
                        "integrity": integrity,
                        "agent": ag_name,
                        "loaded_by_agents": [ag_name],
                    }
                )
    except Exception:
        logger.debug("agent-local skill listing failed", exc_info=True)
    return web.json_response(skills)


async def api_skills_marketplaces(request: web.Request) -> web.Response:
    """GET /api/skills/marketplaces — list registered skill marketplaces."""
    from personalclaw.skills.marketplace import get_default_skills_registry

    return web.json_response(get_default_skills_registry().info())


def _mark_installed(entries: "list") -> "list":
    """Stamp ``SkillEntry.installed`` from the user's own skills dir, in place.

    ONE reader of installed-ness for BOTH branches of the search endpoint. The scoped
    branch used to report ``installed: false`` for the very row the unscoped branch
    dropped as installed — the same skill, two answers — and the frontend cannot annotate
    a row the server never labelled. Matched on ``id`` OR ``name`` because a marketplace
    keyed on the bare directory name (``native``) and one keyed on ``owner/repo/skill``
    both have to line up against ``SkillsLoader.list_skills()``'s ``key``.
    """
    from personalclaw.skills.loader import SkillsLoader

    installed_names = {s["key"] for s in SkillsLoader(install_builtins=False).list_skills()}
    for entry in entries:
        entry.installed = entry.id in installed_names or entry.name in installed_names
    return entries


async def api_skills_search(request: web.Request) -> web.Response:
    """GET /api/skills/search — search across all registered skill providers.

    Fans out the query to every registered marketplace and merges results.
    An optional ``marketplace`` param restricts to a single provider.

    ``counts`` carries the per-source matched count computed BEFORE the global cap, so
    the store's source filter can show how many of a large catalog's skills match even
    when the merged, capped result list only shows the top few.
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
            results = _mark_installed(mp.search(query, limit=limit))
            return web.json_response(
                {
                    "results": [r.to_dict() for r in results],
                    "counts": {marketplace_name: len(results)},
                    # A named marketplace resolved, so this branch is never the
                    # "nothing to install from" case. Reported so both branches of this
                    # endpoint answer the same shape.
                    "installable_sources": 1,
                }
            )
        except Exception as exc:
            logger.warning("skills search failed for %s: %s", marketplace_name, exc)
            return web.json_response({"error": relayed_failure_copy(exc)}, status=500)

    results, counts = search_marketplaces_counted(query, limit=limit)
    # 🔴 `installable_sources` is what tells zero MATCHES apart from nothing to install FROM.
    #
    # Two sources register at import (`skills/native.py`): `native` mirrors the bundled
    # catalogue and `installed` mirrors the user's own skills. Both report
    # `marketplace_type == "native"`, and neither is somewhere to install FROM — the fan-out
    # skips `installed` outright and reports every `native` hit as `installed: true`, because
    # a stock install has already copied that whole bundle into the user's skills dir. So a
    # fresh install can MATCH plenty and still have nothing to install from, and
    # `{"results": [], "counts": {}}` was byte-identical to a query that genuinely matched
    # nothing (issue 1780). The user searched, got silence, and the panel blamed the query.
    #
    # Counting non-native sources rather than all of them is the whole point: `len(info())` is
    # 2 on a fresh install and would have made this field say "configured" about two mirrors of
    # what the user already has. The type is what the interface already declares, so this reads
    # a property rather than hardcoding the two names.
    catalogues = [m for m in registry.info() if m.get("type") != "native"]
    return web.json_response(
        {
            "results": [r.to_dict() for r in results],
            "counts": counts,
            "installable_sources": len(catalogues),
        }
    )


def search_marketplaces_counted(query: str, limit: int = 20) -> "tuple[list, dict[str, int]]":
    """Fan a query out to every registered marketplace and return
    ``(results, per_source_counts)``.

    The counts are taken BEFORE the merged list is capped at *limit*, so a source that
    matched 40 skills reports 40 even though only its top rows survive the cap.
    Never raises — a failing marketplace (an unreachable catalog) is logged and skipped,
    so one bad source cannot empty the store.

    🔴 Already-installed hits are ANNOTATED (``SkillEntry.installed``), never withheld.
    Dropping them read as "no results" for every query a stock install can make (#301):
    the gateway copies the whole bundled tree into the user's skills dir at startup
    (``skills/loader.py:_ensure_builtin_skills``) and the ``native`` marketplace is
    registered against that same bundled dir, so ``native``'s ids were ALWAYS a subset of
    the installed set and the filter emptied the catalogue structurally rather than
    occasionally. It also inverted this endpoint's documented contract — an unscoped
    search returned strictly LESS than the same search scoped to one marketplace. The
    ``installed`` source itself is still skipped, which is a different fact: it mirrors the
    user's own skills dir, so including it would list every skill twice.
    """
    from personalclaw.skills.marketplace import get_default_skills_registry

    registry = get_default_skills_registry()

    results = []
    for name in registry.list():
        if name == "installed":
            continue
        try:
            mp = registry.get(name)
            results.extend(mp.search(query, limit=limit))
        except Exception as exc:
            logger.warning("skills search failed for %s: %s", name, exc)
    _mark_installed(results)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.source] = counts.get(r.source, 0) + 1
    results.sort(key=lambda r: r.installs, reverse=True)
    return results[:limit], counts


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

    from personalclaw.skills.marketplace import (
        SkillNotFoundError,
        get_default_skills_registry,
    )

    try:
        mp = get_default_skills_registry().get(marketplace_name)
    except KeyError:
        return web.json_response(
            {"error": f"Marketplace '{marketplace_name}' not registered"}, status=404
        )

    try:
        detail = mp.fetch(skill_id)
    except SkillNotFoundError as exc:
        return json_error("not_found", message=str(exc), status=404)
    except Exception as exc:
        logger.warning("skills detail fetch failed for %s/%s: %s", marketplace_name, skill_id, exc)
        return web.json_response({"error": relayed_failure_copy(exc)}, status=500)

    skill_md = detail.skill_md() or ""
    # Parse the SKILL.md frontmatter so the dashboard can render structured fields
    # (name, description, triggers, tags, version, author, license). Both halves
    # delegate to the one parser — a marketplace preview must show the same
    # metadata the loader will read once installed, or the consent surface lies.
    from personalclaw.skills.loader import SkillsLoader

    frontmatter: dict[str, Any] = dict(SkillsLoader._parse_frontmatter_text(skill_md))
    body = SkillsLoader.strip_frontmatter(skill_md) if frontmatter else skill_md

    # JSON-safe file view: paths + a binary marker only. Raw ``data`` bytes from a binary
    # entry aren't JSON-serializable, and the preview UI only needs the tree — file
    # contents are fetched on demand via the per-file browser endpoint.
    file_view = [{"path": f.get("path", ""), "binary": "data" in f} for f in detail.files]
    return web.json_response(
        {
            "id": detail.id,
            "name": detail.name,
            "audit_status": detail.audit_status,
            "files": file_view,
            "frontmatter": frontmatter,
            "body": body,
            "marketplace": marketplace_name,
        }
    )


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
        SkillNotFoundError,
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
    except SkillNotFoundError as exc:
        _sel_log("skills.install", "denied", f"notfound:{marketplace_name}/{skill_id}", request)
        return json_error("not_found", message=str(exc), status=404)
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
        return web.json_response({"error": relayed_failure_copy(exc)}, status=500)


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
    _sel_log(
        "skills.verify", "ok" if rep.ok or rep.unlocked else "tampered", f"{name}:{status}", request
    )
    return web.json_response(
        {
            "name": name,
            "integrity": status,
            "ok": rep.ok,
            "unlocked": rep.unlocked,
            "mutated": rep.mutated,
            "missing": rep.missing,
            "added": rep.added,
            "summary": rep.summary(),
        }
    )


async def api_skill_overlay_revert(request: web.Request) -> web.Response:
    """POST /api/skills/overlay/revert — drop a skill's accepted-refinement overlay.

    Body: ``{name: "<skill name>"}`` (the name may carry a namespace slash, e.g.
    ``auto/release-flow``, which is why it rides the body rather than a URL segment). Revert is
    the deletion of exactly ONE sidecar file: the base ``SKILL.md`` and its ``.pclaw-lock.json``
    are untouched, so a marketplace skill stays verifiable across the round trip."""
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    name = require_string(body, "name")

    from personalclaw.skills import overlays

    removed = overlays.revert_overlay(name)
    _sel_log("skills.overlay_revert", "ok" if removed else "noop", name, request)
    return web.json_response({"ok": True, "name": name, "reverted": removed})


# ── Ephemeral session skills (skill-ephemeral-promotion) ─────────────────────


async def api_ephemeral_skills_list(request: web.Request) -> web.Response:
    """GET /api/skills/ephemeral/{session} — the session-live drafts awaiting a
    promote/forget decision (drives the end-of-session modal)."""
    from personalclaw.dashboard.handlers.sessions import _session_exists
    from personalclaw.skills import ephemeral

    session = request.match_info.get("session", "")
    if not _session_exists(request.app["state"], session):
        return json_error("session_not_found", status=404)
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
            session,
            slug,
            scope,
            agent=(str(body.get("agent", "")).strip() or None),
            title=(string_field(body, "title") or None),
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
    # A missing draft is the sibling api_skills_delete's condition and gets its
    # shape: 404, not 200 {ok:false} — no UI caller read the flag, so a stale
    # slug reported "Forgotten" for a request that changed nothing (#636).
    if not ephemeral.discard(session, slug):
        return web.json_response({"error": f"draft '{slug}' not found"}, status=404)
    return web.json_response({"ok": True})


# ── Skill proposals inbox (skill-evolution-proposal-only) ────────────────────


async def api_skill_proposals_list(request: web.Request) -> web.Response:
    """GET /api/skills/proposals — the pending autonomous-synthesis proposals
    awaiting human review (propose-only; nothing here is installed).

    ``lastReview`` carries the most recent skill-ladder pass (``null`` when none has
    ever run). It is here rather than on a route of its own because it is the answer
    to a question this route's own payload otherwise cannot answer: an empty
    ``proposals`` list with a ``lastReview`` is a working ladder that found nothing,
    and an empty list with ``lastReview: null`` is a ladder that never fired (`G44`).
    """
    from personalclaw.skills import proposals

    return web.json_response(
        {
            "proposals": [p.summary() for p in proposals.list_pending()],
            "lastReview": proposals.last_review(),
        }
    )


async def api_skill_proposal_detail(request: web.Request) -> web.Response:
    """GET /api/skills/proposals/{id} — full proposal incl. procedure + fenced source.

    For a ``kind="refine"`` proposal the payload also carries ``diff`` (a unified diff) and
    ``version`` (the refinement version accepting it would create). Both are DERIVED here,
    per-request, from the skill's current body — never stored on the proposal. A diff frozen
    at enqueue time would go stale the moment anything else refined the same skill, and an
    approval surface showing a stale diff asks the user to approve a change they were not
    shown. Empty ``diff`` is meaningful: the target no longer resolves, or the refinement
    would change nothing.
    """
    from personalclaw.skills import proposals, refine

    prop = proposals.get(request.match_info.get("id", ""))
    if prop is None:
        return web.json_response({"error": "not found"}, status=404)
    payload = prop.to_dict()
    if prop.kind == "refine" and prop.refine_target:
        from personalclaw.skills import overlays

        payload["diff"] = refine.proposal_diff(prop)
        payload["version"] = overlays.next_version(prop.refine_target)
    return web.json_response(payload)


async def api_skill_proposal_accept(request: web.Request) -> web.Response:
    """POST /api/skills/proposals/{id}/accept — install into the live auto/ tier
    (with optional reviewer edits) + clear the proposal."""
    from personalclaw.skills import proposals

    pid = request.match_info.get("id", "")
    body = await json_object_body(request)
    body = body if isinstance(body, dict) else {}
    try:
        result = proposals.accept(
            pid,
            description=(str(body["description"]) if body.get("description") else None),
            procedure_md=(str(body["procedure_md"]) if body.get("procedure_md") else None),
        )
    except proposals.AcceptError as exc:
        _sel_log("skills.proposal_accept", "rejected", f"{pid}:{exc}", request)
        return web.json_response({"error": str(exc)}, status=409)
    # The version is on the audit line, not only in the response: two refinements of one skill
    # are two different approvals, and an audit that records both as the bare skill name cannot
    # say which one a reader is looking at.
    _sel_log(
        "skills.proposal_accept",
        "ok",
        f"{result.name} v{result.version}" if result.version else result.name,
        request,
    )
    return web.json_response({"ok": True, "name": result.name, "version": result.version})


async def api_skill_proposal_reject(request: web.Request) -> web.Response:
    """DELETE /api/skills/proposals/{id} — drop a proposal (never installed)."""
    from personalclaw.skills import proposals

    pid = request.match_info.get("id", "")
    ok = proposals.reject(pid)
    _sel_log("skills.proposal_reject", "ok" if ok else "rejected", pid, request)
    # Same condition, same shape as api_skills_delete: a nonexistent proposal is
    # a 404 — the 200 {ok:false} told every ignore-the-body caller "Rejected"
    # while a double-submit or stale surface (Skills page / Inbox / ActionCenter
    # all offer the same id) had changed nothing (#636).
    if not ok:
        return web.json_response({"error": f"proposal '{pid}' not found"}, status=404)
    return web.json_response({"ok": True})
