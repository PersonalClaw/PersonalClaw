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


class TestJsonCrusher:
    """§2.1 — the JSON crusher: per-field schema over arrays (bounded sample),
    first/last item verbatim, repeated-structure folding."""

    def test_uniform_array_folds_to_schema(self):
        data = [{"id": i, "name": f"n{i}", "score": i * 0.5} for i in range(5000)]
        text = json.dumps(data)
        p = project_output(text, cap=2000, content_type="json")
        assert p.truncated and p.content_type == "json"
        assert "[array: 5000 items, uniform shape" in p.text
        # per-field schema: names, types, numeric range
        assert "id: int [0.." in p.text
        assert "name: str" in p.text
        assert "score: float" in p.text
        # first/last item verbatim
        assert '"id": 0' in p.text and '"id": 4999' in p.text

    def test_null_counts_in_schema(self):
        data = [{"v": None if i % 2 else i, "pad": "x" * 30} for i in range(500)]
        p = project_output(json.dumps(data), cap=1500, content_type="json")
        assert p.truncated
        assert "null)" in p.text  # null count surfaced

    def test_mixed_array_reports_sampled_types(self):
        data = [1, "two", {"three": 3}] * 500
        p = project_output(json.dumps(data), cap=800, content_type="json")
        assert "[array: 1500 items, sampled types:" in p.text

    def test_dict_with_large_array_value_folds_per_path(self):
        d = {"status": "ok", "rows": [{"a": i} for i in range(1000)], "meta": {}}
        p = project_output(json.dumps(d), cap=1500, content_type="json")
        assert "object:" in p.text
        # the big array VALUE gets its own crushed view, not just the sample cut
        assert '"rows":' in p.text and "[array: 1000 items" in p.text

    def test_parse_failure_falls_back_to_head_tail(self):
        text = '{"broken": ' + "x" * 5000
        p = project_output(text, cap=500, content_type="json")
        assert p.truncated and "truncated:" in p.text  # maybe_truncate marker


class TestCodeCompressor:
    """§2.2 — AST-aware code compressor: signatures + docstring first-lines + line
    map for Python; regex outliner for other languages; fail-soft everywhere."""

    PY = (
        '"""Module doc first line.\nsecond line."""\n'
        "import os\nfrom pathlib import Path\n\n"
        + "\n".join(
            f'def fn_{i}(x: int) -> str:\n    """Doc for fn_{i}."""\n    return str(x)\n'
            f"    # filler\n    y = {i}\n    z = y * 2\n"
            for i in range(80)
        )
        + "\nclass Big:\n"
        + '    """A class."""\n'
        + "    def method(self, a, b=3):\n"
        + "        return a + b\n"
    )

    def test_sniffs_python_as_code(self):
        assert infer_content_type(self.PY) == "code"

    def test_python_outline_keeps_signatures_docstrings_linemap(self):
        p = project_output(self.PY, cap=6000)
        assert p.truncated and p.content_type == "code"
        assert "[code outline:" in p.text
        assert '"""Module doc first line."""' in p.text
        assert "import os" in p.text
        assert "def fn_0(x: int) -> str:  # line" in p.text
        assert '"""Doc for fn_0."""' in p.text
        # the function BODY is elided (that's the compression)
        assert "y = 0" not in p.text

    def test_class_methods_outlined_with_indent(self):
        src = (
            "class Widget:\n"
            '    """W doc."""\n'
            "    def render(self, ctx):\n"
            '        """Render it."""\n'
            "        return ctx\n" + "# pad\n" * 50
        )
        p = project_output(src, cap=400, content_type="code")
        assert "class Widget:  # line 1" in p.text
        assert "    def render(self, ctx):  # line 3" in p.text

    def test_non_python_uses_regex_outliner(self):
        ts = (
            'import { a } from "./a"\n'
            "export function main(x: number) {\n  return x\n}\n"
            "const helper = (y) => y * 2\n"
            "class Widget {\n  render() {}\n}\n"
        ) * 30
        p = project_output(ts, cap=1200, content_type="code")
        assert p.content_type == "code"
        assert "export function main" in p.text and "# line" in p.text

    def test_unparseable_no_outline_falls_back(self):
        # declared code but no definition lines at all → head/tail fallback
        text = "just words here\n" * 2000
        p = project_output(text, cap=400, content_type="code")
        assert p.truncated and "truncated:" in p.text

    def test_prose_does_not_sniff_as_code(self):
        prose = (
            "We import goods from overseas. The class of 2020 graduated.\n"
            "Function follows form in architecture.\n"
        ) * 50
        assert infer_content_type(prose) == "generic"

    def test_shebang_sniffs_as_code(self):
        script = "#!/usr/bin/env bash\necho hi\n" + "echo pad\n" * 20
        assert infer_content_type(script) == "code"


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


# ── §2.3: the three-layer overlay (project > user > builtin) + rule ops v2 ───


class TestBuiltinRulePack:
    """The in-tree rule pack maps common command-output markers to strategies —
    the dispatch analog of the projectors, so a `git diff` run through the shell
    (which declares everything "log") still projects as a diff."""

    def test_pack_loads_and_compiles(self):
        import personalclaw.tool_providers.projection as P

        rules = P._builtin_rules()
        assert len(rules) >= 20  # start ~25, grown by evidence
        assert all(r.strategy in P._PROJECTORS for r in rules)

    def test_builtin_rule_beats_declared_type(self):
        # run_command declares "log" — but a diff body matched by the builtin pack
        # projects as a DIFF (rule > declared > sniff specificity).
        diff = "diff --git a/x b/x\n@@ -1,2 +1,2 @@\n-old\n+new\n" + ("+pad\n" * 3000)
        p = project_output(diff, cap=1500, content_type="log")
        assert p.content_type == "diff"
        assert p.text.startswith("[diff:")

    def test_pytest_through_shell_projects_as_test(self):
        text = (
            "=== test session starts ===\n"
            + "test_a PASSED\n" * 800
            + "test_b FAILED\nE   AssertionError: nope\n"
            + "=== 800 passed, 1 failed ===\n"
        )
        p = project_output(text, cap=1500, content_type="log")
        assert p.content_type == "test"
        assert "AssertionError" in p.text

    def test_plain_log_still_projects_as_log(self):
        # No builtin marker → the declared type stands (no regression for real logs).
        text = "starting service\n" + "noise\n" * 5000 + "ERROR kaboom\n" + "noise\n" * 100
        p = project_output(text, cap=1500, content_type="log")
        assert p.content_type == "log"
        assert "ERROR kaboom" in p.text


class TestProjectRuleLayer:
    """`.personalclaw/projection_rules.json` in the session cwd — repo-supplied
    dispatch rules, mtime-cached, most-specific layer (beats user + builtin)."""

    def teardown_method(self):
        from personalclaw.tool_providers.projection import set_user_rules

        set_user_rules([])

    def _bind(self, tmp_path, rules):
        import json as _json

        import personalclaw.tool_providers.projection as P

        d = tmp_path / ".personalclaw"
        d.mkdir(exist_ok=True)
        (d / "projection_rules.json").write_text(_json.dumps(rules))
        P._PROJECT_RULES_CACHE.clear()
        return P.bind_project_dir(tmp_path)

    def test_project_rule_dispatches(self, tmp_path):
        import personalclaw.tool_providers.projection as P

        tok = self._bind(tmp_path, [{"name": "svc", "match_regex": r"^\[SVC\]", "strategy": "log"}])
        try:
            assert infer_content_type("[SVC] boot\nstep\n") == "log"
        finally:
            P.reset_project_dir(tok)
        # unbound → the project layer is gone
        assert infer_content_type("[SVC] boot\nstep\n") == "generic"

    def test_project_beats_user_layer(self, tmp_path):
        import personalclaw.tool_providers.projection as P
        from personalclaw.tool_providers.projection import ProjectionRule, set_user_rules

        set_user_rules([ProjectionRule(name="u", match_regex=r"^\[SVC\]", strategy="csv")])
        tok = self._bind(tmp_path, [{"name": "p", "match_regex": r"^\[SVC\]", "strategy": "log"}])
        try:
            assert infer_content_type("[SVC] x\ny\n") == "log"  # project wins
        finally:
            P.reset_project_dir(tok)
        assert infer_content_type("[SVC] x\ny\n") == "csv"  # user layer still there

    def test_bad_project_file_never_breaks_dispatch(self, tmp_path):
        import personalclaw.tool_providers.projection as P

        d = tmp_path / ".personalclaw"
        d.mkdir()
        (d / "projection_rules.json").write_text("{not json")
        P._PROJECT_RULES_CACHE.clear()
        tok = P.bind_project_dir(tmp_path)
        try:
            assert infer_content_type("anything at all here") == "generic"
        finally:
            P.reset_project_dir(tok)

    def test_mtime_cache_reloads_on_change(self, tmp_path):
        import json as _json
        import os

        import personalclaw.tool_providers.projection as P

        tok = self._bind(tmp_path, [{"name": "a", "match_regex": r"^\[A\]", "strategy": "log"}])
        try:
            assert infer_content_type("[A] x\n") == "log"
            f = tmp_path / ".personalclaw" / "projection_rules.json"
            f.write_text(_json.dumps([{"name": "b", "match_regex": r"^\[B\]", "strategy": "log"}]))
            os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))  # force mtime change
            assert infer_content_type("[A] x\n") == "generic"
            assert infer_content_type("[B] x\n") == "log"
        finally:
            P.reset_project_dir(tok)


class TestRuleOpsV2:
    """Declarative rule operations (head/tail/keep/skip/count) executed by the one
    shared interpreter — still no user code."""

    def teardown_method(self):
        from personalclaw.tool_providers.projection import set_user_rules

        set_user_rules([])

    def test_keep_and_head_tail(self):
        from personalclaw.tool_providers.projection import ProjectionRule, set_user_rules

        set_user_rules(
            [
                ProjectionRule(
                    name="errs",
                    match_regex=r"^\[SVC\]",
                    strategy="log",
                    keep=r"(ERROR|^\[SVC\])",
                    head=2,
                    tail=2,
                )
            ]
        )
        lines = ["[SVC] boot"] + [f"info {i}" for i in range(3000)]
        lines[100] = "ERROR one"
        lines[200] = "ERROR two"
        lines[300] = "ERROR three"
        p = project_output("\n".join(lines), cap=2000)
        assert p.truncated and p.content_type == "log"
        assert "[rule 'errs':" in p.text
        # keep filtered to the SVC header + 3 error lines; head/tail windowed them
        assert "ERROR one" in p.text and "ERROR three" in p.text
        assert "info 5" not in p.text

    def test_skip_filter(self):
        from personalclaw.tool_providers.projection import ProjectionRule, set_user_rules

        set_user_rules(
            [ProjectionRule(name="s", match_regex=r"^\[SVC\]", strategy="log", skip=r"^DEBUG")]
        )
        text = "[SVC] start\n" + "DEBUG chatter\n" * 4000 + "RESULT: 42\n"
        p = project_output(text, cap=2000)
        assert "RESULT: 42" in p.text
        assert "DEBUG chatter" not in p.text

    def test_count_folds_matching_lines(self):
        from personalclaw.tool_providers.projection import ProjectionRule, set_user_rules

        set_user_rules(
            [
                ProjectionRule(
                    name="hb", match_regex=r"^\[SVC\]", strategy="log", count=r"^heartbeat"
                )
            ]
        )
        text = "[SVC] up\n" + "heartbeat ok\n" * 5000 + "shutdown clean\n"
        p = project_output(text, cap=2000)
        assert "5000 line(s) matching" in p.text
        assert "shutdown clean" in p.text

    def test_ops_output_respects_cap(self):
        from personalclaw.tool_providers.projection import ProjectionRule, set_user_rules

        set_user_rules(
            [ProjectionRule(name="k", match_regex=r"^\[SVC\]", strategy="log", keep=r".")]
        )
        text = "[SVC] x\n" + "y" * 100_000
        p = project_output(text, cap=1000)
        assert len(p.text) <= 1000 + 200

    def test_strategy_only_rule_unaffected_by_ops_path(self):
        # A rule with NO ops still routes to its strategy projector (the v1 path).
        from personalclaw.tool_providers.projection import ProjectionRule, set_user_rules

        set_user_rules([ProjectionRule(name="v1", match_regex=r"^\[SVC\]", strategy="log")])
        text = "[SVC] go\n" + "noise\n" * 4000 + "ERROR boom\n" + "noise\n" * 100
        p = project_output(text, cap=2000)
        assert p.content_type == "log" and "ERROR boom" in p.text
        assert "[rule" not in p.text  # projector output, not the ops interpreter
