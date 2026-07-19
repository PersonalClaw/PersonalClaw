"""Interactive question cards (AskUserQuestion → question_card).

Two layers:
- ``validate_ask_user_question`` normalizes the Claude Code AskUserQuestion
  schema with defensive caps and rejects unusable payloads.
- ``_emit_question_card`` (chat_runner) broadcasts a session-keyed, redacted,
  normalized ``question_card`` frame for a valid payload, and silently skips
  (logs, no broadcast) a malformed one so a garbled tool call never breaks the
  turn.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from personalclaw.dashboard.chat_runner import _emit_question_card
from personalclaw.validation import (
    ValidationError,
    _AUQ_MAX_OPTIONS,
    _AUQ_MAX_QUESTIONS,
    validate_ask_user_question,
)


# ── validate_ask_user_question ──


class TestValidateAskUserQuestion:
    def test_normalizes_full_payload(self) -> None:
        out = validate_ask_user_question(
            {
                "questions": [
                    {
                        "question": "Which DB?",
                        "header": "Storage",
                        "multiSelect": False,
                        "options": [
                            {"label": "Postgres", "description": "relational"},
                            {"label": "Redis"},
                        ],
                    }
                ]
            }
        )
        assert out == [
            {
                "question": "Which DB?",
                "header": "Storage",
                "multiSelect": False,
                "options": [
                    {"label": "Postgres", "description": "relational"},
                    {"label": "Redis", "description": ""},
                ],
            }
        ]

    def test_string_options_are_lifted_to_objects(self) -> None:
        out = validate_ask_user_question(
            {"questions": [{"question": "Pick", "options": ["a", "b"]}]}
        )
        assert out[0]["options"] == [
            {"label": "a", "description": ""},
            {"label": "b", "description": ""},
        ]
        assert out[0]["multiSelect"] is False
        assert out[0]["header"] == ""

    def test_drops_question_with_no_usable_options(self) -> None:
        # One good, one optionless → only the good one survives.
        out = validate_ask_user_question(
            {
                "questions": [
                    {"question": "No opts", "options": []},
                    {"question": "Good", "options": [{"label": "x"}]},
                ]
            }
        )
        assert len(out) == 1
        assert out[0]["question"] == "Good"

    def test_caps_questions_and_options(self) -> None:
        out = validate_ask_user_question(
            {
                "questions": [
                    {"question": f"q{i}", "options": [{"label": f"o{j}"} for j in range(_AUQ_MAX_OPTIONS + 5)]}
                    for i in range(_AUQ_MAX_QUESTIONS + 5)
                ]
            }
        )
        assert len(out) == _AUQ_MAX_QUESTIONS
        assert all(len(q["options"]) <= _AUQ_MAX_OPTIONS for q in out)

    def test_truncates_long_strings(self) -> None:
        out = validate_ask_user_question(
            {"questions": [{"question": "x" * 5000, "options": [{"label": "y" * 5000}]}]}
        )
        assert len(out[0]["question"]) == 2000  # _AUQ_TEXT_CAP
        assert len(out[0]["options"][0]["label"]) == 400  # _AUQ_LABEL_CAP

    @pytest.mark.parametrize(
        "payload",
        [
            "not a dict",
            {"questions": "not a list"},
            {"questions": []},
            {"questions": [{"question": "", "options": [{"label": "a"}]}]},  # blank prompt
            {"questions": [{"question": "q", "options": []}]},  # no options
            {"no_questions": True},
        ],
    )
    def test_rejects_unusable_payload(self, payload) -> None:
        with pytest.raises(ValidationError):
            validate_ask_user_question(payload)


# ── _emit_question_card (broadcast wiring) ──


class TestEmitQuestionCard:
    def _state(self) -> MagicMock:
        state = MagicMock()
        state.broadcast_ws = MagicMock()
        return state

    def test_valid_input_broadcasts_normalized_frame(self) -> None:
        state = self._state()
        tool_input = json.dumps(
            {
                "questions": [
                    {"question": "Deploy now?", "header": "Action", "options": [{"label": "Yes"}, {"label": "No"}]}
                ]
            }
        )
        _emit_question_card(state, "sess-1", tool_input, "call-abc")

        state.broadcast_ws.assert_called_once()
        event, payload = state.broadcast_ws.call_args[0]
        assert event == "question_card"
        assert payload["session"] == "sess-1"
        assert payload["tool_call_id"] == "call-abc"
        assert payload["questions"][0]["question"] == "Deploy now?"
        assert [o["label"] for o in payload["questions"][0]["options"]] == ["Yes", "No"]

    def test_redacts_every_user_facing_string(self) -> None:
        state = self._state()
        secret = "AKIAIOSFODNN7EXAMPLE"  # AWS access-key id → redact_credentials catches it
        tool_input = json.dumps(
            {
                "questions": [
                    {
                        "question": f"Use key {secret}?",
                        "header": secret,
                        "options": [{"label": secret, "description": f"the {secret} key"}],
                    }
                ]
            }
        )
        _emit_question_card(state, "sess-1", tool_input, None)

        payload = state.broadcast_ws.call_args[0][1]
        q = payload["questions"][0]
        blob = json.dumps(q)
        assert secret not in blob  # redacted in question, header, label, and description
        assert "[REDACTED" in q["question"]
        assert "[REDACTED" in q["header"]
        assert "[REDACTED" in q["options"][0]["label"]
        assert "[REDACTED" in q["options"][0]["description"]

    def test_malformed_json_skips_broadcast(self, caplog) -> None:
        state = self._state()
        _emit_question_card(state, "sess-1", "{not valid json", "call-x")
        state.broadcast_ws.assert_not_called()
        assert "AskUserQuestion card skipped" in caplog.text

    def test_unusable_payload_skips_broadcast(self, caplog) -> None:
        state = self._state()
        _emit_question_card(state, "sess-1", json.dumps({"questions": []}), "call-x")
        state.broadcast_ws.assert_not_called()
        assert "AskUserQuestion card skipped" in caplog.text

    def test_empty_tool_input_is_noop(self) -> None:
        state = self._state()
        _emit_question_card(state, "sess-1", None, "call-x")
        _emit_question_card(state, "sess-1", "", "call-x")
        state.broadcast_ws.assert_not_called()
