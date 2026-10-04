"""Local static artifact deploy — the webapp serve registry and path spine.

Deploying an artifact makes its own bytes reachable at a stable in-gateway URL
(``/artifacts/serve/<slug>/<capability>/``) so an html/widget artifact can be opened and
driven as a real page instead of only rendered inside a chat bubble. Local-only: public
exposure is explicitly out of scope.

Four properties are load-bearing here, because this route serves
**model- or user-authored HTML** rather than shipped assets:

*An origin of its own.* Every response the route gives carries a CSP ``sandbox``
directive that allows scripts and nothing else, so the browser runs the page in an
opaque origin however it is opened — framed in the dashboard, in a tab, or by any other
route. From there it cannot reach the dashboard's window (no same-origin, no popups, no
top-level navigation), the owner's cookie, web storage, or a service worker of its own.
It is the response header (not a ``<meta>`` the document could omit) that carries it,
because the document is untrusted input.

*The capability.* A sandboxed page is cross-site to the gateway, so the browser sends its
own requests — its script, its stylesheet, its font — without the session cookie. What
authorizes them is the URL itself: each deployment holds an unguessable capability (256
bits), minted on every deploy and gone on teardown, and the route serves the deployment's
own files to whoever presents it and nothing to anyone without it. The dashboard's session
check lets exactly that path shape through to this route (``SERVED_PATH``), and nothing
else under the prefix. The capability is never written to a log: the audit names the slug.

*Containment.* A request path is resolved and asserted to live under the artifact's
own files root — never string-matched against ``..``. Marker rejection
(``..``/absolute/backslash/percent-encoded traversal) is a cheap first gate; the
resolve-and-contain assertion is the one that actually holds, and a symlink is
refused outright so the served set can never point outside the root.

*Teardown removes the route.* aiohttp freezes its router at startup, so the
"route" a user can tear down is this registry: the handler serves nothing for a
slug that is not deployed. Deleting an artifact tears its deployment down in the
store itself (``NativeArtifactProvider.delete``), whoever deletes it, and a new
artifact never inherits a row its slug left behind. An artifact deleted but still
reachable — or a later artifact served at its URL — is precisely the defect the
teardown clause exists to prevent.

The CSP stays the floor inside the sandbox: ``connect-src 'none'`` (no fetch, XHR,
WebSocket or beacon), ``form-action``/``base-uri``/``object-src 'none'``. Navigation is
not a CSP matter: framed, the page cannot navigate the dashboard (no top navigation) and
the dashboard's ``frame-src`` keeps the frame on this origin; in a tab of its own it can
still navigate itself away, which reaches nothing of the dashboard's.

Persistence lives at ``<home>/artifacts/deployments.json`` — inside the artifacts
tree, so the existing ``artifacts`` durability inventory entry already covers it
and the provider's directory-only ``list`` ignores it (same bargain as
``folders.json``).
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from personalclaw.artifacts.models import is_valid_slug
from personalclaw.atomic_write import atomic_write
from personalclaw.config import loader as config_loader
from personalclaw.security import is_sensitive_path


def config_dir() -> Path:
    """The active home, re-resolved per call — see :func:`personalclaw.config.loader.config_dir`.

    DEFINED here rather than imported: this module can be imported lazily, and an
    import-time binding captures whatever the name pointed at on first use (#2443).
    """
    return config_loader.config_dir()


logger = logging.getLogger(__name__)

#: In-gateway URL prefix for a deployed artifact. Deliberately NOT under ``/api``:
#: it serves a document, and keeping it off the API namespace is what lets the CSP
#: fence below read as "this origin's /api is not reachable from here".
SERVE_URL_PREFIX = "/artifacts/serve"

#: Bytes of randomness in a deployment's capability: 256 bits, so the URL cannot be guessed.
CAPABILITY_BYTES = 32

#: A capability as ``secrets.token_urlsafe(CAPABILITY_BYTES)`` spells it: 43 characters of the
#: URL-safe base64 alphabet.
_CAPABILITY = re.compile(r"[A-Za-z0-9_-]{43}")

#: The one path shape the dashboard's session check lets through to the serve route without a
#: session: a slug, then a capability, then (optionally) a file under it. The route authorizes it
#: itself, by the capability; every other path under the prefix stays behind the session.
SERVED_PATH = re.compile(rf"{SERVE_URL_PREFIX}/[a-z0-9-]{{1,80}}/{_CAPABILITY.pattern}(?:/.*)?")

#: Default entry document for a deployed artifact (the webapp contract: a
#: multi-file artifact's entry is ``index.html``).
DEFAULT_ENTRY = "index.html"

#: Kinds whose body is a servable document. A markdown/json/image artifact has a
#: reader already and would only be a confusing thing to "deploy".
DEPLOYABLE_KINDS = frozenset({"widget", "html", "react"})

#: Bounds the registry file and the deployed-app listing.
MAX_DEPLOYMENTS = 200

#: Sub-directory of an artifact's own directory holding extra static files (css/js/
#: assets, or a built bundle). The entry document falls back to the
#: artifact's single body when this directory holds no entry, so a plain
#: single-file html artifact deploys with nothing extra on disk.
FILES_SUBDIR = "webapp"

#: The fence. Mirrors ``web/src/ui/widget/widgetSrcdoc.ts`` (the widget iframe's
#: own CSP) so a deployed widget behaves the same served as embedded, with ``'self'``
#: added where a multi-file webapp must load its OWN files. The directives that make
#: it a fence rather than a formality:
#:
#: * ``sandbox allow-scripts`` — the page runs in an opaque origin, framed or not: its
#:   scripts run, and it gets no same-origin access to the dashboard, no popups, no
#:   top-level navigation, no forms, no cookie, no web storage and no service worker.
#:   Measured in Chromium: ``'self'`` below still matches this response's origin for the
#:   page's own files (CSP matches it against the URL the policy came from).
#: * ``default-src 'none'`` — nothing is fetchable unless a directive below allows it.
#: * no other origin in ``script-src``/``style-src``/``font-src`` — a served page runs its
#:   own files and inline code only, and reaches no third party (a react artifact's bundle
#:   carries React itself; see ``build.py``).
#: * ``connect-src 'none'`` — no fetch/XHR/WebSocket/EventSource/sendBeacon at all.
#: * ``worker-src 'none'`` — no worker of any kind; without it the directive fell back to
#:   ``script-src 'self'``, and a service worker would keep answering the page's URL after
#:   the deployment came down.
#: * ``form-action 'none'`` + ``base-uri 'none'`` — no exfiltration by form POST and
#:   no rewriting relative URLs out from under the other directives.
#: * ``frame-ancestors 'self'`` — embeddable in the dashboard's own pane, nowhere else.
ARTIFACT_SERVE_CSP = (
    "default-src 'none'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'none'; "
    "worker-src 'none'; "
    "form-action 'none'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'self'; "
    "sandbox allow-scripts"
)

#: Powerful features a served page is never given, framed or in a tab of its own. Only names
#: the browser knows: an unknown one is a console warning on every load.
_DENIED_FEATURES = (
    "accelerometer",
    "bluetooth",
    "browsing-topics",
    "camera",
    "clipboard-read",
    "clipboard-write",
    "display-capture",
    "fullscreen",
    "gamepad",
    "geolocation",
    "gyroscope",
    "hid",
    "identity-credentials-get",
    "idle-detection",
    "local-fonts",
    "magnetometer",
    "microphone",
    "midi",
    "otp-credentials",
    "payment",
    "publickey-credentials-create",
    "publickey-credentials-get",
    "screen-wake-lock",
    "serial",
    "storage-access",
    "usb",
    "window-management",
    "xr-spatial-tracking",
)

#: Response headers every answer of the serve route carries — a refusal and a redirect as much
#: as the page and its files, so no answer at that path runs in the dashboard's origin.
SERVE_HEADERS: dict[str, str] = {
    "Content-Security-Policy": ARTIFACT_SERVE_CSP,
    # A window that opened the page (or that it is opened into) shares no browsing context with
    # it, whatever the link that opened it said.
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": ", ".join(f"{name}=()" for name in _DENIED_FEATURES),
    "X-Content-Type-Options": "nosniff",
    # Also what keeps the capability in the page's URL out of any request the page makes.
    "Referrer-Policy": "no-referrer",
    # The body is the artifact's live content and changes on every edit; a cached
    # copy would keep serving a torn-down deployment's page from the browser.
    "Cache-Control": "no-store",
}

#: What a served file adds. The page's module scripts and fonts are fetched in CORS mode, and from
#: its opaque origin every one of its requests is cross-origin, so without this they are blocked.
#: Opening nothing: the capability in the URL is the whole authority to read the file, and it is
#: already in the hand of whoever asks.
SERVED_FILE_HEADERS: dict[str, str] = {**SERVE_HEADERS, "Access-Control-Allow-Origin": "*"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ArtifactDeployment:
    """One deployed artifact: the slug, its entry document, when it went up, and the
    capability its URL carries."""

    slug: str
    entry: str = DEFAULT_ENTRY
    created_at: str = ""
    capability: str = ""

    @property
    def url(self) -> str:
        return f"{SERVE_URL_PREFIX}/{self.slug}/{self.capability}/"

    def admits(self, capability: str) -> bool:
        """Whether *capability* is this deployment's. Compared in constant time."""
        return bool(self.capability) and hmac.compare_digest(
            self.capability.encode(), capability.encode()
        )

    def to_dict(self) -> dict[str, Any]:
        """The record ``deployments.json`` keeps."""
        return {
            "slug": self.slug,
            "entry": self.entry,
            "created_at": self.created_at,
            "capability": self.capability,
        }

    def to_public(self) -> dict[str, Any]:
        """What the API shows the owner: the URL, which is how the capability is handed out."""
        return {
            "slug": self.slug,
            "entry": self.entry,
            "created_at": self.created_at,
            "url": self.url,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ArtifactDeployment":
        return cls(
            slug=str(d.get("slug", "")),
            entry=str(d.get("entry", "") or DEFAULT_ENTRY),
            created_at=str(d.get("created_at", "")),
            capability=str(d.get("capability", "")),
        )


#: Annotation alias — ``ArtifactDeployStore`` defines its own ``list()``, which
#: shadows the builtin in class scope (same reason as ``folders.py``).
_Deployments = list[ArtifactDeployment]


class ArtifactDeployStore:
    """Flat-JSON registry of deployed artifacts.

    Reads re-load from disk on every call (no cache), so a store constructed fresh
    against the same tree sees what another instance wrote — the same reload
    contract the folder store keeps.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) if root else (config_dir() / "artifacts")
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._root / "deployments.json"

    def files_root(self, slug: str) -> Path:
        """The static-file root for *slug*. Raises on an invalid slug.

        This is the containment boundary: nothing outside this directory may be
        served for *slug*, and the assertion is made against its resolved form.
        """
        if not is_valid_slug(slug):
            raise ValueError(f"invalid slug: {slug!r}")
        return self._root / slug / FILES_SUBDIR

    # ── persistence ──

    def _load(self) -> _Deployments:
        path = self.path
        if is_sensitive_path(str(path)) or not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            logger.warning("corrupt artifact deployments file: %s", path)
            return []
        if not isinstance(raw, list):
            return []
        out: _Deployments = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            dep = ArtifactDeployment.from_dict(entry)
            # A record whose slug would not validate, or that carries no capability (one
            # written before deployments had one), can never be served: no URL names it. So it
            # is dropped on read rather than kept as an unreachable row.
            if dep.slug and is_valid_slug(dep.slug) and _CAPABILITY.fullmatch(dep.capability):
                out.append(dep)
        return out

    def _save(self, deployments: _Deployments) -> None:
        root = self._root
        if is_sensitive_path(str(root)):
            raise PermissionError("artifact root resolves to a sensitive path")
        root.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps([d.to_dict() for d in deployments], indent=2))

    # ── reads ──

    def list(self) -> _Deployments:
        """Every deployment, newest first (the deployed-app listing's order)."""
        with self._lock:
            deployments = self._load()
        deployments.sort(key=lambda d: d.created_at, reverse=True)
        return deployments

    def get(self, slug: str) -> ArtifactDeployment | None:
        if not slug or not is_valid_slug(slug):
            return None
        with self._lock:
            return next((d for d in self._load() if d.slug == slug), None)

    def is_deployed(self, slug: str) -> bool:
        return self.get(slug) is not None

    def authorize(self, slug: str, capability: str) -> ArtifactDeployment | None:
        """*slug*'s deployment when *capability* is the one its URL carries, else ``None``.

        The serve route's whole authorization: nothing else about the request (a session, a
        header) stands in for it, and one ``None`` answers "not deployed", "torn down" and "not
        this deployment's capability" alike.
        """
        if not _CAPABILITY.fullmatch(capability or ""):
            return None
        dep = self.get(slug)
        if dep is None or not dep.admits(capability):
            return None
        return dep

    # ── writes ──

    def deploy(self, slug: str, *, entry: str = "") -> ArtifactDeployment:
        """Register *slug* as deployed, under a newly minted capability.

        Idempotent in effect — re-deploying refreshes the entry rather than erroring — but
        every deploy mints the capability afresh, so the URL handed out before stops
        answering: a re-deploy publishes the page again, at a URL of its own.
        """
        if not is_valid_slug(slug):
            raise ValueError(f"invalid slug: {slug!r}")
        clean_entry = (entry or "").strip() or DEFAULT_ENTRY
        # The entry is a path INSIDE the files root, so it takes the same refusal as
        # any request path — a deployment whose entry escapes must never be recorded.
        if rejects_path(clean_entry):
            raise ValueError(f"invalid entry: {entry!r}")
        capability = secrets.token_urlsafe(CAPABILITY_BYTES)
        with self._lock:
            deployments = self._load()
            existing = next((d for d in deployments if d.slug == slug), None)
            if existing is not None:
                existing.entry = clean_entry
                existing.capability = capability
                self._save(deployments)
                return existing
            if len(deployments) >= MAX_DEPLOYMENTS:
                raise ValueError(f"too many deployed artifacts (max {MAX_DEPLOYMENTS})")
            dep = ArtifactDeployment(
                slug=slug, entry=clean_entry, created_at=_now(), capability=capability
            )
            deployments.append(dep)
            self._save(deployments)
            return dep

    def teardown(self, slug: str) -> bool:
        """Remove *slug*'s deployment, and with it its capability. Returns whether anything
        was removed.

        Content is untouched: teardown un-publishes, it never destroys the artifact. The one
        function every un-publishing goes through — the REST undeploy, and the store's own
        delete and create (``NativeArtifactProvider``), so no caller of the store can leave a
        page up behind an artifact that is gone.
        """
        if not slug:
            return False
        with self._lock:
            deployments = self._load()
            remaining = [d for d in deployments if d.slug != slug]
            if len(remaining) == len(deployments):
                return False
            self._save(remaining)
            return True


# ── the path spine ──

#: Substrings that can only be an escape attempt in a request path. Checked on the
#: raw path AND on its once-unquoted form, so ``%2e%2e%2f`` is refused even where a
#: layer decodes late.
_REJECT_MARKERS = ("..", "\\", "\x00", "//", ":")


def rejects_path(rel_path: str) -> bool:
    """Whether *rel_path* is refused before any filesystem work.

    Cheap first gate only — containment below is the assertion that holds. Rejects
    absolute paths, home expansion, backslash and NUL, dot-dot in either raw or
    percent-decoded form, and ``:`` (a Windows drive or a URL scheme).
    """
    if not rel_path:
        return True
    candidates = [rel_path]
    once = unquote(rel_path)
    if once != rel_path:
        candidates.append(once)
    for candidate in candidates:
        if candidate.startswith("/") or candidate.startswith("~"):
            return True
        if any(marker in candidate for marker in _REJECT_MARKERS):
            return True
    return False


def resolve_served_file(files_root: Path, rel_path: str) -> Path | None:
    """Resolve *rel_path* under *files_root*, or ``None`` if it must be refused.

    Refuses: a rejected path shape (:func:`rejects_path`), anything whose resolved
    location is not contained by the resolved root, any symlinked component, and
    anything that is not a regular file (so a directory never yields an index).
    """
    if rejects_path(rel_path):
        return None
    try:
        root = files_root.resolve()
        candidate = root / rel_path
        real = candidate.resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    # Containment is ASSERTED on the resolved paths, not matched on the string.
    if real != root and root not in real.parents:
        return None
    # A symlink is refused outright: the served set is the artifact's own real files,
    # and following one is how a link planted under the root reaches outside it.
    probe = candidate
    while True:
        try:
            if probe.is_symlink():
                return None
        except OSError:
            return None
        if probe == root or root not in probe.parents:
            break
        probe = probe.parent
    try:
        if not real.is_file():
            return None
    except OSError:
        return None
    return real


def content_type_for(path: Path) -> str:
    """MIME type for a served file. ``.html`` is pinned rather than guessed."""
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        return "text/html"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"
