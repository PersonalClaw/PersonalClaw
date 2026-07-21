"""OP1 — content-type-aware tool-output projection.

Projection keeps the salient slice (log error lines, diff hunks+stat, json shape,
test failures, csv head/tail) for large recognized types, and is conservative +
fail-soft: small results pass through untouched; unknown types fall back to the
head/tail cap (today's behavior).
"""

from __future__ import annotations

import json

from personalclaw.tool_providers.projection import (
    infer_content_type,
    project_output,
)

# ── conservative pass-through + fallback ────────────────────────────────────


def test_small_result_passes_through_untouched():
    text = "short output"
    p = project_output(text, cap=1000)
    assert p.text == text and p.truncated is False and p.original_length is None


def test_uncapped_passes_through():
    text = "x" * 100_000
    p = project_output(text, cap=None)
    assert p.text == text and p.truncated is False


def test_unknown_large_type_falls_back_to_head_tail():
    text = "lorem ipsum dolor " * 1000  # prose, no recognized markers
    p = project_output(text, cap=500)
    assert p.truncated and p.original_length == len(text)
    assert p.content_type == "generic"
    assert "truncated:" in p.text  # the maybe_truncate marker


# ── type inference ──────────────────────────────────────────────────────────


def test_infer_diff():
    assert infer_content_type("diff --git a/f b/f\n@@ -1 +1 @@\n-a\n+b\n") == "diff"


def test_infer_json_must_parse():
    assert infer_content_type('{"a": 1, "b": [1,2,3]}') == "json"
    # leading brace but invalid → NOT json (conservative)
    assert infer_content_type("{not valid json at all") == "generic"


def test_infer_test_output():
    assert infer_content_type("=== test session ===\n2 passed, 1 failed\n") == "test"


def test_infer_csv():
    assert infer_content_type("a,b,c\n1,2,3\n4,5,6\n") == "csv"


def test_infer_generic_for_prose():
    assert infer_content_type("just some normal prose text here") == "generic"


# ── per-type projection keeps the signal ────────────────────────────────────


def test_log_projection_keeps_error_lines_from_the_middle():
    lines = [f"line {i}" for i in range(500)]
    lines[250] = "ERROR: the thing that actually broke"
    lines[251] = "Traceback (most recent call last):"
    text = "\n".join(lines)
    p = project_output(text, cap=2000, content_type="log")
    assert p.truncated and p.content_type == "log"
    # the middle error line survives even though head/tail would have cut it
    assert "the thing that actually broke" in p.text
    assert "Traceback" in p.text


def test_diff_projection_has_stat_summary():
    diff = "diff --git a/x b/x\n@@ -1,2 +1,2 @@\n-old\n+new\n+added\n" + ("+pad\n" * 2000)
    p = project_output(diff, cap=1500, content_type="diff")
    assert p.content_type == "diff"
    assert p.text.startswith("[diff:")  # +N/-M stat summary leads


def test_json_projection_shows_shape_not_midcut():
    data = {"items": list(range(1000)), "name": "big", "nested": {"k": "v"}}
    text = json.dumps(data)
    p = project_output(text, cap=400, content_type="json")
    assert p.truncated and p.content_type == "json"
    # shape line names the top-level keys/types rather than a mid-string cut
    assert "object:" in p.text and "items" in p.text


def test_test_projection_keeps_failures():
    lines = ["test_a PASSED"] * 200
    lines.insert(100, "test_b FAILED")
    lines.insert(101, "E   AssertionError: expected 1 got 2")
    lines.append("=== 199 passed, 1 failed ===")
    text = "\n".join(lines)
    p = project_output(text, cap=1500, content_type="test")
    assert p.content_type == "test"
    assert "FAILED" in p.text and "AssertionError" in p.text
    assert "1 failed" in p.text  # summary kept


def test_csv_projection_head_tail_rows():
    rows = ["col1,col2,col3"] + [f"{i},{i*2},{i*3}" for i in range(500)]
    text = "\n".join(rows)
    p = project_output(text, cap=2000, content_type="csv")
    assert p.content_type == "csv"
    assert "col1,col2,col3" in p.text  # header kept
    assert "more rows" in p.text  # elision note


def test_declared_type_beats_inference():
    # looks like prose, but the tool declared it json → still routed to generic
    # fallback because it won't parse (projector is itself fail-soft)
    text = "not json " * 500
    p = project_output(text, cap=300, content_type="json")
    assert p.truncated  # projected/capped one way or another, never crashes


def test_projection_respects_cap_budget():
    text = "ERROR boom\n" * 5000
    p = project_output(text, cap=1000, content_type="log")
    # the projected slice itself is re-capped to the budget (with some slack for markers)
    assert len(p.text) <= 1000 + 200


# ── OP5: shared project_and_retain (used by native tools AND the MCP adapter) ──


def _isolate_store(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg
    import personalclaw.session_workspace as ws

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)


def test_project_and_retain_small_passthrough(tmp_path, monkeypatch):
    from personalclaw.tool_providers.projection import project_and_retain

    _isolate_store(tmp_path, monkeypatch)
    out, meta = project_and_retain("short", session_key="s", cap=10000)
    assert out == "short" and "raw_ref" not in meta


def test_project_and_retain_large_projects_and_retains(tmp_path, monkeypatch):
    from personalclaw.tool_providers import result_store
    from personalclaw.tool_providers.projection import project_and_retain

    _isolate_store(tmp_path, monkeypatch)
    big = "line\n" * 4000 + "ERROR boom in the middle\n" + "line\n" * 4000
    out, meta = project_and_retain(big, session_key="sess-op5", content_type="log", cap=2000)
    assert len(out) < len(big)
    assert "raw_ref" in meta and 'tool_result_get(result_id="' in out
    # the retained raw is the FULL original, recoverable by the affordance's id
    got = result_store.get_result("sess-op5", meta["raw_ref"])
    assert got is not None and got["raw"] == big


def test_project_and_retain_no_session_no_raw(tmp_path, monkeypatch):
    from personalclaw.tool_providers.projection import project_and_retain

    _isolate_store(tmp_path, monkeypatch)
    big = "x" * 100000
    out, meta = project_and_retain(big, session_key="", cap=2000)
    # projected (shorter) but no raw_ref possible without a session store
    assert len(out) < len(big) and "raw_ref" not in meta


# ── user-teachable projection rules (TokenJuice OP6) ────────────────────────


class TestUserProjectionRules:
    """A user rule teaches the DISPATCH: output matching a marker → a builtin
    strategy. Consulted before the heuristic sniff; fail-soft on bad rules."""

    def teardown_method(self):
        # Never leak rules across tests (module-global state).
        from personalclaw.tool_providers.projection import set_user_rules

        set_user_rules([])

    def test_user_rule_wins_over_heuristic(self):
        from personalclaw.tool_providers.projection import (
            ProjectionRule,
            infer_content_type,
            set_user_rules,
        )

        # This sample would sniff as generic; the rule forces 'log'.
        sample = "[ACME] boot sequence begin\nstep 1\nstep 2\n"
        assert infer_content_type(sample) == "generic"
        set_user_rules([ProjectionRule(name="acme", match_regex=r"^\[ACME\]", strategy="log")])
        assert infer_content_type(sample) == "log"

    def test_user_rule_engages_the_matching_projector(self):
        from personalclaw.tool_providers.projection import (
            ProjectionRule,
            project_output,
            set_user_rules,
        )

        set_user_rules([ProjectionRule(name="acme", match_regex=r"^\[ACME\]", strategy="log")])
        # A big custom-log output → projected via the log projector (keeps errors).
        big = "[ACME] start\n" + "noise\n" * 4000 + "ERROR kaboom\n" + "noise\n" * 4000
        p = project_output(big, cap=2000)
        assert p.content_type == "log" and p.truncated
        assert "ERROR kaboom" in p.text  # the log projector kept the error line

    def test_no_match_falls_through_to_heuristics(self):
        from personalclaw.tool_providers.projection import (
            ProjectionRule,
            infer_content_type,
            set_user_rules,
        )

        set_user_rules([ProjectionRule(name="acme", match_regex=r"^\[ACME\]", strategy="log")])
        # A real diff still sniffs as diff (rule didn't match).
        assert infer_content_type("diff --git a/f b/f\n@@ -1 +1 @@\n-a\n+b\n") == "diff"

    def test_bad_regex_is_skipped_fail_soft(self):
        from personalclaw.tool_providers.projection import ProjectionRule, set_user_rules

        set_user_rules([ProjectionRule(name="bad", match_regex="(", strategy="log")])
        import personalclaw.tool_providers.projection as P

        assert len(P._USER_RULES) == 0  # invalid regex dropped, no raise

    def test_unknown_strategy_is_skipped(self):
        from personalclaw.tool_providers.projection import ProjectionRule, set_user_rules

        set_user_rules([ProjectionRule(name="x", match_regex="foo", strategy="nonsense")])
        import personalclaw.tool_providers.projection as P

        assert len(P._USER_RULES) == 0
