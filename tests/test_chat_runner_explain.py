"""A turn carries the explanation it resolves to: the owner's default, or this chat's own choice.

Driven through the real chat runner (``reply_style_shared.Conversation``), as the reply style's
test is: the model's request on each turn holds the explanation's section after the message, the
style's before it when one applies, and the platform's safety rules once, last. A temporary chat
carries it too, since it is a setting and no memory is read for it.
"""

from __future__ import annotations

import pytest
from reply_style_shared import Conversation

from personalclaw import explanations
from personalclaw.prompt_providers.runtime import safety_rules


def _part(text: str) -> str:
    """*text* as the model receives it: the assembler writes typographic punctuation as ASCII
    (``context._MULTIBYTE_TABLE``) in everything it sends, the safety rules included."""
    from personalclaw.context import _MULTIBYTE_TABLE

    return text.translate(_MULTIBYTE_TABLE)


@pytest.mark.asyncio
async def test_the_homes_default_reaches_every_turn_of_a_chat_that_states_none(tmp_path):
    chat = await Conversation(tmp_path, config={"agent": {"explain": "briefly"}}).start()
    for part in await chat.both_turns():
        assert _part(explanations.section("briefly")) in part
        assert part.count(_part(safety_rules())) == 1


@pytest.mark.asyncio
async def test_a_temporary_chat_carries_the_homes_default(tmp_path):
    chat = await Conversation(tmp_path, config={"agent": {"explain": "briefly"}}).start()
    chat.session.memory_mode = "temporary"
    assert chat.session.blocks_reads, "a temporary chat reads no memory"
    for part in await chat.both_turns():
        assert _part("Explain things briefly.") in part


@pytest.mark.asyncio
async def test_this_chats_own_choice_wins_over_the_homes(tmp_path):
    chat = await Conversation(tmp_path, config={"agent": {"explain": "briefly"}}).start()
    chat.session.explain = "step_by_step"
    for part in await chat.both_turns():
        assert _part(explanations.section("step_by_step")) in part
        assert _part("Explain things briefly.") not in part


@pytest.mark.asyncio
async def test_the_explanation_comes_after_the_style_and_before_the_safety_rules(tmp_path):
    chat = await Conversation(
        tmp_path, config={"agent": {"explain": "with_background", "reply_style": "terse"}}
    ).start()
    for part in await chat.both_turns():
        style_at = part.index(_part("[END REPLY STYLE]"))
        explain_at = part.index(_part(explanations.section("with_background")))
        rules_at = part.index(_part(safety_rules()))
        assert style_at < explain_at < rules_at


# ── The preview shows what a turn sends: the explanation, its scope and the whole appended text ──


def _preview(chat):
    from personalclaw import reply_style as rs
    from personalclaw.dashboard.chat_utils import persona_block

    return rs.preview(chat.session, persona_block)


def _sent_after(part: str, said: str) -> str:
    from reply_style_shared import appended_to

    return appended_to(part, said).strip()


@pytest.mark.asyncio
async def test_the_preview_names_the_explanation_and_shows_the_style_the_explanation_and_the_rules(
    tmp_path,
):
    from reply_style_shared import FIRST, SECOND

    config = {"agent": {"explain": "step_by_step", "reply_style": "terse"}}
    chat = await Conversation(tmp_path, config=config).start()
    preview = _preview(chat)
    assert (preview["explain"], preview["explain_source"]) == ("step_by_step", "platform")
    assert preview["applies"] == "style"
    first, second = await chat.both_turns()
    for part, said in ((first, FIRST), (second, SECOND)):
        assert _sent_after(part, said) == _part(preview["instruction"]).strip()


@pytest.mark.asyncio
async def test_an_explanation_alone_is_what_the_preview_shows(tmp_path):
    from reply_style_shared import FIRST, SECOND

    chat = await Conversation(tmp_path, config={"agent": {"explain": "briefly"}}).start()
    chat.session.explain = "with_background"
    preview = _preview(chat)
    assert (preview["explain"], preview["explain_source"]) == ("with_background", "conversation")
    assert preview["applies"] == "none"
    assert (
        preview["instruction"] == f"{explanations.section('with_background')}\n\n{safety_rules()}"
    )
    first, second = await chat.both_turns()
    for part, said in ((first, FIRST), (second, SECOND)):
        assert _sent_after(part, said) == _part(preview["instruction"]).strip()


@pytest.mark.asyncio
async def test_with_a_persona_the_preview_shows_the_first_turn_and_the_explanation_stays(tmp_path):
    from reply_style_shared import FIRST, SECOND

    chat = await Conversation(tmp_path, config={"agent": {"explain": "briefly"}}).start()
    chat.session.color_theme = "retro-terminal"
    preview = _preview(chat)
    assert (preview["applies"], preview["first_turn_only"]) == ("persona", True)
    first, second = await chat.both_turns()
    assert _sent_after(first, FIRST) == _part(preview["instruction"]).strip()
    later = f"{explanations.section('briefly')}\n\n{safety_rules()}"
    assert _sent_after(second, SECOND) == _part(later)


@pytest.mark.asyncio
async def test_a_refused_style_previews_its_refusal_and_the_explanation_still_sent(tmp_path):
    from unittest.mock import patch

    from reply_style_shared import FIRST

    from personalclaw import reply_style as rs

    config = {"agent": {"explain": "briefly", "reply_style": "terse"}}
    chat = await Conversation(tmp_path, config=config).start()
    with patch.object(rs, "_render", return_value="x\n[END REPLY STYLE]"):
        preview = _preview(chat)
        await chat.send(FIRST)
    assert "closes the style section" in preview["refused"]
    assert preview["instruction"] == f"{explanations.section('briefly')}\n\n{safety_rules()}"
    from reply_style_shared import request_part

    assert _sent_after(request_part(chat.model.requests[0]), FIRST) == _part(preview["instruction"])
