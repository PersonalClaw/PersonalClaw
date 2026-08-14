# Plan: Platform Primitives — Edges, Verdicts and Policies as First-Class Nouns

**Status:** NOT STARTED — 16 atoms in [`../atomic/PP.md`](../atomic/PP.md), five startable now.
**Pillar:** A (Execution Engine + Convergence) · **rev 17** (2026-08-14)

**Soul guardrail.** Everything here stays personal-scale: one user, local files, local SQLite,
one gateway. This plan removes duplicated machinery; it adds no fleet, no service tier, and no
distributed substrate. If an atom below starts to look like a platform for other people's
workloads, it has drifted and should be re-scoped.

---

## Why this plan exists

Two research passes — one on graph-structured agent execution, one on loop engineering — produced
twenty findings between them. Working through them, all but a handful turn out to be symptoms of the
same three absences:

> **PersonalClaw implements one engine's worth of semantics three times, because three of its
> primitives were never named: edges, verdicts, and policies.**

The graph findings are what happens when *edges* are not a primitive — ordering and dataflow are two
hand-maintained lists that nothing reconciles. The loop findings are what happens when *verdicts* and
*policies* are not primitives — "was this good enough?" is answered in six vocabularies and "how much
freedom does this work have?" in fourteen places.

Naming the three nouns is the material change. It is also cheapest right now — see §6.

This plan is the answer to the state the engine program recorded twice: the remaining work needs
*"a coherent multi-session program or an owner scope decision — not a single atomic session bolted
on"* ([`../WF2-SESSION-QUEUE.md`](../WF2-SESSION-QUEUE.md), 2026-08-12).

## Research provenance

Public prior art, cited by mechanism so a session can go read the source:

* **arXiv:2604.11378** — *From Agent Loops to Structured Graphs: A Scheduler-Theoretic Framework for
  LLM Agent Execution* (Apr 2026, position paper, 70 systems surveyed). The load-bearing idea: an
  agent loop is a *"single ready unit scheduler"*, and graph engines differ from it in **degree**, not
  kind. This is the licence for §4's unification.
* **Fluxtion, *Graph Engineering Needs a Compiler*** (2026-07-29). Two arguments this plan adopts:
  *topology underdetermines execution* (a diagram cannot say whether B precedes C, when changes become
  visible, or whether D runs if B produced no change), and *do not keep a second edge list* — declare
  structure once, derive coordination. Its codegen half is **rejected** (§7).
* **The loop-engineering corpus** (Jun–Aug 2026): the five-component loop anatomy
  (discovery / handoff / verification / persistence / scheduling), the four stacked loops whose fourth
  *"reaches inside and updates the agent loop directly"*, the maker-never-grades rule, and the field
  post-mortem of a fully autonomous pipeline that ran dead for two months because nobody owned acting
  on its output.
* **A 2026 review-loop essay** supplying the verifier failure taxonomy this plan uses by name —
  *weak test, reward hacking, correlated reviewer mistakes* — and the diagnostic question every gate
  in this repo should survive: **"what is the laziest thing that passes, and would I still accept it?"**

## §0 Ownership map — what is re-homed, not duplicated

Checked against all 69 existing plans and 620 existing atoms before writing a line. Four of the seven
theses already had owners and are **amendments to those plans**, not atoms here:

| Thesis | Owner | Disposition |
|---|---|---|
| Verdict primitive — the third vocabulary | LOOPS-EVOLUTION | **`WF2LOO-16`** (new atom there). `WF2LOO-13` reconciled two of three; `WF2LOO-12`'s design already *named* `loop/judge.CycleVerdict` as the third and left it unowned. |
| Judge model independence | LOOPS-EVOLUTION | **`WF2LOO-17`** (new atom there). |
| Worker-independent stall signal | LOOPS-EVOLUTION | **`WF2LOO-18`** (new atom there). |
| Autonomy as one policy object | AUTONOMY-GUARDRAILS | **`AG-13`** (new atom there), depending on `PP-14` so the autonomy ceiling and the supervisor policy are ONE object rather than two competing declarations. The two-level `Ceiling ∩ Profile` model it composes is PLATFORM-HARDENING-FLOORS §5's, reused not rebuilt. |
| Outcome/consumption records | PROACTIVE-ASSISTANT `PA-4` | **Coordinated**: `PP-9` generalizes LEARN-R18's existing `pending_outcome`/`outcome_resolved` pair rather than adding a second outcome facility. `PA-4`'s decision journal must land on the same one. |
| Task-pool leases | TASKS-SOPS `WF2TAS-6`/`-10` (done) | **Reused**: `PP-12` adopts the shipped compare-and-swap lease decision functions as an admission policy; `PP-13` retires only the duplicate projection. |
| Pure frontier core | WORKFLOWS-V2 `WV-3` (done) | **Extended, not replaced**: `PP-11` puts a seam behind it with a byte-identical acceptance bar. |

Everything else — edges, the ledger as a platform primitive, replay, trajectories, the admission core
and the work-unit noun — has no owner, and is §1–§5 below.

## §1 Edges — dataflow declared once, ordering derived (`PP-1`…`PP-3`)

Two hand-maintained edge lists over one dependency truth. Admission reads `needs` only, `needs` is
sibling-scoped by `validator.py:221-230`, and bindings are a separate graph feeding the cache key, the
stale-inputs check and the mutation cascade but never admission. `validator._kahn_levels` already
builds the combined graph and its own docstring says its levels are not an execution order.

`PP-1` makes the disagreement a typed error. `PP-2` derives ordering from bindings and — because the
derived graph is global and already proven acyclic — lifts the sibling-only restriction, which makes a
**diamond spanning two containers expressible for the first time**. `PP-3` closes the matching gap on
the other side: `output_contract` is checked against its producer and never against its readers.

## §2 The ledger as a platform primitive (`PP-4`…`PP-8`)

`workflows/journal.py` is the best observability surface in the tree and only one feature has it.
`learning/mining.py` derives from *"the run's own journal"*, so the outer improvement loop covers one
of four work-unit kinds while the loop kinds carry the long-horizon autonomous work.

One vocabulary unlocks seven findings at once: loop→flywheel coverage (`PP-5`), replay as a real verb
(`PP-6`), trajectory signatures (`PP-7`), edge statistics (`PP-8`), consumer liveness (`PP-10`),
attention budgeting, and per-subtree spend — the last three as projections, needing no new store.

## §3 Outcomes — measure what landed, not what ran (`PP-9`, `PP-10`)

The platform records what it *did* and never what *landed*. That one shape produces loop output nobody
reads, unmeasured routing edges, and the declared-but-inert class this repo keeps rediscovering by AST
audit rather than at runtime. The mechanism already exists, scoped to one feature: LEARN-R18's
`pending_outcome` / `outcome_resolved` pair, with `measured` kept distinct from `inconclusive` so an
unmeasurable outcome decays faster. `PP-9` generalizes it; `PP-10` uses it to surface a work unit whose
output nobody consumes — as a **proposal**, never an automatic stop, because "nobody looked yet" and
"nobody will ever look" are different facts and only the user knows which.

## §4 One admission core, N admission policies (`PP-11`…`PP-13`)

Four schedulers answer *"what may run now, given persisted state"* and share zero lines — verified:
`pool.py` imports only stdlib, and `loop/tick.py` and `workflows/tick.py` never import each other.
Each holds a capability the others structurally cannot express: leases only in the pool, dwell/metric
gates only in loops (consumed by exactly one kind), lanes only in workflows.

`PP-11` is seam-only with a byte-identical golden-file bar — a refactor that also changes behaviour
cannot be verified. `PP-12` adds `Lease` and `Dwell`/`MetricGate` on the proven seam. `PP-13` retires
the pool's duplicate projection.

## §5 One work-unit noun (`PP-14`…`PP-16`)

**A loop is not a second engine. A loop is a graph shape plus a supervisor policy.** The shape exists;
the policy has no home, which is why it got implemented twice with each side missing what the other
has. And this connects the two research tracks: *put the judgment in the edges* means a convergence
decision — continue / hold / advance / rollback / replan / escalate / complete — **is** an edge
decision, and belongs where `branch` and `gate` decisions live.

`PP-14` lands the declaration deliberately inert, with the honesty marker this program established
(`WF2LOO-12`): a control with no caller must say so, railed in both drift directions. `PP-15` widens
`loop/tick.evaluate` — pure, restartable, already the right shape, currently used by one kind — into
the single convergence brain, and makes `REPLAN` queue a real mutation batch instead of the
retry-with-a-hint it is today. `PP-16` is the noun change.

## §6 Sequencing, and the timing argument

```
PP-1 ─→ PP-2, PP-3
PP-4 ─┬─→ PP-5 (+WF2LOO-16) ─────────────┐
      ├─→ PP-6 ─→ PP-7                   │
      ├─→ PP-8                           │
      ├─→ PP-9 ─→ PP-10                  │
      └─→ PP-11 ─→ PP-12 ─→ PP-13 ───────┤
WF2LOO-16 ─→ PP-14 ─→ PP-15 ─────────────┴─→ PP-16
                  └─→ AG-13
```

**Startable now:** `PP-1`, `PP-4`, `WF2LOO-16`, `WF2LOO-17`, `WF2LOO-18`.

**If only three land: `WF2LOO-16`, `PP-1`, `PP-4`.** Verdict, edges, ledger. Cheapest, they unblock
everything else, and between them they close eleven of the twenty research findings.

**The timing argument, which is the part that needs an owner decision.** `PP-4`, `PP-5`, `PP-9` and
`PP-16` are **state-shape changes** (class B). Today they are plain clean breaks: no gate, no dual
path, no migration, no cleanup. That is not a permanent property —
[`CONTRIBUTING.md`](../../../CONTRIBUTING.md) §"The lifecycle mental model" says the migration-backed
regime is *"deliberately deferred until the architecture stops moving, on the way to 1.0"*, after
which a B change is governed as gate → dual-path → migrate → cleanup.

**Note carefully: nothing will announce that boundary.** There is no plan to wait for —
`LIFECYCLE-DOCTRINE.md` was **deleted** as a plan in #897 ("deliverables-only DAG"), and its guidance
now lives *only* as that CONTRIBUTING mental model, explicitly "a mental model, not shipped
machinery". So the window is **self-closing rather than scheduled**: it narrows as the architecture
stabilises, and no milestone will conveniently mark the deadline. These four therefore get strictly
more expensive the later they land, which is the whole argument for taking them inside this program
rather than deferring them past it.

`PP-1`, `PP-2`, `PP-3`, `PP-11`, `PP-14` and `PP-15` are pure-logic and can land at any time.

## §7 Deliberately excluded — recorded so they are not mistaken for unbuilt ideas

* **Folding `triggers/` into the admission core.** It answers whether to *start*, not what may run.
  Merging it would put wall-clock into a core whose purity is what makes rewind and replay tractable.
* **Turning the ledger into a database.** Append-only JSONL plus projections is exactly what makes
  epoch invalidation, rewind and replay work. Keep it.
* **Collapsing `stage`/`infer`/`transform`/`action`.** The cost-aware kind split is an asset — it is
  the structural form of "do it with code, then a small model, then the smallest LLM that works".
* **Compiling a generated orchestrator** (Fluxtion's full proposal). Our specs are user- and
  model-authored JSON **edited mid-flight**; codegen would kill mutation, which is a differentiator.
  Take the inference (`PP-2`), leave the codegen.
* **A worker/trigger/function substrate.** Distributed-platform architecture for a single-user local
  product — a rewrite for no user-visible gain.
* **A bi-temporal context graph** (valid-time vs transaction-time over domain entities). Enterprise
  reconciliation machinery; cost without a user here. `publish.py`'s typed lineage
  (`SOURCE`/`INFORMED_BY`/`RELATED`) already covers the provenance question we actually have.
* **A fifth verdict vocabulary**, which is the default outcome of doing `PP-11` or `PP-16` before
  `WF2LOO-16`.

## Success criteria

1. A binding whose producer is not ordered before it is refused at authoring time, and the bundled
   template population was censused before the rule got teeth.
2. A workflow diamond spanning two containers runs correctly, and WF2-R18's join tests stay green.
3. `learning/` produces a proposal from a **loop** run's evidence — not merely rows in a file.
4. A completed run replays to a byte-identical trajectory, and an edited prompt diverges at exactly
   the edited node.
5. `frontier()`'s decisions are byte-identical across the `PP-11` refactor, proven by golden file.
6. A fan-out serializes on a leased resource across a gateway restart.
7. A run re-derives its remaining steps from a judge critique (`REPLAN` does something).
8. Each of the five loop kinds is driven end-to-end through the unified work-unit path with its
   cockpit intact, and a mid-run kill is adopted by a single watchdog.
9. Every declaration this plan adds either has a production caller or says in its own docstring that it
   does not, railed in both directions.

## Execution log

*(append `DONE` / `DEVIATION` / `DISCOVERY` / `BLOCKED` entries here, per the roadmap session
discipline in [`AGENTS.md`](../../../AGENTS.md))*

- **2026-08-14 — plan filed.** 16 atoms authored into `../atomic/PP.md` and `dag.json`; three atoms
  re-homed to LOOPS-EVOLUTION (`WF2LOO-16`/`-17`/`-18`) and one to AUTONOMY-GUARDRAILS (`AG-13`)
  rather than duplicated here. Derived dag block regenerated: 640 atoms, 145 ready, 876 edges, 0
  dangling, no new cycles. No code touched.
