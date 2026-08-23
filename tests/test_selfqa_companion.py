"""SV-9 — the Self-QA Companion core, one test class per clause of the atom's `done_when`.

The four clauses are checked independently, and each one carries a vacuity floor — a mutation
that would make it red — because every mechanism here is one whose failure mode is *looking
fine*. A watcher that never fires files nothing; a triage that never runs files nothing; a
filing step that files three times still files at least one. "No findings appeared" is the
expected output of a healthy run AND of a completely dead loop, so nothing in this file asserts
an absence without also asserting the positive record that distinguishes the two.

Every test drives a REAL temporary git repository, the REAL installed cron script (imported from
the file the installer wrote, not a parallel copy), the REAL run ledger, and the REAL native
inbox/task sinks. The session-wide `_isolate_real_home_writers` fixture in `conftest.py` redirects
`config_dir()` to a tmp dir, so the ledger and task writes here cannot reach `~/.personalclaw`;
the crons dir is additionally passed explicitly.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import personalclaw.inbox_providers.native_source as ns
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.selfqa_finding_provider import SelfQaFindingActionProvider
from personalclaw.action_providers.selfqa_triage_provider import SelfQaTriageActionProvider
from personalclaw.inbox import InboxState, InboxStore
from personalclaw.ledger import DECISION, STEP_SKIPPED
from personalclaw.selfqa import findings as findings_mod
from personalclaw.selfqa.findings import ScenarioFinding, file_finding
from personalclaw.selfqa.install import COMMIT_WATCH_SCRIPT, install_commit_watch_script
from personalclaw.selfqa.ledger import record_triage
from personalclaw.selfqa.triage import (
    IMPACT_NONE,
    IMPACT_TEST,
    IMPACT_USER,
    CommitTriage,
    classify_paths,
    triage_commit,
)
from personalclaw.workflows.journal import Journal, ledger

BUNDLED = Path(__file__).resolve().parents[1] / "src/personalclaw/workflows/bundled/self-qa"


def _run(coro):
    return asyncio.run(coro)


# ── fixtures ────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A real git repo with one commit. Local identity, so no global config is consulted."""
    root = tmp_path / "watched"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "selfqa@example.invalid")
    _git(root, "config", "user.name", "Self QA Test")
    (root / "README.md").write_text("start\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def commit(repo: Path, rel: str, body: str, message: str) -> str:
    """Write `rel`, commit it, return the new SHA."""
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def watcher(tmp_path):
    """The INSTALLED cron script, imported from the file the installer wrote.

    Loading the installed copy rather than the package module is the point: it proves the file a
    Schedule would actually execute behaves correctly, and it exercises the installer on the way.
    A test against a parallel implementation would pass while the shipped script was broken.
    """
    crons = tmp_path / "crons"
    path = install_commit_watch_script(crons)
    assert path.name == COMMIT_WATCH_SCRIPT
    spec = importlib.util.spec_from_file_location("_selfqa_watch_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inbox_state(tmp_path, monkeypatch):
    """A real InboxStore behind the native push sink, plus a per-test task directory.

    The task dir is redirected per TEST, not just away from the real home: `_tasks_dir()` derives
    from the session-scoped `config_dir()` guard, so without this every test in the class would
    see the tasks the previous one filed and "exactly one" would be measured against a running
    total. That is the same class of error the clause is about — a count read beside the thing it
    is counting.
    """
    from personalclaw.tasks import native as native_tasks

    task_home = tmp_path / "task-home"
    task_home.mkdir()
    monkeypatch.setattr(native_tasks, "config_dir", lambda: task_home)

    store = InboxStore(path=tmp_path / "inbox.json")
    store.load()
    st = MagicMock()
    st._inbox_svc = None
    st._inbox_store = store
    st._inbox_state = InboxState(path=tmp_path / "inbox_state.json")
    st.broadcast_ws = lambda ev, payload: None
    ns.set_dashboard_state(st)
    findings_mod.reset_filed_keys()
    yield store
    findings_mod.reset_filed_keys()
    # `set_dashboard_state` is a module global. Left pointing at this test's MagicMock, the next
    # test in the same xdist worker that calls `post_to_inbox` writes into a torn-down tmp dir
    # instead of taking the no-state path — a cross-test leak `test_inbox_native_source.py`
    # already clears and this fixture did not.
    ns.set_dashboard_state(None)


def inbox_items(store) -> list:
    """`InboxStore.items` is a dict keyed by id — ordered by creation for these assertions."""
    return sorted(store.items.values(), key=lambda i: i.created_at)


# ── Clause 1: a real commit fires the companion within one cron interval ─────


class TestClauseOneWatcherFires:
    """`a real commit to the watched repo fires the companion within one cron interval`.

    Driven directly rather than through a real cron tick, deliberately: deleting the last cron
    entry mid-session has been observed to kill the scheduler, and a repo-tracked script run by
    workspace cron changes behaviour with the checked-out branch. The unit under test is the
    script's decision, and one `check()` call is one interval.
    """

    def _ctx(self, repo: Path):
        ctx = MagicMock()
        ctx.message = str(repo)
        return ctx

    def test_first_sight_records_head_and_stays_quiet(self, watcher, repo):
        """Enabling the companion must not fire a run against whatever was checked out."""
        with pytest.raises(watcher.Skip):
            watcher.check(self._ctx(repo))
        state = watcher.read_state()
        assert state["last_sha"] == _git(repo, "rev-parse", "HEAD")
        assert state["repo"] == str(repo)

    def test_a_real_commit_fires_within_one_interval(self, watcher, repo):
        """The clause itself: one commit, then ONE tick, and the SHA is in the payload."""
        with pytest.raises(watcher.Skip):
            watcher.check(self._ctx(repo))

        sha = commit(repo, "src/personalclaw/thing.py", "x = 1\n", "feat: a thing")

        with pytest.raises(watcher.Report) as caught:
            watcher.check(self._ctx(repo))

        payload = json.loads(caught.value.message)
        assert payload["commits"] == [sha], payload
        assert payload["repo"] == str(repo)
        # The state advanced, so the same commit does not re-fire and burn a run every interval.
        assert watcher.read_state()["last_sha"] == sha

    def test_no_new_commit_is_silent(self, watcher, repo):
        """The vacuity floor for the clause above.

        Without it, a script that raised `Report` unconditionally would pass
        `test_a_real_commit_fires_within_one_interval` — the SHA would be right, and the fire
        would be meaningless.
        """
        with pytest.raises(watcher.Skip):
            watcher.check(self._ctx(repo))
        commit(repo, "src/personalclaw/thing.py", "x = 1\n", "feat: a thing")
        with pytest.raises(watcher.Report):
            watcher.check(self._ctx(repo))
        # Third tick, nothing new.
        with pytest.raises(watcher.Skip):
            watcher.check(self._ctx(repo))

    def test_several_commits_all_arrive_oldest_first(self, watcher, repo):
        """A push is a range, not a commit. Missing the middle of one is how a regression hides."""
        with pytest.raises(watcher.Skip):
            watcher.check(self._ctx(repo))
        first = commit(repo, "src/a.py", "a\n", "feat: a")
        second = commit(repo, "src/b.py", "b\n", "feat: b")
        with pytest.raises(watcher.Report) as caught:
            watcher.check(self._ctx(repo))
        assert json.loads(caught.value.message)["commits"] == [first, second]

    def test_an_unreadable_repo_is_silent_rather_than_a_nag(self, watcher, tmp_path):
        """A watcher pointed at a moved directory must not deliver a message every interval."""
        ctx = MagicMock()
        ctx.message = str(tmp_path / "not-a-repo")
        with pytest.raises(watcher.Skip):
            watcher.check(ctx)

    def test_the_env_var_overrides_the_job_message(self, watcher, repo, monkeypatch):
        """`agent.self_qa.watched_repo` is the configured answer; the job message is not."""
        monkeypatch.setenv(watcher.REPO_ENV, str(repo))
        ctx = MagicMock()
        ctx.message = str(repo.parent / "somewhere-else")
        with pytest.raises(watcher.Skip):
            watcher.check(ctx)
        assert watcher.read_state()["repo"] == str(repo)

    def test_the_repo_comes_from_the_file_not_the_env_or_the_message(self, watcher, repo, tmp_path):
        """The channel the sandbox can actually read.

        An env var is dropped by `build_child_env`'s allowlist and a trigger fire passes
        `context=""`, so a script that depended on either would be inert in production while
        passing any test that set them. This asserts the file is sufficient ON ITS OWN — the ctx
        carries no message and no env var is set.
        """
        from personalclaw.selfqa.install import write_watch_config

        write_watch_config(str(repo), tmp_path / "crons")
        ctx = MagicMock()
        ctx.message = ""
        with pytest.raises(watcher.Skip):
            watcher.check(ctx)
        assert watcher.read_state()["repo"] == str(repo), "the config file was not the source"

    def test_a_new_commit_starts_the_workflow_run(self, watcher, repo, tmp_path):
        """The loop's closing edge: the watcher STARTS the run rather than hoping something reads
        its message. Without this the `Report` would be a message nobody acts on."""
        from personalclaw.selfqa.install import write_watch_config

        write_watch_config(str(repo), tmp_path / "crons")
        ctx = MagicMock()
        ctx.message = ""
        ctx.call_tool = MagicMock(return_value={"run_id": "r-1"})

        with pytest.raises(watcher.Skip):
            watcher.check(ctx)
        assert ctx.call_tool.call_count == 0, "first sight started a run"

        sha = commit(repo, "src/personalclaw/thing.py", "x = 1\n", "feat: a thing")
        with pytest.raises(watcher.Report):
            watcher.check(ctx)

        assert ctx.call_tool.call_count == 1
        tool, args = ctx.call_tool.call_args[0]
        assert tool == "workflow_start"
        assert args["name"] == "self-qa"
        assert args["inputs"]["commits"] == [sha]
        # Keyed on the frontier, so an overlapping tick cannot open a second browser session
        # against the same commit.
        assert args["idempotency_key"] == f"selfqa-{sha}"

    def test_the_installer_is_idempotent_and_repairs_a_edited_copy(self, tmp_path):
        crons = tmp_path / "crons"
        first = install_commit_watch_script(crons)
        stamp = first.stat().st_mtime_ns
        again = install_commit_watch_script(crons)
        assert again == first
        assert again.stat().st_mtime_ns == stamp, "an identical install rewrote the file"
        first.write_text("# clobbered\n", encoding="utf-8")
        install_commit_watch_script(crons)
        assert "def check(" in first.read_text(encoding="utf-8")


# ── Clause 2: a test-only commit yields a ledger-only skip with a rationale ──


class TestClauseTwoLedgerOnlySkip:
    """`a test-only commit yields a ledger-only skip with a one-line rationale`.

    This is an assertion about what gets WRITTEN. The row and its rationale are the only things
    that distinguish "ran and correctly skipped" from "never fired", so the row is asserted
    positively, its rationale is asserted non-empty and single-line, and the absence of findings
    is asserted only alongside it.
    """

    def test_a_test_only_commit_classifies_as_test_with_a_reason(self, repo):
        sha = commit(repo, "tests/test_thing.py", "def test_x():\n    pass\n", "test: assertion")
        verdict = triage_commit(repo, sha)
        assert verdict.impact == IMPACT_TEST
        assert verdict.skipped is True
        assert verdict.rationale.strip()
        assert "\n" not in verdict.rationale, "the rationale must be ONE line"

    def test_the_skip_writes_a_ledger_row_carrying_the_rationale(self, repo):
        """The clause. Read back through the real ledger reader, not the writer's return value."""
        sha = commit(repo, "tests/test_thing.py", "def test_x():\n    pass\n", "test: assertion")
        verdict = triage_commit(repo, sha)
        run_id = "selfqa-clause2"
        record_triage(Journal(run_id=run_id), verdict)

        rows = ledger(run_id, kinds={STEP_SKIPPED})
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["sha"] == sha
        assert row["impact"] == IMPACT_TEST
        assert row["rationale"].strip(), "a skip row with no rationale is the silence, restated"
        assert "\n" not in row["rationale"]

    def test_an_impactful_commit_writes_a_decision_not_a_skip(self, repo):
        """The vacuity floor.

        Without it, a `record_triage` that wrote `step_skipped` for EVERY commit would pass the
        test above while classifying nothing — the loop would skip its way through a release.
        """
        sha = commit(repo, "src/personalclaw/thing.py", "x = 1\n", "feat: a thing")
        verdict = triage_commit(repo, sha)
        assert verdict.impact == IMPACT_USER
        run_id = "selfqa-clause2-floor"
        record_triage(Journal(run_id=run_id), verdict)
        assert ledger(run_id, kinds={STEP_SKIPPED}) == []
        assert len(ledger(run_id, kinds={DECISION})) == 1

    def test_an_option_shaped_ref_never_reaches_git(self, repo, tmp_path, monkeypatch):
        """`commits` is model-reachable — a run can be started by an agent calling
        `workflow_start` — and the argv is fixed, so the exposure is option injection rather than
        command injection. Measured, not theorised: with the hex guard disabled, `--output=<path>`
        is a real `git show` diff option and git wrote the commit subject to that exact path. The
        ref is validated before use now, and a refused commit still gets a VERDICT so the refusal
        cannot masquerade as "the companion never ran".

        The payload path stays under `tmp_path`: a shared absolute path would let one run's
        artifact fail a later run, which is how this test failed the first time it was written.
        """
        import personalclaw.selfqa.triage as triage_mod

        payload = tmp_path / "pwned"
        seen: list[tuple] = []
        real_git = triage_mod._git
        monkeypatch.setattr(
            triage_mod, "_git", lambda root, *args: (seen.append(args), real_git(root, *args))[1]
        )

        verdict = triage_commit(repo, f"--output={payload}")
        assert verdict.impact == IMPACT_NONE
        assert "refused" in verdict.rationale
        assert seen == [], f"the refused ref still reached git: {seen}"
        assert not payload.exists(), "git ran and wrote the option's target"

        # The floor: a real sha DOES reach git, or the assertions above pass because triage never
        # calls git at all and the guard is proving nothing.
        good = _git(repo, "rev-parse", "HEAD")
        assert triage_commit(repo, good).impact in (IMPACT_USER, IMPACT_TEST, IMPACT_NONE)
        assert seen, "a valid sha did not reach git either — the guard is vacuous"

    def test_a_rationale_less_verdict_is_refused(self):
        """The record's whole job is answering "why?" — an empty answer is a defect, not a value."""
        with pytest.raises(ValueError, match="rationale-less"):
            record_triage(Journal(run_id="selfqa-empty"), CommitTriage("abc", IMPACT_TEST, "  "))

    def test_the_triage_provider_records_every_verdict_and_files_nothing(self, repo, inbox_state):
        """The CALL SITE, not just the mechanism: the provider the template dispatches.

        Also the "ledger-ONLY" half — the same run that wrote the skip rows filed no Inbox item.
        """
        test_sha = commit(repo, "tests/test_a.py", "def test_a():\n    pass\n", "test: a")
        doc_sha = commit(repo, "docs/guide.md", "# guide\n", "docs: guide")
        run_id = "selfqa-clause2-provider"

        assert len(inbox_items(inbox_state)) == 0, "the inbox was not empty before the run"

        result = _run(
            SelfQaTriageActionProvider().execute(
                {"repo": str(repo), "commits": [test_sha, doc_sha]},
                ActionContext(event="workflow_node", payload={"run_id": run_id}),
            )
        )
        assert result.success, result.error
        output = json.loads(result.stdout)
        assert output["has_impactful"] is False
        assert output["recorded"] == 2
        assert [v["impact"] for v in output["skipped"]] == [IMPACT_TEST, IMPACT_NONE]

        rows = ledger(run_id, kinds={STEP_SKIPPED})
        assert len(rows) == 2, rows
        assert all(r["rationale"].strip() for r in rows)
        assert len(inbox_items(inbox_state)) == 0, "a ledger-only skip filed an inbox item"

    def test_the_provider_routes_an_impactful_commit_forward(self, repo):
        """The vacuity floor for the provider: `has_impactful` must be able to be true."""
        sha = commit(repo, "src/personalclaw/thing.py", "x = 1\n", "feat: a thing")
        result = _run(
            SelfQaTriageActionProvider().execute(
                {"repo": str(repo), "commits": [sha]},
                ActionContext(event="workflow_node", payload={"run_id": "selfqa-c2-fwd"}),
            )
        )
        output = json.loads(result.stdout)
        assert output["has_impactful"] is True
        assert [v["sha"] for v in output["impactful"]] == [sha]

    def test_the_scenario_cap_bounds_one_fire(self, repo):
        """A push of many commits must not open many browser sessions."""
        shas = [commit(repo, f"src/m{i}.py", f"x={i}\n", f"feat: {i}") for i in range(5)]
        result = _run(
            SelfQaTriageActionProvider().execute(
                {"repo": str(repo), "commits": shas, "max_scenarios": 2},
                ActionContext(event="workflow_node", payload={"run_id": "selfqa-c2-cap"}),
            )
        )
        output = json.loads(result.stdout)
        assert len(output["verdicts"]) == 5, "every commit still gets a verdict"
        assert len(output["impactful"]) == 2, "the cap did not bound the scenarios"

    @pytest.mark.parametrize(
        "paths,expected",
        [
            (["tests/test_a.py"], IMPACT_TEST),
            (["src/x/test_a.py"], IMPACT_TEST),
            (["web/src/a.test.ts"], IMPACT_TEST),
            (["conftest.py"], IMPACT_TEST),
            (["docs/a.md"], IMPACT_NONE),
            (["README.md"], IMPACT_NONE),
            ([".github/workflows/ci.yml"], IMPACT_NONE),
            ([], IMPACT_NONE),
            (["src/personalclaw/a.py"], IMPACT_USER),
            (["web/src/App.tsx"], IMPACT_USER),
            # One shipped line among a hundred test lines is still a shipped line.
            (["tests/test_a.py", "src/personalclaw/a.py"], IMPACT_USER),
            (["tests/test_a.py", "docs/a.md"], IMPACT_TEST),
        ],
    )
    def test_the_classifier_verdicts(self, paths, expected):
        impact, rationale = classify_paths(paths)
        assert impact == expected, (paths, rationale)
        assert rationale.strip() and "\n" not in rationale


# ── Clause 3: the scenario drives the real UI via Chrome DevTools MCP ────────


class TestClauseThreeScenarioDrivesTheUI:
    """`a user-impacting commit generates a scenario that mutates state through the real UI via
    Chrome DevTools MCP`.

    **What is asserted here is the template contract, not a UI drive.** Executing this clause
    end-to-end needs a reachable Chrome DevTools MCP server and a running gateway built from the
    commit under test; that was not available in this environment, and no test here pretends
    otherwise. What these tests do enforce is every property of the shipped template that the
    clause depends on — the routing, the MCP binding, the state-mutation requirement, and the
    engine-enforced proof gate — so the parts that CAN be checked are checked rather than
    assumed.
    """

    @staticmethod
    def _spec() -> dict:
        return json.loads((BUNDLED / "workflow.json").read_text(encoding="utf-8"))

    @staticmethod
    def _nodes(spec: dict) -> dict:
        found: dict[str, dict] = {}

        def walk(node):
            if isinstance(node, dict):
                if "kind" in node and "id" in node:
                    found[node["id"]] = node
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(spec["root"])
        return found

    def test_the_template_ships(self):
        assert (BUNDLED / "workflow.json").is_file()
        assert self._spec()["name"] == "self-qa"

    def test_an_impactful_commit_reaches_the_scenario_subtree(self):
        """Triage must select the scenario path, or nothing is ever driven."""
        nodes = self._nodes(self._spec())
        route = nodes["route"]
        assert "nodes.triage.output.has_impactful" in route["config"]["on"]
        impactful_case = route["cases"]["true"]
        assert impactful_case["kind"] == "foreach"
        assert "nodes.triage.output.impactful" in impactful_case["config"]["items"]
        for node_id in ("scenario-gen", "execute", "evidence", "file-findings"):
            assert node_id in nodes, f"the scenario subtree has no {node_id} node"

    def test_the_execute_stage_binds_chrome_devtools_mcp(self):
        """The clause names the driver. A stage that does not say so drives nothing."""
        prompt = self._nodes(self._spec())["execute"]["config"]["prompt"]
        assert "Chrome DevTools MCP" in prompt
        assert "NEW page" in prompt, "co-tenant discipline: never take over the user's page"

    def test_the_scenario_prompt_requires_a_state_mutation(self):
        """Render-checking is the failure mode this clause exists to exclude."""
        cfg = self._nodes(self._spec())["scenario-gen"]["config"]
        prompt = cfg["prompt"]
        assert "MUST mutate state" in prompt
        assert "Opening a page and reading it is not a scenario" in prompt
        # The mutation is a required output field AND gated by success_when, so a scenario that
        # forgot to name one fails the node instead of proceeding to drive nothing.
        assert "mutation" in cfg["schema"]
        assert "output.mutation != ''" in cfg["success_when"]

    def test_the_scenario_prompt_carries_the_repo_gotchas(self):
        """A pass measured against a stale backend or a cached bundle is a false pass."""
        prompt = self._nodes(self._spec())["execute"]["config"]["prompt"]
        assert "does NOT hot-reload" in prompt
        assert "SYMLINK" in prompt
        assert "hard-reload" in prompt

    def test_the_proof_gate_is_engine_enforced(self):
        """`required_artifacts` is checked by the engine, so the proof cannot be self-reported."""
        cfg = self._nodes(self._spec())["evidence"]["config"]
        assert cfg["required_artifacts"] == [
            "screenshots/*.png",
            "recording.mp4",
            "manifest.json",
        ]

    def test_ffmpeg_is_a_preflight_requirement_not_a_late_surprise(self):
        """Declared, so a run blocks cleanly at start instead of degrading at the evidence node."""
        binaries = self._spec()["metadata"]["requirements"]["binaries"]
        assert "ffmpeg" in binaries and "git" in binaries

    def test_the_fix_branch_is_off_by_default_and_never_pushed(self):
        spec = self._spec()
        assert spec["inputs"]["fix_branch_enabled"]["default"] is False
        prompt = self._nodes(spec)["fix-branch"]["config"]["prompt"]
        assert "Do NOT merge it and do NOT push it" in prompt


# ── Clause 4: a failing scenario files ONE Inbox item + ONE Task ─────────────


class TestClauseFourFilesOneOfEach:
    """`a failing scenario files one Inbox item + one Task` (Success Criterion #6).

    One is a floor and a ceiling. Both ends are asserted, and the counts are read before the
    call as well as after, so "exactly one" cannot be satisfied by an item that was already
    there.
    """

    @staticmethod
    def _finding(**over) -> ScenarioFinding:
        base = {
            "sha": "0123456789abcdef",
            "scenario_id": "s1",
            "title": "Sending a message does not persist it",
            "scenario_text": "Open the chat, send 'hello', reload.",
            "repro_steps": ["open /chat", "send hello", "reload"],
            "evidence_ref": "artifact:bundle-1",
        }
        base.update(over)
        return ScenarioFinding(**base)

    @staticmethod
    def _tasks():
        from personalclaw.tasks.registry import list_all_tasks

        tasks, _total = _run(list_all_tasks())
        return tasks

    def test_one_failing_scenario_files_exactly_one_of_each(self, inbox_state):
        assert len(inbox_items(inbox_state)) == 0, "the inbox was not empty before the call"
        assert len(self._tasks()) == 0, "tasks existed before the call"

        filed = _run(file_finding(self._finding()))

        assert len(inbox_items(inbox_state)) == 1, [i.message for i in inbox_items(inbox_state)]
        tasks = self._tasks()
        assert len(tasks) == 1, [t.title for t in tasks]
        assert filed.already_filed is False
        assert filed.task_id == tasks[0].id
        assert filed.inbox_item_id == inbox_items(inbox_state)[0].id

    def test_the_inbox_item_says_what_broke_and_points_at_the_evidence(self, inbox_state):
        _run(file_finding(self._finding()))
        message = inbox_items(inbox_state)[0].message
        assert "01234567" in message, "the item does not name the commit"
        assert "does not persist" in message
        assert "artifact:bundle-1" in message, "the item does not reach the evidence"

    def test_the_task_carries_the_scenario_and_the_reproduction(self, inbox_state):
        _run(file_finding(self._finding()))
        task = self._tasks()[0]
        assert "self-qa" in task.labels
        assert "Open the chat" in task.description
        assert "1. open /chat" in task.description, "the reproduction is not actionable"

    def test_filing_the_same_finding_twice_still_files_one_of_each(self, inbox_state):
        """The CEILING. Three inbox items for one failure trains the user to ignore the inbox.

        A resumed run replays the node, so this is the realistic case, not a hypothetical.
        """
        first = _run(file_finding(self._finding()))
        second = _run(file_finding(self._finding()))

        assert first.already_filed is False
        assert second.already_filed is True
        assert len(inbox_items(inbox_state)) == 1, [i.message for i in inbox_items(inbox_state)]
        assert len(self._tasks()) == 1

    def test_a_task_failure_does_not_double_post_the_inbox_on_its_replay(
        self, inbox_state, monkeypatch
    ):
        """The partial-failure path, which is where "exactly one" actually breaks.

        The Inbox item is posted first and the Task second. If the Task raises, an all-or-nothing
        dedup flag has not been set, so the replay starts from the top and posts a SECOND
        interrupt for one failure. Both ends are asserted after the replay: exactly one Inbox item
        (the ceiling — the bug this closes) and exactly one Task (the floor — a dedup flag set
        before the Task instead of after would satisfy the ceiling by never filing the work).
        """
        import personalclaw.tasks.registry as task_registry

        real_create = task_registry.create_task
        calls = {"n": 0}

        async def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("task store unavailable")
            return await real_create(*a, **kw)

        monkeypatch.setattr(task_registry, "create_task", flaky)

        with pytest.raises(RuntimeError, match="task store unavailable"):
            _run(file_finding(self._finding()))
        assert len(inbox_items(inbox_state)) == 1, "the inbox item did not land before the failure"
        assert len(self._tasks()) == 0

        replay = _run(file_finding(self._finding()))

        assert replay.already_filed is False, "the replay reported filed while the task was missing"
        assert len(inbox_items(inbox_state)) == 1, [i.message for i in inbox_items(inbox_state)]
        assert len(self._tasks()) == 1, [t.title for t in self._tasks()]
        assert calls["n"] == 2, "the replay did not retry the task"

    def test_a_different_scenario_on_the_same_commit_files_its_own(self, inbox_state):
        """The vacuity floor for the dedup.

        Without it, a dedup keyed on the SHA alone would pass the ceiling test above while
        swallowing every finding after the first — one commit, one report, forever.
        """
        _run(file_finding(self._finding(scenario_id="s1")))
        _run(file_finding(self._finding(scenario_id="s2")))
        assert len(inbox_items(inbox_state)) == 2
        assert len(self._tasks()) == 2

    def test_the_provider_call_site_files_one_of_each(self, inbox_state):
        """The CALL SITE the template dispatches, not just the function behind it."""
        result = _run(
            SelfQaFindingActionProvider().execute(
                {
                    "sha": "0123456789abcdef",
                    "scenario_id": "s1",
                    "title": "Sending a message does not persist it",
                    "scenario_text": "send and reload",
                    "repro_steps": ["send", "reload"],
                },
                ActionContext(event="workflow_node", payload={"run_id": "selfqa-c4"}),
            )
        )
        assert result.success, result.error
        assert len(inbox_items(inbox_state)) == 1
        assert len(self._tasks()) == 1

    def test_the_provider_replay_is_a_suppressed_skip_not_a_second_filing(self, inbox_state):
        provider = SelfQaFindingActionProvider()
        config = {
            "sha": "0123456789abcdef",
            "scenario_id": "s1",
            "title": "Sending a message does not persist it",
        }
        ctx = ActionContext(event="workflow_node", payload={"run_id": "selfqa-c4-replay"})
        _run(provider.execute(config, ctx))
        again = _run(provider.execute(config, ctx))
        assert again.success and again.outcome == "skip"
        assert len(inbox_items(inbox_state)) == 1
        assert len(self._tasks()) == 1

    def test_the_provider_refuses_an_incomplete_finding(self, inbox_state):
        """A finding with no title would file an untitled task nobody can triage."""
        result = _run(
            SelfQaFindingActionProvider().execute(
                {"sha": "abc", "scenario_id": "s1"},
                ActionContext(event="workflow_node", payload={}),
            )
        )
        assert not result.success
        assert len(inbox_items(inbox_state)) == 0
        assert len(self._tasks()) == 0


# ── the config round trip (SELF-VERIFICATION §5, all four wiring points) ─────


class TestSelfQaConfigRoundTrip:
    """The four points, asserted individually. `test_config_roundtrip.py` covers three of five
    contract points generically, so the write path and the load mapping are pinned here too —
    an omission in `load()` is a silent drop that no schema test sees.
    """

    def test_defaults_are_off(self):
        from personalclaw.config.loader import SelfQaConfig

        cfg = SelfQaConfig()
        assert cfg.enabled is False
        assert cfg.fix_branch_enabled is False
        assert cfg.watched_repo == ""
        assert cfg.max_scenarios_per_fire == 3

    def test_every_field_carries_label_and_help(self):
        """Point (a): a field with no `_meta` is unreachable from Settings."""
        from dataclasses import fields

        from personalclaw.config.loader import SelfQaConfig

        for f in fields(SelfQaConfig):
            assert f.metadata.get("label"), f.name
            assert f.metadata.get("help"), f.name

    def test_load_maps_every_field(self, tmp_path, monkeypatch):
        """Point (b): the explicit mapping. An omitted field loads as its default, silently."""
        from personalclaw.config import loader as loader_mod

        home = tmp_path / "home"
        home.mkdir()
        (home / "config.json").write_text(
            json.dumps(
                {
                    "agent": {
                        "self_qa": {
                            "enabled": True,
                            "watched_repo": "/tmp/watched",
                            "fix_branch_enabled": True,
                            "max_scenarios_per_fire": 7,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(loader_mod, "config_dir", lambda: home)

        cfg = loader_mod.AppConfig.load().agent.self_qa
        assert cfg.enabled is True
        assert cfg.watched_repo == "/tmp/watched"
        assert cfg.fix_branch_enabled is True
        assert cfg.max_scenarios_per_fire == 7

    def test_load_clamps_the_scenario_ceiling(self, tmp_path, monkeypatch):
        """The file cannot express a ceiling the dashboard would refuse."""
        from personalclaw.config import loader as loader_mod

        home = tmp_path / "home"
        home.mkdir()
        (home / "config.json").write_text(
            json.dumps({"agent": {"self_qa": {"max_scenarios_per_fire": 9999}}}), encoding="utf-8"
        )
        monkeypatch.setattr(loader_mod, "config_dir", lambda: home)
        assert loader_mod.AppConfig.load().agent.self_qa.max_scenarios_per_fire == 20

    def test_to_dict_round_trips(self, tmp_path, monkeypatch):
        """Point (c): what `to_dict()` emits must load back to the same values."""
        from personalclaw.config import loader as loader_mod

        home = tmp_path / "home"
        home.mkdir()
        (home / "config.json").write_text(
            json.dumps({"agent": {"self_qa": {"enabled": True, "watched_repo": "/x"}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(loader_mod, "config_dir", lambda: home)

        emitted = loader_mod.AppConfig.load().to_dict()["agent"]["self_qa"]
        assert emitted["enabled"] is True
        assert emitted["watched_repo"] == "/x"
        assert set(emitted) == {
            "enabled",
            "watched_repo",
            "fix_branch_enabled",
            "max_scenarios_per_fire",
        }

    def test_every_field_has_a_write_path(self):
        """Point (d): a field absent from `_EDITABLE_CONFIG` cannot be changed from the UI."""
        from dataclasses import fields

        from personalclaw.config.loader import SelfQaConfig
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        for f in fields(SelfQaConfig):
            assert f"agent.self_qa.{f.name}" in _EDITABLE_CONFIG, f.name


# ── registration (the two providers the template names must be dispatchable) ──


class TestWatchTriggerReconcile:
    """`reconcile()` — the installer's call site, and the reason the script is not dead code."""

    @staticmethod
    def _store(tmp_path):
        from personalclaw.triggers.store import TriggerStore

        base = tmp_path / "trigger-home"
        base.mkdir(exist_ok=True)
        return TriggerStore(base_dir=base)

    @staticmethod
    def _configure(tmp_path, monkeypatch, **fields):
        from personalclaw.config import loader as loader_mod

        home = tmp_path / "cfg-home"
        home.mkdir(exist_ok=True)
        (home / "config.json").write_text(
            json.dumps({"agent": {"self_qa": fields}}), encoding="utf-8"
        )
        monkeypatch.setattr(loader_mod, "config_dir", lambda: home)

    def test_off_registers_nothing(self, tmp_path, monkeypatch):
        """A user who never asked for the companion must not find a disabled cron in their list."""
        from personalclaw.selfqa.install import WATCH_TRIGGER_ID, reconcile

        self._configure(tmp_path, monkeypatch, enabled=False)
        store = self._store(tmp_path)
        reconcile(store, crons_dir=tmp_path / "crons")
        assert store.get(WATCH_TRIGGER_ID) is None

    def test_enabled_with_a_repo_arms_the_watcher(self, tmp_path, monkeypatch):
        from personalclaw.selfqa.install import (
            COMMIT_WATCH_SPEC,
            WATCH_TRIGGER_ID,
            reconcile,
        )

        self._configure(tmp_path, monkeypatch, enabled=True, watched_repo="/tmp/watched")
        store = self._store(tmp_path)
        reconcile(store, crons_dir=tmp_path / "crons")

        row = store.get(WATCH_TRIGGER_ID)
        assert row is not None, "enabling the companion registered no watcher"
        assert row.trigger.enabled is True
        assert row.trigger.spec["kind"] == "interval"
        assert row.trigger.workflow["inline"]["config"]["script"] == COMMIT_WATCH_SPEC
        # ARMED, not merely registered. `enabled=True` with no `next_fire_at` is a row the
        # scheduler never picks up — the watcher would sit in the trigger list looking healthy
        # and never fire, which is clause 1 failing in the shape that reads as working.
        from datetime import datetime, timezone

        from personalclaw.selfqa.install import WATCH_INTERVAL_SECS

        assert row.trigger.next_fire_at, "the watcher was registered but never armed"
        due = datetime.fromisoformat(row.trigger.next_fire_at)
        delta = (due - datetime.now(timezone.utc)).total_seconds()
        assert 0 < delta <= WATCH_INTERVAL_SECS + 5, f"first fire is {delta}s out"
        # Decision 7: a script that starts a workflow run is write-capable, so the fence needs the
        # frozen grant. Without it the fire is screened off and the watcher is inert a second way.
        assert "run-script" in row.trigger.capabilities.get("providers", [])
        # The script and the path it reads both landed.
        config = json.loads((tmp_path / "crons" / "selfqa_commit_watch.config.json").read_text())
        assert config["repo"] == "/tmp/watched"

    def test_enabled_without_a_repo_does_not_arm(self, tmp_path, monkeypatch):
        """A watcher with nowhere to look would tick forever doing nothing."""
        from personalclaw.selfqa.install import WATCH_TRIGGER_ID, reconcile

        self._configure(tmp_path, monkeypatch, enabled=True, watched_repo="")
        store = self._store(tmp_path)
        reconcile(store, crons_dir=tmp_path / "crons")
        assert store.get(WATCH_TRIGGER_ID) is None

    def test_disabling_switches_the_row_off_rather_than_deleting_it(self, tmp_path, monkeypatch):
        """Deleting the last cron entry has been observed to stop the scheduler outright."""
        from personalclaw.selfqa.install import WATCH_TRIGGER_ID, reconcile

        store = self._store(tmp_path)
        self._configure(tmp_path, monkeypatch, enabled=True, watched_repo="/tmp/watched")
        reconcile(store, crons_dir=tmp_path / "crons")
        assert store.get(WATCH_TRIGGER_ID).trigger.enabled is True

        self._configure(tmp_path, monkeypatch, enabled=False, watched_repo="/tmp/watched")
        reconcile(store, crons_dir=tmp_path / "crons")
        row = store.get(WATCH_TRIGGER_ID)
        assert row is not None, "the row was deleted instead of disabled"
        assert row.trigger.enabled is False

    def test_repointing_the_repo_converges_one_row(self, tmp_path, monkeypatch):
        """The vacuity floor for convergence: a non-deterministic id would add a second watcher."""
        from personalclaw.selfqa.install import WATCH_TRIGGER_ID, reconcile

        store = self._store(tmp_path)
        self._configure(tmp_path, monkeypatch, enabled=True, watched_repo="/tmp/one")
        reconcile(store, crons_dir=tmp_path / "crons")
        self._configure(tmp_path, monkeypatch, enabled=True, watched_repo="/tmp/two")
        reconcile(store, crons_dir=tmp_path / "crons")

        rows = [t for t in store.list_triggers() if t.id == WATCH_TRIGGER_ID]
        all_ids = [t.id for t in store.list_triggers()]
        assert len(rows) == 1, f"reconcile added a second watcher: {all_ids}"
        config = json.loads((tmp_path / "crons" / "selfqa_commit_watch.config.json").read_text())
        assert config["repo"] == "/tmp/two", "the watcher still points at the old repo"

    def test_the_trigger_session_key_is_unattended(self):
        """A companion run must count as unattended, and that turns on a COLON.

        `is_unattended_session` matches `cron:`-style prefixes only, so a key written `cron_x`
        silently loses the status — and with it the headless profile and the budgets. Asserted
        against the real predicate rather than by reading the format string.
        """
        from personalclaw.guardrails.policy import is_unattended_session
        from personalclaw.selfqa.install import WATCH_TRIGGER_ID

        assert is_unattended_session(f"cron:{WATCH_TRIGGER_ID}") is True
        # The floor: the predicate must be capable of saying no, or the assertion above is vacuous.
        assert is_unattended_session(f"cron_{WATCH_TRIGGER_ID}") is False


class TestProvidersAreDispatchable:
    def test_both_providers_register(self):
        from personalclaw.action_providers.registry import (
            _ensure_default_providers_registered,
            get_action_provider,
        )

        _ensure_default_providers_registered()
        assert get_action_provider("selfqa-triage") is not None
        assert get_action_provider("selfqa-file-finding") is not None

    def test_both_are_allowed_on_a_hook(self):
        """A registered provider missing from this set is one the scheduler would refuse."""
        from personalclaw.validation import ALLOWED_HOOK_PROVIDERS

        assert "selfqa-triage" in ALLOWED_HOOK_PROVIDERS
        assert "selfqa-file-finding" in ALLOWED_HOOK_PROVIDERS

    def test_both_carry_an_autonomy_declaration(self):
        """A provider in the dispatch registry with no declaration is an ungoverned action."""
        from personalclaw.guardrails.autonomy import action_type_for_provider
        from personalclaw.guardrails.rungs import ensure_core_action_types

        ensure_core_action_types()
        assert action_type_for_provider("selfqa-triage").key == "action.selfqa_triage"
        assert action_type_for_provider("selfqa-file-finding").key == "action.create_task"
