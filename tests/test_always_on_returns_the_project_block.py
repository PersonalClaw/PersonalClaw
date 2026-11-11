"""The always-on read returns the block a new chat in a project is given, word for word.

A project's page shows what a new chat there is told. The block is written once, by
`dashboard.chat_utils.project_context_block`, whose text a chat's first turn gets
(`_project_context_preamble`, `chat_runner`). `GET /api/legibility/always-on?project_id=` already
built it, to check that its items land in a real prompt, and then dropped it from its answer, so the
page could only have rebuilt it, which is the second copy that drifts. It now answers
`project_block`:

- `text`: the producer's own text, masked as the chat's first turn masks it where the block joins
  the request (`chat_runner._ahead_of_the_request`);
- `parts`: how many of the owner's parts the block carries, counted by the code that writes it:
  the brief and the overview (0 or 1), the decisions it carries (the newest 8), and the open
  questions and out-of-scope items it carries (the first 8 of each).

With no `project_id` there is no block (`null`). A project that does not exist gets an empty one,
and so does a project whose block cannot be written, while the rest of the read answers.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from gateway_routes_shared import handler_for

from personalclaw import project_context
from personalclaw.security import redact_for_model
from personalclaw.tasks.hierarchy import HierarchyStore

pytestmark = pytest.mark.security

SECRET = "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
# A link carrying data out in its query, which the chat's mask replaces as a whole.
EXFIL = "https://collect.example.net/p?d=" + ("Q" * 60)
_NO_PARTS = {"brief": 0, "overview": 0, "decisions": 0, "open_questions": 0, "out_of_scope": 0}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    with (
        patch("personalclaw.tasks.hierarchy.config_dir", return_value=home),
        patch("personalclaw.loop.files.config_dir", return_value=home),
    ):
        yield home


def _always_on(query: str) -> dict:
    """The route the gateway serves, answered as the gateway answers it."""
    from personalclaw.dashboard.handlers.legibility import api_always_on

    assert handler_for("GET", "/api/legibility/always-on") is api_always_on
    request = make_mocked_request("GET", f"/api/legibility/always-on{query}", app=web.Application())
    resp = asyncio.run(api_always_on(request))
    assert resp.status == 200, resp.body
    return json.loads(resp.body)


def test_the_block_is_the_producers_text_and_says_what_it_carries():
    from personalclaw.dashboard.chat_runner import _ahead_of_the_request
    from personalclaw.dashboard.chat_utils import _project_context_preamble

    project = HierarchyStore().create_project("Garden")
    HierarchyStore().update_project(
        project.id,
        brief=f"Plan and grow the spring beds. The sensor key is {SECRET}, the notes at {EXFIL}",
    )
    assert project_context.write_overview(project.id, "The beds are dug and the soil is ordered.")
    for n in range(10):
        assert project_context.append_ledger(project.id, "decisions", f"Decision {n}: raised beds")
    for n in range(9):
        assert project_context.append_ledger(project.id, "fog", f"Question {n}: which vendor?")
        assert project_context.append_ledger(project.id, "out_of_scope", f"Not doing {n}: a shed")

    produced = _project_context_preamble(project.id)
    assert "Plan and grow the spring beds" in produced, "planted nothing"
    assert SECRET in produced and EXFIL in produced, "planted nothing to mask"

    block = _always_on(f"?project_id={project.id}")["project_block"]
    assert block["text"] == redact_for_model(produced)
    assert _ahead_of_the_request(produced, "hi").startswith(block["text"]), "not what a chat gets"
    assert SECRET not in block["text"], "a credential reached the read"
    assert EXFIL not in block["text"], "the read shows a link the chat is given masked"
    assert block["parts"] == {
        "brief": 1,
        "overview": 1,
        "decisions": 8,
        "open_questions": 8,
        "out_of_scope": 8,
    }


def test_a_bare_project_carries_none_of_the_owners_parts():
    project = HierarchyStore().create_project("Bare")
    block = _always_on(f"?project_id={project.id}")["project_block"]
    assert block["parts"] == _NO_PARTS
    assert "Bare" in block["text"], "the block still names its project"


def test_no_project_means_no_block_and_an_unknown_one_an_empty_block():
    assert _always_on("")["project_block"] is None
    assert _always_on("?project_id=p-nope")["project_block"] == {"text": "", "parts": _NO_PARTS}


def test_a_block_that_cannot_be_written_leaves_the_read_answering(monkeypatch):
    project = HierarchyStore().create_project("Garden")

    def composer_failed(project_id: str):
        raise RuntimeError("composer failed")

    monkeypatch.setattr("personalclaw.dashboard.chat_utils.project_context_block", composer_failed)
    body = _always_on(f"?project_id={project.id}")
    assert body["project_block"] == {"text": "", "parts": _NO_PARTS}
    assert "items" in body, "the rest of the read still answers"
