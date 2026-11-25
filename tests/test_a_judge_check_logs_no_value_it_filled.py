"""A judge's check is logged and shown by what its definition writes, never as it ran.

A check's command can read a value the run fills in (a secret, an input); the run fills it only into
what it runs (``BindingContext.filled``), and the judge reads the command as written. 🔴 Before,
a runner that raised was logged with the command as it ran when the check wrote none of its own,
and with a traceback that can hold that command: each value the run filled in, in clear text in
the log. Now the log names the check by its label and the error by its kind, and a check that
writes no command of its own is shown by its label.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from personalclaw.workflows import judge_checks
from personalclaw.workflows.judge_checks import Check

#: A value the run fills in from its secrets: a placeholder, in no real token's format.
FILLED = "placeholder-deploy-value-0000"


class _Chosen:
    """Every input as its owner chose it: no refusal."""

    def refusal(self, text: object, what: str) -> str:
        return ""


def _printed(check: Check, monkeypatch, *, raised: str) -> str:
    """What the judge reads of *check*, run where the run fills :data:`FILLED` into its command,
    by a runner that raises *raised* naming the command as it ran."""

    calls: list[object] = []

    def resolve(text: object, ctx: object) -> str:
        # The command is resolved first, then the folder: only the command is filled.
        calls.append(text)
        return f"deploy --token {FILLED}" if len(calls) == 1 else str(text)

    async def runner(command: str, cwd: str, **_kw: object) -> bool:
        raise RuntimeError(raised.format(command=command))

    monkeypatch.setattr(judge_checks, "resolve", resolve)
    monkeypatch.setattr("personalclaw.loop.gates.run_verify_command", runner)
    monkeypatch.setattr("personalclaw.loop.gates.refusal", lambda command, cwd: "")
    ctx = SimpleNamespace(filled=[FILLED])
    return asyncio.run(judge_checks._printed(check, ctx, "/work/folder", _Chosen()))


@pytest.mark.parametrize(
    "command",
    ["", "deploy --token {{inputs.token}}"],
    ids=["no command of its own", "a command its run fills"],
)
def test_a_check_whose_runner_raised_logs_no_value_the_run_filled(
    command: str, monkeypatch, caplog
) -> None:
    check = Check(command=command, label="deploy")
    with caplog.at_level(logging.DEBUG, logger=judge_checks.logger.name):
        said = _printed(check, monkeypatch, raised="could not start {command}")
    logged = [r for r in caplog.records if r.name == judge_checks.logger.name]
    assert logged, "positive control: the raise is logged"
    assert any("'deploy'" in r.getMessage() for r in logged), [r.getMessage() for r in logged]
    assert FILLED not in caplog.text, caplog.text
    assert all(r.exc_info is None for r in logged), "a traceback can print the command as it ran"
    assert FILLED not in said, said
    assert "did not run" in said


def test_a_check_with_no_command_of_its_own_is_shown_by_its_label(monkeypatch) -> None:
    said = _printed(Check(command="  ", label="deploy"), monkeypatch, raised="boom")
    assert said.startswith("`deploy` in /work/folder: did not run"), said
