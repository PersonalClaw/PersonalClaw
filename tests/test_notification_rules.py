"""Per-(source, kind) notification rules.

The most important tests in this file are the ones under "with no rules": an owner who has
never opened Settings → Notifications is interrupted only by approvals, questions and errors,
and every other notification waits in the bell, listed and counted. Each kind's registered
default says which (``notification_kinds``); what these pin is that ``notify()`` delivers by it,
and that the owner's own choices (a Notify row, a keyword, their name) still raise any kind to a
toast.

Everything else here is about the second-order risk: a *policy* layer that can silence the
system. Every failure path must fail OPEN, and there are more of those paths than is
obvious — missing file, malformed JSON, wrong root type, wrong rule type, unknown mode,
unknown target, and a rules file written by a newer build.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from table_rows import row

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


# ── with no rules ───────────────────────────────────────────────────────


def test_no_rules_file_resolves_every_kind_to_its_registered_default(home):
    """No rules file ⇒ every registered kind resolves to its registered default, dashboard only.

    Targets and conditions are universal: "an unconfigured rule adds no delivery channel and
    escalates nothing", which must hold for every kind including a brand-new one.
    """
    for k in nk.all_kinds():
        rule = nr.resolve_rule(k.source, k.kind)
        assert rule.mode == k.default_mode, f"{k.key} did not resolve to its registered default"
        assert rule.targets == ("dashboard",)
        assert rule.conditions.matches("anything at all") == ""


def test_with_no_rules_only_the_wire_kinds_of_decisions_and_errors_ping(home):
    """`notify()` is handed the flat wire string, so each must reach its OWN kind's rule: one that
    fell through to `system/generic` would take generic's badge, and a question would wait in the
    bell unseen. Of every wire string, only the decisions' and the errors' reach an `immediate`
    rule; the monthly recap and a loop's progress wait for the digest, and everything else is a
    badge."""
    by_mode: dict[str, list[str]] = {}
    for flat in nk._WIRE_TO_PAIR:
        by_mode.setdefault(nr.resolve_rule_for_legacy(flat).mode, []).append(flat)
    assert sorted(by_mode.pop("immediate")) == [
        "agent_request",
        "approval",
        "cron_failed",
        "error",
        "loop_failed",
        "needs_input",
        "room_paused",
        "run_review",
        "trigger_import",
    ]
    assert sorted(by_mode.pop("digest")) == [nk.LOOP, nk.USAGE_RECAP]
    assert set(by_mode) == {"badge"}


# ── mode resolution ─────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["never", "badge", "immediate", "digest"])
def test_stored_mode_is_honored(home, mode):
    # A kind whose default is another mode, so the answer can only come from the stored rule.
    source, kind = ("cron", "failed") if mode == "badge" else ("cron", "result")
    assert nk.resolve_kind(source, kind).default_mode != mode
    _write_rules(home, {"rules": {f"{source}/{kind}": {"mode": mode}}})
    assert nr.resolve_rule(source, kind).mode == mode


def test_unrelated_kinds_keep_their_defaults(home):
    """One configured row must not change any other row."""
    _write_rules(home, {"rules": {"cron/result": {"mode": "never"}}})
    assert nr.resolve_rule("cron", "result").mode == "never"
    assert nr.resolve_rule("hook", "fired").mode == "badge"


# ── fail-open on every corruption path ──────────────────────────────────
#
# Each falls back to the kind's registered default — a badge for `cron/result`, which is listed
# and counted in the bell — and never to `never`.


def _reads_as_no_rules() -> None:
    """A rules file that cannot be read gives every kind its own default: a job's result is a
    badge, and a question still interrupts. A fallback that made every kind a badge would leave
    the question unseen in the bell."""
    assert nr.resolve_rule("cron", "result").mode == "badge"
    assert nr.resolve_rule("loop", "needs_input").mode == "immediate"


def test_missing_file_falls_back_to_defaults(home):
    assert nr.load_rules() == {}
    _reads_as_no_rules()


def test_malformed_json_falls_back_to_defaults(home):
    (home / "entity_settings" / "notification_rules.json").write_text("{not json", encoding="utf-8")
    assert nr.load_rules() == {}
    _reads_as_no_rules()


@pytest.mark.parametrize("root", ["[]", '"a string"', "42", "null"])
def test_non_object_root_falls_back_to_defaults(home, root):
    (home / "entity_settings" / "notification_rules.json").write_text(root, encoding="utf-8")
    _reads_as_no_rules()


@pytest.mark.parametrize("bad", ["[]", '"never"', "5", "null"])
def test_non_object_rule_falls_back_to_default_mode(home, bad):
    _write_rules(home, {"rules": {"cron/result": json.loads(bad)}})
    assert nr.resolve_rule("cron", "result").mode == "badge"


def test_unknown_mode_falls_back_to_default_not_silence(home, caplog):
    """A typo'd mode must not become an accidental `never`."""
    _write_rules(home, {"rules": {"cron/result": {"mode": "nevr"}}})
    with caplog.at_level("WARNING"):
        rule = nr.resolve_rule("cron", "result")
    assert rule.mode == "badge"
    assert "unknown mode" in caplog.text


def test_non_dict_rules_container_falls_back(home):
    _write_rules(home, {"rules": ["cron/result"]})
    _reads_as_no_rules()


def test_a_good_mode_survives_a_malformed_targets_list(home):
    """Per-FIELD fallback: an unrelated typo must not discard a deliberate mode."""
    _write_rules(home, {"rules": {"cron/result": {"mode": "never", "targets": "dashboard"}}})
    rule = nr.resolve_rule("cron", "result")
    assert rule.mode == "never"
    assert rule.targets == ("dashboard",)


def test_unregistered_kind_resolves_through_the_generic_fallback(home):
    """An unknown pair still gets a rule (fail-open), not an exception: a badge, in the bell."""
    rule = nr.resolve_rule("no-such-source", "no-such-kind")
    assert rule.mode == "badge"
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
    assert c.matches("hey jordan, look", "Jordan Marlow") == "name mention: Jordan"
    assert c.matches("jordanian politics", "Jordan Marlow") == ""


def test_a_name_mention_names_the_part_it_matched_as_the_owner_wrote_it():
    """The reason carries the name, as a keyword's carries the keyword: an inbox alert's note
    keeps only the first 200 characters of the message, so the reason is where the name a
    mention matched further on is still in the note's own text, which `notify()` re-matches."""
    c = nr.Conditions(name_mention=True)
    assert c.matches("thanks MARLOW", "Jordan Marlow") == "name mention: Marlow"
    assert c.matches("ask jordan or marlow", "Jordan Marlow") == "name mention: Jordan"


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
    of the retired implementation (the original body of `inbox.evaluate_alert`, kept here as
    the oracle) — so a user whose keywords were backfilled cannot silently start getting
    different alerts.

    The one difference is deliberate and exact: a name mention now also says which part of the
    name it matched, as the owner wrote it, so each name case states that part.
    """
    import re

    def legacy(text: str, keywords: list[str], name_mention: bool, user_name: str) -> str:
        """The original `inbox.evaluate_alert` body, verbatim."""
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

    # The last field is the part of the name a mention matched, for the cases where one does.
    cases = [
        ("ship the deploy tonight", ["deploy"], False, "Jordan Marlow", ""),
        ("nothing to see", ["deploy"], False, "Jordan Marlow", ""),
        ("hey Jordan can you look", [], True, "Jordan Marlow", "Jordan"),
        ("jordanian politics", [], True, "Jordan Marlow", ""),
        ("REDEPLOY now", ["deploy"], False, "", ""),
        ("", ["deploy"], True, "Jordan Marlow", ""),
        ("a de facto standard", [], True, "J de Vries", ""),
        ("  ", ["x"], True, "Jordan", ""),
        ("Deploy DEPLOY deploy", ["DePlOy"], False, "", ""),
        ("hey marlow", [], True, "Jordan Marlow", "Marlow"),
        ("nothing", [], False, "Jordan Marlow", ""),
        ("deploy, Jordan", ["deploy"], True, "Jordan Marlow", ""),
    ]
    for text, keywords, name_mention, user, part in cases:
        want = legacy(text, keywords, name_mention, user)
        if want == "name mention":
            want = f"{want}: {part}"
        else:
            assert not part, f"{text!r}: the oracle matched no name, so no part is expected"
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


# ── per-kind push sound ─────────────────────────────────────────────────


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


def test_rules_document_has_a_row_for_every_configurable_kind(home):
    doc = nr.rules_document()
    assert {r["key"] for r in doc["rules"]} == {k.key for k in nk.configurable_kinds()}


def test_resolution_only_and_retired_rules_are_tolerated_but_not_rendered(home):
    """Historical rules may outlive their row; reads stay fail-open and the matrix stays honest."""
    _write_rules(
        home,
        {
            "rules": {
                "system/session": {"mode": "never"},
                "loop/stalled": {"mode": "badge"},
                "hook/fired": {"mode": "digest"},
            }
        },
    )
    rows = {r["key"]: r for r in nr.rules_document()["rules"]}
    assert "system/session" not in rows
    assert "loop/stalled" not in rows
    assert rows["hook/fired"]["mode"] == "digest"
    assert nr.resolve_rule("system", "session").mode == "never"


def test_rules_document_marks_which_rows_are_configured(home):
    _write_rules(home, {"rules": {"cron/result": {"mode": "badge"}}})
    rows = {r["key"]: r for r in nr.rules_document()["rules"]}
    assert rows["cron/result"]["configured"] is True
    assert rows["hook/fired"]["configured"] is False


def test_rules_document_exposes_the_default_alongside_the_effective_mode(home):
    """The UI needs both to show "changed from default"."""
    _write_rules(home, {"rules": {"cron/result": {"mode": "never"}}})
    row = next(r for r in nr.rules_document()["rules"] if r["key"] == "cron/result")
    assert (row["mode"], row["default_mode"]) == ("never", "badge")


def test_with_no_rules_the_matrix_shows_notify_only_on_decisions_and_errors(home):
    """What Settings → Notifications draws for an owner who has set nothing: Notify on the
    decisions and the errors, Digest on the monthly recap and on a loop's progress, Badge on every
    other row."""
    rows = nr.rules_document()["rules"]
    assert not any(r["configured"] for r in rows)
    pinging = sorted(r["key"] for r in rows if r["mode"] == "immediate")
    assert pinging == sorted(
        k.key for k in nk.configurable_kinds() if k.decision or k.default_severity == nk.SEV_ERROR
    )
    assert len(pinging) == 9
    assert [r["key"] for r in rows if r["mode"] == "digest"] == [
        "loop/progress",
        "system/usage_recap",
    ]
    assert {r["mode"] for r in rows} == {"immediate", "digest", "badge"}
    assert sum(r["mode"] == "badge" for r in rows) == len(rows) - 11


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


# ── The inbox-alert backfill ────────────────────────────────────────────
#
# Not a `lifecycle/migrations/m_*.py`: it is an idempotent
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
    """The owner set keywords, not a mode: both rows keep their default, a badge, and a message
    that matches is what raises the alert to a toast. A stored Notify would make every agent
    message interrupt on a home that came through the backfill, and on no other home."""
    _write_legacy(legacy_home, {"alert_keywords": ["deploy", "prod"]})
    for wire in (nk.INBOX_ALERT, nk.AGENT):
        rule = nr.resolve_rule_for_legacy(wire)
        assert rule.conditions.keywords == ("deploy", "prod"), wire
        assert rule.mode == nk.kind_for_legacy(wire).default_mode == "badge", wire
        assert nr.rule_outcome(wire, "the roadmap is up", posture="deliver").mode == "badge", wire
        raised = nr.rule_outcome(wire, "please deploy it", posture="deliver")
        assert (raised.mode, raised.escalated_by) == ("immediate", "keyword: deploy"), wire


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


# ── The digest ──────────────────────────────────────────────────────────


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


# ── The digest cron ─────────────────────────────────────────────────────


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


# 🔴 These tests drove a `_FakeCrons` double, and passed the whole time the digest DID
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


def test_a_converged_schedule_is_re_armed(home, monkeypatch):
    """🔴 The fire is computed FROM the expression, so converging the spec without re-arming would
    leave the digest running on the schedule the user just replaced."""
    from personalclaw.action_providers.digest_provider import (
        DIGEST_JOB_NAME,
        reconcile_digest_cron,
    )

    # The host zone is pinned because the assertion below reads the armed instant as UTC
    # (`21:45:00+00:00` for a `45 21 * * *` row). An absent `spec.timezone` resolves the machine's
    # zone now (#2520), so on a PDT laptop the correct answer is `04:45:00+00:00`. What this test
    # is about is RE-ARMING after a converge, not which zone the digest runs in.
    monkeypatch.setenv("TZ", "UTC")
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
    """`timezone`/`skip_dates`/`strict` must survive a schedule change — the same contract a
    cadence edit keeps. Replacing the spec wholesale would drop a user's holidays."""
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
    against `ScheduleJob`, retire with the legacy read.)
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


# ── the `native` target ──────────────────────────────────────────────────
#
# `native` sat in TARGETS as an accepted-and-persisted string with no dispatch
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

    This is the whole change on the Python side. The note that goes out over the WS is the
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
    state, _sent = native_state
    state.notify("app.route.drift", "App route drift", "one route moved")
    assert state._notification_log[-1]["source"] == "system"


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


# ── "Escalate on name mention" keys on the USER's name ──────────────────
#
# The toggle's hint is "Upgrade when the text mentions you by name". `notify()` checked it against
# `agent.bot_name` — the ASSISTANT's name — while the inbox path read `dashboard.user_name`.
# Measured through the real rules PUT and the real `notify()`: with the assistant named Jarvis,
# "Ada, the disk is at 91%" stayed `badge` and "Jarvis finished the disk check" escalated; on an
# install with no assistant name, nothing escalated at all.


@pytest.fixture()
def mention_state(home, tmp_path, monkeypatch):
    """`notify()` with name-mention ON for a `badge` kind, and a way to set both names.

    `badge`, so an escalation shows as the mode it produces (`immediate`) instead of being a no-op
    on a kind that interrupts anyway. The names are written to the real `config.json`, not stubbed:
    which config field is read IS the defect, so the lookup has to run.
    """
    from personalclaw.config.loader import config_dir, config_path
    from tests.chat_test_helpers import _make_state

    _write_rules(
        home,
        {"rules": {"system/warning": {"mode": "badge", "conditions": {"name_mention": True}}}},
    )
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    monkeypatch.setattr(state, "_broadcast", lambda note: None)

    def names(user: str, assistant: str) -> None:
        doc = {"dashboard": {"user_name": user}, "agent": {"bot_name": assistant}}
        config_dir()  # the home this writes into: finding where the file is makes nothing
        config_path().write_text(json.dumps(doc), encoding="utf-8")

    def deliver(body: str) -> dict:
        state.notify("warning", "Disk check", body)
        return state._notification_log[-1]

    return names, deliver


@pytest.mark.parametrize("assistant", ["", "Jarvis"], ids=["no-assistant-name", "assistant-named"])
def test_a_mention_of_the_users_name_escalates(mention_state, assistant):
    names, deliver = mention_state
    names("Ada Lovelace", assistant)
    note = deliver("Ada, the disk is at 91% and needs you")
    assert note["mode"] == "immediate"
    assert note["escalated_by"] == "name mention: Ada"


def test_a_mention_of_only_the_assistants_name_does_not_escalate(mention_state):
    names, deliver = mention_state
    names("Ada Lovelace", "Jarvis")
    note = deliver("Jarvis finished the disk check")
    assert note["mode"] == "badge"
    assert "escalated_by" not in note


@pytest.mark.parametrize(
    "user",
    ["", "Operator"],
    ids=["no-user-name", "skip-setup-placeholder"],
)
def test_with_no_user_name_nothing_escalates_and_nothing_raises(mention_state, user):
    """No name given means no name to match. Through the UI that is usually `Operator`: skipping
    setup commits it (the route guard needs a non-empty name) and Account refuses an empty one, so
    matching it would escalate every note that says "operator" for someone who never chose it."""
    names, deliver = mention_state
    names(user, "Jarvis")
    for body in (
        "Ada, the disk is at 91%",
        "Jarvis finished the disk check",
        "The operator console lost its disk",
    ):
        note = deliver(body)
        assert note["mode"] == "badge", body
        assert "escalated_by" not in note, body


# ── quiet hours over an attention kind (issue #341, bug B) ───────────────


@pytest.fixture()
def quiet_state(home, tmp_path, monkeypatch):
    """A `notify()` path inside quiet hours, with the window forced rather than clocked.

    The window MATH is already covered in `test_entity_settings_routes.py`; forcing it keeps this
    test about what `notify()` does with the verdict, and off the wall clock — a test that only
    reproduces between 22:00 and 08:00 is not a rail.
    """
    from personalclaw.providers import entity_routes as er
    from tests.chat_test_helpers import _make_state

    (tmp_path / "entity_settings" / "notifications.json").write_text(
        json.dumps(
            {"quiet_hours_enabled": True, "quiet_hours_start": "22:00", "quiet_hours_end": "08:00"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(er, "_in_quiet_window", lambda *_a, **_k: True)
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    sent: list[dict] = []
    monkeypatch.setattr(state, "_broadcast", sent.append)
    return state, sent


def test_quiet_hours_records_an_attention_kind_as_a_badge(quiet_state):
    """It used to leave NO record: `notify()` returned before the note was even built.

    So "a loop needs your input", raised overnight, was not logged, not persisted and not
    broadcast — while the durable inbox row still counted toward the badge, which is precisely what
    made the gap invisible. The whole point is the audit trail, so this asserts on the persisted
    log, not on a return value.
    """
    state, sent = quiet_state
    state.notify("needs_input", "Loop needs your input", "refactor-auth")

    assert len(state._notification_log) == 1, "the note was dropped, not recorded"
    note = state._notification_log[-1]
    assert note["kind"] == "needs_input"
    assert note["mode"] == "badge", "quiet hours must not let it interrupt"
    assert note["badge_only"] is True
    assert sent == [], "a badge does not broadcast — that is what keeps quiet hours quiet"


def test_quiet_hours_still_drops_a_non_attention_kind(home, quiet_state):
    """🪤 THE VACUITY LEG. Same window, same code path, a kind of the same severity.

    Without it, the test above would pass against a gate that had simply stopped suppressing
    anything. The carve-out is "this persists a row somebody must answer", not "warning". The
    owner's rule makes it a ping, because a warning with no rule is already a badge (below).
    """
    state, sent = quiet_state
    _write_rules(home, {"rules": {"system/warning": {"mode": "immediate"}}})
    state.notify("warning", "Disk filling", "78% used")
    assert state._notification_log == []
    assert sent == []


def test_quiet_hours_record_a_badge_default_silently(quiet_state):
    """A kind that waits in the bell by default is delivered as its rule says inside quiet hours
    too: recorded and counted, without a ping. The pair to the test above: the same kind, the
    same window, and only the rule differs."""
    state, sent = quiet_state
    state.notify("warning", "Disk filling", "78% used")
    (note,) = state._notification_log
    assert note["mode"] == "badge" and note["badge_only"] is True
    assert sent == []


def test_quiet_hours_lets_an_error_through_untouched(quiet_state):
    """SEV_ERROR rode through quiet hours before and still does, as an `immediate`."""
    state, sent = quiet_state
    state.notify("error", "Loop failed", "refactor-auth")
    assert state._notification_log[-1]["mode"] == "immediate"
    assert len(sent) == 1


def test_quiet_hours_does_not_resurrect_a_never_rule(home, quiet_state):
    """`never` is the user's own instruction and outranks the downgrade.

    The posture only ever makes delivery quieter, so a kind the user muted must stay muted — a
    "record it anyway" that resurrected a `never` would be the policy layer overriding the person.
    """
    state, sent = quiet_state
    _write_rules(home, {"rules": {"loop/needs_input": {"mode": "never"}}})
    state.notify("needs_input", "Loop needs your input", "refactor-auth")
    assert state._notification_log == []
    assert sent == []


# ── with no rules, what reaches you ─────────────────────────────────────
#
# Only approvals, questions and errors interrupt; every other notification waits in the bell,
# listed and counted. Driven through the real `notify()`, so each test reads what the owner meets:
# the `notification` frame that raises a toast (and carries the desktop, push and channel
# targets), or the `notification_logged` frame that only counts the note in the bell.


@pytest.fixture()
def bell_state(home, tmp_path, monkeypatch):
    """`notify()` with no rules file, and every way a note leaves it recorded."""
    from tests.chat_test_helpers import _make_state

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    out: dict[str, list] = {"toast": [], "logged": [], "push": [], "dm": []}
    monkeypatch.setattr(state, "_broadcast", out["toast"].append)
    monkeypatch.setattr(state, "_push_target", lambda kind, note: out["push"].append(note))
    monkeypatch.setattr(state, "_channel_dm_target", out["dm"].append)
    sent_ws = state.broadcast_ws

    def broadcast_ws(event, payload, *args, **kwargs):
        if event == "notification_logged":
            out["logged"].append(payload)
        return sent_ws(event, payload, *args, **kwargs)

    monkeypatch.setattr(state, "broadcast_ws", broadcast_ws)
    assert not (home / "entity_settings" / "notification_rules.json").exists()
    return state, out


def _reset(state, out, home) -> None:
    """What a row of a table below touched, put back, so the next row starts from nothing."""
    state._notification_log.clear()
    for sent in out.values():
        sent.clear()
    (home / "entity_settings" / "notification_rules.json").unlink(missing_ok=True)


def _owner_named(name: str) -> None:
    """Settings → Account → Your name, written where `identity.operator_name` reads it."""
    from personalclaw.config.loader import config_dir, config_path

    config_dir()  # the home this writes into: finding where the file is makes nothing
    config_path().write_text(json.dumps({"dashboard": {"user_name": name}}), encoding="utf-8")


def _served(state) -> dict:
    """`GET /api/notifications` as the bell reads it: the log, and how many of it are unread."""
    import asyncio

    from personalclaw.dashboard.handlers.messaging import api_notifications

    request = MagicMock()
    request.app = {"state": state}
    request.get = lambda key, default=None: default
    return json.loads(asyncio.run(api_notifications(request)).body)


_EVERY_TARGET = list(nr.TARGETS)

#: ``(case, the stored rules or None for no rules file, wire kind, toasts, fires the targets the
#: row names besides the dashboard)``.
_DELIVERY = [
    ("a scheduled job's result", None, nk.CRON, False, False),
    ("a loop's question", None, "needs_input", True, False),
    ("an error", None, nk.ERROR, True, False),
    # Ticking a target in the matrix stores the target alone, so the row keeps its default: a
    # badge, which fires none of the targets it names until the row is Notify.
    (
        "a result on a row with every target ticked",
        {"cron/result": {"targets": _EVERY_TARGET}},
        nk.CRON,
        False,
        False,
    ),
    # One click on the row: the owner's Notify outranks the default, for any kind.
    (
        "a result on a row set to Notify",
        {"cron/result": {"mode": "immediate"}},
        nk.CRON,
        True,
        False,
    ),
    (
        "a result on a row set to Notify with every target ticked",
        {"cron/result": {"mode": "immediate", "targets": _EVERY_TARGET}},
        nk.CRON,
        True,
        True,
    ),
]


def test_with_no_rule_of_yours_only_a_decision_or_an_error_interrupts(bell_state, home, subtests):
    """A scheduled job's result lands in the bell as a badge, listed and counted, and nothing
    fires; a loop's question and an error interrupt. Read as the bell reads it, through
    `GET /api/notifications`. A target ticked on the result's row fires nothing while the row is
    a badge, and the owner's Notify brings the toast back with every target the row names."""
    state, out = bell_state
    assert len(_DELIVERY) == 6, "a row was dropped from the table"
    for case, rules, kind, toasts, fires in _DELIVERY:
        with row(subtests, case=case):
            try:
                if rules is not None:
                    _write_rules(home, {"rules": rules})
                state.notify(kind, case, "body")
                served = _served(state)
                (note,) = served["notifications"]
                assert served["unread"] == 1, f"{case}: the bell does not count it"
                if toasts:
                    assert [n["title"] for n in out["toast"]] == [case], f"{case} did not toast"
                    assert note["mode"] == "immediate" and "badge_only" not in note, note
                    assert out["logged"] == [], f"{case} toasted and was logged quietly too"
                else:
                    assert out["toast"] == [], f"{case} toasted"
                    assert (note["mode"], note.get("badge_only")) == ("badge", True), note
                    assert out["logged"] == [{"ts": note["ts"]}], f"{case}: the bell was not told"
                fired = (len(out["push"]), len(out["dm"]), "native" in note)
                assert fired == ((1, 1, True) if fires else (0, 0, False)), f"{case}: {fired}"
            finally:
                _reset(state, out, home)


def _inbox_alert(state, text: str) -> str:
    """One message through the inbox's own alert path: `evaluate_alert` decides it alerts, and
    `notify_inbox_alert` raises it. Returns the reason, "" when it does not alert."""
    from personalclaw.identity import operator_name
    from personalclaw.inbox import InboxItem, evaluate_alert, notify_inbox_alert

    item = InboxItem(
        id="C1_1",
        channel="C1",
        channel_name="#finance",
        thread_ts=None,
        message=text,
        sender_id="U1",
        sender_name="Sam",
    )
    reason = evaluate_alert(item, operator_name())
    if reason:
        notify_inbox_alert(state, item, reason)
    return reason


#: A message that mentions the owner past the first 200 characters, all the alert's note keeps.
_FAR_MENTION = "The quarterly figures are attached for review. " * 7 + "Ada, can you sign them off?"

#: ``(case, the conditions on the inbox/alert row, message, the reason it alerts with)``. The row
#: stores conditions and no mode, so it stays a badge, and only the condition can make it toast.
_ALERTS = [
    ("a keyword", {"keywords": ["deploy"]}, "Can someone deploy the fix?", "keyword: deploy"),
    ("a name past the note's 200", {"name_mention": True}, _FAR_MENTION, "name mention: Ada"),
]


def test_an_inbox_alert_your_condition_raised_still_interrupts(bell_state, home, subtests):
    """An inbox alert fires only when a condition of the owner's matched the message
    (`inbox.evaluate_alert`), and `notify()` matches the conditions again against the note's own
    text to raise the row's badge to a toast. The note keeps only the first 200 characters of the
    message, so its reason names what matched, the keyword or the part of the name, and a name
    mentioned past that cut still interrupts."""
    state, out = bell_state
    assert _FAR_MENTION.index("Ada") > 200, "the mention is inside what the note keeps"
    _owner_named("Ada Lovelace")
    assert len(_ALERTS) == 2, "a row was dropped from the table"
    for case, conditions, message, reason in _ALERTS:
        with row(subtests, case=case):
            try:
                _write_rules(home, {"rules": {"inbox/alert": {"conditions": conditions}}})
                assert nr.resolve_rule("inbox", "alert").mode == "badge", "the row itself pings"
                assert _inbox_alert(state, message) == reason, f"{case} did not alert as named"
                (note,) = state._notification_log
                assert out["toast"] == [note], f"{case} did not toast: {note}"
                assert note["escalated_by"] == reason
            finally:
                _reset(state, out, home)


# ── the pings the owner set up wait in the bell too ─────────────────────
#
# A `notify` action ("ping me when this happens") and a task's due notice are notices like any
# other: in the bell, counted, with no toast, until their row is Notify. An error is the
# exception, as everywhere.


def test_a_notify_action_waits_in_the_bell_unless_it_reports_an_error(bell_state, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from personalclaw.action_providers import notify_provider
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.notify_provider import NotifyActionProvider

    state, out = bell_state
    monkeypatch.setattr(
        notify_provider, "get_action_services", lambda: SimpleNamespace(state=state)
    )

    def act(kind: str, title: str) -> None:
        config = {"kind": kind, "title_template": title}
        ctx = ActionContext(event="Stop", context="", payload={})
        assert asyncio.run(NotifyActionProvider().execute(config, ctx)).success

    act("info", "The export finished")
    (note,) = state._notification_log
    assert note["badge_only"] is True and out["toast"] == []
    assert out["logged"] == [{"ts": note["ts"]}]

    act("error", "The export failed")
    assert [n["title"] for n in out["toast"]] == ["The export failed"]


def test_a_tasks_due_notice_waits_in_the_bell(bell_state, monkeypatch):
    from datetime import datetime

    from personalclaw.tasks import due_notices
    from personalclaw.tasks.models import Task

    state, out = bell_state
    monkeypatch.setattr(due_notices, "_owner", lambda: "")
    task = Task(id="t-1", title="Ship the release notes", due="2026-10-02")
    now = datetime(2026, 10, 1, 9, 0).timestamp()

    assert due_notices.sweep([task], notify=state.notify, now=now) == ["t-1"]
    (note,) = state._notification_log
    assert note["title"] == "Due tomorrow: Ship the release notes"
    assert note["badge_only"] is True and out["toast"] == []
    assert out["logged"] == [{"ts": note["ts"]}]


# ── a loop's progress waits for the morning digest ──────────────────────
#
# The one frequent kind: a loop at the default 2-minute cycle reports about 30 times an hour. In
# the bell its notes buried what does wait on the owner and, past the log's 400, trimmed the
# evening's failures out of it.


def test_a_loops_progress_waits_for_the_digest_not_the_bell(bell_state):
    """Each cycle's heads-up goes to the morning digest, which counts it, and nothing is listed,
    counted or toasted in the bell meanwhile."""
    from personalclaw.loop.watchdog import LoopWatchdog

    state, out = bell_state
    watchdog = LoopWatchdog(state, MagicMock())
    for cycle in (1, 2, 3):
        watchdog._notify_progress("a1b2c3d4e5f6", cycle, 30)
    served = _served(state)
    assert (served["notifications"], served["unread"]) == ([], 0), served
    assert out["toast"] == [] and out["logged"] == []
    assert nr.run_digest(state), "the morning digest has nothing to count"
    (digest,) = state._notification_log
    assert digest["title"] == "Digest — 3 notifications"
    assert "**Loop progress** — 3" in digest["body"]
