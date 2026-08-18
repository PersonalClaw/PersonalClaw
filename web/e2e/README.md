# Visual-regression + a11y harness (`e2e/`)

The **safety rail** for the Design-System Consistency plan (S2/S3): every consistency fix must show **zero unintended visual diff** against a captured baseline, and every route must stay **WCAG 2 AA clean** (no serious/critical axe violations). Mirrors the personalclaw.dev pattern — `@playwright/test` + `toHaveScreenshot` with **platform-qualified baselines**.

## What it covers

- **`visual.spec.ts`** — a full-page screenshot of every nav route (16) × both themes (light/dark) = 32 baselines. Baselines live in `e2e/__screenshots__/` (committed), suffixed by platform (`-darwin`, `-linux`) so mac-dev and linux-CI goldens never collide.
- **`a11y.spec.ts`** — axe-core (`wcag2a/2aa/21a/21aa` tags) over the same 32 route×theme combos. **Fails only on serious/critical** violations (PRODUCT.md targets AA, not AAA); moderate/minor are attached to the report for triage but don't block.
- **`routes.ts`** — the single source of truth for the route list (mirror of `src/app/App.tsx` NAV). Add a route here and it's snapshotted + axe-scanned automatically.
- **`helpers.ts`** — `seedTheme` (seeds `localStorage['mode']` + `prefers-color-scheme` before boot), `gotoRoute` (waits for fonts + settle), `expectRouteScreenshot`.

## Auth: the harness starts its own onboarded gateway

The built SPA gates its first render on an authenticated identity/config fetch. With **no gateway** it cannot resolve identity and renders the **onboarding screen** for every route: no `NavRail`, no page content, no ⌘K listener. axe finds nothing serious/critical there — byte-identical to a genuinely clean route. That is how 96 route scans reported a pass while visiting a surface no user ever sees, and why the only test that noticed (`command palette [opened]`) failed on its mounted-ness floor while naming the *palette*.

So `playwright.config.ts` runs **two** web servers, and needs nothing configured by hand:

1. **the gateway** — `PERSONALCLAW_HOME` (and `PERSONALCLAW_WORKSPACE`, which `PERSONALCLAW_HOME` does *not* confine) under the OS temp dir, wiped per run, never `~/.personalclaw`. `dashboard.user_name` is pre-seeded into `config.json` because `onboarded` is **derived** from a non-empty *server-side* name — that skips the onboarding hijack with no PUT, so no CSRF/origin dance. Readiness is the `PERSONALCLAW_READY:` line, whose token Playwright's `wait.stdout` capture group hands to `auth.setup.ts` as `PW_TOKEN`.
2. **the preview server** — `PERSONALCLAW_PORT` points its `/api` proxy at that gateway.

**Auth stays ON.** No `PERSONALCLAW_AUTH_MODE=none`: that flag swaps `csrf_middleware` for `_dev_user_middleware`, so an a11y/CSRF-adjacent finding made under it would not describe a real user. `auth.setup.ts` performs the gateway's real `/?token=` handshake through the preview origin (`vite.config.ts`'s token-proxy plugin relays the `Set-Cookie`), asserts the **shell** mounted, and writes the cookie jar every spec reuses.

`e2e/.auth/` is gitignored (it holds a live token). To drive an **already-running** gateway instead:

```bash
PW_TOKEN=<owner token> PW_NO_SERVER=1 PW_BASE_URL=http://localhost:10000 npm run e2e
```

Two failure modes this closes, both of which read as success:

- **The shell floor.** `helpers.assertShellMounted` (called from `gotoRoute`, so visual *and* a11y get it) fails the test when `nav[data-tour="rail"]` is absent. The rail renders only once the server reports a name, so it proves reachable + authenticated + onboarded in one assertion. `auth.setup.ts` asserts the same thing — its old `#root.innerHTML.length > 100` check *passed* on the onboarding screen, i.e. on precisely the state it existed to prevent.
- **Skips that state their reason.** An opener returns `true` or `{ skip: <why> }`. "no list rows on this route", "no contenteditable composer", and "app shell not mounted" need different next actions from whoever reads the report; one hardcoded "no seeded data" message could not tell them apart.

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

- The harness builds + serves the app via `vite preview`, proxying `/api` to the gateway it starts (`PERSONALCLAW_PORT`). Data-backed routes still render their **empty** state — the gateway's home is fresh — which is a valid baseline: we guard *chrome*, not data. What is **not** a valid baseline is the onboarding screen, which is what "no gateway" actually produced.
- `PW_PORT` overrides the preview port (default 4318). `PW_GATEWAY_PORT` overrides the gateway port (default 10437 — deliberately not 10000, so the harness can never drive your real install). `PW_NO_SERVER=1` skips both built-in servers (use an already-running pair). `PW_BASE_URL` points at an external server; `STORAGE_STATE` at an existing cookie jar.
- The gateway is launched with `../.venv/bin/personalclaw` when that exists, else `personalclaw` from `PATH`. With neither, the run fails loudly on the webServer — it does **not** fall back to a backendless SPA.
- Screenshots disable animations; `seedTheme` runs before app boot so there's no theme-flash in the capture.
