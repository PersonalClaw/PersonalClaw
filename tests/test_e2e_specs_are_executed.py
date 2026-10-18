"""Every Playwright spec in the tree either runs in CI or says why it cannot.

`web/e2e/` holds three specs. Only `a11y.spec.ts` was named by a workflow. `pwa.spec.ts` —
the proof that the service worker never serves an authenticated `/api` response from
cache, which is a data-leak control — and `visual.spec.ts` with its 40 committed baselines
ran in no automated gate at all. A suite nobody executes is not a slower suite; it is a
suite whose failures nobody will ever see, and its green history is what makes that
invisible.

This rail closes the gap in the only way that is honest about the constraint:

* **`pwa.spec.ts` now runs.** It has zero snapshot assertions and serves its own
  build on a private port, so it is platform-neutral. Verified before wiring — 3 passed in
  48s locally — rather than added on the assumption that a spec in the tree works.
* **`visual.spec.ts` cannot run yet, and the reason is mechanical, not editorial.**
  `playwright.config.ts` sets `snapshotPathTemplate` with `{platform}`, and all 40 committed
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

from e2e_specs_are_executed_shared import _E2E, LOCAL_ONLY, _specs


def test_the_declared_exemptions_still_exist():
    """A LOCAL_ONLY entry for a deleted spec is a stale exemption pretending to be a policy."""
    specs = set(_specs())
    stale = sorted(s for s in LOCAL_ONLY if s not in specs)
    assert not stale, f"LOCAL_ONLY names specs that no longer exist: {stale}"


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
