# Contributing to PersonalClaw

Thanks for contributing. This document covers the project's engineering
doctrine, how to set up a dev environment, and what we expect from a PR.

## Doctrine for all new work

These principles have governed every feature in the codebase; new work is held
to them.

- **Clean break** — no backward-compat shims, dual paths, or dual
  implementations. When you replace a mechanism, remove the old one in the same
  change. Vendor-specific logic lives only in removable provider apps (the
  `apps/` bundles) — the core stays provider-agnostic. Port features from other
  tools as end-behavior native to PersonalClaw's entity/provider model, not as
  translations. The `web/` app is the only frontend for new UI.
- **Implementation owns product too** — user flows, UX, and look-and-feel are
  in scope for every change, not just function. A feature isn't done when the
  endpoint works; it's done when a user can find it, use it, and understand it.
- **As-built authority** — [docs/vision.md](docs/vision.md) records the
  intended design; the code is the as-built truth. When they disagree, that's
  a bug to reconcile deliberately, not silently.
- **Validation bar** — implement fully, then validate *as a user*: drive the
  system from the frontend, inspecting every surface (UI, console, network,
  backend logs, persisted state). Any gap, issue, or UX rough edge found during
  validation is in scope to fix. Only call a change complete after it is both
  implemented and validated end to end.
- **Judge by code truth, not banners** — status text in plans and docs goes
  stale; verify against the actual code before deciding something is or isn't
  done.

## Development setup

```bash
# from the repo root
python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"

# build the dashboard SPA once (rebuilds are picked up live)
make web-build

# run an isolated dev gateway (state under ./.dev-home, never ~/.personalclaw)
make serve
```

Useful Makefile targets (see `make help` for the full list):

| Target | What it does |
|---|---|
| `make test` | Run the Python test suite (pytest). |
| `make lint` / `make format` | black + isort + flake8 + mypy / auto-format. |
| `make web-build` | Build the React SPA and link `static/dist -> web/dist` (a symlink by design — never copy). |
| `make serve` / `make serve-fresh` | Dev gateway on `:10000` with an isolated `PERSONALCLAW_HOME` / same, after a fresh SPA build. |
| `make serve-web` | Vite dev server with HMR on `:3000`, proxying to a running gateway. |

Frontend tests run from `web/`: `npm test` (vitest).

**Frontend builds run from the repo root**, never from inside `web/`. The root
`package.json` owns an npm **workspace** (`web`, `desktop`) with a single
root `package-lock.json` — workspace members carry no lockfile of their own
(they're gitignored). Running `npm ci`/`npm install` inside a member trips npm's
optional-dependency bug ([npm/cli#4828](https://github.com/npm/cli/issues/4828))
and silently skips the platform-native binaries (rollup/esbuild/lightningcss),
producing a broken build. Use `make web-build` (or `npm ci && npm run build
--workspace web`). If a build ever fails with `Cannot find module
@rollup/rollup-<platform>` or a missing `*.node` binary, the escape hatch is
`rm -rf node_modules package-lock.json && npm install` from the root, then
re-commit the regenerated lockfile. (End users never hit this — `pip`/`uv`/Docker
installs ship a prebuilt `web/dist`.)

Two runtime facts that save debugging time:

- **Backend `.py` changes need a gateway restart** to take effect; frontend
  rebuilds are served live from `web/dist`.
- **The gateway loads installed app copies** from `~/.personalclaw/apps/<name>/`,
  not the workspace `apps/` tree — push app edits to a running gateway via
  `POST /api/apps/{name}/update`.

## Testing expectations

- Every behavior change comes with tests. The suite is large; run the shards
  relevant to your change locally, and the full suite before a PR.
- Destructive tests must be isolated: monkeypatch `config_dir`/`tmp_path` so a
  test can never touch a real `~/.personalclaw` (this has bitten before).
- The config system has a round-trip contract: a new config field must appear in
  the dataclass (+ `_meta`), `load()`, `to_dict()`, and a write path —
  `test_config_roundtrip.py` enforces most of this generically.

## Pull requests

- **One concern per PR.** Keep refactors separate from behavior changes.
- **Describe what you validated**, not just what you wrote — which flows you
  drove in the UI, what you checked in persistence.
- **No dead code, no commented-out blocks, no "phase 2" stubs.** Ship the whole
  slice or don't ship it (clean break, above).
- **Docs are part of the change.** If you alter config fields, routes, or CLI
  flags, update [docs/reference/](docs/reference/) in the same PR.
- Match the existing style; `make lint` must pass.

## Architecture orientation

- Core package: `src/personalclaw/` — gateway (`gateway.py`), dashboard API
  (`dashboard/`), agents, memory, knowledge, loops, tasks, skills, app platform
  (`apps/`, `providers/`, `sdk/`).
- Extension apps: the workspace `apps/` directory (siblings of this repo) —
  every vendor integration lives there.
- Frontend: `web/` (Vite + React SPA).
- Reference docs: [docs/reference/](docs/reference/); user guides:
  [docs/guides/](docs/guides/); forward-looking plans:
  [docs/roadmap/](docs/roadmap/roadmap.md) (the roadmap is maintained by the
  project owner — open an issue to discuss it rather than editing it in a PR).

## License

By contributing you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
