"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_ci_merge_controller.py``

Its checks:
``tests/rails/test_merge_controller_gate_plants_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module. It holds each gate of the merge verdict as the behaviour module plants
it in main's tree: its ``pull_request`` filter and its jobs' bounds, which the
rail holds equal to the real workflows'.
"""

from __future__ import annotations

from tools.ci import trust

_WORKFLOWS = trust.DEFINITION_ROOTS[0]
_ACTIONS = trust.DEFINITION_ROOTS[1]

#: Each gate's ``pull_request`` paths, by its workflow file.
GATE_FILTERS: dict[str, list[str]] = {
    "nix.yml": [
        "flake.nix",
        "flake.lock",
        "nix/**",
        "pyproject.toml",
        "uv.lock",
        "scripts/nix_pins.py",
        "scripts/nix_smoke.py",
        _WORKFLOWS + "nix.yml",
    ],
    "action-pins.yml": [_WORKFLOWS + "**", _ACTIONS + "**", "tools/ci/action_pins.py"],
    "release-image-tooling.yml": [
        _WORKFLOWS + "release.yml",
        _WORKFLOWS + "release-artifacts.yml",
        _WORKFLOWS + "release-image-tooling.yml",
        "tools/ci/oci_layout.py",
    ],
}

#: Each gate's jobs' ``timeout-minutes``, by its workflow file.
GATE_BOUNDS: dict[str, int] = {
    "nix.yml": 45,
    "action-pins.yml": 10,
    "release-image-tooling.yml": 10,
}
