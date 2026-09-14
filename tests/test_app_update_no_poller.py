"""APE-7 'zero polling processes added' — the clause the atom's own audit flags as
the one worth re-checking whenever this area changes: *"an update badge is exactly the
feature that acquires a background poller by accident."*

The behavioral tests in ``test_app_catalog.py`` pin WHAT the update surfacing does (one
notification, dedup by name+latest_version, refire on a genuinely newer version). These
pin the structural invariant that keeps it cheap: the latest-available version is
computed synchronously ON THE READ PATH, and NOTHING schedules, loops, or polls it. A
regression here does not fail a behavioral assertion — the notification still fires — it
just quietly adds a background process. So it needs its own source-level guard.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


def _external_callers(symbol: str) -> dict[str, str]:
    """Files under src/personalclaw that REFERENCE ``symbol`` without also DEFINING it.

    Matches the bare identifier rather than ``symbol(`` on purpose: the one production
    reacher passes it by name — ``asyncio.to_thread(surface_app_updates, state)`` — so a
    call-only regex would miss it. A file that defines the symbol is its home and is
    excluded; every remaining hit is an external reacher, which is precisely where an
    accidental poller would appear.
    """
    ref = re.compile(rf"\b{symbol}\b")
    define = re.compile(rf"\bdef\s+{symbol}\b")
    out: dict[str, str] = {}
    for p in SRC.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if ref.search(text) and not define.search(text):
            out[p.relative_to(SRC).as_posix()] = text
    return out


def test_surface_app_updates_has_exactly_one_production_caller_the_read_path() -> None:
    callers = set(_external_callers("surface_app_updates"))
    assert callers == {"dashboard/handlers/apps.py"}, (
        "APE-7 'zero polling processes added': surface_app_updates must be reached only "
        "from the /api/apps read handler. A second caller is how a poller creeps in. "
        f"Found: {sorted(callers)}"
    )


def test_updates_available_stays_internal_to_catalog() -> None:
    # updates_available() is the pure computation. Only apps/catalog.py (which defines it,
    # and so is excluded above) may call it; any EXTERNAL caller could schedule it on a loop.
    callers = set(_external_callers("updates_available"))
    assert callers == set(), (
        "APE-7: updates_available must stay internal to apps/catalog.py; an external caller "
        f"is how a background poller creeps in. Found: {sorted(callers)}"
    )


def test_the_update_surfacing_call_sits_in_the_read_handler_not_a_loop() -> None:
    text = (SRC / "dashboard" / "handlers" / "apps.py").read_text(encoding="utf-8")
    start = text.index("async def api_apps_list")
    nxt = text.find("\nasync def ", start + 1)
    body = text[start : nxt if nxt != -1 else len(text)]
    assert (
        "surface_app_updates" in body
    ), "the update surfacing call must live in the api_apps_list GET read handler"
    # The GET handler must not spin a poller around it. `asyncio.to_thread(...)` (offloading
    # one synchronous call) is fine; a loop / sleep / delayed-callback / scheduler is not.
    for prim in ("while True", "asyncio.sleep", "call_later", "call_at", "ensure_future"):
        assert (
            prim not in body
        ), f"api_apps_list must compute updates on the read path, not via a poller ({prim!r})"


def test_no_scheduler_or_cron_surfaces_updates() -> None:
    # The app-cron scheduler and the background/worker runtimes are the modules that DO run
    # things on a timer. None of them may reach the update surfacing — that is the exact
    # "acquires a background poller by accident" failure the atom names.
    for rel in ("apps/app_crons.py", "apps/worker_runtime.py", "apps/backend_runtime.py"):
        p = SRC / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for sym in ("surface_app_updates", "updates_available"):
            assert sym not in text, f"{rel} must not reference {sym} — no update poller"
