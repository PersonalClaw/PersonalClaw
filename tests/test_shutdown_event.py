"""Tests for the process-wide ``shutdown_event`` lazy proxy.

``shutdown_event`` must not be a plain ``asyncio.Event()`` created at import
time: an Event bound to the import-time loop, awaited later from a different
loop (e.g. the one ``asyncio.run()`` creates for the gateway), raises
``RuntimeError: got Future attached to a different loop``. The lazy proxy binds
to the running loop on first use instead, and re-binds across fresh
``asyncio.run()`` loops while preserving a pending set() across them.
"""

import asyncio

import personalclaw


def test_shutdown_event_importable_without_running_loop() -> None:
    """Importing the module must not require a running event loop."""
    assert hasattr(personalclaw, "shutdown_event")
    assert callable(personalclaw.shutdown_event.set)
    assert callable(personalclaw.shutdown_event.clear)
    assert callable(personalclaw.shutdown_event.is_set)


def test_shutdown_event_survives_fresh_asyncio_run() -> None:
    """``await shutdown_event.wait()`` must work inside a fresh ``asyncio.run()``.

    This mirrors the exact pattern that crashed the gateway: the module is
    imported at top level, then ``asyncio.run()`` creates a new loop and the
    gateway coroutine awaits ``shutdown_event.wait()``.
    """
    personalclaw.shutdown_event.clear()

    async def main() -> None:
        async def setter() -> None:
            await asyncio.sleep(0.01)
            personalclaw.shutdown_event.set()

        asyncio.create_task(setter())
        await personalclaw.shutdown_event.wait()

    asyncio.run(main())
    assert personalclaw.shutdown_event.is_set()
    personalclaw.shutdown_event.clear()


def test_shutdown_event_survives_multiple_asyncio_runs() -> None:
    """The proxy must rebind cleanly across successive loops."""
    for _ in range(3):

        async def main() -> None:
            async def setter() -> None:
                await asyncio.sleep(0.01)
                personalclaw.shutdown_event.set()

            asyncio.create_task(setter())
            await personalclaw.shutdown_event.wait()

        personalclaw.shutdown_event.clear()
        asyncio.run(main())
        assert personalclaw.shutdown_event.is_set()

    personalclaw.shutdown_event.clear()


def test_shutdown_event_wait_for_timeout() -> None:
    """``asyncio.wait_for(shutdown_event.wait(), timeout=...)`` must work."""
    personalclaw.shutdown_event.clear()

    async def main() -> str:
        try:
            await asyncio.wait_for(personalclaw.shutdown_event.wait(), timeout=0.05)
        except asyncio.TimeoutError:
            return "timed_out"
        return "set"

    result = asyncio.run(main())
    assert result == "timed_out"


def test_shutdown_event_does_not_bind_to_default_loop_via_get_event_loop() -> None:
    """The proxy must NOT bind to the default loop on first access."""
    # Step 1: make sure there's a default loop on the main thread
    try:
        default_loop = asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        default_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(default_loop)
    assert default_loop is not None

    # Step 2: reset the proxy's cached Event so it has to rebuild
    personalclaw.shutdown_event.clear()
    personalclaw.shutdown_event._event = None  # type: ignore[attr-defined]
    personalclaw.shutdown_event._loop = None  # type: ignore[attr-defined]

    # Step 3 + 4: fresh loop via asyncio.run, must not cross-loop
    async def main() -> None:
        async def setter() -> None:
            await asyncio.sleep(0.01)
            personalclaw.shutdown_event.set()

        asyncio.create_task(setter())
        await personalclaw.shutdown_event.wait()

    asyncio.run(main())
    assert personalclaw.shutdown_event.is_set()
    personalclaw.shutdown_event.clear()


def test_shutdown_event_pending_set_preserved_across_loops() -> None:
    """A ``set()`` call without a running loop must survive until one starts."""
    # Reset
    personalclaw.shutdown_event.clear()
    personalclaw.shutdown_event._event = None  # type: ignore[attr-defined]
    personalclaw.shutdown_event._loop = None  # type: ignore[attr-defined]

    # Sync set() before any loop runs
    personalclaw.shutdown_event.set()
    assert personalclaw.shutdown_event.is_set()

    async def main() -> bool:
        return personalclaw.shutdown_event.is_set()

    assert asyncio.run(main()) is True
    personalclaw.shutdown_event.clear()


def test_shutdown_event_get_raises_without_loop() -> None:
    """``_get()`` must raise RuntimeError when no loop is running."""
    personalclaw.shutdown_event._event = None  # type: ignore[attr-defined]
    personalclaw.shutdown_event._loop = None  # type: ignore[attr-defined]
    try:
        personalclaw.shutdown_event._get()  # type: ignore[attr-defined]
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "without a running event loop" in str(e)
    finally:
        personalclaw.shutdown_event.clear()
