# Research Lab

**Ask one question, walk away, come back to a report.**

Research Lab turns a question into a tree of sub-questions and works that tree down over
many unattended cycles: each cycle takes a few open sub-questions, hands each one to its
own subagent, records what came back with its sources, and grafts on whatever new
questions the work turned up. When the tree is answered — or the cycle budget runs out —
it synthesises everything into one markdown report.

A PersonalClaw **tool** app. It implements `ToolProvider` from `personalclaw.sdk.tool`.

## The split: this app is the ledger, not the researcher

The app stores campaigns and decides what to work next. It does **no fetching, no
summarising and no model calls** — the agent already does all three, better. That is why
`app.json` declares only two permissions:

| Permission | Why |
|---|---|
| `storage` | campaigns and reports live under the app's own data dir |
| `cron` | the `advance-campaigns` job is what makes "unattended" real |

No `network`, no `agent`, no `api`. If you were expecting a web-search provider, that is a
different app; this one composes with whichever one you have.

## The five tools

| Tool | What it does |
|---|---|
| `research_open` | Open a campaign: a question, optional starting sub-questions, a cycle budget. |
| `research_list` | Every campaign with its status, cycles used and progress. |
| `research_next` | Close the open cycle, hand back the next worklist. Reports `done` when there is nothing left or the budget is spent. |
| `research_record` | One sub-question's finding + sources, plus any follow-up questions it raised. |
| `research_report` | Synthesise findings, open questions and sources into `report.md`. |

A campaign lives at
`~/.personalclaw/apps/research-lab/data/campaigns/<id>/campaign.json`, with its report
beside it. Plain JSON and plain markdown — readable, greppable, and still yours if you
uninstall the app.

## The unattended loop

`app.json` declares one cron, `advance-campaigns`, which runs hourly and does exactly one
cycle per tick:

1. `research_next` — if it says `done`, call `research_report` once and stop.
2. Otherwise research each sub-question in the worklist **in its own subagent, in
   parallel**, so one slow question does not stall the cycle.
3. `research_record` per answered sub-question, with sources and any follow-ups.
4. Stop. The next tick runs the next cycle.

App crons are headless and auto-approved, so nothing waits for you. Disable or uninstall
the app and the cron goes with it.

### Why it terminates

A loop that can always find more to do never stops, so the app refuses to let a campaign
grow without limit:

- **Cycle budget** (default 5, per campaign) — a spent budget ends the campaign as
  `exhausted`, and the report says which questions are still open rather than pretending
  they were answered.
- **Depth cap** (3) — a follow-up question three levels below the root is dropped rather
  than grafted.
- **Duplicate drop** — the same sub-question arriving from two branches is asked once.
- **Node ceiling** (200) plus length caps on questions, findings and sources.

`research_next` reporting `done` is a **success**, not an error. That matters: a failure
would read as transient and keep the cron retrying a finished campaign forever.

## Run the tests

```bash
pytest research-lab
```

30 tests, no network, no gateway, no pytest plugins required. The one to read first is
`test_a_campaign_runs_multiple_unattended_cycles_and_synthesises_a_report` — it drives the
loop the way the cron's prompt does.

## Install it

From the dashboard: **Store → Add source → local path**, point it at this directory,
then install and enable it. Or from a shell against a running gateway — the gateway takes
the owner token as a `?token=` query parameter (`personalclaw token` prints a URL
carrying it), not an `Authorization` header:

```bash
curl -X POST "$PERSONALCLAW_URL/api/apps?token=$PERSONALCLAW_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"source": "'"$PWD"'", "confirm": true}'
curl -X POST "$PERSONALCLAW_URL/api/apps/research-lab/enable?token=$PERSONALCLAW_TOKEN"
```

Enabling the app registers the provider and the cron; disabling it removes both.
`personalclaw doctor` shows the campaign store, how many campaigns are open, and any
campaign file that will not parse.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `default_cycle_budget` | 5 | Cycles a new campaign may run unattended. |
| `cycle_breadth` | 3 | Sub-questions one cycle hands out to subagents. |

A `research_open` call may override the budget; a `research_next` call may override the
breadth.

## License

MIT — see `LICENSE`.
