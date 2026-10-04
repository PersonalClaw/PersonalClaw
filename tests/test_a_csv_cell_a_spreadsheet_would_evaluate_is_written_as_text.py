"""A CSV PersonalClaw writes opens in a spreadsheet as the data it holds.

A CSV says nothing about what its cells are, so a spreadsheet program opening one decides from each
cell's text: it reads a cell whose text begins with ``=``, ``+``, ``-`` or ``@`` as a formula, and
some programs drop a leading tab or carriage return first. A CSV's cells come from wherever the
agent, an app or a workflow found them: a page, a message, an imported file. So every CSV
PersonalClaw writes holds such a cell behind a single quote, and the spreadsheet shows it as the
text it holds. That is the file the CSV writer renders (``sheet_create``, an app's
``get_writer("csv")``), and the text of every CSV artifact, whoever saves it: the agent's artifact
tools, a workflow's publish and its artifact-update step, an app's request and your own edit in the
Artifacts editor, since the artifact store keeps that text by the same rule. A number stays a number
(a negative one, an amount with a currency sign and a percentage too), a date stays a date, a dash
written for "none" stays a dash, and every other cell is written as it was before.
"""

from __future__ import annotations

import csv
import io
import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from personalclaw.apps.permissions import scoped_to_app
from personalclaw.artifacts import changes
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.documents import get_writer
from personalclaw.documents.model import Sheet, SheetCell, SheetModel
from personalclaw.stale_write import revision_of

#: Each character a spreadsheet reads as the start of a formula when it begins a cell's text.
LEADS = ["=", "+", "-", "@", "\t", "\r"]

#: A ledger as it might arrive from a page: dates, amounts, and an item that begins like a formula.
LEDGER = "Date,Item,Amount\n2026-10-04,Refund,-20\n2026-10-05,=1+1,-$45.20\n"
#: The same ledger as a CSV artifact keeps it.
LEDGER_KEPT = "Date,Item,Amount\n2026-10-04,Refund,-20\n2026-10-05,'=1+1,-$45.20\n"

#: CSV text the ``csv`` module cannot read: one field longer than its field limit.
UNREADABLE = "Item\n" + "x" * 200_000 + "\n"
#: What a refusal of that text says.
CANNOT_READ = "could not read the csv text: field larger than field limit (131072)"


def _render(rows: list[list[object]]) -> bytes:
    return get_writer("csv")(SheetModel.from_rows({"Sheet1": rows}))


def _cells(data: bytes) -> list[list[str]]:
    """The cells a CSV reader finds in the writer's bytes: what a spreadsheet is handed."""
    return list(csv.reader(io.StringIO(data.decode("utf-8"), newline="")))


def _as_before(rows: list[list[object]]) -> bytes:
    """What the writer wrote for these rows before the rule: the standard library's CSV."""
    out = io.StringIO(newline="")
    csv.writer(out).writerows(rows)
    return out.getvalue().encode("utf-8")


@pytest.fixture(autouse=True)
def _no_stray_listeners():
    """Every test starts and ends with no artifact listener: the change seam is module state."""
    before = list(changes._listeners)
    changes._listeners.clear()
    yield
    changes._listeners.clear()
    changes._listeners.extend(before)


# ── the file the CSV writer renders ──────────────────────────────────────────


@pytest.mark.parametrize(
    "lead", LEADS, ids=["equals", "plus", "minus", "at", "tab", "carriage-return"]
)
def test_a_cell_whose_text_begins_like_a_formula_is_written_as_text(lead):
    text = f"{lead}1+1"

    assert _cells(_render([["Note"], [text]])) == [["Note"], [f"'{text}"]]


def test_a_formula_the_model_declares_is_written_as_its_text():
    """A CSV cannot say that a cell is a formula, so a formula cell is written by the same rule:
    its text shows, and the spreadsheet computes nothing."""
    model = SheetModel(
        sheets=[
            Sheet(
                name="Totals",
                cells=[[SheetCell(value="Total"), SheetCell(value=30, formula="=SUM(B1:B2)")]],
            )
        ]
    )

    assert _cells(get_writer("csv")(model)) == [["Total", "'=SUM(B1:B2)"]]


def test_text_that_only_begins_like_a_number_is_written_as_text():
    """A sign followed by anything but a number's digits and separators is text a spreadsheet
    would try to compute, so it is written behind the quote like any other."""
    rows: list[list[object]] = [["-20 apples"], ["+1-555-0100"], ["-x-"], ["@home"], ["-5%%"]]

    assert _cells(_render(rows)) == [
        ["'-20 apples"],
        ["'+1-555-0100"],
        ["'-x-"],
        ["'@home"],
        ["'-5%%"],
    ]


def test_a_cell_of_dashes_alone_is_written_as_it_is():
    """A dash is how a table says "none", and dashes alone hold nothing to compute."""
    rows: list[list[object]] = [["Item", "Note"], ["Refund", "-"], ["Fee", "--"]]

    assert _render(rows) == _as_before(rows)


def test_a_byte_order_mark_in_front_of_a_formula_does_not_hide_it():
    """A spreadsheet drops a byte-order mark that opens the file before it reads the first
    cell, so the mark does not keep the cell after it from being read as a formula."""
    assert _cells(_render([["\ufeff=1+1", "Refund"]])) == [["'\ufeff=1+1", "Refund"]]


def test_a_number_cell_is_written_as_the_number_it_is():
    rows: list[list[object]] = [
        ["Item", "Amount"],
        ["Refund", -20],
        ["Fee", -1.5],
        ["Deposit", 1200],
        ["Rounding", -1e-05],
    ]

    data = _render(rows)

    assert data == _as_before(rows)
    assert _cells(data)[1:] == [
        ["Refund", "-20"],
        ["Fee", "-1.5"],
        ["Deposit", "1200"],
        ["Rounding", "-1e-05"],
    ]


def test_text_that_is_a_number_is_written_as_the_number_it_is():
    """CSV text hands every cell over as text, so its negative amounts arrive as text: with a
    sign, separators, an exponent, a currency sign or a percent sign, each is still a number."""
    rows: list[list[object]] = [
        ["Item", "Amount"],
        ["Refund", "-20"],
        ["Transfer", "-1,234.56"],
        ["Rate", "+1.5"],
        ["Drift", "-2e-3"],
        ["Half", "-.5"],
        ["Card", "-$45.20"],
        ["Fare", "-12.50€"],
        ["Change", "-12.5%"],
    ]

    assert _render(rows) == _as_before(rows)


def test_a_date_is_written_as_it_is():
    rows: list[list[object]] = [
        ["Date"],
        ["2026-10-04"],
        ["10/04/2026"],
        ["4 Oct 2026"],
        ["2026-10-04T09:30:00-07:00"],
    ]

    assert _render(rows) == _as_before(rows)


def test_ordinary_text_is_written_as_before():
    rows: list[list[object]] = [
        ["Name", "Notes"],
        ["Ada", "re-check on Monday"],
        ["Grace", 'said "yes", then left'],
        ["Linus", "first line\nsecond line"],
        ["Joan", ""],
        ["Alan", None],
        ["Mary", True],
    ]

    assert _render(rows) == _as_before(rows)


def test_a_cell_already_behind_a_quote_is_not_quoted_again():
    """Writing a sheet that was read back from such a CSV leaves its cells as they were."""
    rows: list[list[object]] = [["'=1+1", "'-20 apples"]]

    assert _render(rows) == _as_before(rows)


class TestTheAgentsCsv:
    """The tool the agent makes a CSV with goes through the same writer, whatever it was given."""

    def _prov(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
        return NativeArtifactProvider(root=tmp_path / "artifacts")

    def _made(self, tmp_path, monkeypatch, args: dict) -> list[list[str]]:
        from personalclaw.mcp_artifacts import _document_create

        prov = self._prov(tmp_path, monkeypatch)
        reply = _document_create(
            prov, "sheet_create", {"name": "Notes", "format": "csv", **args}, None, _quiet
        )
        assert "Error" not in reply, reply
        [made] = prov.list()
        assert made.kind == "csv"
        stored = tmp_path / "artifacts" / made.slug / "current.html"
        return _cells(stored.read_bytes())

    def test_rows_whose_text_begins_like_a_formula_are_saved_as_text(self, tmp_path, monkeypatch):
        rows = [["Note", "Amount"], ["=1+1", -20], ["@home", "-20"]]

        cells = self._made(tmp_path, monkeypatch, {"rows": rows})

        assert cells == [["Note", "Amount"], ["'=1+1", "-20"], ["'@home", "-20"]]

    def test_csv_text_whose_cells_begin_like_a_formula_is_saved_as_text(
        self, tmp_path, monkeypatch
    ):
        text = "Note,Amount\n=1+1,-20\n+1+1,12\n"

        cells = self._made(tmp_path, monkeypatch, {"csv": text})

        assert cells == [["Note", "Amount"], ["'=1+1", "-20"], ["'+1+1", "12"]]


def _quiet(outcome, slug="", error=""):
    """The tool's audit callback; these tests read what was stored, not the audit row."""


# ── the text of a CSV artifact, whoever saves it ─────────────────────────────


@pytest.fixture
def places(tmp_path, monkeypatch):
    """A home and a workspace, the folder a file the agent saves from may be read in."""
    home, ws = tmp_path / "home", tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(ws))
    return home, ws


@pytest.fixture
def store(places):
    """The artifact store every door reaches: the agent's tools, a workflow, a request."""
    home, _ws = places
    provider = NativeArtifactProvider(root=home / "artifacts")
    with (
        patch("personalclaw.artifacts.registry.get_provider", return_value=provider),
        patch("personalclaw.mcp_artifacts._resolve_session_key", return_value="dashboard:chat-1"),
    ):
        yield provider


def test_a_csv_artifact_keeps_a_cell_that_begins_like_a_formula_as_text(store):
    art = store.create(name="Ledger", kind="csv", content=LEDGER, actor="agent")

    assert store.get(art.slug).content == LEDGER_KEPT


def test_a_csv_artifact_whose_cells_need_no_quote_keeps_its_text_byte_for_byte(store):
    """Its quoting, a quoted line break and a missing last line break are kept as they were."""
    texts = [
        'Name,City\nAda,"Paris, France"\n',
        '"Name","Amount"\n"Grace","-1,234.56"\n',
        "Region,Change\nEMEA,-12.5%\nAPAC,-$45.20",
        'Name,Notes\nLinus,"first line\nsecond line"\n',
    ]

    for n, text in enumerate(texts):
        art = store.create(name=f"Plain {n}", kind="csv", content=text, actor="agent")
        assert store.get(art.slug).content == text


def test_an_edit_to_a_csv_artifact_is_kept_by_the_same_rule(store):
    art = store.create(
        name="Ledger", kind="csv", content="Item,Amount\nRefund,-20\n", actor="agent"
    )

    store.update(art.slug, content="Item,Amount\n@home,-20\n", snapshot=True, actor="agent")

    assert store.get(art.slug).content == "Item,Amount\n'@home,-20\n"


def test_a_version_kept_before_the_rule_is_restored_by_it(store, places):
    home, _ws = places
    art = store.create(name="Ledger", kind="csv", content="Item\nRefund\n", actor="agent")
    # The version as an earlier release kept it: the text exactly as it was sent.
    version = home / "artifacts" / art.slug / "versions" / "v1.html"
    version.write_text("Item\n=1+1\n", encoding="utf-8")

    store.revert(art.slug, 1, actor="user")

    assert store.get(art.slug).content == "Item\n'=1+1\n"


def test_a_byte_order_mark_that_opens_a_csv_artifact_stays_in_front(store):
    art = store.create(name="Ledger", kind="csv", content="\ufeff=1+1,Refund\n", actor="agent")

    assert store.get(art.slug).content == "\ufeff'=1+1,Refund\n"


def test_csv_text_the_store_cannot_read_is_refused_and_nothing_is_kept(store):
    with pytest.raises(ValueError, match="could not read the csv text"):
        store.create(name="Ledger", kind="csv", content=UNREADABLE, actor="agent")

    assert store.list() == []


def test_the_text_of_any_other_kind_is_kept_as_it_was_given(store):
    art = store.create(name="Sum", kind="markdown", content="=1+1", actor="agent")

    assert store.get(art.slug).content == "=1+1"


# ── the agent's artifact tools ───────────────────────────────────────────────


def _tool(name: str, args: dict) -> str:
    from personalclaw.mcp_artifacts import _call_tool

    return _call_tool(name, args)


def _kept_cells(store, slug: str) -> list[list[str]]:
    """The cells a spreadsheet reads in the text the store keeps for *slug*."""
    return _cells(store.get(slug).content.encode("utf-8"))


def test_a_csv_the_agent_saves_as_text_is_kept_by_the_rule(store):
    said = _tool("artifact_save", {"name": "Ledger", "kind": "csv", "content": LEDGER})

    assert "slug: ledger" in said, said
    assert _kept_cells(store, "ledger") == _cells(LEDGER_KEPT.encode("utf-8"))


def test_the_agents_edit_to_a_csv_it_made_is_kept_by_the_rule(store):
    """``sheet_create`` writes its file by the rule, and an ``artifact_update`` of that file's
    text is kept by the same rule: the edit cannot hand the spreadsheet a formula."""
    rows = json.dumps([["Item", "Amount"], ["Refund", -20]])
    made = _tool("sheet_create", {"name": "Ledger", "format": "csv", "rows": rows})
    assert made.startswith("Created csv: ledger"), made

    said = _tool("artifact_update", {"slug": "ledger", "content": "Item,Amount\n=1+1,-20\n"})

    assert "version 2" in said, said
    assert _kept_cells(store, "ledger") == [["Item", "Amount"], ["'=1+1", "-20"]]


def test_a_csv_the_agent_makes_is_offered_where_it_downloads(store):
    """The raw route serves a binary document's bytes, and a CSV is text, so the reply names
    where the file opens and downloads rather than a link that serves nothing."""
    rows = json.dumps([["Item", "Amount"], ["Refund", -20]])

    made = _tool("sheet_create", {"name": "Ledger", "format": "csv", "rows": rows})

    assert made.startswith("Created csv: ledger (v1, "), made
    assert made.endswith(
        "It opens in Artifacts at /#/artifacts/ledger, where Download saves it as a .csv file."
    ), made
    assert store.raw_bytes("ledger") is None
    # A binary document keeps the raw link, which serves its bytes.
    book = _tool("sheet_create", {"name": "Ledger book", "format": "xlsx", "rows": rows})
    assert book.endswith("Download at /api/artifacts/ledger-book/raw"), book
    assert store.raw_bytes("ledger-book") is not None


def test_csv_text_the_agent_hands_that_cannot_be_read_makes_nothing_and_says_why(store, places):
    _home, ws = places
    page = ws / "ledger.csv"
    page.write_text(UNREADABLE, encoding="utf-8")

    said = _tool("artifact_save", {"name": "Ledger", "kind": "csv", "content_file": str(page)})

    assert said == f"Error: {CANNOT_READ}"
    assert store.list() == []


# ── a workflow's publish and its artifact-update step ────────────────────────


@pytest.mark.asyncio
async def test_a_csv_a_workflow_publishes_is_kept_by_the_rule(store):
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch
    from personalclaw.workflows.models import Node

    node = Node.from_dict(
        {
            "kind": "transform",
            "id": "t",
            "config": {"expr": LEDGER, "publish": {"artifact": "Ledger", "kind": "csv"}},
        }
    )

    published = await dispatch(node, BindingContext())

    assert published.published["action"] == "create", published.published
    assert store.get(published.published["slug"]).content == LEDGER_KEPT


@pytest.mark.asyncio
async def test_a_workflow_steps_edit_to_a_csv_artifact_is_kept_by_the_rule(store):
    from personalclaw.action_providers.artifact_update_provider import (
        ArtifactUpdateActionProvider,
    )
    from personalclaw.action_providers.base import ActionContext

    store.create(name="Ledger", kind="csv", content="Item\nRefund\n", slug="ledger", actor="agent")

    result = await ArtifactUpdateActionProvider().execute(
        {"slug": "ledger", "content": "Item\n=1+1\n"},
        ActionContext(event="workflow_node", payload={}),
    )

    assert result.success, result.error
    assert store.get("ledger").content == "Item\n'=1+1\n"


# ── an app's request, and your own edit in the Artifacts editor ────────────────────────────


def _request(method: str, body: dict, *, slug: str = "", base: str = "") -> web.Request:
    app = web.Application()
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    headers = {"If-Match": f'"{base}"'} if base else {}
    req = make_mocked_request(
        method, "/api/artifacts", app=app, match_info={"slug": slug}, headers=headers
    )

    async def _json():
        return body

    req.json = _json  # type: ignore[method-assign]
    return req


async def _save(body: dict, *, as_app: str = "") -> tuple[int, dict]:
    from personalclaw.artifacts.handlers import api_artifacts_create

    with scoped_to_app(as_app):
        resp = await api_artifacts_create(_request("POST", body))
    return resp.status, json.loads(resp.body)


async def _edit(slug: str, body: dict, base: str) -> tuple[int, dict]:
    from personalclaw.artifacts.handlers import api_artifact_update

    resp = await api_artifact_update(_request("PATCH", body, slug=slug, base=base))
    return resp.status, json.loads(resp.body)


@pytest.mark.asyncio
async def test_a_csv_an_app_saves_is_kept_by_the_rule(store):
    saving = {"name": "Ledger", "kind": "csv", "content": LEDGER}

    status, made = await _save(saving, as_app="ledger-sync")

    assert status == 201, made
    assert made["content"] == LEDGER_KEPT
    assert store.get(made["slug"]).content == LEDGER_KEPT


@pytest.mark.asyncio
async def test_your_own_edit_in_the_artifacts_editor_is_kept_by_the_rule_too(store):
    """A CSV cannot hold a formula whoever writes its text, so your own edit in the Artifacts
    editor is kept by the rule as well: a formula belongs in an xlsx, where it can be declared."""
    art = store.create(name="Ledger", kind="csv", content="Item\nRefund\n", actor="agent")

    status, edited = await _edit(
        art.slug, {"content": "Item\n=1+1\n"}, revision_of("Item\nRefund\n")
    )

    assert status == 200, edited
    assert edited["content"] == "Item\n'=1+1\n"


@pytest.mark.asyncio
async def test_csv_text_a_request_hands_that_cannot_be_read_is_refused_with_its_reason(store):
    status, said = await _save({"name": "Ledger", "kind": "csv", "content": UNREADABLE})

    assert status == 400
    assert said == {"error": CANNOT_READ}
    assert store.list() == []
