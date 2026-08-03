"""`when:` → trigger kind routing (§4 — S92).

§4's NL contract is one line with two destinations: "`when: "every weekday at 9"` routes through
`nl_to_cron`; `when: "when a file in ~/notes changes"` → file kind." Nothing routed between them.

**🔴 THE FAILURE THESE TESTS PIN.** `nl_to_cron` is cron-shaped by construction. Measured before
writing the module:

    parse_cron_response("when a file in ~/notes changes")
      -> ("", "Could not parse a 5-field cron expression from: 'when a file in ~/note…'")

The error is the GOOD case. The bad case is a model asked for a cron expression while handed a
file-watch request answering `* * * * *` — which VALIDATES (asserted below), schedules, and turns
"when a file changes" into a per-minute LLM turn. So routing must happen before the cron converter
is consulted, and an unroutable request must refuse rather than default to a schedule.

Two more defects the probe caught before any test existed, both pinned here: a URL routing to
`file` (producing the impossible glob `//example.com/page/**`), and a change verb that reached the
dedup hint but not the routing check.
"""

from __future__ import annotations

import pytest

from personalclaw.triggers.nl_kind import route

# ── 🔴 the per-minute-poll trap ──


def test_criterion_2s_own_sentence_routes_to_the_file_kind():
    """🔴 The bar §4 sets, verbatim from criterion 2. Before this module the same sentence reached
    only `nl_to_cron`, whose single output shape is a cadence."""
    r = route("when a file in ~/notes changes")
    assert r.kind == "file"
    assert r.spec["paths"] == ["~/notes/**"]
    assert r.ok


def test_a_star_cron_really_would_have_validated():
    """🔴 Proof the trap is real, not theoretical: `* * * * *` passes the cron validator, so a model
    nudged toward cron with a file-watch request produces a per-minute poll that schedules
    cleanly and reports success."""
    from personalclaw.nl_to_cron import parse_cron_response

    expr, err = parse_cron_response("* * * * *")
    assert (expr, err) == ("* * * * *", "")


def test_the_cron_path_cannot_parse_a_file_request():
    """The other half of the same finding — the good case, pinned so a future change to
    `nl_to_cron` cannot start silently accepting event language."""
    from personalclaw.nl_to_cron import parse_cron_response

    expr, err = parse_cron_response("when a file in ~/notes changes")
    assert expr == ""
    assert err


def test_a_file_request_never_carries_a_cadence():
    """The routing property that keeps the converter unreachable: `cadence` is what the caller
    hands to `nl_to_cron`, and a file route must leave it empty."""
    assert route("when a file in ~/notes changes").cadence == ""


# ── ordering: event cues beat cadence cues ──


def test_every_file_in_a_path_is_a_watch_not_a_cadence():
    """🔴 "every" is a cadence cue AND the first word of this file request. Checking cadence first
    would route it to `clock` — the exact mis-route that becomes a poll."""
    r = route("every file in ~/notes/**/*.md changes")
    assert r.kind == "file"
    assert r.spec["paths"] == ["~/notes/**/*.md"]


def test_a_real_cadence_still_routes_to_clock():
    r = route("every weekday at 9")
    assert r.kind == "clock"
    assert r.cadence == "every weekday at 9"


@pytest.mark.parametrize(
    "text", ["every 30 minutes", "at 5pm", "every day at midnight", "daily at 7am", "hourly"]
)
def test_cadences_route_to_clock(text):
    assert route(text).kind == "clock"


def test_the_clock_route_passes_the_text_through_verbatim():
    """`nl_to_cron` owns cadence→expr; this module must not paraphrase on the way, or the
    converter sees a different request than the user typed."""
    assert route("the first of each month").cadence == "the first of each month"


# ── 🔴 a URL is not a path ──


def test_a_url_routes_to_web_watch_not_file():
    """🔴 MEASURED DEFECT. The path regex matches `//example.com/page` inside a URL, so this
    request routed to `file` with the glob `//example.com/page/**` — a filesystem watch on a path
    that cannot exist, which would never fire and never explain why."""
    r = route("when https://example.com/page changes")
    assert r.kind == "web_watch"
    assert r.spec["url"] == "https://example.com/page"
    assert "paths" not in r.spec


def test_a_url_with_trailing_punctuation_is_clean():
    assert route("watch https://news.site/feed.").spec["url"] == "https://news.site/feed"


def test_an_http_url_also_routes_to_web_watch():
    assert route("when http://intranet/status changes").kind == "web_watch"


# ── 🔴 refuse rather than guess a root ──


def test_a_pathless_file_request_refuses_instead_of_guessing():
    """🔴 Watching the wrong tree is worse than asking. `~/**` is the `broad_watch_glob` failure
    `automation doctor` flags, and a cwd-rooted guess watches whatever directory the gateway
    happened to start in."""
    r = route("when a file changes")
    assert not r.ok
    assert "which path" in r.error.lower()
    assert r.kind == ""


def test_an_unroutable_request_does_NOT_default_to_a_schedule():
    """🔴 The single most important negative: a default of "probably a schedule" is exactly how
    "when a file changes" becomes `* * * * *`."""
    r = route("banana")
    assert r.kind == ""
    assert r.error
    assert r.cadence == ""


def test_an_empty_when_is_an_error_not_a_route():
    assert route("").error
    assert route("   ").kind == ""


def test_the_error_names_both_shapes_the_user_could_have_typed():
    """An error that only says "I don't understand" leaves the user guessing at the grammar."""
    err = route("banana").error
    assert "every weekday" in err and "file" in err


# ── path normalization ──


def test_a_bare_directory_becomes_a_glob():
    """`file_watch` expands GLOBS, not directory roots: a bare `~/notes` would watch the directory
    inode and miss every file inside it — a silent no-fire."""
    assert route("when a file in ~/notes changes").spec["paths"] == ["~/notes/**"]


def test_an_explicit_glob_is_left_alone():
    assert route("when ~/notes/**/*.md changes").spec["paths"] == ["~/notes/**/*.md"]


def test_a_concrete_filename_is_watched_exactly():
    """A named file with an extension is not a directory, so appending `/**` would watch nothing."""
    assert route("when ~/notes/todo.md changes").spec["paths"] == ["~/notes/todo.md"]


def test_an_absolute_path_works():
    assert route("when a file in /var/log changes").spec["paths"] == ["/var/log/**"]


def test_trailing_sentence_punctuation_is_stripped_from_a_path():
    assert route("when a file in ~/notes changes.").spec["paths"] == ["~/notes/**"]


def test_multiple_paths_are_all_carried():
    r = route("when a file in ~/notes or ~/docs changes")
    assert r.spec["paths"] == ["~/notes/**", "~/docs/**"]


def test_a_bare_slash_or_tilde_is_not_a_path():
    """Watching `/` or the whole home directory is the broad-glob failure; neither counts as the
    user having named a path, so this must fall through to the refusal."""
    assert route("when a file in / changes").kind == ""


# ── 🔴 the change-verb vocabulary ──


def test_edited_content_routes_and_sets_the_dedup_hint():
    """🔴 MEASURED DEFECT: `edited` appeared in the dedup-hint check but NOT the routing check, so
    this request did not route at all — it fell through to the generic "could not tell" error
    despite naming a path and a change."""
    r = route("when the content of ~/notes/todo.md is edited")
    assert r.kind == "file"
    assert r.spec["dedup"] == "content"


@pytest.mark.parametrize(
    "verb",
    ["changes", "changed", "modified", "edited", "updated", "appears", "added", "gets a new file"],
)
def test_every_change_verb_routes_a_pathed_request(verb):
    """One vocabulary shared by routing and the dedup hint, so the two cannot drift again."""
    assert route(f"when ~/notes {verb}").kind == "file"


def test_a_plain_watch_does_not_set_a_content_dedup():
    """`dedup: content` means hash every file each poll. Defaulting it on would make a `~/**`
    watch expensive without the user asking for content semantics."""
    assert "dedup" not in route("when a file in ~/notes changes").spec


# ── the other declared kinds ──


def test_a_finished_run_routes_to_run_completed():
    """Checked before the generic event cues: "when my nightly run finishes" also contains
    "when", so a generic cue must not claim it."""
    assert route("when my nightly run finishes").kind == "run_completed"


def test_idle_routes_to_idle():
    assert route("when I have been idle for an hour").kind == "idle"


def test_a_webhook_routes_to_webhook():
    assert route("when a webhook posts to me").kind == "webhook"


def test_every_routed_kind_is_a_kind_the_store_accepts():
    """🔴 A route to a kind the entity rejects would create a trigger that loads broken and never
    fires — the same present-and-inert class this program keeps finding."""
    from personalclaw.triggers.models import SPEC_KEYS

    texts = [
        "when a file in ~/notes changes",
        "every weekday at 9",
        "when my nightly run finishes",
        "when https://example.com changes",
        "when I have been idle for an hour",
        "when a webhook posts to me",
    ]
    for text in texts:
        routed = route(text)
        assert routed.kind in SPEC_KEYS, f"{text!r} → unknown kind {routed.kind!r}"


def test_a_routed_spec_only_uses_keys_the_kind_declares():
    """The spec this module builds must satisfy the entity's own key set, or the trigger is broken
    on arrival."""
    from personalclaw.triggers.models import SPEC_KEYS

    for text in ("when a file in ~/notes changes", "when https://example.com changes"):
        routed = route(text)
        unknown = set(routed.spec) - set(SPEC_KEYS[routed.kind]) - {"kind"}
        assert not unknown, f"{text!r} produced unknown spec keys {unknown}"


# ── the explanation ──


def test_a_route_explains_itself():
    """§4 requires agent-created triggers be "announced to the user on creation". A wrong route
    the user cannot see is a wrong route they cannot correct."""
    assert "path" in route("when a file in ~/notes changes").because
    assert route("every weekday at 9").because


def test_the_route_serializes():
    payload = route("when a file in ~/notes changes").to_dict()
    assert payload["kind"] == "file"
    assert payload["ok"] is True
    assert payload["spec"]["paths"]
