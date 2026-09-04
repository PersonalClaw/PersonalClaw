"""Dismiss-all records the same engagement signal per-item dismiss records (issue 619).

The inbox has six interaction paths that flip user intent into state; five of them fed the
engagement ranker and the bulk one — the strongest topic-rejection gesture — recorded
nothing. These rails pin the parity:

- dismiss-all records a "dismiss" signal against every swept item's topic keys, and saves
  the store exactly ONCE for the whole sweep (the bulk path must not cost N writes);
- the gate still holds — ranking disabled (the default) records nothing;
- per-item dismiss and dismiss-all produce identical signals for identical items, so the
  two paths cannot drift apart again.

Harness matches tests/test_inbox_draft_gate.py: a MagicMock request over a SimpleNamespace
state; real InboxStore/InboxState on tmp paths; the engagement store is a spy injected at
state._engagement_store (the lazy getter returns a cached instance), with the config gate
patched at _inbox_config.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from personalclaw.dashboard.handlers_inbox import api_inbox_dismiss_all
from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemStatus


class _SpyStore:
    """Engagement-store spy: captures record() calls, counts save()s."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []
        self.saves = 0

    def record(self, topic_key: str, signal: str, now: float = 0.0) -> None:
        self.records.append((topic_key, signal))

    def save(self) -> None:
        self.saves += 1


def _item(i: int, status: str = ItemStatus.PENDING) -> InboxItem:
    return InboxItem(
        id=f"chan{i}_{i}.000",
        channel=f"chan{i}",
        channel_name=f"Chan {i}",
        thread_ts=None,
        message=f"m{i}",
        sender_id=f"sender{i}",
        sender_name=f"Sender {i}",
        classification="fyi",
        status=status,
        created_at=float(i),
    )


def _state(tmp_path, items: list[InboxItem]):
    inbox = InboxStore(path=tmp_path / "items.json")
    for it in items:
        inbox.add(it)
    inbox_state = InboxState(path=tmp_path / "state.json")
    svc = SimpleNamespace(state=inbox_state, inbox=inbox)
    state = SimpleNamespace(_inbox_svc=svc)
    return state, inbox


def _request(state):
    req = MagicMock()
    req.app = {"state": state}
    return req


@pytest.mark.asyncio
async def test_dismiss_all_records_a_dismiss_signal_per_open_item(tmp_path):
    # Two open items (one PENDING, one SEEN — dismiss-all sweeps both), one already dismissed.
    items = [_item(1), _item(2, status=ItemStatus.SEEN), _item(3, status=ItemStatus.DISMISSED)]
    state, inbox = _state(tmp_path, items)
    spy = _SpyStore()
    state._engagement_store = spy

    with patch(
        "personalclaw.dashboard.handlers_inbox._inbox_config",
        return_value=SimpleNamespace(engagement_ranking_enabled=True, engagement_half_life_days=0),
    ):
        resp = await api_inbox_dismiss_all(_request(state))

    assert resp.status == 200
    # Every swept item contributed all three of its topic keys; the pre-dismissed one did not.
    expect = {
        ("ch:chan1", "dismiss"),
        ("snd:sender1", "dismiss"),
        ("cls:fyi", "dismiss"),
        ("ch:chan2", "dismiss"),
        ("snd:sender2", "dismiss"),
    }
    assert expect.issubset(set(spy.records))
    assert not any("chan3" in tk for tk, _ in spy.records)
    # The sweep is ONE store write, however many items it covered.
    assert spy.saves == 1
    # And the state transition itself still happened.
    assert inbox.items["chan1_1.000"].status == ItemStatus.DISMISSED


@pytest.mark.asyncio
async def test_dismiss_all_records_nothing_when_ranking_disabled(tmp_path):
    # The default-off gate holds for the bulk path exactly as it does per-item: no opt-in,
    # no accrued state.
    state, _ = _state(tmp_path, [_item(1)])
    spy = _SpyStore()
    state._engagement_store = spy

    with patch(
        "personalclaw.dashboard.handlers_inbox._inbox_config",
        return_value=SimpleNamespace(engagement_ranking_enabled=False, engagement_half_life_days=0),
    ):
        resp = await api_inbox_dismiss_all(_request(state))

    assert resp.status == 200
    assert spy.records == []
    assert spy.saves == 0


@pytest.mark.asyncio
async def test_bulk_and_per_item_dismiss_record_identical_signals(tmp_path):
    # Parity pin: the same item dismissed through either path trains the ranker identically,
    # so the two call sites cannot drift apart again.
    from personalclaw.dashboard.handlers_inbox import _record_signal, _record_signals

    item = _item(7)
    state = SimpleNamespace()
    enabled = SimpleNamespace(engagement_ranking_enabled=True, engagement_half_life_days=0)

    with patch("personalclaw.dashboard.handlers_inbox._inbox_config", return_value=enabled):
        spy_single = _SpyStore()
        state._engagement_store = spy_single
        _record_signal(state, item, "dismiss")

        spy_bulk = _SpyStore()
        state._engagement_store = spy_bulk
        _record_signals(state, [item], "dismiss")

    assert spy_single.records == spy_bulk.records
    assert spy_single.saves == spy_bulk.saves == 1
