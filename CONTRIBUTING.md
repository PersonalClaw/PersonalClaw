# Contributing to PersonalClaw

Thanks for contributing. This document covers the project's engineering
doctrine, how to set up a dev environment, and what we expect from a PR.

Agents (Claude Code and other coding agents) should read
[AGENTS.md](AGENTS.md) — the same rules, compressed to a build/test/lint +
doctrine + rejection-list brief.

## The model

PersonalClaw is a **solo-maintained** project growing its first contributors. The
governance is sized to match — no committees, no RFC process, no CLA — but the
engineering bar is not lowered.

- **This repo (core) is a high-doctrine working tree.** PRs are welcome, held to
  the full validation bar below. It is the source of product truth.
- **The roadmap is maintainer-owned, with a written intake path** — not a closed
  door. To propose or reshape roadmap work: open an **issue** describing the
  problem → discuss in **[Discussions → Roadmap Input](https://github.com/PersonalClaw/PersonalClaw/discussions)**
  → the maintainer files or updates a plan under `docs/roadmap/plans/`. Please
  don't edit `docs/roadmap/` directly in a PR; the plan set is curated so the
  execution order stays coherent.
- **The newcomer ramp is the [apps repo](https://github.com/PersonalClaw/PersonalClawApps)**,
  not a softened core. First-party and community apps meet the SDK-contract bar
  (import core only via `personalclaw.sdk.*`), ship per-app tests, and get a
  faster review turnaround. If you're looking for a first contribution, start
  there or with a [good first issue](https://github.com/PersonalClaw/PersonalClaw/labels/good-first-issue).

## Developer Certificate of Origin (DCO)

Contributions are accepted under the [DCO](https://developercertificate.org/) —
a lightweight alternative to a CLA that keeps MIT provenance clean without
paperwork. Every commit must carry a `Signed-off-by` trailer, which you add by
committing with `-s`:

```bash
git commit -s -m "fix(memory): correct recall ordering"
```

This appends `Signed-off-by: Your Name <your@email>` (using your git
`user.name`/`user.email`), certifying you wrote the change or have the right to
submit it under the project's license. A CI check enforces it on every PR; an
unsigned commit fails. If you forget, `git commit --amend -s` (or `git rebase
--signoff`) fixes it.

**So you never forget:** run `npm run hooks:install` once (also installs the
pre-commit lint and pre-push gates). It adds a `prepare-commit-msg` hook that
appends the `Signed-off-by` trailer automatically at commit time — you won't
need `-s`. The sign-off must match your commit author, so set a real
`git config user.name`/`user.email` first (not the git default placeholder).

## Doctrine for all new work

These principles have governed every feature in the codebase; new work is held
to them.

- **Clean break** — no backward-compat shims, dual paths, or dual
  implementations. When you replace a mechanism, remove the old one in the same
  change. Vendor-specific logic lives only in removable provider apps (the
  `apps/` bundles) — the core stays provider-agnostic. Port features from other
  tools as end-behavior native to PersonalClaw's entity/provider model, not as
  translations. The `web/` app is the only frontend for new UI.
  **Who breaks what** — clean break is a *maintainer* license, not a contributor
  expectation. See [Breaking changes](#breaking-changes) below: during 0.x the
  maintainer will land breaking, backward-incompatible architectural changes
  without migrations; a contributor PR is not expected to, and shouldn't need to.
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

## <a name="breaking-changes"></a>Breaking changes: who makes them, and what you should do

PersonalClaw is pre-1.0 and deliberately still moving its architecture. Two
different standards apply depending on who is making the change — this is the
one place in the doctrine where maintainer and contributor expectations differ,
so it's worth stating plainly.

**The maintainer will make breaking changes.** While the project works through
the roadmap's architectural program, the maintainer lands backward-incompatible
clean breaks where a better design requires one: state shapes change, stores get
rewritten, endpoints and config fields are replaced outright, and there is **no
automatic migration** of existing `~/.personalclaw` data. Release notes advise
`personalclaw snapshot`, and the README carries the standing pre-1.0 warning.
This is a decision, not an oversight: carrying compatibility shims through a
half-built architecture is how projects calcify around designs they meant to
replace. The migration-backed regime — the **lifecycle mental model** below —
is **deliberately deferred until the architecture stops moving**, on the way to
1.0. Until then, assume no gate/migration machinery exists yet: there is no
`lifecycle/` package, and a plan file that asks for one is describing the
contributor methodology, not a maintainer dependency.

### The lifecycle mental model

This is how to *think* about a change that touches persisted state or a stable
surface — the posture a contribution should adopt even while the maintainer is
still clean-breaking. It is a mental model, not shipped machinery.

- **Name the change class.** **R** (refactor) — nothing observable outside the
  module changes; clean break, replace + delete in one commit. **B** (behavior/
  state) — changes runtime behavior or anything persisted under
  `~/.personalclaw`. **S** (stable surface) — touches a surface other code
  depends on (the `personalclaw.sdk.*` exports, the app manifest schema, inbound
  wire contracts, on-disk formats, the `personalclaw-client` package). R needs
  nothing special; B and S are the governed cases.
- **Govern a B/S change as gate → dual-path → migrate → cleanup.** Introduce a
  boolean **gate** defaulting off; build the new path behind it so the old path
  stays byte-identical when the gate is off (**dual-path**); ship an idempotent,
  snapshot-backed **migration** that moves old state to the new shape and
  verifies a concrete post-condition; then **cleanup** — flip the default on,
  delete the old path, and retire the gate. One user per install means a gate is
  just a boolean with a lifecycle, never a flag service or percentage rollout.
- **A stable-surface (S) change adds a deprecation window** — the old surface
  keeps working for two minor releases (or 90 days, whichever is longer) with a
  runtime warning, plus a CHANGELOG entry.

Until this regime lands, contributors get the *shape* of it for free by staying
additive (below); the maintainer gets it by clean-breaking under the pre-1.0
banner. Nobody hand-rolls the gate/migration runner early — it would only have
to be removed when the real one arrives.

**You are not expected to make breaking changes.** Nothing about the above asks
a contributor to break compatibility, and a PR that does will usually be asked
to change course. Write your contribution as though the lifecycle mental model
above were already in force:

- **Additive by default.** New config fields get defaults; new endpoints sit
  beside existing ones; a missing persisted field reads as today's behavior.
- **Assume someone is already running the thing you're changing.** Existing
  users have live state in `~/.personalclaw` — chats, memory, knowledge,
  credentials, config — and an upgrade must not lose it or require hand-editing
  files. Before you change anything persisted, ask what happens to a home
  directory written by the *previous* release. If the answer is "it breaks" or
  "they'd have to start over," the change isn't ready as written.
- **A breaking change needs a migration path, and the path is part of the PR.**
  If your change alters a persisted shape, a stored format, or a public route
  contract, it needs a route from old state to new that runs without user
  intervention: read the old shape and write the new one on first load
  (idempotently, so re-running is a no-op), keep the old field readable until the
  data has moved, and only then remove it. A PR that changes a format and leaves
  existing data stranded will be asked for the migration before anything else.
  State plainly in the PR what old state you tested against and what happened to
  it — a fixture home written by the previous release is the cheapest proof.
- **Don't invent gate or migration machinery.** There is no `lifecycle/` package
  yet, and hand-rolled versioning frameworks or migration *runners* will be
  rejected — they'd have to be removed when the real mechanism lands. Prefer the
  patterns already in the tree: a store's own additive column ladder
  (`knowledge/store.py`'s `_NEW_ITEM_COLUMNS`, `vector_memory.py`'s
  `_MIGRATIONS`), `CREATE TABLE IF NOT EXISTS`, tolerant `from_dict` reads, and
  backfills keyed on inspecting the data rather than on a version number. If your
  change seems to need machinery none of those cover, stop and ask.
- **Flag it instead of doing it.** If the clean fix genuinely requires changing
  a persisted shape, a public route contract, or a stored credential/state
  format, say so in an issue (or in the PR description under a clear
  "breaking change" heading) and let the maintainer decide whether to take it as
  a clean break, reshape it additively, or schedule it. Surfacing the tension is
  the contribution; you don't need to resolve it alone.
- **A rejected breaking change is not a rejected idea.** The usual outcome is
  that the maintainer lands the breaking part on their side and your PR keeps
  the rest.

If you're unsure which side of the line a change falls on, open an issue first —
that costs one round trip and saves a rewrite.

## Development setup

This is the **build-from-source / contributor** path (a git checkout with a
local venv and a Vite build). End users install a release instead — see
[docs/guides/getting-started.md](docs/guides/getting-started.md) (uv tool, pipx,
pip, or Docker), which never requires Node or a manual SPA build.

```bash
# from the repo root
python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[dev]"

# build the dashboard SPA once (rebuilds are picked up live)
make web-build

# run an isolated dev gateway (state under ./.dev-home, never ~/.personalclaw)
make serve

# one-time: repository-owned git hooks (pre-commit lint + pre-push render-smoke) + browser
npm run hooks:install
npx playwright install chromium
```

Useful Makefile targets (see `make help` for the full list):

| Target | What it does |
|---|---|
| `make test` | Run the Python test suite (pytest). |
| `make lint` / `make format` | black + isort + flake8 + mypy / auto-format. |
| `make web-build` | Build the React SPA and link `static/dist -> web/dist` (a symlink by design — never copy). |
| `make serve` / `make serve-fresh` | Dev gateway on `:10000` with an isolated `PERSONALCLAW_HOME` / same, after a fresh SPA build. |
| `make serve-web` | Vite dev server with HMR on `:3000`, proxying to a running gateway. |

Frontend tests run from the repo root: `npm run test:web` (vitest).

**The pre-commit lint hook.** Once `npm run hooks:install` has run, a
repository-owned `pre-commit` hook formats the staged Python files with black
and isort — writing the fixes back into the commit — and then runs flake8,
blocking the commit only on issues that can't be auto-fixed (an unused import,
an undefined name). It mirrors CI's `lint` job so the mechanical reformatting
that otherwise fails a PR after the fact is handled before the commit is made.
It runs only when a commit stages a `.py` file, so frontend-only and docs
commits are unaffected. mypy is not run here (a per-file type check without the
full module graph is noisy and slow) — run `make lint` for the complete gate,
and `git commit --no-verify` to bypass the hook for a deliberate WIP commit.

**The render-smoke gate.** Static checks are not enough for the frontend:
typecheck, vitest (jsdom), and `vite build` all passed while the v0.1.0
release shipped a blank dashboard — a dependency skew split the installed tree
across React 18 and React-DOM 19, and the bundle crashed at first render in a
way only a real browser exposes. So every frontend-affecting push must also
prove the **built artifact mounts**: `npm run smoke:render` serves `web/dist`
and loads the key routes in headless Chromium, asserting `#root` renders real
content with no uncaught errors and no ErrorBoundary fallback. The
repository-owned pre-push hook (`npm run hooks:install`, one-time) runs the
whole chain — clean `npm ci` (this is what catches declared-vs-resolved
lockfile skew), typecheck, vitest, build, render smoke — automatically whenever
outgoing commits touch `web/`, `package.json`, or `package-lock.json`, and CI's
`web` job repeats it on every PR. To smoke a live dev gateway instead of the
static server: `PC_SMOKE_URL=http://127.0.0.1:10000 npm run smoke:render`.

The same bar applies to dependency updates: a Dependabot or manual bump of
React or the build toolchain merges only after this gate is green — reviewing
the diff is not sufficient for changes whose failure mode is invisible to
static checks.

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
