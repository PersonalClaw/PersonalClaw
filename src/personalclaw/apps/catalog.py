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

from personalclaw.apps.manifest import AppManifest, version_tuple
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
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
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


# Git source URLs PersonalClaw ships as Store defaults. The published first-party
# apps repo is a default source, so a shipped ``pip install`` surfaces every
# first-party app in the Store WITHOUT the dev workspace tree — uninstalled, so the
# per-app install-consent contract is preserved (nothing runs until the user opts
# in). User-added URLs accumulate alongside these; a bundled default is not
# user-removable. This is a Store-listing default only — it never auto-installs.
_DEFAULT_GIT_SOURCES: tuple[str, ...] = ("https://github.com/PersonalClaw/PersonalClawApps.git",)

# The curated app REGISTRY (ECOSYSTEM-TOOLING T2.2) — a SEEDED default, deliberately NOT a
# member of the tuple above. That distinction IS the mechanism: ``_DEFAULT_GIT_SOURCES`` is
# folded into every read of :func:`list_git_sources`, so a bundled default cannot be removed
# (the next read puts it back) — which is exactly why the docstring above says "not
# user-removable". The registry has to be REMOVABLE, so instead of being folded in on read it
# is written ONCE into ``app-sources.json`` as an ordinary row (:func:`seed_default_git_sources`,
# run at gateway start) alongside a marker recording that the seed already happened. From then
# on it is a normal user entry: the existing DELETE removes it, and the marker — which survives
# the removal — is what stops the next start from seeding it again.
#
# Gated by ``apps.registry_source_enabled``: flag off means the seed never runs, so an operator
# who does not want a shipped NETWORK source never acquires one. Listing-only either way — a
# source contributes Store cards, and installing one still goes through the single scanner-gated
# install path (nothing is fetched-and-run without explicit per-app consent).
_REGISTRY_GIT_SOURCE = "https://github.com/PersonalClaw/registry.git"

# Marker recorded in the sources file once the registry seed has run. Its ABSENCE means "never
# seeded"; its PRESENCE is what makes a removal stick across restarts.
_SEEDED_REGISTRY_KEY = "registry"


def _first_party_source() -> Path | None:
    """The always-present, read-only FIRST-PARTY app source — DEV filesystem path.

    First-party apps live in the workspace ``apps/`` dir (a sibling of the
    ``PersonalClaw/`` core repo). This is the DEV convenience source: when you're
    working out of the workspace tree, the apps beside core surface in the Store
    without network. A SHIPPED install has no workspace tree, so this returns None
    there — the published apps repo in ``_DEFAULT_GIT_SOURCES`` is what makes
    first-party apps appear on a plain ``pip install`` (uninstalled — the user opts
    in). Resolved relative to the package: ``.../PersonalClaw/src/personalclaw/`` →
    ``.../PersonalClaw/`` → ``../apps``. Not user-removable (not in the persisted
    list)."""
    # catalog.py is at src/personalclaw/apps/catalog.py → parents: apps, personalclaw,
    # src, PersonalClaw, <workspace>. The workspace holds apps/ beside PersonalClaw/.
    workspace_apps = Path(__file__).resolve().parents[4] / "apps"
    return workspace_apps if workspace_apps.is_dir() else None


# Env override so a packaged/relocated install can point at a local first-party dir
# (e.g. this workspace's PersonalClawApps clone) instead of the published git source
# in _DEFAULT_GIT_SOURCES — used for offline dev + tests.
import os as _os  # noqa: E402

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
    source: str = ""  # install source: a local path (bundled) or git URL
    sourceKind: str = "bundled"  # noqa: N815 — "bundled" | "git"
    isProvider: bool = False  # noqa: N815
    providerType: str = ""  # noqa: N815
    # The provider's DECLARED capabilities (``provider.capabilities``: chat, stt, tts,
    # search, messaging, …). ``providerType`` alone cannot tell a chat model from a
    # speech model — faster-whisper (stt) and piper-tts (tts) are both
    # ``providerType: "model"``, so a surface that groups apps by what they DO (the
    # onboarding essential-apps step) needs the capability list, not free-text ``tags``,
    # which are author-controlled and unvalidated. Empty for a non-provider app or for a
    # registry-index pointer whose manifest has not been fetched yet.
    providerCapabilities: list[str] = field(default_factory=list)  # noqa: N815
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
    repo: str = ""  # git URL (or path) to clone/read at install; "" → same source
    branch: str = ""  # optional ref
    subdirectory: str = ""  # optional path within the repo where app.json lives
    displayName: str = ""  # noqa: N815 — index hint
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
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--no-checkout", "--", url, tmp],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if proc.returncode != 0:
            logger.debug(
                "app registry: git fetch failed for %s: %s", url, (proc.stderr or "")[-200:]
            )
            return None
        # Pull just the index file out of the tree without checking out the rest.
        show = subprocess.run(
            ["git", "-C", tmp, "show", f"HEAD:{_REGISTRY_FILENAME}"],
            capture_output=True,
            text=True,
            timeout=30,
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
        name=p.name,
        displayName=p.displayName or p.name,
        description=p.description,
        version=p.version,
        icon=p.icon,
        author=p.author,
        source=source,
        sourceKind="git" if is_git else "local",
        tags=list(p.tags),
        pointer=pointer,
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
            capture_output=True,
            text=True,
            timeout=90,
        )
        if proc.returncode != 0:
            logger.debug(
                "git scan: clone failed for %s: %s",
                url,
                (proc.stderr or "")[-200:],
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
                    entry.name,
                    url,
                    exc_info=True,
                )
                continue
            if m.name in installed or m.name in seen:
                continue
            seen.add(m.name)
            _perms, _crons = _manifest_consent(m)
            entries.append(
                CatalogEntry(
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
                    providerType=(m.provider.type if m.provider else ""),
                    providerCapabilities=(list(m.provider.capabilities) if m.provider else []),
                    tags=list(m.tags),
                    pointer=f"{url}#{entry.name}",
                    permissions=_perms,
                    crons=_crons,
                )
            )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        logger.debug(
            "git scan: error scanning %s",
            url,
            exc_info=True,
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


def _git_source_key(url: str) -> str:
    """The identity of a git source for de-duplication.

    One repository typed two ways is ONE source: GitHub serves the published apps repo
    at both ``…/PersonalClawApps`` and ``…/PersonalClawApps.git``, so comparing raw
    strings lets a user "add" a repo that already ships as a default and pay a second
    full shallow clone per catalog refresh for zero extra apps.

    A comparison key ONLY — the original string is what gets cloned, because the suffix
    is load-bearing for some remotes (a bare repo at ``file:///…/apps.git`` does not
    exist without it). Case is preserved: some hosts serve case-sensitive paths."""
    return url.strip().rstrip("/").removesuffix(".git")


def list_git_sources() -> list[str]:
    """The configured git source URLs (defaults + user-added), de-duped in order.

    De-duped by :func:`_git_source_key`, so a default and a user entry naming the same
    repo collapse to the default (listed first)."""
    seen: set[str] = set()
    out: list[str] = []
    for url in (*_DEFAULT_GIT_SOURCES, *_read_user_sources()):
        u = url.strip()
        if u and (key := _git_source_key(u)) not in seen:
            seen.add(key)
            out.append(u)
    return out


def _read_sources() -> dict[str, list[str]]:
    """The typed user-sources store ``{"git": [...], "local": [...], "seeded": [...]}``.

    Back-reads the legacy flat ``{"sources": [urls]}`` shape (git-only) as ``git`` so
    an existing sources file upgrades transparently on the next write.

    ``seeded`` holds the markers of shipped sources already written into ``git`` once
    (currently just ``"registry"``). It is NOT a source list — it is the record that lets
    a seeded default stay removed: removing the row leaves the marker, so the next start
    does not re-seed it."""
    p = _sources_path()
    if not p.is_file():
        return {"git": [], "local": [], "seeded": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("failed to read app sources list", exc_info=True)
        return {"git": [], "local": [], "seeded": []}
    git = [str(u) for u in data.get("git", data.get("sources", [])) if str(u).strip()]
    local = [str(u) for u in data.get("local", []) if str(u).strip()]
    seeded = [str(u) for u in data.get("seeded", []) if str(u).strip()]
    return {"git": git, "local": local, "seeded": seeded}


def _write_sources(sources: dict[str, list[str]]) -> None:
    p = _sources_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        p,
        json.dumps(
            {
                "git": sources.get("git", []),
                "local": sources.get("local", []),
                # Persisted by every write path, not just the seeder: add/remove read the
                # whole dict and write it back, so dropping this key here would erase the
                # marker on the next source edit and silently resurrect a removed default.
                "seeded": sources.get("seeded", []),
            },
            indent=2,
        )
        + "\n",
    )


def _read_user_sources() -> list[str]:
    """Legacy shim: the user GIT sources only (used by list_git_sources)."""
    return _read_sources()["git"]


def add_git_source(url: str) -> list[str]:
    """Add a user git source URL; returns the updated USER git list (excludes defaults).

    Idempotent by :func:`_git_source_key`: re-adding a repo already configured — as a
    user entry OR as a bundled default, with or without a ``.git`` suffix — is a no-op,
    so the Store never lists one repository twice or clones it twice per refresh."""
    u = url.strip()
    if not u:
        raise ValueError("empty source URL")
    src = _read_sources()
    key = _git_source_key(u)
    known = {_git_source_key(x) for x in (*_DEFAULT_GIT_SOURCES, *src["git"])}
    if key not in known:
        src["git"].append(u)
        _write_sources(src)
    return src["git"]


def remove_git_source(url: str) -> list[str]:
    """Remove a user git source URL (a bundled default can't be removed).

    Matched by :func:`_git_source_key`, so the URL that removes a source is any spelling
    of the one that added it."""
    key = _git_source_key(url)
    src = _read_sources()
    src["git"] = [x for x in src["git"] if _git_source_key(x) != key]
    _write_sources(src)
    return src["git"]


def seed_default_git_sources() -> list[str]:
    """Write the shipped registry git source into ``app-sources.json`` — once, ever.

    Run at gateway start (``_app_sources_seed_startup``). Returns the URLs actually seeded:
    empty on every start after the first, empty when the row is already configured, and
    empty whenever ``apps.registry_source_enabled`` is off.

    The marker is recorded ONLY when the flag is on, so flipping the flag on later still
    seeds; and it is recorded even if the row was already present by another route, so the
    seeder never fights a user who added the registry by hand.

    Config-read failures are non-fatal and seed NOTHING: a shipped network source is
    opt-out-able state, and the safe direction for an unreadable config is to add no source
    the operator never saw."""
    from personalclaw.config.loader import AppConfig

    try:
        enabled = bool(AppConfig.load().apps.registry_source_enabled)
    except Exception:
        logger.warning("could not read apps.registry_source_enabled; not seeding", exc_info=True)
        return []
    if not enabled:
        return []
    src = _read_sources()
    if _SEEDED_REGISTRY_KEY in src["seeded"]:
        return []
    seeded: list[str] = []
    known = {_git_source_key(x) for x in (*_DEFAULT_GIT_SOURCES, *src["git"])}
    if _git_source_key(_REGISTRY_GIT_SOURCE) not in known:
        src["git"].append(_REGISTRY_GIT_SOURCE)
        seeded.append(_REGISTRY_GIT_SOURCE)
    src["seeded"].append(_SEEDED_REGISTRY_KEY)
    _write_sources(src)
    return seeded


def default_git_sources() -> list[str]:
    """Which CURRENTLY-LISTED git sources PersonalClaw itself put there (as listed).

    The bundled tuple plus the seeded registry. The Store labels these "Default" so a user
    can tell a shipped source from one they typed. Matched by :func:`_git_source_key`, so a
    default spelled with or without ``.git`` still reads as a default."""
    keys = {_git_source_key(u) for u in (*_DEFAULT_GIT_SOURCES, _REGISTRY_GIT_SOURCE)}
    return [u for u in list_git_sources() if _git_source_key(u) in keys]


def builtin_git_sources() -> list[str]:
    """The listed git sources that CANNOT be removed — the bundled tuple only.

    Folded into every read of :func:`list_git_sources`, so ``remove_git_source`` on one is a
    no-op by construction; the Store hides the remove control for these rather than offering
    a button that silently does nothing. The seeded registry is deliberately absent: it is a
    real row in the sources file and removing it persists (T2.2)."""
    keys = {_git_source_key(u) for u in _DEFAULT_GIT_SOURCES}
    return [u for u in list_git_sources() if _git_source_key(u) in keys]


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
        for c in m.crons or []:
            cd = c.to_dict() if hasattr(c, "to_dict") else {}
            # a compact, human-review summary: name + cadence + what it runs. A
            # manifest cron runs an AGENT with a MESSAGE (see app_crons: it becomes
            # make_agent_action(message=, agent=)) — there is no action/command field —
            # so "what it runs" is the agent + its prompt, straight from CronEntry.
            crons.append(
                {
                    "name": cd.get("name", ""),
                    "every": cd.get("every", 0),
                    "cron_expr": cd.get("cron_expr", ""),
                    "agent": cd.get("agent", ""),
                    "message": cd.get("message", ""),
                }
            )
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
            out.append(
                CatalogEntry(
                    name=m.name,
                    displayName=m.displayName or m.name,
                    description=m.description,
                    version=m.version,
                    icon=m.icon,
                    heroUrl=resolve_hero_url(entry, m.heroImage),
                    author=m.author,
                    source=str(entry),
                    sourceKind=kind,
                    isProvider=bool(m.provider),
                    providerType=(m.provider.type if m.provider else ""),
                    providerCapabilities=(list(m.provider.capabilities) if m.provider else []),
                    tags=list(m.tags),
                    permissions=_perms,
                    crons=_crons,
                )
            )
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
        out.append(
            CatalogEntry(
                name=m.name,
                displayName=m.displayName or m.name,
                description=m.description,
                version=m.version,
                icon=m.icon,
                heroUrl=resolve_hero_url(entry, m.heroImage),
                author=m.author,
                source=str(entry),
                sourceKind="native",
                isProvider=bool(m.provider),
                providerType=(m.provider.type if m.provider else ""),
                providerCapabilities=(list(m.provider.capabilities) if m.provider else []),
                tags=list(m.tags),
                permissions=_perms,
                crons=_crons,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Update surfacing (APE-7)
#
# An installed app's SOURCE may offer a newer version than the copy on disk. We
# surface that WITHOUT a polling loop: the latest-available version is computed on
# the existing ``/api/apps`` read path from CHEAP, on-disk local-source manifest
# reads (the same dir-scan ``_scan_local_sources`` does) — no network clone on the
# hot path. The Store keeps its own (cached, network-capable) discovery for BROWSING;
# this is the always-cheap "is anything I already have out of date?" check.
#
# One notification per ``(name, latest_version)`` is delivered through the registered
# ``apps/update`` attention kind, deduped by a persisted ``entity_settings/app_updates.json``
# high-water mark so re-computing on every read never re-nags — only a version NEWER than the
# one already announced fires again.
# ---------------------------------------------------------------------------

_APP_UPDATES_ENTITY = "app_updates"


def _latest_local_versions() -> dict[str, str]:
    """``{app_name: highest version}`` discoverable across the configured LOCAL sources.

    Unlike ``_scan_local_sources`` (which OMITS installed apps, since it feeds the Store's
    "available to install" list), this includes every app a local source declares — because
    the whole point here is to compare an INSTALLED app against the newer copy its source now
    carries. On-disk manifest reads only; a bad manifest is skipped, never fatal."""
    from pathlib import Path

    latest: dict[str, str] = {}
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
                logger.debug("update check: bad local manifest %s", entry, exc_info=True)
                continue
            if not m.name or not m.version:
                continue
            current = latest.get(m.name)
            if current is None or version_tuple(m.version) > version_tuple(current):
                latest[m.name] = m.version
    return latest


def updates_available() -> list[dict[str, Any]]:
    """Installed apps whose local source now offers a NEWER version.

    Compares each installed app's on-disk version against the highest version the configured
    local sources declare for that app, using the single app-version comparator
    (``manifest.version_tuple``). Returns one entry per out-of-date app::

        {"name", "displayName", "installedVersion", "latestVersion", "source"}

    Pure + cheap (on-disk reads, no network, no side effects) — safe to call on the
    ``/api/apps`` read path. An app with no newer version, or with no source-side manifest,
    is simply absent."""
    from personalclaw.apps.manager import list_apps

    latest = _latest_local_versions()
    out: list[dict[str, Any]] = []
    for app in list_apps():
        name = app.get("name", "")
        installed_version = str(app.get("version", ""))
        latest_version = latest.get(name)
        if not name or not latest_version:
            continue
        if version_tuple(latest_version) > version_tuple(installed_version):
            manifest = app.get("manifest") or {}
            out.append(
                {
                    "name": name,
                    "displayName": manifest.get("displayName") or name,
                    "installedVersion": installed_version,
                    "latestVersion": latest_version,
                    "source": app.get("source", ""),
                }
            )
    return out


def _load_notified() -> dict[str, str]:
    """The per-app high-water mark of the latest version we've already notified about
    (``entity_settings/app_updates.json`` → ``{"notified": {name: version}}``). Tolerant:
    an unreadable/corrupt file means we've announced nothing (fail open — a duplicate
    notification is a lesser evil than a silently-swallowed one)."""
    try:
        from personalclaw.providers.entity_routes import _load_entity_settings

        data = _load_entity_settings(_APP_UPDATES_ENTITY)
        notified = data.get("notified") if isinstance(data, dict) else None
        return {str(k): str(v) for k, v in notified.items()} if isinstance(notified, dict) else {}
    except Exception:
        logger.debug("app-update notified state unreadable", exc_info=True)
        return {}


def _save_notified(notified: dict[str, str]) -> None:
    from personalclaw.providers.entity_routes import _save_entity_settings

    _save_entity_settings(_APP_UPDATES_ENTITY, {"notified": notified})


def surface_app_updates(state: Any) -> list[dict[str, Any]]:
    """Compute available updates AND emit ONE notification per newly-available version.

    The dedup contract (APE-7): a notification fires the first time an app's source offers a
    given ``latestVersion``, and never again for that version — even after the inbox row is
    dismissed — because the high-water mark is persisted OUTSIDE the inbox
    (``entity_settings/app_updates.json``), keyed by ``name``. Only a version strictly newer
    than the one last announced re-fires. Emission routes through the registered
    ``apps/update`` attention kind via ``emit_attention_item`` (dual-honesty: even if the
    kind's delivery rule is muted, the inbox row still lands and ``state.notify`` still runs —
    the rules layer, not this code, decides whether to toast).

    Returns the same list as :func:`updates_available` so a caller on the read path can attach
    it to its response without recomputing. Best-effort: a persistence/emit error is logged and
    never breaks the read path."""
    updates = updates_available()
    if state is None:
        return updates
    try:
        notified = _load_notified()
    except Exception:
        notified = {}
    changed = False
    for u in updates:
        name = u["name"]
        latest_version = u["latestVersion"]
        already = notified.get(name, "")
        if version_tuple(latest_version) > version_tuple(already):
            _emit_app_update(state, u)
            notified[name] = latest_version
            changed = True
    if changed:
        try:
            _save_notified(notified)
        except Exception:
            logger.warning("could not persist app-update notified state", exc_info=True)
    return updates


def _emit_app_update(state: Any, update: dict[str, Any]) -> None:
    from personalclaw.inbox import emit_attention_item

    name = update["name"]
    display = update.get("displayName") or name
    latest_version = update["latestVersion"]
    installed_version = update.get("installedVersion", "")
    try:
        emit_attention_item(
            state,
            source="apps",
            kind="update",
            title=f"Update available for {display}",
            body=f"Version {latest_version} is available (you have {installed_version}).",
            refs={"app": name, "latest_version": latest_version},
            # Dedup within the inbox on the exact version too; the persisted high-water mark
            # above is the durable "never re-nag" guarantee, this just avoids a duplicate row
            # if the same version is surfaced twice before the mark is written.
            dedup_key=f"app_update:{name}:{latest_version}",
        )
    except Exception:
        logger.warning("app-update notification failed for %s", name, exc_info=True)


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
        # Which gitSources PersonalClaw shipped (label "Default") and which of those are
        # bundled-and-unremovable (hide the remove control — see builtin_git_sources). The
        # seeded registry appears in the first list and NOT the second: it is a shipped
        # default the user may remove for good.
        "defaultGitSources": default_git_sources(),
        "builtinGitSources": builtin_git_sources(),
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
