"""``python -m tools.ci <command>``: the CI controller's commands, run from the repository root."""

from __future__ import annotations

import sys
from collections.abc import Callable

from . import (
    audit,
    catalogue,
    catalogue_check,
    fetch,
    flaky_issues,
    image_pins,
    jobs,
    parity,
    plan,
    quarantine_rerun,
    rails_census,
    release_approval,
    release_manifest,
    release_targets,
    report,
    select,
    selection_map,
    settings_report,
    shards,
    telemetry,
    variation_census,
    verdict,
)


def _release_tag(argv: list[str]) -> int:
    # Imported when called, as the verifier is: the tagging command reads tools/ci/trust.py,
    # which no other command, and so no ordinary CI run, loads.
    from . import release_tag

    return release_tag.main(argv)


def _contract(argv: list[str]) -> int:
    # Imported when called: only the poster's contract job runs it.
    from . import contract

    return contract.main(argv)


def _merge_controller(argv: list[str]) -> int:
    # Imported when called, as the verifier is: the poster reads tools/ci/trust.py.
    from . import merge_controller

    return merge_controller.main(argv)


def _replay(argv: list[str]) -> int:
    # Imported when called, as the verifier is: the push's replay reads the merge verdict through
    # tools/ci/trust.py, which no other command, and so no ordinary CI run, loads.
    from . import main_replay

    return main_replay.main(argv)


def _replay_shadow(argv: list[str]) -> int:
    from . import main_replay

    return main_replay.shadow_main(argv)


def _verify_check_run(argv: list[str]) -> int:
    # Imported when called: the other commands, which every CI run starts, load neither the
    # verifier nor tools/ci/trust.py.
    from . import merge_verdict

    return merge_verdict.main(argv)


COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "audit": audit.main,
    "catalogue-check": catalogue_check.main,
    "contract": _contract,
    "explain": catalogue.explain_main,
    "fetch": fetch.main,
    "flaky-issues": flaky_issues.main,
    "image-pins": image_pins.main,
    "list": catalogue.list_main,
    "merge-controller": _merge_controller,
    "parity": parity.main,
    "plan": plan.main,
    "quarantine-rerun": quarantine_rerun.main,
    "rails": rails_census.main,
    "release-approval": release_approval.main,
    "release-manifest": release_manifest.main,
    "release-tag": _release_tag,
    "release-targets": release_targets.main,
    "replay": _replay,
    "replay-shadow": _replay_shadow,
    "report": report.main,
    "run": jobs.run_main,
    "run-job": jobs.run_job_main,
    "select": select.main,
    "selection-map": selection_map.main,
    "settings-report": settings_report.main,
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
