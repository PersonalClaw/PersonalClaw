# Python

- Target Python 3.12. Add `from __future__ import annotations` to new modules.
- Async: prefer `asyncio.TaskGroup` over a bare `gather` for anything that can fail, so one failed
  task cancels its siblings instead of leaving them running.
- HTTP: httpx with explicit timeouts, e.g. `httpx.Timeout(10.0, connect=5.0)`. No requests in new code.
- Logging: structlog in Cartwheel services, stdlib `logging` in feedsmith. Never log a full carrier
  payload; log the tracking number hash and the event type.
- Tests: pytest with pytest-asyncio in auto mode, respx for HTTP mocks, `freezegun` only when a
  test is about time.
- Money and ETAs are never floats in storage: `numeric` in Postgres, `Decimal` in Python.
