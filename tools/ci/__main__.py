"""``python -m tools.ci <command>``: the CI controller's commands, run from the repository root."""

from __future__ import annotations

import sys
from collections.abc import Callable

from . import audit, rails_census, shards, telemetry, variation_census, verdict

COMMANDS: dict[str, Callable[[list[str]], int]] = {
    "audit": audit.main,
    "rails": rails_census.main,
    "shards": shards.main,
    "telemetry": telemetry.main,
    "variation-census": variation_census.main,
    "verdict": verdict.main,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: python -m tools.ci {{{','.join(sorted(COMMANDS))}}} ...", file=sys.stderr)
        return 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
