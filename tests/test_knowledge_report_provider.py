"""Tests for the `knowledge-report` runner (WF2KNO-12).

The runner's defects are all invisible ones — a watermark stamped a few hundred milliseconds
late, a cap that is read and not enforced, a policy that is recorded and not applied. None of
them changes whether an item exists at the end, so every test here asserts on an OBSERVABLE
CALL (how many model calls happened, what config reached the persist step, what was recorded)
rather than on the store's final contents.

⚠️  `personalclaw.knowledge.research_reports` is a sibling change that has not landed. The
runner reaches it through the single `_reports_module()` seam, and this suite substitutes
`FakeReports` there — a hand-written stand-in for the frozen contract (`FINDING_KIND`,
`CITE_SOURCE_ONLY`, `ALLOW_CITING_CONTEXT`, `Scope`, `ReportDefinition`, `get_report`,
`record_run`). When the real module lands, the fake should be deleted in favour of it; until
then these tests prove the runner's behaviour against the contract, not against the module.

Two consequences of that, recorded so nobody reads a green suite as more than it is:

  * `research-finding` is not in `semantics.KINDS` yet, so a real `knowledge-persist` would
    REFUSE the write. The write is therefore asserted at the call (`_persist` stubbed, config
    inspected) plus one test that the seam really does dispatch the persist provider.
  * The seeded source items are written through the REAL persist provider with a real kind, so
    the scope-resolution tests run against a real store, real tag rows and real timestamps.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

import pytest

from personalclaw.action_providers import knowledge_report_provider as krp
from personalclaw.action_providers.base import ActionContext, ActionResult
from personalclaw.action_providers.knowledge_persist_provider import (
    KnowledgePersistActionProvider,
    _open_store,
)

FINDING_KIND = "research-finding"
CITE_SOURCE_ONLY = "cite-source-only"
ALLOW_CITING_CONTEXT = "allow-citing-context"


# ── the sibling module's contract, faked ──


@dataclass
class Scope:
    tags: tuple[str, ...] = ()
    window_secs: int = 0


@dataclass
class ReportDefinition:
    id: str
    name: str
    prompt: str
    schedule: str
    tz: str = ""
    source: Scope = field(default_factory=Scope)
    context: Scope | None = None
    citation_policy: str = CITE_SOURCE_ONLY
    iteration_cap: int = 3
    enabled: bool = True
    created_ts: float = 0.0
    last_run_ts: float | None = None
    last_status: str = ""
    last_error: str = ""
    watermark_ts: float = 0.0


@dataclass
class RunRecord:
    report_id: str
    ok: bool
    error: str
    watermark_ts: float | None


class FakeReports:
    """Stand-in for `personalclaw.knowledge.research_reports` (see the module docstring)."""

    FINDING_KIND = FINDING_KIND
    CITE_SOURCE_ONLY = CITE_SOURCE_ONLY
    ALLOW_CITING_CONTEXT = ALLOW_CITING_CONTEXT
    Scope = Scope
    ReportDefinition = ReportDefinition

    def __init__(self) -> None:
        self.reports: dict[str, ReportDefinition] = {}
        self.runs: list[RunRecord] = []

    def add(self, defn: ReportDefinition) -> ReportDefinition:
        self.reports[defn.id] = defn
        return defn

    def get_report(self, report_id: str) -> ReportDefinition | None:
        return self.reports.get(report_id)

    #: The dueness answer this double hands back. Default DUE, so every test in this file keeps
    #: exercising the run path it was written for — the pre-flight is a gate on WHEN the runner
    #: is invoked, and this file is about what the run then does. The gate itself is driven in
    #: `test_research_report_scheduling.py`, against the real module.
    due: tuple[bool, str] = (True, "due")

    def is_due(self, defn: ReportDefinition, *, now: float) -> tuple[bool, str]:
        return self.due

    def record_run(
        self,
        report_id: str,
        *,
        ok: bool,
        error: str = "",
        watermark_ts: float | None = None,
    ) -> None:
        self.runs.append(RunRecord(report_id, ok, error, watermark_ts))


# ── stubs for the two expensive seams ──


class ModelStub:
    """Counts calls. The count IS the assertion for the cap and the empty-scope rule."""

    def __init__(self, reply: str = "a finding [1]", always_continue: bool = False) -> None:
        self.reply = reply
        self.always_continue = always_continue
        self.prompts: list[str] = []
        self.sleep = 0.0

    @property
    def calls(self) -> int:
        return len(self.prompts)

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.sleep:
            time.sleep(self.sleep)
        if self.always_continue:
            return f"{self.reply}\n{krp.CONTINUE_TOKEN}"
        return self.reply


class PersistStub:
    def __init__(self, ok: bool = True, error: str = "") -> None:
        self.ok = ok
        self.error = error
        self.configs: list[dict] = []
        self.at: float = 0.0

    @property
    def calls(self) -> int:
        return len(self.configs)

    async def __call__(self, action_config, ctx, *, timeout: int = 30) -> ActionResult:
        self.configs.append(action_config)
        self.at = time.time()
        return ActionResult(
            success=self.ok,
            stdout=json.dumps({"item_id": "itm-new"}),
            error=self.error,
        )


# ── fixtures ──


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home. Never the developer's own — this runner WRITES."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def reports(monkeypatch):
    fake = FakeReports()
    monkeypatch.setattr(krp, "_reports_module", lambda: fake)
    return fake


@pytest.fixture
def provider():
    return krp.KnowledgeReportActionProvider()


@pytest.fixture
def ctx():
    return ActionContext(event="clock", payload={"trigger_id": "t-1"})


@pytest.fixture
def model(monkeypatch):
    stub = ModelStub()
    monkeypatch.setattr(krp, "_one_shot", stub)
    return stub


@pytest.fixture
def persist(monkeypatch):
    stub = PersistStub()
    monkeypatch.setattr(krp, "_persist", stub)
    return stub


# ── helpers ──


def run(coro):
    return asyncio.run(coro)


def seed(ctx, *, title: str, tags, content: str = "some body text", kind: str = "fact") -> str:
    """Write a real knowledge item through the real persist provider."""
    result = run(
        KnowledgePersistActionProvider().execute(
            {
                "title": title,
                "content": content,
                "kind": kind,
                "tags": list(tags),
                "unsourced": True,
            },
            ctx,
        )
    )
    assert result.success, result.error
    return str(json.loads(result.stdout)["item_id"])


def retag_kind(item_id: str, kind: str) -> None:
    """Force an item's kind past `check_persist`.

    Needed only because `research-finding` is not in `semantics.KINDS` yet (sibling change) —
    the store column itself is untyped, so the runner's exclusion rule can still be measured.
    """
    store = _open_store()
    store.db.execute("UPDATE items SET kind = ? WHERE id = ?", (kind, item_id))


def make_child_tag(parent: str, child: str) -> None:
    store = _open_store()
    ids = {str(t["name"]): int(t["id"]) for t in store.list_tags()}
    assert parent in ids and child in ids, ids
    assert store.set_tag_parent(ids[child], ids[parent])


def body(result: ActionResult) -> dict:
    return json.loads(result.stdout or "{}")


def defn_for(reports, **kw) -> ReportDefinition:
    base = dict(
        id="rep-1",
        name="Weekly perf",
        prompt="Summarize what changed.",
        schedule="0 9 * * 1",
        source=Scope(tags=("perf",)),
    )
    base.update(kw)
    return reports.add(ReportDefinition(**base))


# ── bullet 1: the watermark is taken at scope-resolution time ──


def test_the_watermark_is_taken_at_scope_resolution_not_completion(
    home, reports, provider, ctx, model, persist
):
    """An item captured while the model was writing must not fall behind the new watermark.

    Stamping completion time would put the stamp AFTER the persist step; stamping resolution
    time puts it measurably before, so the assertion is an inequality with a real gap in it.
    """
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports)
    model.sleep = 0.15  # the model "writes" for 150ms — the window a late stamp would swallow

    before = time.time()
    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert result.success, result.error

    assert persist.calls == 1
    record = reports.runs[-1]
    assert record.ok is True
    assert record.watermark_ts is not None
    assert record.watermark_ts >= before
    # The stamp predates the persist call by roughly the model's writing time. A completion-time
    # stamp would be LATER than `persist.at`, not 0.1s earlier.
    assert persist.at - record.watermark_ts > 0.1, (persist.at, record.watermark_ts)


def test_the_reported_watermark_is_the_recorded_one(home, reports, provider, ctx, model, persist):
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports)
    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert body(result)["watermark_ts"] == reports.runs[-1].watermark_ts


# ── bullet 2: the source scope ──


def test_the_scope_spans_the_tag_subtree(home, reports, provider, ctx, model, persist):
    """A child tag counts: `tags.parent_id` is real parentage, and a report scoped to a parent
    that ignored its children would miss the items filed most specifically."""
    seed(ctx, title="Parent-tagged", tags=["perf"])
    child_id = seed(ctx, title="Child-tagged", tags=["perf-latency"])
    make_child_tag("perf", "perf-latency")
    defn_for(reports)

    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert result.success, result.error
    assert body(result)["source_items"] == 2

    dry = run(provider.execute({"report_id": "rep-1", "dry_run": True}, ctx))
    assert child_id in body(dry)["source_items"]


def test_an_untagged_sibling_tag_is_out_of_scope(home, reports, provider, ctx, model, persist):
    seed(ctx, title="In scope", tags=["perf"])
    seed(ctx, title="Elsewhere", tags=["cooking"])
    defn_for(reports)
    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert body(result)["source_items"] == 1


def test_items_at_or_before_the_watermark_are_out_of_scope(
    home, reports, provider, ctx, model, persist
):
    seed(ctx, title="Already reported", tags=["perf"])
    defn_for(reports, watermark_ts=time.time() + 60)
    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert result.success
    assert body(result)["source_items"] == 0
    assert model.calls == 0


def test_a_window_overrides_the_watermark(home, reports, provider, ctx, model, persist):
    """`window_secs` is a statement about what the report is ABOUT, not a resumption cursor."""
    seed(ctx, title="Fresh", tags=["perf"])
    defn_for(
        reports,
        source=Scope(tags=("perf",), window_secs=3600),
        watermark_ts=time.time() + 60,
    )
    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert body(result)["source_items"] == 1


def test_a_report_never_reads_its_own_findings(home, reports, provider, ctx, model, persist):
    """The infinite regress: a finding is a knowledge item newer than the watermark, so without
    the kind exclusion run two summarizes run one's summary, forever."""
    finding = seed(ctx, title="Last week's finding", tags=["perf"], kind="report")
    retag_kind(finding, FINDING_KIND)
    defn_for(reports)

    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert result.success, result.error
    assert body(result)["source_items"] == 0
    assert model.calls == 0, "the report fed on its own output"


# ── bullet 3: an empty scope is a terminal success ──


def test_an_empty_scope_never_calls_the_model(home, reports, provider, ctx, model, persist):
    defn_for(reports)
    result = run(provider.execute({"report_id": "rep-1"}, ctx))

    assert result.success, result.error
    assert model.calls == 0, "an empty scope spent a model call"
    assert persist.calls == 0
    note = body(result)["note"]
    assert "nothing new" in note


def test_an_empty_scope_still_advances_the_watermark(home, reports, provider, ctx, model, persist):
    defn_for(reports)
    before = time.time()
    run(provider.execute({"report_id": "rep-1"}, ctx))

    record = reports.runs[-1]
    assert record.ok is True
    assert record.watermark_ts is not None and record.watermark_ts >= before


# ── bullet 4: the loop is bounded by iteration_cap ──


@pytest.mark.parametrize("cap", [1, 2, 5])
def test_the_loop_stops_at_the_iteration_cap(
    home, reports, provider, ctx, persist, monkeypatch, cap
):
    """Counted, not read: a cap that is loaded into a variable and never used to bound the loop
    looks identical at the call site."""
    stub = ModelStub(always_continue=True)
    monkeypatch.setattr(krp, "_one_shot", stub)
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports, iteration_cap=cap)

    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert result.success, result.error
    assert stub.calls == cap, f"cap {cap} allowed {stub.calls} model calls"
    assert body(result)["model_calls"] == cap


def test_a_nonsense_cap_still_makes_one_call(home, reports, provider, ctx, persist, monkeypatch):
    stub = ModelStub(always_continue=True)
    monkeypatch.setattr(krp, "_one_shot", stub)
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports, iteration_cap=0)
    run(provider.execute({"report_id": "rep-1"}, ctx))
    assert stub.calls == 1


def test_the_loop_stops_early_when_the_model_is_done(
    home, reports, provider, ctx, persist, monkeypatch
):
    stub = ModelStub(reply="done [1]", always_continue=False)
    monkeypatch.setattr(krp, "_one_shot", stub)
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports, iteration_cap=5)
    run(provider.execute({"report_id": "rep-1"}, ctx))
    assert stub.calls == 1
    assert persist.configs[0]["content"] == "done [1]"


def test_the_continue_sentinel_never_reaches_the_stored_finding(
    home, reports, provider, ctx, persist, monkeypatch
):
    stub = ModelStub(reply="body [1]", always_continue=True)
    monkeypatch.setattr(krp, "_one_shot", stub)
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports, iteration_cap=2)
    run(provider.execute({"report_id": "rep-1"}, ctx))
    assert krp.CONTINUE_TOKEN not in persist.configs[0]["content"]


def test_the_prompt_forbids_inventing_a_citation_marker(
    home, reports, provider, ctx, model, persist
):
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports)
    run(provider.execute({"report_id": "rep-1"}, ctx))

    prompt = model.prompts[0]
    assert "Do NOT invent a citation marker" in prompt
    assert "[1]" in prompt


# ── bullet 5: the citation policy decides which refs are registered ──


def _context_report(reports, policy: str) -> ReportDefinition:
    return defn_for(
        reports,
        source=Scope(tags=("perf",)),
        context=Scope(tags=("arch",)),
        citation_policy=policy,
    )


def test_cite_source_only_registers_only_the_source_items(
    home, reports, provider, ctx, model, persist
):
    source_id = seed(ctx, title="Latency regressed", tags=["perf"])
    context_id = seed(ctx, title="Service topology", tags=["arch"])
    _context_report(reports, CITE_SOURCE_ONLY)

    run(provider.execute({"report_id": "rep-1"}, ctx))
    registered = [s["item_id"] for s in persist.configs[0]["citation_sources"]]
    assert registered == [source_id]
    assert context_id not in registered


def test_allow_citing_context_registers_context_after_source(
    home, reports, provider, ctx, model, persist
):
    """ONE numbering, source first — numbering the two scopes separately would mint two `[1]`s
    resolving to different items."""
    source_id = seed(ctx, title="Latency regressed", tags=["perf"])
    context_id = seed(ctx, title="Service topology", tags=["arch"])
    _context_report(reports, ALLOW_CITING_CONTEXT)

    run(provider.execute({"report_id": "rep-1"}, ctx))
    refs = persist.configs[0]["citation_sources"]
    assert [r["item_id"] for r in refs] == [source_id, context_id]
    assert [r["marker"] for r in refs] == [1, 2]


@pytest.mark.parametrize(
    "policy,resolves",
    [(ALLOW_CITING_CONTEXT, True), (CITE_SOURCE_ONLY, False)],
)
def test_a_context_marker_resolves_only_under_allow_citing_context(
    home, reports, provider, ctx, persist, monkeypatch, policy, resolves
):
    """The policy has to BITE, not merely be recorded: run the registered refs through the real
    citation resolver and check whether `[2]` — the context-only item — is attributable."""
    from personalclaw.knowledge import citations

    seed(ctx, title="Latency regressed", tags=["perf"])
    context_id = seed(ctx, title="Service topology", tags=["arch"])
    _context_report(reports, policy)
    stub = ModelStub(reply="the topology explains it [2]")
    monkeypatch.setattr(krp, "_one_shot", stub)

    run(provider.execute({"report_id": "rep-1"}, ctx))
    refs = tuple(
        citations.SourceRef(
            marker=int(r["marker"]),
            item_id=str(r["item_id"]),
            chunk_index=int(r["chunk_index"]),
            excerpt=str(r["excerpt"]),
        )
        for r in persist.configs[0]["citation_sources"]
    )
    resolution = citations.resolve("the topology explains it [2]", refs)
    cited_ids = [c.item_id for c in resolution.citations]
    assert (context_id in cited_ids) is resolves
    if not resolves:
        # Not merely unregistered: the marker resolves to nothing, so the write drops it rather
        # than storing a dangling promise of provenance.
        assert "[2]" not in resolution.text


# ── bullet 6: one finding per run, on the one write path ──


def test_the_finding_is_written_with_the_finding_kind(home, reports, provider, ctx, model, persist):
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports)
    run(provider.execute({"report_id": "rep-1"}, ctx))

    assert persist.calls == 1, "one finding per run"
    cfg = persist.configs[0]
    assert cfg["kind"] == FINDING_KIND
    assert cfg["title"] == "Weekly perf"
    assert cfg["content"] == "a finding [1]"
    assert cfg["source_ref"] == "research-report:rep-1"
    assert cfg["tags"] == ["perf"]


def test_the_write_goes_through_the_persist_provider(home, reports, provider, ctx, model):
    """The seam itself, unstubbed: `_persist` must dispatch `knowledge-persist`, not the store.

    Asserted against the provider CLASS rather than the stored row because `research-finding`
    is not in `semantics.KINDS` yet — a real end-to-end write is refused until the sibling
    change lands, and this is the part that does not depend on it.
    """
    seen: list[dict] = []

    async def fake_execute(self, action_config, ctx, timeout=30):
        seen.append(action_config)
        return ActionResult(success=True, stdout=json.dumps({"item_id": "itm-1"}))

    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(KnowledgePersistActionProvider, "execute", fake_execute)
        result = run(provider.execute({"report_id": "rep-1"}, ctx))

    assert result.success, result.error
    assert len(seen) == 1 and seen[0]["kind"] == FINDING_KIND


# ── bullet 7: failure records the error without advancing the run stamp ──


def test_a_refused_persist_records_a_failed_run_and_no_stamp(
    home, reports, provider, ctx, model, monkeypatch
):
    stub = PersistStub(ok=False, error="unknown kind 'research-finding'")
    monkeypatch.setattr(krp, "_persist", stub)
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports)

    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert result.success is False
    assert "research-finding" in result.error
    record = reports.runs[-1]
    assert record.ok is False
    assert "unknown kind" in record.error
    assert record.watermark_ts is None, "a failed run advanced the watermark"


def test_an_exception_records_a_failed_run(home, reports, provider, ctx, monkeypatch, persist):
    async def boom(prompt):
        raise RuntimeError("provider chain exhausted")

    monkeypatch.setattr(krp, "_one_shot", boom)
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports)

    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert result.success is False
    assert "provider chain exhausted" in result.error
    record = reports.runs[-1]
    assert record.ok is False and record.watermark_ts is None
    assert persist.calls == 0


def test_an_empty_finding_is_a_failed_run(home, reports, provider, ctx, monkeypatch, persist):
    monkeypatch.setattr(krp, "_one_shot", ModelStub(reply="   "))
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports)

    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert result.success is False
    assert reports.runs[-1].ok is False
    assert reports.runs[-1].watermark_ts is None
    assert persist.calls == 0


def test_an_unreadable_store_records_a_failed_run(home, reports, provider, ctx, monkeypatch):
    def boom():
        raise OSError("database is locked")

    monkeypatch.setattr(krp, "_open_store", boom)
    defn_for(reports)
    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert result.success is False
    assert reports.runs[-1].ok is False and reports.runs[-1].watermark_ts is None


# ── config surface & registration ──


def test_a_missing_report_id_is_an_error(home, reports, provider, ctx, model, persist):
    result = run(provider.execute({}, ctx))
    assert result.success is False
    assert "report_id" in result.error
    assert reports.runs == []


def test_an_unknown_report_records_nothing(home, reports, provider, ctx, model, persist):
    result = run(provider.execute({"report_id": "nope"}, ctx))
    assert result.success is False
    assert "nope" in result.error
    assert reports.runs == [], "there is no report row to stamp"


def test_a_disabled_report_does_nothing(home, reports, provider, ctx, model, persist):
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports, enabled=False)
    result = run(provider.execute({"report_id": "rep-1"}, ctx))
    assert result.success is True
    assert body(result)["skipped"] == "disabled"
    assert model.calls == 0 and persist.calls == 0 and reports.runs == []


def test_a_dry_run_spends_nothing_and_stamps_nothing(home, reports, provider, ctx, model, persist):
    seed(ctx, title="Latency regressed", tags=["perf"])
    defn_for(reports)
    result = run(provider.execute({"report_id": "rep-1", "dry_run": True}, ctx))

    assert result.success is True
    assert body(result)["dry_run"] is True
    assert len(body(result)["source_items"]) == 1
    assert model.calls == 0 and persist.calls == 0
    assert reports.runs == [], "a preview advanced the watermark"


def test_the_provider_is_registered_and_declares_itself(home):
    """`home` is not decoration: registering the defaults constructs every provider, and some
    constructor resolves a store — without the isolated home this test writes to the real one
    (measured: `memory.db-wal` appeared under `~/.personalclaw` and the real-home rail red)."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )

    _ensure_default_providers_registered()
    found = get_action_provider("knowledge-report")
    assert found is not None
    assert found.display_name
    assert found.supports_dry_run is True
