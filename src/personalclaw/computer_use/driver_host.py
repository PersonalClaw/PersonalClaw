"""The ceilinged child process the platform driver runs inside (`DCU-4`, §3.5).

**Why a subprocess at all**, when `DCU-3`'s macOS driver is a ctypes FFI that would happily
import in-process: §3.5 requires the driver to run as a ceilinged spawn so *"a wedged/looping
driver is bounded by the kernel, not just a userspace timeout"*. An accessibility call into an
unresponsive application can block for a long time inside the OS, and a blocked FFI call inside
the gateway is a blocked gateway. So the driver lives here, behind
``sandbox.create_subprocess_limited``, and the dispatch talks to it over one JSON request on
stdin and one JSON response on stdout.

**This process holds NO authority.** It never reads the keystone, never consults the allowlist,
and never screens an input target. By the time an operation reaches this module the dispatch has
already decided, audited, and committed; a second copy of any of those checks here would be a
second home for one policy, and this is the process an operator's own driver code will one day
run inside. Its only job is to turn one operation into one OS call and report honestly.
``tests/test_computer_use_dispatch.py::test_the_driver_child_makes_no_policy_decision`` asserts
that by AST.

**Every platform in the map now resolves, and each answers for itself.** `DCU-3` landed the macOS
driver and `DCU-6` the Windows and Linux ones, so :func:`resolve_driver` no longer returns
``None`` for a mapped platform: macOS runs real accessibility calls, and Windows/Linux answer
``ERR_COMPUTER_USE_PLATFORM_UNSUPPORTED`` naming the API a real driver there will use. §3 floor 6
is explicit about the alternative being unacceptable: an unsupported platform reports a typed
refusal, *"never a silent no-op or a simulated success"*.

The ``None`` branch below is therefore **not** dead — it is what a platform *outside* the map
gets (an unrecognised ``platform.system()``), and keeping the two answers apart is deliberate:
``ERR_COMPUTER_USE_DRIVER_UNAVAILABLE`` means *this build has no driver for you and never claimed
to*, while the Windows/Linux code means *you are on a platform this build names and intends, and
the implementation is what is missing*. An operator acts on those differently, so they are not
one code.
"""

from __future__ import annotations

import json
import platform
import sys
from typing import Any

#: Platform → the driver module that serves it. All three exist and all three import on any
#: platform (`DCU-6`'s two never touch an OS library), so this mapping is a fact rather than an
#: intention — which matters, because a name pointing at an absent module and a real driver
#: raising ``ImportError`` reach :func:`resolve_driver` as the same ``None``.
DRIVER_MODULES = {
    "Darwin": "personalclaw.computer_use.macos_driver",
    "Windows": "personalclaw.computer_use.windows_driver",
    "Linux": "personalclaw.computer_use.linux_driver",
}

ERR_DRIVER_UNAVAILABLE = "ERR_COMPUTER_USE_DRIVER_UNAVAILABLE"


def resolve_driver(system: str = "") -> Any:
    """The platform driver module, or ``None`` when this build has none for *system*.

    Resolution is by import, not by a capability flag: a flag can say yes about a module that
    is not there. ``None`` is the honest answer and the caller turns it into a refusal — this
    function never invents a stand-in, because a stand-in that accepted an operation and did
    nothing is the *simulated success* §3 floor 6 forbids.
    """
    import importlib

    name = DRIVER_MODULES.get(system or platform.system())
    if not name:
        return None
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def run_op(request: dict[str, Any]) -> dict[str, Any]:
    """Run one operation and return the answer envelope. Never raises.

    A raise here would reach the dispatch as an unparseable answer, which it reports as a
    driver defect — true but unhelpful. Returning the envelope keeps the reason legible all the
    way to the model.
    """
    system = platform.system()
    driver = resolve_driver(system)
    if driver is None:
        expected = DRIVER_MODULES.get(system, "")
        return {
            "error": {
                "code": ERR_DRIVER_UNAVAILABLE,
                "message": f"no accessibility driver is available for {system or 'this platform'}",
                "why": (
                    "Desktop computer use needs a platform driver to walk the accessibility "
                    "tree and post input. This build ships none for "
                    f"{system or 'this platform'}"
                    + (f" (it would be {expected})." if expected else ".")
                ),
                "fix": (
                    "Nothing to configure — the capability is armed but has no driver to run. "
                    "Nothing was clicked, typed or changed on the desktop."
                ),
            }
        }
    op = str(request.get("op") or "")
    handler = getattr(driver, f"op_{op}", None)
    if handler is None:
        return {
            "error": {
                "code": ERR_DRIVER_UNAVAILABLE,
                "message": f"the {system} driver does not implement {op!r}",
                "why": "The driver exists but has no handler for this operation.",
                "fix": "Nothing happened on the desktop. Use a different tool.",
            }
        }
    try:
        result = handler(dict(request))
    except Exception as exc:  # noqa: BLE001 - a driver fault is a reported envelope
        return {
            "error": {
                "code": ERR_DRIVER_UNAVAILABLE,
                "message": f"the {system} driver failed: {type(exc).__name__}: {exc}",
                "why": "The OS accessibility call did not complete.",
                "fix": "Nothing was changed on the desktop. Retry, or re-snapshot first.",
            }
        }
    return result if isinstance(result, dict) else {"result": result}


def main(argv: list[str] | None = None) -> int:
    """Read one JSON request from stdin, write one JSON answer to stdout.

    One request per process, deliberately. A long-lived driver process would accumulate OS
    handles on the operator's windows between calls and would need its own lifecycle, its own
    idle timeout and its own crash recovery; a per-operation child gets all three from the
    kernel for free, and the resource ceiling is re-applied on every single operation instead of
    once at the start of a session.
    """
    try:
        request = json.loads(sys.stdin.read() or "{}")
    except Exception:
        request = None
    if not isinstance(request, dict):
        answer: dict[str, Any] = {
            "error": {
                "code": ERR_DRIVER_UNAVAILABLE,
                "message": "the driver received no readable request",
                "why": "stdin did not carry a JSON object.",
                "fix": "Nothing happened on the desktop; this is an internal defect.",
            }
        }
    else:
        answer = run_op(request)
    sys.stdout.write(json.dumps(answer, default=str))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the real spawn
    raise SystemExit(main())
