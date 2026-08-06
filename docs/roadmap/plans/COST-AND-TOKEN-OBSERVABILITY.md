# COST-AND-TOKEN-OBSERVABILITY

**Status:** DECOMPOSED — the executable work now lives in [`../atomic/CATO.md`](../atomic/CATO.md) as 8 atomic plan(s).

This plan was split because parts of it blocked on other plans, which forced it to sit half-done while other work ran. Each atom below its own file executes start-to-finish in one go; the dependency graph lives in [`../atomic/dag.json`](../atomic/dag.json).

The original design record is kept below — execution logs, measured findings and owner rulings are the reason this document still matters.

---
# Plan: Cost & Token Observability — Answer "What Did This Cost Me?"

**Status:** DESIGNED — created 2026-07-29 (owner ask: close the cost-observability gap)
**Created:** 2026-07-29
**Wave:** 2 (S1: the durable ledger; S2: the surfaces)
**Depends on:** nothing hard. Every input already exists: provider-reported tokens on `LLMEvent` (`llm/events.py:61-66`), `pricing.estimate_cost` (`pricing.py:59`), `model_pricing.json` (26 priced models), and the `Stats` counters (`stats.py:40-47`). Coordinates with PROMPT-CACHE-SUBSTRATE (that plan produces the cache-hit numbers; **this plan owns the store and the UI they land in** — the two must not each invent a stats file), MODEL-ROUTING-TELEMETRY (its learned local-vs-cloud routing needs per-model actual cost — this plan is its data prerequisite; it consumes, never forks, the ledger here), AUTONOMY-GUARDRAILS (DONE — its `SpendMeter` owns *enforcement*/caps at `guardrails/budgets.py`; this plan owns *observation*. They stay separate: a budget answers "may I spend?", a ledger answers "what did I spend?"), FEEDBACK-SIGNAL (shipped — same "capture then surface" shape; imitate its store layout).
**Scope:** the product's positioning claim is "observable autonomy," and cost is the one axis with no observability at all. Verified: tokens are captured, cost is computed at exactly **one** call site (`chat_runner.py:2558` → `stats.inc_cost_usd`), `Stats.get_cost_usd()` (`stats.py:96`) has **zero consumers**, `Stats` is an **in-memory process-global lost on every restart** (`stats.py:47`), and `SystemAgentStats.input_tokens` is typed in the frontend API layer (`web/src/lib/api.ts:880`) and rendered by **no** `.tsx` file. The only spend surface in the UI is a *configuration input* (`GuardrailsPanel.tsx`) with no corresponding actual-spend readout, and `savings.py` is explicitly self-labelled a counterfactual ledger, not spend metering. So a user cannot answer "what did this cost me?", "which model is eating my budget?", or "did caching help?". This plan adds a **durable, per-turn cost ledger** with per-session/per-model/per-source rollups, and the three surfaces that make it felt: a turn-level readout in chat, a Usage panel in Settings, and honest-zero handling for unpriced models. **Soul guardrails:** (1) **one meter, never a second currency** — this plan reports *real provider-reported tokens and real USD*; it never invents credits, points, or an internal unit. Opaque multi-meter credit systems are a known source of user distrust (products that make users ask "why are my credits being used so fast?" and run three independent billing axes) — PersonalClaw's advantage is that the user pays the vendor directly, so the honest number is available and must be shown; (2) **honest zero over invented precision** — an unpriced model reports its tokens with `cost_usd = 0.0` and a visible "unpriced" marker, never an estimate. This mirrors `model_pricing.json`'s existing in-file rule ("A model absent here costs 0.0 (honest: we never invent a price)"); (3) **observation only — never enforcement** — this plan cannot block, throttle, or refuse a turn. `SpendMeter` (AUTONOMY-GUARDRAILS) owns every gate; a ledger that starts refusing work has become a budget and violates the split. Class **B** (a new durable store) — pre-LIFECYCLE-DOCTRINE, so it lands as a **plain clean break under the pre-1.0 banner** (tolerant reads, no gate/migration; CHANGELOG entry + snapshot advice in release notes).

---

## Context (code recon, 2026-07-29 — every claim verified against code)

**What exists (the arithmetic is done; the memory and the screen are missing):**
- **Per-turn numbers arrive already-computed from the provider.** `AgentEvent`/`LLMEvent` (`llm/events.py:61-66`) carries `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens`, `cost_usd`, `num_turns`. Providers that report a cost pass it through; others leave it 0.0 and core derives it.
- **`pricing.estimate_cost(model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens) -> float`** (`pricing.py:59-71`) — already accepts all four token classes and already documents the honest-zero rule verbatim: "Returns 0.0 for an unknown model … an honest 'unpriced', never a guess." **No new pricing math is needed anywhere in this plan.**
- **`Stats` already counts the right things** (`stats.py:40-47`): `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens`, `total_turns`, `total_duration_ms`, plus a `_cost_usd` float with a mutex (`stats.py:86-98`).
- **The single write site** is `chat_runner.py:2558` (`stats.inc_cost_usd(event.cost_usd)`), inside the turn-completion handling that also renders the existing "Turn complete: N events, M tool calls, context X%" line. That is the natural place to also write a ledger row — the event is already in hand.

**What's missing, precisely (do not restate these as vague "no observability"):**
1. **No durability.** `Stats` is constructed per-process (`stats.py:47`). A gateway restart zeroes lifetime spend. There is no per-day, per-session, per-model, or per-source breakdown anywhere.
2. **Two orphans that prove the intent existed.** `Stats.get_cost_usd()` has zero consumers (grep: only its own definition). `SystemAgentStats.input_tokens` is declared in `web/src/lib/api.ts:880` and rendered by no component. Both should end this plan wired, not deleted.
3. **`cost_usd` is only accumulated for the dashboard chat path.** Subagents discard the completion event's numbers outright — `subagent.py:1684-1685` is `elif event.kind == EVENT_COMPLETE: break`, and `SubagentInfo` (`subagent.py:262-292`) has no token or cost field. Loops, crons, and channel turns route through their own paths. So even the in-memory total is *not* whole-system today; §C2 fixes attribution by `source`.
4. **The UI shows a cap input with no actual.** `GuardrailsPanel.tsx` renders `SpendMeter` *configuration*; nothing renders spend. `savings.py` is a **counterfactual** ledger (its own docstring says "this is the SAVINGS (counterfactual) ledger, not spend metering") and must not be conflated with this one.

**The boundary that keeps this plan honest (read twice — an executor will be tempted to blur it):**
`guardrails/budgets.py::SpendMeter` already persists spend to `~/.personalclaw/spend.json` for **run and day scopes**, enforced at 4 sites, warning at 80%, and **only for unattended work** (`guardrails/model_call.py` guards `reasoning|background|loops|orchestration`; interactive chat is explicitly out of scope by that plan's design). That is an **enforcement** meter with a deliberately narrow scope. This plan's ledger is an **observation** store covering *every* turn including interactive chat. They are different objects with different scopes, and merging them would either (a) start enforcing caps on interactive chat — a behavior change AUTONOMY-GUARDRAILS deliberately declined — or (b) widen an enforcement file into a reporting file. **Do neither.** The ledger may *read* `SpendMeter`'s configured caps to render "spent X of your Y cap" (§C4); it never writes to it.

## Design

- **S1 — the durable ledger.** A new `usage_ledger.py` owns one append-only JSONL at `config_dir()/usage/turns.jsonl`, one row per completed turn: timestamp, session key, source (`chat|loop|cron|subagent|channel|cli|background`), agent, provider, model, the four token classes, `cost_usd`, `priced` (bool), duration. Rows are written from the turn-completion path that already holds the event, plus the three currently-unattributed paths (subagent, loop, cron). Reads are aggregations computed on demand over a bounded tail with a small in-memory rollup cache — no second database, no ORM. Retention follows the house convention for append-only logs (trim at 2× cap, mirroring `notifications.jsonl`/SEL). `Stats` keeps its in-memory counters (cheap, used by channel `status` replies) and additionally becomes *derivable* from the ledger after a restart, which is what closes the "lost on restart" gap.
- **S2 — the surfaces.** Three, in ascending detail: (a) **turn-level** — the existing "Turn complete" line gains real cost + tokens (and a cache-hit fragment when PROMPT-CACHE-SUBSTRATE has landed), with an "unpriced" marker instead of `$0.00` when the model has no price row; (b) **session-level** — a session's total in the chat header/detail, answering "what did this conversation cost?"; (c) **account-level** — a Usage panel in Settings: today / 7d / 30d totals, a per-model table (tokens, cost, share), a per-source table (which subsystem spends), and the cache-savings line. The panel also renders "spent X of your configured Y cap" by reading `SpendMeter`'s config — the first time the existing cap input has a corresponding actual.
- **What this is NOT:** not a budget or a limiter (AUTONOMY-GUARDRAILS owns enforcement); not a credit system (guardrail 1); not the counterfactual savings ledger (`savings.py` stays as-is, separate and separately labelled); not a billing integration; not telemetry — **nothing leaves the machine**, consistent with the verified zero-telemetry posture (no SDK, no upload; the ledger is local-only and excluded from any export that excludes state, per §C1).

## Contracts & Interfaces (conventions per [INTEGRATION-ARCHITECTURE](INTEGRATION-ARCHITECTURE.md))

### C1 — The ledger (`usage_ledger.py`, new; storage per §2.4)

```python
@dataclass
class TurnUsage:
    ts: str                    # ISO-UTC, matching the SEL's timestamp convention
    session_key: str
    source: str                # chat | loop | cron | subagent | channel | cli | background
    agent: str                 # "" = the default agent
    provider: str              # the resolved provider entry name (e.g. "anthropic")
    model: str                 # the resolved model id — the join key to model_pricing.json
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float = 0.0
    priced: bool = True        # False ⇒ no model_pricing.json row; cost_usd is 0.0 and MUST render "unpriced"
    duration_ms: int = 0

def record_turn(u: TurnUsage) -> None:
    """Append one row. Best-effort and NEVER raises into a turn — a ledger write
    failure must degrade to a DEBUG log, never break the user's conversation
    (fail-open per §2.7: this is a user-facing availability surface, not a
    security control)."""

def rollup(*, since: str = "", until: str = "", group_by: str = "model") -> list[dict]:
    """Aggregate the ledger. group_by ∈ {model, source, agent, provider, day}.
    Returns rows with summed tokens + cost + a `priced` flag that is False when ANY
    constituent row was unpriced (so a partially-unpriced total can never present
    as complete)."""

def totals(*, since: str = "", until: str = "") -> dict: ...
```

Storage: `config_dir()/usage/turns.jsonl`, `atomic_write`-appended per the house convention, trimmed at 2× cap. Register the new path in `durability/inventory.py` — **mandatory**: `audit_home()` fails on any unclaimed path (that guard exists precisely because `snapshot.CORE_FILES` and `EXPORT_EXCLUDE` had already drifted and left nine store directories uncovered). Mark it `secret=False`, `derived=True` (it is reconstructible telemetry-of-self, not irreplaceable user content) so retention/export policy treats it correctly.

### C2 — Attribution: the four write sites

The event is already in hand at each; add one `record_turn` call. **All four are required** — a ledger covering only dashboard chat would under-report exactly the unattended work the user most wants to see.

| Source | Site | Note |
|---|---|---|
| `chat` | `dashboard/chat_runner.py:2558` (beside the existing `stats.inc_cost_usd`) | the reference implementation |
| `subagent` | `subagent.py:1684` — **replace the discard.** Today `elif event.kind == EVENT_COMPLETE: break` throws the numbers away | also add `input_tokens`/`output_tokens`/`cost_usd` to `SubagentInfo` (`subagent.py:262`) so a fan-out's cost is visible per child |
| `loop` / `cron` | the loop-worker + schedule turn-completion paths | `source` distinguishes them; a loop's rows join on its worker session key |
| `channel` / `cli` | the channel + CLI turn paths | same event shape |

Derive `cost_usd` with `pricing.estimate_cost` **only when the provider reported none**; when the provider reports a cost, record theirs (vendor truth beats our table) and set `priced=True`. Set `priced=False` **only** when there is no price row AND no provider-reported cost.

### C3 — Surfaces (frontend)

```
Turn line   (extend the existing "Turn complete" render):
  Turn complete: 12 events, 4 tool calls, context 34% · 8.2k in / 1.1k out · $0.031
  …and when unpriced:                                  · 8.2k in / 1.1k out · unpriced
  …and with caching active:                            · 7.9k cached (96%) · $0.004

Session total: in the chat session header/detail — "$0.19 · 46k tokens"

Settings → Usage (new panel):
  Today / 7 days / 30 days   →  total cost, total tokens, turn count
  By model    — table: model, turns, in, out, cached, cost, % of spend, [unpriced badge]
  By source   — table: chat / loops / crons / subagents / channels, cost, share
  Cache       — "saved $X.XX on N cached reads this period" (0 when no cache support)
  Cap context — "spent $X of your $Y daily cap" (reads SpendMeter config; read-only)
```

Frontend rules that apply (non-negotiable, from the protocol): filter state lives in the URL; use shell primitives (`TopBar`/`ListScaffold`/`Segmented`) and design tokens — no hardcoded colors/spacing; the **primitive-adoption ratchet** (`web/src/design/primitiveAdoption.test.ts`) means any new raw `<button>`/`<input>` outside `web/src/ui/` turns CI red — use `Button`/`Segmented`/the `ui/forms` family. Wire the orphaned `SystemAgentStats.input_tokens` (`api.ts:880`) rather than adding a parallel type.

### C4 — What this plan may and may not touch on the guardrails side

- **MAY:** read `AppConfig`'s configured spend caps to render the "spent X of Y" context line.
- **MUST NOT:** write `~/.personalclaw/spend.json`, call `SpendMeter` mutators, add a cap, or gate/refuse/throttle any turn. If a task appears to require enforcement, that is escalation trigger **E6** (another plan owns it) — record it and stop.

### Integration points
- **Calls:** `pricing.estimate_cost` (`pricing.py:59`), `config_dir()`, `atomic_write`, `durability/inventory.py` registration, `AppConfig.load()` (cap context only).
- **Called by:** the four turn-completion paths (§C2); the Usage panel via new read routes; MODEL-ROUTING-TELEMETRY later (it consumes `rollup(group_by="model")` — do not pre-build its features here).
- **Storage owned:** `config_dir()/usage/turns.jsonl` (new, class B). PROMPT-CACHE-SUBSTRATE writes **no** store of its own — its numbers ride these rows.
- **Deliberately NOT touched:** `guardrails/budgets.py` + `spend.json` (enforcement), `savings.py` (counterfactual ledger — leave its labelling intact), SEL (a security log, not a metering log — never add cost rows to it), provider adapters.

## Task breakdown (executor-ready — run under [EXECUTION-PROTOCOL](EXECUTION-PROTOCOL.md))

### Session 1 — The durable ledger

| ID | Task | Files | Done when |
|---|---|---|---|
| T1.1 | `usage_ledger.py`: `TurnUsage`, `record_turn` (fail-open, never raises), `rollup`, `totals`; JSONL at `config_dir()/usage/turns.jsonl` with 2×-cap trim; register the path in `durability/inventory.py` (`secret=False`, `derived=True`) | `src/personalclaw/usage_ledger.py`, `src/personalclaw/durability/inventory.py`, `tests/test_usage_ledger.py` | rows round-trip; `rollup` groups by all five keys; a write failure logs and does not raise; `audit_home()` passes with the new path claimed (it fails without the registration — prove both) |
| T1.2 | Wire the `chat` site beside the existing `stats.inc_cost_usd` (`chat_runner.py:2558`); derive cost only when the provider reported none; set `priced=False` only when unpriced AND unreported | `dashboard/chat_runner.py`, tests | a real chat turn writes exactly one row with correct tokens; an unpriced model writes `priced=False, cost_usd=0.0` |
| T1.3 | Wire `subagent`: replace the completion-event discard at `subagent.py:1684-1685`; add `input_tokens`/`output_tokens`/`cost_usd` to `SubagentInfo` (`subagent.py:262`) and carry them onto the existing completion delivery | `src/personalclaw/subagent.py`, tests | a sub-agent run writes a row with `source="subagent"`; the parent's completion event reports the child's cost; a 3-way fan-out yields 3 rows |
| T1.4 | Wire `loop`, `cron`, `channel`, `cli` turn-completion paths with the right `source`; one test per source proving attribution | the loop-worker, schedule, channel, and CLI turn paths, tests | each source appears in `rollup(group_by="source")` after a real turn on that path |
| T1.5 | Read routes: `GET /api/usage/rollup?group_by=&since=&until=` and `GET /api/usage/totals`; §2.2 error envelope; regenerate the offline agent reference (`python -m personalclaw.manifest_reference`) since routes changed — there is a drift test | `dashboard/handlers/`, `server.py`, `docs/reference/`, tests | routes return correct aggregates; reference drift test green |
| V1 | Validation as a user: on an isolated dev home with a real model bound, run chat turns + a sub-agent + a cron; confirm rows land with correct sources; **restart the gateway and confirm history survives** (the specific gap this session closes); confirm an unpriced model shows `priced=false`; `make lint` + targeted pytest + `make test` | — | holds |

### Session 2 — The surfaces

| ID | Task | Files | Done when |
|---|---|---|---|
| T2.1 | Turn-line extension: tokens + cost on the existing "Turn complete" render; "unpriced" instead of `$0.00`; cache fragment rendered only when cache tokens are non-zero | `dashboard/chat_runner.py` (the line's composer) + the FE renderer, tests | a priced turn shows real USD; an unpriced turn shows "unpriced" and never `$0.00`; no cache fragment when caching is off |
| T2.2 | Session total on the chat session header/detail (reads `rollup` scoped to the session key) | `web/src/pages/ChatPage.tsx` (or the session detail component), `web/src/lib/api.ts` | a multi-turn session reports a total matching the sum of its turn lines |
| T2.3 | Settings → Usage panel: period segmented control (Today/7d/30d, URL-backed), by-model table, by-source table, cache-savings line, "spent X of Y cap" context; wire the orphaned `SystemAgentStats.input_tokens` rather than adding a parallel type | `web/src/pages/settings/UsagePanel.tsx` (+ settings registration), `web/src/lib/api.ts` | tables match the API; period state round-trips the URL; zero ratchet trips (use `Button`/`Segmented`/`ui/forms` — no raw chrome); token-lint + a11y (focus-visible, reduced-motion) pass |
| T2.4 | Honest-zero + partial-pricing test sweep: a period containing BOTH priced and unpriced models must render the total with a visible "partial — N unpriced models" marker, never a confidently-complete number | tests + the panel | a mixed period is visibly marked partial |
| V2 | Validation as a user: drive real spend across chat + a loop + a sub-agent; open the Usage panel and reconcile the by-model and by-source tables against the turn lines by hand; verify the cap-context line matches the configured cap; verify an unpriced local model appears with tokens and no invented cost; full local gate incl. web typecheck/test/build | — | holds |

## Owner tasks (real world)
1. **Decide the retention window** for `turns.jsonl` (the plan proposes the house 2×-cap trim; a year of per-turn rows is small, but it is your disk and your call).
2. **Confirm the honest-zero presentation.** The plan refuses to estimate a price for unpriced models (mirroring `model_pricing.json`'s stated rule). The alternative — estimating from a similar model — is rejected here as inventing a number; say so if you disagree.
3. **Consider expanding `model_pricing.json`** beyond 26 models once the panel shows how often "unpriced" appears in your real usage. The panel is the instrument that tells you which rows are worth adding.

## Risks & open questions
- **Provider-reported vs derived cost disagreeing.** Rule: vendor number wins when present; ours fills gaps. Recorded per row via `priced` so a later audit can tell which is which.
- **Attribution gaps for ACP runtimes.** An ACP sub-agent never goes through a core `ModelProvider`, so it is unmetered — the same structural gap AUTONOMY-GUARDRAILS hit. Record rows with the tokens the ACP path *does* report and mark `priced=False` where it reports none; **do not fabricate**. Note it in the panel copy rather than silently under-reporting.
- **Ledger write volume.** One row per turn is trivial; a 100-way fan-out is 100 rows. Fine at JSONL scale, and the trim bounds it.
- **Open:** whether per-tool cost attribution (which tool triggered the most expensive turns) is worth a follow-up. Deferred — turn granularity is the honest unit today because providers bill per request, not per tool.

## Execution log

- **CATO-1 (T1.1) DONE — the ledger store.** Added `src/personalclaw/usage_ledger.py`: the
  `TurnUsage` dataclass (§C1 shape — session/source/agent/provider/model + four token classes +
  cost_usd + `priced` + duration_ms), `record_turn` (append-only JSONL at
  `config_dir()/usage/turns.jsonl`, fail-open — a write failure DEBUG-logs and never raises into a
  turn, §2.7; 2×-cap atomic-rewrite trim per the feedback.py house convention), `rollup(group_by ∈
  {model,source,agent,provider,day})` and `totals` — both fold a `priced` flag that goes False when
  ANY constituent row is unpriced (a partial total can't present as complete), sorted by descending
  cost. Registered the path in `durability/inventory.py` as `usage_ledger` (`KIND_JSONL_APPEND`,
  `DOMAIN_PLATFORM`, `derived=True`, `secret=False`) so `audit_home()` claims it — proven both ways
  (passes WITH the entry, reports `usage/` unclaimed WITHOUT). Scope: T1.1 ONLY — the four write
  sites (C2) and the three surfaces (S2) are separate atoms, so no user-facing readout yet → no
  CHANGELOG entry (the store is invisible until S2). Soul guardrails honored: observation-only (no
  enforcement — that's SpendMeter), honest-zero (`priced=False` never renders `$0.00`), one meter
  (real provider tokens + USD, never an invented unit). **Gates:** `make lint` clean (697 source
  files); `tests/test_usage_ledger.py` (12: round-trip, all 5 group keys, tainted-total rule,
  window filter, fail-open, corrupt-line tolerance, inventory audit both ways) + `test_durability_inventory.py`
  (22) pass.

- **CATO-2 DONE — the chat write-site (C2, 1 of 4).** A real dashboard chat turn now writes exactly
  one ledger row at the `EVENT_COMPLETE` handler (`chat_runner.py`, beside the existing
  `stats.inc_cost_usd`), via a new `_record_turn_usage(event, *, session_key, source, agent, provider,
  model)` helper. Vendor-reported cost wins when present; when the provider reported none the caller's
  `estimate_cost` fallback (already resolved at that site) is recorded. `priced` is False ONLY when the
  model has no `model_pricing.json` row (via the existing `pricing.has_pricing`) AND the provider
  reported no cost — then `cost_usd` is an honest 0.0 the UI renders "unpriced". `record_turn` is
  fail-open, so a ledger fault can't break a turn. Scope: the CHAT write-site only — the subagent /
  loop-cron / channel-cli sites (the other three of C2) and the surfaces (S2) are later atoms, so
  still no user-visible readout → no CHANGELOG. **Gates:** `make lint` clean (697 files);
  `tests/test_usage_ledger.py::TestChatWriteSite` (4: one row with provider cost=priced, priced-model
  zero-cost still priced, unpriced-model→priced=False+0.0, write-site fail-open) + full usage-ledger
  suite (16) + `test_chat_runner_procedural_wiring` (3) pass.

- **CATO-3 DONE — the subagent write-site (C2, 2 of 4).** The `EVENT_COMPLETE` handler in
  `subagent.py::_run_inner` discarded the child's token/cost numbers (`break`); it now captures them,
  derives cost via `pricing.estimate_cost` when the provider reported none, stamps
  `input_tokens`/`output_tokens`/`cost_usd` onto `SubagentInfo` (new fields, carried on the existing
  completion delivery), and writes one ledger row via a new `SubagentManager._record_subagent_usage`
  helper with `source="subagent"` keyed to the PARENT session — so a 3-way fan-out yields 3 rows and
  a fan-out's cost is attributable per child. `provider="acp"` (subagents run through the ACP
  runtime). Fail-open (record_turn never raises into completion delivery). Scope: subagent site only —
  the loop-cron / channel-cli sites (C2 remainder = CATO-4) and the surfaces (S2 = CATO-5..8) remain,
  so still no user-visible readout → no CHANGELOG. **Gates:** `make lint` clean (698 files);
  `tests/test_usage_ledger.py::TestSubagentWriteSite` (4: parent-keyed row, 3-way fan-out→3 rows,
  unpriced→priced=False, SubagentInfo carries the fields) + full usage-ledger (20) + `test_subagent.py`
  (65, SubagentInfo field addition safe) pass.

- **CATO-4 DONE — the remaining C2 write-sites (background / channel / cron / cli).** Root finding:
  the loop-worker + schedule-injection paths ALREADY route through `_run_chat` (so CATO-2 covers them
  via `source=session._app`); the genuine gaps go through `stream_and_collect`, which returned only
  text and discarded the `EVENT_COMPLETE` usage. Fix: added an optional `on_complete(event)` callback
  to `stream_and_collect` (fires at EVENT_COMPLETE, default None → byte-identical for its 10 callers,
  fail-open) and a shared `usage_ledger.record_from_event(event, *, source, …)` seam that owns the
  vendor-cost-wins / estimate-when-absent / honest-unpriced / fail-open logic. Wired: heartbeat
  (`source="background"`), `_inject_with_retry` (`source=label` → "channel"/"cron"), and `cli_chat`'s
  own EVENT_COMPLETE loop (`source="cli"`). Refactored CATO-2's `_record_turn_usage` and CATO-3's
  `_record_subagent_usage` to delegate to `record_from_event` (deleted the duplicated cost/priced
  logic — clean break). DEVIATION (recorded): the atom framed 4 discrete sites; two were already
  covered via `_run_chat`, and the real work was the shared `stream_and_collect` seam that unlocks
  every text-only caller at once. Model is best-effort at these paths (ACP abstracts it); `source` is
  what the done-when's `rollup(group_by='source')` requires. Scope: write-sites complete — the API
  (CATO-5) + surfaces (CATO-6..8) remain, so no user-visible readout yet → no CHANGELOG. **Gates:**
  `make lint` clean (698 files); `tests/test_usage_ledger.py::{TestRecordFromEvent,TestStreamAndCollectOnComplete}`
  (7: vendor-cost-wins, estimate-when-absent, honest-zero, all-six-sources-in-rollup, fail-open,
  on_complete fires with the event, None→byte-identical) + full usage-ledger + `test_subagent.py`
  (65) + `test_llm_helpers.py` = 92 passed.
