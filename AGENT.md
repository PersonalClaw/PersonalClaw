# AGENT.md — machine-facing repo gotchas

Curated operational gotchas a coding agent needs to work in this repo without stepping on
a known landmine. This is deliberately distinct from the two neighboring docs:

- **[AGENTS.md](AGENTS.md)** — the human/contributor brief (doctrine, git rules, DoD).
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — the long-form contract.
- **AGENT.md** (this file) — the *mechanical* gotcha list: the handful of things that look
  fine but silently misbehave. Each entry cross-references its harness rule spec
  (`harness/specs/rules/`) where one exists, so the "why" is versioned and greppable rather
  than living only in a maintainer's private memory (which resets).

If you fix a bug that belongs to one of these classes, update the matching spec in the
**same commit** (the same-PR rule) — that is how this list stays true.

---

## Running & rebuilding

- **Backend `.py` change ⇒ restart the gateway.** Backend Python NEVER hot-reloads.
  Ctrl-C `make serve`, then `make serve` again. A change that "has no effect" is almost
  always a stale process — see `harness/specs/scenarios/backend-change-needs-restart.md`.
- **Frontend rebuild ⇒ served live** through the `static/dist` symlink; no restart needed
  (except the first-ever build after a clean clone, which needs one restart so `/assets`
  routes register).
- **`src/personalclaw/static/dist` is a SYMLINK to `web/dist`, not a copy.** A `cp -R`
  leaves a frozen directory that shadows the symlink and serves a **stale** SPA forever.
  Use `make web-build` (it recreates the symlink correctly). See
  `harness/specs/scenarios/frontend-serves-stale-bundle.md`.
- **Build the SPA from the repo ROOT, never `cd web`.** Only the root `package-lock.json`
  exists (`web`/`desktop` carry none — npm/cli#4828). Use `npm run build --workspace web`
  or `make web-build`.
- **`personalclaw stop`/`restart` are SERVICE-FIRST.** If a real launchd/systemd service is
  installed they act on *that* — not your foreground `make serve`. For dev, Ctrl-C the
  foreground server and tail `.dev-home/gateway.log`.

## The dev home

- **Always use an isolated dev home — never `~/.personalclaw`.** `make serve` defaults
  `PERSONALCLAW_HOME=./.dev-home`. The gateway ready line prints a tokenized URL; treat it
  as sensitive.
- **Tests that touch on-disk state MUST isolate it** via `tmp_path`/`monkeypatch` of
  `config_dir()`. An unisolated destructive test once deleted a developer's real bound
  model. See `harness/specs/rules/destructive-test-isolation.md`.

## Apps

- **The gateway runs INSTALLED app copies** from `$PERSONALCLAW_HOME/apps/<name>/`, not your
  workspace tree. Push repo edits with `POST /api/apps/{name}/update {source, confirm:true}`
  — editing the workspace `apps/` source does nothing to the running app. See
  `harness/specs/scenarios/installed-app-edit-not-live.md`.
- **First-party apps live in the sibling `PersonalClawApps` clone**, not `apps/`. Point the
  gateway at them with `PERSONALCLAW_FIRST_PARTY_APPS_DIR=$PWD/../PersonalClawApps` (or an
  `apps` symlink) or they won't appear in the Store.
- **Apps import core only via `personalclaw.sdk.*`.** A deep `personalclaw.<internal>`
  import breaks the removability boundary. See `harness/specs/rules/app-sdk-boundary.md`.

## Config

- **A new config field is a four-point contract:** dataclass + `_meta`, `AppConfig.load()`
  mapping, `to_dict()`, and `_EDITABLE_CONFIG` (if runtime-editable) + a frontend control
  (if user-facing). Miss `load()` and it reverts to default on reload; miss `to_dict()` and
  it's dropped on save. `tests/test_config_roundtrip.py` catches most misses. See
  `harness/specs/rules/config-four-points.md`.
- **Entity/user state goes in `entity_settings/*.json`, not `config.json`; secrets go in
  the credential store.**

## Streams & prompts

- **SSE event types must be registered on BOTH ends.** A new backend event string that
  isn't in the frontend's `RUN_LIFECYCLE` union is silently dropped by `EventSource` — no
  error, just a missing UI update. See `harness/specs/rules/sse-event-registered.md`.
- **Keep chat/run stream state in the pure folds** (`coalesceReducers.ts`, `runFold.ts`) —
  no React/fetch/mutation — so recorded traces can be replayed through them. See
  `harness/specs/rules/pure-stream-folds.md`.
- **Fence untrusted text at ingestion** (`fence_untrusted`) before it enters a prompt; its
  wording is a security control (don't reword). See
  `harness/specs/rules/fence-at-ingestion.md`.
- **Never truncate a transcript between a tool call and its result** — route truncation
  through the orphan-dropping walk-back helper. See
  `harness/specs/rules/no-naive-transcript-cut.md`.

## Frontend testing

- **Controlled inputs use `onChange`, not native DOM value-setting.** PersonalClaw's
  `TextInput` is a controlled React component (`value` + `onChange`); a test that sets a
  DOM value without firing the React `onChange` won't update component state. Drive inputs
  through the React event, not by assigning `.value`.

## The venv interpreter

- **The venv lives at `.venv/` inside the repo** and is not relocatable. Run tools as
  `.venv/bin/python -m …` (the harness CLI, pytest for collection, etc.) so you never
  accidentally hit a system interpreter missing the dev extras.
