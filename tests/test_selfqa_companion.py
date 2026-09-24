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
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import personalclaw.inbox_providers.native_source as ns
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.selfqa_finding_provider import SelfQaFindingActionProvider
from personalclaw.action_providers.selfqa_triage_provider import SelfQaTriageActionProvider
from personalclaw.action_providers.selfqa_watch_provider import SelfQaCommitWatchActionProvider
from personalclaw.inbox import InboxState, InboxStore
from personalclaw.ledger import DECISION, STEP_SKIPPED
from personalclaw.selfqa import findings as findings_mod
from personalclaw.selfqa import watch as watch_mod
from personalclaw.selfqa.findings import ScenarioFinding, file_finding
from personalclaw.selfqa.install import WATCH_TRIGGER_ID, reconcile, remove_retired_script
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

#: The ENGINE's instance key for the `self-qa` template's triage node — `root` is a sequence and
#: `triage` is its first child, so `models.walk` names the instance `root.children[0]`. Written out
#: rather than derived, because the whole point of the SC#6 surfacing half is that the ledger row
#: must carry the engine's key and not the node id: `service.inspect_node` slices a run's ledger on
#: `instance_path`, so a row stamped `triage` is durably written and invisible in the runs surface.
TRIAGE_PATH = "root.children[0]"


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


@pytest.fixture(autouse=True)
def watch_state(tmp_path, monkeypatch):
    """Isolate the watcher's state file per test.

    `watch.state_path()` derives from `config_dir()`, which conftest redirects once per
    SESSION — shared state across tests in this file would make "first sight" true exactly
    once and every later test's quiet/fire verdicts depend on ordering.
    """
    path = tmp_path / "selfqa-state" / "commit_watch.state.json"
    monkeypatch.setattr(watch_mod, "state_path", lambda: path)
    return path


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


# ── Clause 1: a real commit fires the companion (now via the vcs trigger, SV-11) ─────


class TestClauseOneWatcherFires:
    """`a real commit to the watched repo fires the companion` — SV-11's seam.

    The decision under test is unchanged from the interim script (first-sight quiet,
    fire-on-commit, state-advances-first); what changed is WHERE it runs: in-process
    (`selfqa.watch.check`), invoked by the `selfqa-commit-watch` provider when the vcs
    trigger fires, instead of a sandboxed cron script on an interval.
    """

    def test_first_sight_records_head_and_stays_quiet(self, repo):
        """Enabling the companion must not fire a run against whatever was checked out."""
        fire = watch_mod.check(str(repo))
        assert fire.inputs is None
        assert "first sight" in fire.quiet_reason
        state = watch_mod.read_state()
        assert state["last_sha"] == _git(repo, "rev-parse", "HEAD")
        assert state["repo"] == str(repo)

    def test_a_real_commit_fires_on_the_next_check(self, repo):
        """The clause itself: one commit, then ONE fire, and the SHA is in the inputs."""
        watch_mod.check(str(repo))
        sha = commit(repo, "src/personalclaw/thing.py", "x = 1\n", "feat: a thing")
        fire = watch_mod.check(str(repo))
        assert fire.inputs is not None
        assert fire.inputs["commits"] == [sha]
        assert fire.inputs["repo"] == str(repo)
        # Keyed on the frontier, so two fires seeing the same HEAD open ONE run.
        assert fire.idempotency_key == f"selfqa-{sha}"
        # The state advanced, so the same commit does not re-fire and burn a run per change.
        assert watch_mod.read_state()["last_sha"] == sha

    def test_no_new_commit_is_quiet_with_its_reason(self, repo):
        """The vacuity floor: a check that fired unconditionally would pass the test above."""
        watch_mod.check(str(repo))
        commit(repo, "src/personalclaw/thing.py", "x = 1\n", "feat: a thing")
        assert watch_mod.check(str(repo)).inputs is not None
        third = watch_mod.check(str(repo))
        assert third.inputs is None
        assert third.quiet_reason == "no new commits"

    def test_several_commits_all_arrive_oldest_first(self, repo):
        """A push is a range, not a commit. Missing the middle of one hides a regression."""
        watch_mod.check(str(repo))
        first = commit(repo, "src/a.py", "a\n", "feat: a")
        second = commit(repo, "src/b.py", "b\n", "feat: b")
        fire = watch_mod.check(str(repo))
        assert fire.inputs is not None and fire.inputs["commits"] == [first, second]

    def test_an_unreadable_repo_is_quiet_rather_than_a_nag(self, tmp_path):
        """A watcher pointed at a moved directory must not error on every ref change forever."""
        fire = watch_mod.check(str(tmp_path / "not-a-repo"))
        assert fire.inputs is None
        assert "not a readable git repo" in fire.quiet_reason

    def test_the_provider_delegates_the_start_to_run_workflow(self, repo, monkeypatch):
        """The loop's closing edge: the fire STARTS the run, through the one seam that owns
        dedupe and supervisor registration — never a second start implementation."""
        from personalclaw.action_providers import registry as reg
        from personalclaw.action_providers.base import ActionResult

        watch_mod.check(str(repo))  # first sight
        sha = commit(repo, "src/personalclaw/thing.py", "x = 1\n", "feat: a thing")

        runner = MagicMock()

        async def fake_execute(action_config, ctx, timeout=30):
            runner(action_config)
            return ActionResult(success=True, stdout="run started")

        runner_provider = MagicMock()
        runner_provider.execute = fake_execute
        monkeypatch.setattr(
            reg,
            "get_action_provider",
            lambda name: runner_provider if name == "run-workflow" else None,
        )

        provider = SelfQaCommitWatchActionProvider()
        result = _run(provider.execute({"repo": str(repo)}, MagicMock(), timeout=30))
        assert result.success
        assert runner.call_count == 1
        cfg = runner.call_args[0][0]
        assert cfg["workflow"] == "self-qa"
        assert cfg["inputs"]["commits"] == [sha]
        assert cfg["idempotency_key"] == f"selfqa-{sha}"

    def test_a_quiet_fire_is_a_success_carrying_its_reason(self, repo, monkeypatch):
        """First sight through the provider: no run started, and the skip SAYS why — a
        silent skip and a dead watcher must never look alike."""
        from personalclaw.action_providers import registry as reg

        runner_provider = MagicMock()
        monkeypatch.setattr(reg, "get_action_provider", lambda name: runner_provider)

        provider = SelfQaCommitWatchActionProvider()
        result = _run(provider.execute({"repo": str(repo)}, MagicMock(), timeout=30))
        assert result.success
        assert "first sight" in (result.stdout or "")
        runner_provider.execute.assert_not_called()

    def test_reconcile_swaps_an_interim_clock_row_in_place(self, repo, monkeypatch, tmp_path):
        """An upgraded Wave-2 home carries a `clock` row under the same id: the swap edits
        THAT row rather than minting a second watcher beside it."""
        from personalclaw.config import loader as loader_mod
        from personalclaw.triggers.models import Trigger

        cfg = SimpleNamespace(
            agent=SimpleNamespace(self_qa=SimpleNamespace(enabled=True, watched_repo=str(repo)))
        )
        monkeypatch.setattr(loader_mod.AppConfig, "load", staticmethod(lambda: cfg))

        old = Trigger(
            id=WATCH_TRIGGER_ID, name="Self-QA commit watch", kind="clock", created_by="system"
        )
        loaded = SimpleNamespace(trigger=old)
        store = MagicMock()
        store.get.return_value = loaded
        reconcile(store, crons_dir=tmp_path / "crons")

        trigger = store.upsert.call_args[0][0]
        assert trigger is old
        assert trigger.kind == "file"
        assert trigger.workflow["inline"]["provider"] == "selfqa-commit-watch"

    def test_reconcile_removes_the_retired_script_artifacts(self, monkeypatch, tmp_path):
        """A Wave-2 home's installed script, config, and state are cleaned up — a dead
        script left in the fenced crons dir invites a user to schedule it."""
        from personalclaw.config import loader as loader_mod

        crons = tmp_path / "crons"
        crons.mkdir()
        for name in (
            "selfqa_commit_watch.py",
            "selfqa_commit_watch.config.json",
            "selfqa_commit_watch.state.json",
        ):
            (crons / name).write_text("retired\n", encoding="utf-8")

        cfg = SimpleNamespace(
            agent=SimpleNamespace(self_qa=SimpleNamespace(enabled=False, watched_repo=""))
        )
        monkeypatch.setattr(loader_mod.AppConfig, "load", staticmethod(lambda: cfg))
        store = MagicMock()
        store.get.return_value = None
        reconcile(store, crons_dir=crons)

        assert not any(crons.iterdir()), "retired artifacts survived the reconcile"
        # And it is idempotent on the second pass.
        assert remove_retired_script(crons) == []


class TestTheInterimScriptStaysRetired:
    """SV-11's rule spec: the interim seam must not come back.

    The done_when asks for a spec that asserts the script's ABSENCE once the vcs trigger
    kind exists — a grep-shaped rail, so a future change that re-materializes a watcher
    script (or resurrects the installer) fails here with the reason attached.
    """

    SRC = Path(__file__).resolve().parents[1] / "src"

    def test_the_vcs_trigger_kind_exists(self):
        """The precondition the rule is scoped to, asserted rather than assumed."""
        from personalclaw.triggers.file_watch import vcs_patterns
        from personalclaw.triggers.models import KINDS

        assert "file" in KINDS
        assert any("refs/heads" in p for p in vcs_patterns("."))

    def test_no_commit_watch_script_ships(self):
        hits = [p for p in self.SRC.rglob("selfqa_commit_watch*") if p.is_file()]
        assert hits == [], f"the interim commit-watch script came back: {hits}"

    def test_install_no_longer_materializes_scripts(self):
        import personalclaw.selfqa.install as install_mod

        for retired in (
            "install_commit_watch_script",
            "write_watch_config",
            "packaged_script_source",
        ):
            assert not hasattr(install_mod, retired), f"{retired} resurfaced in selfqa.install"


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
        record_triage(Journal(run_id=run_id), verdict, instance_path=TRIAGE_PATH)

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
        record_triage(Journal(run_id=run_id), verdict, instance_path=TRIAGE_PATH)
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
            record_triage(
                Journal(run_id="selfqa-empty"),
                CommitTriage("abc", IMPACT_TEST, "  "),
                instance_path=TRIAGE_PATH,
            )

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
                ActionContext(
                    event="workflow_node", payload={"run_id": run_id, "instance_path": TRIAGE_PATH}
                ),
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
                ActionContext(
                    event="workflow_node",
                    payload={"run_id": "selfqa-c2-fwd", "instance_path": TRIAGE_PATH},
                ),
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
                ActionContext(
                    event="workflow_node",
                    payload={"run_id": "selfqa-c2-cap", "instance_path": TRIAGE_PATH},
                ),
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


# ── SC#6's other half: the skip is VISIBLE IN THE RUNS SURFACE ───────────────


class TestTheSkipIsVisibleInTheRunsSurface:
    """Success Criterion #6 reads: a test-only commit produces "a ledger-only skip record with a
    one-line rationale **(visible in the runs surface, no full run spent)**".

    SV-9 shipped the write and recorded that this parenthetical was unmet. Two independent breaks
    stood between a written row and a legible one, and each looked exactly like working code:

    1. **The row was stamped with the node id, not the engine's instance key.** `record_triage`
       wrote `instance_path="triage"`, while the engine names that instance `root.children[0]`
       (`models.walk`). `service.inspect_node` builds a node's ledger slice by filtering the run's
       ledger on `instance_path == <target>`, so every triage row fell outside its own node's
       slice — written, readable through the ledger reader, and unreachable from the runs surface.
       Fixed by threading the instance path into the action payload (the engine is the only layer
       that knows it) and refusing an absent one, the way an absent rationale is already refused.
    2. **The surface rendered a row's `kind` and nothing else** — pinned in
       `web/src/pages/workflows/NodeInspectorDrawer.test.tsx`, on rendered DOM.

    The interesting property is SEVERAL rows under ONE node id: the companion writes one per
    commit, so a surface (or a slice) that kept only the latest would lose skips while still
    looking populated. Every test here uses THREE commits for that reason — one row cannot see it.
    """

    def test_the_template_node_really_lives_at_the_asserted_instance_path(self):
        """`TRIAGE_PATH` is a claim about the ENGINE's naming, so derive it from the engine.

        If the bundled template ever moves the triage node, this fails here rather than silently
        stranding every row it writes outside its own node's slice again.
        """
        from personalclaw.workflows.models import Node, walk

        spec = json.loads((BUNDLED / "workflow.json").read_text(encoding="utf-8"))
        paths = {path: node.id for path, node in walk(Node.from_dict(spec["root"]))}
        assert paths.get(TRIAGE_PATH) == "triage", paths

    def test_the_engine_puts_the_instance_path_in_an_action_payload(self):
        """The CALL SITE. A provider cannot reconstruct its own instance key, so the engine must
        hand it over — and `node_id` cannot stand in, because a `foreach` body shares one id
        across every item.
        """
        from personalclaw.action_providers.base import ActionResult
        from personalclaw.workflows.engine import dispatch_action
        from personalclaw.workflows.models import Node

        seen: dict = {}

        class _Capture:
            name = "capture"
            display_name = "capture"

            async def execute(self, action_config, ctx, timeout=30):
                seen.update(ctx.payload)
                return ActionResult(success=True, stdout="{}")

        node = Node.from_dict({"kind": "action", "id": "triage", "config": {"provider": "capture"}})
        _run(
            dispatch_action(
                node,
                MagicMock(),
                get_provider=lambda _n: _Capture(),
                run_id="run-x",
                instance_path=TRIAGE_PATH,
            )
        )
        assert seen.get("instance_path") == TRIAGE_PATH, seen
        # The floor: absent an engine-supplied path the key is simply not there — the provider is
        # never handed a fabricated one to write, which is how the invisible rows happened.
        seen.clear()
        _run(dispatch_action(node, MagicMock(), get_provider=lambda _n: _Capture(), run_id="run-x"))
        assert "instance_path" not in seen, seen

    def test_the_controller_hands_the_running_instances_path_to_the_provider(
        self, tmp_path, monkeypatch
    ):
        """The CALL SITE one layer up. The test above pins `dispatch_action`; this pins the only
        caller that supplies the argument, driving a REAL run through `RunController`.

        Without it, `instance_path=item.path` could be dropped from the controller and every
        assertion in this class would still be green — the engine would keep honouring a value
        nothing passes it, which is how a wired-but-unfed control looks from inside its own test.
        """
        from personalclaw.workflows import store
        from personalclaw.workflows.controller import EngineServices, RunController
        from personalclaw.workflows.models import RunStatus, WorkflowRun

        home = tmp_path / "wf-home"
        home.mkdir()
        monkeypatch.setattr(store, "config_dir", lambda: home)

        seen: list[dict] = []

        class _Capture:
            async def execute(self, cfg, ctx, timeout=30):
                seen.append(dict(ctx.payload))
                return MagicMock(
                    success=True,
                    stdout="{}",
                    outcome="",
                    error="",
                    exit_code=0,
                    stderr="",
                    agent_error=None,
                )

        spec = {
            "name": "selfqa-wiring",
            "root": {
                "kind": "sequence",
                "id": "root",
                "children": [
                    {"kind": "action", "id": "triage", "config": {"provider": "selfqa-triage"}}
                ],
            },
        }
        run = store.create(WorkflowRun(id="", workflow_name="selfqa-wiring"))
        store.write_spec(run.id, spec)
        controller = RunController(
            run, spec, services=EngineServices(get_provider=lambda _name: _Capture())
        )
        assert _run(controller.run_to_completion(timeout=20)) == RunStatus.COMPLETE

        assert seen, "the action provider never fired"
        # The ENGINE's key for the instance that actually ran — the same string the run's node
        # state is filed under, which is what makes the row findable in the runs surface.
        assert seen[0].get("instance_path") == TRIAGE_PATH, seen[0]
        assert TRIAGE_PATH in store.read_state(run.id), "the path is not the run's own instance key"

    def test_three_skips_all_reach_the_runs_surface_with_their_reasons(
        self, repo, tmp_path, monkeypatch
    ):
        """THE assertion: `inspect_node` — what the runs surface fetches — carries every skip row,
        each with its `sha`, `impact` and `rationale`.

        The run is persisted with the engine's OWN writers (`store.write_state`/`write_output`, the
        real `Journal`), so this cannot drift from the shape the controller actually leaves behind.
        """
        from personalclaw.workflows import service, store
        from personalclaw.workflows.models import InstanceState, NodeInstance, WorkflowRun

        home = tmp_path / "wf-home"
        home.mkdir()
        monkeypatch.setattr(store, "config_dir", lambda: home)

        shas = [
            commit(repo, f"tests/test_{i}.py", f"def test_{i}():\n    pass\n", f"test: {i}")
            for i in range(3)
        ]
        run = store.create(WorkflowRun(id="", workflow_name="self-qa"))
        store.write_spec(
            run.id, json.loads((BUNDLED / "workflow.json").read_text(encoding="utf-8"))
        )
        ref = store.write_output(run.id, TRIAGE_PATH, {"recorded": 3})
        store.write_state(
            run.id,
            {TRIAGE_PATH: NodeInstance(path=TRIAGE_PATH, state=InstanceState.DONE, output_ref=ref)},
        )

        result = _run(
            SelfQaTriageActionProvider().execute(
                {"repo": str(repo), "commits": shas},
                ActionContext(
                    event="workflow_node",
                    payload={"run_id": run.id, "instance_path": TRIAGE_PATH},
                ),
            )
        )
        assert result.success, result.error
        assert json.loads(result.stdout)["recorded"] == 3

        body = service.inspect_node(run.id, "triage")
        assert body["ok"], body
        skips = [e for e in body["ledger_events"] if e["kind"] == STEP_SKIPPED]
        # All THREE, not the last one — the several-rows-under-one-node-id property.
        assert len(skips) == 3, body["ledger_events"]
        assert [e["sha"] for e in skips] == shas
        assert all(e["impact"] == IMPACT_TEST for e in skips)
        for e in skips:
            assert e["rationale"].strip(), "a skip row reached the surface with no reason"
            assert "\n" not in e["rationale"], "the rationale must stay ONE line on the surface"

    def test_a_row_stamped_with_the_node_id_is_invisible_to_the_surface(
        self, tmp_path, monkeypatch
    ):
        """THE VACUITY FLOOR, and the exact pre-fix state reproduced.

        The test above passes for two different reasons — because the slice is correctly scoped, or
        because it returns everything in the run regardless of path. This distinguishes them: a row
        stamped with the bare node id (what `record_triage` used to write) is DROPPED by the very
        same read, while an identical row stamped with the engine's key comes through. Without this,
        the fix could be reverted and the test above would still be green.
        """
        from personalclaw.workflows import service, store
        from personalclaw.workflows.models import InstanceState, NodeInstance, WorkflowRun

        home = tmp_path / "wf-home"
        home.mkdir()
        monkeypatch.setattr(store, "config_dir", lambda: home)

        run = store.create(WorkflowRun(id="", workflow_name="self-qa"))
        store.write_spec(
            run.id, json.loads((BUNDLED / "workflow.json").read_text(encoding="utf-8"))
        )
        ref = store.write_output(run.id, TRIAGE_PATH, {"recorded": 2})
        store.write_state(
            run.id,
            {TRIAGE_PATH: NodeInstance(path=TRIAGE_PATH, state=InstanceState.DONE, output_ref=ref)},
        )

        journal = Journal(run_id=run.id)
        record_triage(
            journal,
            CommitTriage("a" * 40, IMPACT_TEST, "reachable — stamped with the engine's key"),
            instance_path=TRIAGE_PATH,
        )
        # The old shape, written directly past `record_triage`'s guard so the guard itself is not
        # what this measures: the READ is.
        journal.write(
            STEP_SKIPPED,
            node_id="triage",
            instance_path="triage",
            epoch=0,
            sha="b" * 40,
            impact=IMPACT_TEST,
            rationale="unreachable — stamped with the node id",
        )

        # Both rows are genuinely on disk, so the absence below is about the SLICE, not the write.
        assert len(ledger(run.id, kinds={STEP_SKIPPED})) == 2

        body = service.inspect_node(run.id, "triage")
        surfaced = [e["rationale"] for e in body["ledger_events"] if e["kind"] == STEP_SKIPPED]
        assert surfaced == ["reachable — stamped with the engine's key"], surfaced

    def test_a_pathless_verdict_is_refused(self):
        """Symmetric with the rationale refusal. A row no surface can find answers "why did
        nothing run?" to nobody, so it is refused rather than written into the dark.
        """
        with pytest.raises(ValueError, match="no instance_path"):
            record_triage(
                Journal(run_id="selfqa-pathless"),
                CommitTriage("c" * 40, IMPACT_TEST, "a perfectly good reason"),
                instance_path="  ",
            )
        assert ledger("selfqa-pathless", kinds={STEP_SKIPPED}) == []


# ── Clause 3: the scenario drives the real UI via Chrome DevTools MCP ────────


class TestClauseThreeScenarioDrivesTheUI:
    """`a user-impacting commit generates a scenario that mutates state through the real UI via
    Chrome DevTools MCP`.

    **What is asserted here is the template contract, not a UI drive.** Executing this clause
    end-to-end needs a reachable Chrome DevTools MCP server and a running gateway built from the
    commit under test. Both were obtained on 2026-08-24 and the clause STILL does not close — the
    two things in the way are recorded here so the next session does not re-derive them:

    1. **FIXED.** `route` could select neither of its own cases, because the engine keyed a
       boolean selector as `str(True)` → `"True"` and a JSON template can only spell the case
       `true`. So the entire scenario subtree was unreachable from a real run while the
       declarative assertion below passed. `test_the_ENGINE_selects_that_case_...` is the rail
       that would have caught it, and now does.
    2. **NOT this atom's, and NOT fixed here.** A `stage` node's spawn completion has no
       consumer: `dispatch_stage` returns RUNNING carrying `{"subagent_id": ...}` and nothing in
       the repo reads that key, while `_apply` pops the instance out of `_inflight` — so neither
       the stall clock nor the total timeout can see it either. Measured: `scenario-gen`'s
       subagent reported `done: True, error: ""` and the node was still RUNNING with no
       `step_completed` fifteen minutes later. That is the engine's stage seam, not the
       companion's, and it blocks every template with a stage node.

    What these tests do enforce is every property of the shipped template that the clause depends
    on — the routing (now through the real dispatcher), the MCP binding, the state-mutation
    requirement, and the engine-enforced proof gate.

    The MCP half of the clause IS satisfied and was measured separately: with `chrome-devtools`
    in `$PERSONALCLAW_HOME/mcp.json` and the `mcp-tools` app installed, the running gateway's
    `/api/tools` lists all 29 `mcp/chrome-devtools/*` tools, so a stage subagent can drive the
    browser. Nothing declares or checks that dependency, which is its own gap — the template
    declares `git`/`ffmpeg` under `metadata.requirements.binaries` and preflight has no
    equivalent for an MCP server.
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

    @pytest.mark.parametrize(
        "has_impactful,case", [(True, "true"), (False, "false")], ids=["impactful", "skip-only"]
    )
    def test_the_ENGINE_selects_that_case_not_just_the_JSON_declaring_it(
        self, has_impactful: bool, case: str
    ):
        """The case above being PRESENT in the spec is not the same as the engine taking it.

        Measured: it did not. `triage` returns a real Python `bool`, the engine keyed the selector
        on `str(value)` → `"True"`, and a template written in JSON can only spell its cases
        `true`/`false` — so `route` failed "matched no case" for BOTH verdicts and the whole
        scenario subtree was unreachable from a real run. The declarative assertion above passed
        the entire time. So this drives the SHIPPED `route` node through the SHIPPED dispatcher
        with the SHIPPED provider's output shape.
        """
        from personalclaw.workflows.bindings import BindingContext
        from personalclaw.workflows.engine import dispatch_branch
        from personalclaw.workflows.models import InstanceState, Node

        route = Node.from_dict(self._nodes(self._spec())["route"])
        verdict = CommitTriage(sha="a" * 40, impact=IMPACT_USER, rationale="shipped code changed")
        output = {
            "has_impactful": has_impactful,
            "impactful": [verdict.to_dict()] if has_impactful else [],
            "skipped": [] if has_impactful else [verdict.to_dict()],
        }
        result = asyncio.run(
            dispatch_branch(route, BindingContext(node_outputs={"triage": output}))
        )
        assert result.state == InstanceState.DONE, (
            f"the shipped route branch could not select its own {case!r} case: "
            f"{result.failure and result.failure.cause_plain}"
        )
        assert result.output == {"case": case}

    def test_a_stringly_typed_has_impactful_is_NOT_quietly_accepted(self):
        """Vacuity floor for the test above: it must be the BOOLEAN that routes.

        If the rail passed for the string `"True"` too it would no longer be measuring the
        normalisation that made the branch reachable — it would be measuring a
        case-insensitive match that also merges two distinct cases.
        """
        from personalclaw.workflows.bindings import BindingContext
        from personalclaw.workflows.engine import dispatch_branch
        from personalclaw.workflows.models import InstanceState, Node

        route = Node.from_dict(self._nodes(self._spec())["route"])
        result = asyncio.run(
            dispatch_branch(
                route, BindingContext(node_outputs={"triage": {"has_impactful": "True"}})
            )
        )
        assert result.state == InstanceState.FAILED

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
        """The proof cannot be self-reported. The evidence node is a deterministic ACTION
        bound to the `selfqa-evidence` provider — code that hashes the real bytes and
        refuses completion through `check_required_kinds` (the KIND-level successor of the
        file-glob `required_artifacts` gate) — never an LLM stage grading its own bundle.
        The refusal behaviour itself is pinned in `test_selfqa_evidence.py`; this pins the
        TEMPLATE wiring and what the default gate requires."""
        from personalclaw.selfqa import evidence as ev

        node = self._nodes(self._spec())["evidence"]
        assert node["kind"] == "action", "the proof seam regressed to a self-reporting stage"
        assert node["config"]["provider"] == "selfqa-evidence"
        assert ev.DEFAULT_REQUIRED_KINDS == (
            ev.KIND_SCREENSHOT,
            ev.KIND_RECORDING,
            ev.KIND_MANIFEST,
        )

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


class TestTheSelfQaPanelRowsPatchTheNestedPath:
    """The quiet-revert failure the UI drive exists to catch, railed at the FE call site.

    `agent.self_qa.*` is a NESTED config section, so every row in the panel's Self-QA section
    has to be bound to `patchSelfQa` — the closure that prefixes the sub-path — and not to the
    flat `patch` sitting six lines above it in the same component. A row bound to the flat one
    PATCHes `agent.<field>`, a path the server's allowlist rejects; the toggle flips
    optimistically, the request 400s, and the FE rolls the value back. The control looks like it
    saved and did not, which is exactly the class of defect a render check never sees.

    `test_every_field_has_a_write_path` above cannot catch this: it derives its paths from
    `SelfQaConfig`, so it stays green no matter which closure the panel actually calls. This
    reads the panel source — the call site — and then closes the loop on the backend by
    asserting the flat path really is rejected, because if `agent.<field>` were editable a
    mis-bound row would be harmless and this rail would be measuring nothing.
    """

    PANEL = (
        Path(__file__).resolve().parents[1]
        / "web"
        / "src"
        / "pages"
        / "settings"
        / "AgentDefaultsPanel.tsx"
    )

    @classmethod
    def _section(cls) -> str:
        """The Self-QA `<Section>` body, bounded by real markup — not a character window.

        A fixed window around the title would drift with the hint copy and could swallow a
        sibling section's flat-`patch` rows, which would make this rail red for the wrong
        reason.
        """
        src = cls.PANEL.read_text(encoding="utf-8")
        start = src.index('<Section title="Self-QA companion"')
        return src[start : src.index("</Section>", start)]

    @classmethod
    def _rows(cls) -> dict[str, str]:
        """Map each `field="..."` row in the section to that element's own attribute text.

        Split on `<` followed by an uppercase letter — a component open tag. Scanning to the
        first `>` instead would truncate at the `<sha>` inside a `hint` string, and `</Section>`
        and `<div` are both excluded by the same rule.
        """
        import re

        rows: dict[str, str] = {}
        for chunk in re.split(r"<(?=[A-Z])", cls._section()):
            m = re.search(r'field="([^"]+)"', chunk)
            if m:
                rows[m.group(1)] = chunk
        return rows

    def test_the_scan_actually_finds_every_row(self):
        """Vacuity floor: a regex that matched nothing would make every assertion below pass.

        Pinned to the exact set rather than a count, so a renamed field cannot be absorbed by a
        newly added one.
        """
        assert set(self._rows()) == {
            "enabled",
            "watched_repo",
            "fix_branch_enabled",
            "max_scenarios_per_fire",
        }

    def test_the_scan_did_not_swallow_a_sibling_section(self):
        """Second vacuity floor: the extracted block must be a section, not the whole file.

        `approval_mode` is a flat `patch={patch}` row in a *different* section. If it appears
        here the boundary slipped, and the rail below would be asserting against rows it was
        never meant to see.
        """
        section = self._section()
        assert "approval_mode" not in section
        assert len(section) < len(self.PANEL.read_text(encoding="utf-8"))

    def test_every_row_is_bound_to_the_nested_patcher(self):
        """The call site: `patch={patchSelfQa}`, never the flat `patch={patch}`."""
        for field, chunk in self._rows().items():
            assert "patch={patchSelfQa}" in chunk, field
            assert "patch={patch}" not in chunk, field

    def test_the_flat_path_each_row_would_have_sent_is_rejected(self):
        """Why a mis-bound row reverts instead of writing the wrong key — the backend half."""
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        for field in self._rows():
            assert f"agent.self_qa.{field}" in _EDITABLE_CONFIG, field
            assert f"agent.{field}" not in _EDITABLE_CONFIG, field


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
        from personalclaw.selfqa.install import WATCH_TRIGGER_ID, reconcile

        self._configure(tmp_path, monkeypatch, enabled=True, watched_repo="/tmp/watched")
        store = self._store(tmp_path)
        reconcile(store, crons_dir=tmp_path / "crons")

        row = store.get(WATCH_TRIGGER_ID)
        assert row is not None, "enabling the companion registered no watcher"
        assert row.trigger.enabled is True
        # SV-11: the vcs preset, not an interval. A `file` trigger is POLLED (file_poll
        # surfaces it by kind), so there is no `next_fire_at` to assert — the arming property
        # is the kind + the preset paths themselves.
        assert row.trigger.kind == "file"
        paths = row.trigger.spec["paths"]
        assert any("refs/heads" in p for p in paths), paths
        assert any(p.endswith(".git/HEAD") for p in paths), paths
        assert all("/tmp/watched" in p for p in paths), paths
        assert row.trigger.spec["dedup"] == "content"
        inline = row.trigger.workflow["inline"]
        assert inline["provider"] == "selfqa-commit-watch"
        assert inline["config"] == {"repo": "/tmp/watched"}
        # Decision 7: an action that starts a workflow run is write-capable, so the fence
        # needs the frozen grant. Without it the fire is screened off and the watcher is
        # inert a second way.
        assert "selfqa-commit-watch" in row.trigger.capabilities.get("providers", [])

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
        row = store.get(WATCH_TRIGGER_ID)
        assert (
            row.trigger.workflow["inline"]["config"]["repo"] == "/tmp/two"
        ), "the watcher still points at the old repo"
        assert all("/tmp/two" in p for p in row.trigger.spec["paths"])

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
