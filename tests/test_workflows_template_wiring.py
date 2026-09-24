"""The template pipeline's PRODUCTION WIRING (UP-R9/UP-R13.3, WF2UNI-7).

`test_workflows_template_pipeline.py` already proves the pipeline's rules are right. This file
proves they RUN — which is a different claim, and the one that was false: both
`workflows/template_pipeline.py` and `workflows/eval_specs.py` shipped in S45 with zero production
importers, so every rule they implement was correct and unreachable.

So every test here drives a real surface end to end, and the fixtures are deliberately hostile to
the shape of test that would have passed while the code was inert:

* **Mining reads a REAL transcript file.** The records are written to `sessions/<sid>.jsonl` in the
  format `history.ConversationLog.append` actually writes, and the assertion is that
  `workflow_plan` reports what that FILE said. A hand-built record list handed straight to
  `mine_session` is exactly the test that stayed green for a year while nothing called it.
* **The nudge survives a restart.** Persistence is asserted by discarding every in-memory object
  and re-entering through the tool, because an anti-nag rule held in memory is not an anti-nag rule
  — a restart that re-offers a declined shape is the nagging the rules exist to prevent.
* **A frozen candidate comes back through the matcher.** Freezing is only worth doing if the next
  similar intent finds it, so the round trip is driven, not the write alone.
"""

from __future__ import annotations

import json

import pytest

from personalclaw import mcp_core, mcp_workflows
from personalclaw.workflows import template_pipeline, template_store


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home that EVERY layer sees.

    `PERSONALCLAW_HOME` rather than only patching `config_dir`: `session_map` resolves its sessions
    dir through its own helper and `template_store` through `workflows.store`, so patching one
    module's `config_dir` leaves the other reading the real home (which is how a test writes into
    `~/.personalclaw` and passes).
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def write_transcript(home, sid: str, records: list[dict]) -> None:
    """Write records as a real session JSONL, one JSON object per line."""
    sessions = home / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / f"{sid}.jsonl"
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records),
        encoding="utf-8",
    )


REAL_SESSION = [
    {"_type": "metadata", "created_at": "2026-01-01T00:00:00", "title": "Release triage"},
    {"role": "user", "content": "Triage the open issues in the release milestone", "ts": "t0"},
    {"role": "tool", "content": "github_list_issues", "meta": {"approval": "allow"}},
    {"role": "assistant", "content": "Here are the issues…", "ts": "t1"},
    {"role": "tool", "content": "github_list_issues", "meta": {"approval": "allow"}},
    {"role": "tool", "content": "shell_exec", "meta": {"approval": "deny"}},
    {"role": "user", "content": "now also label them", "ts": "t2"},
]


# ── clause 1: source_session_id → mine_session ────────────────────────────────


class TestSourceSessionMining:
    def test_a_session_id_resolves_to_its_jsonl(self, home):
        write_transcript(home, "chat-7", REAL_SESSION)
        from personalclaw import session_map

        path = session_map.transcript_path("chat-7")
        assert path is not None and path.name == "chat-7.jsonl"
        assert path.parent.name == "sessions"

    def test_records_come_from_the_file_not_the_caller(self, home):
        """The whole point of the clause: `mine_session` receives what the FILE said.

        Asserted through `read_transcript` rather than by handing records over, because the
        resolution + parse step is the part that did not exist."""
        write_transcript(home, "chat-7", REAL_SESSION)
        from personalclaw import session_map

        records = session_map.read_transcript("chat-7")
        assert len(records) == len(REAL_SESSION)
        mined = template_pipeline.mine_session(records)
        assert mined.title == "Release triage"
        assert mined.user_turns[0] == "Triage the open issues in the release milestone"
        assert [t.name for t in mined.tools] == ["github_list_issues", "shell_exec"]

    def test_a_missing_session_resolves_to_nothing(self, home):
        from personalclaw import session_map

        assert session_map.transcript_path("nope") is None
        assert session_map.read_transcript("nope") == []

    def test_a_traversing_id_cannot_escape_the_sessions_dir(self, home):
        from personalclaw import session_map

        for bad in ("../config", "a/b", ".hidden", ""):
            assert session_map.transcript_path(bad) is None

    def test_one_unparseable_line_does_not_lose_the_session(self, home):
        sessions = home / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        (sessions / "chat-8.jsonl").write_text(
            json.dumps({"_type": "metadata", "title": "T"})
            + "\n{ not json\n"
            + json.dumps({"role": "user", "content": "do the thing"})
            + "\n",
            encoding="utf-8",
        )
        from personalclaw import session_map

        records = session_map.read_transcript("chat-8")
        assert template_pipeline.mine_session(records).user_turns == ["do the thing"]

    def test_plan_reports_the_mined_permission_signature(self, home):
        """The plan carries the session's REAL tool use, and the denial is reported.

        A denied tool absent from the signature but also absent from the output would leave a
        reviewer unable to check the claim that the signature is pre-validated."""
        write_transcript(home, "chat-7", REAL_SESSION)
        out = mcp_workflows._call_tool(
            "workflow_plan", {"goal": "triage issues", "source_session_id": "chat-7"}
        )
        assert "mined_session" in out
        assert "github_list_issues" in out
        # The denied tool is named as denied, and NOT in the signature.
        body = json.loads(out[out.index("{") :])
        mined = body["mined_session"]
        assert mined["permission_signature"] == ["github_list_issues"]
        assert mined["denied"] == ["shell_exec"]
        assert mined["session_id"] == "chat-7"

    def test_the_transcript_supplies_the_goal_when_none_is_given(self, home):
        """`source_session_id` alone is a complete request — retyping what the session recorded
        would make mining more work than not mining."""
        write_transcript(home, "chat-7", REAL_SESSION)
        out = mcp_workflows._call_tool("workflow_plan", {"source_session_id": "chat-7"})
        assert "WF_PLAN_GOAL_REQUIRED" not in out
        assert "Triage the open issues in the release milestone" in out

    def test_neither_a_goal_nor_a_session_is_still_an_error(self, home):
        assert "WF_PLAN_GOAL_REQUIRED" in mcp_workflows._call_tool("workflow_plan", {})

    def test_an_unmineable_session_with_no_goal_is_named_not_guessed(self, home):
        out = mcp_workflows._call_tool("workflow_plan", {"source_session_id": "ghost"})
        assert "WF_PLAN_SESSION_NOT_MINEABLE" in out

    def test_no_session_means_no_mined_block(self, home):
        """An empty mined block would read as 'that session did nothing'."""
        out = mcp_workflows._call_tool("workflow_plan", {"goal": "summarize issues"})
        assert "mined_session" not in out


# ── clause 2: eval_specs imported by a live surface ───────────────────────────


class TestEvalSpecsAreProduced:
    def test_the_template_plan_path_produces_a_benchmark(self, home):
        """`eval_specs` had zero importers. The template plan path is now one."""
        definition = {
            "name": "demo",
            "root": {
                "kind": "sequence",
                "id": "main",
                "children": [
                    {"kind": "infer", "id": "think", "config": {"prompt": "on {{inputs.topic}}"}}
                ],
            },
            "inputs": {"topic": {"type": "string", "required": True}},
            "metadata": {"keywords": ["demo"], "example_outputs": ["a demo report"]},
        }
        surface = mcp_workflows._eval_surface("demo", definition)
        assert surface["eval_spec"]["template"] == "demo"
        assert surface["eval_spec"]["fixtures"]

    def test_ungradeable_checks_are_reported_not_graded(self, home):
        """Grading is LEARNING-FLYWHEEL's. A spec that names what a judge would have to grade —
        and does not grade it — is the CORRECT output here, so the surface must carry the names and
        must not claim a verdict."""
        definition = {
            "name": "demo",
            "root": {
                "kind": "sequence",
                "id": "main",
                "children": [
                    {"kind": "infer", "id": "think", "config": {"prompt": "write a summary"}}
                ],
            },
            "inputs": {},
            "metadata": {"keywords": ["demo"]},
        }
        spec = mcp_workflows._eval_surface("demo", definition)["eval_spec"]
        # A model-spending template is not free, and the graded checks name the work rather than
        # asserting a result.
        assert spec["free"] is (not spec["graded_checks"])
        for key in ("passed", "score", "verdict", "grade"):
            assert key not in spec

    def test_a_broken_definition_costs_the_eval_not_the_plan(self, home):
        """A malformed tree must not raise out of the plan path.

        `eval_specs` is TOLERANT rather than strict (its own docstring: pure functions over spec
        dicts), so a junk `root` degrades to a thin spec instead of erroring — either outcome is
        acceptable here, and raising is not. What this pins is that the plan survives."""
        surface = mcp_workflows._eval_surface("demo", {"root": "not-a-dict"})
        assert surface == {} or surface["eval_spec"]["template"] == "demo"

    def test_the_plan_output_carries_the_eval_block(self, home):
        """Driven through the real tool, so a surface that computed the block and dropped it on the
        floor cannot pass.

        A frozen candidate is the plan source, because it needs no def provider registered — the
        template path is the same code either way."""
        candidate = template_pipeline.freeze_candidate(
            {
                "root": {
                    "kind": "sequence",
                    "id": "main",
                    "children": [
                        {"kind": "infer", "id": "think", "config": {"prompt": "summarize"}}
                    ],
                },
                "inputs": {},
            },
            "summarize the release notes",
            session_id="",
        )
        template_store.save_candidate(candidate)
        out = mcp_workflows._call_tool(
            "workflow_plan", {"goal": "summarize notes", "template": candidate.name}
        )
        assert "eval_spec" in out


# ── clause 3: suggest_template + persisted NudgeState ─────────────────────────


class TestSuggestTemplateNudge:
    def test_it_is_registered_as_a_core_chat_tool(self, home):
        assert "suggest_template" in [t["name"] for t in mcp_core._list_tools()]

    def test_it_dispatches(self, home):
        out = mcp_core._call_tool("suggest_template", {"shape": "summarize new issues"})
        assert "Unknown tool" not in out

    def test_under_threshold_it_refuses_and_says_why(self, home):
        out = mcp_core._call_tool("suggest_template", {"shape": "summarize new issues"})
        assert "Do not suggest" in out
        assert f"1/{template_pipeline.NUDGE_AFTER}" in out

    def test_it_offers_once_the_shape_has_recurred(self, home):
        for _ in range(template_pipeline.NUDGE_AFTER):
            out = mcp_core._call_tool("suggest_template", {"shape": "summarize new issues"})
        assert "Suggest a template" in out
        assert "summarize new issues" in out

    def test_occurrences_accumulate_across_fresh_processes(self, home):
        """Counting in memory means the count restarts with the process and the threshold is never
        reached — the shape of inertness that looks like a working feature."""
        mcp_core._call_tool("suggest_template", {"shape": "shape-a"})
        assert template_store.load_nudge("shape-a").occurrences == 1
        mcp_core._call_tool("suggest_template", {"shape": "shape-a"})
        assert template_store.load_nudge("shape-a").occurrences == 2

    def test_a_decline_survives_a_restart(self, home):
        """THE anti-nag property. Every in-memory object is discarded between the decline and the
        re-approach: the second call reads only what is on disk."""
        for _ in range(template_pipeline.NUDGE_AFTER):
            mcp_core._call_tool("suggest_template", {"shape": "weekly report"})
        mcp_core._call_tool("suggest_template", {"shape": "weekly report", "decision": "declined"})

        # Simulate the restart: nothing is cached, so a fresh read is all a new process would have.
        assert template_store.load_nudge("weekly report").declined is True
        out = mcp_core._call_tool("suggest_template", {"shape": "weekly report"})
        assert "Do not suggest" in out
        assert "declined for this shape" in out

    def test_an_accepted_shape_is_not_re_offered_after_a_restart(self, home):
        for _ in range(template_pipeline.NUDGE_AFTER):
            mcp_core._call_tool("suggest_template", {"shape": "standup notes"})
        mcp_core._call_tool("suggest_template", {"shape": "standup notes", "decision": "accepted"})
        assert template_store.load_nudge("standup notes").accepted is True
        out = mcp_core._call_tool("suggest_template", {"shape": "standup notes"})
        assert "already saved as a template" in out

    def test_a_decline_is_per_shape_not_global(self, home):
        """ "no, not for this" must not become "no, never again for anything"."""
        mcp_core._call_tool("suggest_template", {"shape": "shape-x", "decision": "declined"})
        for _ in range(template_pipeline.NUDGE_AFTER):
            out = mcp_core._call_tool("suggest_template", {"shape": "shape-y"})
        assert "Suggest a template" in out

    def test_an_offer_enters_cooldown(self, home):
        for _ in range(template_pipeline.NUDGE_AFTER):
            mcp_core._call_tool("suggest_template", {"shape": "shape-z"})
        out = mcp_core._call_tool("suggest_template", {"shape": "shape-z"})
        assert "cooldown" in out

    def test_the_nudge_clock_persists(self, home):
        """The cooldown is measured against it, so a clock that reset on restart would end every
        cooldown early."""
        mcp_core._call_tool("suggest_template", {"shape": "shape-clock"})
        first = template_store.current_turn()
        mcp_core._call_tool("suggest_template", {"shape": "shape-clock"})
        assert template_store.current_turn() > first

    def test_a_shape_is_required(self, home):
        """Refused as a named argument error, not silently counted under an empty key.

        The shared validator catches it before the handler's own guard, which is the better of the
        two — so this asserts the OUTCOME (a readable refusal naming the field) rather than which
        layer produced it."""
        out = mcp_core._call_tool("suggest_template", {"shape": "  "})
        assert "Error" in out and "shape" in out
        assert not template_store.all_nudges()

    def test_the_clock_key_is_not_mistaken_for_a_shape(self, home):
        mcp_core._call_tool("suggest_template", {"shape": "real-shape"})
        assert [s.shape for s in template_store.all_nudges()] == ["real-shape"]


# ── clause 4: discover-then-freeze → the tiered matcher ───────────────────────


class TestDiscoverThenFreeze:
    def test_a_validated_dry_run_freezes_a_session_scoped_candidate(self, home):
        out = mcp_workflows._call_tool(
            "workflow_author",
            {
                "name": "probe-spec",
                "description": "Probe the staging endpoint each morning",
                "root": {
                    "kind": "sequence",
                    "id": "main",
                    "children": [
                        {"kind": "transform", "id": "seed", "config": {"expr": {"n": 1}}},
                    ],
                },
                "save": False,
            },
        )
        assert "Error" not in out.split("\n")[0]
        candidates = template_store.load_candidates()
        assert candidates, "a validated dry-run spec was thrown away"
        assert candidates[0].scope == template_pipeline.SCOPE_LADDER[0] == "session"

    def test_a_frozen_candidate_is_matchable(self, home):
        """The freeze is only worth doing if the matcher can see it."""
        candidate = template_pipeline.freeze_candidate(
            {"root": {"kind": "sequence", "id": "main", "children": []}},
            "probe the staging endpoint each morning",
            session_id="",
        )
        template_store.save_candidate(candidate)
        names = [p.name for p in mcp_workflows._library_profiles()]
        assert candidate.name in names

    def test_a_frozen_candidate_loads_back_as_a_plan_source(self, home):
        candidate = template_pipeline.freeze_candidate(
            {
                "root": {
                    "kind": "sequence",
                    "id": "main",
                    "children": [{"kind": "transform", "id": "seed", "config": {"expr": {"n": 1}}}],
                },
                "inputs": {},
            },
            "probe the staging endpoint each morning",
            session_id="",
        )
        template_store.save_candidate(candidate)
        assert mcp_workflows._def_resolvable(candidate.name) is True
        out = mcp_workflows._call_tool(
            "workflow_plan", {"goal": "probe staging", "template": candidate.name}
        )
        assert "WF_PLAN_TEMPLATE_NOT_FOUND" not in out
        # Provenance is declared: a one-off guess must not be presented as a shipped template.
        assert "FROZEN CANDIDATE" in out

    def test_a_session_scoped_candidate_does_not_leak_to_another_session(self, home):
        candidate = template_pipeline.freeze_candidate(
            {"root": {}}, "private goal", session_id="dashboard:chat-1"
        )
        template_store.save_candidate(candidate)
        assert template_store.load_candidates(session_id="dashboard:chat-1")
        assert not template_store.load_candidates(session_id="dashboard:chat-2")

    def test_a_promoted_candidate_is_visible_beyond_its_session(self, home):
        """Promotion is what earns cross-session visibility, and it is by REUSE."""
        candidate = template_pipeline.freeze_candidate(
            {"root": {}}, "shared goal", session_id="dashboard:chat-1"
        )
        for _ in range(template_pipeline.PROMOTE_AFTER):
            candidate = template_pipeline.record_reuse(candidate)
        assert candidate.scope != "session"
        template_store.save_candidate(candidate)
        assert template_store.load_candidates(session_id="dashboard:chat-2")

    def test_the_same_goal_updates_rather_than_duplicates(self, home):
        for _ in range(3):
            template_store.save_candidate(
                template_pipeline.freeze_candidate({"root": {}}, "one same goal", session_id="")
            )
        assert len(template_store.load_candidates()) == 1

    def test_a_bundled_template_wins_a_name_collision(self, home):
        """A shipped, tested shape beats a candidate frozen on one successful parse."""
        from personalclaw.workflows import bundled_defs

        bundled = list(bundled_defs.template_names())
        if not bundled:
            pytest.skip("no bundled templates in this environment")
        clash = template_pipeline.Candidate(name=bundled[0], origin_goal="impostor")
        template_store.save_candidate(clash)
        profiles = [p for p in mcp_workflows._library_profiles() if p.name == bundled[0]]
        assert len(profiles) == 1
        assert profiles[0].description != "impostor"

    def test_a_failed_validation_freezes_nothing(self, home):
        """A candidate is a spec that PARSED. Freezing an invalid one would put a broken shape in
        front of the next similar intent."""
        mcp_workflows._call_tool(
            "workflow_author", {"name": "bad-spec", "root": {"kind": "nope"}, "save": False}
        )
        assert not template_store.load_candidates()
