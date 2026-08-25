"""The harvested regression suite — real runs turned into scenario-library cases.

Six things are asserted here, and each one is a hazard rather than a happy path:

* the **journal-only gap** — `run_started` (the sole carrier of a run's redacted inputs) is
  outside `LEDGER_KINDS`, so `read_events` returns `[]` for it, which reads exactly like "the run
  had no inputs". The harvester must read the journal, and a drift rail asserts the split;
* the **CALL SITE** — `personalclaw eval-harvest` is driven end to end through the real
  `cli._eval_harvest`, not just the primitive. A module with no production importer is not done;
* **redaction, with a vacuity floor** — a credential planted in a run's inputs is absent from the
  harvested case, AND the same scan finds it when redaction is disabled. An absence assertion that
  cannot fail proves nothing;
* the **empty-population refusal** — "no runs to harvest" and "harvested a suite of zero" are
  distinct, and the second is a measurement while the first is not;
* **no shadowing** — a harvest can never write over a packaged scenario;
* **≥1 turn per case** — a zero-turn scenario would be run by `personalclaw eval` and pass without
  asserting anything, which is the zero-case-reads-as-a-pass failure at case scale.
"""

from __future__ import annotations

import argparse
import json

import pytest

from personalclaw.evals import harvest as hv
from personalclaw.evals import scenarios as sc
from personalclaw.ledger import reader as ledger_reader
from personalclaw.ledger.kinds import LEDGER_KINDS, RUN_FINISHED, RUN_STARTED
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store as store_mod
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun

#: A credential shape `redact_credentials` actually matches. Checked by
#: `test_the_planted_credential_is_one_redaction_recognizes` so this suite cannot pass by planting
#: a string nothing was ever going to strip.
SECRET = "sk-abcdefghijklmnopqrstuvwxyz012345"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate the run store AND the evals library under a tmp home.

    `workflows.store` and `evals.store` both bind `config_dir` as an imported SYMBOL at module
    import, so patching `personalclaw.config.loader.config_dir` does not reach them. `config_dir()`
    re-reads `$PERSONALCLAW_HOME` on every call, which is why setting the env var is what actually
    isolates an import-bound store — both are set here so neither route can touch the real home.
    """
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


def _run(
    name: str = "daily_digest",
    *,
    status: RunStatus = RunStatus.COMPLETE,
    inputs: dict | None = None,
    journal_inputs: dict | None = None,
    started: bool = True,
    finished: bool = True,
    steps: tuple[str, ...] = ("fetch",),
    consulted: tuple[str, ...] = (),
) -> WorkflowRun:
    """Create a run and journal it the way the controller does.

    `inputs` goes on the run ROW (as the API writes it); `journal_inputs` goes through
    `Journal.run_started` (as the controller writes it). They are separable on purpose: the row is
    unredacted and the harvester must never read it, which is what
    `test_inputs_come_from_the_ledger_not_the_run_row` pins.
    """
    run = store_mod.create(WorkflowRun(id="", workflow_name=name, inputs=dict(inputs or {})))
    run.status = status
    run = store_mod.save(run)
    j = journal_mod.Journal(run.id)
    if started:
        j.run_started(name, inputs=dict(journal_inputs or inputs or {}), spec_version=3)
    for node in steps:
        j.step_completed(
            f"root.{node}", node, epoch=1, cache_key=f"ck-{node}", state=InstanceState.DONE
        )
    # Written through the REAL WF2-R13 emitter, so a change to the `consulted` event's shape
    # breaks the `consulted_refs` scope instead of quietly emptying it.
    for index, ref in enumerate(consulted):
        j.consulted(f"root.{steps[0] if steps else 'fetch'}", f"n{index}", ref=ref)
    if finished:
        j.run_finished(status.value, elapsed_secs=1.5, tokens=42)
    return run


# ── the journal-only gap (why `read_journal` exists at all) ───────────────────


def test_run_started_is_invisible_to_the_events_mirror(home):
    """The finding the whole read path rests on.

    `run_started` carries the inputs and is NOT in `LEDGER_KINDS`, so the `events.jsonl` mirror
    never gets it. A harvester built on `read_events` would see an empty list and be unable to tell
    that from a run that genuinely took no inputs.
    """
    run = _run(journal_inputs={"topic": "caches"})

    assert RUN_STARTED not in LEDGER_KINDS
    assert RUN_FINISHED not in LEDGER_KINDS
    assert journal_mod.ledger(run.id, kinds={RUN_STARTED}) == []

    from_journal = journal_mod.journal_records(run.id, kinds={RUN_STARTED})
    assert len(from_journal) == 1
    assert from_journal[0]["inputs"] == {"topic": "caches"}


def test_the_harvesters_kind_split_matches_the_ledgers_own_registry(home):
    """A drift rail: if a kind moves between the journal and the mirror, this fails LOUDLY.

    Without it, promoting `run_started` into `LEDGER_KINDS` (or demoting `step_completed` out of
    it) would leave the harvest reading the wrong file and silently returning nothing.
    """
    assert ledger_reader.journal_only_kinds(set(hv.JOURNAL_KINDS)) == hv.JOURNAL_KINDS
    assert ledger_reader.journal_only_kinds(set(hv.EVENT_KINDS)) == frozenset()
    assert hv.EVENT_KINDS <= LEDGER_KINDS


# ── the primitive ─────────────────────────────────────────────────────────────


def test_a_terminal_run_becomes_a_library_shaped_scenario(home):
    run = _run(journal_inputs={"topic": "caches", "depth": 3})
    scenario, reason = hv.case_from_run(run)

    assert reason == ""
    assert scenario is not None
    # The library's OWN machinery applies with no special-casing.
    assert sc.fixture_home_of(scenario) == sc.DEFAULT_FIXTURE_HOME
    assert sc.sha256_of_scenario_data(scenario)
    assert scenario["name"] == hv.case_name("daily_digest", run.id)
    assert scenario["name"].startswith(hv.HARVEST_PREFIX)


def test_provenance_names_the_run_and_the_events_it_was_built_from(home):
    """A case that cannot name its population is the fabricated-evidence shape."""
    run = _run(journal_inputs={"topic": "caches"}, steps=("fetch", "summarize"))
    scenario, _ = hv.case_from_run(run)
    block = scenario["harvest"]

    assert block["run_id"] == run.id
    assert block["workflow_name"] == "daily_digest"
    assert block["harvest_version"] == hv.HARVEST_VERSION
    assert block["spec_version"] == 3
    assert block["status"] == RunStatus.COMPLETE.value
    prov = block["provenance"]
    # Both journal-only records DO carry an `event_id` — `_append` stamps every record. What they
    # lack is a row in `events.jsonl`, which is a different thing from lacking an id, and is
    # exactly the confusion that makes a mirror-only reader look correct.
    assert prov["run_started_event_id"].startswith(run.id)
    assert prov["run_finished_event_id"].startswith(run.id)
    assert prov["run_started_event_id"] != prov["run_finished_event_id"]
    assert len(prov["event_ids"]) == 2, "one event id per completed step"
    assert all(eid.startswith(run.id) for eid in prov["event_ids"])
    # The observed outcome is recorded as a BASELINE to pair against, not as an authored golden.
    assert block["baseline"]["steps_completed"] == 2
    assert block["baseline"]["status"] == RunStatus.COMPLETE.value


def test_the_case_records_a_baseline_output_hash_that_outlives_the_run_dir(home):
    """`output_ref` is a path into `runs/<id>/`, which retention reaps. The hash survives it."""
    run = store_mod.create(WorkflowRun(id="", workflow_name="wf"))
    run.status = RunStatus.COMPLETE
    run = store_mod.save(run)
    j = journal_mod.Journal(run.id)
    j.run_started("wf", inputs={"q": "x"}, spec_version=1)
    ref, _ = j.store_output("root.fetch", {"answer": "42"})
    j.step_completed(
        "root.fetch",
        "fetch",
        epoch=1,
        cache_key="k",
        state=InstanceState.DONE,
        output_ref=ref,
        resolved_prompt_ref="prompts/abc123",
    )
    j.run_finished("complete")

    scenario, _ = hv.case_from_run(run)
    outputs = scenario["harvest"]["outputs"]
    assert len(outputs) == 1
    assert outputs[0]["output_ref"] == ref
    assert outputs[0]["output_sha256"], "the body's hash must be recorded, not only its path"
    # The only pointer to what the node was ACTUALLY asked. Reconstructing the prompt from the
    # template + inputs would replay today's template, which is the variable an A/B holds still.
    assert outputs[0]["resolved_prompt_ref"] == "prompts/abc123"
    # The BODY is deliberately not inlined — the writer's spill boundary must not be undone here.
    assert "42" not in json.dumps(outputs)


def test_every_case_carries_at_least_one_turn_naming_its_workflow(home):
    """A zero-turn scenario would be run by `personalclaw eval` and pass, asserting nothing.

    Also holds for a run that took NO inputs, which is the case a naive `if inputs:` guard would
    emit as an empty scenario.
    """
    for journal_inputs in ({"topic": "caches"}, {}):
        run = _run(journal_inputs=journal_inputs)
        scenario, _ = hv.case_from_run(run)
        turns = scenario["sessions"][0]["turns"]
        assert len(turns) == 1
        assert "daily_digest" in turns[0]["user"]
        assert turns[0]["assertions"], "a turn with no assertions is a turn that cannot fail"


def test_the_turn_render_is_order_independent(home):
    """Identity is the content hash; a render varying with dict order would rewrite forever."""
    a = hv._render_turn("wf", {"b": 2, "a": 1})
    b = hv._render_turn("wf", {"a": 1, "b": 2})
    assert a == b


def test_harvesting_the_same_run_twice_yields_the_same_hash(home):
    """The precondition for the idempotent backfill: no timestamp of the HARVEST in the content."""
    run = _run(journal_inputs={"topic": "caches"})
    first, _ = hv.case_from_run(run)
    second, _ = hv.case_from_run(run)
    assert sc.sha256_of_scenario_data(first) == sc.sha256_of_scenario_data(second)


# ── inputs come from the ledger, never the run row ────────────────────────────


def test_inputs_come_from_the_ledger_not_the_run_row(home):
    """The row is unredacted; the ledger record went through the writer's `redact()`.

    Reading the row would be the shortcut that puts an un-screened API payload into a file the
    flywheel reads forever, so the harvest uses the journal's dict even when the two differ.
    """
    run = _run(inputs={"from": "the_row"}, journal_inputs={"from": "the_ledger"})
    scenario, _ = hv.case_from_run(run)
    assert scenario["harvest"]["inputs"] == {"from": "the_ledger"}
    assert "the_row" not in json.dumps(scenario)


def test_a_run_without_run_started_is_refused_not_harvested_off_the_row(home):
    run = _run(inputs={"from": "the_row"}, started=False)
    scenario, reason = hv.case_from_run(run)
    assert scenario is None
    assert reason == hv.SKIP_NO_RUN_STARTED


def test_a_non_terminal_run_is_skipped(home):
    """An in-flight run's ledger is incomplete — harvesting it records a partial baseline."""
    run = _run(status=RunStatus.RUNNING)
    scenario, reason = hv.case_from_run(run)
    assert scenario is None
    assert reason == hv.SKIP_NOT_TERMINAL


# ── redaction, and its vacuity floor ──────────────────────────────────────────


def test_the_planted_credential_is_one_redaction_recognizes():
    """The vacuity floor UNDER the redaction test.

    If `SECRET` were a shape `redact_credentials` ignores (`password=hunter2` is one — it passes
    through untouched), the absence assertions below would pass without redaction ever running.
    """
    from personalclaw.ledger import redact

    assert redact(SECRET) != SECRET
    assert SECRET not in redact(SECRET)
    assert redact("password=hunter2") == "password=hunter2", (
        "a shape redaction does NOT catch — proof this test measures the pattern set, "
        "not merely that redact() returns a string"
    )


def test_redaction_is_not_idempotent_over_a_key_value_line():
    """WHY the harvest screens each source exactly once instead of re-screening the composed case.

    `redact()` IS idempotent on its own output. It is NOT idempotent on an already-screened value
    sitting in a `<key>: <value>` string: `redact_credentials` has a `key: value` pattern, so a
    second pass matches `api_key: [REDACTED:` and rewrites it — garbling the text AND losing the
    field name. A trailing whole-scenario `redact()` would therefore corrupt the turn text of every
    case whose inputs held a credential. Pinned here so nobody "simplifies" the screen back into
    one trailing pass.
    """
    from personalclaw.ledger import redact

    once = redact(f"- api_key: {SECRET}")
    assert redact(once) == once, "redact must be idempotent on its OWN output"

    already_screened = "- api_key: [REDACTED: credential]"
    assert redact(already_screened) != already_screened
    assert "api_key" not in redact(already_screened), "the field name is lost by a second pass"


def test_the_turn_text_of_a_screened_case_is_not_garbled(home):
    """Found by driving the CLI, not by a unit test: the rendered line must stay readable."""
    run = _run(journal_inputs={"endpoint": "https://api.example.com", "api_key": SECRET})
    scenario, _ = hv.case_from_run(run)
    user = scenario["sessions"][0]["turns"][0]["user"]

    assert SECRET not in user
    assert "api_key: [REDACTED: credential]" in user, "the field name must survive the screen"
    assert "credential] credential]" not in user, "a double screen would garble the line"
    assert "endpoint: https://api.example.com" in user


def test_a_credential_in_a_runs_inputs_is_absent_from_the_harvested_case(home):
    """The headline redaction proof.

    The journal line is written RAW through the store, bypassing `Journal.write`. Planting it via
    `run_started` would have the WRITER strip it, and the harvest's own screen would then be
    unexercised — a test of the mechanism instead of a test of the use.
    """
    run = _run(journal_inputs={"topic": "caches"}, started=False, finished=False, steps=())
    store_mod.append_jsonl(
        run.id,
        "journal.jsonl",
        {
            "kind": RUN_STARTED,
            "workflow_name": "daily_digest",
            "inputs": {"topic": "caches", "api_key": SECRET},
            "spec_version": 1,
            "seq": 1,
            "event_id": f"{run.id}-evt-1",
            "ts": "2026-01-01T00:00:00Z",
        },
    )
    # The raw line really does hold the credential — otherwise the absence below is vacuous.
    raw = store_mod.read_jsonl(run.id, "journal.jsonl")
    assert SECRET in json.dumps(raw), "planting failed; the assertion below would be vacuous"

    scenario, reason = hv.case_from_run(run)
    assert reason == ""
    assert SECRET not in json.dumps(scenario)
    assert "[REDACTED" in json.dumps(scenario["harvest"]["inputs"])


def test_the_credential_scan_can_fail(home, monkeypatch):
    """The VACUITY FLOOR for the test above: with redaction disabled the scan goes red.

    Without this, `SECRET not in json.dumps(scenario)` could be passing because the credential
    never reached the scenario for some unrelated reason, and the screen could be dead code.
    """
    run = _run(journal_inputs={"topic": "caches"}, started=False, finished=False, steps=())
    store_mod.append_jsonl(
        run.id,
        "journal.jsonl",
        {
            "kind": RUN_STARTED,
            "workflow_name": "daily_digest",
            "inputs": {"topic": "caches", "api_key": SECRET},
            "spec_version": 1,
            "seq": 1,
            "event_id": f"{run.id}-evt-1",
            "ts": "2026-01-01T00:00:00Z",
        },
    )
    monkeypatch.setattr(hv, "redact", lambda value: value)
    scenario, _ = hv.case_from_run(run)
    assert SECRET in json.dumps(scenario), (
        "with redaction stubbed out the credential MUST appear — if it does not, "
        "the redaction test above is asserting an absence it could never have observed"
    )


# ── writing: idempotent, non-shadowing ────────────────────────────────────────


def test_a_second_harvest_of_the_same_run_writes_nothing(home):
    _run(journal_inputs={"topic": "caches"})

    first = hv.harvest()
    assert first.population == 1
    assert first.cases[0].written is True

    second = hv.harvest()
    assert second.population == 1
    assert second.cases[0].written is False, "the backfill is content-keyed, not write-always"


def test_the_manifest_reports_a_harvested_case_as_harvested(home):
    """One inventory, three origins — a study names its population from the manifest."""
    _run(journal_inputs={"topic": "caches"})
    report = hv.harvest()
    name = report.cases[0].name

    manifest = sc.read_manifest()
    assert manifest is not None
    entries = manifest["scenarios"]
    assert entries[name]["origin"] == hv.ORIGIN_HARVESTED
    # The packaged set is untouched and still reported as shipped.
    assert entries["smoke_test"]["origin"] == "shipped"
    assert entries[name]["sha256"] == report.cases[0].sha256


def test_a_case_that_lost_its_provenance_stops_claiming_to_be_harvested(home, tmp_path):
    """`origin` is derived by INSPECTING the file, not from a name list."""
    _run(journal_inputs={"topic": "caches"})
    report = hv.harvest()
    path = report.cases[0].path
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["harvest"]
    path.write_text(json.dumps(data), encoding="utf-8")

    manifest = sc.install_library()
    assert manifest["scenarios"][report.cases[0].name]["origin"] == "local"


def test_a_harvest_cannot_write_over_a_packaged_scenario(home):
    """Two refusals: an unprefixed name, and a prefixed name that collides with the shipped set."""
    with pytest.raises(sc.ScenarioLibraryError, match="must start with"):
        hv._target_path("smoke_test")

    # The prefix alone is not the guard — the collision check is separate, so it still holds if a
    # `harvested_*` scenario is ever shipped.
    shipped = sc.packaged_library_dir() / f"{hv.HARVEST_PREFIX}x.json"
    shipped.write_text("{}", encoding="utf-8")
    try:
        with pytest.raises(sc.ScenarioLibraryError, match="shadow the shipped"):
            hv._target_path(f"{hv.HARVEST_PREFIX}x")
    finally:
        shipped.unlink()


def test_the_shipped_library_is_untouched_by_a_harvest(home):
    before = {p.name: p.read_bytes() for p in sc.packaged_library_dir().glob("*.json")}
    _run(journal_inputs={"topic": "caches"})
    hv.harvest()
    after = {p.name: p.read_bytes() for p in sc.packaged_library_dir().glob("*.json")}
    assert before == after


# ── the empty population is a refusal, never a suite of zero ──────────────────


def test_no_runs_at_all_is_a_refusal(home):
    report = hv.harvest()
    assert report.is_refusal is True
    assert report.considered == 0
    assert report.population == 0
    assert "not the same as a harvested suite of zero cases" in report.refusal


def test_runs_that_all_disqualify_is_a_measurement_not_a_refusal(home):
    """The distinction the exit code and the report both have to carry.

    Two runs existed and neither qualified. That is a measured zero over a real population, and
    calling it a refusal would be as wrong as calling an empty ledger a zero.
    """
    _run(status=RunStatus.RUNNING)
    _run(started=False)

    report = hv.harvest()
    assert report.considered == 2
    assert report.population == 0
    assert report.is_refusal is False
    assert report.refusal == ""
    assert report.skipped_by_reason() == {hv.SKIP_NOT_TERMINAL: 1, hv.SKIP_NO_RUN_STARTED: 1}
    assert set(report.skipped_by_reason()) <= hv.SKIP_REASONS


def test_the_strict_loader_raises_rather_than_returning_an_empty_suite(home):
    """`[]` against a threshold is a pass nobody measured, so the loader refuses to return it."""
    with pytest.raises(hv.EmptyHarvestError, match="no replay population"):
        hv.load_harvested_suite()


def test_the_strict_loader_returns_the_installed_suite(home):
    _run(name="alpha", journal_inputs={"q": "1"})
    _run(name="beta", journal_inputs={"q": "2"})
    hv.harvest()

    suite = hv.load_harvested_suite()
    assert len(suite) == 2
    assert all(case["harvest"]["run_id"] for case in suite)

    scoped = hv.load_harvested_suite(workflow_name="alpha")
    assert [c["harvest"]["workflow_name"] for c in scoped] == ["alpha"]
    with pytest.raises(hv.EmptyHarvestError, match="'gamma'"):
        hv.load_harvested_suite(workflow_name="gamma")


# ── the `consulted_refs` scope (ES-7 §3.3's replay population) ────────────────


def test_consulted_refs_records_what_the_run_actually_loaded(home):
    """The field the scope reads. Untested until now, and a filter over an unwritten field
    would have matched nothing while looking like a clean predicate."""
    run = _run(journal_inputs={"q": "1"}, consulted=("skill:code/foo", "template:daily"))
    scenario, reason = hv.case_from_run(run)

    assert reason == ""
    assert scenario["harvest"]["consulted_refs"] == ["skill:code/foo", "template:daily"]
    # A run that consulted nothing records the ABSENCE, not a missing key — the scope has to be
    # able to tell "loaded nothing" from "we never looked".
    quiet, _ = hv.case_from_run(_run(name="quiet", journal_inputs={"q": "2"}))
    assert quiet["harvest"]["consulted_refs"] == []


def test_the_suite_scopes_to_the_runs_that_consulted_a_skill(home):
    """BOTH directions plus the vacuity floor.

    Keeps the run whose ledger names the skill; drops the run that named a different one AND the
    run that named nothing. The unfiltered suite is asserted first: without it, a scope that
    matched nothing because nothing was harvested would read exactly like a working filter.
    """
    _run(name="alpha", journal_inputs={"q": "1"}, consulted=("skill:code/foo",))
    _run(name="beta", journal_inputs={"q": "2"}, consulted=("skill:code/bar",))
    _run(name="gamma", journal_inputs={"q": "3"})
    hv.harvest()

    # Vacuity floor: all three ARE in the library, so the scope below is narrowing a real
    # population rather than reporting an empty one.
    assert len(hv.load_harvested_suite()) == 3

    kept = hv.load_harvested_suite(consulted_ref="code/foo")
    assert [c["harvest"]["workflow_name"] for c in kept] == ["alpha"]
    assert [
        c["harvest"]["workflow_name"] for c in hv.load_harvested_suite(consulted_ref="code/bar")
    ] == ["beta"]
    # The DROPPED directions, each named: another skill's run, and a run that consulted nothing.
    dropped = {c["harvest"]["workflow_name"] for c in hv.installed_harvested_cases()} - {
        c["harvest"]["workflow_name"] for c in kept
    }
    assert dropped == {"beta", "gamma"}


def test_the_consulted_scope_is_not_a_substring_match_at_suite_level(home):
    """`foo` must not pull in `foo-bar`'s run, or one skill's bench replays another's inputs."""
    _run(name="alpha", journal_inputs={"q": "1"}, consulted=("skill:code/foo-bar",))
    hv.harvest()

    assert len(hv.load_harvested_suite()) == 1, "vacuity floor: the case IS in the suite"
    assert hv.load_harvested_suite(consulted_ref="code/foo-bar")
    with pytest.raises(hv.EmptyHarvestError, match="code/foo"):
        hv.load_harvested_suite(consulted_ref="code/foo")


def test_the_refusal_names_which_scope_emptied_the_suite(home):
    """ "Nothing was harvested" and "this skill has no harvested run" must not read alike."""
    _run(name="alpha", journal_inputs={"q": "1"}, consulted=("skill:code/foo",))
    hv.harvest()

    with pytest.raises(hv.EmptyHarvestError) as caught:
        hv.load_harvested_suite(consulted_ref="code/missing")
    assert "runs that consulted 'code/missing'" in str(caught.value)
    with pytest.raises(hv.EmptyHarvestError) as both:
        hv.load_harvested_suite(workflow_name="alpha", consulted_ref="code/missing")
    assert "workflow 'alpha' and runs that consulted 'code/missing'" in str(both.value)


def test_one_matcher_serves_the_live_event_and_the_frozen_ref(home):
    """The anti-second-mechanism rail.

    `consulted_runs` matches the live `consulted` event; the suite scope matches the SAME ref
    after it was frozen into `harvest.consulted_refs`. If they ever became two predicates they
    would disagree about `foo` vs `foo-bar` and one skill's bench would silently replay
    another's runs. Asserted by identity, and by the absence of the private copy that used to
    live in the bench.
    """
    from personalclaw.evals import skills_bench

    assert skills_bench.harvest.ref_names_skill is hv.ref_names_skill
    assert not hasattr(skills_bench, "_ref_names_skill"), "a second matcher was re-introduced"
    # The predicate itself, both directions.
    assert hv.ref_names_skill("skill:code/foo", "code/foo") is True
    assert hv.ref_names_skill("code/foo", "code/foo") is True
    assert hv.ref_names_skill("skill:code/foo", "foo") is True
    assert hv.ref_names_skill("skill:code/foo-bar", "foo") is False
    assert hv.ref_names_skill("", "foo") is False
    assert hv.ref_names_skill("skill:code/foo", "") is False


def test_the_suite_excludes_a_case_whose_provenance_was_stripped(home):
    """An anonymous case must drop OUT of the suite, not be scored without a population."""
    _run(journal_inputs={"q": "1"})
    report = hv.harvest()
    path = report.cases[0].path
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["harvest"]["run_id"]
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(hv.EmptyHarvestError):
        hv.load_harvested_suite()


# ── the CALL SITE: `personalclaw eval-harvest` ────────────────────────────────


def test_the_cli_command_is_wired_to_the_dispatch(home):
    """A module with no production importer is not done. This is the importer."""
    from personalclaw import cli, cli_commands

    assert cli._eval_harvest is cli_commands._eval_harvest
    source = __import__("pathlib").Path(cli.__file__).read_text(encoding="utf-8")  # noqa: PLC2701
    assert 'sub.add_parser(\n        "eval-harvest"' in source
    assert 'args.command == "eval-harvest"' in source


def _cli(**kwargs) -> argparse.Namespace:
    base = {"workflow": "", "limit": 0, "dry_run": False, "list_suite": False}
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_the_cli_harvests_a_real_run_end_to_end(home, capsys):
    from personalclaw.cli_commands import _eval_harvest

    run = _run(journal_inputs={"topic": "caches"})
    _eval_harvest(_cli())
    out = capsys.readouterr().out

    assert "Considered 1 terminal run(s); harvested 1 case(s)" in out
    assert run.id in out
    written = sc.installed_dir() / f"{hv.case_name('daily_digest', run.id)}.json"
    assert written.is_file(), "the CLI must actually land the case in the library"
    assert json.loads(written.read_text(encoding="utf-8"))["harvest"]["run_id"] == run.id


def test_the_cli_dry_run_writes_nothing(home, capsys):
    from personalclaw.cli_commands import _eval_harvest

    _run(journal_inputs={"topic": "caches"})
    _eval_harvest(_cli(dry_run=True))
    out = capsys.readouterr().out

    assert "--dry-run: nothing written" in out
    assert not list(sc.installed_dir().glob(f"{hv.HARVEST_PREFIX}*.json"))


def test_the_cli_exits_nonzero_on_an_empty_population(home, capsys):
    """The refusal has to be machine-visible: a study wiring this in must not read 0 as green."""
    from personalclaw.cli_commands import _eval_harvest

    with pytest.raises(SystemExit) as exc:
        _eval_harvest(_cli())
    assert exc.value.code == 1
    assert "Refusing: no replay population" in capsys.readouterr().out


def test_the_cli_exits_zero_when_runs_existed_but_none_qualified(home, capsys):
    """The other half of the distinction — a measured zero is a success, and says so."""
    from personalclaw.cli_commands import _eval_harvest

    _run(status=RunStatus.RUNNING)
    _eval_harvest(_cli())  # must NOT raise SystemExit
    out = capsys.readouterr().out
    assert "not an empty population" in out
    assert hv.SKIP_NOT_TERMINAL in out


def test_the_cli_list_flag_prints_the_installed_suite(home, capsys):
    from personalclaw.cli_commands import _eval_harvest

    run = _run(journal_inputs={"topic": "caches"})
    hv.harvest()
    _eval_harvest(_cli(list_suite=True))
    out = capsys.readouterr().out
    assert "Harvested suite: 1 case(s)" in out
    assert run.id in out


def test_the_cli_list_flag_refuses_an_empty_suite(home, capsys):
    from personalclaw.cli_commands import _eval_harvest

    with pytest.raises(SystemExit) as exc:
        _eval_harvest(_cli(list_suite=True))
    assert exc.value.code == 1
    assert "Refusing:" in capsys.readouterr().out


# ── the library's own runner accepts a harvested case ─────────────────────────


def test_the_existing_scenario_loader_parses_a_harvested_case(home):
    """`personalclaw eval --all` globs the library, so a harvested file has to load unchanged."""
    from personalclaw.eval.scenario import AssertionType, load_scenario

    _run(journal_inputs={"topic": "caches"})
    report = hv.harvest()
    parsed = load_scenario(report.cases[0].path)

    assert parsed.name == report.cases[0].name
    assert "harvested_run" in parsed.dimensions
    assert len(parsed.sessions) == 1
    turn = parsed.sessions[0].turns[0]
    assert turn.user
    assert [a.type for a in turn.assertions] == [AssertionType.JUDGE]
