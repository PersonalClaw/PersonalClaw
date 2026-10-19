"""Every browser run gets a home of its own: the harness names no fixed home folder.

``web/playwright.config.ts`` once wiped and recreated one fixed folder under the temp dir at the
start of every run, and a spec built the same path to reach the gateway's home. Two runs on one
machine then shared that folder, and one run's boot wiped the home of the other's live gateway.
Each run now makes its home with ``mkdtemp`` (``web/e2e/runPlaces.ts``) and hands it on as
``PW_E2E_HOME``. This rail keeps the fixed folder, and any wipe of one, out of the harness.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_WEB = REPO / "web"


def _harness() -> dict[str, str]:
    paths = [_WEB / "playwright.config.ts", *sorted((_WEB / "e2e").glob("*.ts"))]
    return {str(p.relative_to(REPO)): p.read_text(encoding="utf-8") for p in paths}


def test_the_harness_names_no_fixed_home_folder() -> None:
    texts = _harness()
    assert "web/e2e/runPlaces.ts" in texts, "the helper that makes each run's home is gone"
    # Positive control: the helper still makes the home with mkdtemp.
    assert "mkdtempSync(" in texts["web/e2e/runPlaces.ts"]
    fixed = sorted(name for name, text in texts.items() if "personalclaw-e2e-home" in text)
    assert fixed == []


def test_the_harness_wipes_no_folder_at_boot() -> None:
    config = (_WEB / "playwright.config.ts").read_text(encoding="utf-8")
    # Positive control: the gateway still boots from a command the config writes.
    assert "GATEWAY_COMMAND" in config
    assert not re.search(r"\brm\s+-[a-z]*r", config)


def test_a_spec_reaches_the_gateways_home_through_the_run() -> None:
    texts = _harness()
    readers = sorted(name for name, text in texts.items() if "PW_E2E_HOME" in text)
    assert "web/e2e/runPlaces.ts" in readers
    # A spec takes the home through the guard, never straight from the environment: an unset
    # variable would hand the CLI no home, and it would fall back to the default one.
    spec = texts["web/e2e/staleWrite.spec.ts"]
    assert "harnessHome(process.env)" in spec
    raw = sorted(
        name
        for name, text in texts.items()
        if name != "web/e2e/runPlaces.ts"
        and re.search(r"process\.env\.PW_E2E_HOME\b(?!\s*:)", text)
    )
    assert (
        raw == []
    ), "read PW_E2E_HOME through harnessHome(), which refuses an unset or default home"


def test_the_guard_refuses_an_unset_home_and_the_default_home() -> None:
    places = (_WEB / "e2e" / "runPlaces.ts").read_text(encoding="utf-8")
    body = re.search(r"export function harnessHome\(.*?\n}\n", places, re.S)
    assert body, "runPlaces.ts no longer defines harnessHome()"
    guard = body.group(0)
    assert "PW_E2E_HOME is unset" in guard
    assert "homedir()" in guard and "'.personalclaw'" in guard
    assert "realpathSync" in guard
    # The planted runs that show each refusal before the CLI starts.
    planted = (_WEB / "src" / "design" / "e2eHarnessHome.test.ts").read_text(encoding="utf-8")
    assert "throws when PW_E2E_HOME is unset, before the CLI starts" in planted
    assert "throws when PW_E2E_HOME is the stand-in default home, before the CLI starts" in planted
