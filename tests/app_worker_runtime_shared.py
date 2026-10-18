"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_app_worker_runtime.py``

Its checks:
``tests/rails/test_app_worker_runtime_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any, Iterator

import pytest

from personalclaw.apps import manager, worker_runtime


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Apps, secrets, data dirs and guardrail state all live under a tmp home."""
    from personalclaw.config import loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "config_dir", lambda: tmp_path)
    # The suite-wide `_no_app_child_processes` fixture sets `PERSONALCLAW_SKIP_APP_WORKERS` so no
    # OTHER test drives the real home's workers from a boot-started daemon thread. This suite is
    # the one that drives the sweep on purpose, against the tmp home above, so it opts back in.
    monkeypatch.delenv(worker_runtime._SKIP_ENV, raising=False)
    return tmp_path


#: The declaration registry the stub `apps.background` answers from — `app name -> specs`.
_DECLARED: dict[str, list[Any]] = {}


@pytest.fixture(autouse=True)
def _stub_background(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    """Inject per-test worker declarations into the REAL supervisor.

    Integration re-homed `WorkerSpec`/`declared_workers` from `apps/background.py` into
    `worker_runtime` itself: they are PARENT-side bookkeeping an app author never reads, and
    routing them through the app-facing SDK facade would have exported names with no
    app-side consumer. `apps/background.py` is no longer stubbed at all — the real module is
    imported, so `BACKGROUND_TASKS_PERMISSION`, `WORKER_ENTRY_POINT`, `WORKER_DEFAULT_NAME`
    and `WORKER_ID_ENV` are the shipped values rather than a fixture's guess at them.

    What still needs injecting is WHICH workers a manifest declares, because the real
    `declared_workers` derives exactly one from the permission and these tests need several
    (a second worker, a missing entry point, an escaping path).
    """
    from personalclaw.apps import worker_runtime as _wr

    monkeypatch.setattr(
        _wr, "declared_workers", lambda manifest: list(_DECLARED.get(manifest.name, []))
    )
    _DECLARED.clear()
    try:
        yield _wr
    finally:
        _DECLARED.clear()
