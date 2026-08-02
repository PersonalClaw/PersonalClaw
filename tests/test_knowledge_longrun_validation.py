"""Long-run validation for the Knowledge Synthesis program (§8 success criteria).

These are the properties that only break AFTER a while: a watcher whose cost grows every cycle,
a seen-set that resets on restart, a persist that duplicates on the fiftieth retry. None of them
fail loudly — the run keeps working and just gets slower, more expensive, or quietly wrong. So
each test here simulates the passage of many cycles rather than checking one.

The plan's criteria this file covers:

* #2 — re-executing a persist is a provable no-op (no duplicate items, stable mention counts)
* #3 — a week of simulated cycles at bounded per-cycle cost, zero re-processed guids
* #5 — the seen-set survives a restart
* #6 — health and lint over a 100+-item store
* #8 — rich-ingest writes knowledge and tasks, and NOTHING to memory
"""

import asyncio
import json

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.knowledge import consolidation
from personalclaw.workflows.longrun import SeenSet, sibling_view


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def body(result):
    return json.loads(result.stdout)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def ctx():
    return ActionContext(event="workflow_node", payload={"node_id": "n-1"})


def _persist():
    from personalclaw.action_providers.knowledge_persist_provider import (
        KnowledgePersistActionProvider,
    )

    return KnowledgePersistActionProvider()


def _open(home):
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    return KnowledgeStore(db_path=str(knowledge_db_path()))


def _count(home) -> int:
    return list(_open(home).db.execute("SELECT COUNT(*) AS n FROM items"))[0]["n"]


# ── criterion 2: a re-executed persist is a provable no-op ──


def test_fifty_identical_persists_write_one_item(home, ctx):
    """The property that makes a nightly synthesis loop safe: a retried, resumed or rewound
    persist recomputes the same identity and writes nothing. Fifty rather than two, because a
    duplicate-on-Nth bug (a counter in the key, a timestamp in the hash) survives a two-call
    test."""
    persist = _persist()
    cfg = {"kind": "fact", "title": "Cold starts", "content": "4.2s on the M2 after boot."}
    first = body(run(persist.execute(cfg, ctx)))
    for _ in range(49):
        again = body(run(persist.execute(cfg, ctx)))
        assert again["item_id"] == first["item_id"]
        assert again["created"] is False
    assert _count(home) == 1


def test_mention_counts_stay_stable_across_retries(home, ctx):
    """A retry that re-appended its own mention would inflate `support_count` — and a claim that
    looks corroborated by fifty sources when one source retried fifty times is the most dangerous
    possible artifact, because confidence is computed from it."""
    persist = _persist()
    claims = [{"id": "c1", "statement": "Cold starts are slow", "confidence": 0.6}]
    cfg = {"kind": "fact", "title": "Cold starts", "content": "v1", "claims": claims}
    run(persist.execute(cfg, ctx))
    for _ in range(20):
        assert body(run(persist.execute(cfg, ctx)))["mentions_appended"] == 0

    store = _open(home)
    meta = json.loads(
        list(store.db.execute("SELECT file_metadata FROM items WHERE title='Cold starts'"))[0][
            "file_metadata"
        ]
    )
    assert meta["claims"][0]["support_count"] == 1


def test_alternating_content_does_not_fork_the_item(home, ctx):
    """A synthesis loop whose output oscillates between two phrasings must keep ONE item. Forking
    on every flip would produce a store where the same article exists twice, each citing the other
    as independent corroboration."""
    persist = _persist()
    for index in range(20):
        run(
            persist.execute(
                {
                    "kind": "fact",
                    "title": "Cold starts",
                    "content": "4.2 seconds" if index % 2 else "4.2s",
                },
                ctx,
            )
        )
    assert _count(home) == 1


# ── criterion 3: a week of cycles at bounded cost ──


def test_a_week_of_cycles_costs_the_same_per_cycle():
    """168 hourly cycles, each accumulating findings. Without the window, cycle 168 carries 168
    cycles of context and every cycle costs more than the last — superlinear in run length, with
    nothing indicating why until a context limit is hit hours in."""
    accumulated: list = []
    sizes: list[int] = []
    for cycle in range(168):
        accumulated.append(
            {
                "findings": [
                    {"guid": f"c{cycle}-{n}", "statement": f"finding {cycle}-{n}"} for n in range(5)
                ]
            }
        )
        sizes.append(len(sibling_view(accumulated)))
    assert max(sizes) <= 20
    assert sizes[-1] == sizes[20]  # flat from the moment the window fills


def test_a_week_of_cycles_reprocesses_nothing():
    """Zero re-processed guids over 168 cycles. A seen-set that missed even 1% would mean the
    synthesizer paying to re-read old items forever, which is invisible in the output."""
    seen = SeenSet()
    processed: list[str] = []
    for cycle in range(168):
        batch = [{"guid": f"c{cycle}-{n}"} for n in range(5)]
        # Every cycle also re-offers the PREVIOUS cycle's items, as a real feed does.
        if cycle:
            batch += [{"guid": f"c{cycle - 1}-{n}"} for n in range(5)]
        novel = seen.unseen(batch)
        processed.extend(i["guid"] for i in novel)
        seen.mark_all(novel)
    assert len(processed) == len(set(processed)) == 168 * 5


def test_the_seen_set_footprint_stays_bounded():
    """A watcher running for months must not grow its own state without limit."""
    seen = SeenSet(capacity=500)
    for n in range(50_000):
        seen.mark(f"g{n}")
    assert len(seen) == 500


# ── criterion 5: the seen-set survives a restart ──


def test_the_seen_set_survives_a_restart():
    """Held only in memory it would reset on every gateway restart — which is precisely when a
    months-long watcher is most likely to be interrupted, and a reset seen-set silently
    re-processes everything the run already paid for."""
    seen = SeenSet()
    batch = [{"guid": f"g{n}"} for n in range(30)]
    seen.mark_all(batch)

    # The journal round-trip, exactly as `_mark_seen` writes it and `_rehydrate_context` reads it.
    revived = SeenSet.from_dict(json.loads(json.dumps(seen.to_dict())))
    assert revived.unseen(batch) == []
    assert len(revived) == 30


def test_a_restart_mid_cycle_does_not_lose_unprocessed_items():
    """`unseen` deliberately does not mark. A cycle that dies mid-synthesis must not have
    suppressed the items it never actually processed — they would be lost for good."""
    seen = SeenSet()
    batch = [{"guid": "a"}, {"guid": "b"}]
    seen.unseen(batch)  # read, then "crash" before marking
    revived = SeenSet.from_dict(seen.to_dict())
    assert len(revived.unseen(batch)) == 2


# ── criterion 6: health and lint over a 100+-item store ──


def test_health_over_a_hundred_item_store(home, ctx):
    persist = _persist()
    for index in range(110):
        run(
            persist.execute(
                {
                    "kind": "fact",
                    "title": f"Measurement {index}",
                    "content": f"Run {index} measured {index / 10:.1f} seconds on the M2 host.",
                },
                ctx,
            )
        )
    assert _count(home) == 110

    from personalclaw.action_providers.knowledge_maintain_provider import (
        KnowledgeHealthActionProvider,
    )

    payload = body(run(KnowledgeHealthActionProvider().execute({}, ctx)))
    assert payload["item_count"] == 110
    # No FALSE findings on a store of well-formed items. A maintenance pass that reports 110
    # problems on a healthy library is one nobody reads a second time.
    assert payload["counts"]["stubs"] == 0
    assert payload["counts"]["unindexed"] == 0


def test_a_consolidation_pass_over_a_large_store_stays_capped(home, ctx):
    """Marginal cost has to be independent of store size, or the pass gets slower every week
    until it stops finishing."""
    items = [
        consolidation.Item(
            id=f"i{n}",
            title="Cold starts",
            content=f"Cold start latency measured about {4 + n % 3}.2 seconds on the M2 host",
            inbound_relations=1,
        )
        for n in range(400)
    ]
    plan = consolidation.plan_consolidation(items)
    assert len(plan.clusters) <= consolidation.MAX_CLUSTERS_PER_PASS
    assert sum(c.size for c in plan.clusters) <= consolidation.MAX_ITEMS_PER_PASS


def test_lint_does_not_fire_on_a_healthy_quiet_store():
    """Cadenced by WRITES, so a store nobody added to costs nothing. A wall-clock cadence would
    pay for a semantic pass over unchanged content forever."""
    assert not consolidation.lint_due(persists_since_last=0, every_n=12, health_clean=True)[0]


# ── criterion 8: rich-ingest writes knowledge and tasks, never memory ──


def test_rich_ingest_writes_no_memory():
    """Episodic and preference capture is the MEMORY subsystem's job. A template that wrote both
    would put user-modeling inside an ingest path nobody audits — so the boundary is asserted
    structurally rather than trusted."""
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.models import Node, walk

    spec = read_template("rich-ingest")
    root = spec.root if isinstance(spec.root, Node) else Node.from_dict(spec.root)
    providers = {
        str((node.config or {}).get("provider", "") or "")
        for _path, node in walk(root)
        if node.kind.value == "action"
    }
    assert providers == {"knowledge-persist", "create-task"}
    assert not any("memory" in p for p in providers)


def test_rich_ingest_persists_each_lens_under_its_own_kind():
    """One merged persist would collapse four typed vocabularies into a single item and lose
    exactly the typing the separate lenses exist to produce."""
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.models import Node, walk

    spec = read_template("rich-ingest")
    root = spec.root if isinstance(spec.root, Node) else Node.from_dict(spec.root)
    kinds = {
        str(((node.config or {}).get("with") or {}).get("kind", "") or "")
        for _path, node in walk(root)
        if node.kind.value == "action"
        and (node.config or {}).get("provider") == "knowledge-persist"
    }
    assert kinds == {"decision", "reference", "fact", "report"}


def test_rich_ingest_fences_the_transcript():
    """A transcript is untrusted input — someone in a meeting can read an injection out loud, and
    a lens prompt that interpolated it raw would treat that as an instruction."""
    from personalclaw.workflows.bundled_defs import read_template

    raw = json.dumps(read_template("rich-ingest").to_dict())
    assert raw.count("<untrusted_content") >= 5  # the classifier plus every lens


def test_every_lens_tolerates_its_own_failure():
    """One lens failing must not sink the pass: four of five vocabularies extracted is a good
    outcome, and losing all of them because the facts prompt tripped is not."""
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.models import Node, walk

    spec = read_template("rich-ingest")
    root = spec.root if isinstance(spec.root, Node) else Node.from_dict(spec.root)
    lenses = [n for _p, n in walk(root) if n.id.startswith("lens-")]
    assert lenses
    assert all((n.config or {}).get("allow_failure") for n in lenses)


# ── criterion 9: publish-article records its own approval ──


def test_publish_article_appends_a_decision_at_the_gate():
    """The journal records that a gate was approved; a `kind: decision` item records WHY. Without
    it a later run re-opens a settled question and the approval is invisible to it."""
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.models import Node, walk

    spec = read_template("publish-article")
    root = spec.root if isinstance(spec.root, Node) else Node.from_dict(spec.root)
    order = [n for _p, n in walk(root)]
    gate_at = next(i for i, n in enumerate(order) if n.kind.value == "gate")
    decision_at = next(
        i
        for i, n in enumerate(order)
        if ((n.config or {}).get("with") or {}).get("kind") == "decision"
    )
    assert gate_at < decision_at


def test_publish_article_reviews_from_two_independent_angles():
    """One reviewer asked for both accuracy and readability trades one against the other inside a
    single answer. Two lenses disagree visibly, which is the useful signal."""
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.models import Node, walk

    spec = read_template("publish-article")
    root = spec.root if isinstance(spec.root, Node) else Node.from_dict(spec.root)
    reviewers = [n.id for _p, n in walk(root) if n.id.startswith("review-")]
    assert len(reviewers) == 2


# ── criterion 4: retrieval reaches prompts fenced and budgeted ──


def test_every_slate_template_fences_what_it_retrieves():
    """A template that interpolates `{{nodes.x.output.items}}` raw into a prompt bypasses the
    platform's fencing doctrine — knowledge items partly derive from web and inbox content."""
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.models import Node, walk

    for name in ("knowledge-synthesis", "thesis-tracker", "publish-article"):
        spec = read_template(name)
        root = spec.root if isinstance(spec.root, Node) else Node.from_dict(spec.root)
        prompts = " ".join(str((n.config or {}).get("prompt", "") or "") for _p, n in walk(root))
        retrieve_ids = [
            n.id for _p, n in walk(root) if (n.config or {}).get("provider") == "knowledge-retrieve"
        ]
        for node_id in retrieve_ids:
            raw_ref = f"{{{{nodes.{node_id}.output.items}}}}"
            assert raw_ref not in prompts, f"{name}: {node_id} interpolated unfenced"
            assert f"nodes.{node_id}.output.items | fenced_sources" in prompts, name


def test_the_synthesis_template_spends_exactly_one_model_call():
    """The whole reason the retrieve and persist halves are ACTIONS. Doing either through a stage
    would triple the cost of the pattern the plan is built around."""
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.models import Node, walk

    spec = read_template("knowledge-synthesis")
    root = spec.root if isinstance(spec.root, Node) else Node.from_dict(spec.root)
    llm_nodes = [n for _p, n in walk(root) if n.kind.value in ("stage", "infer", "judge")]
    assert len(llm_nodes) == 1
