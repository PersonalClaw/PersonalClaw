"""The browser run's seeded home holds every system trigger the boot creates, switched off.

The gateway the browser suite starts runs its automations (it is not started with
``--no-crons``), and its boot creates the system's own clock triggers in a fresh home: the
notification digest, the usage recap, the source digest, the identity report, self-remediation and
the heartbeat task queue. The queue's trigger fires every minute, and an empty pass is recorded as
a fire held by a gate, so a minute into a run the dashboard's schedule card changed from its empty
state to "every recent fire was held by a gate", and any check of that card depended on how long
the run had been going.

So the run's home starts from ``web/e2e/fixtures/home/``: a ``triggers.json`` holding each of
those rows switched off, which the boot keeps as it finds it, and a ``config.json`` that switches
off the two whose switch is a setting (the identity report's cadence and self-remediation), which
the boot would otherwise switch back on.

The same ``config.json`` turns automatic update checks off. The run's gateway is a source
checkout, so its own update check, at start and on the status poll's schedule, is a ``git fetch``
of its origin: on a slow network the fetch timed out, its warning landed in Settings ›
Diagnostics' log panel, and the panel's pinned tree read it (#5618's run). With checks off the
gateway reaches out only when a test presses Check now.

These checks hold the seed to the boot: the reconcilers the boot calls are read from the gateway's
own source, so one added there, or a row one of them starts creating differently, is a red here
until the seed is regenerated.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import shutil
import textwrap
from pathlib import Path

import pytest

from tests.rails.rails_repo import REPO

SEED = REPO / "web" / "e2e" / "fixtures" / "home"

#: What the seed keeps of each row: what the boot decides when it creates one. Run-time fields
#: (next fire, counters, health) are left for the boot to fill in.
KEPT = ("id", "name", "kind", "created_by", "spec", "workflow", "delivery", "capabilities")


def _boot_reconcilers() -> list[tuple[str, str]]:
    """``(module, function)`` for every call the boot makes with its trigger store, in order.

    Read from ``GatewayOrchestrator``'s source: each such call is imported inside the method that
    makes it, so the import names the module and the call names the store.
    """
    from personalclaw.gateway import GatewayOrchestrator

    tree = ast.parse(textwrap.dedent(inspect.getsource(GatewayOrchestrator)))
    imported: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported[alias.asname or alias.name] = (node.module, alias.name)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "_trigger_store"
        and node.func.id.startswith("reconcile")
    ]
    calls.sort(key=lambda n: (n.lineno, n.col_offset))
    return [imported[c.func.id] for c in calls]


def _boot(home: Path) -> list[dict]:
    """Run the boot's reconcilers against ``home``'s trigger store; return its rows."""
    from personalclaw.triggers.store import TriggerStore

    store = TriggerStore(base_dir=home)
    for module, fn in _boot_reconcilers():
        getattr(importlib.import_module(module), fn)(store)
    return [json.loads(json.dumps(t.to_dict())) for t in store.list_triggers(include_broken=True)]


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A home of the test's own, holding the seed's settings, as the browser run's does."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    (tmp_path / "workspace").mkdir()
    shutil.copy(SEED / "config.json", tmp_path / "config.json")
    from personalclaw.config.loader import config_dir as _resolved

    assert _resolved() == tmp_path, "the home redirect did not take: refusing to touch a real home"
    return tmp_path


def _seed_rows() -> list[dict]:
    return json.loads((SEED / "triggers.json").read_text(encoding="utf-8"))["triggers"]


def test_the_boot_calls_reconcilers_this_check_can_read():
    # VACUITY: an AST walk that stopped matching would compare an empty boot to the seed.
    found = {fn for _, fn in _boot_reconcilers()}
    assert {"reconcile_digest_cron", "reconcile_heartbeat_tasks_trigger"} <= found, sorted(found)


def test_the_seed_holds_every_row_the_boot_creates_as_the_boot_creates_it(home):
    created = {r["id"]: {k: r[k] for k in KEPT} for r in _boot(home)}
    seeded = {r["id"]: {k: r[k] for k in KEPT} for r in _seed_rows()}
    assert "system:heartbeat-tasks" in created, "the minutely queue trigger is no longer created"
    assert seeded == created, (
        "web/e2e/fixtures/home/triggers.json no longer matches what the boot creates. Regenerate "
        "it from a fresh home's triggers.json, each row switched off, keeping only "
        + ", ".join(KEPT)
    )


def test_every_seeded_row_is_off_and_stays_off_through_the_boot(home):
    rows = _seed_rows()
    assert rows and all(r["enabled"] is False for r in rows), [
        r["id"] for r in rows if r["enabled"]
    ]
    shutil.copy(SEED / "triggers.json", home / "triggers.json")
    after = _boot(home)
    on = [r["id"] for r in after if r["enabled"]]
    assert on == [], f"the boot switched these back on, so they can fire during a run: {on}"
    assert sorted(r["id"] for r in after) == sorted(r["id"] for r in rows), "the boot added a row"


def test_the_seeded_settings_are_what_keeps_the_two_config_switched_rows_off(home):
    # The control: without the seed's settings, the boot switches the identity report and
    # self-remediation back on, which is why config.json carries them.
    (home / "config.json").write_text('{"dashboard":{"user_name":"Keyur"}}', encoding="utf-8")
    shutil.copy(SEED / "triggers.json", home / "triggers.json")
    on = sorted(r["id"] for r in _boot(home) if r["enabled"])
    assert on == ["system:learning-identity-report", "system:self-remediation"], on


# ── The update check ─────────────────────────────────────────────────────────────────────────


def test_the_seed_turns_automatic_update_checks_off(home):
    from personalclaw import self_update

    assert self_update.may_check_for_updates(asked=False) is False, (
        "web/e2e/fixtures/home/config.json no longer turns automatic update checks off, so the"
        " run's gateway runs a `git fetch` of its checkout on its own"
    )


def test_without_the_seeds_setting_the_gateway_would_check_on_its_own(home):
    # The control: checks are on by default, which is why config.json carries the setting.
    (home / "config.json").write_text('{"dashboard":{"user_name":"Keyur"}}', encoding="utf-8")
    from personalclaw import self_update

    assert self_update.may_check_for_updates(asked=False) is True


def test_the_gateway_starts_with_the_check_the_seed_turns_off():
    # tests/test_the_browser_runs_gateway_runs_no_update_check.py runs this call under the seed.
    from personalclaw import gateway

    assert "_updates.update_status(asked=False, at_start=True)" in inspect.getsource(gateway)


def test_the_browser_run_starts_its_gateway_on_the_seeds_settings():
    config = (REPO / "web" / "playwright.config.ts").read_text(encoding="utf-8")
    assert """'cp e2e/fixtures/home/config.json "$D/config.json"',""" in config
