"""The source-tree checks split from
``tests/test_e2e_specs_are_executed.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import re

from e2e_specs_are_executed_shared import _E2E, _ROOT, LOCAL_ONLY, _specs

_WORKFLOWS = _ROOT / ".github" / "workflows"


def _workflow_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_WORKFLOWS.glob("*.yml")))


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


def names_spec(text: str, spec: str, tags: set[str] | None = None) -> bool:
    """Whether `text` runs `spec` on a `playwright test` RUN line.

    Two forms count. A line that names the spec. Or a line that names NO spec, which runs the
    whole `testDir`, so it runs `spec` unless its `--grep-invert` drops a tag the spec declares
    (`tags`, read from the spec when not given). That is how the `e2e` job runs every spec but
    the visual rail in one invocation.

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


def test_every_e2e_spec_is_executed_or_declared():
    """The rail: a spec that runs nowhere must be named as such, with a reason."""
    specs = _specs()
    assert specs, "no e2e specs found — this sweep would pass vacuously"
    silent = [s for s in specs if not _run_by_ci(s) and s not in LOCAL_ONLY]
    assert not silent, (
        "these Playwright specs run in no workflow and are not declared local-only:\n"
        + "\n".join(f"  web/e2e/{s}" for s in silent)
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

    `a11y.spec.ts` appears in four ci.yml COMMENTS. A matcher that counted those would report
    every mentioned spec as executed, and the sweep would be green for a repo running none of
    them.
    """
    assert _run_by_ci("a11y.spec.ts"), "the matcher missed the spec that genuinely does run"
    assert not _run_by_ci("definitely-not-a-real.spec.ts"), "the matcher invents matches"
    # The distinction itself, through the SAME function the assertions above use.
    commented = "      # - run: npx playwright test e2e/ghost.spec.ts --project=chromium\n"
    assert not names_spec(commented, "ghost.spec.ts"), (
        "a commented-out run line counted as execution — every spec merely DESCRIBED in a "
        "workflow comment would read as wired"
    )
    live = "      - run: npx playwright test e2e/ghost.spec.ts --project=chromium\n"
    assert names_spec(live, "ghost.spec.ts"), "a real run line was not recognised"


def test_a_whole_directory_run_counts_except_for_what_its_grep_invert_drops():
    """The `e2e` job names no spec: it runs the whole `testDir` minus `@visual`. Both halves of
    that, through the same function every assertion above trusts."""
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
