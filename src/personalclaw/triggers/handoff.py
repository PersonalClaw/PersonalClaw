"""Intercept a SYSTEM scheduler write and offer the substrate instead (§7 criterion 12 — S143).

Criterion 12: *"An agent attempting `crontab -e` is prompted and offered the substrate;
`automation doctor` flags an orphaned workflow ref and a broad file-watch glob."* The second clause
shipped (`calendar.diagnose` + `GET /api/triggers/doctor`, S110). The first was **unmet — measured,
not inferred**::

    is_sensitive_bash_command("crontab -e")                     -> None
    denied_command_reason("crontab -e")                         -> None
    is_sensitive_bash_command("echo '* * * * *' | crontab -")   -> None
    ... launchctl load / systemctl --user enable                 -> None, None

So an agent could install a cron in the user's real crontab and nothing said a word. The one
`crontab` pattern that does exist lives in `supply_chain.py`'s app-bundle scanner, which reads app
files at INSTALL time — a different surface, and one an agent's own bash call never touches.

**Why this matters beyond tidiness.** A cron in the system crontab is invisible to every surface
this program spent 65 sessions building: no ledger row, no autopause, no quiet window, no capability
fence, no kill switch, no run history. It survives uninstall. It is the one way for unattended work
to escape the substrate entirely — so an agent reaching for it is exactly when to say "there is a
supported way to do this".

**PROMPTED, not blocked — and this is the whole design decision.** Criterion 12 says *prompted and
offered*, and the distinction is load-bearing in both directions:

* Blocking would be wrong. Reading a crontab is diagnostic (`crontab -l` is how you find out what is
  already scheduled), and a legitimate one-off — reproducing a user's bug, migrating their existing
  crons INTO the substrate — is real work. A hard denial would make the migration path itself
  impossible, which is the opposite of offering an alternative.
* Silence is also wrong, which is what shipped.

So: WRITES prompt and carry the offer; READS pass silently. The read/write split is the whole
predicate, because it is what separates "look at what is scheduled" from "schedule something outside
every control on this machine".

**Fail-OPEN on an unrecognized command.** This is an advisory seam, not a security fence: its output
is a prompt and a suggestion. A false positive nags the user about a legitimate command and trains
them to click through prompts — which degrades every *real* approval prompt on the machine. The
capability fence, PathGuard and the denylist are the fail-closed controls (see
`docs/architecture/provider-boundary.md` and `triggers/pathguard.py`); this is not one of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: The three system schedulers a user can actually reach on a supported platform. Each is matched
#: only in its WRITING form — see the module docstring for why reads pass.
#:
#: `crontab` is matched on its write FLAGS and on the `| crontab -` stdin idiom, not on the bare
#: word: `crontab -l` is diagnostic, and flagging it would train the user to dismiss prompts.
#: `crontab <file>` (no flag) installs a file and IS a write — the classic footgun, since it
#: silently replaces the whole table.
_WRITE_PATTERNS: tuple[tuple[str, "re.Pattern[str]", str], ...] = (
    (
        "cron",
        re.compile(
            # `crontab` must be the COMMAND, i.e. at a command position: string start, or after a
            # `;` `&&` `||` `|` `(` or newline. Measured: without the anchor,
            # `grep -rn crontab docs/` flagged — the pattern read `docs/` as the file being
            # installed. A false positive on an agent GREPPING for the word is exactly the nag that
            # teaches a user to click through prompts, which costs more than it protects.
            r"(?:^|[;&|(\n]|&&|\|\|)\s*(?:sudo\s+)?crontab\s+(?:-[er]\b|-\s*$|-\s*[<]|[^\s-][^\s]*)"
            # …and the stdin idiom, where `crontab -` IS after a pipe: `echo '…' | crontab -`.
            r"|[|]\s*(?:sudo\s+)?crontab\s+-\s*$",
        ),
        "crontab",
    ),
    (
        "launchd",
        re.compile(
            r"\blaunchctl\s+(?:load|bootstrap|enable|submit)\b"
            r"|\blaunchctl\s+(?:unload|bootout)\b"
            r"|>\s*[^\s]*Library/LaunchAgents/",
        ),
        "launchd",
    ),
    (
        "systemd",
        re.compile(
            r"\bsystemctl\b[^\n]*\b(?:enable|start|--now)\b[^\n]*\.timer\b"
            r"|\bsystemd-run\b[^\n]*--on-(?:calendar|active|boot)\b"
            r"|>\s*[^\s]*/systemd/user/[^\s]*\.timer\b",
        ),
        "systemd timer",
    ),
)

#: Read-only invocations, checked FIRST so a diagnostic call can never be caught by a write pattern.
#: `crontab -l` and `launchctl list` are how you find out what already exists — including as the
#: first step of migrating those jobs into the substrate, which this seam exists to encourage.
_READ_PATTERNS: tuple["re.Pattern[str]", ...] = (
    re.compile(r"\bcrontab\s+-[lu]\b"),
    re.compile(r"\blaunchctl\s+(?:list|print|dumpstate)\b"),
    re.compile(r"\bsystemctl\b[^\n]*\b(?:list-timers|list-units|status|show|cat)\b"),
)

#: What the agent is told. Names the SUPPORTED path, because a prompt that only says "no" gets
#: worked around: the model tries `at`, or writes the plist with `python -c`, and the user is no
#: safer for the friction. Naming the tool is what makes the offer an offer.
HANDOFF_HINT = (
    "Use PersonalClaw's automation substrate instead of the system scheduler: it gives this job a "
    "run history, failure autopause, quiet hours, a capability fence and the kill switch, none of "
    "which a system cron has. Create it with the `automation_create` tool (or ask the user to add "
    "it on the Automations page). If you are MIGRATING existing system crons, read them with "
    "`crontab -l` — that is not intercepted — and create one automation per entry."
)


@dataclass(frozen=True)
class Handoff:
    """The verdict on one command.

    `scheduler` is the human name for the prompt copy; `pattern` is the machine key a surface can
    switch on. Both, because a UI needs the first and a test needs the second — and deriving one
    from the other at each call site is how two surfaces end up disagreeing.
    """

    scheduler: str
    pattern: str
    command: str

    @property
    def reason(self) -> str:
        return (
            f"This writes to the system {self.scheduler}, which puts the job outside PersonalClaw "
            "entirely — no run history, no autopause, no quiet hours, no kill switch, and it "
            "survives uninstall."
        )

    @property
    def observation(self) -> str:
        """The text handed back to the model when a surface declines the call."""
        return f"{self.reason} {HANDOFF_HINT}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheduler": self.scheduler,
            "pattern": self.pattern,
            "reason": self.reason,
            "hint": HANDOFF_HINT,
        }


def detect(command: str) -> Handoff | None:
    """Whether *command* writes to a system scheduler. Returns the offer, or None.

    Reads are checked FIRST and win: a command with both (`crontab -l > backup && crontab new`) is
    ambiguous, and the safe reading of an ambiguous command is the non-nagging one — see the module
    docstring on why a false positive is the expensive failure here.

    Never raises. An unparseable command is not a scheduler write as far as this seam is concerned.
    """
    if not command or not isinstance(command, str):
        return None
    try:
        text = command.strip()
        if any(pattern.search(text) for pattern in _READ_PATTERNS):
            return None
        for key, pattern, label in _WRITE_PATTERNS:
            if pattern.search(text):
                return Handoff(scheduler=label, pattern=key, command=text[:200])
    except Exception:  # noqa: BLE001 - advisory only; see the module docstring
        return None
    return None


def needs_prompt(command: str) -> bool:
    """Thin predicate for a caller that only needs the boolean."""
    return detect(command) is not None
