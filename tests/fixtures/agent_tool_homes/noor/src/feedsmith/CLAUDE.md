# feedsmith

Public repo. Everything here is visible to the world: no internal hostnames, no tokens, no
Cartwheel anything.

## Layout
- `src/feedsmith/fetch.py`: async fetcher (httpx), conditional GET, 16 overall / 2 per host.
- `src/feedsmith/parse.py`: feedparser to `Entry`; `entry_hash` is the dedupe key.
- `src/feedsmith/store.py`: `Storage` protocol, `SQLiteStore` (default), `PostgresStore`.
- `src/feedsmith/digest.py` + `templates/digest.html.j2`: the HTML digest. Jinja autoescape is ON.
- `migrations/`: raw SQL for Postgres, applied in order. SQLite creates its schema in code.

## Commands
- Tests: `uv run pytest -q`. Postgres tests run only with `FEEDSMITH_TEST_PG=postgresql://...`.
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Local Postgres: `docker compose up -d db` (binds 5433).

## Conventions
- Public API is `feedsmith.fetch.fetch_all`, `feedsmith.parse.parse_feed`,
  `feedsmith.digest.render_digest`. Do not break their signatures in a minor release.
- Every bug fix gets a regression test named after the issue, e.g. `test_issue_412_...`.
- CHANGELOG entry under `## Unreleased` for anything user-visible, with the PR or issue number.
- Tomás reviews changelog wording; Aiko owns packaging and the release workflow.
