"""Every Playwright spec in the tree either runs in CI or says why it cannot.

`web/e2e/` holds three specs. Only `a11y.spec.ts` was named by a workflow. `pwa.spec.ts` —
the proof that the service worker never serves an authenticated `/api` response from
cache, which is a data-leak control — and `visual.spec.ts` with its 32 committed baselines
ran in no automated gate at all. A suite nobody executes is not a slower suite; it is a
suite whose failures nobody will ever see, and its green history is what makes that
invisible.

This rail closes the gap in the only way that is honest about the constraint:

* **`pwa.spec.ts` now runs.** It has zero snapshot assertions and serves its own
  build on a private port, so it is platform-neutral. Verified before wiring — 3 passed in
  48s locally — rather than added on the assumption that a spec in the tree works.
* **`visual.spec.ts` cannot run yet, and the reason is mechanical, not editorial.**
  `playwright.config.ts` sets `snapshotPathTemplate` with `{platform}`, and all 32 committed
  baselines are `-darwin`. A Linux CI job would find zero baselines and fail on every route
  on its first run. Producing `-linux` baselines requires a Linux run, which is the thing
  that does not exist yet.

So the exemption is declared with its reason AND with the condition that retires it: the
moment a `-linux` baseline is committed, `test_the_visual_exemption_retires_itself` fails and
tells whoever committed it to wire the job. An exemption that cannot expire is how a
temporary decision becomes permanent silently.

Not asserted, because it was checked and is not true: `make gates` is invoked by no workflow,
but its three gates ARE executed in CI — `test_gate_report.py`'s
`test_all_gates_pass_on_a_clean_tree` calls `gate_report.main()` and asserts it exits 0, and
the suite runs on every PR. The target is a convenience wrapper, not an unrun gate.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_E2E = _ROOT / "web" / "e2e"
_WORKFLOWS = _ROOT / ".github" / "workflows"

#: Specs deliberately NOT run in CI, each with the mechanical reason. A spec absent from
#: both this map and the workflows fails the sweep — "we'll wire it later" has to be a
#: sentence someone typed, not a file nobody noticed.
LOCAL_ONLY: dict[str, str] = {
    "visual.spec.ts": (
        "All 32 baselines are platform-qualified `-darwin` (playwright.config.ts's "
        "`snapshotPathTemplate` includes `{platform}`), so a Linux runner has no baseline to "
        "compare against and would fail every route on its first run. Wiring this job "
        "requires committing `-linux` baselines, which requires a Linux run — see "
        "`test_the_visual_exemption_retires_itself`, which fails the moment one appears."
    ),
}


def _specs() -> list[str]:
    return sorted(p.name for p in _E2E.glob("*.spec.ts"))


def _workflow_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_WORKFLOWS.glob("*.yml")))


def names_spec(text: str, spec: str) -> bool:
    """Whether `text` invokes `spec` on a `playwright test` RUN line.

    Matched against the run line, not the whole file: `a11y.spec.ts` is mentioned in four
    comments in ci.yml, and a substring search would have called the spec "executed" on the
    strength of prose describing it — the precise mistake this file exists to catch one
    level up. Takes the text as an argument so the vacuity test below can feed it a
    comment-only workflow and see the answer change.
    """
    for line in text.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if stripped.startswith("#"):
            continue
        if "playwright test" in stripped and spec in stripped:
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


def test_the_declared_exemptions_still_exist():
    """A LOCAL_ONLY entry for a deleted spec is a stale exemption pretending to be a policy."""
    specs = set(_specs())
    stale = sorted(s for s in LOCAL_ONLY if s not in specs)
    assert not stale, f"LOCAL_ONLY names specs that no longer exist: {stale}"


def test_no_exemption_is_also_wired():
    """A spec cannot be both exempt and executed — one of the two claims would be a lie."""
    both = sorted(s for s in LOCAL_ONLY if _run_by_ci(s))
    assert (
        not both
    ), f"these specs are declared local-only AND run in CI — remove the exemption: {both}"


def test_every_exemption_carries_a_real_reason():
    """ "Later" is not a reason. The text has to say what makes running it impossible today."""
    thin = sorted(s for s, why in LOCAL_ONLY.items() if len(why) < 80)
    assert not thin, f"these exemptions have no mechanical reason: {thin}"


def test_the_visual_exemption_retires_itself():
    """The exemption's expiry condition, as a test rather than a promise.

    `visual.spec.ts` is exempt ONLY because every baseline is `-darwin`. The day a `-linux`
    baseline is committed that reason is gone, and this fails so the person who committed it
    is the one told to wire the job — instead of the exemption quietly outliving its cause.
    """
    shots = sorted((_E2E / "__screenshots__" / "visual.spec.ts").glob("*.png"))
    assert shots, "no visual baselines found — the exemption's premise cannot be checked"
    platforms = sorted({re.sub(r".*-([a-z0-9]+)\.png$", r"\1", p.name) for p in shots})
    assert platforms == ["darwin"], (
        "the visual baselines are no longer darwin-only "
        f"({platforms}) — the reason `visual.spec.ts` is exempt from CI no longer holds. "
        "Wire a job that runs it on that platform and remove it from LOCAL_ONLY."
    )


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
