"""The owner's yes to what a runner definition of theirs runs.

A runner definition of the owner's (``runners/<id>.json``, :mod:`personalclaw.agents.runners`)
names the CLI PersonalClaw runs for it: the binary names it looks for, the variable that points at
another one, the arguments it passes, and the ACP adapter package it launches through. A Check runs
that CLI for its version, a second opinion fires it on a stuck run's workspace
(:mod:`personalclaw.proposer`), and the unattended-spawn gate trusts the adapter it names
(``runners.guard_unattended_spawn``).

It used to do all of that as the file said, whoever wrote the file: a device sync that brought the
definition from another machine, or another machine's edit to one, an agent's shell writing into
``runners/``, an import of another machine's export. So a definition runs only once the owner
allowed what it runs here (`owner_grants`, a book no sync or export carries), sealed to
:func:`definition`. A change to any of it is a new question: the runner waits until the owner
allows the new one. A shipped row (``runner_catalog.json``) asks nothing: it ships with
PersonalClaw, and a file of the same id replaces it, so that file asks.

🔴 ONLY THE OWNER'S SURFACE GIVES A YES: Allow on the runner in Settings → Agent defaults
(``POST /api/agent-runners/{id}/allow``), which shows what it runs before it records anything.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from personalclaw.owner_grants import GrantBook

if TYPE_CHECKING:
    from personalclaw.agents.runners import RunnerDefinition

#: Where the owner's yes to each runner definition is kept, keyed by the runner's id.
BOOK = GrantBook("runners")

#: Why a runner that waits does not run, as each thing that would run it says.
WAITING_REASON = (
    "Not allowed to run yet: PersonalClaw runs this CLI only after you allow what it runs, with "
    "Allow on the runner in Settings → Agent defaults."
)

#: The consent dialog's heading.
CONSENT_TITLE = "Allow this runner to run?"


def exempt(defn: RunnerDefinition) -> bool:
    """A row PersonalClaw ships (``runner_catalog.json``), never a file under ``runners/``."""
    return defn.source == "builtin"


def definition(defn: RunnerDefinition) -> dict[str, Any]:
    """What the seal is taken of: everything the definition says about what runs."""
    adapter = defn.adapter
    return {
        "runtime_id": defn.runtime_id,
        "bin_names": list(defn.bin_names),
        "env_var": defn.env_var,
        "version_args": list(defn.version_args),
        "acp_args": list(defn.acp_args),
        "dialect": defn.dialect,
        "adapter": (
            None
            if adapter is None
            else {
                "npm_pkg": adapter.npm_pkg,
                "env_var": adapter.env_var,
                "bin_names": list(adapter.bin_names),
                "version": adapter.version,
                "integrity": adapter.integrity,
            }
        ),
    }


def _content(defn: RunnerDefinition) -> str:
    return json.dumps(definition(defn), sort_keys=True, separators=(",", ":"))


def allowed(defn: RunnerDefinition) -> bool:
    """Whether the owner allowed *defn* exactly as it is defined now (a shipped row always is)."""
    return exempt(defn) or BOOK.holds(defn.id, _content(defn))


def give(defn: RunnerDefinition) -> None:
    """Record the owner's yes to *defn* as it is defined now. Only the Allow route calls this,
    after its question."""
    BOOK.give(defn.id, _content(defn))


def revision(defn: RunnerDefinition) -> str:
    """The revision of what the owner is shown (`stale_write.revision_of`). Allow names it, so a
    yes is never given to a definition that changed after the page read it."""
    from personalclaw.stale_write import revision_of

    return revision_of(definition(defn))


def _names(names: list[str]) -> str:
    quoted = [f"“{n}”" for n in names]
    if len(quoted) == 1:
        return quoted[0]
    return ", ".join(quoted[:-1]) + " or " + quoted[-1]


def consent(defn: RunnerDefinition) -> str:
    """The sentence the owner agrees to before *defn*'s CLI first runs here. Product copy."""
    from personalclaw.agents.runners import runner_env_var

    looks_for = _names(list(defn.bin_names))
    command = " ".join([defn.bin_names[0], *defn.version_args])
    return (
        f"Allowing “{defn.display_name}” lets PersonalClaw run the program it names as you: "
        f"{looks_for} as found on this machine, or the one {runner_env_var(defn)} points at. It "
        f"runs `{command}` when you press Check version, and works in a run's folder, able to "
        "change its files, when that run is handed to it for a second opinion. Like any program "
        "you start, it can read and change your files and reach the network. A change to what it "
        "runs asks you again."
    )
