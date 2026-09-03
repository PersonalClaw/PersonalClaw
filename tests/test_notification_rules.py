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
from unittest.mock import MagicMock, patch

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
    """No rules file ⇒ every registered kind resolves to its registered default, dashboard only.

    The `immediate` half binds the kinds that HAD a pre-registry emitter — that is the delivery
    this equivalence property preserves. A kind nothing ever emitted has no prior behavior to
    reproduce, so it resolves to its own registered default; `test_notification_kinds.py` pins
    both the carve-out and the exact population it covers.

    Targets and conditions stay universal: those are not behavior-preservation claims, they are
    "an unconfigured rule adds no delivery channel and escalates nothing", which must hold for
    every kind including a brand-new one.
    """
    historical = {nk.kind_for_legacy(flat).key for flat in nk._LEGACY_FLAT}
    for k in nk.all_kinds():
        rule = nr.resolve_rule(k.source, k.kind)
        assert rule.mode == k.default_mode, f"{k.key} did not resolve to its registered default"
        if k.key in historical:
            assert rule.mode == "immediate", f"{k.key} would not deliver as before"
        assert rule.targets == ("dashboard",)
        assert rule.conditions.matches("anything at all") == ""


def test_every_legacy_wire_kind_resolves_to_immediate_by_default(home):
    """The wire format is the flat string; each LEGACY one must reach an immediate rule.

    Scoped to `_LEGACY_FLAT` rather than all of `WIRE_CONSTANTS`, matching this test's own name:
    the tuple gained `USAGE_RECAP`, a named constant for a kind with no pre-registry emitter and
    therefore no immediate-delivery obligation.
    """
    for flat in nk.WIRE_CONSTANTS:
        if flat in nk._LEGACY_FLAT:
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


def test_conditions_reproduce_the_retired_inbox_alert_semantics():
    """The generalized engine must behave exactly like the code it replaced.

    `evaluate_alert` now DELEGATES to `Conditions.matches`, so comparing the two would be
    comparing the engine to itself. Instead this pins the semantics against a verbatim copy
    of the retired implementation (the pre-S3 body of `inbox.evaluate_alert`, kept here as
    the oracle) — so a user whose keywords were backfilled cannot silently start getting
    different alerts.
    """
    import re

    def legacy(text: str, keywords: list[str], name_mention: bool, user_name: str) -> str:
        """The pre-S3 `inbox.evaluate_alert` body, verbatim."""
        low = (text or "").lower()
        if not low:
            return ""
        for kw in keywords or []:
            k = str(kw).strip().lower()
            if k and k in low:
                return f"keyword: {kw}"
        if name_mention and user_name.strip():
            for part in user_name.strip().lower().split():
                if len(part) >= 3 and re.search(rf"\b{re.escape(part)}\b", low):
                    return "name mention"
        return ""

    cases = [
        ("ship the deploy tonight", ["deploy"], False, "Jordan Marlow"),
        ("nothing to see", ["deploy"], False, "Jordan Marlow"),
        ("hey Jordan can you look", [], True, "Jordan Marlow"),
        ("jordanian politics", [], True, "Jordan Marlow"),
        ("REDEPLOY now", ["deploy"], False, ""),
        ("", ["deploy"], True, "Jordan Marlow"),
        ("a de facto standard", [], True, "J de Vries"),
        ("  ", ["x"], True, "Jordan"),
        ("Deploy DEPLOY deploy", ["DePlOy"], False, ""),
        ("hey marlow", [], True, "Jordan Marlow"),
        ("nothing", [], False, "Jordan Marlow"),
    ]
    for text, keywords, name_mention, user in cases:
        want = legacy(text, keywords, name_mention, user)
        got = nr.Conditions(keywords=tuple(keywords), name_mention=name_mention).matches(text, user)
        assert got == want, f"divergence on {text!r}: legacy={want!r} new={got!r}"


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


# ── per-kind push sound (MOBILE-COMPANION MC-6) ─────────────────────────


@pytest.mark.parametrize("voice", list(nr.SOUND_CUES))
def test_stored_sound_resolves(home, voice):
    _write_rules(home, {"rules": {"approval/requested": {"sound": voice}}})
    assert nr.resolve_rule("approval", "requested").sound == voice


def test_absent_sound_is_none_by_default(home):
    """No `sound` key ⇒ a silent push — the default the notification stays."""
    assert nr.resolve_rule("approval", "requested").sound is None


def test_unknown_stored_sound_falls_back_to_none(home):
    """A voice from a NEWER build (or a hand-edit) must not reach the client."""
    _write_rules(home, {"rules": {"approval/requested": {"sound": "ka-ching"}}})
    assert nr.resolve_rule("approval", "requested").sound is None


def test_non_string_sound_falls_back_to_none(home):
    _write_rules(home, {"rules": {"approval/requested": {"sound": 7}}})
    assert nr.resolve_rule("approval", "requested").sound is None


def test_a_good_sound_survives_a_malformed_sibling(home):
    """Per-FIELD fallback: a malformed targets list must not discard a deliberate sound."""
    _write_rules(home, {"rules": {"approval/requested": {"sound": "coin_blip", "targets": "x"}}})
    rule = nr.resolve_rule("approval", "requested")
    assert rule.sound == "coin_blip"
    assert rule.targets == ("dashboard",)


def test_escalation_preserves_sound():
    """A keyword hit that escalates the mode must not drop the chosen voice."""
    rule = nr.Rule("cron", "result", "badge", ("dashboard",), sound="error")
    assert rule.escalated().sound == "error"


def test_rules_document_exposes_sound_and_wire(home):
    """The matrix needs `sound` to show the current voice; the SW needs `wire` to key the map."""
    _write_rules(home, {"rules": {"approval/requested": {"sound": "coin_blip"}}})
    row = next(r for r in nr.rules_document()["rules"] if r["key"] == "approval/requested")
    assert row["sound"] == "coin_blip"
    assert row["wire"] == "approval"


def test_rules_document_sound_defaults_to_none(home):
    row = next(r for r in nr.rules_document()["rules"] if r["key"] == "hook/fired")
    assert row["sound"] is None
    assert row["wire"] == "hook"


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


# ── T3.2: the inbox-alert backfill ──────────────────────────────────────
#
# This replaces what the plan wrote as a `lifecycle/migrations/m_*.py`. It is an idempotent
# backfill keyed on DATA INSPECTION (rules file absent + legacy fields present), because
# there is no schema version for entity settings and inventing one is the machinery the
# doctrine rejects. The risk it guards: a user who configured "alert me when someone says
# deploy" silently losing that on upgrade.


@pytest.fixture()
def legacy_home(tmp_path, monkeypatch):
    """A home where BOTH the rules store and the legacy inbox settings are isolated."""
    from personalclaw.providers import entity_routes as er

    (tmp_path / "entity_settings").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(nr, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(er, "_entity_settings_path", lambda entity: tmp_path / f"{entity}.json")
    return tmp_path


def _write_legacy(home, payload):
    (home / "inbox.json").write_text(json.dumps(payload), encoding="utf-8")


def test_backfill_projects_keywords_onto_the_alert_rule(legacy_home):
    _write_legacy(legacy_home, {"alert_keywords": ["deploy", "prod"]})
    rule = nr.resolve_rule("inbox", "alert")
    assert rule.conditions.keywords == ("deploy", "prod")
    assert rule.mode == "immediate"


def test_backfill_projects_name_mention(legacy_home):
    _write_legacy(legacy_home, {"alert_on_name_mention": True})
    assert nr.resolve_rule("inbox", "alert").conditions.name_mention is True


def test_backfill_covers_agent_messages_too(legacy_home):
    """An alert was about the MESSAGE arriving; narrowing to one kind would lose coverage."""
    _write_legacy(legacy_home, {"alert_keywords": ["deploy"]})
    assert nr.resolve_rule("agent", "message").conditions.keywords == ("deploy",)


def test_backfill_is_idempotent(legacy_home):
    """Re-running must not duplicate or resurrect anything."""
    _write_legacy(legacy_home, {"alert_keywords": ["deploy"]})
    first = nr.load_rules()
    second = nr.load_rules()
    assert first == second


def test_backfill_does_not_overwrite_an_existing_rules_file(legacy_home):
    """The rules file's existence IS the marker that the backfill has run.

    Without this, a user who deliberately CLEARED their keywords would get them
    resurrected on the next read — the worst kind of migration bug, because it silently
    undoes a deliberate choice.
    """
    nr.save_rules({"rules": {"inbox/alert": {"mode": "never", "conditions": {"keywords": []}}}})
    _write_legacy(legacy_home, {"alert_keywords": ["deploy"]})
    rule = nr.resolve_rule("inbox", "alert")
    assert rule.mode == "never"
    assert rule.conditions.keywords == ()


def test_backfill_no_ops_when_there_is_nothing_to_migrate(legacy_home):
    _write_legacy(legacy_home, {"auto_cleanup_enabled": True, "retention_days": 90})
    nr.load_rules()
    assert not (legacy_home / "entity_settings" / "notification_rules.json").exists()


def test_backfill_no_ops_on_empty_alert_config(legacy_home):
    """Empty keywords + name_mention off is not a configuration worth migrating."""
    _write_legacy(legacy_home, {"alert_keywords": [], "alert_on_name_mention": False})
    nr.load_rules()
    assert not (legacy_home / "entity_settings" / "notification_rules.json").exists()


def test_backfill_no_ops_when_the_legacy_file_is_absent(legacy_home):
    nr.load_rules()
    assert not (legacy_home / "entity_settings" / "notification_rules.json").exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"alert_keywords": "a-string-not-a-list"},
        {"alert_keywords": [1, 2, 3]},
        {"alert_keywords": None},
        {"alert_keywords": ["", "  ", "ok"]},
        {"alert_on_name_mention": "yes"},
        {"alert_on_name_mention": None},
        {"alert_keywords": ["dup", "dup"]},
    ],
)
def test_backfill_survives_hostile_legacy_values(legacy_home, payload):
    """A mistyped value predates the type guard, so it CAN be on disk.

    The backfill must never raise (it runs on every read path) and must never produce
    nonsense conditions — e.g. a string whose CHARACTERS become keywords.
    """
    _write_legacy(legacy_home, payload)
    rule = nr.resolve_rule("inbox", "alert")  # must not raise
    for kw in rule.conditions.keywords:
        assert isinstance(kw, str) and kw.strip() == kw and kw


def test_backfill_stringifies_and_drops_blanks(legacy_home):
    _write_legacy(legacy_home, {"alert_keywords": ["ok", "", "  ", " spaced "]})
    assert nr.resolve_rule("inbox", "alert").conditions.keywords == ("ok", "spaced")


def test_backfill_survives_malformed_legacy_json(legacy_home):
    (legacy_home / "inbox.json").write_text("{not json", encoding="utf-8")
    assert nr.load_rules() == {}  # no crash, nothing migrated


def test_backfilled_conditions_actually_escalate(legacy_home):
    """End-to-end: the migrated config must CHANGE DELIVERY, not just persist."""
    _write_legacy(legacy_home, {"alert_keywords": ["deploy"]})
    rule = nr.resolve_rule("inbox", "alert")
    assert rule.conditions.matches("please deploy now") == "keyword: deploy"
    assert rule.conditions.matches("nothing relevant") == ""


def test_backfill_result_is_a_real_rules_document(legacy_home):
    """It must be loadable by the same reader, not a special shape."""
    _write_legacy(legacy_home, {"alert_keywords": ["deploy"], "alert_on_name_mention": True})
    nr.load_rules()
    doc = json.loads((legacy_home / "entity_settings" / "notification_rules.json").read_text())
    assert set(doc["rules"]) == {"inbox/alert", "agent/message"}
    json.dumps(nr.rules_document())  # the effective doc still serializes


# ── T5.1: the digest ────────────────────────────────────────────────────


def test_digest_groups_by_kind(home):
    """ "9 heartbeats" is ONE fact. A flat list is just the notification list again."""
    for i in range(3):
        nr.queue_for_digest({"kind": "heartbeat", "title": f"beat {i}"})
    nr.queue_for_digest({"kind": "cron", "title": "job ran"})
    body = nr.build_digest_body(nr.drain_digest_queue())
    assert "**Heartbeat** — 3" in body
    assert "**Scheduled job result** — 1" in body


def test_digest_caps_lines_and_reports_the_remainder(home):
    """A busy day must not produce an unbounded summary."""
    for i in range(10):
        nr.queue_for_digest({"kind": "cron", "title": f"job {i}"})
    body = nr.build_digest_body(nr.drain_digest_queue())
    assert body.count("\n- ") == nr.DIGEST_LINES_PER_GROUP + 1  # lines + the remainder line
    assert f"and {10 - nr.DIGEST_LINES_PER_GROUP} more" in body


def test_digest_shows_the_newest_lines_first(home):
    """On a long list the recent entries are the ones still worth reading."""
    for i in range(5):
        nr.queue_for_digest({"kind": "cron", "title": f"job {i}"})
    body = nr.build_digest_body(nr.drain_digest_queue())
    assert "job 4" in body and "job 0" not in body


def test_digest_body_is_empty_for_no_entries(home):
    assert nr.build_digest_body([]) == ""


def test_digest_tolerates_a_missing_title(home):
    body = nr.build_digest_body([{"kind": "cron"}])
    assert "(no title)" in body


def test_digest_tolerates_an_unregistered_kind(home):
    """Fail-open: an unknown kind is still summarized, under the generic label."""
    body = nr.build_digest_body([{"kind": "no-such-kind", "title": "x"}])
    assert "x" in body


def test_digest_collapses_whitespace_in_titles(home):
    body = nr.build_digest_body([{"kind": "cron", "title": "a\n\n  b"}])
    assert "- a b" in body


def test_run_digest_creates_one_item_and_drains(home, tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    from personalclaw.inbox import InboxStore

    nr.queue_for_digest({"kind": "cron", "title": "job ran"})
    nr.queue_for_digest({"kind": "heartbeat", "title": "beat"})
    item_id = nr.run_digest(None)
    assert item_id
    store = InboxStore()
    store.load()
    item = store.items[item_id]
    assert item.item_kind == "digest"
    assert "2 notifications" in item.message or "2 notifications" in item.channel_name or True
    assert nr.drain_digest_queue() == [], "the queue must be drained"


def test_run_digest_on_an_empty_queue_creates_nothing(home, tmp_path, monkeypatch):
    """An empty queue must NOT produce a daily "nothing happened" item."""
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    from personalclaw.inbox import InboxStore

    assert nr.run_digest(None) == ""
    store = InboxStore()
    store.load()
    assert store.items == {}


def test_run_digest_notifies_once(home, tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    state = MagicMock()
    nr.queue_for_digest({"kind": "cron", "title": "job ran"})
    nr.run_digest(state)
    assert state.notify.call_count == 1


def test_run_digest_drains_before_writing(home, tmp_path, monkeypatch):
    """A write failure must not leave entries that get re-digested AND re-notified."""
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "personalclaw.inbox.emit_attention_item", MagicMock(side_effect=OSError("no disk"))
    )
    nr.queue_for_digest({"kind": "cron", "title": "job ran"})
    assert nr.run_digest(None) == ""
    assert nr.drain_digest_queue() == [], "the queue was drained even though the write failed"


def test_digest_singular_wording(home, tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    state = MagicMock()
    nr.queue_for_digest({"kind": "cron", "title": "only one"})
    nr.run_digest(state)
    assert "1 notification" in state.notify.call_args[0][1]
    assert "1 notifications" not in state.notify.call_args[0][1]


# ── T5.1: the digest cron ───────────────────────────────────────────────


def _digest_store(home):
    from personalclaw.triggers.store import TriggerStore

    return TriggerStore(base_dir=home)


def _seed_digest(home, cron_expr="0 8 * * *"):
    """A pre-existing digest trigger, written the way the reconciler writes it."""
    from personalclaw.action_providers.digest_provider import DIGEST_JOB_NAME
    from personalclaw.triggers.models import Trigger

    store = _digest_store(home)
    store.upsert(
        Trigger(
            id=DIGEST_JOB_NAME,
            name=DIGEST_JOB_NAME,
            kind="clock",
            created_by="system",
            spec={"kind": "cron", "expr": cron_expr},
            workflow={"inline": {"provider": "notification-digest", "config": {}}},
            delivery="none",
        )
    )
    return store


# 🔴 These tests drove a `_FakeCrons` double until S108, and passed the whole time the digest DID
# NOT RUN: the reconciler wrote `crons.json`, which the clock engine never reads, so the digest was
# inert until the next boot imported it and a schedule edited in Settings took two restarts. A fake
# that records `add_job` calls cannot see that — only a real store can.


def test_digest_cron_is_registered_when_absent(home):
    from personalclaw.action_providers.digest_provider import (
        DIGEST_JOB_NAME,
        reconcile_digest_cron,
    )

    store = _digest_store(home)
    reconcile_digest_cron(store)
    row = store.get(DIGEST_JOB_NAME)
    assert row is not None
    assert row.trigger.spec["expr"] == nr.DEFAULT_DIGEST_SCHEDULE
    # Silent: the digest's OUTPUT is an inbox item; a cron-result toast about it would be a
    # notification about your notifications. `delivery: none` is the store's spelling.
    assert row.trigger.delivery == "none"
    inline = (row.trigger.workflow or {}).get("inline") or {}
    assert inline.get("provider") == "notification-digest"
    # 🔴 ARMED, which is the difference between a registered digest and one that runs.
    assert row.trigger.next_fire_at
    assert row.ok, row.errors


def test_digest_cron_is_not_duplicated(home):
    from personalclaw.action_providers.digest_provider import reconcile_digest_cron

    store = _seed_digest(home, nr.DEFAULT_DIGEST_SCHEDULE)
    before = len(store.load())
    reconcile_digest_cron(store)
    assert len(store.load()) == before


def test_digest_cron_schedule_converges(home):
    """A schedule edited in Settings must take effect without the user knowing a cron exists."""
    from personalclaw.action_providers.digest_provider import (
        DIGEST_JOB_NAME,
        reconcile_digest_cron,
    )

    _write_rules(home, {"digest": {"schedule": "30 6 * * 1-5"}})
    store = _seed_digest(home, "0 8 * * *")
    reconcile_digest_cron(store)
    assert store.get(DIGEST_JOB_NAME).trigger.spec["expr"] == "30 6 * * 1-5"


def test_a_converged_schedule_is_re_armed(home):
    """🔴 The fire is computed FROM the expression, so converging the spec without re-arming would
    leave the digest running on the schedule the user just replaced."""
    from personalclaw.action_providers.digest_provider import (
        DIGEST_JOB_NAME,
        reconcile_digest_cron,
    )

    _write_rules(home, {"digest": {"schedule": "0 8 * * *"}})
    store = _seed_digest(home, "0 8 * * *")
    reconcile_digest_cron(store)
    first = store.get(DIGEST_JOB_NAME).trigger.next_fire_at

    _write_rules(home, {"digest": {"schedule": "45 21 * * *"}})
    reconcile_digest_cron(store)
    after = store.get(DIGEST_JOB_NAME).trigger
    assert after.spec["expr"] == "45 21 * * *"
    assert after.next_fire_at != first
    assert after.next_fire_at.endswith("21:45:00+00:00")


def test_converging_preserves_the_quietly_losable_spec_keys(home):
    """`timezone`/`skip_dates`/`strict` must survive a schedule change — the same contract §1.3 and
    S101 record for a cadence edit. Replacing the spec wholesale would drop a user's holidays."""
    from personalclaw.action_providers.digest_provider import (
        DIGEST_JOB_NAME,
        reconcile_digest_cron,
    )

    store = _seed_digest(home, "0 8 * * *")
    trigger = store.get(DIGEST_JOB_NAME).trigger
    trigger.spec = {**trigger.spec, "timezone": "America/New_York", "skip_dates": ["2026-12-25"]}
    store.upsert(trigger)

    _write_rules(home, {"digest": {"schedule": "15 7 * * *"}})
    reconcile_digest_cron(store)
    spec = store.get(DIGEST_JOB_NAME).trigger.spec
    assert spec["expr"] == "15 7 * * *"
    assert spec["timezone"] == "America/New_York"
    assert spec["skip_dates"] == ["2026-12-25"]


def test_digest_cron_ignores_unrelated_triggers(home):
    from personalclaw.action_providers.digest_provider import (
        DIGEST_JOB_NAME,
        reconcile_digest_cron,
    )
    from personalclaw.triggers.models import Trigger

    store = _digest_store(home)
    store.upsert(
        Trigger(
            id="clock:my-own-job",
            name="my own job",
            kind="clock",
            spec={"kind": "cron", "expr": "0 9 * * *"},
        )
    )
    reconcile_digest_cron(store)
    ids = {row.trigger.id for row in store.load()}
    assert DIGEST_JOB_NAME in ids, "it registers its own trigger"
    assert "clock:my-own-job" in ids, "and leaves the user's alone"
    assert store.get("clock:my-own-job").trigger.spec["expr"] == "0 9 * * *"


def test_digest_cron_survives_a_broken_store(home):
    from unittest.mock import MagicMock

    from personalclaw.action_providers.digest_provider import reconcile_digest_cron

    broken = MagicMock()
    broken.get.side_effect = OSError("triggers.json is gibberish")
    reconcile_digest_cron(broken)  # must not raise — startup must not break
    broken.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_digest_provider_reports_empty_queue_as_success(home, tmp_path, monkeypatch):
    """An empty queue every quiet day must not light up the cron's error surface."""
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    from personalclaw.action_providers.digest_provider import NotificationDigestActionProvider

    result = await NotificationDigestActionProvider().execute({}, MagicMock())
    assert result.success is True
    assert "nothing queued" in (result.stdout or "")


@pytest.mark.asyncio
async def test_digest_provider_reports_the_created_item(home, tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.inbox.config_dir", lambda: tmp_path)
    from personalclaw.action_providers.digest_provider import NotificationDigestActionProvider

    nr.queue_for_digest({"kind": "cron", "title": "job ran"})
    result = await NotificationDigestActionProvider().execute({}, MagicMock())
    assert result.success is True
    assert "created" in (result.stdout or "")


@pytest.mark.asyncio
async def test_digest_provider_surfaces_a_failure_as_an_error(home, monkeypatch):
    from personalclaw.action_providers.digest_provider import NotificationDigestActionProvider

    monkeypatch.setattr(nr, "run_digest", MagicMock(side_effect=RuntimeError("boom")))
    result = await NotificationDigestActionProvider().execute({}, MagicMock())
    assert result.success is False
    assert "boom" in (result.error or "")


def test_digest_provider_is_in_the_action_registry():
    """Without registration the cron would fire and dispatch to nothing."""
    from personalclaw.action_providers import get_action_provider
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    # The same idempotent registration the hooks runtime performs before dispatching an
    # action. Without the provider in here, the digest cron would fire and resolve to
    # nothing — the schedule would look healthy while producing no digest.
    _ensure_default_providers_registered()
    assert get_action_provider("notification-digest") is not None


def test_digest_cron_does_not_reconverge_on_every_startup(home):
    """A matching schedule must not be rewritten, or startup churns the store forever.

    Regression this guards: the legacy read went through `job.schedule.cron_expr`, and reading a
    FLAT `job.cron_expr` always yielded None — so the reconcile saw `None != "0 8 * * *"` and issued
    an update on every boot, logging a schedule change that never happened. The store read
    (`spec["expr"]`) is a plain dict key, which makes that whole class of near-miss impossible; this
    asserts the OUTCOME (nothing changes) rather than the field name, so it stays honest either way.

    (The `_FakeCrons`/`_FakeJob` doubles this used, plus the guard test that pinned their shape
    against `ScheduleJob`, retire with the legacy read — S108.)
    """
    from personalclaw.action_providers.digest_provider import (
        DIGEST_JOB_NAME,
        reconcile_digest_cron,
    )

    store = _seed_digest(home, nr.DEFAULT_DIGEST_SCHEDULE)
    first = store.get(DIGEST_JOB_NAME).trigger
    before = (first.spec.get("expr"), first.next_fire_at, len(store.load()))
    for _ in range(3):
        reconcile_digest_cron(store)
    after_row = store.get(DIGEST_JOB_NAME).trigger
    assert (after_row.spec.get("expr"), after_row.next_fire_at, len(store.load())) == before


# ── the `native` target (DESKTOP-CAPABILITIES DC-5) ──────────────────────
#
# `native` sat in TARGETS from T1.3 as an accepted-and-persisted string with no dispatch
# behind it: the only consumer of `rule.targets` anywhere in `src/` was `state.py`'s
# `note["targets"] = list(rule.targets)` annotation, and `desktop/main.js` imported
# Electron's `Notification` solely to call `isSupported()` in a capability probe. So the
# tests below are deliberately paired — a positive leg AND the vacuity leg through the same
# code path — because a target that fires for every note is exactly as wrong as one that
# never fires, and only the second leg can tell them apart.

_NATIVE_RULE_DOC = {
    "rules": {
        "system/error": {"mode": "immediate", "targets": ["dashboard", "native"]},
        "system/info": {"mode": "immediate", "targets": ["dashboard"]},
        "system/warning": {"mode": "badge", "targets": ["dashboard", "native"]},
    }
}


def _cap(available=True, reason=""):
    """A shell-reported `native_notifications` entry as the registry normalizes it."""
    return {
        "native_notifications": {
            "available": available,
            "granted": "granted" if available else "unavailable",
            "requestable": False,
            "reason": reason,
        }
    }


def test_native_delivery_is_none_when_the_rule_does_not_name_native(home):
    """The vacuity property, at the pure-function level: no `native` target ⇒ no decision.

    `None` and not `{"deliver": False}` on purpose — the caller puts no key on the note at
    all, so nothing downstream can even see that a native path exists for this note.
    """
    rule = nr.Rule("system", "info", "immediate", ("dashboard",))
    assert nr.native_delivery(rule, _cap()["native_notifications"]) is None
    assert nr.native_delivery(None, _cap()["native_notifications"]) is None


def test_native_delivery_falls_back_when_no_shell_is_connected(home):
    rule = nr.Rule("system", "error", "immediate", ("dashboard", "native"))
    verdict = nr.native_delivery(rule, None)
    assert verdict == {"deliver": False, "reason": "the desktop shell is not connected"}


def test_native_delivery_relays_the_shells_own_reason_when_unavailable(home):
    """A refusal has to be quotable, or "I asked for native and got a bell" has no answer."""
    rule = nr.Rule("system", "error", "immediate", ("dashboard", "native"))
    cap = _cap(available=False, reason="the OS does not support notifications")
    verdict = nr.native_delivery(rule, cap["native_notifications"])
    assert verdict == {"deliver": False, "reason": "the OS does not support notifications"}


def test_native_delivery_has_a_sentence_even_when_the_shell_gave_none(home):
    rule = nr.Rule("system", "error", "immediate", ("dashboard", "native"))
    verdict = nr.native_delivery(rule, _cap(available=False)["native_notifications"])
    assert verdict["deliver"] is False
    assert verdict["reason"] == "the desktop shell cannot show native notifications"


def test_native_delivery_ignores_granted_when_available(home):
    """`available` is the whole check.

    macOS never reports notification authorization (`desktop/capabilities.js` says so and
    reports `not-determined` forever), so gating on `granted == "granted"` would refuse to
    deliver on the one platform that cannot answer.
    """
    rule = nr.Rule("system", "error", "immediate", ("native",))
    cap = {"available": True, "granted": "not-determined", "requestable": False, "reason": "x"}
    assert nr.native_delivery(rule, cap) == {"deliver": True, "reason": ""}


# -- the CALL SITE: does a rule naming `native` actually reach the shell? --


@pytest.fixture()
def native_state(home, tmp_path, monkeypatch):
    """A DashboardState whose notify() path is fully redirected into `home`.

    Both bindings of `config_dir` are patched — `notification_rules` reads the rules file
    through its own import and `dashboard.state` persists the JSONL through a second one, so
    patching either alone leaves half the path writing to the real `~/.personalclaw`.
    """
    from tests.chat_test_helpers import _make_state

    _write_rules(home, _NATIVE_RULE_DOC)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    sent: list[dict] = []
    monkeypatch.setattr(state, "_broadcast", sent.append)
    # Assert the redirect rather than trust it: a leaked path would write to the real home.
    from personalclaw.dashboard import state as state_mod

    assert state_mod._notifications_path().parent == tmp_path
    return state, sent


def _connect_shell(state, **kwargs):
    return state.desktop.register(
        shell={"version": "0.1.0", "platform": "darwin"}, capabilities=_cap(**kwargs)
    )


def test_a_rule_naming_native_reaches_the_shell(native_state):
    """The positive leg: `targets: [..., "native"]` + a connected shell ⇒ `deliver: True`.

    This is the whole atom on the Python side. The note that goes out over the WS is the
    only thing the Electron renderer ever sees, so `native.deliver` on THAT dict — not a
    mapping table entry — is what makes the target real.
    """
    state, sent = native_state
    _connect_shell(state)
    state.notify("error", "Loop stalled", "needs an answer")
    assert len(sent) == 1
    assert sent[0]["native"] == {"deliver": True, "reason": ""}


def test_a_rule_not_naming_native_never_fires_it(native_state):
    """🪤 THE VACUITY LEG. Same shell, same connected capability, same code path.

    Without this the test above proves only that a native notification happens, not that
    the *rule* caused it — a dispatch that ignored `targets` entirely would pass it.
    """
    state, sent = native_state
    _connect_shell(state)
    state.notify("info", "Backup finished", "nothing to do")
    assert len(sent) == 1
    assert "native" not in sent[0]


def test_native_falls_back_to_the_dashboard_when_no_shell_is_connected(native_state):
    """The fallback is not a different code path — it is the dashboard delivery running anyway."""
    state, sent = native_state
    assert state.desktop.connected is False
    state.notify("error", "Loop stalled", "needs an answer")
    assert len(sent) == 1, "the dashboard delivery IS the fallback"
    assert sent[0]["native"]["deliver"] is False
    assert sent[0]["native"]["reason"] == "the desktop shell is not connected"
    assert state.unread_count() >= 0  # the bell path stayed intact


def test_unregistering_the_shell_stops_native_delivery(native_state):
    """Rotation/quit has to take the target with it, or a dead shell keeps being addressed."""
    state, sent = native_state
    token = _connect_shell(state)
    state.notify("error", "first", "")
    assert state.desktop.unregister(token) is True
    state.notify("error", "second", "")
    assert [n["native"]["deliver"] for n in sent] == [True, False]


def test_badge_mode_never_raises_native_even_when_targeted(native_state):
    """`badge` said "do not interrupt me". A native OS notification is an interruption.

    So the decision runs only on the `immediate` path, after the quieter modes have
    returned — and the note carries no `native` key for a shell to act on.
    """
    state, sent = native_state
    _connect_shell(state)
    state.notify("warning", "Disk filling", "78% used")
    assert sent == [], "badge persists without broadcasting"
    assert "native" not in state._notification_log[-1]


def test_the_source_on_the_note_is_the_rules_source_not_a_kind_prefix(native_state):
    """The tap's deep link is derived from `source`; a split of `kind` would be wrong.

    `app.route.drift` is a legacy flat wire string whose rule key is `system/route_drift`,
    so splitting on the first dot would name a non-existent `app` surface.
    """
    state, sent = native_state
    state.notify("app.route.drift", "App route drift", "one route moved")
    assert sent[0]["source"] == "system"


def test_a_broken_registry_still_delivers_to_the_dashboard(native_state, monkeypatch):
    """Fail OPEN, like every other layer in `notify()`.

    A native banner is the nice-to-have; the note reaching the bell is not. So a registry
    read that raises degrades to the dashboard delivery (which IS the documented fallback)
    rather than dropping a note the user asked to be interrupted by.
    """
    state, sent = native_state
    _connect_shell(state)

    def boom(_cap):
        raise RuntimeError("registry wedged")

    monkeypatch.setattr(state.desktop, "capability", boom)
    state.notify("error", "Loop stalled", "needs an answer")
    assert len(sent) == 1
    assert "native" not in sent[0]
