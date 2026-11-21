"""An unreadable model-watchdog record revokes every standing autonomy grant.

The model watchdog revokes every standing autonomy grant when the models its evidence was taken
under change, and knows the last bindings from its record, ``evals/model_watchdog.json``. A record
that was there but could not be read was read as no record: the tick recorded the current bindings
as a first run's baseline over it and revoked nothing. A rebind made between the last tick that read
it and the first after it was damaged never revoked a grant, and the old bindings, the one record of
what changed, were written over.

Now an unreadable record is a rebind the watchdog cannot see. Every grant is revoked, every watched
use case's re-benchmark is queued, the one digest names the record and the copy kept of it, and the
next tick is ordinary. A missing or empty record is still a first run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from personalclaw.evals import model_watchdog as watchdog

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    (home / "config.json").write_text(
        json.dumps({"providers": [{"name": n} for n in ("A", "B")]}) + "\n", encoding="utf-8"
    )
    (home / "active_models.json").write_text(json.dumps({"chat": ["A:1"]}), encoding="utf-8")
    return home


@pytest.fixture()
def revoked(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(
        "personalclaw.guardrails.ladder.revoke_granted_scopes",
        lambda **kw: calls.append(kw) or [],
    )
    return calls


class _Notifier:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, kind, title, body, **kwargs):
        self.calls.append((kind, title, body))


def _record(text: str) -> None:
    path = watchdog.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("damaged", ["{not json", "[1, 2, 3]"])
def test_an_unreadable_record_revokes_every_grant_and_says_so(home, revoked, damaged):
    """🔴 Red before: it was read as a first run, and nothing was revoked."""
    _record(damaged)
    notifier = _Notifier()
    result = watchdog.check(now=NOW, notifier=notifier)

    assert result.reason == "state_unreadable" and result.changed is True
    assert [call["evidence_id"] for call in revoked] == ["model_watchdog:unreadable"]
    assert revoked[0]["cause"] == watchdog.UNREADABLE_CAUSE
    assert result.queued and watchdog.load_queue() == result.queued
    ((_kind, title, body),) = notifier.calls
    assert "unreadable" in title and "model_watchdog.json" in body


def test_the_damaged_record_is_kept_and_the_next_tick_is_ordinary(home, revoked):
    """🔴 Red before: the damaged record was written over, and no copy of it was kept."""
    _record("{not json")
    watchdog.check(now=NOW, notifier=None)
    kept = list(watchdog.state_path().parent.glob("model_watchdog.json.broken*"))
    assert [path.read_text(encoding="utf-8") for path in kept] == ["{not json"]

    again = watchdog.check(now=NOW, notifier=None)
    assert again.reason == "no_change" and len(revoked) == 1


@pytest.mark.parametrize("record", [None, ""])
def test_a_missing_or_empty_record_is_still_a_first_run(home, revoked, record):
    if record is not None:
        _record(record)
    result = watchdog.check(now=NOW, notifier=_Notifier())
    assert result.reason == "baseline_recorded" and revoked == []
