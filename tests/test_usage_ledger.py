"""COST-AND-TOKEN-OBSERVABILITY §2.4 / C1 — the per-turn cost/token ledger.

Covers the store only (the write sites C2 + surfaces S2 are later atoms):
round-trip, the five rollup group keys, the tainted-total `priced` rule, the
fail-open write contract, and the inventory registration (audit_home passes WITH
the `usage` entry and fails WITHOUT it — both proven, per the done-when).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from personalclaw import usage_ledger as ul
from personalclaw.usage_ledger import TurnUsage


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    """Isolated config_dir so the ledger writes under tmp, never the real home."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _u(**over) -> TurnUsage:
    base = dict(
        ts="2026-08-06T12:00:00+00:00",
        session_key="s1",
        source="chat",
        agent="",
        provider="anthropic",
        model="claude-opus-5",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.5,
        priced=True,
    )
    base.update(over)
    return TurnUsage(**base)


def test_round_trip(_home):
    ul.record_turn(_u(input_tokens=100, output_tokens=20, cost_usd=0.5))
    rows = ul._iter_rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["model"] == "claude-opus-5" and r["input_tokens"] == 100
    assert r["cost_usd"] == 0.5 and r["priced"] is True


def test_storage_path_is_usage_turns_jsonl(_home):
    ul.record_turn(_u())
    assert ul._path() == _home / "usage" / "turns.jsonl"
    assert (_home / "usage" / "turns.jsonl").is_file()


def test_rollup_groups_by_each_key(_home):
    ul.record_turn(_u(model="m-a", source="chat", cost_usd=1.0))
    ul.record_turn(_u(model="m-a", source="loop", cost_usd=2.0))
    ul.record_turn(_u(model="m-b", source="chat", cost_usd=0.5))
    for key in ("model", "source", "agent", "provider", "day"):
        rows = ul.rollup(group_by=key)
        assert rows, f"rollup by {key} returned nothing"
        # Every group's cost is the sum of its members; the grand total is invariant.
        assert round(sum(r["cost_usd"] for r in rows), 6) == 3.5
    by_model = {r["model"]: r for r in ul.rollup(group_by="model")}
    assert by_model["m-a"]["cost_usd"] == 3.0 and by_model["m-a"]["turns"] == 2
    assert by_model["m-b"]["cost_usd"] == 0.5


def test_rollup_rejects_unknown_group_key(_home):
    with pytest.raises(ValueError, match="group_by must be one of"):
        ul.rollup(group_by="nonsense")


def test_unpriced_row_taints_group_and_total(_home):
    ul.record_turn(_u(model="m-a", cost_usd=1.0, priced=True))
    ul.record_turn(_u(model="m-a", cost_usd=0.0, priced=False))  # unpriced constituent
    grp = {r["model"]: r for r in ul.rollup(group_by="model")}["m-a"]
    assert grp["priced"] is False, "a group with any unpriced row must report priced=False"
    assert ul.totals()["priced"] is False


def test_priced_total_stays_true_when_all_priced(_home):
    ul.record_turn(_u(cost_usd=1.0, priced=True))
    ul.record_turn(_u(cost_usd=2.0, priced=True))
    t = ul.totals()
    assert t["priced"] is True and t["cost_usd"] == 3.0 and t["turns"] == 2


def test_rollup_window_filters_by_ts(_home):
    ul.record_turn(_u(ts="2026-08-01T00:00:00+00:00", cost_usd=1.0))
    ul.record_turn(_u(ts="2026-08-05T00:00:00+00:00", cost_usd=2.0))
    # [since, until) half-open window.
    rows = ul.rollup(since="2026-08-03T00:00:00+00:00", group_by="day")
    assert len(rows) == 1 and rows[0]["day"] == "2026-08-05"


def test_record_turn_is_fail_open(monkeypatch, _home):
    """A write failure degrades to a DEBUG log, never raises into the turn (§2.7)."""

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", _boom)
    # Must not raise despite the write blowing up.
    ul.record_turn(_u())


def test_iter_rows_tolerates_a_corrupt_line(_home):
    p = ul._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    ul.record_turn(_u(model="good"))
    with open(p, "a", encoding="utf-8") as f:
        f.write("{not valid json\n")
    ul.record_turn(_u(model="good2"))
    rows = ul._iter_rows()
    assert [r["model"] for r in rows] == ["good", "good2"]  # bad line skipped, rest kept


# ── inventory registration (done-when: audit passes WITH, fails WITHOUT) ──


def test_usage_path_is_registered_in_inventory():
    from personalclaw.durability.inventory import by_id

    e = by_id("usage_ledger")
    assert e is not None and e.path == "usage"
    assert e.derived is True and e.secret is False  # telemetry-of-self, disposable


def test_audit_home_passes_with_registration(_home):
    from personalclaw.durability.inventory import audit_home

    ul.record_turn(_u())  # creates usage/turns.jsonl under the home
    result = audit_home(_home)
    assert "usage/" not in result.unclaimed, f"usage should be claimed, got {result.unclaimed}"


def test_audit_home_fails_without_registration(_home, monkeypatch):
    """Prove the registration is load-bearing: drop the usage entry and the audit
    reports usage/ as unclaimed."""
    import personalclaw.durability.inventory as inv

    ul.record_turn(_u())
    stripped = tuple(e for e in inv.INVENTORY if e.id != "usage_ledger")
    monkeypatch.setattr(inv, "INVENTORY", stripped)
    result = inv.audit_home(_home)
    assert "usage/" in result.unclaimed


# ── CATO-2: the chat write-site (_record_turn_usage) ──────────────────────────


class TestChatWriteSite:
    """`_record_turn_usage` lands exactly one row with the right priced/cost logic."""

    def _event(self, **over):
        base = dict(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd=0.0,
            duration_ms=1234,
        )
        base.update(over)
        return SimpleNamespace(**base)

    def _call(self, event, model="claude-opus-4.5"):
        from personalclaw.dashboard.chat_runner import _record_turn_usage

        _record_turn_usage(
            event,
            session_key="dashboard:s1",
            source="chat",
            agent="",
            provider="anthropic",
            model=model,
        )

    def test_one_row_with_provider_reported_cost_is_priced(self, _home):
        self._call(self._event(cost_usd=0.42))
        rows = ul._iter_rows()
        assert len(rows) == 1
        r = rows[0]
        assert r["source"] == "chat" and r["provider"] == "anthropic"
        assert r["model"] == "claude-opus-4.5" and r["input_tokens"] == 100
        assert r["cost_usd"] == 0.42 and r["priced"] is True

    def test_priced_model_with_zero_cost_still_priced(self, _home):
        # No provider cost, but the model HAS a price row → priced=True (cost may be
        # a real estimate the caller already set, or 0.0 for a tiny turn).
        self._call(self._event(cost_usd=0.0), model="claude-opus-4.5")
        r = ul._iter_rows()[0]
        assert r["priced"] is True

    def test_unpriced_model_writes_priced_false_and_zero(self, _home):
        self._call(self._event(cost_usd=0.0), model="some-unknown-local-model")
        r = ul._iter_rows()[0]
        assert r["priced"] is False and r["cost_usd"] == 0.0

    def test_write_site_is_fail_open(self, monkeypatch, _home):
        """A ledger write failure must not raise into the turn."""
        import personalclaw.usage_ledger as _ul

        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(_ul, "_path", _boom)
        self._call(self._event(cost_usd=0.1))  # must not raise


# ── CATO-3: the subagent write-site (SubagentManager._record_subagent_usage) ──


class TestSubagentWriteSite:
    """A completed subagent turn lands one row keyed to the PARENT session, so a
    fan-out's cost is attributable per child (source='subagent')."""

    def _info(self, **over):
        from personalclaw.subagent import SubagentInfo

        base = dict(id="a1", task="do a thing", parent_session_key="dashboard:parent")
        base.update(over)
        return SubagentInfo(**base)

    def _event(self, **over):
        base = dict(
            input_tokens=80,
            output_tokens=15,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd=0.0,
            duration_ms=900,
        )
        base.update(over)
        return SimpleNamespace(**base)

    def test_one_row_keyed_to_parent_session(self, _home):
        from personalclaw.subagent import SubagentManager

        info = self._info(agent="researcher", model="claude-opus-4.5")
        SubagentManager._record_subagent_usage(info, "subagent:a1", self._event(cost_usd=0.3))
        rows = ul._iter_rows()
        assert len(rows) == 1
        r = rows[0]
        assert r["source"] == "subagent" and r["provider"] == "acp"
        assert r["session_key"] == "dashboard:parent"  # parent, not the child session
        assert r["agent"] == "researcher" and r["cost_usd"] == 0.3 and r["priced"] is True

    def test_fanout_of_three_yields_three_rows(self, _home):
        from personalclaw.subagent import SubagentManager

        for i in range(3):
            info = self._info(id=f"a{i}", model="claude-opus-4.5")
            SubagentManager._record_subagent_usage(
                info, f"subagent:a{i}", self._event(cost_usd=0.1)
            )
        rows = ul._iter_rows()
        assert len(rows) == 3
        assert all(r["source"] == "subagent" for r in rows)
        assert round(ul.totals()["cost_usd"], 6) == 0.3

    def test_unpriced_subagent_model_is_priced_false(self, _home):
        from personalclaw.subagent import SubagentManager

        info = self._info(model="some-unknown-local-model")
        SubagentManager._record_subagent_usage(info, "subagent:a1", self._event(cost_usd=0.0))
        r = ul._iter_rows()[0]
        assert r["priced"] is False and r["cost_usd"] == 0.0

    def test_subagent_info_carries_tokens_after_capture(self):
        """SubagentInfo gained the token/cost fields (default 0.0) for delivery."""
        info = self._info()
        assert info.input_tokens == 0 and info.output_tokens == 0 and info.cost_usd == 0.0


# ── CATO-4: the shared record_from_event seam + the non-_run_chat sources ──────


class TestRecordFromEvent:
    """The shared seam every write-site delegates to (chat/subagent/background/
    channel/cron/cli), and the done-when: each source appears in rollup(by source)."""

    def _event(self, **over):
        base = dict(
            input_tokens=50,
            output_tokens=10,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            cost_usd=0.0,
            duration_ms=500,
        )
        base.update(over)
        return SimpleNamespace(**base)

    def test_vendor_cost_wins_and_is_priced(self, _home):
        ul.record_from_event(self._event(cost_usd=0.7), source="cli", model="claude-opus-4.5")
        r = ul._iter_rows()[0]
        assert r["source"] == "cli" and r["cost_usd"] == 0.7 and r["priced"] is True

    def test_cost_estimated_when_provider_reports_none(self, _home):
        # A priced model with tokens but no provider cost → estimate_cost fills it.
        ul.record_from_event(
            self._event(cost_usd=0.0, input_tokens=1_000_000),
            source="background",
            model="claude-opus-4.5",
        )
        r = ul._iter_rows()[0]
        assert r["priced"] is True and r["cost_usd"] > 0

    def test_unpriced_model_is_honest_zero(self, _home):
        ul.record_from_event(self._event(cost_usd=0.0), source="cron", model="unknown-local")
        r = ul._iter_rows()[0]
        assert r["priced"] is False and r["cost_usd"] == 0.0

    def test_each_source_appears_in_rollup_by_source(self, _home):
        """CATO-4 done-when: every write-site's source string is a distinct group."""
        for src in ("chat", "subagent", "background", "channel", "cron", "cli"):
            ul.record_from_event(self._event(cost_usd=0.1), source=src, model="claude-opus-4.5")
        by_source = {r["source"] for r in ul.rollup(group_by="source")}
        assert {"chat", "subagent", "background", "channel", "cron", "cli"} <= by_source

    def test_fail_open_on_broken_ledger(self, monkeypatch, _home):
        monkeypatch.setattr(ul, "_path", lambda: (_ for _ in ()).throw(OSError("boom")))
        ul.record_from_event(self._event(), source="cli", model="m")  # must not raise


class TestStreamAndCollectOnComplete:
    """`stream_and_collect(on_complete=...)` fires the callback with the terminal
    event — the seam the background/channel/cron write-sites hang the ledger on."""

    def test_on_complete_fires_with_the_complete_event(self):
        import asyncio

        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
        from personalclaw.llm_helpers import stream_and_collect

        class _Provider:
            async def stream(self, _message):
                yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="hello ")
                yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="world")
                yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=42, output_tokens=7, cost_usd=0.05)

        seen = {}

        def _cb(event):
            seen["input"] = event.input_tokens
            seen["cost"] = event.cost_usd

        text = asyncio.run(stream_and_collect(_Provider(), "hi", on_complete=_cb))
        assert text == "hello world"
        assert seen == {"input": 42, "cost": 0.05}

    def test_on_complete_none_is_byte_identical_text(self):
        import asyncio

        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
        from personalclaw.llm_helpers import stream_and_collect

        class _Provider:
            async def stream(self, _message):
                yield LLMEvent(kind=EVENT_TEXT_CHUNK, text="abc")
                yield LLMEvent(kind=EVENT_COMPLETE)

        assert asyncio.run(stream_and_collect(_Provider(), "hi")) == "abc"


# ── CATO-6: the "Turn complete" cost line composer ────────────────────────────


class TestTurnCompleteLine:
    """`_turn_complete_line` shows real USD + tokens, honest 'unpriced', and the
    cache fragment only when cache tokens are non-zero."""

    def _line(self, **over):
        from personalclaw.dashboard.chat_runner import _turn_complete_line

        base = dict(
            events=3,
            tool_calls=1,
            context_pct=42.0,
            input_tokens=1200,
            output_tokens=340,
            cache_tokens=0,
            cost_usd=0.0123,
            priced=True,
        )
        base.update(over)
        return _turn_complete_line(**base)

    def test_priced_turn_shows_usd_and_tokens(self):
        line = self._line()
        assert "Turn complete: 3 events, 1 tool calls, context 42%" in line
        assert "$0.0123" in line
        assert "1,200 in / 340 out tokens" in line

    def test_unpriced_never_renders_dollar_zero(self):
        line = self._line(cost_usd=0.0, priced=False)
        assert "unpriced" in line
        assert "$0.00" not in line and "$" not in line

    def test_cache_fragment_only_when_nonzero(self):
        assert "cached" not in self._line(cache_tokens=0)
        assert "2,000 cached" in self._line(cache_tokens=2000)

    def test_no_tokens_is_backward_compatible_bare_line(self):
        # A turn with no token counts renders exactly the pre-CATO-6 line.
        line = self._line(input_tokens=0, output_tokens=0, cost_usd=0.0, priced=False)
        assert line == "Turn complete: 3 events, 1 tool calls, context 42%"

    def test_priced_zero_cost_still_shows_dollar_not_unpriced(self):
        # A priced model whose tiny turn rounds to $0.0000 is still PRICED — show the
        # dollar amount, not "unpriced" (which means "no price row").
        line = self._line(cost_usd=0.0, priced=True)
        assert "$0.0000" in line and "unpriced" not in line
