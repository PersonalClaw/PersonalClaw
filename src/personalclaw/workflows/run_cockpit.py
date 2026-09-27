"""The run cockpit's reads — projections over what a run already wrote, never a second store.

`introspect` (the nine questions), `ledger_rails` (findings and verdicts), `run_deliverable`,
`template_trajectory` and `touched_items` each answer a question the cockpit asks about one run,
or about its template's recent runs, by reading the run's ledger and outbox. `introspection.py`
holds the arithmetic; this module holds the reads. Each returns the service-result dict every
workflow operation returns (`service._ok` / `service._service_failure`), so the HTTP handlers
and the chat tools serialize the same answer.
"""

from __future__ import annotations

import time
from typing import Any

from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store
from personalclaw.workflows.service import _nodes_of, _ok, _service_failure


def introspect(run_id: str) -> dict[str, Any]:
    """The nine-question introspection projection for one run (WORK-CONTAINERS §6.4, R6).

    Everything here is a PROJECTION over the journal this run already wrote —
    `introspection.py` holds the arithmetic and this function holds the reads. No metrics
    store, per the plan's own words: "pass-rate, failure distribution and latency
    percentiles are queries over this".

    The template card aggregates ACROSS runs of the same template, which is why this reads
    the sibling runs' ledgers too: "what is costing money" is a question about the template,
    not about the one run in front of you, and a p95 computed from a single run would just
    restate the run. The sibling read is bounded by `_TEMPLATE_CARD_RUNS` because a personal
    instance accumulates runs forever and the surface that answers "what does this usually
    cost" must not get slower every week.

    `checklist_gaps` runs LAST, over the payload actually assembled, so the response says
    which of the nine questions its own body cannot answer. That is what makes the checklist
    a contract rather than a comment: a surface rendering eight of nine has a named hole, and
    the name arrives with the data instead of in a review.
    """
    from personalclaw.workflows import filedrop, introspection

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")

    events = journal_mod.ledger(run_id)
    now = time.time()
    stats = introspection.run_stats(
        run_id, events, elapsed_secs=introspection.run_elapsed(run, now)
    )
    gates = introspection.gate_stats(events)

    # Evidence for the Proof section is the run's OWN published outbox, not a directory scan:
    # a file the run dropped but never published is a byproduct, and counting it as evidence
    # would let a run prove itself with its own scratch output.
    evidence = [str(entry.get("slug") or "") for entry in filedrop.outbox_entries(run_id)]
    proof = introspection.proof_section(stats, evidence_files=[e for e in evidence if e])

    # The template card, across this template's recent runs. The current run is included —
    # excluding it would make the card disagree with the strip directly above it.
    card = introspection.TemplateCard(template=run.workflow_name)
    edges = introspection.EdgeStats()
    if run.workflow_name:
        siblings, _total = store.list_runs(
            workflow_name=run.workflow_name, limit=_TEMPLATE_CARD_RUNS
        )
        # Read each sibling's ledger ONCE and reuse it for every cross-run projection: the run
        # economics, the said-no badge and the edge distribution all ask the same events, and a
        # personal instance's card must not get slower by reading them three times.
        sibling_ledgers = [
            (r.id, events if r.id == run_id else journal_mod.ledger(r.id)) for r in siblings
        ]
        elapsed = {r.id: introspection.run_elapsed(r, now) for r in siblings}
        sibling_stats = [
            stats if rid == run_id else introspection.run_stats(rid, evs, elapsed_secs=elapsed[rid])
            for rid, evs in sibling_ledgers
        ]
        # Per-branch case and per-judge verdict distributions across the template (PP-8). Sample-
        # gated exactly like the said-no badge below: "always case A" and "case B never taken" are
        # claims about the selector's HISTORY, and one run can never carry the sample for either.
        edges = introspection.edge_stats([evs for _rid, evs in sibling_ledgers])
        # Gate warnings are the template's, not the run's: "this gate has never rejected" is a
        # claim about the gate's history, and one run can never carry the sample for it. The edge
        # findings (dead cases, degenerate selectors) ride the same list — they are the same shape
        # of claim over the same sample, and a reader should meet them in one place.
        warnings = sorted(
            {
                w
                for g in _template_gates(sibling_ledgers, run_id, gates).values()
                if (w := g.fake_check_warning())
            }
            | set(edges.warnings())
        )
        card = introspection.template_card(run.workflow_name, sibling_stats, warnings=warnings)

    # The trajectory signature (PP-7): this run's decision PATH as a pure ledger projection, plus
    # the template-level regression signal — have this template's recent runs shifted to a path that
    # fails more often? Both are projections over ledgers already on disk; no new store.
    signature = introspection.trajectory_signature(run_id, events)
    trajectory_regression = None
    trajectory_distribution: dict[str, int] = {}
    if run.workflow_name:
        # Oldest-first: the shift detector reasons about "the path it USED to take" vs the recent
        # one, so the history it reads must run forward in time.
        history: list[tuple[str, bool]] = []
        for r in sorted(siblings, key=lambda s: getattr(s, "created_at", "") or ""):
            sib_events = events if r.id == run_id else journal_mod.ledger(r.id)
            sib_sig = introspection.trajectory_signature(r.id, sib_events).signature
            sib_failed = (
                stats
                if r.id == run_id
                else introspection.run_stats(r.id, sib_events, elapsed_secs=elapsed[r.id])
            ).steps_failed > 0
            trajectory_distribution[sib_sig] = trajectory_distribution.get(sib_sig, 0) + 1
            history.append((sib_sig, sib_failed))
        trajectory_regression = introspection.trajectory_regression(run.workflow_name, history)

    from personalclaw.workflows.human_input import list_continuations

    nodes = _nodes_of(run_id)
    # The open asks, as the wire rows the inbox already renders. Read from the continuation
    # directory rather than inferred from node state: a WAITING node is not necessarily
    # answerable (a `wait` deadline is nobody's decision), and offering an answer box for a
    # timer would teach the user the surface guesses.
    open_asks = [
        {"resume_token": c.token, "node_id": c.node_id, "ask": journal_mod.redact(c.ask or {})}
        for c in list_continuations(run_id)
        if not c.expired
    ]
    answers: dict[str, Any] = {
        # "what is running now, and why" — the live nodes plus the template that asked for them.
        "running": {
            "status": run.status.value,
            "workflow": run.workflow_name,
            "nodes": [n for n in nodes if n.get("state") in ("running", "ready", "waiting")],
        },
        # "what changed" — the journal timeline, which is also the attempt ledger's source.
        "changed": introspection_timeline(events),
        # "what is blocked" — a waiting node is blocked on something external by definition.
        "blocked": [n for n in nodes if n.get("state") == "waiting"],
        # "what needs my approval" — the open continuations, i.e. the answerable gates.
        "approval": open_asks,
        "failed": [n for n in nodes if n.get("state") in ("failed", "scope_violation")],
        "cost": stats.to_dict(),
        # "what is risky" — the degraded nodes, every said-no warning the gates earned, and the
        # edge findings (a dead case or a selector doing no work is a risk the same way a fake check
        # is: the plan declares a decision the run never actually makes).
        "risky": {
            "degraded": [n for n in nodes if n.get("state") == "degraded"],
            "gates": [g.to_dict() for g in gates.values()],
            "edges": edges.to_dict(),
            "verification_debt": stats.verification_debt,
            # A template drifting onto a path that fails more often is a risk the per-run numbers
            # cannot show — it is only visible across the template's history.
            "trajectory_regression": (
                trajectory_regression.to_dict() if trajectory_regression else None
            ),
        },
        # "what happens next if I say nothing" — a WAITING run does nothing until answered; a
        # terminal run is done. Stated rather than implied: the question the plan promotes to a
        # criterion is exactly the one every other surface leaves to inference.
        "next": _next_if_silent(run, nodes, open_asks),
        "proof": proof.to_dict(),
    }
    return _ok(
        run_id=run_id,
        workflow=run.workflow_name,
        stats=stats.to_dict(),
        gates={node_id: g.to_dict() for node_id, g in gates.items()},
        # Per-branch case and per-judge verdict distributions across the template (PP-8), beside the
        # said-no gate table rather than on a surface of their own — a routing decision and a gate
        # decision are the same kind of edge, and a reader should meet them in one place.
        edges=edges.to_dict(),
        template_card=card.to_dict(),
        # This run's decision PATH plus the template's signature-class distribution and the
        # regression signal — the "which runs of this template went a different way" query (PP-7).
        trajectory=signature.to_dict()
        | {
            "distribution": trajectory_distribution,
            "regression": trajectory_regression.to_dict() if trajectory_regression else None,
        },
        proof=proof.to_dict(),
        timeline=answers["changed"],
        # The live touched-items feed (§6.5): what this run published and what was handed to it.
        # Rides this payload rather than a route of its own — it answers "what changed" for
        # THINGS, where the timeline answers it for STEPS, and a reader needs both together.
        touched=touched_items(run_id),
        answers=answers,
        # Empty is the healthy answer. A non-empty list names a question this payload cannot
        # answer, which is a backend gap — the FE cannot close it by rendering harder.
        checklist_gaps=introspection.checklist_gaps(answers),
    )


def ledger_rails(run_id: str) -> dict[str, Any]:
    """The two ledger rails for one run (PP-16 seam 4, the ledger-rails third).

    The run-side answer to the loop cockpit's findings rail and verdict/ROI rail. Both are pure
    PROJECTIONS over the ledger this run already wrote — `introspection.py` holds the arithmetic,
    this holds the read — so nothing new is stored and no kind is minted. It is the same read the
    loop side does through `loop/store.py::get_redacted`, which attaches `findings`
    (`files.get_findings`, over `step_completed`) and `verdicts` (`files.get_verdicts`, over
    `judge_verdict`) to the loop's own detail payload.

    ONE ledger read for both rails and their totals, and the totals are computed from the projected
    ROWS: a second read with a second filter is how a count and the rows beneath it drift apart.

    Per-run and cheap, which is why it is not folded into `introspect`. That payload reads this
    template's SIBLING runs to earn its p50/p95 card and its said-no sample, so it is bounded by
    `_TEMPLATE_CARD_RUNS` and gets slower as a template accumulates history. These two rails are a
    single run's own history, so the cockpit can paint them on connect.

    Redacted through `journal_mod.redact` — the SAME recursive redactor the journal writer uses,
    reused rather than re-derived so the two cannot drift. The findings rail carries `model`,
    `degraded_reason` and `output_ref`, and a degraded reason is exactly where a credential
    surfaces in a screenshot.
    """
    from personalclaw.workflows import introspection

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")

    events = journal_mod.ledger(run_id)
    # Redact BEFORE aggregating, not after. `rail_totals` lifts a verdict's own word into a
    # `verdicts_by_word` KEY, so totals computed from raw rows would carry any free text that word
    # held straight past the row-level redaction — measured by mutation, not reasoned about. This
    # order also strengthens the totals-agree-with-rows property: both now derive from the
    # identical redacted list.
    findings = [journal_mod.redact(row) for row in introspection.findings_rail(events)]
    verdicts = [journal_mod.redact(row) for row in introspection.verdict_rail(events)]
    totals = introspection.rail_totals(findings, verdicts)
    return _ok(
        run_id=run_id,
        workflow=run.workflow_name,
        findings=findings,
        verdicts=verdicts,
        totals=totals.to_dict(),
        # Which rail kinds have a producer at all, and how many events each holds. `events: null`
        # names a kind nothing on this side writes — an absent cell, not a zero.
        coverage=introspection.rail_coverage(events),
    )


def run_deliverable(run_id: str) -> dict[str, Any]:
    """The run's DOCUMENT deliverable and working log (PP-16 unit 1).

    The run-side answer to `GET /api/loops/{id}/report`, which serves `store.read_deliverable` +
    `store.read_log` off one route. Same two slots, the same kind-declared filenames and the same
    redaction — a READ over files the run already has, so nothing is stored and no kind is minted.

    **The filename is DERIVED, not configured here.** `deliverable.resolve_name` walks the loop
    alias table forward and asks each kind's own `deliverable_name`, so `goal-pursuit-open-ended`
    resolves to `REPORT.md`, `goal-pursuit-monitor` to `MONITOR_LOG.md` and `design-project` to
    `DESIGN.md` because those kinds say so — not because this module repeats them.

    **Absence is named, five ways** (see `workflows/deliverable.py`), because a blank panel cannot
    tell a user whether the worker has not written yet, whether this kind produces a check rather
    than a document, or whether the template never asked for one. `instructed` is that question,
    measured per run against the run's OWN spec: today no bundled template names its kind's
    document, so an absent REPORT.md is a template gap rather than a slow worker, and the surface
    says which.

    **No money field, deliberately** — issue #2566: `run_totals` reports `cost_usd 0.0` for a loop
    because `LoopJournal.cycle` writes no money keys, and PP-16 sends loop-backed runs through every
    run-side surface. A cost here would read `$0.00` for work that cost real money, on the one page
    a user opens to find out what the document cost.

    404s for an unknown run, so a polled deleted run is distinguishable from one whose worker has
    not written yet — the same rule `api_loop_report` adopted after the same bug.
    """
    from personalclaw.loop import store as loop_store
    from personalclaw.workflows import deliverable as deliverable_mod

    run = store.get(run_id)
    if run is None:
        return _service_failure("WF_RUN_NOT_FOUND", f"no run {run_id!r}")

    workflow = str(getattr(run, "workflow_name", "") or "")
    resolved = deliverable_mod.resolve_name(workflow)
    roots = deliverable_mod.run_roots(run)
    report = deliverable_mod.read_document(roots, resolved.name, reason=resolved.reason)
    # The log's name is the loop store's own declaration, imported rather than re-spelled: one
    # on-disk convention, one string. Unconditional — every kind's worker keeps a working log, which
    # is why the loop side's `read_log` takes no kind at all.
    log = deliverable_mod.read_document(roots, loop_store.LOG_NAME)
    return _ok(
        run_id=run_id,
        workflow=workflow,
        report=report.to_dict(),
        log=log.to_dict(),
        # How the name was decided, so a reader can tell a derived name from a guessed one.
        derivation=resolved.to_dict(),
        # Where we looked, in order. A user staring at "not written" needs to know whether we looked
        # in the workspace their worker actually used.
        roots=roots.to_dict(),
        # Whether this run's OWN spec ever names the document. `false` reframes the absence from
        # "not yet" to "never asked for" — see the docstring.
        instructed=deliverable_mod.instructed_by_spec(store.read_spec(run_id), resolved.name),
    )


def template_trajectory(name: str) -> dict[str, Any]:
    """The trajectory-signature distribution and regression signal for one template (PP-7).

    Queryable WITHOUT a run in hand: given a template name, this reads its recent runs' ledgers,
    projects each to its trajectory signature, and reports the distribution of signature classes,
    each run's class, and the sample-gated regression signal. A pure projection over ledgers already
    on disk — no store, no model call. Bounded by `_TEMPLATE_CARD_RUNS` for the same reason the
    introspection card is: a personal instance accumulates runs forever.
    """
    from personalclaw.workflows import introspection

    runs, _total = store.list_runs(workflow_name=name, limit=_TEMPLATE_CARD_RUNS)
    history: list[tuple[str, bool]] = []
    distribution: dict[str, int] = {}
    signatures: list[dict[str, Any]] = []
    # Oldest-first, so the regression detector reads the template's history forward in time.
    for run in sorted(runs, key=lambda r: getattr(r, "created_at", "") or ""):
        run_id = getattr(run, "id", "")
        if not run_id:
            continue
        events = journal_mod.ledger(run_id)
        sig = introspection.trajectory_signature(run_id, events).signature
        elapsed = introspection.run_elapsed(run, time.time())
        failed = introspection.run_stats(run_id, events, elapsed_secs=elapsed).steps_failed > 0
        distribution[sig] = distribution.get(sig, 0) + 1
        history.append((sig, failed))
        signatures.append({"run_id": run_id, "signature": sig, "failed": failed})
    regression = introspection.trajectory_regression(name, history)
    return _ok(
        template=name,
        runs=len(history),
        distribution=distribution,
        signatures=signatures,
        regression=regression.to_dict() if regression else None,
    )


def touched_items(run_id: str) -> list[dict[str, Any]]:
    """What this run TOUCHED, newest-first — the live touched-items feed (§6.5 / R13).

    Unions the two run-attributed mutation records that exist today:

    * ``publishes.jsonl`` — every artifact this run published, versioned or converged (§2.5).
    * the file-drop manifest — every file handed INTO the run.

    Both are already run-scoped, which is the whole reason the feed is buildable: attribution is
    the hard part, not the union. A feed assembled by scanning the artifact registry for things
    that changed recently would attribute another run's work to this one the moment two runs
    overlapped.

    **The knowledge half is absent, not omitted.** Knowledge mutations carry no run attribution
    (S47's lineage covered artifacts only), so a knowledge row here would have to be guessed from
    timing — and a feed that says "this run wrote that memory" on a coincidence is worse than a
    feed that does not mention memory. See the plan's §6.5 note; closing it is a journal-format
    change, not a rendering one.
    """
    from personalclaw.workflows import filedrop

    rows: list[dict[str, Any]] = []
    for entry in filedrop.outbox_entries(run_id):
        rows.append(
            {
                "kind": "artifact",
                "ref": str(entry.get("slug") or ""),
                "label": str(entry.get("artifact") or entry.get("slug") or ""),
                # `version` / `noop` / `create` — the verb matters: a converged republish is not
                # the same event as a new version, and collapsing them would make an unchanged
                # artifact look freshly written.
                "action": str(entry.get("action") or ""),
                "detail": str(entry.get("change_note") or ""),
                "node_id": str(entry.get("node_id") or ""),
                "ts": str(entry.get("updated_at") or ""),
            }
        )
    for entry in filedrop.read_manifest(run_id):
        rows.append(
            {
                "kind": "file",
                "ref": str(entry.get("filename") or ""),
                "label": str(entry.get("filename") or ""),
                "action": "dropped",
                "detail": str(entry.get("mime") or ""),
                "node_id": "",
                "ts": str(entry.get("accepted_at") or ""),
            }
        )
    # Newest-first: a feed is read from the top, and the most recent touch is the one a watching
    # user is waiting for. Empty timestamps sort last rather than crashing the comparison.
    rows.sort(key=lambda r: r["ts"] or "", reverse=True)
    return rows


#: How many of a template's recent runs the card aggregates. Bounded because a personal
#: instance accumulates runs indefinitely and the surface answering "what does this usually
#: cost" must not get slower every week. Newest-first, so the bound drops the oldest history
#: rather than the runs a user is actually asking about.
_TEMPLATE_CARD_RUNS = 50


def _template_gates(
    sibling_ledgers: list[tuple[str, list[dict[str, Any]]]],
    run_id: str,
    own: dict[str, Any],
) -> dict[str, Any]:
    """Per-gate stats ACROSS the template's runs, over ledgers the caller already read.

    The fake-check badge needs a SAMPLE: `FAKE_CHECK_MIN_RUNS` gate resolutions is a claim
    about the gate's history, and computing it from one run would leave the badge permanently
    unarmed — the exact "declared but can never fire" shape this atom exists to close. Takes the
    pre-read ledgers so the run-economics, said-no and edge-distribution projections share one read
    of each sibling rather than three.
    """
    from personalclaw.workflows import introspection

    merged: dict[str, Any] = {}
    for rid, events in sibling_ledgers:
        gates = own if rid == run_id else introspection.gate_stats(events)
        for node_id, stats in gates.items():
            into = merged.setdefault(node_id, introspection.GateStats(node_id=node_id))
            into.passes += stats.passes
            into.rejects += stats.rejects
            into.retries_consumed += stats.retries_consumed
    return merged


#: Journal kinds the cockpit timeline shows. A whitelist rather than "everything": the ledger
#: carries internal bookkeeping (cache keys, effect idempotency) that would bury the handful of
#: events a human reads, and a timeline nobody can scan is a timeline nobody opens.
_TIMELINE_KINDS = (
    "run_started",
    "run_finished",
    "step_started",
    "step_completed",
    "step_failed",
    "step_skipped",
    "step_cached",
    "step_cancelled",
    "step_attempt",
    "step_escalated",
    "gate_resolved",
    "gate_revised",
    "handoff",
    "decision",
    "steering",
    "breaker_trip",
    # `iteration` is FILTERED, not simply allowlisted — see `_TRIPPED_ITERATION` below. A loop's
    # per-round bookkeeping is the noise this whitelist exists to keep out (an `until_cancelled`
    # watcher writes one row per cycle for months), but the subset that records a TRIPPED BREAKER is
    # the opposite: measured on a `general-project` run, `breaker:identical_output` was journaled
    # twice and reachable from no user surface at all (#3524). `breaker_trip` above is a kind only
    # the LOOP noun writes (`introspection.RAIL_PRODUCERS` declares the asymmetry), so the run-side
    # trip has no other row to travel on.
    "iteration",
)

#: The prefix `loop_iteration.advance_loop` writes into an `iteration` row's `outcome` when
#: `check_breaker` tripped. Everything else it writes (`continue`, `dry_streak`, `condition_met`, …)
#: is bookkeeping a reader does not need one row per round of.
_TRIPPED_ITERATION = "breaker:"


def introspection_timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The journal timeline + attempt ledger, oldest-first, redacted.

    Routed through `journal_mod.redact` — the SAME recursive redactor the journal writer uses,
    reused rather than re-derived so the two cannot drift. The ledger records a node's model and
    failure detail verbatim, and a failure message is exactly where a credential surfaces in a
    screenshot.

    Oldest-first because this reads as a narrative: "what changed" answered newest-first makes a
    reader reconstruct causality backwards.
    """
    out: list[dict[str, Any]] = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "")
        if kind not in _TIMELINE_KINDS:
            continue
        outcome = str(event.get("outcome") or "")
        if kind == "iteration" and not outcome.startswith(_TRIPPED_ITERATION):
            continue
        row = {
            "kind": str(event.get("kind") or ""),
            "ts": str(event.get("ts") or ""),
            "node_id": str(event.get("node_id") or ""),
            "instance_path": str(event.get("instance_path") or ""),
            "attempt": event.get("attempt"),
            "state": str(event.get("state") or ""),
            "duration_secs": event.get("duration_secs"),
            "tokens": event.get("tokens"),
            "cost_usd": event.get("cost_usd"),
            "model": str(event.get("model") or ""),
            "approved": event.get("approved"),
            # `outcome` joins the chain for the `iteration` rows above: it is the only field that
            # names WHICH breaker tripped, and a row reading just `iteration` with a blank detail
            # would surface the event while still hiding the signal.
            "detail": event.get("detail") or event.get("error") or outcome,
        }
        out.append(journal_mod.redact(row))
    return out


def _next_if_silent(
    run: Any, nodes: list[dict[str, Any]], open_asks: list[dict[str, Any]]
) -> dict[str, Any]:
    """ "What happens next if I say nothing" — answered, not implied.

    The one checklist question no existing surface answers, and the one that decides whether a
    user can walk away. Three real cases, because they demand different user action: a run
    waiting on an answer will sit there indefinitely (the user IS the blocker), a running run
    proceeds on its own, and a terminal run has already stopped.
    """
    from personalclaw.workflows.models import TERMINAL_RUN_STATUSES

    if run.status in TERMINAL_RUN_STATUSES:
        return {"action": "nothing", "detail": f"this run is {run.status.value}", "queued": []}
    if open_asks:
        return {
            "action": "waits",
            "detail": (
                f"{len(open_asks)} question(s) are waiting for an answer — this run makes no "
                "further progress until one is given"
            ),
            "queued": [str(c.get("node_id") or "") for c in open_asks],
        }
    queued = [str(n.get("node_id") or "") for n in nodes if n.get("state") in ("pending", "ready")]
    return {
        "action": "proceeds",
        "detail": (
            f"{len(queued)} node(s) are queued and will run without further input"
            if queued
            else "no queued work remains; the run is finishing"
        ),
        "queued": queued,
    }
