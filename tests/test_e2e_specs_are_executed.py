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
* **`visual.spec.ts` runs too**, in merge.yml's `e2e-pinned` leg, against the pinned
  Playwright image's browser its baselines are made in (#3943), so `LOCAL_ONLY` is empty.

An exemption, when there is one, is declared with its reason AND with the condition that retires
it: the moment a workflow runs the spec, `test_no_exemption_is_also_wired` (in the tree checks)
fails and tells whoever wired it to drop the exemption. An exemption that cannot expire is how a
temporary decision becomes permanent silently.

Not asserted, because it was checked and is not true: `make gates` is invoked by no workflow,
but its gates ARE executed in CI — the `rails` job runs `scripts/gate_report.py`, the script the
target wraps, on the real tree (`tests/rails/test_gate_aggregate_runs_in_ci_tree.py` holds that
step in place). The target is a convenience wrapper, not an unrun gate.
"""

from __future__ import annotations

from e2e_specs_are_executed_shared import LOCAL_ONLY, _specs


def test_the_declared_exemptions_still_exist():
    """A LOCAL_ONLY entry for a deleted spec is a stale exemption pretending to be a policy."""
    specs = set(_specs())
    stale = sorted(s for s in LOCAL_ONLY if s not in specs)
    assert not stale, f"LOCAL_ONLY names specs that no longer exist: {stale}"


def test_every_exemption_carries_a_real_reason():
    """ "Later" is not a reason. The text has to say what makes running it impossible today."""
    thin = sorted(s for s, why in LOCAL_ONLY.items() if len(why) < 80)
    assert not thin, f"these exemptions have no mechanical reason: {thin}"
