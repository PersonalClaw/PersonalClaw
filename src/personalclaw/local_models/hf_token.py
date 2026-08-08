"""HuggingFace token cascade — three sources, whoami-validated (LMMV §5).

One shared resolver replacing each provider's private two-source lookup. The token
is resolved from three sources, in priority order:

1. **credential store (.env)** — the value persisted under ``HF_TOKEN`` in
   ``<PERSONALCLAW_HOME>/.env`` (0600), what the Settings → Models field writes.
2. **environment** — ``HF_TOKEN`` / the legacy ``HUGGING_FACE_HUB_TOKEN``.
3. **HF CLI file** — ``~/.cache/huggingface/token`` (``$HF_HOME/token`` when set).

The FIRST source that has a token AND survives a live ``whoami`` call wins — an
invalid higher-priority token is skipped (with a per-source status) rather than
blocking a lower-priority valid one. ``whoami`` goes through the **``net.fetch``
CONNECTOR egress chokepoint** (never a hand-rolled HTTP call), and its result is
cached ~``whoami_ttl_s`` so list renders don't hammer HF.

Token VALUES never leave this module unmasked: :func:`hf_token_status` returns a
masked preview only (Success Criterion 4), and nothing here logs a raw token. The
set/clear writers touch source 1 only and are SEL-audited.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from personalclaw.net import CONNECTOR, egress_policy_for
from personalclaw.net.client import fetch

logger = logging.getLogger(__name__)

#: The credential-store key (source 1) the Settings field writes, and the primary
#: environment variable (source 2). HF's own libraries read ``HF_TOKEN`` too.
_TOKEN_KEY = "HF_TOKEN"
#: Legacy environment spelling still honored as part of source 2.
_LEGACY_ENV_KEY = "HUGGING_FACE_HUB_TOKEN"

#: Source identifiers surfaced in the status payload (stable, FE keys on them).
SOURCE_CREDENTIAL = "credential_store"
SOURCE_ENV = "environment"
SOURCE_HF_CLI = "hf_cli_file"

_DEFAULT_WHOAMI_TTL_S = 600

# ── whoami cache ────────────────────────────────────────────────────────────────
# Keyed by a digest of the token VALUE (never the value itself), so two tokens are
# cached independently and a rotated token isn't served a stale verdict. Cleared on
# every set/clear so a just-written token is re-validated immediately.
_whoami_cache: dict[str, "_WhoamiResult"] = {}


@dataclass(frozen=True)
class _WhoamiResult:
    valid: bool
    username: str | None
    expires_at: float  # monotonic


@dataclass(frozen=True)
class TokenSource:
    """One cascade source's status — never carries the raw token value.

    ``masked`` is a preview safe to render (``hf_…3jw``); ``username`` is populated
    only when the source has a token that passed ``whoami``.
    """

    source: str
    present: bool
    valid: bool
    username: str | None = None
    masked: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "present": self.present,
            "valid": self.valid,
            "username": self.username,
            "masked": self.masked,
        }


def _mask(token: str) -> str:
    """A render-safe preview of a token (``hf_…3jw``) — never the whole value.

    Shows the leading 3 and trailing 3 characters (the ``hf_`` prefix is not secret;
    the tail lets a user recognize *which* token is set) with the middle elided. Too
    short to preview safely → a bare ellipsis.
    """
    token = token.strip()
    if len(token) < 8:
        return "…"
    return f"{token[:3]}…{token[-3:]}"


def _whoami_ttl_s() -> int:
    """The whoami cache TTL from config (best-effort; default 600s).

    Read lazily + fail-soft so this module stays importable without a loaded config
    (early boot, unit tests)."""
    try:
        from personalclaw.config.loader import AppConfig

        return max(0, int(AppConfig.load().local_models.whoami_ttl_s))
    except Exception:
        return _DEFAULT_WHOAMI_TTL_S


# ── Source reads (network-free) ───────────────────────────────────────────────


def _env_file_path() -> Path:
    from personalclaw.config.loader import env_path

    return env_path()


def _read_credential_token() -> str | None:
    """Source 1: the ``HF_TOKEN`` value persisted in ``<home>/.env`` (the file, NOT the
    process environment — that is source 2, kept distinct on purpose)."""
    ep = _env_file_path()
    try:
        if not ep.exists():
            return None
        for line in ep.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, val = stripped.split("=", 1)
            if key.strip() == _TOKEN_KEY:
                return val.strip() or None
    except OSError:
        logger.debug("could not read %s for HF token", ep, exc_info=True)
    return None


def _read_env_token() -> str | None:
    """Source 2: ``HF_TOKEN`` then the legacy ``HUGGING_FACE_HUB_TOKEN`` from env."""
    for key in (_TOKEN_KEY, _LEGACY_ENV_KEY):
        val = os.environ.get(key)
        if val and val.strip():
            return val.strip()
    return None


def _hf_cli_token_path() -> Path:
    """``$HF_HOME/token`` when ``HF_HOME`` is set, else ``~/.cache/huggingface/token``."""
    hf_home = os.environ.get("HF_HOME")
    base = Path(hf_home) if hf_home else Path.home() / ".cache" / "huggingface"
    return base / "token"


def _read_hf_cli_token() -> str | None:
    """Source 3: the token the ``huggingface-cli login`` flow writes to disk."""
    path = _hf_cli_token_path()
    try:
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        logger.debug("could not read HF CLI token at %s", path, exc_info=True)
        return None


def _ordered_sources() -> list[tuple[str, str | None]]:
    """The three cascade sources in priority order → ``[(source_id, token|None), …]``."""
    return [
        (SOURCE_CREDENTIAL, _read_credential_token()),
        (SOURCE_ENV, _read_env_token()),
        (SOURCE_HF_CLI, _read_hf_cli_token()),
    ]


def hf_token_present() -> bool:
    """Whether ANY source holds a token — a cheap, NETWORK-FREE presence check.

    The gated-download pre-warn (§4.3) consumes this server-side: with no token in
    any source, a gated fetch is doomed, so the runner fails it fast with
    ``gated_repo:no_token`` instead of attempting a 401. Validity (``whoami``) is the
    async :func:`resolve_hf_token` / :func:`hf_token_status` path — presence is all a
    synchronous, hot fetch-start path can afford.
    """
    return any(tok for _src, tok in _ordered_sources())


# ── whoami validation (through the egress chokepoint) ──────────────────────────


async def _whoami(token: str) -> _WhoamiResult:
    """Validate ``token`` against HF's ``whoami-v2`` endpoint, cached ~``whoami_ttl_s``.

    The call goes through :func:`personalclaw.net.client.fetch` with the CONNECTOR
    egress policy (public-only, pinned IP, byte/timeout caps, SEL-audited) — never a
    hand-rolled client. A 200 yields ``valid=True`` + the account name; a 401/403
    yields ``valid=False``; a transport error yields ``valid=False`` (unverifiable ≠
    trusted). The verdict is cached by a digest of the token value.
    """
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.monotonic()
    cached = _whoami_cache.get(digest)
    if cached is not None and cached.expires_at > now:
        return cached

    valid = False
    username: str | None = None
    try:
        resp = await fetch(
            "https://huggingface.co/api/whoami-v2",
            policy=egress_policy_for(CONNECTOR),
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status == 200:
            try:
                data = json.loads(resp.body.decode("utf-8", "replace"))
                # whoami-v2 returns {"name": "...", "type": "user"|"org", ...}
                name = data.get("name") if isinstance(data, dict) else None
                if name:
                    valid = True
                    username = str(name)
            except (ValueError, AttributeError):
                logger.debug("HF whoami returned 200 with an unparseable body")
    except Exception:  # noqa: BLE001 — an unreachable HF must not raise into the caller
        logger.debug("HF whoami call failed", exc_info=True)

    result = _WhoamiResult(valid=valid, username=username, expires_at=now + _whoami_ttl_s())
    _whoami_cache[digest] = result
    return result


async def hf_token_status() -> dict[str, object]:
    """Per-source status for the Settings → Models surface (Success Criterion 4).

    Returns ``{sources: [{source, present, valid, username?, masked}], active_source,
    username}`` — the first whoami-valid source is ``active_source``. Token VALUES
    never appear: each source carries a :func:`_mask` preview only. A present but
    whoami-invalid higher-priority source is reported ``valid=False`` and skipped, so
    a lower-priority valid token still wins.
    """
    sources: list[TokenSource] = []
    active_source: str | None = None
    active_username: str | None = None

    for source_id, token in _ordered_sources():
        if not token:
            sources.append(TokenSource(source=source_id, present=False, valid=False))
            continue
        result = await _whoami(token)
        sources.append(
            TokenSource(
                source=source_id,
                present=True,
                valid=result.valid,
                username=result.username,
                masked=_mask(token),
            )
        )
        if result.valid and active_source is None:
            active_source = source_id
            active_username = result.username

    return {
        "sources": [s.to_dict() for s in sources],
        "active_source": active_source,
        "username": active_username,
    }


async def resolve_hf_token() -> str | None:
    """The winning token VALUE — the first source that has one AND passes ``whoami``.

    Server-internal only: callers (a gated download, the pyannote provider via the
    SDK re-export) get the raw token to authenticate an HF fetch. Returns ``None``
    when no source holds a whoami-valid token. Cached whoami keeps this cheap on the
    hot list-render path.
    """
    for _source_id, token in _ordered_sources():
        if not token:
            continue
        if (await _whoami(token)).valid:
            return token
    return None


# ── set / clear (source 1 only, SEL-audited) ──────────────────────────────────


def _sel_log(operation: str, outcome: str, error: str = "") -> None:
    """Audit a token mutation in the SEL (best-effort). NEVER logs the token value —
    only the operation and its outcome (mirrors the credential-handling audit rule)."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="dashboard",
            operation=operation,
            outcome=outcome,
            source="models",
            resources="hf_token",
            error=error,
        )
    except Exception:
        logger.debug("SEL audit for %s failed", operation, exc_info=True)


def set_hf_token(token: str) -> None:
    """Persist ``token`` as source 1 (credential store ``.env``, 0600) and SEL-audit.

    Writes ONLY the ``.env`` file (not the process environment — that is source 2,
    kept independent): the resolver reads every source fresh, so the running gateway
    picks up the new value on the next call without a mirror. Invalidates the whoami
    cache so the new token is re-validated immediately.
    """
    token = token.strip()
    if not token:
        raise ValueError("token must be non-empty")
    _write_env_key(_TOKEN_KEY, token)
    _whoami_cache.clear()
    _sel_log("hf_token.set", "ok")


def clear_hf_token() -> None:
    """Remove source 1 (the credential-store ``HF_TOKEN``) and SEL-audit.

    Only the ``.env`` line is removed; environment source-2 tokens the user exported
    themselves are left intact. If the process environment carries the SAME value
    (a startup mirror of this ``.env`` key), that copy is popped too so a clear
    actually clears — a user-exported, different ``HF_TOKEN`` is untouched.
    """
    removed = _read_credential_token()
    _remove_env_key(_TOKEN_KEY)
    if removed and os.environ.get(_TOKEN_KEY) == removed:
        os.environ.pop(_TOKEN_KEY, None)
    _whoami_cache.clear()
    _sel_log("hf_token.clear", "ok")


def _write_env_key(key: str, value: str) -> None:
    """Upsert ``key=value`` into ``<home>/.env`` (0600), preserving other lines.

    A local mirror of the credential-store write that does NOT touch
    ``os.environ`` — so source 1 (the file) stays cleanly distinct from source 2
    (the environment)."""
    ep = _env_file_path()
    ep.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    found = False
    if ep.exists():
        for line in ep.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                if stripped.split("=", 1)[0].strip() == key:
                    lines.append(f"{key}={value}")
                    found = True
                    continue
            lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    ep.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        ep.chmod(0o600)
    except OSError:
        logger.warning("Cannot enforce 0600 permissions on %s", ep)


def _remove_env_key(key: str) -> None:
    """Drop any ``key=…`` line from ``<home>/.env`` (no-op if absent), keeping 0600."""
    ep = _env_file_path()
    if not ep.exists():
        return
    kept: list[str] = []
    for line in ep.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            if stripped.split("=", 1)[0].strip() == key:
                continue
        kept.append(line)
    ep.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    try:
        ep.chmod(0o600)
    except OSError:
        logger.warning("Cannot enforce 0600 permissions on %s", ep)
