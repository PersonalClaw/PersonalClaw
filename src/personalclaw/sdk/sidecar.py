"""SDK: the sidecar runner surface for ``execution: "sidecar"`` provider apps.

Stable re-export of the LOCAL-MODEL-MANAGER-V2 §3 sidecar machinery — an app whose
manifest declares ``provider.execution: "sidecar"`` drives its torch-heavy work through
:class:`SidecarRunner` (one child process, its own venv, newline-JSON protocol) instead
of importing the engine into the gateway process. The app ships a **worker module**
(``load(**kwargs)`` / ``call(method, payload)`` / optional ``unload()``) and passes its
absolute path as ``worker=``; :func:`register_runner` hands the child to core's
watchdog and memory-pressure surfaces.

Crash honesty is the point of the boundary: a child killed mid-call raises
:class:`SidecarCrashed`, whose ``typed_reason`` (``sidecar_crashed:signal_11``,
``…:timeout``, ``…:eof``) is the machine-readable string the FE translates — the
gateway stays up and says *what* died. A child that is alive but refuses the call
raises :class:`SidecarWorkerError` instead, so a bad request never burns a restart.

Re-exported here (never core internals — the app boundary) so a provider app imports
``personalclaw.sdk.sidecar``, exactly like ``personalclaw.sdk.tts``.
"""

from personalclaw.local_models.sidecar import (  # noqa: F401
    SidecarCrashed,
    SidecarRunner,
    SidecarWorkerError,
    get_runner,
    register_runner,
    sidecar_venv_dir,
    unregister_runner,
)

__all__ = [
    "SidecarCrashed",
    "SidecarRunner",
    "SidecarWorkerError",
    "get_runner",
    "register_runner",
    "sidecar_venv_dir",
    "unregister_runner",
]
