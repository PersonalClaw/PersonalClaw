"""The owner's Settings → Security → Git push switch, and the one kind of push it lets past the
baseline: :func:`plain_push`, an ordinary publish of a branch.

Off by default (``security.allow_git_push``). While it is off, the baseline's push rule refuses
every push, as it always has, and its refusal says where the switch is. While it is on, the rule
lets a plain push through; everything else the rule catches stays refused. The packaged baseline
file and its digest are untouched: the exemption is applied where one pattern is matched
(``security.denied_command``, ``security.is_denied``), never by removing the pattern.

A push still reaches the network, so the approval path asks about it each time in a chat and
refuses it in a run nobody watches unless the owner's own rules allow it (``run_bounds``). This
module adds no way to approve one.

Deny by default: :func:`plain_push` answers True only for ONE command, read as the shell reads it
(``shell_syntax.one_command``), of the form ``git [-C <dir>] push [options] [remote] [refspec…]``
where the remote, when named, is a configured remote's name (``origin``), never a URL or a path,
where every option is on a short list that publishes, and no refspec forces or deletes. Anything
this reader does not know is not plain, so a new git option is refused until it is listed here.

Refused even with the switch on: ``-f``/``--force`` (also inside a group such as ``-fu``),
``--force-with-lease`` without an explicit ``=<ref>:<expected-sha>``, a lease beside ``--force``,
``--force-if-includes``, ``--mirror``, ``-d``/``--delete``, a ``:<ref>`` refspec (a delete), a
``+<refspec>`` (a forced update), ``--all``, ``--tags``, ``--prune``, ``--no-verify``,
``--receive-pack``/``--exec``, any ``git`` option before ``push`` other than ``-C <dir>``, a word
the shell expands (``$BRANCH``, a glob), a remote given as a URL or a path (or through
``--repo``), a redirect that opens a file (``2>&1``, which only copies a descriptor, is
allowed), and a push joined to another command.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone

from personalclaw.shell_syntax import duplicates_a_descriptor, one_command

logger = logging.getLogger(__name__)

#: The config field the switch is.
FIELD = "security.allow_git_push"

#: Where the switch is, named by every refusal while it is off.
SWITCH_PLACE = "Settings → Security → Git push"

#: How to publish from another folder, said by every refusal of a push.
ONE_COMMAND_HINT = (
    "a push is allowed only as one command on its own: `cd <dir> && git push …` is refused, and "
    "`git -C <dir> push …` is the form to use"
)

#: What stays refused with the switch on, said when a push is refused while it is on.
STILL_REFUSED = (
    "a remote given as a URL or a path instead of a configured remote's name, "
    "a force push without an explicit lease (`--force-with-lease=<ref>:<expected-sha>`), "
    "--mirror, --delete, a `:<ref>` or `+<ref>` refspec, --all, --tags, --prune and --no-verify "
    "stay refused"
)

_SUB = "push"

#: Options that only publish what is named, each taking no value.
_FLAGS = frozenset(
    {
        "-u",
        "--set-upstream",
        "-n",
        "--dry-run",
        "-v",
        "--verbose",
        "-q",
        "--quiet",
        "--porcelain",
        "--progress",
        "--no-progress",
        "--atomic",
        "--no-atomic",
    }
)
#: Options that take a value, as ``--opt value`` or ``--opt=value``.
_VALUED = frozenset({"-o", "--push-option"})
#: Short flags that may be grouped (``-uv``).
_SHORT_GROUPABLE = frozenset("unvq")

_LEASE = re.compile(r"^--force-with-lease=[^:\s]+:[0-9a-fA-F]{7,40}$")
_REF = re.compile(r"^[A-Za-z0-9._/@{}^~-]+(:[A-Za-z0-9._/@{}^~-]+)?$")
#: A configured remote's name: no `:` and no `/`, so never a URL, an scp-style address or a
#: path. A name of dots alone (`.`, `..`) is a folder, so it is refused too.
_REMOTE = re.compile(r"^[A-Za-z0-9._-]+$")


def switch_on() -> bool:
    """Whether the owner turned the switch on. Fails closed: a config that cannot be read is off
    (``config.loader.CONFIG_ON_DISCARDED_READ`` holds it off too)."""
    try:
        from personalclaw.config.loader import AppConfig

        return AppConfig.load().security.allow_git_push is True
    except Exception:  # noqa: BLE001 - an unreadable switch never lets a push through
        logger.warning("the git push switch could not be read; treating it as off", exc_info=True)
        return False


def plain_push(command: str) -> bool:
    """Whether *command* is one ordinary publish of a branch (the module docstring says what is)."""
    simple = one_command(command or "")
    if simple is None:
        return False
    # `2>&1` opens no file and starts nothing; every other redirect writes or reads one.
    if not all(duplicates_a_descriptor(r, target) for r, target in simple.redirects):
        return False
    if any(w.opaque or w.glob_at >= 0 for w in simple.words):
        return False
    args = [w.text for w in simple.words]
    if not args or args[0] != "git":
        return False
    i = 1
    while i < len(args) and args[i] != _SUB:
        if args[i] == "-C" and i + 1 < len(args):
            i += 2
            continue
        return False
    if i >= len(args):
        return False
    rest = args[i + 1 :]
    operands: list[str] = []
    j = 0
    while j < len(rest):
        a = rest[j]
        if a == "--":
            operands.extend(rest[j + 1 :])
            break
        if a.startswith("--force-with-lease"):
            if not _LEASE.match(a):
                return False
        elif a in _FLAGS:
            pass
        elif a in _VALUED:
            if j + 1 >= len(rest):
                return False
            j += 1
        elif a.startswith("--") and "=" in a and a.split("=", 1)[0] in _VALUED:
            pass
        elif a.startswith("-") and not a.startswith("--") and len(a) > 1:
            if not set(a[1:]) <= _SHORT_GROUPABLE:
                return False
        elif a.startswith("-"):
            return False
        else:
            operands.append(a)
        j += 1
    if any(a.startswith("--force-with-lease") for a in rest) and any(
        a in ("-f", "--force") for a in rest
    ):
        return False
    if operands and not _remote_name(operands[0]):
        return False
    return all(not r.startswith(("+", ":")) and _REF.match(r) for r in operands[1:])


def _remote_name(word: str) -> bool:
    """Whether *word* names a configured remote, not a URL, an address or a folder."""
    return bool(_REMOTE.match(word)) and not word.startswith("-") and set(word) != {"."}


def allowed(command: str) -> bool:
    """Whether the switch lets *command* past the baseline's push rule: it is on, and *command*
    is a plain push."""
    return plain_push(command) and switch_on()


def refusal_clause() -> str:
    """What a refusal by the push rule adds: where the switch is while it is off, what stays
    refused while it is on, and the one-command form either way."""
    if switch_on():
        return (
            f"pushing is allowed, but only as an ordinary push: {STILL_REFUSED}; {ONE_COMMAND_HINT}"
        )
    return (
        f"pushing is off; the owner can allow ordinary pushes in {SWITCH_PLACE}, and then "
        f"{ONE_COMMAND_HINT}"
    )


def log_change(on: bool, *, caller: str, source: str) -> None:
    """Record the owner turning the switch on or off in the security event log. Best effort: the
    write it follows has already been made, and a failed audit must not undo it."""
    try:
        from personalclaw.sel import SecurityEvent, SecurityEventLog

        SecurityEventLog().log(
            SecurityEvent(
                event_id=uuid.uuid4().hex[:16],
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                event_type="security_setting_changed",
                caller_identity=caller,
                agent="personalclaw",
                source=source,
                operation=FIELD,
                outcome="enabled" if on else "disabled",
                resources=f"{FIELD}={'true' if on else 'false'}",
            )
        )
    except Exception:  # noqa: BLE001 - audit must not undo the owner's write
        logger.warning("the security log did not record the git push switch change", exc_info=True)
