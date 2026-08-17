"""A rung a USER reads is named by its label, never by its code key.

`RUNG_LABELS`' own docstring draws the line: *"runs on its own" is what a user needs to know;
"autonomous" is what the code calls it.* Four sentences a person reads were on the wrong side of
it, in three modules:

    rungs.route_action_type      "action.create_task resolves draft_only"
                                 → the BODY of the inbox row a held action raises
                                   ("The 'create_task' action on trigger t-1 did not run: …"),
                                   and the trigger's recorded outcome error
    rungs.route_action_type      "…, narrowed to auto_with_undo by the cautious profile"
    autonomy.promotion_eligibility  "Already at its ceiling (autonomous)."
                                 → the Guardrails panel's `record` line, on twelve rows
    ladder.explain_refused_grant "…can never go above one_tap — that is its declared ceiling."
                                 → the 400 body a refused promotion click shows

This file is the ratchet for the rule, driven through the real composers rather than asserted
against a hand-built object, plus the four MACHINE-facing sites that deliberately keep the key.

🪤 Assert the exact rung KEYS, never a word from one. Action-type keys contain rung-ish words
(`inbox.reply_draft` has "draft"), so a substring rule on "draft" would fail on a correct sentence.
"""

from __future__ import annotations

import pytest

from personalclaw.guardrails import autonomy as au
from personalclaw.guardrails import ladder as ld
from personalclaw.guardrails import policy as pol
from personalclaw.guardrails import rungs as rg


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """A throwaway home for the rung store. `PERSONALCLAW_HOME` as well as the patched
    `config_dir`, because several stores bind `config_dir` at import."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    cfg = home / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: cfg)
    rg.ensure_core_action_types()
    return home


def _keys_in(sentence: str) -> list[str]:
    """Which rung CODE keys leak into this sentence."""
    return [r for r in rg.RUNGS if r in sentence]


def test_the_vocabularies_are_actually_distinct() -> None:
    """The floor under every assertion below. If a key ever equalled its label, or a key were a
    substring of some label, the whole file would pass while proving nothing."""
    assert len(rg.RUNGS) >= 4
    for rung in rg.RUNGS:
        label = rg.rung_label(rung)
        assert label != rung, f"{rung} has no user-facing label, so this rule is unenforceable"
        assert rung not in label, f"{rung!r} inside its own label {label!r} defeats _keys_in"


def test_the_HELD_ACTION_inbox_body_names_the_rung_in_user_words() -> None:
    """`route_action_type().reason` is the sentence `announce_withheld` puts in an inbox row."""
    seen = 0
    for spec in rg.CORE_ACTION_TYPES:
        reason = rg.route_action_type(spec.key).reason
        assert _keys_in(reason) == [], f"{spec.key}: code name in user copy — {reason!r}"
        assert rg.rung_label(rg.resolve_rung(spec.key)) in reason, reason
        seen += 1
    assert seen >= 4, "the sweep must actually cover the declared types"


def test_the_NARROWED_branch_names_both_rungs_in_user_words(monkeypatch) -> None:
    """The second half of that sentence: a SafetyProfile that narrows names two rungs, and both
    were code keys. Exercised through a real narrowing profile, not a string built by hand."""
    monkeypatch.setattr(
        pol,
        "profile_for_session",
        lambda *a, **k: pol.SafetyProfile(name="cautious", approval="reads"),
    )
    reason = rg.route_action_type("action.artifact_write", session_key="s").reason

    assert "narrowed" in reason, f"the narrowing branch was not taken: {reason!r}"
    assert _keys_in(reason) == [], f"code name in user copy — {reason!r}"
    # Both halves, in user words: what it would have been, and what it was narrowed to.
    assert rg.rung_label(rg.RUNG_AUTONOMOUS) in reason
    assert rg.rung_label(rg.RUNG_AUTO_WITH_UNDO) in reason


def test_the_PANEL_record_line_names_the_ceiling_in_user_words() -> None:
    """ "Already at its ceiling (autonomous)." rendered on twelve rows of the Guardrails panel."""
    at_ceiling = [s for s in rg.CORE_ACTION_TYPES if s.floor == s.ceiling]
    assert at_ceiling, "no type is at its ceiling, so this branch is unreachable here"
    reason = au.promotion_eligibility(at_ceiling[0].key).reason

    assert "ceiling" in reason, reason
    assert _keys_in(reason) == [], f"code name in user copy — {reason!r}"
    assert rg.rung_label(at_ceiling[0].ceiling) in reason, reason


def test_the_REFUSED_GRANT_error_names_the_ceiling_in_user_words() -> None:
    """The 400 body a refused promotion shows. "above <rung>" needs a noun, so the predicate is
    quoted as the rung's name — the form this family settled on for the inbox proposal title."""
    low = [
        s
        for s in rg.CORE_ACTION_TYPES
        if rg.rung_rank(s.ceiling) < rg.rung_rank(rg.RUNG_AUTONOMOUS)
    ]
    assert low, "no type ceilings below autonomous, so this branch is unreachable here"
    spec = low[0]
    msg = ld.explain_refused_grant(spec.key, rg.RUNG_AUTONOMOUS)

    assert "declared ceiling" in msg, msg
    assert _keys_in(msg) == [], f"code name in user copy — {msg!r}"
    assert f"“{rg.rung_label(spec.ceiling)}”" in msg, msg


# ── the machine-facing sites that deliberately KEEP the key ───────────────────


def test_an_unknown_rung_from_a_CALLER_is_echoed_verbatim() -> None:
    """Not a leak — the honest answer to "why was `whatever` refused?" is the value sent. A label
    lookup here would either invent a rung or print an empty string."""
    msg = ld.explain_refused_grant(rg.CORE_ACTION_TYPES[0].key, "not_a_rung")
    assert "not_a_rung" in msg and "is not a rung on the ladder" in msg


def test_a_BAD_DECLARATION_raises_with_the_key_and_the_whole_ladder() -> None:
    """A developer registering an unknown ceiling needs the key and the valid set, not prose."""
    from dataclasses import replace

    with pytest.raises(ValueError) as e:
        au.register_action_type(replace(rg.CORE_ACTION_TYPES[0], ceiling="nonsense"))
    assert "nonsense" in str(e.value) and "ceiling" in str(e.value)


def test_the_MACHINE_strings_still_carry_the_key() -> None:
    """Two strings that must stay machine-readable, pinned so a later pass does not "finish the
    job": the audit detail (an SEL row is worth its exact value) and the proposal dedup key."""
    import inspect

    grant_src = inspect.getsource(au.grant_rung)
    assert 'detail=f"rung={rung}' in grant_src, "the audit row must keep the rung KEY"
    file_src = inspect.getsource(ld._file_proposal)
    assert 'dedup_key=f"autonomy_promotion:{key}:{next_rung}"' in file_src, "a dedup key is machine"


def test_there_is_ONE_label_accessor_now() -> None:
    """Three copies of `RUNG_LABELS.get(x, x)` had grown up in as many modules. Prose composers
    call `rung_label`; the only other reader is the handler that ships the whole vocabulary to the
    frontend, which needs the dict itself."""
    import inspect

    for mod in (au, ld):
        src = inspect.getsource(mod)
        assert "RUNG_LABELS.get(" not in src, f"{mod.__name__} should call rung_label()"
    from personalclaw.dashboard.handlers import autonomy as handler

    assert "RUNG_LABELS.get(" in inspect.getsource(handler), "the wire vocabulary is the exception"
