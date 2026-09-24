"""SDK: the supervised background-worker ABC + its cooperative control protocol.

Stable re-export of ``personalclaw.apps.background`` — an app imports these, not the core
module directly, so the core path can move without breaking installed apps. Gated by the
app's ``backgroundTasks`` permission, verified host-side before the worker is spawned.
"""

from personalclaw.apps.background import (  # noqa: F401
    BACKGROUND_TASKS_PERMISSION,
    DEFAULT_POLL_INTERVAL,
    WORKER_APP_ENV,
    WORKER_DATA_DIR_ENV,
    WORKER_DEFAULT_NAME,
    WORKER_ENTRY_POINT,
    WORKER_GRANT_ENV,
    WORKER_ID_ENV,
    BackgroundWorker,
    PauseReason,
    StopReason,
    WorkerContext,
    WorkerContractError,
    WorkerControl,
    WorkerState,
    run_worker,
)

__all__ = [
    "BACKGROUND_TASKS_PERMISSION",
    "DEFAULT_POLL_INTERVAL",
    "WORKER_APP_ENV",
    "WORKER_DATA_DIR_ENV",
    "WORKER_DEFAULT_NAME",
    "WORKER_ENTRY_POINT",
    "WORKER_GRANT_ENV",
    "WORKER_ID_ENV",
    "BackgroundWorker",
    "PauseReason",
    "StopReason",
    "WorkerContext",
    "WorkerContractError",
    "WorkerControl",
    "WorkerState",
    "run_worker",
]
