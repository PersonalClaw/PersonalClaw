"""A browser run's gateway finds every folder it looks for in the run's own places.

The harness starts its gateway with ``PERSONALCLAW_HOME`` set to the run's home, and that was all:
``HOME`` was still the machine's, and so was every variable that names another tool's folder. So
whatever the gateway finds through the home folder or one of those variables was the host's. At
every start it lists the shared skills folder in the real home (``outside_home``
``settle_previous_locations``) and copies in any skill an earlier release left there, and
Settings > Security named the real ``~/.claude``, ``~/.codex``, ``~/.agents/skills`` and Hugging
Face folder, so a committed baseline held a path of the machine that wrote it.

Now the gateway runs with ``HOME`` set to a folder of the run's own beside its home
(``userHomeFor`` in ``web/e2e/runPlaces.ts``), made and removed with it, and every variable in
``GATEWAY_UNSET`` taken out of its environment. This rail runs the harness's own environment
prefix (``gatewayEnvPrefix``) with a probe in the gateway's place, from an outer environment that
points every one of those variables somewhere else, and holds every folder the probe resolves to
the run's places. A rail rather than a suite test because only the rails job has both Node and the
repo's Python.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.rails.rails_repo import REPO

WEB = REPO / "web"
RUN_PLACES = WEB / "e2e" / "runPlaces.ts"
CONFIG = WEB / "playwright.config.ts"

#: Every variable the gateway reads to find a folder outside its home (the survey is in this
#: change's commit). The harness must take each out of the gateway's environment.
FOLDER_VARIABLES = (
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "HF_HOME",
    "HF_HUB_CACHE",
    "HF_TOKEN_PATH",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_RUNTIME_DIR",
    "ZDOTDIR",
    "TMUX_TMPDIR",
    "PERSONALCLAW_PROJECT_DIR",
    "PERSONALCLAW_TRACE_DIR",
    "PERSONALCLAW_EXTRA_BIN_PATHS",
    "PERSONALCLAW_FIRST_PARTY_APPS_DIR",
    "PERSONALCLAW_ARTIFACT_BUILD_ROOT",
    "PERSONALCLAW_CEILING_FILE",
    "PERSONALCLAW_COMPUTER_USE_ENABLE_FILE",
    "PERSONALCLAW_LOCAL_MODEL_APPS_DIR",
)

#: Asks the harness for one run's places and the gateway's environment prefix, and prints both.
NODE_PROBE = """
import { join } from 'node:path'
import { choosePlaces, gatewayEnvPrefix, GATEWAY_UNSET } from %(run_places)s
const tmp = process.argv[1]
let next = 51000
const p = await choosePlaces({}, {
  tmp, testResults: join(tmp, 'test-results'), pickPort: async () => next++,
})
console.log(JSON.stringify({ places: p, prefix: gatewayEnvPrefix(), unset: GATEWAY_UNSET }))
"""

#: Run where the gateway would run: prints what this process reads and where each folder the
#: gateway looks in resolves.
PY_PROBE = r"""
import json, os, sys
from pathlib import Path
from personalclaw import outside_home, owner_only
from personalclaw.onboarding_import.sources import claude_code, codex
names = json.loads(sys.argv[1])
folders = {"home folder": str(Path.home())}
for place in outside_home.places():
    for i, path in enumerate(place.paths):
        folders[f"{place.id} [{i}]"] = path
folders["Hugging Face folder"] = str(outside_home.huggingface_home())
folders["Claude Code"] = str(claude_code.resolve_root())
folders["Codex"] = str(codex.resolve_root())
for path, _is_dir, kind in owner_only.owners_own():
    folders[f"owner's {kind} file {Path(path).name}"] = path
print(json.dumps({
    "folders": folders,
    "set": sorted(n for n in names if n in os.environ),
    "personalclaw_home": os.environ.get("PERSONALCLAW_HOME", ""),
}))
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH: the rails job installs it, and this check needs it")
    return node


def _harness(tmp: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("PW_") and k != "STORAGE_STATE"}
    done = subprocess.run(
        [
            _node(),
            "--input-type=module",
            "-e",
            NODE_PROBE % {"run_places": json.dumps(str(RUN_PLACES))},
            str(tmp),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=WEB,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def _as_the_gateway(prefix: str, home: str, elsewhere: Path) -> dict:
    """Run the probe through the harness's prefix, as GATEWAY_COMMAND runs the gateway, from an
    outer environment where HOME and every folder variable point at ``elsewhere``."""
    outer = dict(os.environ)
    outer["HOME"] = str(elsewhere / "home")
    for name in FOLDER_VARIABLES:
        outer[name] = str(elsewhere / name.lower())
    outer["PW_E2E_HOME"] = home
    outer["PYTHONPATH"] = str(REPO / "src")
    script = (
        f'D="$PW_E2E_HOME"\n' f'{prefix} PERSONALCLAW_HOME="$D" exec "$PY" -c "$PROBE" "$NAMES"'
    )
    outer["PY"] = sys.executable
    outer["PROBE"] = PY_PROBE
    outer["NAMES"] = json.dumps(list(FOLDER_VARIABLES))
    done = subprocess.run(
        ["/bin/sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env=outer,
        cwd=WEB,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def _inside(path: str, roots: list[Path]) -> bool:
    real = Path(os.path.realpath(path))
    return any(real == r or r in real.parents for r in roots)


def test_every_folder_the_gateway_looks_in_is_one_of_the_runs(tmp_path):
    h = _harness(tmp_path)
    places = h["places"]
    elsewhere = tmp_path / "elsewhere"
    seen = _as_the_gateway(h["prefix"], places["home"], elsewhere)
    roots = [Path(os.path.realpath(places["home"])), Path(os.path.realpath(places["userHome"]))]

    assert seen["personalclaw_home"] == places["home"]
    assert seen["set"] == [], f"left in the gateway's environment: {seen['set']}"
    assert len(seen["folders"]) >= 8, f"the probe resolved too few folders: {seen['folders']}"
    outside = {k: v for k, v in seen["folders"].items() if not _inside(v, roots)}
    assert outside == {}, f"folders the gateway would look in outside the run's places: {outside}"
    assert Path(seen["folders"]["home folder"]).resolve() == Path(places["userHome"]).resolve()


def test_the_control_without_the_prefix_reads_the_outer_folders(tmp_path):
    # The control: the same probe with no prefix sees every variable and the outer home, so the
    # check above cannot pass because the probe reads nothing.
    h = _harness(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    seen = _as_the_gateway("", h["places"]["home"], elsewhere)
    assert seen["set"] == sorted(FOLDER_VARIABLES)
    assert Path(seen["folders"]["home folder"]) == elsewhere / "home"
    assert Path(seen["folders"]["Claude Code"]) == elsewhere / "claude_config_dir"


def test_the_harness_unsets_every_folder_variable():
    unset = _harness_unset()
    assert sorted(unset) == sorted(
        FOLDER_VARIABLES
    ), "GATEWAY_UNSET in web/e2e/runPlaces.ts and FOLDER_VARIABLES here must name the same set"


def _harness_unset() -> list[str]:
    src = RUN_PLACES.read_text(encoding="utf-8")
    m = re.search(r"export const GATEWAY_UNSET = \[(.*?)\] as const", src, re.S)
    assert m, "web/e2e/runPlaces.ts declares no GATEWAY_UNSET list"
    return re.findall(r"'([A-Z0-9_]+)'", m.group(1))


def test_the_gateway_command_starts_the_gateway_through_the_prefix():
    src = CONFIG.read_text(encoding="utf-8")
    starts = [line for line in src.splitlines() if " gateway --port " in line]
    assert len(starts) == 1, f"expected one gateway start in the config, found {starts}"
    assert "${gatewayEnvPrefix()}" in starts[0], (
        "playwright.config.ts starts the gateway without gatewayEnvPrefix(), so HOME and the "
        f"folder variables reach it from the machine: {starts[0].strip()}"
    )


#: A shell line that starts a gateway: the CLI's `gateway` command with a port.
_GATEWAY_START = re.compile(r"\bgateway --port\b")


def test_no_harness_file_starts_a_gateway_without_the_prefix():
    # Files only: a folder can end in `.ts` too (one under the harness does).
    files = sorted(p for p in (WEB / "e2e").rglob("*.ts") if p.is_file()) + [CONFIG]
    assert len(files) > 10, f"only {len(files)} harness files found: the walk is wrong"
    starts = [
        (path, line)
        for path in files
        for line in path.read_text(encoding="utf-8").splitlines()
        if _GATEWAY_START.search(line)
    ]
    assert starts, "no harness file starts a gateway: the pattern is wrong"
    bare = [
        f"{path.relative_to(REPO)}: {line.strip()}"
        for path, line in starts
        if "${gatewayEnvPrefix()}" not in line
    ]
    assert bare == [], f"a gateway started with the machine's HOME and folder variables: {bare}"
    # VACUITY: the pattern catches the start line this change replaced.
    assert _GATEWAY_START.search(
        'PERSONALCLAW_HOME="$D" exec "$PC" gateway --port ${GATEWAY_PORT} --no-open'
    )


def test_the_users_home_folder_goes_with_the_run(tmp_path):
    h = _harness(tmp_path)
    p = h["places"]
    home, user = Path(p["home"]), Path(p["userHome"])
    assert user.is_dir() and p["madeUserHome"] is True
    assert home not in user.parents, "inside the home it is a path no snapshot claims"
    assert user.parent == home.parent, "the home folder sits beside the run's home"
