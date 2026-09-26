"""A model-provider instance's connection — measured, cached, never inferred from settings.

Settings → Providers used to badge every instance from its credential's PRESENCE, so a keyless
OpenAI instance read "✓ Configured" beside its own test saying "No API key or endpoint
configured", and Settings → Models offered ten Claude models from an Anthropic instance whose
key the vendor had rejected. Presence is not the question either surface answers — whether the
instance CONNECTS is, and only a connection test measures that.

So an instance's state is the outcome of its catalog's ``test_connection()``:

* measured in the background the first time it is read, and again once the answer is older
  than :data:`CHECK_TTL_SECS` — never on the request that asked, because a black-holed
  endpoint takes the whole connect timeout to fail;
* measured NOW by the instance's Test button, the one inline caller (:func:`measure`);
* keyed by a fingerprint of the instance's settings, so an edited endpoint or key is never
  judged by the answer its previous settings earned;
* consulted by model discovery: an instance whose last check failed is not asked for its
  models again until a check passes. That is what stops a rejected key being re-sent to its
  vendor on every page load — one validation log held 51 identical 401 warnings.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: How long a measured answer stands before a read re-measures it in the background. The
#: Test button re-measures at once, so this is the backstop for a server that came back.
CHECK_TTL_SECS = 15 * 60.0
#: The most one connection test may take before it is reported as not answering.
CHECK_TIMEOUT_SECS = 30.0

CHECKING = "checking"
CONNECTED = "connected"
FAILED = "failed"
#: The instance's type has no connection test (its app registers no catalog).
UNTESTABLE = "untestable"


@dataclass(frozen=True)
class Connection:
    """One measured connection state. ``checked_at`` is epoch seconds, ``None`` until measured."""

    state: str
    detail: str = ""
    rejected_credential: bool = False
    checked_at: float | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "detail": self.detail,
            "rejected_credential": self.rejected_credential,
            "checked_at": self.checked_at,
        }


_CHECKING = Connection(CHECKING)


def settings_fingerprint(provider_type: str, options: dict[str, Any] | None) -> str:
    """A digest of what decides an instance's connection: its type and its settings.

    Core's bookkeeping keys (``_original_type`` and the like) are left out, so a config dict
    and the registry entry built from it fingerprint the same. A digest, never the values: the
    settings carry the instance's key.
    """
    settings = {k: v for k, v in (options or {}).items() if not str(k).startswith("_")}
    blob = json.dumps({"type": provider_type, "options": settings}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def measure(catalog: Any) -> Connection:
    """Run one connection test and state its outcome in words a user can act on.

    A catalog that lists through core's fail-soft discovery gets ``[]`` for a rejected key,
    and its test then says "No models returned". The failure it swallowed is captured here,
    and its own sentence — the endpoint that answered, the status, what to do — is what the
    result carries.
    """
    from personalclaw.llm.catalog import capture_discovery_failures
    from personalclaw.providers.failure_copy import relayed_failure_copy

    if catalog is None:
        return Connection(
            UNTESTABLE, "This provider type has no connection test.", checked_at=time.time()
        )
    with capture_discovery_failures() as swallowed:
        try:
            result = await asyncio.wait_for(catalog.test_connection(), timeout=CHECK_TIMEOUT_SECS)
        except TimeoutError:
            return Connection(
                FAILED,
                f"The connection test got no answer within {CHECK_TIMEOUT_SECS:.0f} s.",
                checked_at=time.time(),
            )
        except Exception as exc:  # noqa: BLE001 — a connection test must never raise into a page
            logger.debug("connection test raised", exc_info=True)
            return Connection(FAILED, relayed_failure_copy(exc), checked_at=time.time())
    refused = next((exc for exc in swallowed if exc.rejected_credential), None)
    # "ok" over a swallowed failure is the fail-soft artifact, not a connection: the default
    # test counts whatever the listing returned, and a listing that swallowed a 401 returned
    # zero. A refused key, or nothing listed because the listing failed, is not "connected".
    if result.ok and refused is None and (not swallowed or result.model_count):
        detail = result.detail or (
            f"Connected — {result.model_count} model(s) available"
            if result.model_count is not None
            else "Connected"
        )
        return Connection(CONNECTED, detail, checked_at=time.time())
    detail = "" if result.ok else result.detail
    rejected = bool(getattr(result, "rejected_credential", False))
    if swallowed:
        cause = refused or swallowed[-1]
        detail = str(cause)
        rejected = rejected or cause.rejected_credential
    return Connection(
        FAILED,
        detail or "The connection test failed without saying why.",
        rejected,
        checked_at=time.time(),
    )


class ConnectionBoard:
    """Every instance's last measured connection. Reads never block.

    Answers are kept per (instance, settings fingerprint): two readers that fingerprint an
    instance differently — a registry entry and a hand-edited ``config.json`` row the gateway
    has not re-read — each keep their own answer instead of evicting each other's and
    re-measuring on every alternate read.
    """

    def __init__(self, *, ttl_secs: float = CHECK_TTL_SECS) -> None:
        self._ttl = ttl_secs
        self._answers: dict[str, dict[str, tuple[Connection, float]]] = {}  # name → fp → answer
        self._checks: dict[tuple[str, str], asyncio.Task[None]] = {}

    def read(self, name: str, fingerprint: str, catalog: Callable[[], Any]) -> Connection:
        """The answer for ``name`` under these settings, measuring in the background if needed.

        ``catalog`` builds the instance's catalog; it is only called when a check runs.
        """
        hit = self._answers.get(name, {}).get(fingerprint)
        if hit is not None:
            if time.monotonic() - hit[1] >= self._ttl:
                self._schedule(name, fingerprint, catalog)
            return hit[0]
        self._schedule(name, fingerprint, catalog)
        return _CHECKING

    def peek(self, name: str, fingerprint: str) -> Connection | None:
        """The last measured answer, or ``None`` — and never a measurement.

        For status surfaces (the degraded badge, onboarding) that must answer from what is
        already known: a black-holed endpoint takes the whole connect timeout to fail, and a
        status read is not allowed to be the thing that waits for it.
        """
        hit = self._answers.get(name, {}).get(fingerprint)
        return hit[0] if hit is not None else None

    def record(self, name: str, fingerprint: str, answer: Connection) -> None:
        self._answers.setdefault(name, {})[fingerprint] = (answer, time.monotonic())

    def forget(self, name: str) -> None:
        """Drop what was measured for an instance that was edited or removed."""
        self._answers.pop(name, None)
        for key in [k for k in self._checks if k[0] == name]:
            running = self._checks.pop(key)
            if not running.done():
                running.cancel()

    def _schedule(self, name: str, fingerprint: str, catalog: Callable[[], Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        key = (name, fingerprint)
        running = self._checks.get(key)
        # A task stranded on a loop that has since closed never reports done — only a live
        # one on THIS loop means a check is already under way.
        if running is not None and not running.done() and running.get_loop() is loop:
            return
        self._checks[key] = loop.create_task(self._check(name, fingerprint, catalog))

    async def _check(self, name: str, fingerprint: str, catalog: Callable[[], Any]) -> None:
        from personalclaw.providers.failure_copy import relayed_failure_copy

        try:
            built = catalog()
        except Exception as exc:  # noqa: BLE001 — a broken catalog is a failed check
            logger.debug("catalog for %s failed to build", name, exc_info=True)
            answer = Connection(
                FAILED,
                f"This instance's settings could not be loaded: {relayed_failure_copy(exc)}",
                checked_at=time.time(),
            )
        else:
            answer = await measure(built)
        self.record(name, fingerprint, answer)


_board: ConnectionBoard | None = None


def get_connection_board() -> ConnectionBoard:
    global _board
    if _board is None:
        _board = ConnectionBoard()
    return _board


def reset_connection_board() -> None:
    """Forget every measured connection (tests)."""
    global _board
    _board = None


def entry_fingerprint(entry: Any) -> str:
    """:func:`settings_fingerprint` of an LLM-registry entry (its type is already canonical)."""
    from personalclaw.llm.registry import canonical_provider_type

    return settings_fingerprint(canonical_provider_type(entry.type), entry.options)


def chat_provider_status() -> dict[str, Any] | None:
    """The measured connection of the instance chat is bound to — READ from the board.

    ``None`` when chat is bound to no configured instance, or when that instance has not been
    measured yet. This never measures: the degraded badge and ``/api/onboarding`` ask it on
    every poll, and a status read must not be what waits on a black-holed endpoint. Readiness
    ("a model resolves") makes no network calls by design, so without this a configured
    provider that is DOWN read as fully set up.
    """
    from personalclaw.llm.registry import get_default_registry
    from personalclaw.providers.use_cases import active_model_refs

    refs = [str(r) for r in active_model_refs("chat")]
    if not refs or ":" not in refs[0]:
        return None
    provider = refs[0].split(":", 1)[0]
    try:
        entry = get_default_registry().get_entry(provider)
    except Exception:  # noqa: BLE001 — not a configured instance: nothing was measured
        return None
    answer = get_connection_board().peek(provider, entry_fingerprint(entry))
    if answer is None:
        return None
    return {"provider": provider, **answer.to_wire()}


__all__ = [
    "CHECKING",
    "CHECK_TIMEOUT_SECS",
    "CHECK_TTL_SECS",
    "CONNECTED",
    "Connection",
    "ConnectionBoard",
    "FAILED",
    "UNTESTABLE",
    "chat_provider_status",
    "entry_fingerprint",
    "get_connection_board",
    "measure",
    "reset_connection_board",
    "settings_fingerprint",
]
