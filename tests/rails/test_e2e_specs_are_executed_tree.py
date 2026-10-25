"""The source-tree checks split from
``tests/test_e2e_specs_are_executed.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import dataclasses
import functools
import re

import pytest
from ci_catalogue_steps import read
from ci_catalogue_steps import text as catalogue_text
from e2e_specs_are_executed_shared import _E2E, _ROOT, LOCAL_ONLY, _specs

from tools.ci.catalogue import Catalogue, Step

_WORKFLOWS = _ROOT / ".github" / "workflows"


def _workflow_text() -> str:
    """Every workflow's text, then each catalogue step as a ``run:`` line: the tier workflows'
    stage jobs run the catalogue's through ``run-job`` (``tests/ci_catalogue_steps.py``)."""
    workflows = [p.read_text(encoding="utf-8") for p in sorted(_WORKFLOWS.glob("*.yml"))]
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


#: What starts a run line: Playwright's own, and the pinned image's runner
#: (`web/e2e/run-pinned.mjs`, #3943), which runs `playwright test` with the arguments after its
#: `--`. Its `--update` form (the refresh workflow's) regenerates baselines and judges nothing, so
#: it is not one.
_RUN_MARKERS = ("playwright test", "e2e/run-pinned.mjs -- ")


def names_spec(text: str, spec: str, tags: set[str] | None = None) -> bool:
    """Whether `text` runs `spec` on a `playwright test` RUN line, or a `run-pinned.mjs --` one.

    Two forms count. A line that names the spec. Or a line that names NO spec, which runs the
    whole `testDir`, so it runs `spec` unless its `--grep-invert` drops a tag the spec declares
    (`tags`, read from the spec when not given). Either way, a line that names a `--project`
    runs only what the projects it names run (`project_runs`). That is how each `e2e-shard` leg
    runs its half of every shared spec but the visual rail, and none of the stateful set, which
    the second leg's own line names; `e2e-pinned`'s `run-pinned.mjs` lines name the matrix and the
    visual rail.

    Matched against the run line, not the whole file: specs are mentioned in workflow comments,
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
        marker = next((m for m in _RUN_MARKERS if m in stripped), None)
        if stripped.startswith("#") or marker is None:
            continue
        command = stripped.split(marker, 1)[1]
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


def test_no_exemption_is_also_wired():
    """A spec cannot be both exempt and executed — one of the two claims would be a lie."""
    both = sorted(s for s in LOCAL_ONLY if _run_by_ci(s))
    assert (
        not both
    ), f"these specs are declared local-only AND run in CI — remove the exemption: {both}"


def test_the_run_line_matcher_is_not_fooled_by_a_comment():
    """Vacuity for `_run_by_ci`, which is the one function every assertion above trusts.

    A spec is mentioned in workflow COMMENTS. A matcher that counted those would report every
    mentioned spec as executed, and the sweep would be green for a repo running none of them.
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
    # And the real tree: visual.spec.ts declares the tag the shard legs drop, and runs instead in
    # e2e-pinned, against the pinned image's browser (#3943).
    assert "@visual" in _tags("visual.spec.ts"), "visual.spec.ts no longer declares @visual"
    shard_only = "        run: npx playwright test --project=chromium --grep-invert @visual\n"
    assert not names_spec(shard_only, "visual.spec.ts")
    assert _run_by_ci("visual.spec.ts")


def test_the_pinned_runner_is_a_run_line_and_its_refresh_is_not():
    """`run-pinned.mjs --` runs `playwright test` with what follows; its `--update` form makes
    baselines and judges nothing, so a dispatch refresh never counts as the spec's CI run."""
    pinned = "        run: node e2e/run-pinned.mjs -- e2e/visual.spec.ts\n"
    assert names_spec(pinned, "visual.spec.ts")
    refresh = "        run: node e2e/run-pinned.mjs --update -- e2e/visual.spec.ts\n"
    assert not names_spec(refresh, "visual.spec.ts")


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


#: The runners a step may start Playwright through: the pinned image's (#3943), and a shard leg's
#: guard that runs its command on one shard alone (`web/e2e/on-shard.mjs`).
_PINNED = ("node", "e2e/run-pinned.mjs", "--")
_ON_SHARD = ("node", "e2e/on-shard.mjs")


def _browser_steps(cat: Catalogue):
    """Each step that runs `playwright test`, directly or through one of the runners above."""
    for job in cat.jobs:
        for step in job.command:
            argv = tuple(step.argv)
            if argv[:2] == _ON_SHARD and "--" in argv:
                argv = argv[argv.index("--") + 1 :]
            if argv[:3] == _PLAYWRIGHT or argv[:3] == _PINNED:
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
        "blob-report/idle",
        "blob-report/stateful",
        "blob-report/matrix",
        "blob-report/visual",
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


# ── The merge waits on every leg ─────────────────────────────────────────────────────────────────
#
# `e2e` and `ui-quality` judge the report the legs left, so each runs after every leg (`after`),
# and the run's verdict reads each leg's own record: a leg that failed, was cancelled, was skipped
# or has no record is red there, whatever its blob held. (ci.yml's `e2e` read each leg's
# `needs.<leg>.result` itself; the flip, #3915, gave that to the verdict.)


def browser_legs(cat: Catalogue) -> list[str]:
    """The jobs that run `playwright test`: the legs the merges are made of."""
    return sorted({job.id for job, _ in _browser_steps(cat)})


def test_each_merge_runs_after_every_leg():
    cat = read()
    assert browser_legs(cat) == ["e2e-pinned", "e2e-shard"], browser_legs(cat)
    for merging in sorted(merges(cat)):
        assert set(cat.job(merging).after) >= set(browser_legs(cat)), (
            f"{merging} merges the legs' reports but does not run after"
            f" {sorted(set(browser_legs(cat)) - set(cat.job(merging).after))}"
        )


def test_a_merge_that_does_not_wait_on_a_leg_is_named():
    cat = read()
    early = _with_job(cat, "e2e", after=("e2e-pinned",))
    assert not set(early.job("e2e").after) >= set(browser_legs(early))
