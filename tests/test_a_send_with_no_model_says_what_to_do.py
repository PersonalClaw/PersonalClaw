"""A chat send with no model bound says what is missing and what to do, in words.

Measured on a home with no model provider: the send answered "The turn failed with an error
PersonalClaw doesn't recognize. Try again; if it keeps failing, check the gateway log. Details:
WHAT: no model provider resolves for use case 'chat' …", while the gateway log named the cause.
The refusal is not unknown: it carries a coded envelope (``ERR_MODEL_UNRESOLVED``, a registered
code) with the failure, its reason and its fix. The chat's error sentence
(``llm_helpers.humanize_provider_error``) recognised only the registry's own refusal class, so
the resolver's coded refusal fell through to the sentence for a failure nobody can explain, and
"try again" was the one step that could never help.

Driven through the real turn handler with the refusal the real resolver raises on an empty home.
"""

from __future__ import annotations

import logging

import pytest
from test_chat_turn_error_is_said import _run_failing_turn
from test_no_provider_first_run_rail import matches_no_model_text

from personalclaw.errors import AgentError
from personalclaw.llm_helpers import humanize_provider_error
from personalclaw.providers.provider_bridge import (
    ProviderResolutionError,
    resolve_provider_for_use_case,
)


def _the_refusal_an_empty_home_raises() -> ProviderResolutionError:
    with pytest.raises(ProviderResolutionError) as caught:
        resolve_provider_for_use_case("chat")
    return caught.value


@pytest.mark.asyncio
async def test_a_send_with_no_model_bound_says_what_to_do(tmp_path, caplog):
    refusal = _the_refusal_an_empty_home_raises()
    assert refusal.agent_error is not None and refusal.agent_error.code == "ERR_MODEL_UNRESOLVED"

    with caplog.at_level(logging.DEBUG, logger="personalclaw.dashboard.chat_runner"):
        said = await _run_failing_turn(tmp_path, refusal)

    assert "doesn't recognize" not in said, said
    assert "Try again" not in said, "a retry cannot bind a model"
    assert said == (
        "No model provider resolves for use case 'chat': no provider in config.json declares "
        "the capability this use case needs. Add a model provider in Settings → Providers, "
        "then bind 'chat' to it."
    )
    # The chat's calm setup card still recognises it (web/src/pages/chat/NoModelSetupState.tsx).
    assert matches_no_model_text(said)
    # The log names the cause in one line, as it does for any model that cannot be resolved; a
    # traceback is kept for a defect, and this is not one.
    logged = [
        r
        for r in caplog.records
        if r.name == "personalclaw.dashboard.chat_runner" and r.levelno >= logging.WARNING
    ]
    assert [r.getMessage() for r in logged] == [f"Chat turn in session chat-1-test failed: {said}"]
    assert all(r.exc_info is None for r in logged)


def test_a_provider_with_no_model_chosen_is_named_in_the_sentence():
    refusal = ProviderResolutionError(
        "No provider configured for use case 'chat'.",
        AgentError(
            code="ERR_MODEL_UNRESOLVED",
            what="no model provider resolves for use case 'chat'",
            why="no model is chosen for “local”",
            fix="choose one of its models in Settings → Models",
        ),
    )
    assert humanize_provider_error(refusal) == (
        "No model provider resolves for use case 'chat': no model is chosen for “local”. "
        "Choose one of its models in Settings → Models."
    )


def test_a_stale_pin_reads_as_its_own_sentence_and_not_as_first_run_setup():
    """The other coded refusal the resolver raises: a model WAS chosen and its provider went
    missing. Said plainly too, and still not the first-run setup card."""
    refusal = ProviderResolutionError(
        "The model pinned for 'chat' cannot be built.",
        AgentError(
            code="ERR_MODEL_UNRESOLVED",
            what="the model pinned for use case 'chat' ('Gone:m-1') cannot be built",
            why="no provider named 'Gone' is in config.json",
            fix="re-add 'Gone' in Settings → Providers, or rebind 'chat' in Settings → Models",
        ),
    )
    said = humanize_provider_error(refusal)
    assert said == (
        "The model pinned for use case 'chat' ('Gone:m-1') cannot be built: no provider named "
        "'Gone' is in config.json. Re-add 'Gone' in Settings → Providers, or rebind 'chat' in "
        "Settings → Models."
    )
    assert not matches_no_model_text(said)


def test_an_envelope_whose_code_is_not_registered_is_not_taken_as_known():
    """Only a registered code is a known failure: an envelope carrying a code the registry does
    not hold is still said as unrecognised, with its words as the detail."""
    refusal = ProviderResolutionError(
        "unregistered",
        AgentError(code="ERR_NOT_A_REGISTERED_CODE", what="w", why="y", fix="f"),
    )
    said = humanize_provider_error(refusal)
    assert said.startswith("The turn failed with an error PersonalClaw doesn't recognize.")
