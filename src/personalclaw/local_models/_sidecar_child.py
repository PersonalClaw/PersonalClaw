"""The sidecar child harness — a newline-JSON stdio worker (LMMV §3.1).

This module runs **inside the app's dedicated venv**, where ``personalclaw`` is NOT
importable: the venv holds the app's own heavy native dependencies (torch,
sentence-transformers, ctranslate2) and nothing else. So this file imports **stdlib
only** and is executed by path::

    <venv>/bin/python -m personalclaw.local_models._sidecar_child  # never — see below
    <venv>/bin/python <this file> --worker /path/to/app/worker.py

The parent (:class:`~personalclaw.local_models.sidecar.SidecarRunner`) passes the
absolute path of this file, so the child never needs the core package on ``sys.path``.

**The protocol.** One JSON object per line, both directions, five verbs and no more
(the §12 scope fence — anything richer is an app backend, not a sidecar):

===========  ==========================================================
``ping``     liveness + pid, no worker involvement
``load``     hand the worker its model/config (idempotent in the worker)
``call``     ``{"method": ..., "payload": {...}}`` → the worker's result
``stat``     child-reported ``rss_mb`` (feeds the memory-pressure widget)
``unload``   drop the worker's model, keep the process
===========  ==========================================================

Request: ``{"id": "<gen>:<seq>", "verb": ..., "payload": {...}}``.
Reply: ``{"id": ..., "ok": true, "result": ...}`` or
``{"id": ..., "ok": false, "error": ..., "reason": ...}``.
A stat frame (``{"stat": {...}}``, no ``id``) follows every reply — that is the
"periodic" report the widget consumes, emitted on the reply thread so it can never
interleave mid-line with a reply (two writers on one pipe is a corrupted frame).

**Two robustness rules that are the whole point of the isolation:**

1. A line without its terminating newline is a TRUNCATED frame and is refused, never
   acted on. A child killed mid-write leaves exactly that, and treating a half-written
   frame as complete is the failure mode the sidecar exists to prevent.
2. The worker's ``sys.stdout`` is redirected to stderr for the duration of every call.
   Native ML libraries print progress and warnings to stdout; a stray ``print`` would
   otherwise land in the middle of the protocol stream.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

#: The five verbs. A request naming anything else is refused with
#: ``reason="unknown_verb"`` rather than guessed at.
VERBS = ("ping", "load", "call", "stat", "unload")


def _rss_mb() -> float:
    """This process's resident-set size in MB (0.0 when unobtainable).

    ``ru_maxrss`` is bytes on macOS and kilobytes on Linux — the same divisor split
    the gateway's own system-info probe uses.
    """
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        return round(usage.ru_maxrss / divisor, 1)
    except Exception:
        return 0.0


def _load_worker(path: str) -> Any:
    """Import the app's worker module from an absolute file path (stdlib importlib).

    The worker is app-owned code living in the app dir; it is loaded by path because
    the child's venv has no notion of the app as a package.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("_personalclaw_sidecar_worker", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load sidecar worker from {path!r}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Dispatcher:
    """Verb → result, with the worker loaded lazily on first use."""

    def __init__(self, worker_path: str) -> None:
        self._worker_path = worker_path
        self._worker: Any = None

    def worker(self) -> Any:
        if self._worker is None:
            self._worker = _load_worker(self._worker_path)
        return self._worker

    def dispatch(self, verb: str, payload: dict[str, Any]) -> Any:
        if verb == "ping":
            return {"pong": True, "pid": os.getpid()}
        if verb == "stat":
            return {"rss_mb": _rss_mb(), "pid": os.getpid()}
        if verb == "load":
            fn = getattr(self.worker(), "load", None)
            return fn(**payload) if callable(fn) else {"loaded": False}
        if verb == "unload":
            fn = getattr(self.worker(), "unload", None)
            if callable(fn):
                fn()
            return {"unloaded": True}
        if verb == "call":
            method = str(payload.get("method") or "")
            if not method:
                raise ValueError("call requires a 'method'")
            # A `call` payload is EXACTLY {"method", "payload"}. Anything else means the
            # caller flattened its arguments one level too high, and the worker would
            # silently receive `{}` — the arg-nesting bug class. Refuse it loudly instead.
            unexpected = sorted(set(payload) - {"method", "payload"})
            if unexpected:
                raise ValueError(
                    f"call payload has unexpected top-level keys {unexpected}; "
                    "worker arguments belong under 'payload'"
                )
            fn = getattr(self.worker(), "call", None)
            if not callable(fn):
                raise AttributeError("worker exposes no call(method, payload)")
            return fn(method, dict(payload.get("payload") or {}))
        raise ValueError(f"unknown verb {verb!r}")


def _reason_for(exc: BaseException) -> str:
    """A typed, machine-readable reason for a worker failure (§1 tenet 3)."""
    if isinstance(exc, (ImportError, AttributeError)):
        return "worker_contract"
    if isinstance(exc, MemoryError):
        return "out_of_memory"
    if isinstance(exc, ValueError):
        return "bad_request"
    return "worker_error"


def main(argv: list[str] | None = None) -> int:
    """Read request lines from stdin, write reply + stat frames to stdout forever."""
    args = list(sys.argv[1:] if argv is None else argv)
    worker_path = ""
    if "--worker" in args:
        worker_path = args[args.index("--worker") + 1]

    # fd 1 is the protocol pipe. Keep an unbuffered-ish private handle to it and point
    # sys.stdout at stderr so worker prints can never corrupt a frame.
    protocol = sys.stdout
    sys.stdout = sys.stderr

    def emit(obj: dict[str, Any]) -> None:
        protocol.write(json.dumps(obj, default=str) + "\n")
        protocol.flush()

    dispatcher = _Dispatcher(worker_path)
    for raw in sys.stdin:
        if not raw.endswith("\n"):
            # Truncated request (the parent died mid-write) — refuse it, don't guess.
            break
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue  # not a frame; ignore rather than die
        req_id = request.get("id")
        verb = str(request.get("verb") or "")
        payload = request.get("payload")
        payload = dict(payload) if isinstance(payload, dict) else {}
        try:
            result = dispatcher.dispatch(verb, payload)
            emit({"id": req_id, "ok": True, "result": result})
        except BaseException as exc:  # noqa: BLE001 — every failure is a typed reply
            emit(
                {
                    "id": req_id,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "reason": _reason_for(exc),
                    "traceback": traceback.format_exc(limit=4),
                }
            )
        emit({"stat": {"rss_mb": _rss_mb(), "pid": os.getpid()}})
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised as a real child process
    raise SystemExit(main())
