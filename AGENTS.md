# AGENTS.md — brief for coding agents

You are contributing to **PersonalClaw core**: a self-hosted, local-first,
provider-agnostic personal AI gateway (Python 3.12+ aiohttp backend + React/Vite
SPA). This file is the compressed contract — including the session discipline
every roadmap task runs under (see *Roadmap session discipline* below). The long
form is [CONTRIBUTING.md](CONTRIBUTING.md).

## Build / test / lint (run from the repo root)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make web-build          # build the SPA once (npm workspace, from root — never `cd web`)
make serve              # dev gateway on :10000, state under ./.dev-home (NEVER ~/.personalclaw)

make lint               # black --check + isort --check + flake8 + mypy — must pass
make test               # full pytest suite
npm run typecheck:web && npm run test:web && npm run build   # when web/ changed (from root)
npm run smoke:render    # then: mount the BUILT bundle in headless Chromium
```

Definition of done for any change: `make lint` green · targeted `pytest` green ·
`make test` green before the final commit · the web gate **including the render
smoke** when `web/` or the npm manifest/lockfile changed · new behavior has
tests · docs moved with the change.

**Render smoke is non-negotiable for frontend-affecting pushes.** typecheck,
vitest (jsdom), and `vite build` can ALL pass while the built bundle crashes at
first render (this shipped as the v0.1.0 blank dashboard — a dual-React
dependency skew). Only loading the artifact in a real browser proves it
renders. One-time setup per clone: `npm run hooks:install` (repository-owned
pre-push hook) + `npx playwright install chromium`. The hook runs the full
clean-install chain automatically when outgoing commits touch `web/`,
`package.json`, or `package-lock.json`; CI repeats it on every PR. Never bypass
a red gate with `--no-verify` — that is reserved for owner-declared emergencies.

## Doctrine (non-negotiable)

- **Clean break.** No backward-compat shims, dual paths, dead code, or
  TODO/FIXME/commented-out blocks. Replace a mechanism → delete the old one in
  the same change. Unfinished work lives in a plan file, not in code.
- **Breaking changes are the maintainer's call, not yours.** During 0.x the
  maintainer lands backward-incompatible clean breaks with **no migrations**
  (the migration-backed lifecycle regime is deliberately deferred until the
  architecture stops moving — so no `lifecycle/` package exists and hand-rolled
  gate/migration machinery is a rejection). Working an owner-assigned roadmap
  task, a class-B/S clean break is expected: execute it, note it in the
  CHANGELOG, advise `personalclaw snapshot`. Working anything else, stay
  **additive** (defaults on new fields, tolerant reads, routes beside routes); if
  the clean fix needs a persisted-shape, route-contract, or credential-format
  change, it needs a **migration path** — an unattended, idempotent
  read-old/write-new on first load — and if you can't see one, **stop and surface
  it** (E3) instead of improvising compatibility or stranding existing state.
  Full rules incl. the lifecycle mental model:
  [CONTRIBUTING.md](CONTRIBUTING.md#breaking-changes).
- **A plan's gate/migration tasks are methodology, not scope.** Plans written in
  contributor form ask for `lifecycle/gates.py` registrations, dual paths, and
  `lifecycle/migrations/m_*.py` files. On maintainer-assigned work those tasks
  **re-scope, they never block**: drop the gate (the new path is the path), turn
  the migration into an idempotent backfill keyed on inspecting the data, record a
  DEVIATION, keep building. A `Depends on:` a deferred-doctrine header is a claim
  to verify against code, not a fact — and a plan sentence that conflicts with
  doctrine is a wording problem, never an unbuildable feature.
- **Provider-agnostic core.** No vendor names or vendor-specific logic in core.
  Vendor integrations are removable app bundles in the separate PersonalClawApps
  repo; apps import core **only** via `personalclaw.sdk.*`.
- **Implementation owns product.** A change is done when a user can find, use,
  and understand it — not when the endpoint returns 200. The `web/` SPA is the
  only frontend for new UI.
- **Validate as a user.** Drive the system from the UI/CLI and inspect every
  surface (UI, console, network, backend logs, persisted state under the dev
  home) before calling it done.
- **Config round-trip contract.** A new config field wires through: dataclass +
  `_meta`, `load()`, `to_dict()`, a write path, and (if user-facing) a frontend
  control. `test_config_roundtrip.py` catches most misses.
- **Security surfaces are copy-sensitive.** Don't reword warnings, consent text,
  fencing preambles, or refusal messages except as a task specifies.

## Git / PR rules

- **Branch, never commit to `main`:** `feature-<slug>` / `bugfix-<slug>` /
  `improvement-<slug>`, one concern per branch, off `main`.
- **One conceptual commit per branch:** amend + `git push --force-with-lease` as
  it iterates. **`main` is append-only and NEVER force-pushed** (the self-updater
  `git pull`s it).
- **DCO required:** `git commit -s` on every commit (CI enforces it).
- **Clean authorship:** owner is the sole author + committer — no agent
  co-author or session trailers.
- **npm single-root lockfile:** only the root `package-lock.json` exists
  (`web`/`desktop` carry none — npm/cli#4828). Build from root.
- The PR template's four fields are the contract: *what changed / change class
  (R·B·S) / what you validated as a user / docs touched*.

## Roadmap session discipline

Every implementation session that executes a roadmap plan runs under these
standing ground rules — they let a task be delegated to any session, including a
smaller model, without eroding standards. A session that hasn't internalized
them is not ready to execute a task.

- **Before code:** read the plan fully (especially its **soul guardrail** and
  **Context**) and every architecture doc it cites; find your task table and own
  **one task at a time, in listed order**; confirm the change class (R/B/S — see
  Doctrine); and set up an isolated dev home (`make serve`'s `./.dev-home` or
  `PERSONALCLAW_HOME=<tmp>`), never `~/.personalclaw`. Tests monkeypatch
  `config_dir`/`tmp_path`.
- **The task line is the scope.** Don't fix, refactor, or "improve" anything the
  task doesn't name — record an adjacent problem as a DISCOVERY instead of fixing
  it inline. No new dependencies unless the task names the exact package. No dead
  code, TODO/FIXME, commented-out blocks, or "phase 2" stubs.
- **Definition of done, every task:** `make lint` · targeted `pytest` · `make
  test` before the final commit · the web gate incl. render smoke when `web/`
  changed · new behavior has tests (a bug-fix gets a regression test that failed
  before) · docs moved with the change · CHANGELOG entry for class-B/S · the
  task's "done-when" clause is literally true.
- **Close as a user.** Each task table ends with a validation walkthrough —
  execute it, driving the UI/CLI and inspecting every surface (UI, console,
  network, backend logs, persisted state under the dev home). A session whose
  validation fails is not complete.
- **Deviations ledger.** Append (never rewrite) to the plan's `## Execution log`:
  `- [YYYY-MM-DD][T<id>] DONE|DEVIATION|DISCOVERY|BLOCKED: <one line>`. DONE per
  task (with commit ref), DEVIATION when reality forced a change, DISCOVERY for
  adjacent problems you deliberately didn't fix, BLOCKED with the escalation id.
- **Escalation triggers — stop and surface, don't push through.** **E1** premise
  mismatch (code doesn't match the task's citations). **E2** a failing test you
  can't root-cause in ~30 min, or an unannotated pre-existing red. **E3**
  lifecycle ambiguity (the change touches persisted state / a stable surface the
  plan didn't declare) — *not* E3: a plan merely asking for a gate/dual-path/
  migration file, which is the methodology re-scope above. **E4**
  security-control ambiguity (touching auth, fencing, scanner, egress, sandbox,
  or SEL beyond the literal wording). **E5** dependency pressure (seems to need
  an unnamed package). **E6** scope pressure (honestly completing it needs work
  another task owns). On any trigger: write the BLOCKED line, leave the tree
  clean, move only to an unblocked, independent task.

## Shared conventions (what a plan builds on)

Cross-cutting mechanical rules every plan obeys — defined once here (and, for the
storage/SDK/config/SEL details, in
[docs/architecture/overview.md](docs/architecture/overview.md)) so no plan
re-invents a shape another touches:

- **Config round-trip** — a new field wires through dataclass + `_meta`,
  `load()`, `to_dict()`, a write path, and (if user-facing) a frontend control;
  `test_config_roundtrip.py` enforces it. Operator knobs → `config.json`;
  per-entity preferences → `entity_settings/*.json`; secrets → the credential
  store.
- **Error envelope (HTTP)** — new routes return
  `{"error": {"code": "<stable_snake_code>", "message": "<human>"}}`; `code` is
  append-only and never reworded once shipped (a stable surface an agent branches
  on). Success envelopes imitate the neighboring handler — don't standardize
  retroactively.
- **SEL event logging** — every security-relevant action logs via `sel()`
  (`log_tool_invocation` / `log_api_access` / a raw `SecurityEvent`); new
  `event_type`s are lowercase snake. There is one audit log — never a second.
- **Storage** — everything under `config_dir()` (never hardcode the home);
  writes via `atomic_write`/`atomic_write_bytes`; reads tolerate missing/corrupt;
  secrets `mode=0o600`; append-only JSONL trims at 2× cap; new durable state that
  external tools may read is a stable surface.
- **Fail-open vs fail-closed** — user-facing availability surfaces (notification
  rules, settings) fail **open**: corrupt file → permissive default + warn.
  Inbound/security surfaces (tokens, inbound `enabled` flags, capability probes)
  fail **closed**: missing/corrupt → refuse + explicit log. State the choice
  in-code at each site.
- **SDK export boundary** — apps import core only via `personalclaw.sdk.*`
  (`test_apps_import_boundary.py`); a new app-facing primitive is added to the
  relevant `sdk/<area>.py` re-export, never reached into directly. An SDK export
  is a stable surface.

## Repo map

- `src/personalclaw/` — the package: `gateway.py`, `dashboard/` (API + handlers),
  `agents`, `memory.py`, `knowledge/`, `loop/`, `skills/`, app platform
  (`apps/`, `providers/`, `sdk/`), security (`security.py`, `sandbox.py`,
  `sel.py`, `net/`, `trust_mode.py`).
- `web/` — Vite + React SPA (the only new-UI frontend).
- `docs/` — `reference/` (as-built), `guides/` (user), `architecture/`,
  `roadmap/` (maintainer-owned plans).
- `tests/` — pytest suite (isolate destructive tests via `config_dir`/`tmp_path`).

## What gets your PR rejected

- Vendor names or vendor-specific logic in core (belongs in an app bundle).
- An app importing core outside `personalclaw.sdk.*`.
- Dead code, TODO/FIXME comments, commented-out blocks, "phase 2" stubs, or a
  second implementation of an existing behavior left in place.
- Hand-rolled versioning/gate/migration machinery, or an unrequested breaking
  change to a persisted shape / route contract / credential format (surface it,
  don't ship it — see Doctrine).
- A backend/behavior change with no test, or a config field that skips the
  round-trip wiring.
- Editing `docs/roadmap/` to reshape the roadmap in a PR (open an issue instead).
- Unsigned commits (missing DCO), commits authored by an agent, or a force-push
  to `main`.
- Reworded security/consent copy that wasn't the point of the change.
- "Works on my endpoint" with no user-level validation.
