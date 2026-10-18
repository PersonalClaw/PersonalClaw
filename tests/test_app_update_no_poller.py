"""'Zero polling processes added' — the property worth re-checking
whenever this area changes: *"an update badge is exactly the
feature that acquires a background poller by accident."*

The behavioral tests in ``test_app_catalog.py`` pin WHAT the update surfacing does (one
notification, dedup by name+latest_version, refire on a genuinely newer version). These
pin the structural invariant that keeps it cheap: the latest-available version is
computed synchronously ON THE READ PATH, and NOTHING schedules, loops, or polls it. A
regression here does not fail a behavioral assertion — the notification still fires — it
just quietly adds a background process. So it needs its own source-level guard.
"""

from __future__ import annotations

from app_update_no_poller_shared import SRC


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
    # "acquires a background poller by accident" failure the change names.
    for rel in ("apps/app_crons.py", "apps/worker_runtime.py", "apps/backend_runtime.py"):
        p = SRC / rel
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for sym in ("surface_app_updates", "updates_available"):
            assert sym not in text, f"{rel} must not reference {sym} — no update poller"
