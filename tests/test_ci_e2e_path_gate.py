"""The browser gates' admission decision, railed in both directions.

`ci.yml`'s `changes` job decides whether the `e2e` job, every browser spec in one Playwright run,
runs at all. A gate like that has two opposite failure modes and only one of them is loud:

* **Too eager** — it runs when it did not have to. Costs minutes. Visible in every run's timing.
* **Too lazy** — it skips a diff that CAN break a rendered surface. Costs a missed regression,
  reports a green check, and nothing ever says so.

So the asymmetry is encoded as tests rather than as a comment. `scripts/ci_touches_frontend.py`
is an IGNORE-list whose verdict defaults to `True`; the cases below pin the entries that may be
ignored, pin the ones that must never be, and assert the classifier is not vacuous in either
direction. The last group asserts the workflow actually WIRES it — a classifier no job calls is
the dead-helper failure class this repo has hit repeatedly, and it would leave the ignore-list
looking authoritative while deciding nothing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ci_e2e_path_gate_shared import _ROOT

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ci_touches_frontend import IGNORABLE, touches_frontend  # noqa: E402

_SCRIPT = _ROOT / "scripts" / "ci_touches_frontend.py"


def test_an_unenumerated_diff_runs_the_gates() -> None:
    """No paths means an UNKNOWN diff, not an empty one.

    The compare API returning nothing, a base ref failing to resolve, or a fork event shape
    nobody anticipated all land here. Skipping would convert the absence of an answer into a
    passing one — the failure class this repo's rails exist to catch.
    """
    assert touches_frontend([])[0] is True
    assert touches_frontend(["", "   "])[0] is True


def test_the_classifier_is_not_vacuous_in_either_direction() -> None:
    """Both answers must be reachable, or the parametrized cases above prove nothing.

    A classifier hardwired to `True` passes every MUST_RUN case; one hardwired to `False`
    passes every MAY_SKIP case. Only asserting that both answers occur rules out both.
    """
    assert touches_frontend(["web/src/App.tsx"])[0] is True
    assert touches_frontend(["docs/vision.md"])[0] is False


def test_every_ignorable_entry_carries_a_real_reason() -> None:
    """ "Later" is not a reason — the text must say what makes the path unreachable."""
    thin = [what for what, why in IGNORABLE if len(why) < 60]
    assert not thin, f"these ignore-list entries state no mechanical reason: {thin}"


def test_the_e2e_specs_own_directory_can_never_be_ignored() -> None:
    """The gate must not be able to hide the specs it gates.

    `tests/test_e2e_specs_are_executed.py` asserts each spec is NAMED on a workflow run line.
    It cannot see a conditional that stops the job running, so this is the other half of that
    rail: editing a spec always re-runs it.
    """
    specs = sorted((_ROOT / "web" / "e2e").glob("*.spec.ts"))
    assert specs, "no e2e specs found — this sweep would pass vacuously"
    for spec in specs:
        rel = spec.relative_to(_ROOT).as_posix()
        assert touches_frontend([rel])[0] is True, f"{rel} would not re-run its own gate"


# ── The wiring. A classifier no workflow calls decides nothing. ──────────────────────────────


def test_the_script_runs_as_a_cli_and_prints_a_github_output_line() -> None:
    """The workflow appends stdout straight to $GITHUB_OUTPUT, so its shape is a contract."""
    for paths, want in ((b"docs/vision.md\n", "frontend=false"), (b"web/x.tsx\n", "frontend=true")):
        done = subprocess.run(
            [sys.executable, str(_SCRIPT)], input=paths, capture_output=True, check=True
        )
        assert done.stdout.decode().strip() == want
        assert done.stderr.decode().strip(), "no human-readable verdict was written for the summary"
