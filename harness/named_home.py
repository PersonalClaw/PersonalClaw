"""The one way a committed dev tool names the PersonalClaw home it acts on, and finds that home's
gateway.

pytest cannot let a test touch the developer's real home (``tests/real_home_guard.py``), but nothing
held the tools beside the suite to that, and three of them reached the install the moment they were
run with nothing set: a task seeder wiped the tasks of whatever answered on port 10000, which is the
install's own port; a memory validator wrote a probe row through that port and opened the default
home's ``memory.db`` itself; a classify smoke told its reader to point it at the default home. A dev
tool's default is what it does when someone runs it without reading its header, so the only safe
default is none.

So a tool that acts on a home asks here, and gets the home it was NAMED or a refusal it prints:

* :func:`scratch_home` — the home named by ``--home``, else by ``PERSONALCLAW_HOME``. Nothing named
  is refused, and so is a name that resolves to the default home, the install's own data. That is
  asked the way every "not against the real home" rail asks it
  (``config.loader.uses_default_home``), so a symlink to the default home, or a system directory
  the resolver refuses and replaces with the default home, is refused too. Only an accepted name
  is exported, so every product call this process makes afterwards resolves that home and no
  other.
* :func:`scratch_gateway` — that home's running gateway, from the record a gateway writes into its
  own home once it listens (``gateway_base``), and a short-lived owner token minted through the
  home's local secret, the handshake ``personalclaw token`` makes. There is no port to pass: the
  port is whatever the named home's gateway bound.

``python -m harness.named_home`` prints that gateway as one JSON line,
``{url, token, home, pid}``, for the JavaScript tools (``scripts/lib/named_home.mjs``), so
the rule is written once. ``tests/test_dev_tools_home_rail_no_install_by_default.py`` holds the
trees these tools live in to it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: How long a dev tool's token lasts: long enough for a seed or a capture, and no longer.
TOKEN_TTL = "1h"

#: A loopback request must never be handed to a proxy from the environment.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Refused(SystemExit):
    """A dev tool will not run against what it was given, or not given.

    The message is the sentence the person reads, naming what to give it instead. A
    ``SystemExit``, so a tool that does not catch it ends with that sentence and a non-zero
    status, and a broad ``except Exception`` in the tool cannot swallow it.
    """


def start_command(home: str | os.PathLike[str]) -> str:
    """The command that starts a gateway on *home*, for a refusal to quote."""
    return f"PERSONALCLAW_HOME={home} personalclaw gateway --port auto --no-open"


def add_home_argument(parser: argparse.ArgumentParser) -> None:
    """The ``--home`` option every dev tool takes, worded once."""
    parser.add_argument(
        "--home",
        metavar="DIR",
        default=None,
        help="the scratch PersonalClaw home to act on (default: $PERSONALCLAW_HOME). "
        "Nothing named, or the default home, is refused.",
    )


def scratch_home(named: str | os.PathLike[str] | None = None) -> Path:
    """The scratch home this process acts on: *named*, else ``$PERSONALCLAW_HOME``.

    Raises :class:`Refused` when neither names one, or when the name resolves to the default
    home. An accepted name is exported as ``PERSONALCLAW_HOME`` and returned resolved; a refused
    one is never exported. Nothing is created: whether the folder must exist is the caller's
    question.
    """
    raw = os.fspath(named) if named else os.environ.get("PERSONALCLAW_HOME", "")
    if not raw.strip():
        raise Refused(
            "Name the scratch home this runs against: --home DIR, or PERSONALCLAW_HOME=DIR. "
            "With none named it would act on the default home, which is the install's own data."
        )
    from personalclaw.config.loader import default_config_dir, resolve_config_dir, uses_default_home

    candidate = {**os.environ, "PERSONALCLAW_HOME": raw}
    try:
        is_default = uses_default_home(candidate)
    except OSError as exc:
        raise Refused(
            f"{raw} cannot be resolved ({exc}), so there is no telling whether it is the "
            "install's own home. Name a scratch folder."
        ) from exc
    if is_default:
        raise Refused(
            f"{raw} resolves to the default home, {default_config_dir(candidate)}: the install's "
            "own data. Name a scratch home instead, and start a gateway on it if this needs one: "
            f"{start_command('DIR')}"
        )
    os.environ["PERSONALCLAW_HOME"] = raw
    return resolve_config_dir(candidate)


@dataclass(frozen=True)
class ScratchGateway:
    """A named scratch home's running gateway, and an owner token for it."""

    home: Path
    port: int
    pid: int
    token: str

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def call(
        self, method: str, path: str, body: Any = None, *, timeout: float = 30.0
    ) -> tuple[int, Any]:
        """One request as the owner: ``(status, the body decoded)``. An error status is
        returned, not raised; an unreachable gateway raises the ``OSError`` urllib gives."""
        data = None if body is None else json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with _OPENER.open(request, timeout=timeout) as resp:
                return resp.status, _decoded(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decoded(exc.read())

    def get(self, path: str, *, attempts: int = 6, timeout: float = 15.0) -> Any:
        """GET *path* and return its body, or end the tool saying why.

        A gateway that does not answer is asked again, with a growing pause, up to *attempts*
        times: one that has just started can be too busy warming up to answer at once, and a
        validator checks what the gateway says, not how fast. An error status is not retried.
        """
        last: OSError | None = None
        for attempt in range(attempts):
            try:
                status, body = self.call("GET", path, timeout=timeout)
            except OSError as exc:  # URLError, a refused connection, a timeout
                last = exc
                time.sleep(2 * (attempt + 1))
                continue
            if status >= 400:
                raise SystemExit(f"GET {path} answered {status}: {error_text(body)}")
            return body
        raise SystemExit(
            f"the gateway of {self.home} at {self.url} did not answer GET {path}: {last}"
        )


def _decoded(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return text


def error_text(body: Any) -> str:
    """The message of an error envelope (``{"error": {"code", "message"}}`` or the older
    ``{"error": "…"}``), or the body itself."""
    if isinstance(body, dict) and "error" in body:
        error = body["error"]
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or error)
        return str(error)
    return str(body)


def scratch_gateway(
    named: str | os.PathLike[str] | None = None, *, ttl: str = TOKEN_TTL
) -> ScratchGateway:
    """The running gateway of the scratch home :func:`scratch_home` accepts, signed in.

    Raises :class:`Refused` when the home is refused, does not exist, or has no gateway running
    (its runtime record is missing or names a process that has ended), and when that gateway will
    not mint a token for this home's local secret, which is how it shows it IS that home's
    gateway: one of another home answers 403.
    """
    home = scratch_home(named)
    if not home.is_dir():
        raise Refused(
            f"{home} does not exist, so no gateway of it is running. Start one: "
            f"{start_command(home)}"
        )
    from personalclaw import gateway_base
    from personalclaw.cli_run import RunError, mint_local_token

    live = gateway_base.live_gateway()
    if live is None:
        raise Refused(
            f"No gateway of {home} is running: {home / gateway_base.RUNTIME_FILE} is missing or "
            f"names a process that has ended. Start one: {start_command(home)}"
        )
    try:
        token = mint_local_token(live.port, ttl=ttl)
    except RunError as exc:
        raise Refused(
            f"The gateway {home} records (pid {live.pid}, port {live.port}) did not sign this "
            f"tool in: {exc}"
        ) from exc
    return ScratchGateway(home=home, port=live.port, pid=live.pid, token=token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m harness.named_home",
        description="Print the running gateway of a named scratch home as one JSON line: "
        "url, token, home and pid. The token is an owner session; treat it like a password.",
    )
    add_home_argument(parser)
    args = parser.parse_args(argv)
    gateway = scratch_gateway(args.home)
    print(
        json.dumps(
            {
                "url": gateway.url,
                "token": gateway.token,
                "home": str(gateway.home),
                "pid": gateway.pid,
            }
        )
    )
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
