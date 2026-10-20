"""Surfaces that change availability together are announced in ONE notification.

Most model-backed surfaces need the Chat model, and Background and Reasoning resolve through it,
so clearing Chat took ten surfaces down in one evaluation and posted ten "Choose a model for
<surface>" notifications, one after another; choosing a model again posted ten "is ready" ones.
Now one kind of change is one notice, counting the surfaces in its title and naming each in its
body. A single surface keeps its own words (``test_resilience_degraded.py``), and the real
built-in set with a real registry is ``test_a_degraded_notice_says_what_is_wrong.py``.
"""

from __future__ import annotations

import pytest

from personalclaw.resilience import degraded
from personalclaw.resilience.degraded import DegradedContract


@pytest.fixture(autouse=True)
def _only_these_contracts():
    """A registry of just the contracts each test registers; the built-ins come back after."""
    saved = dict(degraded._CONTRACTS)
    degraded._CONTRACTS.clear()
    degraded.reset_transition_state()
    yield
    degraded._CONTRACTS.clear()
    degraded._CONTRACTS.update(saved)
    degraded.reset_transition_state()


class _Bell:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str, str]] = []

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append((kind, title, body))


@pytest.fixture
def models(monkeypatch):
    """Per use case: whether a model resolves (``serves``) and whether one is chosen."""
    state = {"serves": {}, "chosen": {}}
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case",
        lambda uc: state["serves"].get(uc, True),
    )
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.model_chosen",
        lambda uc: state["chosen"].get(uc, True),
    )
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.use_case_problem",
        lambda uc: ("the key was rejected", "update it in Settings → Providers"),
    )
    return state


def _surface(name: str, use_case: str, **extra) -> None:
    degraded.register_contract(
        DegradedContract(
            surface=name, label=name.title(), use_cases=(use_case,), floor=f"{name} floor", **extra
        )
    )


def test_three_surfaces_down_at_once_are_one_notice_and_back_at_once_one_more(models):
    """🔴 Red on main: three "<surface> degraded" warnings, then three "recovered" notices."""
    for name in ("alpha", "beta", "gamma"):
        _surface(name, "chat")
    bell = _Bell()
    degraded.evaluate(notify=True, state=bell)  # the silent baseline: all up
    models["serves"]["chat"] = False
    degraded.evaluate(notify=True, state=bell)
    models["serves"]["chat"] = True
    degraded.evaluate(notify=True, state=bell)

    assert bell.notes == [
        (
            "warning",
            "3 surfaces degraded",
            "The key was rejected. Update it in Settings → Providers. Until then Alpha, Beta "
            "and Gamma do only what works without a model.",
        ),
        (
            "info",
            "3 surfaces recovered",
            "A model is available again for Chat, so Alpha, Beta and Gamma are back.",
        ),
    ]


def test_two_kinds_of_change_in_one_evaluation_are_one_notice_each(models):
    """A decline and a model nobody chose are different things to do, so they are two notices,
    each covering the surfaces of its kind."""
    _surface("alpha", "chat")
    _surface("beta", "chat")
    _surface("gamma", "stt")
    bell = _Bell()
    degraded.evaluate(notify=True, state=bell)
    models["serves"].update(chat=False, stt=False)
    models["chosen"]["stt"] = False
    degraded.evaluate(notify=True, state=bell)

    assert [(kind, title) for kind, title, _body in bell.notes] == [
        ("warning", "2 surfaces degraded"),
        ("info", "Choose a model for Gamma"),
    ]
    assert bell.notes[1][2] == "No model chosen for Speech-to-text. gamma floor"


def test_each_surface_in_a_recovery_says_what_it_re_enriched(models):
    """The drained/backlog clause of a one-surface recovery, per surface in a grouped one."""

    async def _drained(state=None) -> int:
        return 4

    _surface("alpha", "chat", backlog_probe=lambda: 9, drain=_drained)
    _surface("beta", "chat", backlog_probe=lambda: 2)
    _surface("gamma", "chat")
    bell = _Bell()
    degraded.evaluate(notify=True, state=bell)
    models["serves"]["chat"] = False
    degraded.evaluate(notify=True, state=bell)
    models["serves"]["chat"] = True
    degraded.evaluate(notify=True, state=bell)

    assert bell.notes[-1] == (
        "info",
        "3 surfaces recovered",
        "A model is available again for Chat, so Alpha, Beta and Gamma are back · Alpha: 4 "
        "item(s) re-enriched · Beta: 2 item(s) awaiting re-enrichment.",
    )


def test_a_recovery_names_only_the_surfaces_a_notice_said_were_down(models):
    """A surface first seen down (a fresh home's baseline) was never announced, so its coming up
    is setup, not a recovery; it is left out of the recovery notice of the others."""
    _surface("alpha", "chat")
    _surface("beta", "chat")
    _surface("gamma", "stt")
    models["serves"]["stt"] = False  # gamma's first sight: down, silently
    bell = _Bell()
    degraded.evaluate(notify=True, state=bell)
    models["serves"]["chat"] = False
    degraded.evaluate(notify=True, state=bell)
    models["serves"].update(chat=True, stt=True)
    degraded.evaluate(notify=True, state=bell)

    assert [(kind, title) for kind, title, _body in bell.notes] == [
        ("warning", "2 surfaces degraded"),
        ("info", "2 surfaces recovered"),
    ]
    assert "Gamma" not in bell.notes[1][2]


def test_a_notice_that_could_not_be_posted_announces_no_recovery(models):
    """A down notice the bell refused was never seen, so the recovery after it is not announced
    (as for one surface) — and the evaluation itself never raises."""
    _surface("alpha", "chat")
    _surface("beta", "chat")

    class _Broken(_Bell):
        def notify(self, kind, title, body, *, meta=None):
            if "degraded" in title:
                raise RuntimeError("the bell is down")
            super().notify(kind, title, body, meta=meta)

    bell = _Broken()
    degraded.evaluate(notify=True, state=bell)
    models["serves"]["chat"] = False
    degraded.evaluate(notify=True, state=bell)
    models["serves"]["chat"] = True
    degraded.evaluate(notify=True, state=bell)

    assert bell.notes == []
