"""Session templates + conversation export (SESSION-MANAGEMENT S3).

The security-load-bearing test in here is ``test_export_redacts_user_typed_credentials``:
the dashboard write path deliberately skips redaction for ``user``/``system`` roles, so
export is the ONLY pass those ever get before the text leaves the machine.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.dashboard import session_export as se
from personalclaw.dashboard import session_templates as st

# ── templates ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every template test writes under tmp_path — never a real home."""
    monkeypatch.setattr(st, "config_dir", lambda: tmp_path)
    return tmp_path


def test_save_then_get_roundtrips_every_field():
    tid, err = st.save_template(
        {
            "name": "Research deep dive",
            "agent": "personalclaw",
            "model": "claude-opus-5",
            "reasoning_effort": "high",
            "first_prompt": "Review this repo.",
        }
    )
    assert err == ""
    got = st.get_template(tid)
    assert got is not None
    assert got["name"] == "Research deep dive"
    assert got["agent"] == "personalclaw"
    assert got["model"] == "claude-opus-5"
    assert got["reasoning_effort"] == "high"
    assert got["first_prompt"] == "Review this repo."
    assert got["created_at"] > 0


def test_unknown_keys_are_dropped_not_stored():
    """The field dict is the allowlist — a client can't smuggle in a new field."""
    tid, err = st.save_template({"name": "x", "workspace_dir": "/etc", "evil": True})
    assert err == ""
    got = st.get_template(tid)
    assert got is not None
    assert "workspace_dir" not in got
    assert "evil" not in got


def test_blank_name_is_rejected():
    tid, err = st.save_template({"name": "   "})
    assert tid == ""
    assert "name is required" in err


def test_invalid_reasoning_effort_is_rejected():
    tid, err = st.save_template({"name": "x", "reasoning_effort": "extreme"})
    assert tid == ""
    assert "reasoning_effort" in err


@pytest.mark.parametrize("effort", ["", "low", "medium", "high", "max"])
def test_valid_reasoning_efforts_accepted(effort):
    tid, err = st.save_template({"name": f"t-{effort or 'none'}", "reasoning_effort": effort})
    assert err == ""
    assert tid


def test_long_name_and_prompt_are_bounded():
    tid, err = st.save_template({"name": "n" * 500, "first_prompt": "p" * 99_000})
    assert err == ""
    got = st.get_template(tid)
    assert got is not None
    assert len(got["name"]) == st._MAX_NAME
    assert len(got["first_prompt"]) == st._MAX_PROMPT


def test_template_limit_refuses_rather_than_growing_unbounded():
    for i in range(st._MAX_TEMPLATES):
        tid, err = st.save_template({"name": f"t{i}"})
        assert err == "", err
    tid, err = st.save_template({"name": "one too many"})
    assert tid == ""
    assert "limit" in err


def test_list_is_newest_first():
    a, _ = st.save_template({"name": "older"})
    b, _ = st.save_template({"name": "newer"})
    # created_at can tie on a coarse clock; assert both present and the order is by
    # created_at descending rather than depending on sub-ms resolution.
    ids = [t["id"] for t in st.list_templates()]
    assert set(ids) == {a, b}
    stamps = [t["created_at"] for t in st.list_templates()]
    assert stamps == sorted(stamps, reverse=True)


def test_update_preserves_created_at():
    tid, _ = st.save_template({"name": "before"})
    original = st.get_template(tid)["created_at"]  # type: ignore[index]
    assert st.update_template(tid, {"name": "after"}) == ""
    got = st.get_template(tid)
    assert got is not None
    assert got["name"] == "after"
    assert got["created_at"] == original


def test_update_unknown_id_reports_not_found():
    assert st.update_template("nope", {"name": "x"}) == "not found"


def test_delete_removes_and_is_idempotent():
    tid, _ = st.save_template({"name": "gone"})
    assert st.delete_template(tid) is True
    assert st.delete_template(tid) is False
    assert st.get_template(tid) is None


def test_corrupt_store_reads_as_empty_not_an_exception(_isolated_home):
    """A settings file a user (or a crash) mangled must not break the chat page."""
    path = _isolated_home / "entity_settings" / "session_templates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert st.list_templates() == []
    assert st.get_template("anything") is None
    # And it recovers: a save over a corrupt file works.
    tid, err = st.save_template({"name": "recovered"})
    assert err == ""
    assert st.get_template(tid) is not None


def test_non_dict_json_reads_as_empty(_isolated_home):
    path = _isolated_home / "entity_settings" / "session_templates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert st.list_templates() == []


# ── export ───────────────────────────────────────────────────────────────────

_MSGS = [
    {
        "role": "user",
        "content": "deploy with AKIAIOSFODNN7EXAMPLE please",
        "ts": "2026-07-30T01:00:00",
    },
    {"role": "assistant", "content": "Done.", "ts": "2026-07-30T01:00:05"},
    {"role": "chunk", "content": "streaming noise"},
    {"role": "system", "content": "context block"},
]


def test_export_redacts_user_typed_credentials():
    """THE point of export redaction.

    The write path skips redaction for `user`/`system` roles
    (chat_persistence.py:606-608), so a secret the user typed is stored RAW. Export is
    the only pass it ever gets, and an unredacted export is a credential leaving the
    machine in a file the user is about to share.
    """
    md = se.render_markdown(title="t", key="k", meta={}, messages=_MSGS)
    assert "AKIAIOSFODNN7EXAMPLE" not in md
    assert "REDACTED" in md

    raw = se.render_json(title="t", key="k", meta={}, messages=_MSGS)
    assert "AKIAIOSFODNN7EXAMPLE" not in raw
    payload = json.loads(raw)
    assert payload["redacted"] is True


def test_export_drops_ui_bookkeeping_roles():
    md = se.render_markdown(title="t", key="k", meta={}, messages=_MSGS)
    assert "streaming noise" not in md
    payload = json.loads(se.render_json(title="t", key="k", meta={}, messages=_MSGS))
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant", "system"]


def test_markdown_blockquotes_content_so_it_cannot_restructure_the_document():
    """A transcript containing markdown headings must not become the export's outline."""
    msgs = [{"role": "assistant", "content": "# Fake Title\n## Fake Section"}]
    md = se.render_markdown(title="Real", key="k", meta={}, messages=msgs)
    # Every content line is quoted, so no bare heading exists below the header block.
    body = md.split("## Assistant", 1)[1]
    assert "> # Fake Title" in body
    assert "\n# Fake Title" not in body


def test_markdown_preserves_blank_lines_inside_a_message():
    msgs = [{"role": "user", "content": "one\n\ntwo"}]
    md = se.render_markdown(title="t", key="k", meta={}, messages=msgs)
    assert "> one\n>\n> two" in md


def test_empty_transcript_still_renders_valid_output():
    md = se.render_markdown(title="Empty", key="k", meta={}, messages=[])
    assert md.startswith("# Empty")
    payload = json.loads(se.render_json(title="Empty", key="k", meta={}, messages=[]))
    assert payload["messages"] == []


def test_blank_content_messages_are_skipped():
    msgs = [{"role": "user", "content": "   "}, {"role": "assistant", "content": "real"}]
    payload = json.loads(se.render_json(title="t", key="k", meta={}, messages=msgs))
    assert [m["content"] for m in payload["messages"]] == ["real"]


def test_non_dict_entries_are_tolerated():
    msgs: list = ["not a dict", None, {"role": "user", "content": "ok"}]
    payload = json.loads(se.render_json(title="t", key="k", meta={}, messages=msgs))
    assert len(payload["messages"]) == 1


def test_title_is_redacted_too():
    """A session auto-titled from a message can carry the secret in its TITLE."""
    md = se.render_markdown(title="setting up AKIAIOSFODNN7EXAMPLE", key="k", meta={}, messages=[])
    assert "AKIAIOSFODNN7EXAMPLE" not in md
    payload = json.loads(
        se.render_json(title="setting up AKIAIOSFODNN7EXAMPLE", key="k", meta={}, messages=[])
    )
    assert "AKIAIOSFODNN7EXAMPLE" not in payload["title"]


def test_render_rejects_unknown_format():
    with pytest.raises(ValueError, match="unknown format"):
        se.render("pdf", title="t", key="k", meta={}, messages=[])


def test_render_dispatches_content_type():
    _, ct = se.render("md", title="t", key="k", meta={}, messages=[])
    assert ct == "text/markdown"
    _, ct = se.render("json", title="t", key="k", meta={}, messages=[])
    assert ct == "application/json"


@pytest.mark.parametrize(
    "title,expected",
    [
        ("My Chat: Q3/Review!", "my-chat-q3-review.md"),
        ("   ", "chat.md"),
        ("///", "chat.md"),
        ("a" * 200, "a" * 60 + ".md"),
    ],
)
def test_export_filename_is_filesystem_safe(title, expected):
    assert se.export_filename(title, "fallback", "md") == expected


def test_export_filename_is_ascii_so_plain_content_disposition_is_valid():
    """The route uses `filename="…"` (not RFC 5987), so the name must be ASCII."""
    name = se.export_filename("日本語のチャット", "fallback-key", "json")
    name.encode("ascii")  # raises if not
    assert name.endswith(".json")


def test_json_export_carries_provenance():
    payload = json.loads(
        se.render_json(
            title="T",
            key="dashboard:abc",
            meta={"agent": "personalclaw", "model": "m", "created_at": "2026-07-30"},
            messages=[],
        )
    )
    assert payload["key"] == "dashboard:abc"
    assert payload["agent"] == "personalclaw"
    assert payload["model"] == "m"
    assert payload["created_at"] == "2026-07-30"


def test_markdown_header_states_that_it_is_redacted():
    """A consumer must never mistake a redacted export for a verbatim transcript."""
    md = se.render_markdown(title="t", key="k", meta={}, messages=_MSGS)
    assert "Redacted" in md
