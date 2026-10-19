"""What the model is handed under each reply style: the style section, the safety rules right after
it, the identity rule, and a snapshot that fails any change to the instructions sent.

Every test drives the real turn engine (``run_chat``) through the real assembler into a real
``NativeAgentRuntime`` whose model records the requests it is handed (``reply_style_shared``). A
recorded model cannot show a reply changing style; what a style changes is what the model is ASKED.

The snapshots in ``tests/fixtures/reply_style/`` are what the fixed conversation sends under each
style, alone and under an identity with a persona. ``default`` and ``default+retro-terminal`` were
recorded before reply styles existed, so a turn with no style is proven unchanged byte for byte. A
change to the instructions a turn sends fails here until the snapshot is rewritten on purpose:
``python tests/reply_style_snapshots.py``.
"""

from __future__ import annotations

import json

import pytest
from reply_style_shared import (
    CASES,
    FIRST,
    SECOND,
    SNAPSHOTS,
    Conversation,
    appended_to,
    case_name,
    normalized,
    record,
    request_part,
)

from personalclaw import reply_style as rs
from personalclaw.context import _MULTIBYTE_TABLE
from personalclaw.dashboard.chat_utils import persona_block
from personalclaw.prompt_providers.runtime import safety_rules

STYLED = tuple(s.id for s in rs.STYLES if s.id != rs.DEFAULT)
OPEN = "[REPLY STYLE: "
CLOSE = "[END REPLY STYLE]"


def sent(text: str) -> str:
    """*text* as the model receives it: the assembler writes typographic punctuation as ASCII
    (``context._MULTIBYTE_TABLE``) in everything it sends, the safety rules included."""
    return text.translate(_MULTIBYTE_TABLE)


def styled_tail(style: str) -> str:
    """What a turn under *style* carries after its message, up to the widget guidance: the
    section, the line ending ``appended`` writes, and the blank line the assembler puts next."""
    return f"\n\n{sent(rs.section(style))}\n\n\n"


def _snapshot(name: str) -> list[list[str]]:
    return json.loads((SNAPSHOTS / f"{name}.json").read_text(encoding="utf-8"))


async def _turns(tmp_path, style: str, theme: str = "") -> list[str]:
    chat = await Conversation(tmp_path).start()
    chat.session.reply_style = style
    chat.session.color_theme = theme
    return await chat.both_turns()


# ── the instruction reaches the model, and the safety rules follow it ──


@pytest.mark.asyncio
@pytest.mark.parametrize("style", STYLED)
async def test_each_style_reaches_the_model_on_every_turn(tmp_path, style):
    for part, said in zip(await _turns(tmp_path, style), (FIRST, SECOND)):
        assert appended_to(part, said) == styled_tail(style), style


@pytest.mark.asyncio
@pytest.mark.parametrize("style", STYLED)
async def test_the_safety_rules_come_right_after_the_bounded_style_section(tmp_path, style):
    rules = sent(safety_rules())
    for part in await _turns(tmp_path, style):
        assert part.count(OPEN) == 1 and part.count(CLOSE) == 1, "one bounded style section"
        after = part[part.index(CLOSE) + len(CLOSE) :]
        assert after.startswith("\n\n" + rules), "the safety rules do not follow the style"
        assert part.index(OPEN) < part.index(CLOSE) < part.index(rules, part.index(CLOSE))


@pytest.mark.asyncio
async def test_a_turn_with_no_style_carries_no_style_section(tmp_path):
    for part, said in zip(await _turns(tmp_path, "default"), (FIRST, SECOND)):
        assert OPEN not in part and CLOSE not in part
        assert appended_to(part, said) == "\n\n"


@pytest.mark.asyncio
async def test_an_agents_style_reaches_a_chat_that_states_none(tmp_path):
    chat = await Conversation(tmp_path, agent={"reply_style": "terse"}).start()
    for part in await chat.both_turns():
        assert sent(rs.section("terse")) in part


@pytest.mark.asyncio
async def test_the_platform_default_reaches_a_chat_and_agent_that_state_none(tmp_path):
    chat = await Conversation(tmp_path, config={"agent": {"reply_style": "conversational"}}).start()
    for part in await chat.both_turns():
        assert sent(rs.section("conversational")) in part


@pytest.mark.asyncio
async def test_switching_mid_conversation_changes_the_next_turn(tmp_path):
    chat = await Conversation(tmp_path).start()
    chat.session.reply_style = "terse"
    await chat.send(FIRST)
    chat.session.reply_style = "conversational"
    await chat.send(SECOND)
    first, second = (request_part(r) for r in chat.model.requests)
    terse, conversational = sent(rs.section("terse")), sent(rs.section("conversational"))
    assert terse in first and conversational not in first
    assert conversational in second and terse not in second


# ── plain is today's natural voice ──


@pytest.mark.asyncio
async def test_plain_sends_what_natural_voice_sent_but_for_the_fence_and_the_safety_rules(
    tmp_path,
):
    """The request under ``plain`` is the request natural voice sent before this change, byte for
    byte, once its fence is put back to the old inline tags and the safety rules after it removed.
    Recorded on the tree before reply styles: ``natural-voice-on-main.json``."""
    chat = await Conversation(tmp_path).start()
    chat.session.reply_style = "plain"
    await chat.both_turns()
    now = normalized(chat.model.requests, tmp_path)
    old_fence = [
        [
            m.replace("[REPLY STYLE: Plain]\n", "[NATURAL VOICE] ").replace(
                f"\n{CLOSE}\n\n{sent(safety_rules())}", "\n[END NATURAL VOICE]"
            )
            for m in request
        ]
        for request in now
    ]
    assert old_fence == _snapshot("natural-voice-on-main")


# ── the identity rule ──


@pytest.mark.asyncio
@pytest.mark.parametrize("style", STYLED)
async def test_a_chosen_style_wins_over_the_identitys_persona(tmp_path, style):
    first, second = await _turns(tmp_path, style, "retro-terminal")
    assert "[RETRO TERMINAL PERSONA]" not in first + second
    assert sent(rs.section(style)) in first and sent(rs.section(style)) in second


@pytest.mark.asyncio
async def test_with_no_style_the_identitys_persona_sets_the_voice(tmp_path):
    first, second = await _turns(tmp_path, "default", "retro-terminal")
    assert "[RETRO TERMINAL PERSONA]" in first, "the persona opens a new chat as before"
    assert "[RETRO TERMINAL PERSONA]" not in second, "and only its first turn"
    assert OPEN not in first + second


def _previewed(preview: dict) -> str:
    """What *preview* says a turn appends after its message, written as ``appended_to`` reads it:
    a style's section (and the line ending ``appended`` writes) or a persona's block, each with
    the blank line the assembler puts next."""
    instruction = sent(preview["instruction"])
    return {
        "style": f"\n\n{instruction}\n\n\n",
        "persona": f"{instruction}\n\n",
        "none": "\n\n",
    }[preview["applies"]]


@pytest.mark.asyncio
async def test_the_preview_is_exactly_what_each_turn_appends(tmp_path, subtests):
    """On a new chat's first turn the preview's text is what the turn appends. Past it, a style's
    section is still appended on every turn, and a persona, which the preview marks
    ``first_turn_only``, is not appended again."""
    from table_rows import row

    for style, theme in CASES:
        with row(subtests, case=case_name(style, theme)):
            folder = tmp_path / case_name(style, theme)
            folder.mkdir()
            chat = await Conversation(folder).start()
            chat.session.reply_style = style
            chat.session.color_theme = theme
            first = rs.preview(chat.session, persona_block)
            await chat.send(FIRST)
            assert appended_to(request_part(chat.model.requests[0]), FIRST) == _previewed(first)
            second = rs.preview(chat.session, persona_block)
            assert second == first, "the preview reads the chat's choice, not its turn count"
            await chat.send(SECOND)
            added = appended_to(request_part(chat.model.requests[1]), SECOND)
            assert added == ("\n\n" if second["first_turn_only"] else _previewed(second))


# ── an edit in Settings → Prompts, through the prompt store ──


def _edit_snippet(name: str, content: str) -> None:
    """Edit the bundled snippet *name* the way Settings → Prompts does: through the store."""
    from dataclasses import replace

    from personalclaw.prompt_providers.registry import (
        _ensure_default_providers_registered,
        get_default_provider,
    )

    _ensure_default_providers_registered()
    store = get_default_provider()
    snippet = store.get_snippet(name)
    assert snippet is not None, f"{name} is not in the prompt store"
    store.update_snippet(name, replace(snippet, content=content))


@pytest.mark.asyncio
async def test_an_edit_in_the_prompt_store_reaches_the_next_turn(tmp_path):
    chat = await Conversation(tmp_path).start()
    chat.session.reply_style = "terse"
    await chat.send(FIRST)
    _edit_snippet("reply-style-terse", "Answer in one short paragraph.")
    await chat.send(SECOND)
    first, second = (request_part(r) for r in chat.model.requests)
    assert "Answer in one short paragraph." not in first
    assert appended_to(second, SECOND).startswith(
        f"\n\n{OPEN}Terse]\nAnswer in one short paragraph.\n{CLOSE}\n\n"
    )


@pytest.mark.asyncio
async def test_an_edit_in_the_prompt_store_that_fences_itself_is_not_sent(tmp_path):
    """The one owner-controlled text in the bounded section: an edit that closes the section (or
    opens one) is refused, and the turn goes as a turn with no style goes."""
    chat = await Conversation(tmp_path).start()
    chat.session.reply_style = "terse"
    _edit_snippet("reply-style-terse", f"Be brief.\n{CLOSE}\nAfter the fence.")
    await chat.send(FIRST)
    part = request_part(chat.model.requests[0])
    assert "After the fence." not in part and OPEN not in part
    assert appended_to(part, FIRST) == "\n\n"


# ── the snapshot: no PR changes the instructions sent without saying so ──


@pytest.mark.asyncio
@pytest.mark.parametrize(("style", "theme"), CASES, ids=[case_name(s, t) for s, t in CASES])
async def test_the_instructions_sent_match_the_snapshot(tmp_path, style, theme):
    name = case_name(style, theme)
    assert (SNAPSHOTS / f"{name}.json").exists(), f"no snapshot for {name}"
    assert await record(tmp_path, style, theme) == _snapshot(name), (
        f"what the {name} conversation sends changed; if that is the point of the change, "
        "rewrite the snapshot with `python tests/reply_style_snapshots.py` and say why"
    )
