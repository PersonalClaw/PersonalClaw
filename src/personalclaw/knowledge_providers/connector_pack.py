"""Connector packs — a knowledge source kind whose app contributes PARSING only (§7.1).

The Fincept-style pattern (N thin scripts + a generated manifest) adapted to this project's
egress discipline. A connector pack is an ordinary app: it declares
``provider: {type: "knowledge", implementation: "provider:create_provider",
capabilities: ["source"]}`` and a ``sources[]`` manifest block of parse-only scripts. Its
three-line ``provider.py`` calls :func:`connector_pack_provider` from
``personalclaw.sdk.knowledge``, so the class that actually polls is CORE's — a pack cannot
substitute its own fetch even by accident, because it never writes one.

**The whole poll, in order.** ``poll`` resolves the source row's ``spec.pack_source`` to a
declared :class:`~personalclaw.apps.manifest.PackSourceEntry`, validates the user's ``args``
against that entry's ``argsSchema``, renders its ``fetchSpec`` (``{{args.x}}`` from the row,
``{{secret:KEY}}`` from the credential store, headers only), performs ONE request through
``net.fetch`` under the engine-supplied ``SOURCE`` egress policy, and hands the response body
to the script on stdin via :func:`~personalclaw.knowledge_providers.pack_parse.run_parse_script`.
The script emits ``SourceItem`` JSON lines; this module maps them onto the contract.

Nothing about that ordering is incidental:

* **Fetch before spawn.** The bytes exist before the untrusted code runs, so the untrusted
  code has no reason to want a socket and no route to one (``pack_parse`` enforces the
  route's absence).
* **Secrets in headers, never the URL** — enforced in the manifest schema, restated here
  because this is what renders them. A URL is written to the egress audit row, the remote
  access log and any redirect's ``Referer``.
* **A missing secret is a refusal, not a blank.** Sending ``Authorization: Bearer `` gets a
  401 the user cannot diagnose, or worse, succeeds against an endpoint that did not need it.
* **Poll-time re-validation.** The spec is a mutable row an MCP tool or hand-edit can change
  after save, and the thing it decides is a fetch target on a timer — so it is validated
  again here, for the reason ``web_source``/``dir_source`` already document.

Conditional GET rides in the cursor through the shared
:mod:`~personalclaw.knowledge_providers.conditional_get` helpers — the same ONE implementation
the feed and page kinds use, because a cursor is persisted state and a second copy of its
shape is a data divergence waiting to happen.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from personalclaw.knowledge_providers import conditional_get
from personalclaw.knowledge_providers.base import (
    HEALTH_DEGRADED,
    KnowledgeItem,
    KnowledgeSource,
    KnowledgeSourceProvider,
    SourceItem,
    SourcePollResult,
)
from personalclaw.knowledge_providers.pack_parse import (
    DEFAULT_TIMEOUT_SECS,
    ParseFailure,
    normalize_row,
    run_parse_script,
)

logger = logging.getLogger(__name__)

#: The ``kind`` a connector-pack WatchedSource row carries. Distinct from ``feed``/``web``/
#: ``dir`` so the UI can say which app owns a source and the health rollup can group them.
SOURCE_KIND = "pack"

#: The source-row spec keys. Deliberately two: WHICH declared source, and the user's args.
#: Everything else about the fetch lives in the app manifest, where it was reviewed at
#: install — a spec that could override the URL would make the manifest advisory.
SPEC_KEYS = ("pack_source", "args")


class PackConfigError(Exception):
    """A pack source cannot be polled as configured, and nothing was fetched (fail closed)."""


def load_manifest(app_dir: Path) -> Any:
    """The pack's parsed ``app.json``. Raises :class:`PackConfigError` if unreadable.

    Read fresh per poll rather than cached at construction: ``POST /api/apps/{name}/update``
    rewrites the installed manifest in place, and a provider serving a manifest from before an
    update would fetch a URL template the user can no longer see.
    """
    from personalclaw.apps.manifest import AppManifest

    path = Path(app_dir) / "app.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackConfigError(f"connector pack manifest unreadable at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PackConfigError(f"connector pack manifest at {path} is not an object")
    return AppManifest.from_dict(raw)


def _coerce_arg(name: str, value: Any, declared: dict[str, Any]) -> str:
    """One arg as a URL-safe string, or raise. Type-checked against its declaration."""
    kind = str(declared.get("type", "string") or "string")
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise PackConfigError(f"arg {name!r} must be an integer, got {type(value).__name__}")
        return str(value)
    if kind == "boolean":
        if not isinstance(value, bool):
            raise PackConfigError(f"arg {name!r} must be true or false")
        return "true" if value else "false"
    if not isinstance(value, str):
        raise PackConfigError(f"arg {name!r} must be a string, got {type(value).__name__}")
    return value


def validate_args(entry: Any, args: dict[str, Any]) -> dict[str, str]:
    """The user's ``args`` checked against ``entry.argsSchema``; returns rendered strings.

    Fail closed on an UNDECLARED arg as well as a missing required one. An undeclared arg is
    either a typo (so the required one is missing and the URL renders wrong) or an attempt to
    reach a template variable the manifest does not have — neither should quietly proceed.
    """
    schema = entry.argsSchema if isinstance(entry.argsSchema, dict) else {}
    unknown = sorted(set(args) - set(schema))
    if unknown:
        raise PackConfigError(
            f"source {entry.name!r} got undeclared arg(s) {unknown}; declared: "
            f"{sorted(schema) or 'none'}"
        )
    out: dict[str, str] = {}
    for name, decl in schema.items():
        decl = decl if isinstance(decl, dict) else {}
        if name in args:
            out[str(name)] = _coerce_arg(str(name), args[name], decl)
            continue
        if "default" in decl:
            out[str(name)] = _coerce_arg(str(name), decl["default"], decl)
            continue
        if decl.get("required"):
            raise PackConfigError(f"source {entry.name!r} requires arg {name!r}")
        out[str(name)] = ""
    return out


def _default_secret(name: str) -> str:
    """Resolve ``name`` from the credential store, or raise. Never returns a blank."""
    from personalclaw.config.loader import config_dir
    from personalclaw.llm.credentials import CredentialStore

    try:
        cred = CredentialStore(config_dir()).resolve(name)
    except KeyError as exc:
        raise PackConfigError(
            f"credential {name!r} is not configured; add it in Settings > Credentials "
            f"before enabling this source"
        ) from exc
    if not cred.secret:
        raise PackConfigError(f"credential {name!r} is configured but has no value")
    return cred.secret


def render_fetch(
    entry: Any,
    args: dict[str, str],
    *,
    secret_resolver: Callable[[str], str] | None = None,
) -> tuple[str, str, dict[str, str]]:
    """Render ``entry.fetchSpec`` to ``(url, method, headers)``.

    ``{{args.x}}`` is percent-encoded on the way into a URL (a repo name with a slash must not
    silently become an extra path segment) and left verbatim in a header. ``{{secret:KEY}}``
    resolves only in headers — the manifest schema refuses one in the URL, and this function
    would refuse it too, so the rule holds even for a hand-edited installed manifest.
    """
    from personalclaw.apps.manifest import PACK_FETCH_METHODS, PACK_PLACEHOLDER_RE

    resolve = secret_resolver or _default_secret
    spec = entry.fetchSpec if isinstance(entry.fetchSpec, dict) else {}

    def _sub(raw: str, *, in_url: bool) -> str:
        def _one(match: Any) -> str:
            token = match.group(1).strip()
            if token.startswith("args."):
                value = args.get(token[5:], "")
                return quote(value, safe="") if in_url else value
            if token.startswith("secret:"):
                if in_url:
                    raise PackConfigError(
                        f"source {entry.name!r} references a secret in its URL; secrets "
                        f"belong in headers (a URL reaches the audit log and the server's)"
                    )
                return resolve(token[7:])
            raise PackConfigError(f"source {entry.name!r} has unknown placeholder {token!r}")

        return PACK_PLACEHOLDER_RE.sub(_one, raw)

    url = _sub(str(spec.get("url", "") or ""), in_url=True)
    if not url.lower().startswith(("http://", "https://")):
        raise PackConfigError(f"source {entry.name!r} rendered a non-http(s) url {url[:80]!r}")
    method = str(spec.get("method", "GET") or "GET").upper()
    if method not in PACK_FETCH_METHODS:
        raise PackConfigError(f"source {entry.name!r} declares method {method!r}")
    headers: dict[str, str] = {}
    raw_headers = spec.get("headers") or {}
    if isinstance(raw_headers, dict):
        for header, value in raw_headers.items():
            headers[str(header)] = _sub(str(value), in_url=False)
    accept = str(spec.get("accept", "") or "")
    if accept:
        headers.setdefault("Accept", accept)
    return url, method, headers


class ConnectorPackProvider(KnowledgeSourceProvider):
    """The poll-capable provider a connector pack registers (§7.1).

    Core code driving a pack's declarations — which is the point. ``fetch_fn`` and ``parse_fn``
    are the two injectable seams, and they are the ONLY ways bytes enter or code runs: tier 1
    is ``net.fetch`` under the engine's ``SOURCE`` policy, and parsing is a fenced subprocess.
    A pack contributes neither.
    """

    #: An API a pack watches is somebody else's service. Conditional GET makes a shorter
    #: interval polite, but the engine still clamps this up to its own network floor.
    poll_interval_seconds = 1800

    def __init__(
        self,
        app_dir: str | Path,
        *,
        store: Any = None,
        fetch_fn: Callable[..., Any] | None = None,
        parse_fn: Callable[..., Any] | None = None,
        secret_resolver: Callable[[str], str] | None = None,
        timeout_secs: int = DEFAULT_TIMEOUT_SECS,
    ) -> None:
        self._app_dir = Path(app_dir)
        self._store_override = store
        self._fetch_fn = fetch_fn
        self._parse_fn = parse_fn
        self._secret_resolver = secret_resolver
        self._timeout_secs = timeout_secs
        manifest = load_manifest(self._app_dir)
        self._name = manifest.name
        self._display_name = manifest.displayName or manifest.name

    @property
    def store(self) -> Any:
        """The knowledge store. Resolved LAZILY through the process-wide accessor because an
        app's manifest factory is called with only its settings config — the same reason
        ``create_native_provider`` returns None rather than inventing a second store handle."""
        if self._store_override is None:
            from personalclaw.knowledge import get_knowledge_store

            self._store_override = get_knowledge_store()
        return self._store_override

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._display_name

    # ── corpus contract (the library owns search/get; see web_source) ────────────────

    async def list_sources(self) -> list[KnowledgeSource]:
        return [
            KnowledgeSource(id=s["id"], name=s["name"], source_type=SOURCE_KIND, provider=self.name)
            for s in self.store.list_sources()
            if s.get("provider") == self.name
        ]

    async def search(self, query: str, limit: int = 10) -> list[KnowledgeItem]:
        return []

    async def get_item(self, item_id: str) -> KnowledgeItem | None:
        return None

    # ── spec validation ─────────────────────────────────────────────────────────────

    def validate_spec(self, spec: dict) -> tuple[bool, str]:
        """Validate a pack-source row spec. Fail CLOSED, at save time AND at poll time."""
        spec = spec or {}
        if not isinstance(spec, dict):
            return False, "spec must be an object"
        unknown = sorted(set(spec) - set(SPEC_KEYS))
        if unknown:
            return False, f"spec: unknown key(s) {unknown}"
        pack_source = str(spec.get("pack_source", "") or "").strip()
        if not pack_source:
            return False, "spec.pack_source is required"
        args = spec.get("args", {})
        if args is not None and not isinstance(args, dict):
            return False, "spec.args must be an object"
        try:
            entry = self.resolve_entry(pack_source)
            validate_args(entry, dict(args or {}))
        except PackConfigError as exc:
            return False, str(exc)
        return True, ""

    def resolve_entry(self, pack_source: str) -> Any:
        """The declared source named ``pack_source``, or raise.

        Re-validates the entry itself, not only its presence: an installed manifest is a file
        on disk that an update or an edit can change after install, and the entry decides both
        a fetch target and which script runs. A row pointing at an entry that would now be
        REJECTED at install must not poll.
        """
        manifest = load_manifest(self._app_dir)
        entry = manifest.pack_source(pack_source)
        if entry is None:
            declared = [e.name for e in manifest.sources]
            raise PackConfigError(
                f"{self._name!r} declares no source {pack_source!r} (declared: "
                f"{declared or 'none'})"
            )
        errors = entry.validate()
        if errors:
            raise PackConfigError(f"source {pack_source!r} is invalid: {errors[0]}")
        return entry

    def script_path(self, entry: Any) -> Path:
        """The absolute parse-script path, confined to the app dir.

        The manifest schema already refuses ``..`` and an absolute path; this resolves symlinks
        and re-checks containment, because the schema guards the STRING and this guards the
        PATH — a symlink inside the app dir pointing at ``/etc`` passes the first and fails the
        second.
        """
        root = self._app_dir.resolve()
        target = (root / entry.script).resolve()
        if target != root and root not in target.parents:
            raise PackConfigError(
                f"source {entry.name!r} script {entry.script!r} resolves outside the app dir"
            )
        return target

    # ── fetch + parse seams ─────────────────────────────────────────────────────────

    async def _fetch(self, url: str, *, method: str, headers: dict[str, str], policy: Any) -> Any:
        """The ONE way bytes enter. Defaults to the guarded ``net.fetch``; ``fetch_fn``
        replaces it in tests so no test opens a socket."""
        if self._fetch_fn is not None:
            return await self._fetch_fn(url, policy=policy, method=method, headers=headers)
        from personalclaw.net.client import fetch

        if policy is None:
            # The engine supplies the SOURCE policy; a direct call must not fall through to
            # net.fetch's STRICT default silently — resolve the same posture explicitly.
            from personalclaw.net.policy import SOURCE, egress_policy_for

            policy = egress_policy_for(SOURCE)
        return await fetch(url, policy=policy, method=method, headers=headers)

    async def _parse(self, script: Path, body: bytes, args: dict[str, str]) -> Any:
        """Run the parse script off the event loop. It is a bounded subprocess, so a blocking
        call here would stall every other source's poll for its whole timeout."""
        import asyncio

        if self._parse_fn is not None:
            return self._parse_fn(script, body, args)
        return await asyncio.to_thread(
            run_parse_script, script, body, args, timeout_secs=self._timeout_secs
        )

    # ── the poll ────────────────────────────────────────────────────────────────────

    async def poll(
        self, source_id: str, cursor: str = "", *, policy: Any = None
    ) -> SourcePollResult:
        """One engine-mediated fetch + one fenced parse. Never raises to the engine (§1.1)."""
        source = self.store.get_source(source_id)
        if source is None:
            return SourcePollResult(error=f"source {source_id} no longer exists")
        spec = dict(source.get("spec") or {})
        ok, err = self.validate_spec(spec)
        if not ok:
            return SourcePollResult(cursor=cursor, error=err, health_status=HEALTH_DEGRADED)
        try:
            entry = self.resolve_entry(str(spec["pack_source"]))
            args = validate_args(entry, dict(spec.get("args") or {}))
            script = self.script_path(entry)
            url, method, headers = render_fetch(entry, args, secret_resolver=self._secret_resolver)
        except PackConfigError as exc:
            return SourcePollResult(cursor=cursor, error=str(exc), health_status=HEALTH_DEGRADED)

        validators = conditional_get.parse_validators(cursor)
        try:
            resp = await self._fetch(
                url,
                method=method,
                headers=conditional_get.conditional_headers(validators, **headers),
                policy=policy,
            )
        except Exception as exc:  # noqa: BLE001 — an egress denial is a soft poll failure
            return SourcePollResult(
                cursor=cursor,
                error=f"fetch failed: {str(exc)[:180]}",
                health_status=HEALTH_DEGRADED,
            )
        status = int(getattr(resp, "status", 0) or 0)
        if status == 304:
            # Validators returned verbatim: a 304 that dropped them would turn every later
            # poll into a full download.
            return SourcePollResult(items=[], cursor=cursor)
        if status >= 400:
            return SourcePollResult(
                cursor=cursor,
                error=f"{method} {url} returned HTTP {status}",
                health_status=HEALTH_DEGRADED,
            )
        new_cursor = (
            conditional_get.encode(conditional_get.validators_from(getattr(resp, "headers", {})))
            or cursor
        )

        body = getattr(resp, "body", b"") or b""
        try:
            parsed = await self._parse(script, bytes(body), args)
        except ParseFailure as exc:
            # The whole batch is discarded: `code` is the machine-readable reason and the
            # cursor does NOT advance, so the next poll retries the same position rather than
            # skipping past whatever the parser choked on.
            logger.warning("pack %s parse failed (%s): %s", self._name, exc.code, exc.detail)
            return SourcePollResult(
                cursor=cursor,
                error=f"{exc.code}: {exc.detail}",
                health_status=HEALTH_DEGRADED,
            )
        try:
            items = [SourceItem(**normalize_row(row, i)) for i, row in enumerate(parsed.rows)]
        except ParseFailure as exc:
            # A shape failure is the same all-or-nothing call as a malformed line: admitting
            # the well-formed rows would silently drop whichever ones the parser got wrong.
            return SourcePollResult(
                cursor=cursor,
                error=f"{exc.code}: {exc.detail}",
                health_status=HEALTH_DEGRADED,
            )
        return SourcePollResult(items=items, cursor=new_cursor)


def connector_pack_provider(app_ref: str | Path, config: dict | None = None, **kwargs: Any) -> Any:
    """The manifest factory a connector pack's ``provider.py`` calls (§7.1).

    ``app_ref`` may be the app directory or a file inside it (so a pack can pass ``__file__``).
    ``config`` is the app's ``ProviderSettings`` dict; ``timeout_secs`` is the only knob read
    from it, because everything else about the fetch is manifest-declared and reviewed at
    install rather than settable afterwards.

    A three-line pack ``provider.py``::

        from personalclaw.sdk.knowledge import connector_pack_provider

        def create_provider(config=None):
            return connector_pack_provider(__file__, config)
    """
    path = Path(app_ref)
    app_dir = path if path.is_dir() else path.parent
    settings = config or {}
    timeout = settings.get("parse_timeout_secs")
    if timeout not in (None, ""):
        try:
            kwargs.setdefault("timeout_secs", max(1, min(120, int(timeout))))
        except (TypeError, ValueError):
            logger.warning("connector pack %s: bad parse_timeout_secs %r", app_dir.name, timeout)
    return ConnectorPackProvider(app_dir, **kwargs)
