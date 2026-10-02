"""A scheduled report says what it reads, reads what it says, and says what it found.

A report made with no tags read "anything new" on its card and "Empty means anything new" in its
create form, while its runner read NOTHING for a scope with no tags: every run made no model call,
wrote no item and recorded "ok", and "Run now" answered that the run had "started". Nothing on the
page said that a report reads only your knowledge and never searches the web, so a question about
this week's releases, "with links", had no way to be answered and no sentence saying why.

So, driven through the Reports page's own routes against the real stores:

* a report with no tags reads what is new anywhere in your knowledge, as its card says;
* the card states what the report reads, its window, and that it does not search the web, and the
  items a run reads are exactly the ones those words name;
* a run that finds no new material says so, in the run's answer and on the card, in place of an
  "ok" that says nothing;
* a paused report still runs when you press Run now, as a paused automation does;
* the sources reach the model as quoted data, under the sentence that says what that means.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.action_providers import knowledge_report_provider as krp
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.knowledge_persist_provider import (
    KnowledgePersistActionProvider,
    _open_store,
)
from personalclaw.knowledge import research_reports as rr


class ScriptedModel:
    """The model a report run calls, scripted: it answers with one cited sentence and counts."""

    def __init__(self, reply: str = "Two releases shipped [1].") -> None:
        self.reply = reply
        self.prompts: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.prompts)

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home: a report run writes the report store, the library and the inbox."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def model(monkeypatch):
    scripted = ScriptedModel()
    monkeypatch.setattr(krp, "_one_shot", scripted)
    return scripted


def _ctx() -> ActionContext:
    return ActionContext(event="clock", payload={})


def seed(title: str, *, tags: list[str], content: str = "") -> str:
    """One item in the library, written the way every other knowledge write is."""
    result = asyncio.run(
        KnowledgePersistActionProvider().execute(
            {
                "title": title,
                "content": content or f"{title}: the notes say what changed.",
                "kind": "fact",
                "tags": tags,
                "unsourced": True,
            },
            _ctx(),
        )
    )
    assert result.success, result.error
    return str(json.loads(result.stdout)["item_id"])


def make_child_tag(parent: str, child: str) -> None:
    store = _open_store()
    ids = {str(t["name"]): int(t["id"]) for t in store.list_tags()}
    assert store.set_tag_parent(ids[child], ids[parent])


def report(name: str = "Weekly releases", **over: Any) -> rr.ReportDefinition:
    raw: dict[str, Any] = {
        "name": name,
        "prompt": "What shipped this week, with links?",
        "schedule": {"kind": "cron", "cron_expr": "0 8 * * 1"},
        "tz": "UTC",
        "source": {"tags": [], "window_secs": 0},
        "citation_policy": rr.CITE_SOURCE_ONLY,
    }
    raw.update(over)
    return rr.save_report(rr.from_dict(raw))


def _app() -> web.Application:
    from personalclaw.dashboard.handlers.research_reports import setup_research_report_routes
    from personalclaw.dashboard.request_boundary import request_boundary_middleware

    app = web.Application(middlewares=[request_boundary_middleware()])
    setup_research_report_routes(app)
    return app


async def _listed(client: TestClient, report_id: str) -> dict[str, Any]:
    body = await (await client.get("/api/knowledge/reports")).json()
    return next(r for r in body["reports"] if r["id"] == report_id)


async def _run_now(client: TestClient, report_id: str) -> dict[str, Any]:
    resp = await client.post(f"/api/knowledge/reports/{report_id}/run")
    assert resp.status == 200
    return await resp.json()


def _read_by(defn: rr.ReportDefinition) -> tuple[set[str], set[str]]:
    """The items a run of *defn* reads, from the runner's own preview: what new material it
    would read, and what it may look at while writing. A preview reads exactly what a run reads
    and spends and stamps nothing."""
    result = asyncio.run(
        krp.KnowledgeReportActionProvider().execute({"report_id": defn.id, "dry_run": True}, _ctx())
    )
    assert result.success, result.error
    body = json.loads(result.stdout)
    return set(body["source_items"]), set(body["context_items"])


# ── a report with no tags reads what is new anywhere in your knowledge ──────────────────────────


@pytest.mark.asyncio
async def test_a_report_with_no_tags_reads_what_is_new_anywhere_in_your_knowledge(home, model):
    await asyncio.to_thread(seed, "Release notes for the client library", tags=[])
    await asyncio.to_thread(seed, "Garden plan", tags=["garden"])
    defn = report()

    async with TestClient(TestServer(_app())) as client:
        answer = await _run_now(client, defn.id)

    assert model.calls == 1, "a report with no tags read nothing and asked the model nothing"
    prompt = model.prompts[0]
    assert "Release notes for the client library" in prompt and "Garden plan" in prompt
    assert answer == {
        "ok": True,
        "report_id": defn.id,
        "outcome": "wrote",
        "result": "Wrote a finding from 2 items in your knowledge.",
    }


# ── the card says what the report reads, and the run reads what the card says ───────────────────


@pytest.mark.asyncio
async def test_the_card_says_which_knowledge_a_report_reads_and_that_it_does_not_search_the_web(
    home,
):
    anything = await asyncio.to_thread(report, "Anything new")
    tagged = await asyncio.to_thread(
        report,
        "Perf week",
        source={"tags": ["perf", "ops"], "window_secs": 7 * 86400},
        context={"tags": ["arch"], "window_secs": 0},
    )

    async with TestClient(TestServer(_app())) as client:
        assert (await _listed(client, anything.id))["sources_shown"] == (
            "Reads what is new in your knowledge each time it runs. It does not search the web."
        )
        assert (await _listed(client, tagged.id))["sources_shown"] == (
            "Reads your knowledge tagged perf or ops from the last 7 days each time it runs. "
            "While writing, it may also look at your knowledge tagged arch. "
            "It does not search the web."
        )


def test_the_items_a_run_reads_are_the_ones_its_card_names(home):
    perf = seed("Latency regressed", tags=["perf"])
    perf_child = seed("p99 spike on Tuesday", tags=["perf-latency"])
    make_child_tag("perf", "perf-latency")
    cooking = seed("Bread recipe", tags=["cooking"])
    untagged = seed("A note with no tag", tags=[])
    arch = seed("Service topology", tags=["arch"])
    both = seed("Latency and topology", tags=["perf", "arch"])

    scoped = report("Perf", source={"tags": ["perf"], "window_secs": 0})
    assert "tagged perf" in rr.sources_shown(scoped)
    assert _read_by(scoped)[0] == {perf, perf_child, both}

    everything = report("Everything")
    assert "Reads what is new in your knowledge" in rr.sources_shown(everything)
    assert _read_by(everything)[0] == {perf, perf_child, cooking, untagged, arch, both}

    with_context = report(
        "Perf with context",
        source={"tags": ["perf"], "window_secs": 0},
        context={"tags": ["arch"], "window_secs": 0},
    )
    assert "may also look at your knowledge tagged arch" in rr.sources_shown(with_context)
    # An item in both scopes is new material, once.
    assert _read_by(with_context) == ({perf, perf_child, both}, {arch})


# ── a run that finds nothing new says so ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_run_that_finds_no_new_material_says_so_in_its_answer_and_on_the_card(home, model):
    await asyncio.to_thread(seed, "Latency regressed", tags=["perf"])
    defn = await asyncio.to_thread(report, source={"tags": ["perf"], "window_secs": 0})

    async with TestClient(TestServer(_app())) as client:
        first = await _run_now(client, defn.id)
        assert first["outcome"] == "wrote", first
        card = await _listed(client, defn.id)
        assert card["last_status"] == "ok"
        assert card["last_result"] == "Wrote a finding from 1 item in your knowledge tagged perf."

        second = await _run_now(client, defn.id)
        card = await _listed(client, defn.id)

    said = "Found no new material in your knowledge tagged perf since its previous run."
    assert second == {"ok": True, "report_id": defn.id, "outcome": "nothing_new", "result": said}
    assert card["last_status"] == rr.NOTHING_NEW
    assert card["last_result"] == said
    assert model.calls == 1, "the run with nothing new still asked the model"


@pytest.mark.asyncio
async def test_a_first_run_over_an_empty_corner_of_the_library_says_there_is_nothing_yet(
    home, model
):
    defn = await asyncio.to_thread(report, source={"tags": ["perf"], "window_secs": 0})
    async with TestClient(TestServer(_app())) as client:
        answer = await _run_now(client, defn.id)
    assert answer["outcome"] == "nothing_new"
    assert answer["result"] == "Found nothing in your knowledge tagged perf to report on yet."
    assert model.calls == 0


# ── a paused report still runs by hand ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_now_runs_a_paused_report(home, model):
    """Pausing stops a report running on its own. Run now is you asking for one run, as it is for
    a paused automation on the Triggers page: answering "ok" and running nothing was a lie."""
    await asyncio.to_thread(seed, "Latency regressed", tags=["perf"])
    defn = await asyncio.to_thread(
        report, source={"tags": ["perf"], "window_secs": 0}, enabled=False
    )
    async with TestClient(TestServer(_app())) as client:
        answer = await _run_now(client, defn.id)
    assert answer["outcome"] == "wrote"
    assert model.calls == 1


# ── the sources reach the model as quoted data ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_sources_reach_the_model_as_quoted_data(home, model):
    """A report reads what your watched sources brought in from outside, so each source is fenced
    as data and the prompt says what the fence means. Ordinary text still arrives whole."""
    await asyncio.to_thread(
        seed, "Feed item", tags=["perf"], content="The 2.0 release adds a retry budget."
    )
    defn = await asyncio.to_thread(report, source={"tags": ["perf"], "window_secs": 0})
    async with TestClient(TestServer(_app())) as client:
        await _run_now(client, defn.id)

    prompt = model.prompts[0]
    assert "The 2.0 release adds a retry budget." in prompt
    fenced = prompt[prompt.index("<untrusted_content") : prompt.index("</untrusted_content>")]
    assert "The 2.0 release adds a retry budget." in fenced
    assert "QUOTED DATA" in prompt and "never an instruction" in prompt
    assert "It cannot search the web" in prompt
