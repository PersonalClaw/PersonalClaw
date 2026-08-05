"""Criterion 12 clause 1: a system-scheduler write is prompted and offered the substrate (S143).

Criterion 12: *"An agent attempting `crontab -e` is prompted and offered the substrate;
`automation doctor` flags an orphaned workflow ref and a broad file-watch glob."*

🔴 **The second clause shipped in S110; the first was UNMET.** Measured before writing a line::

    is_sensitive_bash_command("crontab -e")                      -> None
    denied_command_reason("crontab -e")                          -> None
    is_sensitive_bash_command("echo '* * * * * x' | crontab -")  -> None
    is_sensitive_bash_command("launchctl load …LaunchAgents/x.plist") -> None
    is_sensitive_bash_command("systemctl --user enable t.timer") -> None

So an agent could install a cron in the user's real crontab and **nothing said a word**. The one
`crontab` pattern that did exist lives in `supply_chain.py`'s app-bundle scanner, which reads app
FILES at install time — a surface an agent's own bash call never touches. Easy to mistake for
coverage; `grep` finds the word, and the criterion looks met.

Why it matters: a job in the system crontab is invisible to everything the substrate provides — no
ledger row, no autopause, no quiet window, no capability fence, no kill switch — and it survives
uninstall. It is the one way for unattended work to escape the substrate entirely.

**Both dispatch seams are asserted here.** The native bash tool AND the ACP `hooks.on_tool_call`
path, because a control on one of two seams is a control the other silently skips — exactly how the
`web_watch` screen gap (S134) opened.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from personalclaw.triggers.handoff import HANDOFF_HINT, detect, needs_prompt

# ── the read/write split, which is the whole predicate ──

WRITES = [
    "crontab -e",
    "crontab -r",
    "crontab mycron.txt",
    "sudo crontab -e",
    "cd /tmp && crontab jobs.txt",
    "echo '* * * * * backup' | crontab -",
    "crontab - < jobs.txt",
    "launchctl load ~/Library/LaunchAgents/x.plist",
    "launchctl bootstrap gui/501 x.plist",
    "launchctl unload ~/Library/LaunchAgents/x.plist",
    "cat > ~/Library/LaunchAgents/com.me.job.plist",
    "systemctl --user enable --now mytimer.timer",
    "systemd-run --on-calendar='*:0/5' /bin/backup",
]

READS = [
    "crontab -l",
    "crontab -u bob -l",
    "launchctl list",
    "launchctl print gui/501",
    "systemctl list-timers",
    "systemctl --user status mytimer.timer",
    "systemctl cat mytimer.timer",
]

#: Ordinary commands that must NEVER prompt. The expensive failure mode here is the false positive:
#: a seam that nags about `grep -rn crontab docs/` teaches the user to click through prompts, which
#: degrades every REAL approval prompt on the machine. `grep -rn crontab docs/` is not
#: hypothetical —
#: it flagged on the first draft, because the pattern read `docs/` as the file being installed.
INNOCENT = [
    "ls -la",
    "git commit -m 'add cron docs'",
    "grep -rn crontab docs/",
    "grep crontab /etc/passwd",
    "rg 'crontab' src/",
    "man crontab",
    "which crontab",
    "echo 'the crontab is legacy' >> NOTES.md",
    "# install with crontab later",
    "python -c 'print(1)'",
    "systemctl --version",
]


@pytest.mark.parametrize("command", WRITES)
def test_a_scheduler_WRITE_is_offered_the_substrate(command):
    offer = detect(command)
    assert offer is not None, command
    assert offer.scheduler
    assert offer.pattern in {"cron", "launchd", "systemd"}
    # The offer must NAME the supported path. A refusal that only says "no" gets worked around —
    # the model tries `at`, or writes the plist with `python -c`, and the user is no safer.
    assert "automation_create" in offer.observation


@pytest.mark.parametrize("command", READS)
def test_a_scheduler_READ_passes_silently(command):
    """Reads are diagnostic, and `crontab -l` is step one of MIGRATING jobs into the substrate.

    Intercepting them would make the migration path this seam recommends impossible.
    """
    assert detect(command) is None, command


@pytest.mark.parametrize("command", INNOCENT)
def test_ordinary_commands_never_prompt(command):
    assert detect(command) is None, command


def test_an_AMBIGUOUS_command_does_not_prompt():
    """`crontab -l > backup && crontab new` reads AND writes. The safe reading of an ambiguous
    command is the non-nagging one — see `INNOCENT` on why a false positive is the expensive
    failure. A user backing up before installing is the likeliest author of this line."""
    assert detect("crontab -l > backup.txt && crontab new.txt") is None


def test_it_is_ADVISORY_not_a_security_fence():
    """Fail-OPEN on junk. Its output is a prompt and a suggestion, so a crash here must not deny a
    legitimate command — the capability fence and PathGuard are the fail-CLOSED controls."""
    for junk in ("", None, 123, [], "\x00\x00"):
        assert detect(junk) is None  # type: ignore[arg-type]
    assert needs_prompt("crontab -e") is True
    assert needs_prompt("ls") is False


def test_the_hint_names_the_MIGRATION_path():
    """A user with existing crons must be told how to bring them in, not just that they should."""
    assert "crontab -l" in HANDOFF_HINT
    assert "automation_create" in HANDOFF_HINT


# ── both dispatch seams ──


def test_the_NATIVE_bash_tool_declines_with_the_offer(tmp_path):
    """Seam 1 of 2: the native agent's `bash` tool."""
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    provider = NativeBuiltinToolProvider(cwd=Path(tmp_path))

    result = asyncio.run(provider.invoke("bash", {"command": "crontab -e"}))
    assert result.success is False
    assert "outside PersonalClaw" in (result.error or "")
    assert any("automation_create" in hint for hint in (result.recovery_hints or []))


def test_the_native_bash_tool_still_RUNS_a_read(tmp_path):
    """…and the read is not intercepted: it reaches the real shell.

    Asserted on the ERROR TEXT rather than on `success`, because a machine with no crontab exits 1
    with "no crontab for <user>" — which is the shell answering, i.e. exactly the pass-through this
    test is for. A bare `assert result.success` would fail on such a machine and look like a bug in
    the seam.
    """
    from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

    provider = NativeBuiltinToolProvider(cwd=Path(tmp_path))
    result = asyncio.run(provider.invoke("bash", {"command": "crontab -l"}))
    assert "outside PersonalClaw" not in (result.error or "")


def test_the_ACP_hook_seam_denies_with_the_offer():
    """Seam 2 of 2: `hooks.on_tool_call`, the ACP path.

    A control on one of two dispatch seams is a control the other silently skips — the shape that
    left `web_watch` unscreened until S134.
    """
    from personalclaw.hooks import TOOL_DENY, HookManager

    manager = HookManager()
    denied = manager.on_tool_call("Running: crontab -e")
    assert denied.action == TOOL_DENY
    assert "automation_create" in (denied.reason or "")

    for allowed in ("Running: crontab -l", "Running: ls -la"):
        assert manager.on_tool_call(allowed).action != TOOL_DENY, allowed


def test_the_two_seams_AGREE():
    """One predicate, two callers. If they diverged, an agent would just use the other path."""
    from personalclaw.hooks import TOOL_DENY, HookManager

    manager = HookManager()
    for command in WRITES + READS + INNOCENT:
        hook_denied = manager.on_tool_call(f"Running: {command}").action == TOOL_DENY
        assert hook_denied == (detect(command) is not None), command
