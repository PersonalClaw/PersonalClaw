"""Cron-script sources shipped for installation into ``~/.personalclaw/crons/``.

A package rather than a bare data directory so the ``.py`` files ship with the wheel through
normal package discovery, and so `importlib.resources` can read them without `__file__`
arithmetic. The modules here are *sources to install*, not code the gateway imports — the
gateway's copy is the one under the crons dir, because that is the only location a script job is
permitted to load from.
"""

from __future__ import annotations
