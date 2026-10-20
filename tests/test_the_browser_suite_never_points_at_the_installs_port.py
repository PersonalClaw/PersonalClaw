"""The browser suite's own instructions never aim it at the install's port.

The suite drives real turns and signs a browser in, so it writes whatever home its gateway
serves. ``playwright.config.ts`` starts its own gateway on a port of its own for that reason,
and ``web/e2e/README.md`` says so — while the README's recipe for driving an already-running
gateway, and the same recipe in ``auth.setup.ts``, pointed it at ``localhost:10000``: the port a
default install listens on. The recipes now name a scratch gateway's port instead.
"""

from __future__ import annotations

import re
from pathlib import Path

from personalclaw.config import loader

_REPO = Path(__file__).resolve().parents[1]
_WEB = _REPO / "web"


def _suite_texts() -> dict[str, str]:
    paths = [_WEB / "e2e" / "README.md", _WEB / "playwright.config.ts"]
    paths += sorted((_WEB / "e2e").glob("*.ts"))
    return {str(p.relative_to(_REPO)): p.read_text(encoding="utf-8") for p in paths}


def test_the_harness_gateway_has_a_port_of_its_own() -> None:
    # Each run picks free ports (`web/e2e/runPlaces.ts`), so there is no default to read. What
    # keeps the install's port out is the helper's own constant, which must be that port, and
    # the config must take its ports from the helper.
    config = (_WEB / "playwright.config.ts").read_text(encoding="utf-8")
    assert (
        "choosePlaces(" in config
    ), "playwright.config.ts no longer takes its ports from runPlaces.ts"
    places = (_WEB / "e2e" / "runPlaces.ts").read_text(encoding="utf-8")
    install = re.search(r"export const INSTALL_PORT = (\d+)", places)
    assert install, "runPlaces.ts no longer names the install's port it keeps out"
    assert int(install.group(1)) == loader._DEFAULT_PORT


def test_no_recipe_aims_the_suite_at_the_installs_port() -> None:
    texts = _suite_texts()
    # Positive control: the README still carries the recipe for an already-running gateway.
    assert "PW_BASE_URL=" in texts["web/e2e/README.md"]
    installs = re.compile(rf"(?:localhost|127\.0\.0\.1|0\.0\.0\.0):{loader._DEFAULT_PORT}(?!\d)")
    aimed = sorted(name for name, text in texts.items() if installs.search(text))
    assert aimed == []
