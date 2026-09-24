"""HF token cascade — three sources, whoami-validated (LOCAL-MODEL-MANAGER-V2 §5).

One shared resolver for the HuggingFace token, replacing each provider's private
two-source lookup. Sources, in priority order:

  1. ``credential_store``  the PClaw secret store (:func:`config.credentials.get_credential`
                           — keychain ∪ ``~/.personalclaw/.env`` at 0600)
  2. ``env``               ``HF_TOKEN``, legacy ``HUGGING_FACE_HUB_TOKEN``
  3. ``hf_cli_file``       ``~/.cache/huggingface/token`` (``huggingface-cli login``)

**The first source that has a token AND survives a live ``whoami`` wins** — an invalid
higher-priority token is skipped, never blocking a valid lower-priority one, and each source
carries its own status. The ``whoami`` call goes through the ``net.fetch`` CONNECTOR egress
chokepoint (never hand-rolled aiohttp), and its result is cached for
``local_models.whoami_ttl_s`` so a list render or a gated pre-warn doesn't hammer HF.

🔴 **A HuggingFace token is a static, broad-privilege credential** — it authenticates every
HF API call on the user's behalf, so a leaked one is a real exposure (ARCC treats the Okta
``SSWS`` token, the same shape, as a High finding). It is therefore handled exactly as the
credential store handles any secret:

* the value is WRITTEN only through the credential store (never ``config.json``, never a log,
  never an SSE frame, never a status payload);
* every status/response MASKS it (:func:`mask_token` → ``hf_…abcd``) — the raw value never
  leaves the server;
* the whoami audit (in ``net.fetch``) logs the host, never the ``Authorization`` header;
* set/clear are audited to the SEL as events, again by name, never by value.

The whole module imports lazily (``net``/``config``/``sel`` inside functions) so re-exporting
it through ``sdk.credentials`` stays a light import for an app.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: The credential-store key the managed token is written under. Named ``HF_TOKEN`` so the
#: credential store's ``os.environ`` mirror lights up exactly the variable the ``huggingface_hub``
#: library reads — a set here immediately unblocks a gated download without a restart.
CREDENTIAL_NAME = "HF_TOKEN"

#: Environment variables read as source 2 (the current name first, then the legacy one HF
#: still honors). Read directly from ``os.environ`` — NOT through the credential store — so
#: an ambient shell token is a distinct source from the managed one.
_ENV_NAMES: tuple[str, ...] = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN")

#: HuggingFace's identity endpoint. A 200 means the token authenticates and carries the
#: account ``name``; a 401/403 means it does not. Reached only through ``net.fetch``.
_WHOAMI_URL = "https://huggingface.co/api/whoami-v2"

# Source identifiers (stable — the FE and tests branch on them).
SOURCE_CREDENTIAL_STORE = "credential_store"
SOURCE_ENV = "env"
SOURCE_HF_CLI = "hf_cli_file"

#: whoami verdict → (verdict, username, monotonic-expiry). Only ``valid``/``invalid`` are
#: cached; ``unknown`` (a network error / blocked egress) is NEVER cached, so a transient
#: failure re-checks on the next call instead of pinning a good token as bad for a whole TTL.
_WHOAMI_CACHE: dict[str, tuple[str, str, float]] = {}

_DEFAULT_WHOAMI_TTL_S = 600.0


@dataclass(frozen=True)
class HfSourceStatus:
    """One cascade source's status for the settings surface — the value is NEVER carried.

    ``masked`` is the only representation of the token that leaves the server
    (:func:`mask_token`). ``active`` marks the single winning source (the first whoami-valid
    one), so the UI can badge it without re-deriving the precedence.
    """

    source: str
    present: bool
    valid: bool
    username: str
    masked: str
    active: bool


@dataclass(frozen=True)
class HfTokenResolution:
    """The cascade's chosen token. ``token`` is ``None`` when nothing usable is present.

    ``valid`` is True only when ``whoami`` CONFIRMED the token; a present-but-unverified
    token (egress unreachable) resolves with ``valid=False`` but a non-None ``token`` so a
    caller can still try it rather than being blocked by a network blip.
    """

    token: str | None
    source: str
    username: str
    valid: bool


def mask_token(token: str) -> str:
    """A safe preview of a token — ``hf_…abcd`` — that reveals neither the secret middle.

    Anything shorter than 12 characters collapses to ``…``: below that, a first-3 + last-4
    preview would reveal most of the value, and no real HuggingFace token (or any credential
    worth masking) is that short — a sub-12 input is a fat-finger, not a secret to preview.
    At/above 12, the first three characters keep the ``hf_`` family prefix legible and the last
    four let a user tell two configured tokens apart, while the secret middle never leaves.
    """
    tok = (token or "").strip()
    if len(tok) < 12:
        return "…" if tok else ""
    return f"{tok[:3]}…{tok[-4:]}"


def _hf_cli_token_path() -> Path:
    """Path to the ``huggingface-cli login`` token file (source 3).

    Honors the HF library's own overrides (``HF_TOKEN_PATH`` then ``HF_HOME``) so we read the
    same file the CLI wrote, and defaults to the documented ``~/.cache/huggingface/token``.
    """
    explicit = os.environ.get("HF_TOKEN_PATH")
    if explicit:
        return Path(explicit).expanduser()
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home).expanduser() / "token"
    return Path.home() / ".cache" / "huggingface" / "token"


def _read_credential_store() -> str:
    """Source 1: the managed credential store (keychain ∪ ``.env``), never ``os.environ``."""
    try:
        from personalclaw.config.credentials import get_credential

        return (get_credential(CREDENTIAL_NAME) or "").strip()
    except Exception:  # noqa: BLE001 — an unreadable store is "no token here", never a crash
        logger.debug("hf_token: credential store read failed", exc_info=True)
        return ""


def _read_env() -> str:
    """Source 2: the ambient process environment (current name, then the legacy one)."""
    for name in _ENV_NAMES:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def _read_hf_cli_file() -> str:
    """Source 3: the ``huggingface-cli`` token file, or ``""`` if absent/unreadable."""
    try:
        path = _hf_cli_token_path()
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.debug("hf_token: HF CLI token file unreadable", exc_info=True)
        return ""


#: Ordered (source, reader-name) — the cascade's precedence lives here, in one place. The
#: reader is named, not bound, and resolved through :func:`_read_source` at call time, so a
#: test (or a future override) that replaces one reader is actually consulted — a tuple of
#: bound functions would freeze the originals at import and silently ignore the replacement.
_SOURCE_ORDER: tuple[tuple[str, str], ...] = (
    (SOURCE_CREDENTIAL_STORE, "_read_credential_store"),
    (SOURCE_ENV, "_read_env"),
    (SOURCE_HF_CLI, "_read_hf_cli_file"),
)


def _read_source(reader_name: str) -> str:
    """Call the named reader as it is bound on the module RIGHT NOW (see :data:`_SOURCE_ORDER`)."""
    return globals()[reader_name]()


def _present_sources() -> list[tuple[str, str]]:
    """``[(source, token)]`` for every source that currently HAS a token, in priority order."""
    out: list[tuple[str, str]] = []
    for source, reader_name in _SOURCE_ORDER:
        token = _read_source(reader_name)
        if token:
            out.append((source, token))
    return out


def _whoami_ttl_s() -> float:
    """The whoami cache TTL from ``local_models.whoami_ttl_s`` (best-effort → default)."""
    try:
        from personalclaw.config.loader import AppConfig

        return float(AppConfig.load().local_models.whoami_ttl_s)
    except Exception:  # noqa: BLE001 — config unreadable → the shipped default, never a crash
        return _DEFAULT_WHOAMI_TTL_S


def _cache_key(token: str) -> str:
    """A stable cache key that is NOT the token itself (sha256), so the raw value is not held
    as a dict key that could surface in a repr/traceback."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cached_state(token: str) -> str | None:
    """The cached whoami verdict for ``token`` if still fresh, else ``None``."""
    entry = _WHOAMI_CACHE.get(_cache_key(token))
    if entry is None:
        return None
    state, _username, expiry = entry
    if expiry <= time.monotonic():
        return None
    return state


def invalidate_whoami_cache() -> None:
    """Drop every cached whoami verdict. Called on set/clear so a changed token re-validates."""
    _WHOAMI_CACHE.clear()


async def _whoami_live(token: str) -> tuple[str, str]:
    """One real ``whoami`` through the CONNECTOR egress chokepoint. ``(verdict, username)``.

    ``valid`` (200 + ``name``), ``invalid`` (401/403), or ``unknown`` (blocked egress, a
    network error, or any other status) — ``unknown`` is deliberately distinct so it is not
    cached and does not brand a good token as bad.
    """
    from personalclaw.net import CONNECTOR, EgressBlocked, egress_policy_for, fetch

    try:
        resp = await fetch(
            _WHOAMI_URL,
            policy=egress_policy_for(CONNECTOR),
            headers={"Authorization": f"Bearer {token}"},
        )
    except EgressBlocked:
        logger.debug("hf_token: whoami egress blocked")
        return ("unknown", "")
    except Exception:  # noqa: BLE001 — a network failure is "unknown", never a raise into a render
        logger.debug("hf_token: whoami request failed", exc_info=True)
        return ("unknown", "")

    if resp.status == 200:
        username = ""
        try:
            import json

            data = json.loads(resp.body.decode("utf-8", "replace"))
            if isinstance(data, dict):
                username = str(data.get("name") or data.get("fullname") or "")
        except Exception:  # noqa: BLE001 — a 200 with an unparseable body is still authenticated
            logger.debug("hf_token: whoami body unparseable", exc_info=True)
        return ("valid", username)
    if resp.status in (401, 403):
        return ("invalid", "")
    return ("unknown", "")


async def _whoami(token: str) -> tuple[str, str]:
    """Cached ``whoami``. ``(verdict, username)`` where verdict ∈ valid/invalid/unknown."""
    if not token:
        return ("invalid", "")
    cached = _WHOAMI_CACHE.get(_cache_key(token))
    if cached is not None and cached[2] > time.monotonic():
        return (cached[0], cached[1])
    state, username = await _whoami_live(token)
    if state in ("valid", "invalid"):
        _WHOAMI_CACHE[_cache_key(token)] = (state, username, time.monotonic() + _whoami_ttl_s())
    return (state, username)


async def resolve_valid_token() -> HfTokenResolution:
    """The authoritative cascade: the first whoami-VALID source wins.

    When no source is confirmed valid but one is present-yet-unverifiable (egress down), that
    one is returned with ``valid=False`` so a caller can still try it. When every present
    source is confirmed invalid — or nothing is present — ``token`` is ``None``.
    """
    present = _present_sources()
    first_unknown: tuple[str, str] | None = None
    for source, token in present:
        state, username = await _whoami(token)
        if state == "valid":
            return HfTokenResolution(token=token, source=source, username=username, valid=True)
        if state == "unknown" and first_unknown is None:
            first_unknown = (source, token)
    if first_unknown is not None:
        return HfTokenResolution(
            token=first_unknown[1], source=first_unknown[0], username="", valid=False
        )
    return HfTokenResolution(token=None, source="", username="", valid=False)


def resolve_token() -> str:
    """The SYNC, network-free token a provider should USE (the provider ``_hf_token()`` delegate).

    Returns the highest-priority present token that is not CACHE-confirmed invalid, preferring
    a cache-confirmed valid one — so it benefits from a whoami the status endpoint or the gated
    pre-warn already ran, without ever blocking on the network itself. ``""`` when no source has
    a token. This is what a provider calls to get a token to hand to ``huggingface_hub``; the
    async :func:`resolve_valid_token` is the authoritative validated view.
    """
    present = _present_sources()
    if not present:
        return ""
    best_unknown = ""
    for _source, token in present:
        state = _cached_state(token)
        if state == "valid":
            return token
        if state != "invalid" and not best_unknown:
            best_unknown = token
    return best_unknown or present[0][1]


async def token_status() -> list[HfSourceStatus]:
    """Per-source ``{present, valid, username, masked, active}`` for the settings surface.

    Every source is reported (present or not) so the UI can show the whole cascade. The value
    itself is never included — only :func:`mask_token`. ``active`` marks the single winning
    source (first whoami-valid).
    """
    out: list[HfSourceStatus] = []
    active_assigned = False
    for source, reader_name in _SOURCE_ORDER:
        token = _read_source(reader_name)
        if not token:
            out.append(
                HfSourceStatus(
                    source=source, present=False, valid=False, username="", masked="", active=False
                )
            )
            continue
        state, username = await _whoami(token)
        valid = state == "valid"
        active = valid and not active_assigned
        if active:
            active_assigned = True
        out.append(
            HfSourceStatus(
                source=source,
                present=True,
                valid=valid,
                username=username,
                masked=mask_token(token),
                active=active,
            )
        )
    return out


async def gated_prewarn_ok() -> bool:
    """Whether a gated download can proceed WITHOUT nagging for a token (server-side pre-warn).

    True when a whoami-valid token exists, or one is present but could not be verified right
    now (don't nag over a network blip — the download itself surfaces the real gated error).
    False only when NO source has a token, or every present token is confirmed invalid — which
    is exactly the case the pre-warn exists to catch before the user clicks Download.
    """
    return (await resolve_valid_token()).token is not None


def _audit(action: str, caller: str) -> None:
    """SEL-audit a token set/clear as an event — by NAME, never by value (best-effort)."""
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller=caller,
            operation=f"hf_token.{action}",
            outcome="success",
            source="dashboard",
            resources=CREDENTIAL_NAME,
        )
    except Exception:  # noqa: BLE001 — an audit failure must not fail the write it records
        logger.debug("hf_token: SEL audit for %s failed", action, exc_info=True)


def set_token(value: str, *, caller: str = "dashboard:hf-token") -> None:
    """Write the token to SOURCE 1 (the credential store) and audit it. Raises on an empty value.

    Never writes ``config.json`` — a secret lives in the credential store. The credential store
    mirrors the value into ``os.environ`` so a running gateway and the HF library see it at once.
    """
    token = (value or "").strip()
    if not token:
        raise ValueError("HF token is empty")
    from personalclaw.config.credentials import save_credential

    save_credential(CREDENTIAL_NAME, token)
    invalidate_whoami_cache()
    _audit("set", caller)


def clear_token(*, caller: str = "dashboard:hf-token") -> bool:
    """Remove the managed token from SOURCE 1 and audit it. True iff a token was there.

    Delegates to the credential store's delete (both backends + the ``os.environ`` mirror), so
    the running gateway stops serving a token it was told to forget.
    """
    from personalclaw.config.credentials import delete_credential

    existed = delete_credential(CREDENTIAL_NAME)
    invalidate_whoami_cache()
    _audit("clear", caller)
    return existed
