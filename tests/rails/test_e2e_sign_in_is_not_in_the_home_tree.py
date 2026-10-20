"""A browser run's sign-in is kept beside the gateway's home, never in it.

The browser suite signs in once (``web/e2e/auth.setup.ts``) and saves the cookie jar every spec
reuses at the path ``web/e2e/runPlaces.ts`` chooses. That path was ``<home>/.auth/state.json``,
inside the home the harness gateway serves, and the Doctor's state inventory read the ``.auth/``
folder as a path no snapshot claims: every browser run showed Settings' Doctor card as
"durability degraded", a state no real home is in.

A rail: two checks read the harness's own source (no harness file builds a sign-in folder inside
the home, and the sign-in still saves its jar at the path ``choosePlaces`` hands on). The other two
run the real ``choosePlaces`` under Node, write the jar where the sign-in writes it, and audit the
run's home with the same ``audit_home`` the Doctor's probe calls. They stay here rather than in the
suite because the rails job is the one CI job with both Node and the repo's Python.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.rails.rails_repo import REPO

HARNESS = REPO / "web" / "e2e"
RUN_PLACES = HARNESS / "runPlaces.ts"
AUTH_SETUP = HARNESS / "auth.setup.ts"

#: Chooses one run's places in a temp dir of the test's own, then writes an empty jar where
#: ``auth.setup.ts`` writes it (its first act, before the sign-in fills it), and prints the places.
PROBE = """
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { choosePlaces } from %(run_places)s
const tmp = process.argv[1]
let next = 50000
const p = await choosePlaces({}, {
  tmp, testResults: join(tmp, 'test-results'), pickPort: async () => next++,
})
mkdirSync(dirname(p.storageState), { recursive: true })
writeFileSync(p.storageState, JSON.stringify({ cookies: [], origins: [] }))
console.log(JSON.stringify(p))
"""


def _places(tmp: Path) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH: the rails job installs it, and this check needs it")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PW_") and k != "STORAGE_STATE"}
    done = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            PROBE % {"run_places": json.dumps(str(RUN_PLACES))},
            str(tmp),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=REPO / "web",
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_probe_writes_the_jar_where_the_sign_in_does():
    # The probe below stands in for `auth.setup.ts`: it writes the jar at the `storageState` that
    # `choosePlaces` hands on as STORAGE_STATE. That only models the real sign-in while the setup
    # still writes its jar there, so the setup's own source is held to it.
    src = AUTH_SETUP.read_text(encoding="utf-8")
    assert (
        "const OUT = process.env.STORAGE_STATE" in src
    ), "auth.setup.ts no longer reads STORAGE_STATE"
    assert (
        "context.storageState({ path: OUT })" in src
    ), "auth.setup.ts no longer saves the jar at OUT"


#: A harness file that builds a path into the run's home and names `.auth` in it: the shape the jar
#: had (`join(home, '.auth', 'state.json')`) and any new spelling of the same mistake.
_JAR_IN_HOME = re.compile(
    r"join\(\s*(?:home|places\.home|p\.home|PW_E2E_HOME)[^)]*['\"]\.auth['\"]"
)


def test_no_harness_file_puts_a_sign_in_folder_in_the_home():
    # Files only: a folder can end in `.ts` too (one under the harness does).
    files = sorted(p for p in HARNESS.rglob("*.ts") if p.is_file())
    assert (
        len(files) > 10
    ), f"only {len(files)} harness files found under {HARNESS}: the walk is wrong"
    offenders = [
        f"{path.relative_to(REPO)}: {m.group(0)}"
        for path in files
        for m in _JAR_IN_HOME.finditer(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "a sign-in folder inside the gateway home is a path no snapshot claims"
    # VACUITY: the pattern catches the line this change removed.
    assert _JAR_IN_HOME.search("home ? join(home, '.auth', 'state.json') : 'e2e/.auth/state.json'")


def test_the_jar_is_not_inside_the_home(tmp_path):
    p = _places(tmp_path)
    home, jar = Path(p["home"]).resolve(), Path(p["storageState"]).resolve()
    assert home.is_dir() and jar.is_file()
    assert home not in jar.parents, f"the jar {jar} is inside the gateway home {home}"
    assert jar.parent.parent == home.parent, "the jar folder sits beside the home"


def test_the_home_has_nothing_unclaimed_after_a_sign_in(tmp_path):
    from personalclaw.durability.inventory import audit_home

    p = _places(tmp_path)
    home = Path(p["home"])
    (home / "workspace").mkdir()
    # The control: a jar in the home is exactly what the Doctor flagged.
    stray = home / ".auth"
    stray.mkdir()
    (stray / "state.json").write_text("{}", encoding="utf-8")
    assert audit_home(home).unclaimed == [".auth/"]
    shutil.rmtree(stray)

    result = audit_home(home)
    assert (result.unclaimed, result.undeclared_dbs) == ([], [])
