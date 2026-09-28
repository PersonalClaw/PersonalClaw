"""The owner's yes to a script the agent CLI's own hooks run.

``agent.agent_hooks`` in ``config.json`` and the executables in ``<home>/hooks`` (or the
``agent_hooks_dir`` it names) are merged into the agent CLI's config (`agent._merge_agent_hooks`),
and the CLI runs each one on its event — before or after a tool call, on a prompt, at the start or
end of a turn — with nobody asked. Measured on `main`: a script the agent's shell wrote into
``<home>/hooks`` and made executable was merged at the next config rebuild and ran before every
tool call after it. The write side is fenced now (`owner_only`), and this is the second half the
fence does not replace: a hook is merged only once the owner allowed it, sealed to the bytes of the
file it runs (`owner_grants`). A new hook, or one whose file changed, is left out of the agent's
config and listed on the Agents page as waiting, with Allow, which asks first.

🔴 WHAT THE CLI RUNS IS THE COPY THE OWNER ALLOWED, not the file. The CLI runs a hook's command on
its event, long after the config was built, so a file rewritten in between — a script outside the
home is no owner-only path — would run its new bytes on the old yes. So the merged command is a
copy of the file as allowed (:func:`pinned`), kept under ``<home>/hooks/.allowed/`` by its seal,
where only the owner's surfaces write: an edit to the file never runs until the owner allows it.

A hook's identity is its event, the real path of the file it runs and its matcher: the same file on
another event, or under another matcher, runs at another moment, and is another question.
"""

from __future__ import annotations

from pathlib import Path

from personalclaw.atomic_write import atomic_write_bytes
from personalclaw.owner_grants import GrantBook, seal

#: Where the owner's yes to each agent hook is kept (`owner_grants`).
BOOK = GrantBook("agent_hooks")

#: When each event's hook runs, as the owner reads it in the consent sentence.
_WHEN = {
    "preToolUse": "before every tool call it matches",
    "postToolUse": "after every tool call it matches",
    "userPromptSubmit": "on every prompt you send",
    "agentSpawn": "each time an agent session starts",
    "stop": "each time an agent turn ends",
}


def key(event: str, command: str, matcher: str | None) -> str:
    return f"{event}\0{command}\0{matcher or ''}"


def _content(command: str) -> bytes | None:
    try:
        return Path(command).read_bytes()
    except OSError:
        return None


def seal_of(command: str) -> str:
    """The seal of the file *command* runs, as it is now; ``""`` when it cannot be read."""
    data = _content(command)
    return seal(data) if data is not None else ""


#: Where the copies the CLI runs are kept, under the home's owner-only ``hooks/`` (`owner_only`).
#: A folder, not ``*.sh``, so the hooks folder's import (`agent._autoimport_agent_hooks`, which
#: reads the ``*.sh`` files at its top) never takes a copy for a hook of its own.
PINNED_DIR = ".allowed"


def pinned_dir() -> Path:
    from personalclaw.config.loader import config_dir

    return Path(config_dir()) / "hooks" / PINNED_DIR


def pinned(event: str, command: str, matcher: str | None, *, write: bool = True) -> str:
    """The command the CLI runs for this hook — the copy of its file as the owner allowed it —
    or ``""`` when the owner has not allowed the file as it is now.

    The file is read ONCE, and that read is both what the seal is checked against and what is
    copied, so no change between the check and the copy can slip into the copy. *write* False
    answers without writing the copy (a page read, which is not a merge).
    """
    data = _content(command)
    if data is None or not BOOK.holds(key(event, command, matcher), data):
        return ""
    target = pinned_dir() / seal(data)
    if write and not target.is_file():
        # Executable by its owner alone: the CLI runs it, and nobody else may read it.
        atomic_write_bytes(target, data, mode=0o700)
    return str(target)


def allowed(event: str, command: str, matcher: str | None) -> bool:
    """Whether the owner allowed this hook with its file exactly as it is now. A file that cannot
    be read is not allowed: there is nothing to have said yes to."""
    return bool(pinned(event, command, matcher, write=False))


def allow(event: str, command: str, matcher: str | None, *, seen: str) -> bool:
    """Record the owner's yes to the hook, as its file stands — only when it is still the file
    they looked at (*seen*, its seal). False, and nothing given, when it changed since."""
    data = _content(command)
    if data is None or seal(data) != seen:
        return False
    BOOK.give(key(event, command, matcher), data)
    return True


def consent(event: str, command: str) -> str:
    """The sentence the owner agrees to — product copy."""
    when = _WHEN.get(event, f"on {event}")
    return (
        f"Allowing “{command}” lets your agent's CLI run it {when}, as the file is now. A change "
        "to the file does not run until you allow it again."
    )


#: The consent dialog's heading for :func:`consent` (`http_errors.consent_required`).
CONSENT_TITLE = "Allow this agent hook to run?"
