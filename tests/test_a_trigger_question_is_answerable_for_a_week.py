"""A trigger's question is answerable for the window a workflow's resume token has, then closed.

When a trigger's action stops for a person, the trigger raises one question (`triggers/parks.py`),
and its token is what an Approve presents. That token lived for as long as nobody answered: a link
to it could run the action months after the question stopped meaning anything. It now lasts the
window `workflows.human_input.DEFAULT_RESUME_TTL_SECS` gives a run's own resume token — one number
for both — after which the answer is refused, the row closed, and the next stop asks afresh.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from personalclaw.action_providers.base import ActionResult
from personalclaw.triggers import parks
from personalclaw.workflows.human_input import DEFAULT_RESUME_TTL_SECS

TID = "clock:balance"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def rows(monkeypatch) -> dict[str, list[str]]:
    """The Inbox side, recorded rather than driven: which rows were raised and which closed."""
    seen: dict[str, list[str]] = {"raised": [], "closed": []}
    monkeypatch.setattr(
        parks, "_raise_row", lambda trigger, park, state=None: seen["raised"].append(park.token)
    )
    monkeypatch.setattr(
        parks, "close_row", lambda state, trigger_id: seen["closed"].append(trigger_id) or 1
    )
    return seen


def _stop() -> ActionResult:
    card = {"blocker": "Sign in to bank.example, then confirm"}
    return ActionResult(
        success=True, outcome="needs_input", stdout=json.dumps({"needs_input": card})
    )


def _trigger() -> SimpleNamespace:
    return SimpleNamespace(id=TID, name="Check my balance")


def _age(park: parks.TriggerPark, secs: float) -> None:
    park.created_at = time.time() - secs
    parks._write(park)


def test_the_window_is_the_one_a_runs_resume_token_has():
    assert parks.answerable_secs() == DEFAULT_RESUME_TTL_SECS


def test_a_question_inside_the_window_is_answered(home, rows):
    park = parks.raise_park(_trigger(), _stop())
    assert park is not None
    _age(park, DEFAULT_RESUME_TTL_SECS - 60)

    assert parks.claim(TID, park.token) is not None


def test_a_question_past_the_window_refuses_its_answer_and_closes_its_row(home, rows):
    """🔴 Red before: the token answered however long ago the question was asked."""
    park = parks.raise_park(_trigger(), _stop())
    assert park is not None
    _age(park, DEFAULT_RESUME_TTL_SECS + 60)

    assert parks.claim(TID, park.token) is None
    assert parks.load(TID) is None, "the question is gone, not left open"
    assert rows["closed"] == [TID]


def test_the_next_stop_asks_afresh_with_a_new_token(home, rows):
    """🔴 Red before: an open question was kept, token and all, however old it was."""
    first = parks.raise_park(_trigger(), _stop())
    assert first is not None
    _age(first, DEFAULT_RESUME_TTL_SECS + 60)

    second = parks.raise_park(_trigger(), _stop())

    assert second is not None and second.token != first.token
    assert rows["raised"] == [first.token, second.token]
    assert parks.claim(TID, first.token) is None
    assert parks.claim(TID, second.token) is not None


def test_a_question_that_says_nothing_of_when_it_was_asked_cannot_be_answered(home, rows):
    park = parks.raise_park(_trigger(), _stop())
    assert park is not None
    park.created_at = 0.0
    parks._write(park)

    assert parks.expired(park) is True
    assert parks.claim(TID, park.token) is None
