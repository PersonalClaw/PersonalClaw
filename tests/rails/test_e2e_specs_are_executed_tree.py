"""The source-tree checks split from
``tests/test_e2e_specs_are_executed.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import dataclasses
import functools
import itertools
import re
import shutil
import subprocess

import pytest
import yaml
from ci_catalogue_steps import read
from ci_catalogue_steps import text as catalogue_text
from e2e_specs_are_executed_shared import _E2E, _ROOT, LOCAL_ONLY, _specs

from tools.ci.catalogue import Catalogue, Step

_WORKFLOWS = _ROOT / ".github" / "workflows"


def _workflow_text(without: str = "") -> str:
    """Every workflow's text (but *without*'s), then each catalogue step as a ``run:`` line: both
    of CI's shapes, ``ci.yml``'s own lines and the tier workflows' stage jobs, which run the
    catalogue's through ``run-job`` (``tests/ci_catalogue_steps.py``)."""
    workflows = [
        p.read_text(encoding="utf-8") for p in sorted(_WORKFLOWS.glob("*.yml")) if p.name != without
    ]
    steps = [f"run: {each}" for job in read().jobs for each in catalogue_text(job).splitlines()]
    return "\n".join([*workflows, *steps])


#: A spec argument on a run line: a path ending in `.spec.ts`.
_SPEC_ARG = re.compile(r"\S+\.spec\.ts\b")


#: The `--grep-invert` a whole-directory run excludes by, and what it leaves out.
_GREP_INVERT = re.compile(r"--grep-invert[ =](\S+)")


def _tags(spec: str) -> set[str]:
    """The `@tag`s a spec's source declares (`tag: '@visual'`)."""
    path = _E2E / spec
    if not path.is_file():
        return set()
    return set(re.findall(r"tag:\s*['\"](@[\w-]+)['\"]", path.read_text(encoding="utf-8")))


#: A `--project` a run line names: it runs only the specs that project runs.
_PROJECT_ARG = re.compile(r"--project[ =](\S+)")
_CONFIG = _E2E.parent / "playwright.config.ts"


@functools.cache
def _isolation() -> tuple[dict[str, str], dict[str, frozenset[str]]]:
    """`e2e/isolation.ts`'s SPEC_ISOLATION, each spec's mode, and PROJECTS_FOR, the projects a
    spec of each mode may run in, read from its source."""
    text = (_E2E / "isolation.ts").read_text(encoding="utf-8")
    modes = dict(re.findall(r"'([\w.-]+\.spec\.ts)':\s*\{\s*mode:\s*'(\w+)'", text))
    table = re.search(r"\bPROJECTS_FOR\b[^=]*=\s*\{(.*?)\n\}", text, re.S)
    allowed = {
        mode: frozenset(re.findall(r"'([\w-]+)'", names))
        for mode, names in re.findall(r"(\w+):\s*\[([^\]]*)\]", table.group(1) if table else "")
    }
    assert modes and allowed, "e2e/isolation.ts was not read: this rail would credit no project"
    return modes, allowed


#: A project's file filter in playwright.config.ts: a regex literal, or `fileMatcher(...)`.
_FILTER = re.compile(r"\b(testMatch|testIgnore):\s*(/(?:\\.|[^/\\\n])+/|fileMatcher\([^)]*\))")


@functools.cache
def project_runs(project: str, config: str | None = None) -> frozenset[str]:
    """The specs in `e2e/` that playwright.config.ts's project `project` runs: what its
    `testMatch` picks (every spec when it has none), less what its `testIgnore` drops, and only
    those whose isolation mode allows the project (the isolation reporter reds any other).

    Read from the config's own text (`config`, the file's when not given), in the two forms it
    spells a filter in. A project this cannot read is an error, not a guess: a guess would
    credit specs nothing runs.
    """
    modes, allowed = _isolation()
    serial = {spec for spec, mode in modes.items() if mode == "serial"}
    alone = {spec for spec, mode in modes.items() if mode == "alone"}
    text = _CONFIG.read_text(encoding="utf-8") if config is None else config
    projects = text[text.index("projects: [") :]
    found = re.search(rf"\bname: '{re.escape(project)}',(.*?)(?=\bname: |\n  \],)", projects, re.S)
    assert found, f"playwright.config.ts has no project {project!r} this rail can read"
    body = re.sub(r"//[^\n]*", "", found.group(1))
    filters = _FILTER.findall(body)
    assert len(filters) == len(re.findall(r"\btest(?:Match|Ignore)\b", body)), (
        f"playwright.config.ts's {project!r} project has a file filter this rail cannot read:"
        f" teach project_runs its form\n{body}"
    )
    specs = set(_specs())
    picked = set(specs)
    for key, value in filters:
        if value.startswith("/"):
            pattern = re.compile(value[1:-1])
            chosen = {s for s in specs if pattern.search(s)}
        else:
            inner = value[len("fileMatcher(") : -1]
            unread = re.sub(
                r"'[^']*'|\.\.\.|\bSTATEFUL_SPECS\b|\bALONE_SPECS\b|[\[\],\s]", "", inner
            )
            assert not unread, f"{project!r}'s {key} names {unread!r}, which this rail cannot read"
            chosen = (
                set(re.findall(r"'([^']+)'", inner))
                | (serial if "STATEFUL_SPECS" in inner else set())
                | (alone if "ALONE_SPECS" in inner else set())
            )
        picked = picked & chosen if key == "testMatch" else picked - chosen
    return frozenset(s for s in picked if project in allowed.get(modes.get(s, ""), ()))


def names_spec(text: str, spec: str, tags: set[str] | None = None) -> bool:
    """Whether `text` runs `spec` on a `playwright test` RUN line.

    Two forms count. A line that names the spec. Or a line that names NO spec, which runs the
    whole `testDir`, so it runs `spec` unless its `--grep-invert` drops a tag the spec declares
    (`tags`, read from the spec when not given). Either way, a line that names a `--project`
    runs only what the projects it names run (`project_runs`). That is how each `e2e-shard` leg
    runs its half of every shared spec but the visual rail, and none of the stateful set, which
    `e2e-stateful`'s line names.

    Matched against the run line, not the whole file: specs are mentioned in ci.yml's comments,
    and a substring search would have called a spec "executed" on the strength of prose
    describing it — the precise mistake this file exists to catch one level up. Takes the text
    as an argument so the vacuity test below can feed it a comment-only workflow and see the
    answer change.
    """
    # A whole-directory run reaches only the specs that are IN the directory: a spec named here
    # that does not exist is run by nothing, whatever the line says. `tags` stands in for a spec
    # file, so a caller that passes them is describing one.
    in_dir = tags is not None or (_E2E / spec).is_file()
    declared = _tags(spec) if tags is None else tags
    for line in text.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if stripped.startswith("#") or "playwright test" not in stripped:
            continue
        command = stripped.split("playwright test", 1)[1]
        projects = [p.strip("'\"") for p in _PROJECT_ARG.findall(command)]
        if projects and not any(spec in project_runs(p) for p in projects):
            continue  # none of the projects it names runs the spec
        if spec in command:
            return True
        if _SPEC_ARG.search(command) or not in_dir:
            continue  # names other specs only, or the spec is not in the directory it runs
        dropped = set(_GREP_INVERT.findall(command))
        if not dropped & declared:
            return True
    return False


def _run_by_ci(spec: str) -> bool:
    return names_spec(_workflow_text(), spec)


def silent(text: str) -> list[str]:
    """The specs no `playwright test` line of `text` runs and LOCAL_ONLY does not exempt."""
    return [s for s in _specs() if not names_spec(text, s) and s not in LOCAL_ONLY]


def test_every_e2e_spec_is_executed_or_declared():
    """The rail: a spec that runs nowhere must be named as such, with a reason."""
    specs = _specs()
    assert specs, "no e2e specs found — this sweep would pass vacuously"
    silent_specs = silent(_workflow_text())
    assert not silent_specs, (
        "these Playwright specs run in no workflow and are not declared local-only:\n"
        + "\n".join(f"  web/e2e/{s}" for s in silent_specs)
        + "\n\nAdd a CI job that runs it, or add it to LOCAL_ONLY with the mechanical reason "
        "it cannot run there. A suite nobody executes has no failures anybody sees."
    )


def test_without_ci_yml_the_catalogue_still_runs_every_spec():
    """The flip (#3915) deletes ci.yml, and the tier workflows run the catalogue's steps: those
    and the other workflows must run every spec the sweep counts on their own."""
    assert silent(_workflow_text(without="ci.yml")) == []


def test_no_exemption_is_also_wired():
    """A spec cannot be both exempt and executed — one of the two claims would be a lie."""
    both = sorted(s for s in LOCAL_ONLY if _run_by_ci(s))
    assert (
        not both
    ), f"these specs are declared local-only AND run in CI — remove the exemption: {both}"


def test_the_run_line_matcher_is_not_fooled_by_a_comment():
    """Vacuity for `_run_by_ci`, which is the one function every assertion above trusts.

    `a11y.spec.ts` appears in four ci.yml COMMENTS. A matcher that counted those would report
    every mentioned spec as executed, and the sweep would be green for a repo running none of
    them.
    """
    assert _run_by_ci("a11y.spec.ts"), "the matcher missed the spec that genuinely does run"
    assert not _run_by_ci("definitely-not-a-real.spec.ts"), "the matcher invents matches"
    # The distinction itself, through the SAME function the assertions above use. No
    # `--project`: no project runs a spec that is not in the tree.
    commented = "      # - run: npx playwright test e2e/ghost.spec.ts\n"
    assert not names_spec(commented, "ghost.spec.ts"), (
        "a commented-out run line counted as execution — every spec merely DESCRIBED in a "
        "workflow comment would read as wired"
    )
    live = "      - run: npx playwright test e2e/ghost.spec.ts\n"
    assert names_spec(live, "ghost.spec.ts"), "a real run line was not recognised"


def test_a_whole_directory_run_counts_except_for_what_its_grep_invert_drops():
    """A line that names no spec runs the whole `testDir`, minus what its `--grep-invert` drops
    (`@visual`, on each `e2e-shard` leg's line). Both halves of that, through the same function
    every assertion above trusts."""
    whole = "        run: npx playwright test --grep-invert @visual\n"
    assert names_spec(whole, "ghost.spec.ts", tags=set()), "a whole-directory run ran nothing"
    assert not names_spec(
        whole, "ghost.spec.ts", tags={"@visual"}
    ), "a spec the run's --grep-invert drops counted as executed"
    # A line that names OTHER specs runs only those.
    other = "        run: npx playwright test e2e/a.spec.ts e2e/b.spec.ts\n"
    assert not names_spec(
        other, "ghost.spec.ts", tags=set()
    ), "a named run ran a spec it never named"
    # And the real tree: visual.spec.ts declares the tag the job drops, so it stays exempt.
    assert "@visual" in _tags("visual.spec.ts"), "visual.spec.ts no longer declares @visual"
    assert not _run_by_ci("visual.spec.ts")


def test_a_project_filtered_line_runs_only_what_its_projects_run():
    """A line with `--project=` runs a spec only when a project it names does: `smoke` runs the
    smoke alone, `chromium` the other shared specs, and neither the stateful set. Through the
    same function every assertion above trusts."""
    both = (
        "        run: npx playwright test --project=smoke --project=chromium"
        " --shard=${{ matrix.shard }}/2 --grep-invert @visual\n"
    )
    assert names_spec(both, "smoke.spec.ts")
    assert names_spec(both, "a11y.spec.ts")
    assert not names_spec(both, "staleWrite.spec.ts"), "a shard line ran the stateful set"
    assert not names_spec(both, "deployedArtifacts.spec.ts"), "a shard line ran the stateful set"
    chromium = both.replace("--project=smoke ", "")
    assert not names_spec(chromium, "smoke.spec.ts"), "chromium ran the smoke, which it ignores"
    assert names_spec(chromium, "a11y.spec.ts")
    smoke = "        run: npx playwright test --project smoke\n"
    assert not names_spec(smoke, "a11y.spec.ts"), "the smoke project ran a shared spec"
    assert not names_spec(smoke, "staleWrite.spec.ts"), "the smoke project ran the stateful set"
    # A line that names a spec and a project that does not run it runs nothing.
    named = "        run: npx playwright test e2e/a11y.spec.ts --project=smoke\n"
    assert not names_spec(named, "a11y.spec.ts")


def test_the_projects_are_read_from_the_config_and_a_form_it_cannot_read_is_refused():
    """`project_runs` reads the real config, so a change there changes its answer; and a filter
    it cannot read is an error, never a guess."""
    config = _CONFIG.read_text(encoding="utf-8")
    ignore = "testIgnore: fileMatcher(['smoke.spec.ts', ...STATEFUL_SPECS, ...ALONE_SPECS]),"
    assert ignore in config, "chromium's testIgnore moved: re-point this plant"
    assert "smoke.spec.ts" not in project_runs("chromium")
    assert "surfaces.spec.ts" not in project_runs("chromium")
    kept = config.replace(ignore, "testIgnore: fileMatcher([...STATEFUL_SPECS, ...ALONE_SPECS]),")
    assert "smoke.spec.ts" in project_runs("chromium", kept)
    match = "testMatch: /smoke\\.spec\\.ts$/,"
    assert match in config, "the smoke project's testMatch moved: re-point this plant"
    with pytest.raises(AssertionError, match="cannot read"):
        project_runs("smoke", config.replace(match, "testMatch: SMOKE_FILES,"))
    with pytest.raises(AssertionError, match="no project 'nightly'"):
        project_runs("nightly", config)


def test_a_spec_the_workflow_lines_leave_out_is_named():
    """The planted controls on the real workflow text, through the sweep's own function: the
    stateful line without one of its specs, and the shard line without its smoke project."""
    text = _workflow_text()
    assert silent(text) == []
    stateful = "npx playwright test e2e/staleWrite.spec.ts e2e/deployedArtifacts.spec.ts"
    assert stateful in text, "the stateful line moved: re-point this plant"
    dropped = text.replace(stateful, "npx playwright test e2e/staleWrite.spec.ts")
    assert silent(dropped) == ["deployedArtifacts.spec.ts"]
    shard = "npx playwright test --project=smoke --project=chromium "
    assert shard in text, "the shard line moved: re-point this plant"
    no_smoke = text.replace(shard, "npx playwright test --project=chromium ")
    assert silent(no_smoke) == ["smoke.spec.ts"]


# ── A sharded run runs every shard, and the merge reads every leg ────────────────────────────────
#
# The shared specs run in `e2e-shard`'s legs, `--shard={shard}/2` each, so the whole-directory line
# above runs every spec only if the job runs every shard: a job of one shard would run half of
# them, and the sweep would still call all of them run. And the run is judged on the report
# `e2e/merge-legs.mjs` merges from the legs' blob folders, so a leg the merge does not name has its
# tests in no report anyone checks. Both are read from the catalogue's steps, which both of CI's
# shapes run: a job's `shards` is its matrix, and `{shard}` each leg's own number.

_SHARD = re.compile(r"--shard[ =](\{shard\}|\d+)/(\d+)")
#: The step that runs Playwright.
_PLAYWRIGHT = ("npx", "playwright", "test")


def _browser_steps(cat: Catalogue):
    for job in cat.jobs:
        for step in job.command:
            if step.argv[:3] == _PLAYWRIGHT:
                yield job, step


def shard_gaps(cat: Catalogue) -> list[str]:
    """Each sharded `playwright test` step whose job does not run each of its shards once."""
    gaps = []
    for job, step in _browser_steps(cat):
        found = next((m for word in step.argv if (m := _SHARD.fullmatch(word))), None)
        if not found:
            continue
        index, total = found.group(1), int(found.group(2))
        if index != "{shard}":
            gaps.append(f"{job.id} runs shard {index}/{total} alone: the other shards run nowhere")
        elif job.shards != total:
            gaps.append(
                f"{job.id} runs --shard={{shard}}/{total} in {job.shards} shards, not {total}"
            )
    return gaps


def leg_folders(cat: Catalogue) -> set[str]:
    """The blob folders the `playwright test` steps write, each shard's by its number."""
    folders = set()
    for job, step in _browser_steps(cat):
        folder = {**job.env, **step.env}.get("PLAYWRIGHT_BLOB_OUTPUT_DIR", "")
        for shard in range(1, job.shards + 1) if folder else ():
            folders.add(folder.replace("{shard}", str(shard)))
    return folders


def merges(cat: Catalogue) -> dict[str, set[str]]:
    """Each job's `merge-legs.mjs` steps and the blob folders they read: the root joined to each
    leg it names. `e2e` and `ui-quality` each merge the whole run."""
    out: dict[str, set[str]] = {}
    for job in cat.jobs:
        for step in job.command:
            words = list(step.argv)
            if len(words) > 2 and words[:2] == ["node", "e2e/merge-legs.mjs"]:
                out.setdefault(job.id, set()).update(f"{words[2]}/{leg}" for leg in words[3:])
    return out


def merged_folders(cat: Catalogue) -> set[str]:
    """The blob folders EVERY merge reads, so one job that drops a leg is not hidden by another."""
    each = list(merges(cat).values())
    return set.intersection(*each) if each else set()


def _with_job(cat: Catalogue, job_id: str, **changes) -> Catalogue:
    return Catalogue(
        jobs=tuple(dataclasses.replace(j, **changes) if j.id == job_id else j for j in cat.jobs)
    )


def _with_argv(cat: Catalogue, job_id: str, edit) -> Catalogue:
    """*cat* with each step of *job_id* given ``edit(argv)``'s argv."""
    job = cat.job(job_id)
    steps = tuple(Step(tuple(edit(list(s.argv))), s.workdir, s.env) for s in job.command)
    return _with_job(cat, job_id, command=steps)


def test_a_sharded_browser_run_runs_every_shard():
    cat = read()
    assert any(
        _SHARD.fullmatch(word) for _, step in _browser_steps(cat) for word in step.argv
    ), "no sharded `playwright test` step found: this check would pass vacuously"
    assert shard_gaps(cat) == []


def test_the_merge_reads_every_leg_the_browser_steps_write():
    cat = read()
    written = leg_folders(cat)
    assert written >= {
        "blob-report/shard-1",
        "blob-report/shard-2",
        "blob-report/stateful",
    }, written
    assert merged_folders(cat) == written
    assert {"e2e", "ui-quality"} <= set(merges(cat)), sorted(merges(cat))
    assert all(f == written for f in merges(cat).values()), merges(cat)


def test_a_missing_shard_or_an_unmerged_leg_is_named():
    """The planted controls, through the same functions the two checks above use."""
    cat = read()
    one = _with_job(cat, "e2e-shard", shards=1)
    assert shard_gaps(one) == ["e2e-shard runs --shard={shard}/2 in 1 shards, not 2"]
    fixed = _with_argv(cat, "e2e-shard", lambda argv: [w.replace("{shard}/2", "1/2") for w in argv])
    assert shard_gaps(fixed) == ["e2e-shard runs shard 1/2 alone: the other shards run nowhere"]
    for merging in ("e2e", "ui-quality"):
        # Either merge dropping a leg is named, though the other still reads it.
        dropped = _with_argv(
            cat,
            merging,
            lambda argv: (
                [w for w in argv if w != "stateful"] if "e2e/merge-legs.mjs" in argv else argv
            ),
        )
        assert leg_folders(dropped) - merged_folders(dropped) == {"blob-report/stateful"}, merging


def _jobs() -> dict:
    """ci.yml's jobs, for the leg gate below: ``ci.yml``'s own orchestration, which the flip
    (#3915) retires with ci.yml, where the merge verdict reads each leg's record."""
    return yaml.safe_load((_WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))["jobs"]


def _playwright_steps(jobs: dict):
    for name, job in jobs.items():
        for step in job.get("steps") or []:
            if "playwright test" in str(step.get("run", "")):
                yield name, job, step


# ── The merge is red whenever a leg did not pass ─────────────────────────────────────────────────
#
# `e2e` runs whatever the legs answered (`!cancelled()`), so it runs after a red leg too, and what
# it checks is the report the legs left. A leg can fail where that report shows nothing: a step
# after its blob was uploaded, or a gateway that never booted, which ends the leg before
# Playwright lists its tests, so its blob holds none of them. So `e2e` reads each leg's own
# result, and a leg that failed, was cancelled, was skipped or has no result is red there too.
# The step is run as written, against every answer each leg can give.

_NEEDS_RESULT = re.compile(r"\$\{\{\s*needs\.([A-Za-z0-9_-]+)\.result\s*\}\}")
#: What `needs.<job>.result` can be: GitHub's four results, and nothing for a job never run.
_ANSWERS = ("success", "failure", "cancelled", "skipped", "")


def browser_legs(jobs: dict) -> list[str]:
    """The jobs that run `playwright test`: the legs the `e2e` merge is made of."""
    return sorted({name for name, _, _ in _playwright_steps(jobs) if name != "e2e"})


def leg_gate_gaps(jobs: dict) -> list[str]:
    """Each way the `e2e` merge could be green while a leg did not pass."""
    merge = jobs.get("e2e") or {}
    legs = browser_legs(jobs)
    gaps = [f"e2e does not wait on {leg}" for leg in legs if leg not in (merge.get("needs") or [])]
    gates = [
        step
        for step in merge.get("steps") or []
        if any(_NEEDS_RESULT.fullmatch(str(v).strip()) for v in (step.get("env") or {}).values())
    ]
    if len(gates) != 1:
        return [*gaps, f"e2e has {len(gates)} steps that read a leg's result, not one"]
    gate = gates[0]
    reads: dict[str, str] = {}
    for var, value in (gate.get("env") or {}).items():
        found = _NEEDS_RESULT.fullmatch(str(value).strip())
        if found:
            reads[found.group(1)] = str(var)
    gaps += [f"e2e's leg gate does not read {leg}'s result" for leg in legs if leg not in reads]
    if re.sub(r"\s", "", str(gate.get("if", ""))) != "${{!cancelled()}}":
        gaps.append(
            f"e2e's leg gate runs under if: {gate.get('if')!r}, not !cancelled(), so it can be"
            " skipped after a red leg"
        )
    if gate.get("continue-on-error"):
        gaps.append("e2e's leg gate has continue-on-error, so its red counts for nothing")
    # A run: step with no shell is `bash -e {0}` on a GitHub runner.
    bash = shutil.which("bash") or "/bin/bash"
    for answers in itertools.product(_ANSWERS, repeat=len(reads)):
        said = dict(zip(reads, answers, strict=True))
        env = {"PATH": "/usr/bin:/bin", **{reads[leg]: said[leg] for leg in said}}
        ran = subprocess.run(
            [bash, "-e", "-c", str(gate.get("run", ""))],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        passed = all(a == "success" for a in answers)
        if (ran.returncode == 0) != passed:
            gaps.append(
                f"e2e's leg gate exits {ran.returncode} when the legs answered {said}: it must be"
                f" {'green' if passed else 'red'}"
            )
    return gaps


def test_the_merge_is_red_whenever_a_leg_did_not_pass():
    jobs = _jobs()
    assert browser_legs(jobs) == ["e2e-shard", "e2e-stateful"], browser_legs(jobs)
    assert leg_gate_gaps(jobs) == []


def test_a_merge_that_can_be_green_after_a_red_leg_is_named():
    """The planted controls, through the same function the check above uses."""
    jobs = _jobs()
    merge = jobs["e2e"]
    gate = next(
        s
        for s in merge["steps"]
        if any(_NEEDS_RESULT.fullmatch(str(v).strip()) for v in (s.get("env") or {}).values())
    )

    def with_gate(new: dict | None) -> dict:
        steps = [(new if s is gate else s) for s in merge["steps"]]
        return {**jobs, "e2e": {**merge, "steps": [s for s in steps if s is not None]}}

    assert leg_gate_gaps(with_gate(None)) == ["e2e has 0 steps that read a leg's result, not one"]
    var = next(k for k, v in gate["env"].items() if "e2e-stateful" in str(v))
    one = {**gate, "env": {k: v for k, v in gate["env"].items() if k != var}}
    assert "e2e's leg gate does not read e2e-stateful's result" in leg_gate_gaps(with_gate(one))
    assert any(
        "not !cancelled()" in g
        for g in leg_gate_gaps(with_gate({**gate, "if": "${{ success() }}"}))
    )
    assert any(
        "continue-on-error" in g
        for g in leg_gate_gaps(with_gate({**gate, "continue-on-error": True}))
    )
    quiet = leg_gate_gaps(with_gate({**gate, "run": str(gate["run"]) + "\ntrue\n"}))
    assert quiet == [], quiet
    swallowed = leg_gate_gaps(with_gate({**gate, "run": f"( {gate['run']} ) || true\n"}))
    assert any(
        "when the legs answered" in g and "it must be red" in g for g in swallowed
    ), swallowed
    unwaited = {**jobs, "e2e": {**merge, "needs": [n for n in merge["needs"] if n != "e2e-shard"]}}
    assert leg_gate_gaps(unwaited) == ["e2e does not wait on e2e-shard"]
