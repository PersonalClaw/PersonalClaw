# Visual-regression + a11y harness (`e2e/`)

The **safety rail** for the Design-System Consistency plan (S2/S3): every consistency fix must show **zero unintended visual diff** against a captured baseline, and every route must stay **WCAG 2 AA clean** (no serious/critical axe violations). Mirrors the personalclaw.dev pattern — `@playwright/test` + `toHaveScreenshot` with **platform-qualified baselines**.

## What it covers

- **`visual.spec.ts`** — a full-page screenshot of every nav route (16) × both themes (light/dark) = 32 baselines. Baselines live in `e2e/__screenshots__/` (committed), suffixed by platform (`-darwin`, `-linux`) so mac-dev and linux-CI goldens never collide.
- **`a11y.spec.ts`** — axe-core (`wcag2a/2aa/21a/21aa` tags) over the same 32 route×theme combos. **Fails only on serious/critical** violations (PRODUCT.md targets AA, not AAA); moderate/minor are attached to the report for triage but don't block.
- **`routes.ts`** — the single source of truth for the route list (mirror of `src/app/App.tsx` NAV). Add a route here and it's snapshotted + axe-scanned automatically.
- **`helpers.ts`** — `seedTheme` (seeds `localStorage['mode']` + `prefers-color-scheme` before boot), `gotoRoute` (waits for fonts + settle), `expectRouteScreenshot`.

## ⚠️ Auth prerequisite (baselines are dormant until seeded)

The built SPA gates its first render on an authenticated identity/config fetch — the gateway needs the owner `pc_token_<port>` cookie. A **fresh, unauthenticated** Playwright context renders a **blank shell for every route**, which is a *false* baseline. So before capturing real baselines you must seed a session:

```bash
# 1. with the gateway running + an owner token available:
PW_TOKEN=<owner token> npx playwright test e2e/auth.setup.ts   # writes e2e/.auth/state.json
# 2. capture real baselines against the authenticated, mounted app:
STORAGE_STATE=e2e/.auth/state.json PW_NO_SERVER=1 PW_BASE_URL=http://localhost:10000 npm run e2e:update
# 3. verify zero-diff / AA-clean thereafter:
STORAGE_STATE=e2e/.auth/state.json PW_NO_SERVER=1 PW_BASE_URL=http://localhost:10000 npm run e2e
```

`e2e/.auth/` is gitignored (holds a token). CI (plan 33 rails) should mint a scoped test-owner token at gateway boot, pass it as `PW_TOKEN`, run `auth.setup.ts` in global-setup, then capture `-linux` baselines. **Until seeded, the harness is wired but produces no valid baselines** — do not commit blank captures.

## Commands (run from `web/`)

| Command | What it does |
|---|---|
| `npm run e2e` | Run visual + a11y against committed baselines — **must be zero-diff / zero serious-critical** |
| `npm run e2e:visual` | Visual regression only |
| `npm run e2e:a11y` | Axe WCAG AA scan only |
| `npm run e2e:update` | **Regenerate** baselines (do this INTENTIONALLY when a real visual change is expected) |
| `npm run e2e:report` | Open the last HTML report |

First-time setup on a fresh machine/CI: `npx playwright install chromium`.

## The workflow the plan mandates

1. **Before touching a surface**, ensure its baseline exists (`npm run e2e:visual` green).
2. Make the consistency fix.
3. `npm run e2e` — expect **zero diff**. If a fix forces a **real** visual change: implement it, run `npm run e2e:update` for that surface, and **record the new baseline in the plan's `## Execution log`** for owner review. Never silently keep or revert a visual change.

## Notes

- The harness builds + serves the app via `vite preview` (proxying the gateway at `PERSONALCLAW_PORT`, default 10000). With **no live gateway** (CI), data-backed routes render their empty/loading **shell** — a valid baseline, since we guard *chrome*, not data.
- `PW_PORT` overrides the preview port (default 4318). `PW_NO_SERVER=1` skips the built-in server (use an already-running preview). `PW_BASE_URL` points at an external server.
- Screenshots disable animations; `seedTheme` runs before app boot so there's no theme-flash in the capture.
