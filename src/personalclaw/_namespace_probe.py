"""Whether this host lets a new process take the Linux namespaces the sandbox launcher takes.

``sandbox._probe_unshare`` and ``sandbox._probe_unshare_net`` start this module as a child
(``self_update.python_child_argv``), naming the namespaces in the order the launcher takes them:
``user``, then ``mount``, then (for a command with no network) ``net``. It calls ``unshare`` once
for each, separately, and exits 0 only when every call succeeded.

A new process, never a fork of the one asking. The gateway and a test worker have other threads,
and a forked copy that runs Python before it execs inherits every lock one of them held at the
fork, with no thread left to release it. A copy that waited on one never exited, and the probe
waited on the copy with no bound: the gateway's event loop stopped, or a test worker hung past its
timeout (#5638).

A pure-stdlib leaf, like the shim: it imports nothing else of the package.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys

#: Each namespace by the name a caller asks for, as ``unshare`` takes it.
NAMESPACES = {"user": 0x10000000, "mount": 0x00020000, "net": 0x40000000}


def main(names: list[str]) -> int:
    """0 when ``unshare`` succeeds for each of *names*, called once each and in order; else 1."""
    flags = [NAMESPACES[name] for name in names]
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    libc.unshare.argtypes = [ctypes.c_int]
    libc.unshare.restype = ctypes.c_int
    for flag in flags:
        if libc.unshare(flag) != 0:
            return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:  # noqa: BLE001 — no unshare (not Linux), or a name it does not know
        sys.exit(1)
