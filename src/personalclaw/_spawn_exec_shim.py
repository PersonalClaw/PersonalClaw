"""Post-exec resource-ceiling shim — stdlib only, ZERO ``personalclaw`` imports.

This module is prepended to a child's argv as ``python -m personalclaw._spawn_exec_shim
<policy-json> -- <real argv...>``. It is deliberately a *pure-stdlib leaf*: it must import
and run in a bare child that has never touched the rest of the package, so the only names
it may reference are Python's own. A test asserts this (``python -c "import
personalclaw._spawn_exec_shim"`` under ``-S`` succeeds and imports no ``personalclaw.*``
submodule).

Why deliver limits here, after ``exec``, instead of via ``subprocess``'s ``preexec_fn``:
``preexec_fn`` forces CPython off ``posix_spawn``/``vfork`` onto a full ``fork()`` of the
(heavily multi-threaded) gateway and runs Python *before* ``exec`` in the child. A lock
another thread held at fork time can never be released there, so the child can wedge
before reaching ``exec`` — and because ``_posixsubprocess.child_exec()`` closes inherited
fds only *after* ``preexec_fn``, a wedged child keeps a duplicate of every inherited fd
(the gateway lock, the listening socket) alive, while ``Popen._execute_child`` blocks the
event loop in an unbounded ``os.read(errpipe)`` with no ``await`` point. The shim moves the
delivery point past ``exec``: it runs in the already-exec'd, *single-threaded* child, so a
plain ``os.execv`` cannot wedge on an inherited lock. Coverage is identical — an rlimit set
here is inherited by the exec'd image and all its descendants.

Argv contract::

    python -m personalclaw._spawn_exec_shim '<policy-json>' -- <argv0> [argv1 ...]

``<policy-json>`` is a compact JSON object (see ``ResourceCeilings`` in ``sandbox.py`` for
the producer):

    {"limits": {"RLIMIT_NOFILE": [soft, hard], ...}, "oom_score_adj": 1000 | null}

* ``limits`` maps a ``resource.RLIMIT_*`` constant *name* to a ``[soft, hard]`` pair. Each
  element is either an integer or the sentinel string ``"hard"`` (resolved in-child to the
  *inherited* hard limit — this is how the ``session_host`` profile raises NOFILE to the
  hard cap without the parent needing to know the child's inherited limit). Each pair is
  applied best-effort: a value the current hard limit forbids is clamped down to that hard
  limit rather than raising (a child must never fail to start because a ceiling was set too
  high for the inherited hard cap).
* ``oom_score_adj`` (Linux only) is written to ``/proc/self/oom_score_adj`` when non-null,
  biasing the OOM killer toward this child. Best-effort: any error (non-Linux, permission,
  missing procfs) is swallowed.

Degradation contract (load-bearing): on a platform without the ``resource`` module
(Windows) the shim applies no limits and simply ``execv``s the target — it NEVER crashes
the child. The single hard failure mode is a malformed/absent argv (no ``--`` separator or
empty target), which exits non-zero with a diagnostic, because there is nothing to exec.
"""

from __future__ import annotations

import json
import os
import sys

try:  # ``resource`` is POSIX-only; absent on Windows.
    import resource as _resource
except ImportError:  # pragma: no cover - exercised only on non-POSIX hosts
    _resource = None  # type: ignore[assignment]


def _apply_limits(policy: dict) -> None:
    """Apply each ``RLIMIT_*`` in *policy* best-effort. Never raises."""
    if _resource is None:
        return
    limits = policy.get("limits") or {}
    if not isinstance(limits, dict):
        return
    for name, pair in limits.items():
        const = getattr(_resource, name, None)
        if const is None or not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        try:
            cur_soft, cur_hard = _resource.getrlimit(const)
            # Resolve the "hard" sentinel and clamp: never ask for more than the
            # inherited hard cap allows — an unprivileged process cannot raise a hard
            # limit, and a soft/hard above it raises ValueError. Clamp to the inherited
            # hard limit instead so the child always starts.
            eff_hard = _resolve_limit(pair[1], cur_hard, cur_hard)
            eff_soft = _resolve_limit(pair[0], cur_hard, eff_hard)
            if eff_hard >= 0:
                eff_soft = min(eff_soft, eff_hard) if eff_soft >= 0 else eff_soft
            _resource.setrlimit(const, (eff_soft, eff_hard))
        except (ValueError, OSError):
            # A limit we cannot set is skipped, not fatal — the child still runs
            # under whatever it inherited.
            continue


def _resolve_limit(value: object, cur_hard: int, cap: int) -> int:
    """Resolve one limit element to an int, honoring the ``"hard"`` sentinel and clamp.

    * ``"hard"`` → the inherited hard limit (``cur_hard``).
    * a negative int (e.g. ``RLIM_INFINITY``) → passed through unchanged.
    * a non-negative int → clamped to ``cap`` when ``cap`` is finite (``>= 0``).
    """
    if isinstance(value, str) and value == "hard":
        return cur_hard
    if not isinstance(value, int):
        return cur_hard
    if value < 0:
        return value
    if cap >= 0:
        return min(value, cap)
    return value


def _apply_oom_bias(policy: dict) -> None:
    """Write ``oom_score_adj`` on Linux when the policy asks. Best-effort; never raises."""
    adj = policy.get("oom_score_adj")
    if adj is None:
        return
    try:
        with open("/proc/self/oom_score_adj", "w", encoding="ascii") as fh:
            fh.write(str(int(adj)))
    except (OSError, ValueError):
        # Non-Linux, no procfs, or denied — the OOM bias is an optional second-order
        # control; its absence never blocks the child.
        pass


def _split_argv(raw: list[str]) -> tuple[str, list[str]]:
    """Return ``(policy_json, target_argv)`` from the shim's own argv tail.

    Expects ``<policy-json> -- <argv...>``. Raises ``SystemExit`` with a diagnostic on a
    malformed invocation, since there is no target to exec.
    """
    if "--" not in raw:
        raise SystemExit("_spawn_exec_shim: missing '--' argv separator")
    sep = raw.index("--")
    head = raw[:sep]
    target = raw[sep + 1 :]
    if not head:
        raise SystemExit("_spawn_exec_shim: missing policy argument before '--'")
    if not target:
        raise SystemExit("_spawn_exec_shim: no target command after '--'")
    return head[0], target


def main(argv: list[str] | None = None) -> None:
    """Apply ceilings from the policy arg, then ``execv`` the real target in place."""
    raw = list(sys.argv[1:] if argv is None else argv)
    policy_json, target = _split_argv(raw)
    try:
        policy = json.loads(policy_json)
        if not isinstance(policy, dict):
            policy = {}
    except (ValueError, TypeError):
        # A corrupt policy string is treated as "no limits" rather than refusing to
        # exec — the target still runs, just uncapped. (The producer always emits
        # valid JSON; this is the defensive floor.)
        policy = {}

    _apply_limits(policy)
    _apply_oom_bias(policy)

    # Replace this process image with the real target, keeping the pid and every
    # inherited fd/limit — coverage of every descendant follows from inheritance.
    # ``execvp`` (not ``execv``) so a bare target name (e.g. an MCP server launched as
    # ``npx``) resolves against the child's inherited PATH, matching subprocess's own
    # default lookup; an absolute path is used directly.
    try:
        os.execvp(target[0], target)
    except OSError as exc:  # e.g. ENOENT — surface it rather than exiting 0 silently.
        raise SystemExit(f"_spawn_exec_shim: cannot exec {target[0]!r}: {exc}") from exc


if __name__ == "__main__":
    main()
