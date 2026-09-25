"""`GET /api/action-providers` says which actions are internal and which can call a model.

Two defects, both measured on the trigger create form (2026-09-25 validation run):

* **Internal actions were offered to users.** The Action picker listed the four Self-QA steps
  (`selfqa-triage`, `selfqa-file-finding`, `selfqa-evidence`, `selfqa-commit-watch`) beside Notify
  and Bash. Each takes config a reconciler writes — a commit sha, a run workspace, a scenario id —
  so offering one offers an action nobody can configure. They must stay REGISTERED (the Self-QA
  loop dispatches them) and stay in this catalog (a row naming one still renders its label); the
  catalog marks them so the picker can leave them out.
* **The cadence-floor hint fired on a notification.** "Every 60s is below the 900s floor for an
  LLM-invoking trigger" appeared under Dashboard Notification. The form cannot know which actions
  call a model; the catalog now carries the backend's own answer (`ZERO_TOKEN_PROVIDERS`).
"""

from __future__ import annotations

import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.dashboard.handlers.hooks import api_action_providers
from personalclaw.triggers.models import ZERO_TOKEN_PROVIDERS

SELF_QA = {"selfqa-triage", "selfqa-file-finding", "selfqa-evidence", "selfqa-commit-watch"}


def _catalog() -> dict[str, dict]:
    req = make_mocked_request("GET", "/api/action-providers", app=web.Application())
    resp = asyncio.run(api_action_providers(req))
    return {p["name"]: p for p in json.loads(resp.body.decode())["providers"]}


def test_the_self_qa_steps_are_listed_AND_marked_internal():
    catalog = _catalog()
    assert SELF_QA <= set(catalog), "internal providers must stay in the catalog, only marked"
    assert all(catalog[name]["internal"] is True for name in SELF_QA)


def test_user_facing_actions_are_not_internal():
    """The vacuity leg: a flag that marked everything would hide the whole picker."""
    catalog = _catalog()
    for name in ("notify", "bash", "run-script", "invoke-agent", "run-prompt", "create-task"):
        assert catalog[name]["internal"] is False, name
    assert {n for n, p in catalog.items() if p["internal"]} == SELF_QA


def test_invokes_model_is_the_backend_table_read_back():
    """One owner for "can this action call a model": the form's hint and the list's warning must
    answer alike, so the catalog publishes the table rather than a second opinion."""
    catalog = _catalog()
    assert catalog["notify"]["invokes_model"] is False
    assert catalog["bash"]["invokes_model"] is False
    assert catalog["invoke-agent"]["invokes_model"] is True
    assert catalog["run-prompt"]["invokes_model"] is True
    for name, entry in catalog.items():
        assert entry["invokes_model"] is (name not in ZERO_TOKEN_PROVIDERS), name
