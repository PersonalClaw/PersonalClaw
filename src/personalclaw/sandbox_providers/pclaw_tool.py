"""The ``pclaw-tool`` shim: how an agent inside a sandbox calls a host tool (§5.2).

A single static script, copied into the sandbox by the sandbox handle, that the agent invokes
like an ordinary CLI::

    pclaw-tool memory_recall --query "the deploy runbook"
    pclaw-tool memory_remember --text "prefers tabs"     # refused under a read-only profile

What it does not contain is the point. There is no socket, no HTTP client, no URL, no token and
no host/port — the transport is two file descriptors the HOST created before this process existed,
so possession of the fd IS the authorisation. Nothing inside the sandbox can be connected to, and
there is no credential to find:

* ``ss``/``netstat`` inside the sandbox report zero listening sockets, because nothing here binds.
* Grepping the sandbox filesystem and environment for credential material finds none, because the
  shim needs none. The only environment it reads is two integers (which fd to write, which to
  read) and an optional advertised tool list for ``--help``.

:data:`SHIM_SOURCE` is the whole program. :func:`install_shim` materialises it; an isolated
provider tier (docker/lima) copies the materialised file in through its own ``copy_file_in`` and
launches the child with the two fds inherited.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

#: Env var carrying the fd the shim WRITES requests to.
ENV_REQUEST_FD = "PCLAW_TOOL_REQUEST_FD"
#: Env var carrying the fd the shim READS responses from.
ENV_RESPONSE_FD = "PCLAW_TOOL_RESPONSE_FD"
#: Env var carrying the comma-separated tool names the host will actually serve (for ``--help``).
ENV_OFFERED = "PCLAW_TOOL_OFFERED"

#: The shim's filename inside the sandbox.
SHIM_NAME = "pclaw-tool"

#: The default fds. 0/1/2 belong to the agent's own stdio, so the channel starts at 3.
DEFAULT_REQUEST_FD = 3
DEFAULT_RESPONSE_FD = 4

SHIM_SOURCE = '''#!/usr/bin/env python3
"""pclaw-tool — call a PersonalClaw host tool from inside a sandbox.

Transport: two inherited file descriptors. Write one JSON request line to the request fd, read
one JSON response line from the response fd. No sockets, no HTTP, no credentials — the host
created the channel, so holding the fd is the authorisation.

Usage:
  pclaw-tool <tool> [--key value ...]
  pclaw-tool --list
"""
import json
import os
import sys

PROTOCOL = 1
REQUEST_FD = int(os.environ.get("PCLAW_TOOL_REQUEST_FD", "3"))
RESPONSE_FD = int(os.environ.get("PCLAW_TOOL_RESPONSE_FD", "4"))


def _parse(argv):
    if not argv or argv[0] in ("-h", "--help"):
        sys.stderr.write(__doc__ or "")
        return None, None
    if argv[0] == "--list":
        offered = os.environ.get("PCLAW_TOOL_OFFERED", "")
        sys.stdout.write((offered.replace(",", "\\n") + "\\n") if offered else "")
        return None, None
    tool = argv[0]
    args = {}
    i = 1
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            key = token[2:]
            if "=" in key:
                key, value = key.split("=", 1)
                args[key] = value
                i += 1
                continue
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                args[key] = argv[i + 1]
                i += 2
                continue
            args[key] = True
            i += 1
            continue
        args.setdefault("_", []).append(token)
        i += 1
    return tool, args


def main(argv):
    tool, args = _parse(argv)
    if tool is None:
        return 0
    request = {"protocol": PROTOCOL, "id": str(os.getpid()), "tool": tool, "args": args}
    try:
        with os.fdopen(os.dup(REQUEST_FD), "wb", closefd=True) as out:
            out.write((json.dumps(request) + "\\n").encode("utf-8"))
            out.flush()
        with os.fdopen(os.dup(RESPONSE_FD), "rb", closefd=True) as inp:
            line = inp.readline()
    except OSError as exc:
        sys.stderr.write(
            "pclaw-tool: the host tool channel is not available (%s).\\n"
            "This command only works inside a PersonalClaw sandbox.\\n" % exc
        )
        return 69
    if not line:
        sys.stderr.write("pclaw-tool: the host closed the tool channel without answering.\\n")
        return 69
    try:
        response = json.loads(line.decode("utf-8"))
    except ValueError:
        sys.stderr.write("pclaw-tool: unparseable response from the host.\\n")
        return 70
    if response.get("ok"):
        sys.stdout.write(str(response.get("result", "")) + "\\n")
        return 0
    sys.stderr.write(
        "pclaw-tool: %s: %s\\n" % (response.get("code", "ERR"), response.get("error", ""))
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''


def install_shim(directory: str | Path, *, name: str = SHIM_NAME) -> Path:
    """Write the shim into *directory* as an executable file and return its path.

    Written with :func:`personalclaw.atomic_write.atomic_write` so a half-written shim can never
    be observed, and mode 0o755 so the agent can simply run it. Idempotent: re-installing over an
    existing shim replaces it, which is what an updated host must be able to do.
    """
    from personalclaw.atomic_write import atomic_write

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / name
    atomic_write(path, SHIM_SOURCE)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def shim_env(
    *,
    request_fd: int = DEFAULT_REQUEST_FD,
    response_fd: int = DEFAULT_RESPONSE_FD,
    offered: tuple[str, ...] = (),
) -> dict[str, str]:
    """The ONLY environment the shim needs — two fd numbers and the advertised tool list.

    Note what is absent: no token, no URL, no host, no port. A sandbox launched with exactly this
    environment plus the two inherited fds can call host tools and still contains no credential
    material, which is the property success criterion 7(b) asks about.
    """
    env = {
        ENV_REQUEST_FD: str(request_fd),
        ENV_RESPONSE_FD: str(response_fd),
    }
    if offered:
        env[ENV_OFFERED] = ",".join(offered)
    return env
