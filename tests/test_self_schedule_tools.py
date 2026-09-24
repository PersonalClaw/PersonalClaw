"""`WF2LOO-9` — an agent may schedule itself, under a bound the operator controls.

Two things were missing. `automation_create` could already build either shape, but an agent
scheduling ITSELF is a different act from an agent building the user an automation, and only the
tool name makes that difference legible in the tool log and the approval prompt. And the one bound
standing between a self-scheduling agent and an unbounded fan-out of clocks was a **module
constant** — `MAX_AGENT_TRIGGERS = 20` — so an operator who wanted 5, or 0, had to edit the source.

Both tools route through `triggers.tools.create(created_by="agent")` rather than reaching the store,
which is why they inherit the bound, the command screening and the on-creation announcement instead
of re-implementing any of them. These tests assert that routing, because a tool that wrote directly
would look identical until the day it mattered.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.triggers import tools as T


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A real TriggerStore over tmp_path — never the operator's home."""
    from personalclaw.triggers.store import TriggerStore

    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path, raising=False)
    return TriggerStore(tmp_path / "triggers.json")


def _cap(monkeypatch, value: int) -> None:
    """Point the bound at *value* without writing a config file."""
    monkeypatch.setattr(T, "max_agent_triggers", lambda: value)


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    """Stub the cadence→cron bridge so the DISPATCHER path is testable without a provider.

    `T.create` takes `cadence_to_cron` injected for exactly this reason, but the MCP dispatcher
    does not thread it — nor should it; natural language is the tools' whole interface. So the
    module-level default is stubbed instead, which keeps the real dispatcher in the test rather
    than bypassing it to call `T.create` directly.
    """
    monkeypatch.setattr(
        T, "_default_cadence_to_cron", lambda cadence: ("0 9 * * 1-5", ""), raising=True
    )


class TestTheBoundIsConfigurable:
    def test_the_default_is_the_historical_twenty(self):
        """The clean break must not change behaviour for anyone who set nothing."""
        from personalclaw.config.loader import AppConfig

        assert AppConfig.load().workflows.self_schedule_max_outstanding == 20
        assert T.DEFAULT_MAX_AGENT_TRIGGERS == 20

    def test_an_unreadable_config_keeps_the_cap_rather_than_removing_it(self, monkeypatch):
        """The fail-safe direction. An unreadable config must not silently uncap a self-scheduling
        agent — and must not read as 0 either, which would look like the operator disabled the
        feature when they had not."""
        import personalclaw.config.loader as loader

        def _boom(*a, **k):
            raise RuntimeError("config unreadable")

        monkeypatch.setattr(loader.AppConfig, "load", _boom)
        assert T.max_agent_triggers() == T.DEFAULT_MAX_AGENT_TRIGGERS == 20

    def test_the_bound_is_read_per_call_not_captured_at_import(self, monkeypatch):
        """A value captured at import cannot be changed by a PATCH without a restart — the same
        defect the mcp.json resolvers had."""
        import personalclaw.config.loader as loader

        seen: list[int] = []

        class _Cfg:
            class workflows:  # noqa: N801
                self_schedule_max_outstanding = 7

        def _load(*a, **k):
            seen.append(1)
            return _Cfg

        monkeypatch.setattr(loader.AppConfig, "load", _load)
        assert T.max_agent_triggers() == 7
        assert T.max_agent_triggers() == 7
        assert len(seen) == 2, "the config was consulted once and cached — a PATCH would not apply"

    def test_the_config_field_round_trips(self):
        """The repo's config contract: dataclass + _meta, load(), to_dict(), PATCH allowlist."""
        from personalclaw.config.loader import AppConfig
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        cfg = AppConfig.load()
        assert "self_schedule_max_outstanding" in cfg.to_dict()["workflows"]
        spec = _EDITABLE_CONFIG.get("workflows.self_schedule_max_outstanding")
        assert spec, "the field is not PATCH-editable, so no UI or API can change it"
        assert spec["type"] == "int" and spec["min"] == 0, spec

    def test_the_field_carries_a_label_and_help(self):
        """A knob with no `_meta` cannot be rendered, so it is a knob only a source-reader has."""
        import dataclasses

        from personalclaw.config.loader import WorkflowsConfig

        f = next(
            f
            for f in dataclasses.fields(WorkflowsConfig)
            if f.name == "self_schedule_max_outstanding"
        )
        assert f.metadata.get("label"), f.metadata
        assert f.metadata.get("help"), f.metadata


class TestTheToolsHonourTheBound:
    def test_a_onetime_task_is_created(self, store, monkeypatch):
        _cap(monkeypatch, 5)
        out = _call(
            "set_onetime_task",
            {"name": "check build", "when": "in 20 minutes", "message": "check the build"},
            store,
        )
        assert out["ok"] is True, out
        rows = store.load()
        assert len(rows) == 1
        assert rows[0].trigger.created_by == "agent"

    def test_a_recurring_task_is_created_from_a_cadence(self, store, monkeypatch):
        _cap(monkeypatch, 5)
        out = _call(
            "set_recurring_task",
            {"name": "daily sweep", "cadence": "every weekday at 9", "message": "sweep"},
            store,
        )
        assert out["ok"] is True, out
        assert len(store.load()) == 1

    def test_the_bound_refuses_the_next_task_and_names_the_number(self, store, monkeypatch):
        """The refusal must carry the count and the cap: "limit reached" with no number leaves the
        user unable to tell what to pause."""
        _cap(monkeypatch, 2)
        for i in range(2):
            assert (
                _call(
                    "set_onetime_task",
                    {"name": f"t{i}", "when": "in 1 hour", "message": "x"},
                    store,
                )["ok"]
                is True
            )
        blocked = _call(
            "set_onetime_task", {"name": "t3", "when": "in 1 hour", "message": "x"}, store
        )
        assert blocked["ok"] is False
        assert "2" in blocked["text"], blocked["text"]
        assert len(store.load()) == 2, "a refused task must not be written"

    def test_a_cap_of_zero_turns_self_scheduling_off(self, store, monkeypatch):
        """0 is a legitimate operator choice, and the reason the fallback cannot be 0."""
        _cap(monkeypatch, 0)
        out = _call("set_onetime_task", {"name": "t", "when": "in 1 hour", "message": "x"}, store)
        assert out["ok"] is False
        assert store.load() == []

    def test_pausing_frees_a_slot_without_deleting_history(self, store, monkeypatch):
        """The cap counts ENABLED rows, so the recovery path is pause — not delete."""
        _cap(monkeypatch, 1)
        first = _call("set_onetime_task", {"name": "a", "when": "in 1 hour", "message": "x"}, store)
        assert first["ok"] is True
        assert (
            _call("set_onetime_task", {"name": "b", "when": "in 1 hour", "message": "x"}, store)[
                "ok"
            ]
            is False
        )
        tid = store.load()[0].trigger.id
        T.set_paused(store, trigger_id=tid, paused=True)
        assert (
            _call("set_onetime_task", {"name": "b", "when": "in 1 hour", "message": "x"}, store)[
                "ok"
            ]
            is True
        )
        assert len(store.load()) == 2, "the paused row is still there"


class TestTheToolsAreWiredWhereAgentsLook:
    def test_both_tools_are_declared(self):
        import personalclaw.mcp_automation as M

        names = {t["name"] for t in M._list_tools()}
        assert {"set_onetime_task", "set_recurring_task"} <= names, sorted(names)

    def test_every_declared_name_actually_dispatches(self, store):
        """A declared tool with no branch reports "unknown tool" at the worst possible moment.

        Asserted against the DISPATCHER rather than `triggers.tools.TOOL_NAMES`: that tuple is
        §4's table of `automation_*` handlers living in that module, and these two live in
        `mcp_automation` and delegate into it. Adding them to TOOL_NAMES broke two of its own
        rails — the tuple means "handlers here", not "names an agent may call".
        """
        import personalclaw.mcp_automation as M

        for tool in M._list_tools():
            out = _call(tool["name"], {}, store)
            text = str(out.get("text", "")) + str(out)
            assert "unknown tool" not in text.lower(), f"{tool['name']} has no dispatch branch"

    def test_the_tools_route_through_create_not_the_store(self, store, monkeypatch):
        """The routing IS the inheritance. A tool that wrote to the store directly would skip the
        bound, the screening and the announcement — and would look identical until it mattered."""
        calls: list[dict] = []

        def _spy(st, **kw):
            calls.append(kw)
            return T.ToolResult(True, "ok", {})

        monkeypatch.setattr(T, "create", _spy)
        _call("set_onetime_task", {"name": "a", "when": "in 1 hour", "message": "m"}, store)
        _call("set_recurring_task", {"name": "b", "cadence": "hourly", "message": "m"}, store)
        assert len(calls) == 2, "a tool bypassed triggers.tools.create"
        assert all(c["created_by"] == "agent" for c in calls), calls
        # the recurring tool's caller-facing `cadence` must reach the router's `when`
        assert calls[1]["when"] == "hourly", calls[1]

    def test_no_tool_description_hardcodes_the_configurable_number(self):
        """`automation_create` said "capped at 20", which is wrong the moment an operator changes
        the config — a description that states a stale number is worse than one that names the
        knob."""
        import personalclaw.mcp_automation as M

        for tool in M._list_tools():
            desc = tool["description"]
            assert "capped at 20" not in desc, tool["name"]


def _call(name: str, args: dict, store) -> dict:
    """Drive the real dispatcher with `_store` pointed at the test store."""
    import personalclaw.mcp_automation as M

    orig = M._store
    M._store = lambda: store  # type: ignore[assignment]
    try:
        raw = M._call_tool_inner(name, args)
    finally:
        M._store = orig  # type: ignore[assignment]
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {"ok": not str(raw).lower().startswith("error"), "text": str(raw)}
