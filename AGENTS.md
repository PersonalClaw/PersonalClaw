# AGENTS.md — brief for coding agents

You are contributing to **PersonalClaw core**: a self-hosted, local-first,
provider-agnostic personal AI gateway (Python 3.12+ aiohttp backend + React/Vite
SPA). This file is the compressed contract. The long form is
[CONTRIBUTING.md](CONTRIBUTING.md); roadmap work additionally runs under
[docs/roadmap/plans/EXECUTION-PROTOCOL.md](docs/roadmap/plans/EXECUTION-PROTOCOL.md).

## Build / test / lint (run from the repo root)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make web-build          # build the SPA once (npm workspace, from root — never `cd web`)
make serve              # dev gateway on :10000, state under ./.dev-home (NEVER ~/.personalclaw)

make lint               # black --check + isort --check + flake8 + mypy — must pass
make test               # full pytest suite
cd web && npm run typecheck && npm test && npm run build   # when web/ changed
```

Definition of done for any change: `make lint` green · targeted `pytest` green ·
`make test` green before the final commit · the web gate when `web/` changed ·
new behavior has tests · docs moved with the change.

## Doctrine (non-negotiable)

- **Clean break.** No backward-compat shims, dual paths, dead code, or
  TODO/FIXME/commented-out blocks. Replace a mechanism → delete the old one in
  the same change. Unfinished work lives in a plan file, not in code.
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
- A backend/behavior change with no test, or a config field that skips the
  round-trip wiring.
- Editing `docs/roadmap/` to reshape the roadmap in a PR (open an issue instead).
- Unsigned commits (missing DCO), commits authored by an agent, or a force-push
  to `main`.
- Reworded security/consent copy that wasn't the point of the change.
- "Works on my endpoint" with no user-level validation.
