"""The questions an ask-the-user call carries, read with defensive caps.

``validate_ask_user_question`` normalizes AskUserQuestion's shape (PersonalClaw's own ``ask_user``
takes the same) and rejects a payload with no question that has options. What becomes of the
questions — the card, her answer, the call that waits — is
``test_an_agent_question_reaches_its_owner.py``.
"""

from __future__ import annotations

import pytest

from personalclaw.validation import (
    _AUQ_MAX_OPTIONS,
    _AUQ_MAX_QUESTIONS,
    ValidationError,
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
                    {
                        "question": f"q{i}",
                        "options": [{"label": f"o{j}"} for j in range(_AUQ_MAX_OPTIONS + 5)],
                    }
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
