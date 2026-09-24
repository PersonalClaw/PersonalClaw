"""DISABLE_LIVE_WRITES — the process-wide destructive-write kill (AUTONOMY-GUARDRAILS §1.4).

``PERSONALCLAW_DISABLE_LIVE_WRITES`` is a process-wide env flag, parsed fail-safe
via ``guard_flag`` (only an explicit falsy value disables the guard). It is
**auto-set in conftest for the whole test suite** — PClaw was already bitten by
exactly this bug class: a destructive test with no ``_models_dir`` monkeypatch
deleted the user's real bound local model.

Honored by every LIVE, hard-to-reverse write core owns: external-write action
providers, the local-model ``delete_model``, and ``net.fetch`` non-GET methods to
non-loopback hosts. Each returns/raises a **typed refusal**, never a silent no-op,
so a test asserting a write FAILS loudly instead of passing vacuously. A localhost
write is exempt — it targets the dev gateway itself, not the outside world.
"""

from __future__ import annotations

import os

_ENV = "PERSONALCLAW_DISABLE_LIVE_WRITES"


def live_writes_disabled() -> bool:
    """True when live destructive writes must be refused.

    Default is ENABLED-writes (the guard is OFF) — the flag is opt-IN, so a normal
    gateway is unaffected. The fail-safe half of ``guard_flag`` only applies once
    the var is PRESENT: an explicit falsy value turns the guard off; any other
    present value turns it on. An ABSENT var means writes are allowed (this is not
    a guard-class default-on flag — it's an explicit test/ops safety toggle)."""
    raw = os.environ.get(_ENV)
    if raw is None:
        return False
    from personalclaw.guardrails.flags import guard_flag

    return guard_flag(raw)


class LiveWriteDisabled(Exception):
    """Raised when a live destructive write is refused because writes are disabled.

    A loud, typed refusal — the whole point of §1.4 is that a suppressed write
    FAILS visibly (so a vacuous test can't pass) rather than silently no-op'ing.
    """

    def __init__(self, what: str) -> None:
        self.what = what
        super().__init__(f"live write refused ({what}): PERSONALCLAW_DISABLE_LIVE_WRITES is set")
