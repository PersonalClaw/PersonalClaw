"""Every ``personalclaw`` command a shipped deployment runs is one the CLI accepts.

A flag the CLI does not have makes ``personalclaw`` print its usage and exit 2 before it starts
anything, which reads as a gateway that never came up. A service path once passed one
(``--no-browser``) and so measured nothing, and the dev Compose overlay ran the same command, so
its gateway exited on every ``up``. Neither needs a runtime to see: the commands are read out of
every ``compose*.yaml`` and ``Dockerfile*`` under ``deploy/``, and each is handed to the CLI's own
parser.

What the image serves once it boots is the image smoke's
(``tools/docker_single_container_smoke.py``); the published Compose stack's own bring-up is the
weekly clean-machine walkthrough's.
"""

from __future__ import annotations

import json
import re
import shlex

import yaml
from rails_repo import REPO


def _deployment_commands() -> list[tuple[str, list[str]]]:
    """Each `personalclaw` command a shipped Compose file or image runs, with where it is."""
    found = []
    for compose in sorted((REPO / "deploy").rglob("compose*.yaml")):
        where = compose.relative_to(REPO)
        services = (yaml.safe_load(compose.read_text(encoding="utf-8")) or {}).get("services")
        for name, service in (services or {}).items():
            for key in ("entrypoint", "command"):
                argv = (service or {}).get(key)
                argv = shlex.split(argv) if isinstance(argv, str) else argv
                if argv and argv[0] == "personalclaw":
                    found.append((f"{where}: {name}.{key}", [str(a) for a in argv]))
    for dockerfile in sorted((REPO / "deploy").rglob("Dockerfile*")):
        where = dockerfile.relative_to(REPO)
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            exec_form = re.match(r"\s*(CMD|ENTRYPOINT)\s+(\[.*\])\s*$", line)
            if exec_form and (argv := json.loads(exec_form.group(2))) and argv[0] == "personalclaw":
                found.append((f"{where}: {exec_form.group(1)}", argv))
    return found


def test_every_gateway_command_the_deployments_run_is_one_the_cli_accepts() -> None:
    from personalclaw import cli

    commands = _deployment_commands()
    # The floor: the image's own command is found, so a reader that finds nothing cannot pass.
    assert any(where == "deploy/docker/Dockerfile.backend: CMD" for where, _ in commands), commands
    refused = []
    for where, argv in commands:
        try:
            cli.build_parser().parse_args(argv[1:])
        except SystemExit:
            refused.append(f"{where}: {shlex.join(argv)}")
    assert not refused, "commands the CLI refuses, so they start nothing:\n" + "\n".join(refused)
