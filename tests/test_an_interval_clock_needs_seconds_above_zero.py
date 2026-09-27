"""An interval clock whose seconds are not above zero is refused where it is written.

Arming reads `spec.interval_secs` through `_positive` and arms anything not above zero to 0.0, so
such a trigger sat enabled and never fired. Nothing refused one: the structural check requires no
`interval_secs` at all, and the semantic check covered cron expressions and skip dates only.
`personalclaw cron add --every -5` created one, and so did `POST /api/triggers` and a chat tool.
`cron add --every 0` was refused, but only because the handler read `0` as "no --every" and
answered "Provide --every or --cron". The parser owns that rule now (`cron add` takes exactly one
of the two), so the value is checked where every door checks a spec: `arm.semantic_spec_issues`.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from personalclaw.cli import build_parser
from personalclaw.cli_commands import _cron
from personalclaw.triggers import tools
from personalclaw.triggers.arm import semantic_spec_issues
from personalclaw.triggers.store import TriggerStore


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.cli_commands.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.cli_commands.sel", MagicMock())
    return tmp_path


def _errors(spec: dict) -> list:
    return [i for i in semantic_spec_issues("clock", spec) if i.severity == "error"]


@pytest.mark.parametrize("seconds", [0, -5, "soon"])
def test_seconds_that_are_not_above_zero_are_an_error(seconds):
    errors = _errors({"kind": "interval", "interval_secs": seconds})
    assert [e.path for e in errors] == ["spec.interval_secs"]
    assert f"{seconds!r} is not a number of seconds above 0" in errors[0].message
    assert "never fire" in errors[0].message


def test_missing_seconds_are_an_error():
    errors = _errors({"kind": "interval"})
    assert [e.path for e in errors] == ["spec.interval_secs"]
    assert "needs the seconds between fires" in errors[0].message


def test_an_interval_that_fires_is_not_flagged():
    assert _errors({"kind": "interval", "interval_secs": 3600}) == []


def test_create_refuses_it_before_the_row_exists(tmp_path):
    """The door `POST /api/triggers` and the chat tools write through."""
    store = TriggerStore(base_dir=tmp_path)
    result = tools.create(
        store,
        name="ops",
        kind="clock",
        spec={"kind": "interval", "interval_secs": 0},
        workflow={"inline": {"provider": "notify", "config": {"title_template": "check"}}},
        created_by="user",
        owner_consented=True,
    )
    assert result.ok is False
    assert "spec.interval_secs" in result.text
    assert store.load() == []


@pytest.mark.parametrize("every", ["0", "-5"])
def test_cron_add_refuses_it_on_stderr_and_creates_nothing(every, tmp_path, capsys):
    args = build_parser().parse_args(["cron", "add", "ops", "check", "--every", every, "--yes"])
    with pytest.raises(SystemExit) as exited:
        _cron(args)
    assert exited.value.code == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "spec.interval_secs" in err and "never fire" in err
    assert TriggerStore(base_dir=tmp_path).load() == []


def test_cron_update_refuses_it_and_keeps_the_interval(tmp_path, capsys):
    _cron(build_parser().parse_args(["cron", "add", "ops", "check", "--every", "300", "--yes"]))
    capsys.readouterr()
    with pytest.raises(SystemExit) as exited:
        _cron(build_parser().parse_args(["cron", "update", "clock:ops", "--every", "0", "--yes"]))
    assert exited.value.code == 1
    assert "spec.interval_secs" in capsys.readouterr().err
    (row,) = TriggerStore(base_dir=tmp_path).load()
    assert row.trigger.spec["interval_secs"] == 300
