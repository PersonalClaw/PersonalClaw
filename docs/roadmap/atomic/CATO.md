# COST-AND-TOKEN-OBSERVABILITY — atomic plans

**Source plan:** [`COST-AND-TOKEN-OBSERVABILITY`](../plans/COST-AND-TOKEN-OBSERVABILITY.md)  
**Code:** `CATO`  
**Source status:** proposed

8 atoms from an unbuilt, dependency-light plan: 5 for S1 (ledger core + 3 attribution write-site groups + read routes) and 3 for S2 surfaces (turn line, session total, Usage panel). No hard cross-plan deps; PROMPT-CACHE coupling is optional/degrading, not a gate.

Each atom below executes start-to-finish in one go. If an atom lists dependencies, they must be `done` before it starts — that is the whole point of the split: no atom should ever need pausing to go execute other work.

| Atom | Status | Title | Depends on | Done when |
|---|---|---|---|---|
| `CATO-1` | ⬜ | Durable usage ledger module + durability/inventory registration | — | TurnUsage rows round-trip through append-only JSONL at config_dir()/usage/turns.jsonl (atomic_write, 2x-cap trim); rollup groups by all five keys (model\|source\|agent\|provider\|day) and sets priced=False when any constituent row is unpriced; totals aggregate; record_turn is fail-open (logs at DEBUG, never raises into a turn); path registered in durability/inventory.py as secret=False,derived=True so audit_home() passes with it claimed AND fails without it (both proven); tests/test_usage_ledger.py green |
| `CATO-2` | ⬜ | Wire the chat turn-completion write site (reference attribution) | `CATO-1` | A real chat turn writes exactly one ledger row beside the existing stats.inc_cost_usd (chat_runner.py ~2730) with correct tokens; cost derived via pricing.estimate_cost ONLY when the provider reported none (vendor cost wins when present); an unpriced model writes priced=False, cost_usd=0.0 |
| `CATO-3` | ⬜ | Subagent attribution — replace the discard, add cost fields to SubagentInfo | `CATO-1` | The EVENT_COMPLETE discard at subagent.py:1704 is replaced with a record_turn(source='subagent'); SubagentInfo (subagent.py:262) gains input_tokens/output_tokens/cost_usd carried onto the existing completion delivery; a 3-way fan-out yields 3 rows and the parent completion reports each child's cost; tests green |
| `CATO-4` | ⬜ | Wire loop, cron, channel, and cli turn-completion attribution | `CATO-1` | Each of the loop-worker, schedule, channel, and CLI turn-completion paths writes a row with the correct source string; each source appears in rollup(group_by='source') after a real turn on that path; one attribution test per source |
| `CATO-5` | ⬜ | Usage read routes + offline agent-reference regen | `CATO-1` | GET /api/usage/rollup?group_by=&since=&until= and GET /api/usage/totals return correct aggregates using the §2.2 {error:{code,message}} envelope; offline agent reference regenerated (python -m personalclaw.manifest_reference) and the reference drift test is green |
| `CATO-6` | ⬜ | Turn-level surface — tokens + cost on the 'Turn complete' line | `CATO-2` | The existing 'Turn complete' render shows real USD + in/out tokens for a priced turn; shows 'unpriced' (never $0.00) for a model with no price row; the cache fragment renders only when cache tokens are non-zero (absent when PROMPT-CACHE hasn't landed); backend line composer + FE renderer both updated; tests green |
| `CATO-7` | ⬜ | Session total on the chat session header/detail | `CATO-5` | The chat session header/detail renders '$X · N tokens' by reading rollup scoped to the session key; a multi-turn session's reported total matches the sum of its turn lines |
| `CATO-8` | ⬜ | Settings → Usage panel with honest-zero / partial-pricing marker | `CATO-5` | UsagePanel renders Today/7d/30d totals, a by-model table, a by-source table, the cache-savings line (0 when no cache support), and a read-only 'spent $X of your $Y cap' from SpendMeter config; period segmented control state round-trips the URL; a period mixing priced+unpriced models shows a visible 'partial — N unpriced models' marker (never a confidently-complete number); the orphaned SystemAgentStats.input_tokens is wired (no parallel type added); zero primitive-adoption ratchet trips (Button/Segmented/ui-forms only); token-lint + a11y (focus-visible, reduced-motion) pass |

## Atom scopes

### `CATO-1` — Durable usage ledger module + durability/inventory registration

**Status:** todo

Design S1; C1 — The ledger (usage_ledger.py, storage per §2.4); Session 1 T1.1

**Done when:** TurnUsage rows round-trip through append-only JSONL at config_dir()/usage/turns.jsonl (atomic_write, 2x-cap trim); rollup groups by all five keys (model|source|agent|provider|day) and sets priced=False when any constituent row is unpriced; totals aggregate; record_turn is fail-open (logs at DEBUG, never raises into a turn); path registered in durability/inventory.py as secret=False,derived=True so audit_home() passes with it claimed AND fails without it (both proven); tests/test_usage_ledger.py green

### `CATO-2` — Wire the chat turn-completion write site (reference attribution)

**Status:** todo

C2 — Attribution (chat row, the reference implementation); Session 1 T1.2

**Done when:** A real chat turn writes exactly one ledger row beside the existing stats.inc_cost_usd (chat_runner.py ~2730) with correct tokens; cost derived via pricing.estimate_cost ONLY when the provider reported none (vendor cost wins when present); an unpriced model writes priced=False, cost_usd=0.0

### `CATO-3` — Subagent attribution — replace the discard, add cost fields to SubagentInfo

**Status:** todo

C2 — Attribution (subagent row); Context §3 (subagents discard the completion event today); Session 1 T1.3

**Done when:** The EVENT_COMPLETE discard at subagent.py:1704 is replaced with a record_turn(source='subagent'); SubagentInfo (subagent.py:262) gains input_tokens/output_tokens/cost_usd carried onto the existing completion delivery; a 3-way fan-out yields 3 rows and the parent completion reports each child's cost; tests green

### `CATO-4` — Wire loop, cron, channel, and cli turn-completion attribution

**Status:** todo

C2 — Attribution (loop/cron/channel/cli rows); Session 1 T1.4

**Done when:** Each of the loop-worker, schedule, channel, and CLI turn-completion paths writes a row with the correct source string; each source appears in rollup(group_by='source') after a real turn on that path; one attribution test per source

### `CATO-5` — Usage read routes + offline agent-reference regen

**Status:** todo

C3 Integration points (read routes); §2.2 error envelope; Session 1 T1.5

**Done when:** GET /api/usage/rollup?group_by=&since=&until= and GET /api/usage/totals return correct aggregates using the §2.2 {error:{code,message}} envelope; offline agent reference regenerated (python -m personalclaw.manifest_reference) and the reference drift test is green

### `CATO-6` — Turn-level surface — tokens + cost on the 'Turn complete' line

**Status:** todo

Design S2(a) turn-level; C3 — Surfaces (Turn line); Session 2 T2.1. NOTE: cache fragment is the optional PROMPT-CACHE-SUBSTRATE seam — renders only when cache tokens are non-zero, absent otherwise (non-blocking, degrades gracefully)

**Done when:** The existing 'Turn complete' render shows real USD + in/out tokens for a priced turn; shows 'unpriced' (never $0.00) for a model with no price row; the cache fragment renders only when cache tokens are non-zero (absent when PROMPT-CACHE hasn't landed); backend line composer + FE renderer both updated; tests green

### `CATO-7` — Session total on the chat session header/detail

**Status:** todo

Design S2(b) session-level; C3 — Surfaces (Session total); Session 2 T2.2

**Done when:** The chat session header/detail renders '$X · N tokens' by reading rollup scoped to the session key; a multi-turn session's reported total matches the sum of its turn lines

### `CATO-8` — Settings → Usage panel with honest-zero / partial-pricing marker

**Status:** todo

Design S2(c) account-level; C3 — Surfaces (Settings→Usage) + frontend rules; C4 (MAY read SpendMeter config, MUST NOT write it); Session 2 T2.3 + T2.4

**Done when:** UsagePanel renders Today/7d/30d totals, a by-model table, a by-source table, the cache-savings line (0 when no cache support), and a read-only 'spent $X of your $Y cap' from SpendMeter config; period segmented control state round-trips the URL; a period mixing priced+unpriced models shows a visible 'partial — N unpriced models' marker (never a confidently-complete number); the orphaned SystemAgentStats.input_tokens is wired (no parallel type added); zero primitive-adoption ratchet trips (Button/Segmented/ui-forms only); token-lint + a11y (focus-visible, reduced-motion) pass

