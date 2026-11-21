"""An accepted workflow proposal starts its run, and every door that starts a run gives it the
gateway's workflow supervisor.

A proposal whose apply case is ``workflow`` names a saved definition, and accepting it in the Inbox
starts that definition through ``workflows.service.start_run``. The apply called it with no
supervisor, so every accept made a run that never launched, and the row said only the service's
code, ``WF_NO_SUPERVISOR``. The A2A door had the same gap.

Now the Inbox's apply hands the case the supervisor of the dashboard the row is applied from, and
a refusal reads as the service's sentence. A rail holds every production call of the run-start
seam to passing one (``tests/rails/test_every_run_start_passes_a_supervisor_tree.py``).

Driven through the real ``proposals_contract.apply_item`` and ``start_run`` in a home of its own,
with a definition a provider of its own serves; the supervisor is a stand-in that records what it
launches.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from personalclaw import proposals_contract as pc
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import store
from personalclaw.workflows.effects import START_DEDUPE

SPEC = {
    "name": "weekly-digest",
    "root": {
        "kind": "sequence",
        "id": "s",
        "children": [{"kind": "transform", "id": "t", "config": {"expr": "1"}}],
    },
}


class _Supervisor:
    def __init__(self) -> None:
        self.launched: list[str] = []

    async def launch(self, run, spec):
        self.launched.append(run.id)
        return SimpleNamespace(run=run)


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))

    class _Defs(defs_mod.WorkflowDefProvider):
        name = "proposal-defs"
        readonly = True

        async def list_defs(self, *, limit: int = 200, offset: int = 0):
            return [SPEC], 1

        async def get_def(self, name: str):
            await asyncio.sleep(0)
            return SPEC if name == SPEC["name"] else None

    monkeypatch.setattr(defs_mod, "_providers", {})
    defs_mod.register_provider(_Defs())
    START_DEDUPE._entries.clear()
    yield
    START_DEDUPE._entries.clear()


def _item(ref: str = SPEC["name"], item_id: str = "it-1"):
    proposal = pc.Proposal(
        title="Run the weekly digest",
        provenance="skills",
        apply={"workflow": {"ref": ref, "inputs": {}}},
    )
    return SimpleNamespace(id=item_id, refs={pc.REFS_KEY: proposal.to_dict()}, status="pending")


@pytest.mark.asyncio
async def test_accepting_a_workflow_proposal_starts_its_run_on_the_gateways_supervisor():
    """🔴 Red before: the run was made, never launched, and the row said `WF_NO_SUPERVISOR`."""
    supervisor = _Supervisor()

    outcome = await pc.apply_item(_item(), state=SimpleNamespace(workflows=supervisor))

    assert outcome.ok, outcome.error
    run_id = outcome.result["run_id"]
    assert supervisor.launched == [run_id]
    runs, _total = store.list_runs(workflow_name=SPEC["name"])
    assert [run.id for run in runs] == [run_id]


@pytest.mark.asyncio
async def test_a_start_the_service_refuses_says_why_in_its_words():
    """🔴 Red before: the row recorded only the service's code."""
    outcome = await pc.apply_item(
        _item(ref="no-such-digest", item_id="it-3"), state=SimpleNamespace(workflows=_Supervisor())
    )

    assert not outcome.ok
    assert "no workflow definition named 'no-such-digest'" in outcome.error


@pytest.mark.asyncio
async def test_an_apply_with_no_dashboard_says_the_run_could_not_start():
    outcome = await pc.apply_item(_item(item_id="it-2"))

    assert not outcome.ok
    assert "supervisor is unavailable" in outcome.error
