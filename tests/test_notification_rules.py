"""Per-(source, kind) notification rules (INBOX-NOTIFICATIONS-UNIFICATION T1.3/T1.4).

The single most important test in this file is
``test_no_rules_file_delivers_exactly_like_before``. This plan replaces `notify()`'s
delivery path with no gate to hide behind, so the safety property is that a user who has
never opened the new settings page sees precisely what they saw before. That equivalence is
what the dropped gate-OFF task would have proven.

Everything else here is about the second-order risk: a *policy* layer that can silence the
system. Every failure path must fail OPEN, and there are more of those paths than is
obvious — missing file, malformed JSON, wrong root type, wrong rule type, unknown mode,
unknown target, and a rules file written by a newer build.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from personalclaw import notification_kinds as nk
from personalclaw import notification_rules as nr


@pytest.fixture()
def home(tmp_path):
    """An isolated config dir for both the rules store and the digest queue."""
    (tmp_path / "entity_settings").mkdir(parents=True, exist_ok=True)
    with (
        patch("personalclaw.notification_rules.config_dir", return_value=tmp_path),
        patch("personalclaw.providers.entity_routes.config_dir", return_value=tmp_path),
    ):
        yield tmp_path


def _write_rules(home, doc):
    (home / "entity_settings" / "notification_rules.json").write_text(
        json.dumps(doc), encoding="utf-8"
    )


# ── the equivalence property ────────────────────────────────────────────


def test_no_rules_file_delivers_exactly_like_before(home):
    """No rules file ⇒ every registered kind resolves to immediate with dashboard only."""
    for k in nk.all_kinds():
        rule = nr.resolve_rule(k.source, k.kind)
        assert rule.mode == "immediate", f"{k.key} would not deliver as before"
        assert rule.targets == ("dashboard",)
        assert rule.conditions.matches("anything at all") == ""


def test_every_legacy_wire_kind_resolves_to_immediate_by_default(home):
    """The wire format is the flat string; each must reach an immediate rule."""
    for flat in nk.WIRE_CONSTANTS:
        assert nr.resolve_rule_for_legacy(flat).mode == "immediate"


# ── mode resolution ─────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["never", "badge", "immediate", "digest"])
def test_stored_mode_is_honored(home, mode):
    _write_rules(home, {"rules": {"cron/result": {"mode": mode}}})
    assert nr.resolve_rule("cron", "result").mode == mode


def test_unrelated_kinds_keep_their_defaults(home):
    """One configured row must not change any other row."""
    _write_rules(home, {"rules": {"cron/result": {"mode": "never"}}})
    assert nr.resolve_rule("cron", "result").mode == "never"
    assert nr.resolve_rule("hook", "fired").mode == "immediate"


# ── fail-open on every corruption path ──────────────────────────────────


def test_missing_file_falls_back_to_defaults(home):
    assert nr.load_rules() == {}
    assert nr.resolve_rule("cron", "result").mode == "immediate"


def test_malformed_json_falls_back_to_defaults(home):
    (home / "entity_settings" / "notification_rules.json").write_text("{not json", encoding="utf-8")
    assert nr.load_rules() == {}
    assert nr.resolve_rule("cron", "result").mode == "immediate"


@pytest.mark.parametrize("root", ["[]", '"a string"', "42", "null"])
def test_non_object_root_falls_back_to_defaults(home, root):
    (home / "entity_settings" / "notification_rules.json").write_text(root, encoding="utf-8")
    assert nr.resolve_rule("cron", "result").mode == "immediate"


@pytest.mark.parametrize("bad", ["[]", '"never"', "5", "null"])
def test_non_object_rule_falls_back_to_default_mode(home, bad):
    _write_rules(home, {"rules": {"cron/result": json.loads(bad)}})
    assert nr.resolve_rule("cron", "result").mode == "immediate"


def test_unknown_mode_falls_back_to_default_not_silence(home, caplog):
    """A typo'd mode must not become an accidental `never`."""
    _write_rules(home, {"rules": {"cron/result": {"mode": "nevr"}}})
    with caplog.at_level("WARNING"):
        rule = nr.resolve_rule("cron", "result")
    assert rule.mode == "immediate"
    assert "unknown mode" in caplog.text


def test_non_dict_rules_container_falls_back(home):
    _write_rules(home, {"rules": ["cron/result"]})
    assert nr.resolve_rule("cron", "result").mode == "immediate"


def test_a_good_mode_survives_a_malformed_targets_list(home):
    """Per-FIELD fallback: an unrelated typo must not discard a deliberate mode."""
    _write_rules(home, {"rules": {"cron/result": {"mode": "never", "targets": "dashboard"}}})
    rule = nr.resolve_rule("cron", "result")
    assert rule.mode == "never"
    assert rule.targets == ("dashboard",)


def test_unregistered_kind_resolves_through_the_generic_fallback(home):
    """An unknown pair still gets a rule (fail-open), not an exception."""
    rule = nr.resolve_rule("no-such-source", "no-such-kind")
    assert rule.mode == "immediate"
    assert rule.key == f"{nk.GENERIC_SOURCE}/{nk.GENERIC_KIND}"


# ── targets ─────────────────────────────────────────────────────────────


def test_known_targets_are_preserved_in_order(home):
    _write_rules(home, {"rules": {"cron/result": {"targets": ["channel_dm", "dashboard"]}}})
    assert nr.resolve_rule("cron", "result").targets == ("channel_dm", "dashboard")


def test_unknown_target_is_dropped_but_known_ones_survive(home):
    """A rules file from a NEWER build must keep the targets this build understands."""
    _write_rules(
        home, {"rules": {"cron/result": {"targets": ["dashboard", "hologram", "channel_dm"]}}}
    )
    assert nr.resolve_rule("cron", "result").targets == ("dashboard", "channel_dm")


def test_all_unknown_targets_fall_back_to_dashboard(home):
    """Never leave a rule with zero targets — that's silence by accident."""
    _write_rules(home, {"rules": {"cron/result": {"targets": ["hologram"]}}})
    assert nr.resolve_rule("cron", "result").targets == ("dashboard",)


def test_duplicate_targets_are_deduplicated(home):
    _write_rules(home, {"rules": {"cron/result": {"targets": ["dashboard", "dashboard"]}}})
    assert nr.resolve_rule("cron", "result").targets == ("dashboard",)


# ── conditions (lifted from inbox.evaluate_alert) ───────────────────────


def test_keyword_condition_matches_case_insensitively():
    c = nr.Conditions(keywords=("Deploy",))
    assert c.matches("time to DEPLOY the thing") == "keyword: Deploy"


def test_keyword_condition_is_a_substring_match_like_the_inbox_alert():
    """`evaluate_alert` used `in`, not word boundaries. Preserve that exactly."""
    assert nr.Conditions(keywords=("deploy",)).matches("redeployment") != ""


def test_empty_conditions_never_match():
    assert nr.Conditions().matches("deploy now, Jordan") == ""


def test_blank_keyword_is_ignored():
    """A stray empty string must not match every notification."""
    assert nr.Conditions(keywords=("", "   ")).matches("anything") == ""


def test_name_mention_matches_whole_words_only():
    c = nr.Conditions(name_mention=True)
    assert c.matches("hey jordan, look", "Jordan Marlow") == "name mention"
    assert c.matches("jordanian politics", "Jordan Marlow") == ""


def test_short_name_parts_are_skipped():
    """Initials and particles false-positive; `evaluate_alert` skipped <3 chars."""
    assert nr.Conditions(name_mention=True).matches("a de facto standard", "J de Vries") == ""


def test_name_mention_without_a_name_never_matches():
    assert nr.Conditions(name_mention=True).matches("anything", "") == ""


def test_empty_text_never_matches():
    assert nr.Conditions(keywords=("x",), name_mention=True).matches("", "Jordan") == ""


def test_conditions_match_agrees_with_inbox_evaluate_alert():
    """The generalized engine must reproduce the surface it replaces.

    Drives the REAL `inbox.evaluate_alert` and the new `Conditions.matches` over the same
    inputs; a divergence means users' existing alert config changes meaning at S3's
    backfill.
    """
    from personalclaw.inbox import InboxItem, evaluate_alert

    cases = [
        ("ship the deploy tonight", ["deploy"], False, "Jordan Marlow"),
        ("nothing to see", ["deploy"], False, "Jordan Marlow"),
        ("hey Jordan can you look", [], True, "Jordan Marlow"),
        ("jordanian politics", [], True, "Jordan Marlow"),
        ("REDEPLOY now", ["deploy"], False, ""),
        ("", ["deploy"], True, "Jordan Marlow"),
        ("a de facto standard", [], True, "J de Vries"),
    ]
    for text, keywords, name_mention, user in cases:
        item = InboxItem(
            id="c_1",
            channel="c",
            channel_name="c",
            thread_ts=None,
            message=text,
            sender_id="s",
            sender_name="s",
        )
        legacy = evaluate_alert(
            item,
            {"alert_keywords": keywords, "alert_on_name_mention": name_mention},
            user_name=user,
        )
        new = nr.Conditions(keywords=tuple(keywords), name_mention=name_mention).matches(text, user)
        assert bool(legacy) == bool(new), f"divergence on {text!r}: legacy={legacy!r} new={new!r}"


# ── escalation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("start", ["never", "badge", "digest"])
def test_escalation_promotes_any_quieter_mode_to_immediate(start):
    rule = nr.Rule("cron", "result", start)
    assert rule.escalated().mode == "immediate"


def test_escalation_is_idempotent_on_immediate():
    rule = nr.Rule("cron", "result", "immediate")
    assert rule.escalated() is rule


def test_escalation_does_not_add_delivery_targets():
    """A keyword hit means "show me now", not "also text me"; channel_dm leaves the box."""
    rule = nr.Rule("cron", "result", "badge", ("dashboard",))
    assert rule.escalated().targets == ("dashboard",)


def test_escalation_preserves_conditions():
    conds = nr.Conditions(keywords=("x",))
    assert nr.Rule("a", "b", "badge", ("dashboard",), conds).escalated().conditions is conds


# ── digest schedule ─────────────────────────────────────────────────────


def test_digest_defaults_to_eight_local(home):
    assert nr.digest_settings()["schedule"] == nr.DEFAULT_DIGEST_SCHEDULE


def test_digest_schedule_is_configurable(home):
    _write_rules(home, {"digest": {"schedule": "0 7 * * 1-5"}})
    assert nr.digest_settings()["schedule"] == "0 7 * * 1-5"


@pytest.mark.parametrize("bad", ["not a cron", "0 8 * *", "", "0 8 * * * *"])
def test_malformed_digest_schedule_falls_back(home, bad):
    _write_rules(home, {"digest": {"schedule": bad}})
    assert nr.digest_settings()["schedule"] == nr.DEFAULT_DIGEST_SCHEDULE


# ── the effective document the settings matrix renders ──────────────────


def test_rules_document_has_a_row_for_every_registered_kind(home):
    doc = nr.rules_document()
    assert {r["key"] for r in doc["rules"]} == {k.key for k in nk.all_kinds()}


def test_rules_document_marks_which_rows_are_configured(home):
    _write_rules(home, {"rules": {"cron/result": {"mode": "badge"}}})
    rows = {r["key"]: r for r in nr.rules_document()["rules"]}
    assert rows["cron/result"]["configured"] is True
    assert rows["hook/fired"]["configured"] is False


def test_rules_document_exposes_the_default_alongside_the_effective_mode(home):
    """The UI needs both to show "changed from default"."""
    _write_rules(home, {"rules": {"cron/result": {"mode": "never"}}})
    row = next(r for r in nr.rules_document()["rules"] if r["key"] == "cron/result")
    assert (row["mode"], row["default_mode"]) == ("never", "immediate")


def test_rules_document_is_json_serializable(home):
    """It crosses the HTTP boundary; a tuple or dataclass would 500 the endpoint."""
    json.dumps(nr.rules_document())


# ── digest queue ────────────────────────────────────────────────────────


def test_queue_and_drain_round_trip(home):
    nr.queue_for_digest({"kind": "cron", "title": "a"})
    nr.queue_for_digest({"kind": "cron", "title": "b"})
    drained = nr.drain_digest_queue()
    assert [d["title"] for d in drained] == ["a", "b"]


def test_drain_clears_the_queue(home):
    nr.queue_for_digest({"title": "a"})
    nr.drain_digest_queue()
    assert nr.drain_digest_queue() == []


def test_drain_of_a_missing_queue_is_empty_not_an_error(home):
    assert nr.drain_digest_queue() == []


def test_drain_truncates_rather_than_deleting(home):
    """Keeps permissions and survives a concurrent appender."""
    nr.queue_for_digest({"title": "a"})
    path = nr.digest_queue_path()
    nr.drain_digest_queue()
    assert path.exists(), "the queue file should be truncated, not unlinked"


def test_malformed_queue_line_is_skipped_not_fatal(home):
    """One bad append must not strand every other queued notification."""
    path = nr.digest_queue_path()
    path.write_text('{"title": "good"}\n{not json\n{"title": "also good"}\n', encoding="utf-8")
    assert [d["title"] for d in nr.drain_digest_queue()] == ["good", "also good"]


def test_non_object_queue_line_is_skipped(home):
    path = nr.digest_queue_path()
    path.write_text('{"title": "good"}\n[1,2,3]\n"a string"\n', encoding="utf-8")
    assert [d["title"] for d in nr.drain_digest_queue()] == ["good"]


def test_blank_lines_are_tolerated(home):
    path = nr.digest_queue_path()
    path.write_text('\n{"title": "good"}\n\n', encoding="utf-8")
    assert len(nr.drain_digest_queue()) == 1


def test_queue_stays_bounded_and_keeps_the_newest(home):
    """An undrained queue must not grow without bound.

    Asserts the INVARIANT (bounded by 2x the cap, newest retained), not an exact count:
    the trim is amortized — it fires when the file exceeds 2x the cap and cuts back to the
    cap, so the length at any instant sits anywhere in [cap, 2*cap]. Pinning a count here
    would encode the append/trim interleaving rather than the guarantee.
    """
    total = nr.DIGEST_QUEUE_CAP * 2 + 5
    for i in range(total):
        nr.queue_for_digest({"n": i})
    remaining = nr.drain_digest_queue()
    assert nr.DIGEST_QUEUE_CAP <= len(remaining) <= nr.DIGEST_QUEUE_CAP * 2
    assert remaining[-1]["n"] == total - 1, "must keep the NEWEST entries"
    assert remaining == sorted(remaining, key=lambda d: d["n"]), "order must be preserved"


def test_queue_never_exceeds_twice_the_cap_on_disk(home):
    """The bound holds on DISK, not just after a drain."""
    for i in range(nr.DIGEST_QUEUE_CAP * 3):
        nr.queue_for_digest({"n": i})
        if i % 250 == 0:
            lines = nr.digest_queue_path().read_text(encoding="utf-8").splitlines()
            assert len(lines) <= nr.DIGEST_QUEUE_CAP * 2 + 1


def test_queue_survives_non_ascii(home):
    nr.queue_for_digest({"title": "café — 日本語"})
    assert nr.drain_digest_queue()[0]["title"] == "café — 日本語"
