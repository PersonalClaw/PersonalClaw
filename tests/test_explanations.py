"""Explanations are a setting: one table, two scopes, and a section after the reply style's.

How replies explain things is the owner's default (``agent.explain``) or this chat's own choice
(its session's ``explain``), resolved on every turn, most specific first, and sent after the reply
style's section with the platform's safety rules once, last (``reply_style.appended``). The table
holds the three answers the old card offered, word for word, so its saved answers are found by
their text. A turn with neither a style nor an explanation is unchanged.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

TABLE = Path(__file__).resolve().parents[1] / "src" / "personalclaw" / "explanations.json"

#: The card's three answers, as it sent them (handlers_system.EXPLAIN_ANSWERS before this change).
CARD = {
    "briefly": "Explain things briefly.",
    "step_by_step": "Explain things step by step.",
    "with_background": "Explain things with the background.",
}


def _configured(monkeypatch: pytest.MonkeyPatch, explain: str) -> None:
    """A config whose home default for explanations is *explain*, read fresh on each call."""
    from personalclaw.config.loader import AppConfig

    cfg = SimpleNamespace(agent=SimpleNamespace(explain=explain, reply_style="default"), agents={})
    monkeypatch.setattr(AppConfig, "load", classmethod(lambda cls: cfg))


def test_the_table_holds_the_cards_answers_word_for_word() -> None:
    rows = json.loads(TABLE.read_text(encoding="utf-8"))
    assert [r["id"] for r in rows] == ["default", "briefly", "step_by_step", "with_background"]
    assert {r["id"]: r["sent"] for r in rows} == {"default": "", **CARD}
    assert [r["label"] for r in rows] == [
        "No preference",
        "Briefly",
        "Step by step",
        "With the background",
    ]
    assert all(r["hint"] for r in rows)
    from personalclaw import explanations

    assert explanations.EXPLAIN_IDS == ("default", "briefly", "step_by_step", "with_background")


def test_this_chats_choice_wins_then_the_homes_and_an_unknown_one_inherits() -> None:
    from personalclaw.explanations import EXPLAIN_PRECEDENCE, resolve

    assert EXPLAIN_PRECEDENCE == ("conversation", "platform")
    assert resolve("step_by_step", "briefly") == ("step_by_step", "conversation")
    assert resolve("", "briefly") == ("briefly", "platform")
    assert resolve("sideways", "briefly") == ("briefly", "platform")
    assert resolve("", "sideways") == ("default", "platform")
    assert resolve(" Briefly ", "") == ("briefly", "conversation")


def test_a_session_resolves_from_a_fresh_read_of_the_home_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from personalclaw.explanations import resolve_for

    _configured(monkeypatch, "with_background")
    assert resolve_for(SimpleNamespace(explain="")) == ("with_background", "platform")
    assert resolve_for(SimpleNamespace(explain="briefly")) == ("briefly", "conversation")
    _configured(monkeypatch, "step_by_step")
    assert resolve_for(SimpleNamespace()) == ("step_by_step", "platform")


def test_the_explanation_follows_the_reply_style_and_the_safety_rules_stay_last() -> None:
    from personalclaw import reply_style
    from personalclaw.prompt_providers.runtime import safety_rules

    sent = reply_style.appended("Plan the garden.", "terse", explain="step_by_step")
    style = reply_style.section("terse").split("\n\n" + safety_rules())[0]
    assert sent == (
        "Plan the garden.\n\n"
        f"{style}\n"
        "[EXPLANATIONS: Step by step]\nExplain things step by step.\n[END EXPLANATIONS]\n\n"
        f"{safety_rules()}\n"
    )
    assert sent.count(safety_rules()) == 1


def test_an_explanation_alone_carries_its_section_and_the_safety_rules() -> None:
    from personalclaw import reply_style
    from personalclaw.prompt_providers.runtime import safety_rules

    assert reply_style.appended("Plan the garden.", "default", explain="briefly") == (
        "Plan the garden.\n\n"
        "[EXPLANATIONS: Briefly]\nExplain things briefly.\n[END EXPLANATIONS]\n\n"
        f"{safety_rules()}\n"
    )


def test_a_turn_with_neither_or_with_a_style_alone_is_unchanged_byte_for_byte() -> None:
    from personalclaw import reply_style

    assert reply_style.appended("Plan the garden.", "default") == "Plan the garden."
    assert (
        reply_style.appended("Plan the garden.", "default", explain="default") == "Plan the garden."
    )
    assert (
        reply_style.appended("Plan the garden.", "default", explain="sideways")
        == "Plan the garden."
    )
    alone = f"Plan the garden.\n\n{reply_style.section('terse')}\n"
    assert reply_style.appended("Plan the garden.", "terse") == alone
    assert reply_style.appended("Plan the garden.", "terse", explain="default") == alone


def test_a_style_whose_text_opens_or_closes_the_explanations_section_is_refused_at_load() -> None:
    from personalclaw import reply_style

    for text in ("Be short.\n[EXPLANATIONS: Briefly]", "Be short.\n[ end explanations ]"):
        why = reply_style._refusal("mine.md", text, "Mine", "Short replies.", len(text))
        assert why and "explanations section" in why, (text, why)
    assert reply_style._refusal("mine.md", "Be short.", "Mine", "Short replies.", 9) == ""


@pytest.mark.parametrize("marker", ["[END EXPLANATIONS]", "[ explanations: Briefly ]"])
def test_a_bundled_style_whose_text_holds_the_explanations_markers_is_not_sent(marker) -> None:
    """An edit of a bundled style's prompt in Settings is read at each turn, not at load, so it is
    refused there: the turn carries the explanation and the rules, and no style section."""
    from unittest.mock import patch

    from personalclaw import reply_style

    with patch.object(reply_style, "_render", return_value=f"Be short.\n{marker}"):
        with pytest.raises(reply_style.StyleRefused, match="explanations section"):
            reply_style.instruction("terse")
        sent = reply_style.appended("Plan the garden.", "terse", explain="briefly")
    assert "[REPLY STYLE" not in sent
    assert "Be short." not in sent
    assert sent.count("[EXPLANATIONS: Briefly]") == 1


def test_the_homes_default_is_editable_as_one_of_the_choices() -> None:
    from personalclaw.config.editable import _EDITABLE_CONFIG
    from personalclaw.explanations import EXPLAIN_IDS

    assert _EDITABLE_CONFIG["agent.explain"] == {"type": "enum", "values": list(EXPLAIN_IDS)}


def test_the_homes_default_reads_back_only_a_known_choice(tmp_path, monkeypatch) -> None:
    import json

    from personalclaw.config import loader

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    for written, read in (
        ("step_by_step", "step_by_step"),
        ("Sideways", "default"),
        (None, "default"),
    ):
        loader.config_dir()
        loader.config_path().write_text(
            json.dumps({"agent": {"explain": written}}), encoding="utf-8"
        )
        assert loader.AppConfig.load().agent.explain == read, written


# ── The answer the old card kept as a pinned preference moves to the default, once ──────────────


@pytest.fixture()
def card_home(tmp_path, monkeypatch):
    """A home with a memory store, holding answers as the old card wrote them."""
    from personalclaw.vector_memory import VectorMemoryStore

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    store = VectorMemoryStore(db_path=tmp_path / "m.db")
    store.init()
    store.embed_fn = lambda t: [1.0, 0.0, 0.0]
    return store


def _card_answer(store, choice: str, *, pinned: bool = True, forgotten: bool = False, at: str = ""):
    """What the old card's answer wrote: an explicit ``style`` facet with its text, pinned."""
    from personalclaw import preference_facets as pf

    key = pf.upsert_facet(store, "style", CARD[choice], cue="explicit")
    pf.pin_facet(store, key, pinned)
    if forgotten:
        pf.forget_facet(store, key)
    if at:
        row = json.loads(store.get_semantic(key)["value_json"])
        store.set_semantic(key, {**row, "updated_at": at}, 0.9, "facet")
    return key


def _facet(store, key):
    from personalclaw.preference_facets import Facet

    return Facet.from_payload(json.loads(store.get_semantic(key)["value_json"]))


def _home_explain(home: Path, explain: str) -> None:
    (home / "config.json").write_text(json.dumps({"agent": {"explain": explain}}), encoding="utf-8")


def test_a_pinned_card_answer_becomes_the_default_and_is_forgotten(card_home, tmp_path):
    from personalclaw.config.loader import AppConfig
    from personalclaw.explanations import adopt_card_answer

    key = _card_answer(card_home, "step_by_step")
    assert adopt_card_answer(card_home) == "step_by_step"
    assert AppConfig.load().agent.explain == "step_by_step"
    assert _facet(card_home, key).forgotten is True


def test_a_second_run_writes_nothing(card_home, tmp_path):
    from personalclaw.explanations import adopt_card_answer

    key = _card_answer(card_home, "briefly")
    adopt_card_answer(card_home)
    config, facet = (tmp_path / "config.json").read_bytes(), card_home.get_semantic(key)[
        "value_json"
    ]
    assert adopt_card_answer(card_home) == ""
    assert (tmp_path / "config.json").read_bytes() == config
    assert card_home.get_semantic(key)["value_json"] == facet


def test_an_unpinned_or_forgotten_card_answer_and_any_other_facet_are_left(card_home, tmp_path):
    from personalclaw import preference_facets as pf
    from personalclaw.config.loader import AppConfig
    from personalclaw.explanations import adopt_card_answer

    unpinned = _card_answer(card_home, "briefly", pinned=False)
    forgotten = _card_answer(card_home, "with_background", forgotten=True)
    other = pf.upsert_facet(card_home, "style", "Keep replies short.", cue="explicit")
    pf.pin_facet(card_home, other, True)
    before = {k: card_home.get_semantic(k)["value_json"] for k in (unpinned, forgotten, other)}
    assert adopt_card_answer(card_home) == ""
    assert AppConfig.load().agent.explain == "default"
    assert {k: card_home.get_semantic(k)["value_json"] for k in before} == before
    assert not (tmp_path / "config.json").exists(), "nothing to adopt writes no config"


def test_a_default_the_config_states_is_kept_and_the_answer_still_forgotten(card_home, tmp_path):
    from personalclaw.config.loader import AppConfig
    from personalclaw.explanations import adopt_card_answer

    _home_explain(tmp_path, "briefly")
    key = _card_answer(card_home, "with_background")
    assert adopt_card_answer(card_home) == ""
    assert AppConfig.load().agent.explain == "briefly"
    assert _facet(card_home, key).forgotten is True


def test_a_config_that_states_no_known_choice_takes_the_answer(card_home, tmp_path):
    from personalclaw.config.loader import AppConfig
    from personalclaw.explanations import adopt_card_answer

    _home_explain(tmp_path, "sideways")
    _card_answer(card_home, "briefly")
    assert adopt_card_answer(card_home) == "briefly"
    assert AppConfig.load().agent.explain == "briefly"


def test_of_two_pinned_answers_the_newest_is_taken_and_both_are_forgotten(card_home, tmp_path):
    """Both forgotten, or the older one would still be sent as a preference beside the default
    and a second run would have something to do."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.explanations import adopt_card_answer

    older = _card_answer(card_home, "with_background", at="2026-10-01T09:00:00+00:00")
    newer = _card_answer(card_home, "briefly", at="2026-11-01T09:00:00+00:00")
    assert adopt_card_answer(card_home) == "briefly"
    assert AppConfig.load().agent.explain == "briefly"
    assert _facet(card_home, older).forgotten and _facet(card_home, newer).forgotten


def test_of_two_pinned_answers_the_newest_is_taken_whichever_it_is(card_home, tmp_path):
    from personalclaw.config.loader import AppConfig
    from personalclaw.explanations import adopt_card_answer

    older = _card_answer(card_home, "briefly", at="2026-10-01T09:00:00+00:00")
    newer = _card_answer(card_home, "with_background", at="2026-11-01T09:00:00+00:00")
    assert adopt_card_answer(card_home) == "with_background"
    assert AppConfig.load().agent.explain == "with_background"
    assert _facet(card_home, older).forgotten and _facet(card_home, newer).forgotten


def test_a_config_that_states_no_preference_takes_the_answer(card_home, tmp_path):
    """No preference (``default``, the pill's value) states nothing, so the answer moves in."""
    from personalclaw.config.loader import AppConfig
    from personalclaw.explanations import adopt_card_answer

    _home_explain(tmp_path, "default")
    _card_answer(card_home, "step_by_step")
    assert adopt_card_answer(card_home) == "step_by_step"
    assert AppConfig.load().agent.explain == "step_by_step"


def test_a_malformed_row_is_passed_over_and_the_answer_still_moves(card_home, tmp_path):
    from personalclaw import preference_facets as pf
    from personalclaw.explanations import adopt_card_answer

    _card_answer(card_home, "briefly")
    bad = pf.facet_key("style", CARD["with_background"])
    card_home.set_semantic(bad, ["not", "a", "facet"], 0.9, "facet")
    assert adopt_card_answer(card_home) == "briefly"


def test_a_config_whose_agent_is_not_an_object_leaves_everything(card_home, tmp_path):
    from personalclaw.explanations import adopt_card_answer

    (tmp_path / "config.json").write_text(json.dumps({"agent": "sideways"}), encoding="utf-8")
    key = _card_answer(card_home, "briefly")
    before = (tmp_path / "config.json").read_bytes()
    assert adopt_card_answer(card_home) == ""
    assert (tmp_path / "config.json").read_bytes() == before
    assert _facet(card_home, key).forgotten is False


# ── The move runs at start, on the card's store, and never stops the start ──────────────────────


def _a_state_whose_memory_holds(store):
    """A dashboard state whose global memory (`handlers.memory._global_provider`) is *store*."""
    return SimpleNamespace(
        context_builder=SimpleNamespace(memory=SimpleNamespace(vector_store=store))
    )


@pytest.mark.asyncio
async def test_the_start_up_move_reads_the_cards_store_and_sets_the_default(card_home):
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard import server

    key = _card_answer(card_home, "with_background")
    await server._adopt_card_answer(_a_state_whose_memory_holds(card_home))
    assert AppConfig.load().agent.explain == "with_background"
    assert _facet(card_home, key).forgotten is True


@pytest.mark.asyncio
async def test_a_start_up_move_that_fails_is_logged_and_never_raised(card_home, caplog):
    from personalclaw.dashboard import server

    class Broken:
        def get_semantic(self, key):
            raise OSError("the store is unreadable")

    with caplog.at_level("WARNING", logger="personalclaw.dashboard.server"):
        await server._adopt_card_answer(_a_state_whose_memory_holds(Broken()))
    assert "Moving the card's explanations answer failed" in caplog.text


@pytest.mark.asyncio
async def test_the_scheduled_move_is_held_on_the_state_and_runs(card_home):
    from personalclaw.config.loader import AppConfig
    from personalclaw.dashboard import server

    _card_answer(card_home, "briefly")
    state = _a_state_whose_memory_holds(card_home)
    server._start_card_answer_move(state)
    assert isinstance(state._card_answer_task, asyncio.Task), "the task is not held against GC"
    await state._card_answer_task
    assert AppConfig.load().agent.explain == "briefly"
