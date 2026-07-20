"""App catalog — what's AVAILABLE to install, for the Store half of the App page.

The App page has two halves:
  * **Library** — what's installed (``apps.manager.list_apps``).
  * **Store** — what's available to install, which this module enumerates from two
    sources:
      1. **Native** — manifests PersonalClaw ships under ``apps/native/`` (native)
         that aren't currently installed (e.g. a default provider the user
         force-uninstalled, or a bundled app they haven't added yet).
      2. **Git sources** — a user-managed list of git URLs (seeded with any
         PersonalClaw-bundled defaults). Each entry is an installable app source;
         the catalog reports it as available without cloning (the clone happens at
         install time, behind the scanner gate).

A catalog entry is metadata only — installing one routes through the normal
``app_manager.install`` (path for bundled, git URL for sources), so the scanner
gate + lifecycle are unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from personalclaw.apps.manifest import AppManifest
from personalclaw.atomic_write import atomic_write
from personalclaw.config.loader import config_dir

logger = logging.getLogger(__name__)

_SOURCES_FILENAME = "app-sources.json"

# Hero-image resolution. An app's ``heroImage`` is a path RELATIVE to its dir; we
# read the file and inline it as a ``data:`` URI so BOTH installed apps and
# not-yet-installed catalog entries render a banner with no per-file serving route
# (and no dependence on the app being enabled). Guardrails: confined to the app
# dir (traversal-safe), only known raster/vector image types, size-capped so a
# stray large asset can't bloat the catalog payload.
_HERO_MAX_BYTES = 1_500_000  # ~1.5 MB — generous for a banner, bounds the payload
_HERO_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
}


def resolve_hero_url(app_dir: Path, hero_rel: str) -> str:
    """Resolve a manifest ``heroImage`` (relative path) under ``app_dir`` to a
    ``data:`` URI, or ``""`` when unset / missing / disallowed. Traversal-guarded,
    type-allowlisted, and size-capped — a bad value degrades to no hero, never an
    error (the card just falls back to the icon layout)."""
    import base64

    rel = (hero_rel or "").strip()
    if not rel:
        return ""
    try:
        root = app_dir.resolve()
        target = (root / rel).resolve()
        # Confine to the app dir (reject ../ escapes and absolute reroutes).
        if root not in target.parents and target != root:
            return ""
        if not target.is_file():
            return ""
        mime = _HERO_MIME.get(target.suffix.lower())
        if not mime:
            return ""
        data = target.read_bytes()
        if len(data) > _HERO_MAX_BYTES:
            logger.debug("hero image %s exceeds %d bytes — skipping", target, _HERO_MAX_BYTES)
            return ""
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except OSError:
        logger.debug("could not read hero image %r under %s", hero_rel, app_dir, exc_info=True)
        return ""

# Git source URLs PersonalClaw ships as Store defaults. Empty for now (the OSS
# package ships no first-party remote registry yet); user-added URLs accumulate
# alongside these. Kept as a constant so a future release can seed defaults here
# without a migration. When the first-party apps repo is published, its git URL
# goes HERE (replacing the dev filesystem default below).
_DEFAULT_GIT_SOURCES: tuple[str, ...] = ()


def _first_party_source() -> Path | None:
    """The always-present, read-only FIRST-PARTY app source.

    First-party apps live in the workspace ``apps/`` dir (a sibling of the
    ``PersonalClaw/`` core repo), destined for their own git repo. Until that repo
    is published, this is a DEV filesystem default so those apps always appear in
    the Store (uninstalled — the user opts in). Resolved relative to the package:
    ``.../PersonalClaw/src/personalclaw/`` → ``.../PersonalClaw/`` → ``../apps``.
    Returns None if it doesn't exist (a shipped install without the workspace tree),
    so this is a no-op there. Not user-removable (it's not in the persisted list)."""
    # catalog.py is at src/personalclaw/apps/catalog.py → parents: apps, personalclaw,
    # src, PersonalClaw, <workspace>. The workspace holds apps/ beside PersonalClaw/.
    workspace_apps = Path(__file__).resolve().parents[4] / "apps"
    return workspace_apps if workspace_apps.is_dir() else None


# Env override so a packaged/relocated install can point at the first-party dir
# (or, later, this becomes the published git URL in _DEFAULT_GIT_SOURCES).
import os as _os

_FIRST_PARTY_ENV = "PERSONALCLAW_FIRST_PARTY_APPS_DIR"


@dataclass
class CatalogEntry:
    """One available-to-install app surfaced in the Store."""

    name: str
    displayName: str  # noqa: N815
    description: str = ""
    version: str = ""
    icon: str = ""
    heroUrl: str = ""  # noqa: N815 — resolved data: URI (from manifest heroImage), "" if none
    author: str = ""
    source: str = ""          # install source: a local path (bundled) or git URL
    sourceKind: str = "bundled"  # noqa: N815 — "bundled" | "git"
    isProvider: bool = False  # noqa: N815
    providerType: str = ""    # noqa: N815
    tags: list[str] = field(default_factory=list)
    # P20 federation: when this entry came from a source's registry index (not a
    # direct dir-scan), the install POINTER — the exact source string to hand
    # app_manager.install (repo URL, optionally with a #subdirectory) so install
    # still routes through source.resolve + the scanner, unchanged. "" for a
    # dir-scanned entry (source itself is the pointer).
    pointer: str = ""
    # P29 install-consent transparency: the app's declared permissions + crons, so the
    # Store can show WHAT the app will be granted + WHAT recurring jobs it will run BEFORE
    # the user installs. Metadata only (populated from the scanned manifest); empty for a
    # registry-index card (pointer-only, manifest not yet fetched — surfaced post-clone).
    permissions: dict[str, Any] = field(default_factory=dict)
    crons: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# P20 — registry index (federated app sources)
#
# A source (git URL or local dir) MAY publish an ``app-registry.json`` at its root:
# a lightweight pointer list so the Store can enumerate the source's apps WITHOUT
# cloning each one. Absent → we fall back to today's clone-then-scan (git) / dir-scan
# (local). The index is metadata only + untrusted: install still routes every app
# through ``source.resolve`` + the supply-chain scanner, so a malicious index can at
# worst list apps that then fail the scanner — it never widens the trust boundary.
# ---------------------------------------------------------------------------

_REGISTRY_FILENAME = "app-registry.json"
_REGISTRY_TTL_SECS = 3600.0  # 1h — stale-better-than-a-clone-per-list; refetched after
# module-level cache: source string → (fetched_at_epoch, pointers). Bounded by the
# small number of configured sources.
_registry_cache: dict[str, tuple[float, list["RegistryPointer"]]] = {}

# Git-source subdirectory scan cache: url → (fetched_at_epoch, entries).
# A shorter TTL than the registry index — re-clones are heavier, but staleness is worse
# for discovery (a user adds a source + expects to see it immediately).
_GIT_SCAN_TTL_SECS = 300.0  # 5 minutes
_git_scan_cache: dict[str, tuple[float, list["CatalogEntry"]]] = {}


@dataclass
class RegistryPointer:
    """One entry in a source's ``app-registry.json`` — a pointer to an installable
    app, resolved to a CatalogEntry card without cloning. ``repo``/``subdirectory``
    build the install pointer; the display fields are index-provided hints (the
    authoritative manifest is only read at install time)."""

    name: str
    repo: str = ""            # git URL (or path) to clone/read at install; "" → same source
    branch: str = ""          # optional ref
    subdirectory: str = ""    # optional path within the repo where app.json lives
    displayName: str = ""     # noqa: N815 — index hint
    description: str = ""
    version: str = ""
    icon: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RegistryPointer | None":
        name = str(d.get("name", "")).strip()
        if not name:
            return None  # a pointer with no name is unusable — skip it
        return cls(
            name=name,
            repo=str(d.get("repo", "")).strip(),
            branch=str(d.get("branch", "")).strip(),
            subdirectory=str(d.get("subdirectory", "")).strip(),
            displayName=str(d.get("displayName", "")).strip(),
            description=str(d.get("description", "")).strip(),
            version=str(d.get("version", "")).strip(),
            icon=str(d.get("icon", "")).strip(),
            author=str(d.get("author", "")).strip(),
            tags=[str(t) for t in (d.get("tags") or []) if str(t).strip()],
        )


def _parse_registry(text: str) -> list[RegistryPointer]:
    """Parse ``app-registry.json`` content → pointer list. Tolerant: accepts either a
    bare array of pointers or an object ``{"apps": [...]}``; drops malformed entries;
    returns [] on any parse error (caller falls back to the scan path)."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("app registry: unparseable index", exc_info=True)
        return []
    raw = data.get("apps", []) if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    out: list[RegistryPointer] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        p = RegistryPointer.from_dict(item)
        if p is None or p.name in seen:
            continue
        seen.add(p.name)
        out.append(p)
    return out


def _read_git_registry(url: str) -> str | None:
    """Fetch ONLY ``app-registry.json`` from a git source, cheaply — a shallow
    treeless clone (blob:none, depth 1) then read the one file, no full checkout of
    every app. Returns the file text, "" if the source has no index, or None on a
    git/timeout error (caller falls back to clone-then-scan). Never raises."""
    import subprocess
    import tempfile
    tmp = tempfile.mkdtemp(prefix="pclaw-registry-")
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout",
             "--", url, tmp],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            logger.debug("app registry: git fetch failed for %s: %s", url,
                         (proc.stderr or "")[-200:])
            return None
        # Pull just the index file out of the tree without checking out the rest.
        show = subprocess.run(
            ["git", "-C", tmp, "show", f"HEAD:{_REGISTRY_FILENAME}"],
            capture_output=True, text=True, timeout=30,
        )
        # A source with no registry index → git exits non-zero on the missing path.
        return show.stdout if show.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        logger.debug("app registry: git fetch errored for %s", url, exc_info=True)
        return None
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def _fetch_registry_index(source: str, *, is_git: bool, now: float) -> list[RegistryPointer] | None:
    """Return a source's registry-index pointers, cached ~1h. None = the source has
    NO usable index (caller keeps the clone-then-scan / dir-scan path). Never raises.

    ``now`` (epoch secs) is injected so the TTL is deterministic in tests."""
    cached = _registry_cache.get(source)
    if cached is not None and (now - cached[0]) < _REGISTRY_TTL_SECS:
        return cached[1] or None
    text: str | None
    if is_git:
        text = _read_git_registry(source)
        if text is None:
            return None  # transient git error → don't cache; fall back this round
    else:
        p = Path(source).expanduser() / _REGISTRY_FILENAME
        try:
            text = p.read_text(encoding="utf-8") if p.is_file() else ""
        except OSError:
            return None
    pointers = _parse_registry(text) if text else []
    _registry_cache[source] = (now, pointers)
    return pointers or None


def _pointer_to_entry(source: str, p: RegistryPointer, *, is_git: bool) -> CatalogEntry:
    """Build a Store card from a registry pointer. The install POINTER is the repo the
    pointer names (falling back to the source itself), with a ``#subdirectory`` suffix
    when the app lives in a subdir — the exact string install hands to source.resolve."""
    repo = p.repo or source
    pointer = repo + (f"#{p.subdirectory}" if p.subdirectory else "")
    return CatalogEntry(
        name=p.name, displayName=p.displayName or p.name, description=p.description,
        version=p.version, icon=p.icon, author=p.author,
        source=source, sourceKind="git" if is_git else "local",
        tags=list(p.tags), pointer=pointer,
    )


def _scan_registries(*, now: float) -> list[CatalogEntry]:
    """Enumerate apps from every configured source's registry index (git + local),
    as install cards — WITHOUT cloning each app. Sources with no index contribute
    nothing here (their apps still surface via the existing git-URL list / local
    dir-scan). Skips apps already installed or already surfaced by a dir-scan."""
    installed = _installed_names()
    out: list[CatalogEntry] = []
    seen: set[str] = set()
    for url in list_git_sources():
        pointers = _fetch_registry_index(url, is_git=True, now=now)
        for p in pointers or []:
            if p.name in installed or p.name in seen:
                continue
            seen.add(p.name)
            out.append(_pointer_to_entry(url, p, is_git=True))
    for root in list_local_sources():
        pointers = _fetch_registry_index(root, is_git=False, now=now)
        for p in pointers or []:
            if p.name in installed or p.name in seen:
                continue
            seen.add(p.name)
            out.append(_pointer_to_entry(root, p, is_git=False))
    return out


# ---------------------------------------------------------------------------
# Git source subdirectory scan (multi-app repos without a registry index)
#
# When a git source has NO ``app-registry.json`` AND no root ``app.json``, it's
# likely a multi-app repo (subdirs each containing ``app.json``). This mirrors
# ``_scan_local_sources`` for git: shallow-clone, scan immediate subdirs, build
# CatalogEntry cards. Cached per-URL with a short TTL so catalog page loads
# don't re-clone each time.
# ---------------------------------------------------------------------------


def _scan_git_source(url: str, *, now: float) -> list[CatalogEntry]:
    """Shallow-clone a git source, scan immediate subdirs for ``app.json``,
    and return installable CatalogEntry objects (with ``pointer=url#subdir``).

    Returns cached results within the TTL. Returns [] on any clone/scan error
    (resilient — a bad source degrades to invisible, never an error page).
    Skips sources that have a registry index (handled by ``_scan_registries``).
    """
    import shutil
    import subprocess
    import tempfile

    # Cache hit?
    cached = _git_scan_cache.get(url)
    if cached is not None and (now - cached[0]) < _GIT_SCAN_TTL_SECS:
        return cached[1]

    entries: list[CatalogEntry] = []
    tmp = tempfile.mkdtemp(prefix="pclaw-gitscan-")
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--", url, tmp],
            capture_output=True, text=True, timeout=90,
        )
        if proc.returncode != 0:
            logger.debug(
                "git scan: clone failed for %s: %s",
                url, (proc.stderr or "")[-200:],
            )
            _git_scan_cache[url] = (now, [])
            return []

        root = Path(tmp)

        # If a registry index exists, this source is handled by
        # _scan_registries — don't double-surface.
        if (root / _REGISTRY_FILENAME).is_file():
            _git_scan_cache[url] = (now, [])
            return []

        # If a root app.json exists, it's a single-app repo — the existing
        # git-source URL list already surfaces it for direct install.
        if (root / "app.json").is_file():
            _git_scan_cache[url] = (now, [])
            return []

        # Scan immediate subdirs for app.json manifests.
        installed = _installed_names()
        seen: set[str] = set()
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            manifest_file = entry / "app.json"
            if not manifest_file.is_file():
                continue
            try:
                m = AppManifest.from_json_file(manifest_file)
            except Exception:
                logger.debug(
                    "git scan: bad manifest %s in %s",
                    entry.name, url, exc_info=True,
                )
                continue
            if m.name in installed or m.name in seen:
                continue
            seen.add(m.name)
            _perms, _crons = _manifest_consent(m)
            entries.append(CatalogEntry(
                name=m.name,
                displayName=m.displayName or m.name,
                description=m.description,
                version=m.version,
                icon=m.icon,
                heroUrl=resolve_hero_url(entry, m.heroImage),
                author=m.author,
                source=url,
                sourceKind="git",
                isProvider=bool(m.provider),
                providerType=(
                    m.provider.type if m.provider else ""
                ),
                tags=list(m.tags),
                pointer=f"{url}#{entry.name}",
                permissions=_perms,
                crons=_crons,
            ))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        logger.debug(
            "git scan: error scanning %s", url, exc_info=True,
        )
        entries = []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    _git_scan_cache[url] = (now, entries)
    return entries


def _scan_git_sources(*, now: float) -> list[CatalogEntry]:
    """Scan all configured git sources that lack a registry index, returning
    discovered multi-app subdirectory entries. Sources WITH a registry index
    are skipped (already handled by ``_scan_registries``)."""
    out: list[CatalogEntry] = []
    seen: set[str] = set()
    installed = _installed_names()
    for url in list_git_sources():
        for entry in _scan_git_source(url, now=now):
            if entry.name in installed or entry.name in seen:
                continue
            seen.add(entry.name)
            out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Git source list (user-managed, persisted)
# ---------------------------------------------------------------------------

def _sources_path() -> Path:
    return config_dir() / "apps" / _SOURCES_FILENAME


def list_git_sources() -> list[str]:
    """The configured git source URLs (defaults + user-added), de-duped in order."""
    seen: set[str] = set()
    out: list[str] = []
    for url in (*_DEFAULT_GIT_SOURCES, *_read_user_sources()):
        u = url.strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _read_sources() -> dict[str, list[str]]:
    """The typed user-sources store ``{"git": [...], "local": [...]}``.

    Back-reads the legacy flat ``{"sources": [urls]}`` shape (git-only) as ``git`` so
    an existing sources file upgrades transparently on the next write."""
    p = _sources_path()
    if not p.is_file():
        return {"git": [], "local": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("failed to read app sources list", exc_info=True)
        return {"git": [], "local": []}
    git = [str(u) for u in data.get("git", data.get("sources", [])) if str(u).strip()]
    local = [str(u) for u in data.get("local", []) if str(u).strip()]
    return {"git": git, "local": local}


def _write_sources(sources: dict[str, list[str]]) -> None:
    p = _sources_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(p, json.dumps({"git": sources.get("git", []),
                                "local": sources.get("local", [])}, indent=2) + "\n")


def _read_user_sources() -> list[str]:
    """Legacy shim: the user GIT sources only (used by list_git_sources)."""
    return _read_sources()["git"]


def add_git_source(url: str) -> list[str]:
    """Add a user git source URL; returns the updated USER git list (excludes defaults)."""
    u = url.strip()
    if not u:
        raise ValueError("empty source URL")
    src = _read_sources()
    if u not in src["git"]:
        src["git"].append(u)
        _write_sources(src)
    return src["git"]


def remove_git_source(url: str) -> list[str]:
    """Remove a user git source URL (a bundled default can't be removed)."""
    u = url.strip()
    src = _read_sources()
    src["git"] = [x for x in src["git"] if x != u]
    _write_sources(src)
    return src["git"]


# ── Local-directory app sources (workspace-core-app-split §4) ───────────────
# A local source is a directory containing app subdirs (each with an app.json) —
# the dev-loop equivalent of a git source (e.g. the post-split ``apps/`` tree). The
# install pipeline already handles a local path (source.resolve → origin="local");
# this adds the persisted source list + dir-scan so local apps surface in the Store.

def _default_local_sources() -> list[str]:
    """Always-present, read-only local sources: the FIRST-PARTY apps dir.

    Resolution: if the env override is SET, it wins exclusively — a valid dir is the
    source, any other value (incl. a nonexistent path) DISABLES the default (this is
    how tests neutralize it). If the env is unset, fall back to the resolved workspace
    ``apps/`` (dev); empty if that doesn't exist (a shipped install without the tree)."""
    if _FIRST_PARTY_ENV in _os.environ:
        env = _os.environ[_FIRST_PARTY_ENV].strip()
        p = Path(env).expanduser() if env else None
        return [str(p)] if (p and p.is_dir()) else []
    fp = _first_party_source()
    return [str(fp)] if fp else []


def first_party_sources() -> set[str]:
    """Paths that are first-party defaults — always present, NOT user-removable."""
    return set(_default_local_sources())


def list_local_sources() -> list[str]:
    """Local app-source dirs: the first-party default(s) FIRST (always present,
    read-only), then user-added ones. De-duped in order."""
    seen: set[str] = set()
    out: list[str] = []
    for path in (*_default_local_sources(), *_read_sources()["local"]):
        p = path.strip()
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def add_local_source(path: str) -> list[str]:
    """Add a local app-source directory; returns the updated local list. Rejects a
    non-directory (a source must be a dir of app subdirs, not a single app or a file)."""
    from pathlib import Path
    p = path.strip()
    if not p:
        raise ValueError("empty source path")
    if not Path(p).expanduser().is_dir():
        raise ValueError(f"not a directory: {p}")
    src = _read_sources()
    if p not in src["local"]:
        src["local"].append(p)
        _write_sources(src)
    return src["local"]


def remove_local_source(path: str) -> list[str]:
    """Remove a USER-added local app-source directory. A first-party default source
    is read-only (always present) and cannot be removed."""
    p = path.strip()
    if p in first_party_sources():
        raise ValueError("cannot remove a first-party (built-in) app source")
    src = _read_sources()
    src["local"] = [x for x in src["local"] if x != p]
    _write_sources(src)
    return src["local"]


def _manifest_consent(m: AppManifest) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(permissions, crons) an app declares — the P29 install-consent surface, extracted
    from a scanned manifest so the Store can show what the app will be granted + what
    recurring jobs it will run BEFORE install. Best-effort; empty on any shape surprise."""
    try:
        perms = m.permissions.to_dict() if m.permissions else {}
    except Exception:
        perms = {}
    crons: list[dict[str, Any]] = []
    try:
        for c in (m.crons or []):
            cd = c.to_dict() if hasattr(c, "to_dict") else {}
            # a compact, human-review summary: name + cadence + what it runs. A
            # manifest cron runs an AGENT with a MESSAGE (see app_crons: it becomes
            # make_agent_action(message=, agent=)) — there is no action/command field —
            # so "what it runs" is the agent + its prompt, straight from CronEntry.
            crons.append({
                "name": cd.get("name", ""),
                "every": cd.get("every", 0),
                "cron_expr": cd.get("cron_expr", ""),
                "agent": cd.get("agent", ""),
                "message": cd.get("message", ""),
            })
    except Exception:
        crons = []
    return perms, crons


def _scan_local_sources() -> list[CatalogEntry]:
    """Scan each configured local source dir for immediate subdirs with a valid
    ``app.json``, surfacing them as one-click-installable catalog entries (mirrors
    ``available_bundled``'s manifest read). Skips apps already in the Library."""
    from pathlib import Path
    installed = _installed_names()
    out: list[CatalogEntry] = []
    seen: set[str] = set()
    for root in list_local_sources():
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            manifest_file = entry / "app.json" if entry.is_dir() else None
            if not manifest_file or not manifest_file.is_file():
                continue
            try:
                m = AppManifest.from_json_file(manifest_file)
            except Exception:
                logger.warning("catalog: bad local manifest %s", entry, exc_info=True)
                continue
            if m.name in installed or m.name in seen:
                continue
            seen.add(m.name)
            # First-party default source → badge as "first-party"; user dirs → "local".
            kind = "first-party" if root in first_party_sources() else "local"
            _perms, _crons = _manifest_consent(m)
            out.append(CatalogEntry(
                name=m.name, displayName=m.displayName or m.name, description=m.description,
                version=m.version, icon=m.icon,
                heroUrl=resolve_hero_url(entry, m.heroImage),
                author=m.author, source=str(entry), sourceKind=kind,
                isProvider=bool(m.provider), providerType=(m.provider.type if m.provider else ""),
                tags=list(m.tags), permissions=_perms, crons=_crons,
            ))
    return out


# ---------------------------------------------------------------------------
# Available-app enumeration
# ---------------------------------------------------------------------------

def _bundled_dir() -> Path:
    from personalclaw.providers.loader import BUNDLED_DIR

    return BUNDLED_DIR


def _installed_names() -> set[str]:
    from personalclaw.apps.manager import list_apps

    return {a.get("name", "") for a in list_apps()}


def installed_logger_roots() -> tuple[str, ...]:
    """Top-level logger namespaces that ENABLED installed apps log under (their own
    root, not ``personalclaw``) — read from each app's manifest ``loggerRoots``.

    This is the runtime replacement for the hard-coded ``constants.APP_LOGGER_ROOTS``:
    the set of app log roots is derived from what's actually installed + enabled, so
    log-level plumbing (CLI boot + the /api/logs/level endpoint) applies the level +
    file handler to each app's logger too — no source edit when an app ships a new root.

    Manifest-only (reads ``list_apps()``'s scanned manifest dict — no app import/exec),
    enabled apps only, de-duped preserving first-seen order. Returns ``()`` when no apps
    dir exists yet (a fresh install), so callers degrade to just ``personalclaw``."""
    from personalclaw.apps.manager import apps_dir, list_apps

    if not apps_dir().is_dir():
        return ()
    seen: set[str] = set()
    out: list[str] = []
    for app in list_apps():
        if not app.get("enabled", True):
            continue
        manifest = app.get("manifest") or {}
        for root in manifest.get("loggerRoots") or []:
            r = str(root).strip()
            if r and r not in seen:
                seen.add(r)
                out.append(r)
    return tuple(out)


def available_bundled() -> list[CatalogEntry]:
    """Native manifests not currently in the Library — installable from
    their on-disk path.

    Native apps are seeded ENABLED at first run and are locked-on (can't be
    uninstalled), so in normal operation none are ever "available but absent" and
    this returns empty. It stays as a defensive self-heal: if a native app's
    installed record is somehow missing (a corrupted state), it resurfaces here so
    the seed path (or a manual re-add) can restore it — native apps are mandatory."""
    bundled = _bundled_dir()
    if not bundled.is_dir():
        return []
    installed = _installed_names()
    out: list[CatalogEntry] = []
    for entry in sorted(bundled.iterdir()):
        manifest_file = entry / "app.json" if entry.is_dir() else None
        if not manifest_file or not manifest_file.is_file():
            continue
        try:
            m = AppManifest.from_json_file(manifest_file)
        except Exception:
            logger.warning("catalog: bad native manifest %s", entry.name, exc_info=True)
            continue
        if not m.native:
            continue  # only native apps live in this dir; skip a stray non-native
        if m.name in installed:
            continue  # already in the Library (the normal case)
        _perms, _crons = _manifest_consent(m)
        out.append(CatalogEntry(
            name=m.name, displayName=m.displayName or m.name, description=m.description,
            version=m.version, icon=m.icon,
            heroUrl=resolve_hero_url(entry, m.heroImage),
            author=m.author, source=str(entry), sourceKind="native",
            isProvider=bool(m.provider), providerType=(m.provider.type if m.provider else ""),
            tags=list(m.tags), permissions=_perms, crons=_crons,
        ))
    return out


def available_catalog() -> dict[str, Any]:
    """The full Store catalog: available bundled apps + configured git sources +
    local sources (with their scanned, one-click-installable apps).

    Git sources are returned as-is (URL list) — resolving each to a manifest means
    cloning, which we defer to install time (behind the scanner gate). Local sources
    ARE scanned (cheap on-disk manifest read) so their apps surface as install cards,
    like the bundled section. The UI lists sources as 'add by source' + offers direct
    install (by URL for git, by discovered card for local).
    """
    import time
    now = time.time()
    return {
        "bundled": [e.to_dict() for e in available_bundled()],
        "gitSources": list_git_sources(),
        "localSources": list_local_sources(),
        # Which localSources are first-party defaults (read-only, not removable) so
        # the UI can label them + hide the remove control.
        "firstPartySources": sorted(first_party_sources()),
        "localApps": [e.to_dict() for e in _scan_local_sources()],
        # P20: apps enumerated from a source's app-registry.json pointer index (git +
        # local) WITHOUT cloning each — install cards that route through the normal
        # scanner-gated install via their `pointer`. Empty when no source publishes an
        # index (the git-URL list + localApps dir-scan remain the fallback).
        "remoteApps": [e.to_dict() for e in _scan_registries(now=now)],
        # Multi-app git repos without a registry index: shallow-clone + subdir
        # scan (mirrors _scan_local_sources for git). Cached per-URL, 5 min TTL.
        "gitApps": [e.to_dict() for e in _scan_git_sources(now=now)],
    }
