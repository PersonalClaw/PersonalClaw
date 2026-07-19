"""Tests for the `persistent_session` flag on the `schedule_add` MCP tool.

The flag must be:
- Exposed in the tool input schema (discoverable by LLMs)
- Default True when omitted
- Stored on the ScheduleJob when False
"""

import uuid

import pytest

from personalclaw.schedule import ScheduleService
from personalclaw.mcp_schedule import _call_tool_inner, _list_tools, _validate_args


def _unique_name() -> str:
    return f"psess-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _isolate_schedule_store(monkeypatch, tmp_path):
    monkeypatch.setattr("personalclaw.schedule._DEFAULT_DIR", tmp_path)
    monkeypatch.setattr("personalclaw.mcp_schedule.config_dir", lambda: tmp_path)
    monkeypatch.delenv("PERSONALCLAW_CHANNEL_ID", raising=False)
    monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)


class TestScheduleAddPersistentSession:
    def test_schema_declares_persistent_session(self):
        """schedule_add tool schema must include the flag so LLMs can find it."""
        tools = _list_tools()
        schedule_add = next(t for t in tools if t["name"] == "schedule_add")
        props = schedule_add["inputSchema"]["properties"]
        assert "persistent_session" in props
        assert props["persistent_session"]["type"] == "boolean"

    def test_default_is_persistent(self):
        """schedule_add without the flag → job.persistent_session is True."""
        name = _unique_name()
        result = _call_tool_inner(
            "schedule_add", {"name": name, "message": "hi", "every": 120}
        )
        assert "Added job" in result

        svc = ScheduleService()
        jobs = [j for j in svc.list_jobs() if j.name == name]
        assert len(jobs) == 1
        assert jobs[0].persistent_session is True

    def test_false_flag_is_stored(self):
        """schedule_add with persistent_session=False → stored as False."""
        name = _unique_name()
        result = _call_tool_inner(
            "schedule_add",
            {
                "name": name,
                "message": "hi",
                "every": 120,
                "persistent_session": False,
            },
        )
        assert "Added job" in result

        svc = ScheduleService()
        jobs = [j for j in svc.list_jobs() if j.name == name]
        assert len(jobs) == 1
        assert jobs[0].persistent_session is False

    def test_true_flag_is_explicit_noop(self):
        """schedule_add with persistent_session=True → stored as True (explicit)."""
        name = _unique_name()
        _call_tool_inner(
            "schedule_add",
            {
                "name": name,
                "message": "hi",
                "every": 120,
                "persistent_session": True,
            },
        )
        svc = ScheduleService()
        jobs = [j for j in svc.list_jobs() if j.name == name]
        assert jobs[0].persistent_session is True


class TestScheduleAddPersistentSessionValidation:
    """validation.py enforces the flag's type before it reaches _call_tool_inner.

    Raw LLM-supplied args must pass through SCHEDULE_ADD_SCHEMA, never be consumed
    directly. This class pins the validator's contract.
    """

    def test_schema_has_persistent_session_bool_field(self):
        """SCHEDULE_ADD_SCHEMA declares persistent_session with bool type."""
        from personalclaw.validation import SCHEDULE_ADD_SCHEMA

        specs = {f.name: f for f in SCHEDULE_ADD_SCHEMA.fields}
        assert "persistent_session" in specs, (
            "persistent_session must be declared in SCHEDULE_ADD_SCHEMA "
            "so unknown-field rejection does not block valid callers"
        )
        assert specs["persistent_session"].type is bool

    def test_validator_accepts_true(self):
        """bool True passes through cleanly."""
        cleaned = _validate_args(
            "schedule_add",
            {"name": "x", "message": "y", "every": 120, "persistent_session": True},
        )
        assert cleaned["persistent_session"] is True

    def test_validator_accepts_false(self):
        """bool False passes through cleanly."""
        cleaned = _validate_args(
            "schedule_add",
            {"name": "x", "message": "y", "every": 120, "persistent_session": False},
        )
        assert cleaned["persistent_session"] is False

    def test_validator_rejects_non_bool_string(self):
        """LLM that sends the string "false" instead of False must be rejected."""
        from personalclaw.validation import ValidationError

        with pytest.raises(ValidationError):
            _validate_args(
                "schedule_add",
                {
                    "name": "x",
                    "message": "y",
                    "every": 120,
                    "persistent_session": "false",
                },
            )

    def test_validator_rejects_non_bool_int(self):
        """An int (even 0 or 1) is not a bool — reject so stored state stays typed."""
        from personalclaw.validation import ValidationError

        with pytest.raises(ValidationError):
            _validate_args(
                "schedule_add",
                {
                    "name": "x",
                    "message": "y",
                    "every": 120,
                    "persistent_session": 0,
                },
            )
