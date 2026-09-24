"""Desktop shell registry — the gateway's half of the capability bridge (DC-2 C2).

The Electron shell owns the only truthful view of the host's native capability
state (macOS TCC status, whether a tray is available, whether a hotkey chord
registered). The gateway cannot query any of it: it is a plain HTTP server that
may equally be serving a browser tab with no shell at all. So the shell PUSHES
its capability manifest here on boot, and every other consumer (the Settings
panel, an app with a ``desktop`` permission) reads this registry instead of
guessing.

Security posture (ARCC was unavailable for this change, so the reasoning is
written out rather than cited):

* **Fail closed.** An unregistered gateway reports ``connected: false`` and an
  EMPTY capability map. There is no permissive default anywhere in this module —
  a caller can never read a capability the shell did not assert.
* **The ``shell_token`` is a credential.** It is minted here with
  :func:`secrets.token_urlsafe`, kept in memory only (never written to disk,
  never returned by :meth:`DesktopRegistry.snapshot`, never placed in a log line
  or an error body), and compared with :func:`hmac.compare_digest` so a wrong
  token cannot be recovered by timing the comparison. It is handed to exactly one
  process — the Electron MAIN process, over loopback, in the response body of a
  request that already proved it can read ``$PERSONALCLAW_HOME/.local_secret``.
  The renderer never sees it: the preload bridge exposes ``probe``/``request``/
  ``on`` and no token accessor, so page JS has no path to the value even if a
  page is compromised.
* **Bootstrapping off ``.local_secret`` rather than a new secret file** is
  deliberate. Any process that can read the home directory can already mint a
  full API token via ``GET /api/token/local``; a second on-disk secret would add
  a file to back up and leak without moving that boundary. The per-session
  ``shell_token`` this module mints is narrower than the local secret (it
  authorizes ``/api/desktop`` writes and nothing else) and dies with the process.

State lives on ``DashboardState.desktop`` — one registry per gateway instance,
so tests get isolation for free and nothing survives a restart (a shell that
outlives a gateway restart re-registers; see ``main.js``).
"""

from __future__ import annotations

import hmac
import secrets
import threading
from datetime import datetime, timezone
from typing import Any

# The canonical capability vocabulary. This tuple is the contract: ``desktop/
# capabilities.js`` declares the same six names and ``tests/test_desktop_seam.py``
# asserts the two sides agree, so a capability added on one side cannot ship
# half-wired.
CAPABILITIES: tuple[str, ...] = (
    "audio_capture",
    "global_hotkey",
    "native_notifications",
    "tray",
    "screen_capture",
    "login_item",
    # DC-3 T3.3. Present in the vocabulary so "no, and here is why" is a state the
    # panel and app-permission checks can READ. The shell always reports it
    # ``unavailable`` with the Screen-Recording reason — capturing speaker output
    # would mean requesting the right to record the screen, so PersonalClaw captures
    # the microphone only (docs/guides/desktop.md says the same in prose).
    "system_audio",
)

# The permission states a capability may report. ``unavailable`` means the host
# or build cannot offer it at all (wrong platform, no shell); the other four
# mirror Electron's ``systemPreferences.getMediaAccessStatus`` vocabulary so the
# shell never has to translate an OS answer into a different word.
GRANT_STATES: tuple[str, ...] = (
    "granted",
    "denied",
    "restricted",
    "not-determined",
    "unavailable",
)


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _normalize_capability(raw: Any) -> dict[str, Any]:
    """Coerce one shell-reported capability entry into the wire shape.

    Fails closed on every axis: an unparseable entry becomes ``unavailable`` and
    not-requestable rather than inheriting a permissive default. ``reason`` is
    truncated because it reaches a UI surface.
    """
    if not isinstance(raw, dict):
        return {"available": False, "granted": "unavailable", "requestable": False, "reason": ""}
    granted = str(raw.get("granted", "unavailable"))
    if granted not in GRANT_STATES:
        granted = "unavailable"
    available = bool(raw.get("available", False)) and granted != "unavailable"
    return {
        "available": available,
        "granted": granted if available else "unavailable",
        # A capability is "requestable" only when the shell can raise the OS
        # prompt itself. macOS exposes no API to prompt for screen recording or
        # to read notification authorization, so those arrive requestable=False
        # and the UI must say so instead of offering a button that does nothing.
        "requestable": bool(raw.get("requestable", False)) and available,
        "reason": str(raw.get("reason", ""))[:200],
    }


def normalize_manifest(raw: Any) -> dict[str, dict[str, Any]]:
    """Normalize a shell capability manifest, dropping names outside ``CAPABILITIES``.

    An unknown capability name is silently ignored rather than stored: the
    registry is read by a consent-bearing UI and by app permission checks, and
    neither should be able to see a name the gateway does not know how to
    describe.
    """
    if not isinstance(raw, dict):
        return {}
    return {
        cap: _normalize_capability(raw[cap])
        for cap in CAPABILITIES
        if cap in raw  # absent → the shell is not claiming it at all
    }


class DesktopRegistry:
    """In-memory record of the connected desktop shell and its capabilities."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str = ""
        self._shell: dict[str, Any] = {}
        self._capabilities: dict[str, dict[str, Any]] = {}
        self._registered_at: str = ""
        self._last_seen: str = ""

    # -- shell side (loopback, credential-bearing) ------------------------

    def register(self, *, shell: dict[str, Any], capabilities: Any) -> str:
        """Register (or re-register) the shell and return a fresh ``shell_token``.

        Re-registering rotates the token, which is what makes a shell restart
        safe: the previous token stops working immediately, so a stale process
        cannot keep writing capability state for a shell that is gone.
        """
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._token = token
            self._shell = {
                "version": str(shell.get("version", ""))[:40],
                "platform": str(shell.get("platform", ""))[:20],
            }
            self._capabilities = normalize_manifest(capabilities)
            self._registered_at = _now()
            self._last_seen = self._registered_at
        return token

    def verify(self, token: str) -> bool:
        """Constant-time check of a presented ``shell_token``. Empty → False."""
        with self._lock:
            expected = self._token
        if not expected or not token:
            return False
        return hmac.compare_digest(expected, token)

    def update(self, *, token: str, capabilities: Any) -> bool:
        """Replace the capability manifest. False (no write) on a bad token."""
        if not self.verify(token):
            return False
        with self._lock:
            self._capabilities = normalize_manifest(capabilities)
            self._last_seen = _now()
        return True

    def unregister(self, token: str) -> bool:
        """Forget the shell (quit / window close). False on a bad token."""
        if not self.verify(token):
            return False
        with self._lock:
            self._token = ""
            self._shell = {}
            self._capabilities = {}
            self._registered_at = ""
            self._last_seen = ""
        return True

    # -- read side (dashboard, apps) --------------------------------------

    @property
    def connected(self) -> bool:
        with self._lock:
            return bool(self._token)

    def snapshot(self) -> dict[str, Any]:
        """The public read shape. Never contains the token.

        When no shell is registered this returns ``connected: False`` with an
        EMPTY ``capabilities`` map — deliberately not the known capability names
        with a placeholder state, because a browser tab must not read as "these
        exist, just not granted yet". The UI renders "not connected" from the
        absence.
        """
        with self._lock:
            return {
                "connected": bool(self._token),
                "shell": dict(self._shell) if self._token else None,
                "capabilities": {k: dict(v) for k, v in self._capabilities.items()},
                "registered_at": self._registered_at,
                "last_seen": self._last_seen,
            }

    def capability(self, cap: str) -> dict[str, Any] | None:
        """One capability's state, or ``None`` when absent/no shell."""
        with self._lock:
            if not self._token:
                return None
            entry = self._capabilities.get(cap)
            return dict(entry) if entry else None
