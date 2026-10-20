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

The gateway also inherited every credential-shaped variable of whoever started the run, so
Settings > Secrets listed the developer's own tokens and sockets as inherited from the host, and
the surface read differently on every machine. The prefix now takes out every variable the secrets
read would list (``secrets_vault._host_secret_names``: a name ``workflows.secrets`` reads as a
credential), plus ``SSH_AUTH_SOCK`` by name. The checks below plant such variables in the outer
environment and hold the probe's own secrets read to none of them.
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

from personalclaw.workflows.secrets import SECRET_KEY_HINTS, SECRET_KEY_WORD_HINTS
from personalclaw.workflows.workspace import looks_secret
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
from personalclaw import secrets_vault
print(json.dumps({
    "folders": folders,
    "set": sorted(n for n in names if n in os.environ),
    "personalclaw_home": os.environ.get("PERSONALCLAW_HOME", ""),
    "host_secrets": secrets_vault._host_secret_names(),
    "env_names": sorted(os.environ),
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


def _as_the_gateway(
    prefix: str,
    home: str,
    elsewhere: Path,
    planted: dict[str, str] | None = None,
    shell: str = "/bin/sh",
) -> dict:
    """Run the probe through the harness's prefix, as GATEWAY_COMMAND runs the gateway, from an
    outer environment where HOME and every folder variable point at ``elsewhere``, and every
    ``planted`` variable is set."""
    done = _run_as_the_gateway(prefix, home, elsewhere, planted, shell)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def _run_as_the_gateway(
    prefix: str,
    home: str,
    elsewhere: Path,
    planted: dict[str, str] | None = None,
    shell: str = "/bin/sh",
) -> subprocess.CompletedProcess:
    outer = dict(os.environ)
    outer.update(planted or {})
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
    return subprocess.run(
        [shell, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env=outer,
        cwd=WEB,
    )


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


# ── No credential reaches the gateway ─────────────────────────────────────────────────────────────


def _planted_credentials() -> dict[str, str]:
    """A variable for every hint the secrets read matches, in three spellings of its case, a word
    hint alone and with an index, the agent socket, and a value that spans lines and holds what
    looks like another variable."""
    names = ["SSH_AUTH_SOCK"]
    for hint in SECRET_KEY_HINTS:
        names += [f"E2E_PLANTED_{hint.upper()}", f"e2e_planted_{hint}", f"E2e_{hint.title()}_X"]
    for word in SECRET_KEY_WORD_HINTS:
        names += [f"E2E_PLANTED_{word.upper()}", f"E2E_PLANTED_{word.upper()}2", word.upper()]
    planted = {name: f"planted-{i}" for i, name in enumerate(names)}
    planted["E2E_PLANTED_MULTILINE_TOKEN"] = "first line\nE2E_PLANTED_NOT_A_NAME=1\nlast line"
    return planted


#: Names the secrets read does NOT call credentials, the word hint inside a longer word among them:
#: the prefix must leave each where it was, so it removes the rule's names and no others.
PLANTED_PLAIN = {
    "E2E_PLANTED_LIBRARY_PATH": "/nowhere/lib",
    "E2E_PLANTED_PATTERN": "plain",
    "E2E_PLANTED_COMPAT_MODE": "plain",
    "E2E_PLANTED_PLAIN": "plain",
}


def test_the_plants_are_what_the_secrets_read_calls_credentials():
    # The premise of the checks below, held to the read itself.
    assert all(looks_secret(n) for n in _planted_credentials())
    assert not any(looks_secret(n) for n in PLANTED_PLAIN)


def test_no_credential_shaped_variable_reaches_the_gateway(tmp_path):
    h = _harness(tmp_path)
    planted = {**_planted_credentials(), **PLANTED_PLAIN}
    seen = _as_the_gateway(h["prefix"], h["places"]["home"], tmp_path / "elsewhere", planted)

    assert seen["host_secrets"] == [], (
        "Settings > Secrets would list these, inherited from whoever started the run: "
        f"{seen['host_secrets']}"
    )
    reached = sorted(set(_planted_credentials()) & set(seen["env_names"]))
    assert reached == [], f"credential-shaped variables that reached the gateway: {reached}"
    kept = sorted(set(PLANTED_PLAIN) & set(seen["env_names"]))
    assert kept == sorted(PLANTED_PLAIN), "the prefix took out a variable the rule does not name"


def test_the_control_without_the_prefix_lists_every_plant(tmp_path):
    # The control: without the prefix the probe's secrets read lists every plant, so the check
    # above cannot pass because the probe reads nothing.
    h = _harness(tmp_path)
    seen = _as_the_gateway("", h["places"]["home"], tmp_path / "elsewhere", _planted_credentials())
    missing = sorted(set(_planted_credentials()) - set(seen["host_secrets"]))
    assert missing == [], f"the probe's secrets read did not see: {missing}"


#: Credential-shaped, and no name a shell can unset.
ODD_NAME = "e2e-planted.token"


@pytest.mark.parametrize(
    "shell", [s for s in ("/bin/sh", "/bin/bash", "/bin/dash") if Path(s).exists()]
)
def test_a_credential_the_shell_cannot_unset_never_reaches_the_gateway(tmp_path, shell):
    # Some shells hand a child a variable whose name they cannot unset, some drop it. The first
    # must refuse the run by the name, the second gives the gateway none.
    h = _harness(tmp_path)
    planted = {ODD_NAME: "planted"}
    elsewhere = tmp_path / "elsewhere"
    control = _as_the_gateway("", h["places"]["home"], elsewhere, planted, shell)
    done = _run_as_the_gateway(h["prefix"], h["places"]["home"], elsewhere, planted, shell)
    if ODD_NAME in control["env_names"]:
        assert done.returncode != 0, f"{shell} ran the gateway with {ODD_NAME} in its environment"
        assert "PERSONALCLAW_E2E_FATAL" in done.stderr and ODD_NAME in done.stderr, done.stderr
    else:
        assert done.returncode == 0, done.stderr
        seen = json.loads(done.stdout.strip().splitlines()[-1])
        assert ODD_NAME not in seen["env_names"]


def _ts_list(name: str) -> list[str]:
    src = RUN_PLACES.read_text(encoding="utf-8")
    m = re.search(rf"export const {name} = \[(.*?)\] as const", src, re.S)
    assert m, f"web/e2e/runPlaces.ts declares no {name} list"
    return re.findall(r"'([^']+)'", m.group(1))


def test_the_harness_reads_credentials_by_the_secrets_reads_rule():
    # The harness holds a copy of the hints (it runs before any Python does); the copy is the
    # secrets read's, entry for entry, so neither can drift from the other.
    assert _ts_list("GATEWAY_CREDENTIAL_HINTS") == list(SECRET_KEY_HINTS)
    assert _ts_list("GATEWAY_CREDENTIAL_WORD_HINTS") == list(SECRET_KEY_WORD_HINTS)
    assert _ts_list("GATEWAY_CREDENTIAL_NAMES") == ["SSH_AUTH_SOCK"]
