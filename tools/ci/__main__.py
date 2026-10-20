"""``python -m tools.ci <command>``: the CI controller's commands, run from the repository root."""

from __future__ import annotations

import sys
from collections.abc import Callable

from . import (
    audit,
    catalogue,
    fetch,
    jobs,
    parity,
    plan,
    quarantine_rerun,
    rails_census,
    release_manifest,
    report,
    selection_map,
    shards,
    telemetry,
    variation_census,
    verdict,
)


def _verify_check_run(argv: list[str]) -> int:
    # Imported when called: the other commands, which every CI run starts, load neither the
    # verifier nor tools/ci/trust.py.
    from . import merge_verdict

    return merge_verdict.main(argv)


COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "audit": audit.main,
    "explain": catalogue.explain_main,
    "fetch": fetch.main,
    "list": catalogue.list_main,
    "parity": parity.main,
    "plan": plan.main,
    "quarantine-rerun": quarantine_rerun.main,
    "rails": rails_census.main,
    "release-manifest": release_manifest.main,
    "report": report.main,
    "run": jobs.run_main,
    "run-job": jobs.run_job_main,
    "selection-map": selection_map.main,
    "shards": shards.main,
    "telemetry": telemetry.main,
    "variation-census": variation_census.main,
    "verdict": verdict.main,
    "verify-check-run": _verify_check_run,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: python -m tools.ci {{{','.join(sorted(COMMANDS))}}} ...", file=sys.stderr)
        return 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
