"""approval_mode / silent fields + list serialization on the unified Trigger facade."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from personalclaw.dashboard.handlers.triggers import api_trigger_create, api_triggers
from personalclaw.schedule import ScheduleDefinition, ScheduleJob, make_agent_action


def _action(task="m", approval_mode=""):
    config = {"task_template": task}
    if approval_mode:
        config["approval_mode"] = approval_mode
    return {"provider": "invoke-agent", "config": config}


class TestScheduleTriggerApprovalMode:
    def _make_request(self, body: dict) -> MagicMock:
        mock_state = MagicMock()
        mock_state.crons.add_job.return_value = ScheduleJob(
            id="abc",
            name="t",
            action=make_agent_action(message="m"),
            schedule=ScheduleDefinition(kind="every", every_secs=300),
        )
        mock_state.crons.is_running.return_value = False
        mock_state.crons.running_since.return_value = None
        mock_state._sessions = {}
        request = MagicMock()
        request.app = {"state": mock_state}
        request.get = lambda *a, **k: "dashboard"
        request.json = AsyncMock(return_value=body)
        return request

    @pytest.mark.asyncio
    async def test_valid_approval_mode_auto(self):
        request = self._make_request(
            {
                "trigger_type": "schedule",
                "name": "t",
                "every": 300,
                "action": _action(approval_mode="auto"),
            }
        )
        resp = await api_trigger_create(request)
        assert resp.status == 200
        # 🔴 SUPERSEDED CONTRACT (S101 write re-point): the action rides `workflow.inline` in the
        # store now, not an `add_job` kwarg. The approval_mode is still folded into its config.
        from personalclaw.dashboard.handlers.triggers import _trigger_store

        workflow = _trigger_store().get("clock:t").trigger.workflow
        assert workflow["inline"]["config"]["approval_mode"] == "auto"

    @pytest.mark.asyncio
    async def test_invalid_approval_mode_rejected(self):
        request = self._make_request(
            {
                "trigger_type": "schedule",
                "name": "t",
                "every": 300,
                "action": _action(approval_mode="evil"),
            }
        )
        resp = await api_trigger_create(request)
        assert resp.status == 400
        request.app["state"].crons.add_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_silent_flag_set(self):
        request = self._make_request(
            {
                "trigger_type": "schedule",
                "name": "t",
                "every": 300,
                "silent": True,
                "action": _action(),
            }
        )
        resp = await api_trigger_create(request)
        assert resp.status == 200
        # 🔴 SUPERSEDED CONTRACT (S101 write re-point). This asserted the legacy `add_job` mock's
        # `.silent`; the row now lives in the unified store, where `silent` is `delivery == "none"`
        # (LEGACY_FIELD_MAP). Reading the mock would pass forever without the write happening.
        from personalclaw.dashboard.handlers.triggers import _trigger_store

        trigger = _trigger_store().get("clock:t").trigger
        assert trigger.delivery == "none"

    @pytest.mark.asyncio
    async def test_no_approval_mode_accepted(self):
        request = self._make_request(
            {"trigger_type": "schedule", "name": "t", "every": 300, "action": _action()}
        )
        resp = await api_trigger_create(request)
        assert resp.status == 200


class TestTriggerListFields:
    @pytest.mark.asyncio
    async def test_schedule_trigger_serialization_includes_action_and_fields(
        self, tmp_path, monkeypatch
    ):
        """🔴 REWRITTEN FOR S110. This built a 20-attribute `MagicMock` job for
        `crons.list_jobs` — the legacy fallback the facade's CRUD retirement deleted. A mock that
        answers every attribute cannot tell you whether the projection reads the right ones; the
        store row can, because a wrong address yields an empty field.

        The wire contract is unchanged: the same keys, from the store's addresses (`delivery`
        carries channel + silent; `approval_mode` rides inside `workflow.inline.config`).
        """
        import personalclaw.config.loader as loader
        from personalclaw.dashboard.handlers import triggers as T
        from personalclaw.triggers.models import Trigger
        from personalclaw.triggers.store import TriggerStore

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(T, "config_dir", lambda: tmp_path)
        TriggerStore(base_dir=tmp_path).upsert(
            Trigger(
                id="j1",
                name="test",
                kind="clock",
                enabled=True,
                spec={"kind": "interval", "interval_secs": 300},
                # `delivery: none` IS the legacy `silent=True`; a silent job delivers nowhere,
                # so the channel is not carried alongside it (S98's mapping).
                delivery="none",
                workflow={
                    "inline": {
                        "provider": "invoke-agent",
                        "config": {"task_template": "msg", "approval_mode": "auto"},
                    }
                },
            )
        )

        mock_state = MagicMock()
        mock_state._sessions = {}
        request = MagicMock()
        request.app = {"state": mock_state}
        request.query = {"type": "schedule"}

        resp = await api_triggers(request)
        data = json.loads(resp.body)
        t = data["triggers"][0]
        assert t["kind"] == "schedule"
        assert t["id"] == "schedule:j1"
        assert t["approval_mode"] == "auto"
        assert t["silent"] is True
        # The action derives from the invoke-agent exec mode.
        assert t["action"]["provider"] == "invoke-agent"


# ── 🔴 #587 at the HTTP layer: `enabled` on POST /api/triggers ────────────────
#
# `_create_schedule` never read `body["enabled"]`. It called `tools.create` (which hardcoded
# `enabled=True`), post-processed only `delivery`, then called `_arm_if_needed` — so a caller
# asking for a trigger created switched off got a live, ARMED one, and the response echoed it as
# enabled: accurate about the wrong thing.


class TestCreateHonorsEnabledOverTheWire:
    def _request(self, body: dict) -> MagicMock:
        mock_state = MagicMock()
        mock_state._sessions = {}
        request = MagicMock()
        request.app = {"state": mock_state}
        request.get = lambda *a, **k: "dashboard"
        request.json = AsyncMock(return_value=body)
        return request

    def _home(self, monkeypatch, tmp_path):
        import personalclaw.config.loader as loader
        from personalclaw.dashboard.handlers import triggers as T

        monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
        monkeypatch.setattr(T, "config_dir", lambda: tmp_path)

    def _body(self, **over):
        body = {"trigger_type": "schedule", "name": "t", "every": 300, "action": _action()}
        body.update(over)
        return body

    @pytest.mark.asyncio
    async def test_enabled_false_creates_a_disabled_unarmed_trigger(self, monkeypatch, tmp_path):
        from personalclaw.triggers.store import TriggerStore

        self._home(monkeypatch, tmp_path)
        resp = await api_trigger_create(self._request(self._body(enabled=False)))
        assert resp.status == 200

        rows = TriggerStore(base_dir=tmp_path).load()
        assert len(rows) == 1
        trigger = rows[0].trigger
        assert trigger.enabled is False
        assert trigger.next_fire_at == "", "a trigger created off must not be armed"

    @pytest.mark.asyncio
    async def test_omitting_enabled_still_creates_it_live(self, monkeypatch, tmp_path):
        """Vacuity floor and the compatibility contract — every existing client omits the field."""
        from personalclaw.triggers.store import TriggerStore

        self._home(monkeypatch, tmp_path)
        resp = await api_trigger_create(self._request(self._body()))
        assert resp.status == 200

        trigger = TriggerStore(base_dir=tmp_path).load()[0].trigger
        assert trigger.enabled is True
        assert trigger.next_fire_at

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["false", "true", 0, 1, "0", None, [], {}])
    async def test_a_non_bool_enabled_is_a_400_not_a_coercion(self, bad, monkeypatch, tmp_path):
        """The same rule `POST /api/triggers/{id}/toggle` already applies, and for the same reason:
        the JSON string "false" is TRUTHY under `bool()`, so coercing would silently ARM a trigger
        the caller asked to be created off — inverting the request rather than refusing it.

        Two endpoints that take the same field must answer about it the same way.
        """
        import json

        from personalclaw.triggers.store import TriggerStore

        self._home(monkeypatch, tmp_path)
        resp = await api_trigger_create(self._request(self._body(enabled=bad)))
        assert resp.status == 400
        # The STRUCTURED envelope, unlike this function's older flat siblings: a new refusal joins
        # the shape `test_wire_error_envelope_census` ratchets the codebase toward.
        error = json.loads(resp.body.decode())["error"]
        assert error["code"] == "invalid_request"
        assert "enabled" in error["message"]
        assert TriggerStore(base_dir=tmp_path).load() == [], "a refused create must persist nothing"
