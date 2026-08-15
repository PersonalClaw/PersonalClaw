"""Inbound skill-catalog importer — an external catalog as a COMMUNITY marketplace.

AGENT-PACKS §6 (AP-6). A *skill catalog* is a named, operator-configured index of
installable skills (``packs.skill_catalogs``): a JSON index endpoint (``kind="index"``)
or a git "tap" repo laid out as ``skills/<slug>/SKILL.md`` (``kind="tap"``). Each one
registers on the shared skills registry as a :class:`CatalogMarketplace`.

Three properties are load-bearing, and each is the reason this module is thin:

1. **COMMUNITY tier, always.** A catalog is untrusted third-party content, so
   :attr:`CatalogMarketplace.trust_tier` is hard-coded to
   :attr:`~personalclaw.supply_chain.TrustTier.COMMUNITY` — never derived from config,
   because "the operator added it" is not provenance. That tier is what makes
   ``install_scanned`` run the full gate instead of the advisory bundled-content one.
2. **No install path of its own.** Like every marketplace this is a read-only SOURCE:
   :meth:`search` and :meth:`fetch` only. Installing is
   :meth:`~personalclaw.skills.marketplace.SkillsRegistry.install_guarded`'s job, which
   quarantines the fetched payload, scans the whole staged dir at this tier, commits the
   exact scanned bytes and writes ``.pclaw-lock.json``. Zero chokepoint bypass.
3. **Network reach is profile-bound.** Every byte arrives through
   :func:`personalclaw.net.fetch` under the CONNECTOR egress profile (layered with the
   operator's ``security.egress`` config via ``egress_policy_for``), so a catalog URL
   cannot reach a private address, follow a redirect off-policy, or stream unbounded
   bytes. There is deliberately no bare HTTP client in this module.

A large index browses without entering the agent budget: the whole index is fetched once
per catalog, memoized, and filtered *in this process* — ``search()`` returns at most
``limit`` :class:`~personalclaw.skills.marketplace.SkillEntry` rows, and only a chosen
skill's files are ever fetched. Nothing here calls an LLM.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from personalclaw.skills.marketplace import (
    SkillDetail,
    SkillEntry,
    SkillsMarketplace,
    get_default_skills_registry,
)

logger = logging.getLogger(__name__)

# Registered-marketplace name prefix. Namespaced so a catalog named "native" can never
# shadow the bundled marketplace, and so the Skills store can tell catalog sources apart
# from core ones by name alone.
CATALOG_SOURCE_PREFIX = "catalog:"

_SKILL_FILENAME = "SKILL.md"
_INDEX_FILENAME = "index.json"
# A tap repo's conventional skill root (``skills/<slug>/SKILL.md``).
_TAP_SKILLS_DIR = "skills"
# Defensive cap on how many index rows are kept in memory for one catalog. A hostile
# catalog cannot make browsing unbounded; CONNECTOR's max_bytes caps the transfer too.
MAX_INDEX_ENTRIES = 5000
# Defensive cap on files fetched for one skill (a catalog cannot make one install
# fan out into hundreds of requests).
MAX_SKILL_FILES = 50


def catalog_source_name(catalog_name: str) -> str:
    """The registry name a catalog registers under (``catalog:<name>``)."""
    return f"{CATALOG_SOURCE_PREFIX}{catalog_name.strip()}"


def fetch_catalog_text(url: str) -> str:
    """GET *url* under the CONNECTOR egress profile and return its decoded body.

    The ONE network primitive this module has. CONNECTOR is layered with the operator's
    ``security.egress`` config (``egress_policy_for``) exactly as the knowledge-connector
    and model-catalog fetches do, so a self-hosted catalog on an allowlisted host works
    without loosening the profile for everyone else. Raises on a non-2xx status so a
    404 index never parses as an empty catalog.
    """
    import asyncio

    from personalclaw.net import CONNECTOR, egress_policy_for, fetch

    policy = egress_policy_for(CONNECTOR)

    async def _go() -> Any:
        return await fetch(url, policy=policy)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        resp = asyncio.run(_go())
    else:
        # Called from inside a running loop (a request-handler thread) — drive the
        # coroutine on a private loop in a worker thread rather than deadlocking.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            resp = pool.submit(asyncio.run, _go()).result()

    status = int(getattr(resp, "status", 0) or 0)
    if status < 200 or status >= 300:
        raise RuntimeError(f"catalog fetch failed: HTTP {status} for {url}")
    return str(resp.text)


@dataclass
class CatalogEntry:
    """One row of a catalog index, normalized.

    ``files`` are catalog-relative paths (``SKILL.md``, ``scripts/run.sh``); ``base`` is
    the URL those paths resolve against. Both are derived here so ``fetch()`` never has
    to re-guess a layout.
    """

    id: str
    name: str
    description: str = ""
    url: str = ""
    installs: int = 0
    base: str = ""
    files: list[str] = field(default_factory=lambda: [_SKILL_FILENAME])


class CatalogMarketplace(SkillsMarketplace):
    """One operator-configured skill catalog, browsed and fetched at COMMUNITY tier.

    Constructed from a :class:`~personalclaw.config.loader.SkillCatalogConfig` row via
    :meth:`from_config`. ``kind`` selects only how the index URL and the per-skill file
    base are DERIVED — both kinds then share one fetch/parse path:

    - ``index``: ``url`` is the index document itself; a row's files resolve against the
      row's ``base`` (or the index URL's directory + the skill id).
    - ``tap``:  ``url`` is a repo raw-content root; the index is
      ``<url>/skills/index.json`` and files resolve under ``<url>/skills/<slug>/``.
    """

    def __init__(self, name: str, url: str, kind: str = "index") -> None:
        self.name = (name or "").strip()
        self.url = (url or "").strip()
        self.kind = (kind or "index").strip().lower() or "index"
        self._index: list[CatalogEntry] | None = None

    @classmethod
    def from_config(cls, cfg: Any) -> CatalogMarketplace:
        """Build from a ``SkillCatalogConfig``-shaped object (duck-typed on purpose so
        the config dataclass stays the config layer's business)."""
        return cls(
            name=getattr(cfg, "name", "") or "",
            url=getattr(cfg, "url", "") or "",
            kind=getattr(cfg, "kind", "index") or "index",
        )

    # ── Identity ─────────────────────────────────────────────────────────────

    @property
    def marketplace_type(self) -> str:
        return "catalog"

    @property
    def trust_tier(self) -> str:
        # NOT configurable. An external catalog is third-party content whatever the
        # operator thinks of it, so it always takes the full community scan gate in
        # install_scanned. Raising this would silence the gate that makes an inbound
        # catalog safe to browse at all.
        from personalclaw.supply_chain import TrustTier

        return TrustTier.COMMUNITY.value

    @property
    def source_name(self) -> str:
        """The registry key this catalog registers under."""
        return catalog_source_name(self.name)

    # ── Index ────────────────────────────────────────────────────────────────

    def _index_url(self) -> str:
        if self.kind == "tap":
            return urljoin(f"{self.url.rstrip('/')}/", f"{_TAP_SKILLS_DIR}/{_INDEX_FILENAME}")
        return self.url

    def _default_base(self, skill_id: str) -> str:
        """Where a row's files live when the row does not say."""
        if self.kind == "tap":
            return urljoin(f"{self.url.rstrip('/')}/", f"{_TAP_SKILLS_DIR}/{skill_id}/")
        # index kind: sibling directory of the index document, named after the skill.
        index_dir = self._index_url().rsplit("/", 1)[0]
        return urljoin(f"{index_dir}/", f"{skill_id}/")

    def _parse_index(self, text: str) -> list[CatalogEntry]:
        """Normalize an index document into :class:`CatalogEntry` rows.

        Accepts ``{"skills": [...]}`` or a bare list. A row missing an id/slug is
        skipped rather than fataled — one malformed row must not cost the catalog.
        """
        doc = json.loads(text)
        rows = doc.get("skills", []) if isinstance(doc, dict) else doc
        if not isinstance(rows, list):
            raise ValueError("catalog index must be a list or {'skills': [...]}")
        out: list[CatalogEntry] = []
        for row in rows[:MAX_INDEX_ENTRIES]:
            if not isinstance(row, dict):
                continue
            skill_id = str(row.get("id") or row.get("slug") or row.get("name") or "").strip()
            if not skill_id or "/" in skill_id or skill_id.startswith("."):
                continue
            files = [
                str(f).strip()
                for f in (row.get("files") or [_SKILL_FILENAME])
                if isinstance(f, str) and str(f).strip()
            ]
            files = [f for f in files if ".." not in f and not f.startswith("/")]
            if _SKILL_FILENAME not in files:
                files.insert(0, _SKILL_FILENAME)
            out.append(
                CatalogEntry(
                    id=skill_id,
                    name=str(row.get("name") or skill_id).strip() or skill_id,
                    description=str(row.get("description") or "").strip(),
                    url=str(row.get("url") or "").strip(),
                    installs=int(row.get("installs") or 0),
                    base=str(row.get("base") or "").strip() or self._default_base(skill_id),
                    files=files[:MAX_SKILL_FILES],
                )
            )
        return out

    def index(self, *, refresh: bool = False) -> list[CatalogEntry]:
        """Fetch (once) and return the catalog index.

        Memoized per instance: a large index costs ONE guarded fetch however many times
        the store searches it, and every subsequent keystroke filters in-process.
        """
        if self._index is None or refresh:
            if not self.url:
                raise RuntimeError(f"catalog {self.name!r} has no url configured")
            self._index = self._parse_index(fetch_catalog_text(self._index_url()))
        return self._index

    # ── SkillsMarketplace ────────────────────────────────────────────────────

    def search(self, query: str, limit: int = 20) -> list[SkillEntry]:
        """Filter the cached index locally and return at most *limit* rows.

        The filter is a plain substring match over id/name/description — no LLM, and no
        path that hands the whole index to an agent. An empty query lists the head of the
        catalog so the store can browse it.
        """
        q = (query or "").strip().lower()
        results: list[SkillEntry] = []
        for entry in self.index():
            if q and q not in entry.id.lower() and q not in entry.name.lower():
                if q not in entry.description.lower():
                    continue
            results.append(
                SkillEntry(
                    id=entry.id,
                    name=entry.name,
                    description=entry.description,
                    source=self.source_name,
                    url=entry.url,
                    installs=entry.installs,
                )
            )
            if len(results) >= max(1, int(limit)):
                break
        return results

    def fetch(self, skill_id: str) -> SkillDetail:
        """Pull one skill's declared files under CONNECTOR and return them unwritten.

        Returns a :class:`SkillDetail` and nothing else: ``install_guarded`` stages this
        payload to quarantine, scans it at COMMUNITY tier, then commits the exact scanned
        bytes and the lock. A missing SKILL.md is fatal here (an install with no SKILL.md
        is not a skill).
        """
        wanted = (skill_id or "").strip()
        entry = next((e for e in self.index() if e.id == wanted), None)
        if entry is None:
            raise RuntimeError(f"skill {skill_id!r} not found in catalog {self.name!r}")

        files: list[dict[str, Any]] = []
        for rel in entry.files[:MAX_SKILL_FILES]:
            file_url = urljoin(f"{entry.base.rstrip('/')}/", rel)
            try:
                text = fetch_catalog_text(file_url)
            except Exception as exc:
                if rel == _SKILL_FILENAME:
                    raise RuntimeError(
                        f"catalog {self.name!r}: cannot fetch {rel} for {wanted!r}: {exc}"
                    ) from exc
                logger.warning(
                    "catalog %s: skipping unfetchable file %s for %s: %s",
                    self.name,
                    rel,
                    wanted,
                    exc,
                )
                continue
            files.append({"path": rel, "contents": text})

        if not any(f["path"] == _SKILL_FILENAME for f in files):
            raise RuntimeError(f"catalog {self.name!r}: {wanted!r} has no {_SKILL_FILENAME}")

        # audit_status stays "unknown": a catalog makes no claim about its own content,
        # and the only verdict that counts is the one install_scanned's scan produces.
        return SkillDetail(id=wanted, name=entry.name or wanted, files=files)


# ── Registration ─────────────────────────────────────────────────────────────


def register_skill_catalogs(config: Any = None, registry: Any = None) -> list[str]:
    """Register every configured ``packs.skill_catalogs`` entry and return their names.

    Fail-open PER CATALOG: a nameless/urlless row is skipped and a row that raises while
    being constructed is logged, so one bad catalog can never cost the operator the
    others (or the bundled marketplaces already on the registry). An empty config
    registers NOTHING — no dead marketplace appears in the store.

    Idempotent: re-registering the same name replaces the instance, which is what a
    config change should do.
    """
    reg = registry if registry is not None else get_default_skills_registry()
    if config is None:
        from personalclaw.config.loader import AppConfig

        config = AppConfig.load()

    registered: list[str] = []
    catalogs = getattr(getattr(config, "packs", None), "skill_catalogs", None) or []
    for cfg in catalogs:
        name = (getattr(cfg, "name", "") or "").strip()
        url = (getattr(cfg, "url", "") or "").strip()
        if not name or not url:
            logger.warning("skipping skill catalog with empty name or url: %r", cfg)
            continue
        try:
            mp = CatalogMarketplace.from_config(cfg)
            reg.register(mp.source_name, mp)
        except Exception:
            logger.exception("failed to register skill catalog %r", name)
            continue
        registered.append(mp.source_name)
    return registered
