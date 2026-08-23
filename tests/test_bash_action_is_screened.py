"""A bash ACTION runs `/bin/sh -c` and was never screened; the interactive path always is.

`action_providers/bash_provider` executes `action_config["command"]` verbatim. `hooks.py:422`
and the native bash tool both call `security.is_sensitive_bash_command` first — an ACTION
reached neither. So `cat ~/.ssh/id_rsa` was refused when typed at the agent and executed when
carried in a cron.

**Why that is reachable, not theoretical.** `snapshot._merge_crons` appends an imported job
VERBATIM — no field is inspected — so restoring an archive installed whatever bash action it
held.
The same hole is open to the HTTP API, the UI, and any app that can create a trigger.

**Why the fix is at the executor, not at import.** Screening in `_merge_crons` would cover the one
creator that happened to be noticed. Screening where the command becomes a process covers
every creator, present and future, and matches what the interactive path already does.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from personalclaw.action_providers import bash_provider
from personalclaw.action_providers.base import ActionContext


def _ctx() -> ActionContext:
    return ActionContext(event="test", context={}, payload={})


def _run(command: str):
    prov = bash_provider.BashActionProvider()
    return asyncio.run(prov.execute({"command": command, "timeout": 5}, _ctx(), timeout=5))


@pytest.mark.parametrize(
    "command",
    [
        "cat ~/.ssh/id_rsa",
        "cat ~/.aws/credentials",
        "head -1 ~/.netrc",
        "cp ~/.gnupg/secring.gpg /tmp/x",
    ],
)
def test_a_credential_reading_action_is_refused(command):
    result = _run(command)
    assert result.success is False, f"{command!r} executed as an action"
    assert "sensitive" in (result.error or "").lower(), result.error


def test_an_ordinary_action_still_runs():
    """The direction that matters as much: an action provider that refuses ordinary work gets
    turned off, and then nothing is screened."""
    result = _run("echo hello")
    assert result.success is True, result.error


def test_a_missing_command_is_still_the_original_error():
    """The screening must not swallow the pre-existing validation."""
    result = _run("")
    assert result.success is False
    assert "command" in (result.error or "").lower()


def test_the_refusal_is_audited(monkeypatch):
    """A refused action must leave a SEL row — a control that fires invisibly cannot be reviewed."""
    logged: list[object] = []
    import personalclaw.sel as sel_mod

    class _Capture:
        def log(self, event):  # noqa: D102
            logged.append(event)

    monkeypatch.setattr(sel_mod, "SecurityEventLog", lambda *a, **k: _Capture())
    result = _run("cat ~/.ssh/id_rsa")
    assert result.success is False
    assert logged, "the refusal wrote no SEL row"
    ev = logged[0]
    assert getattr(ev, "outcome", "") == "denied"
    assert "bash" in str(getattr(ev, "metadata", {}))


def test_an_audit_fault_does_not_turn_a_refusal_into_a_run(monkeypatch):
    """Fail-open on AUDIT, never on the decision."""
    import personalclaw.sel as sel_mod

    def _boom(*a, **k):
        raise RuntimeError("sel down")

    monkeypatch.setattr(sel_mod, "SecurityEventLog", _boom)
    result = _run("cat ~/.ssh/id_rsa")
    assert result.success is False, "an audit failure must not let the command run"


def test_the_provider_actually_calls_the_screener():
    """The CALL SITE, by AST. A screened-looking provider that never calls the guard is the exact
    shape of the defect — `is_sensitive_bash_command` existed and this file did not call it."""
    src = pathlib.Path(bash_provider.__file__).read_text()
    tree = ast.parse(src)
    called = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    assert "is_sensitive_bash_command" in called, (
        "bash_provider no longer calls security.is_sensitive_bash_command — a bash action would "
        "run unscreened again, which is exactly the defect this file was written for."
    )


def test_the_action_path_inherits_whatever_the_screener_learns():
    """Composition, stated: this PR makes the action path CALL the guard; it does not define the
    guard's coverage. `grep`/`jq`/`awk` join the reader set in the separate path-evasion PR, and
    the action path gets them for free precisely because it calls the shared function rather than
    keeping its own list. That is why the cases above use commands the current screener blocks."""
    from personalclaw import security

    assert security.is_sensitive_bash_command("cat ~/.ssh/id_rsa")
    assert not security.is_sensitive_bash_command("echo hi")


def test_the_screener_is_shared_with_the_interactive_path():
    """Not a second copy of the rules.

    The provider must reach the SAME function `hooks.py` uses, so a pattern added for the
    interactive path protects actions too — and the guard cannot drift into two vocabularies.
    """
    hooks_src = pathlib.Path(
        pathlib.Path(bash_provider.__file__).parent.parent / "hooks.py"
    ).read_text()
    assert "is_sensitive_bash_command" in hooks_src, "the interactive path changed; re-derive this"


def test_an_imported_cron_cannot_smuggle_an_unscreened_command(tmp_path, monkeypatch):
    """The import vector, end to end in shape: `_merge_crons` does not inspect a job, so the
    protection has to hold at execution — which is what this asserts."""
    from personalclaw import snapshot

    src = tmp_path / "crons.json"
    dst = tmp_path / "live.json"
    src.write_text(
        '{"jobs": [{"name": "evil", "action": {"provider": "bash", '
        '"config": {"command": "cat ~/.ssh/id_rsa"}}}]}'
    )
    dst.write_text('{"jobs": []}')
    snapshot._merge_crons(src, dst)
    # The merge is deliberately permissive — assert that, so the reason the executor must screen
    # is recorded rather than assumed.
    assert (
        "cat ~/.ssh/id_rsa" in dst.read_text()
    ), "_merge_crons now inspects the job — good, but this test's premise changed: say so"
    # …and the command it imported is refused when it would actually run.
    assert _run("cat ~/.ssh/id_rsa").success is False
