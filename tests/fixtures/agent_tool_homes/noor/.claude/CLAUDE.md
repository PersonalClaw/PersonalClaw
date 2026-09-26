# Noor's global instructions

## Who I am
Staff backend engineer at Cartwheel Freight (remote, Toronto, America/Toronto). I own the
carrier-webhooks ingestion service and the eta-engine. In the evenings I maintain feedsmith
(open source, MIT, github.com/feedsmith/feedsmith) with Tomás and Aiko.

Assume I know Python, Postgres and Kafka well. Skip the basics and tell me what you would do.

## How I work
- Python 3.12 and uv for everything (`uv run`, `uv add`). Never pip install into the system Python.
- ruff for lint and format, pytest for tests. Run the narrowest test first, then the package suite.
  Show me the failing assertion, not the whole log.
- Type hints on public functions. No bare `except:`. A dataclass beats a dict with more than
  three keys.
- Small diffs. If a change is heading past ~300 lines, stop and propose a split.
- Conventional commits (`fix(ingest): ...`). No co-author trailers. Never push without asking.
- Migrations: plan first, no long locks on big tables, backfills in batches,
  `CREATE INDEX CONCURRENTLY` outside a transaction. Ask before touching any non-local database.
- When I paste logs, find the first real error before theorising about the last one.

## Writing
- Canadian spelling in prose (colour, behaviour, cheque). US spelling in code identifiers.
- Short sentences. No emojis. No "Great question".
- For a draft (PR description, postmortem, talk notes) give me one good version, not three.

## Privacy
- Cartwheel data has consignee names, addresses and phone numbers. Never quote them back or send
  them anywhere that leaves this laptop. Replace them with `<consignee>`.
- Do not read `~/Notes/Garden/Health/` or `~/Documents/Finance/` unless I ask in that message.

## Environment
- Laptop: noor-mbp (M3 Max, 64 GB). Home server: mini (Mac mini M1) on Tailscale as
  mini.tail4c2e1.ts.net.
- Scratch Postgres for experiments: postgresql://noor:scratchpad-2026@localhost:5433/scratch
  (the docker compose file in ~/src/feedsmith binds 5433, not 5432).
- On-call handoff is Monday 10:00. When I am on call, prefer small reversible changes.

## Notifications
When a task runs longer than about ten minutes, post a one-line summary to my #agent channel:

    curl -s -X POST -H 'Content-type: application/json' \
      --data '{"text":"<one-line summary>"}' \
      {{SLACK_WEBHOOK_URL}}
