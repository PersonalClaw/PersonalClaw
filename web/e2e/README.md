# Visual-regression + a11y harness (`e2e/`)

The **safety rail** for the dashboard's look and accessibility: every change must show **zero unintended visual diff** against a captured baseline, and every route must stay **WCAG 2 AA clean** (no serious/critical axe violations). Mirrors the personalclaw.dev pattern — `@playwright/test` + `toHaveScreenshot` with **platform-qualified baselines**.

## What it covers

- **`visual.spec.ts`** — a full-page screenshot of every nav route (18) and query-param sub-view (2) × both themes (light/dark) = 40 baselines, plus the agent orb's states (Settings › Design, every phase at 20 and 64 px) in both themes, with motion and under reduced motion = 4 more, each taken with the page's clock paused so a moving orb is the same frame every run. Baselines live in `e2e/__screenshots__/` (committed), suffixed by platform (`-darwin`, `-linux`) so mac-dev and linux-CI goldens never collide.
- **`surfaces.spec.ts`** — the UI quality check (#3941): every route, settings panel, sub-view, substate URL and opened surface in `routes.ts` and `OPENERS`, loaded **once per theme**, with the whole matrix run on that one visit as named steps — axe (`wcag2a/2aa/21a/21aa`, serious/critical block), the non-axe detectors, the accessibility tree against `__aria__/`, reduced motion, 200% text zoom, keyboard-only operation, focus order, rendered contrast of each focus ring, overflow at 1440/1024/768/412/390/320 px and 44 px targets at the four compact widths. Findings main already had live per surface and theme in `__ui-baselines__/` (shrink-only, self-clearing). `surfaceMatrix.control.spec.ts` plants one defect of each kind and requires each under its own step, in both themes; `assert-no-route-skips.mjs` holds the report to one whole scan per declared route and theme.
- **`a11y.spec.ts`** — axe and the detectors over the chat Session Map's two states that need a scripted turn (the mobile drawer and the open desktop rail), which no route visit reaches.
- **`routes.ts`** — the single source of truth for the route list (mirror of `src/app/App.tsx` NAV). Add a route here and it's snapshotted + axe-scanned automatically.
- **`helpers.ts`** — `seedTheme` (seeds `localStorage['mode']` + `prefers-color-scheme` before boot), `gotoRoute` (waits for fonts + settle), `expectRouteScreenshot`.

## Auth: the harness starts its own onboarded gateway

The built SPA gates its first render on an authenticated identity/config fetch. With **no gateway** it cannot resolve identity and renders the **onboarding screen** for every route: no `NavRail`, no page content, no ⌘K listener. axe finds nothing serious/critical there — byte-identical to a genuinely clean route. That is how 96 route scans reported a pass while visiting a surface no user ever sees, and why the only test that noticed (`command palette [opened]`) failed on its mounted-ness floor while naming the *palette*.

So `playwright.config.ts` runs **two** web servers, and needs nothing configured by hand:

1. **the gateway** — `PERSONALCLAW_HOME` (and `PERSONALCLAW_WORKSPACE`, which `PERSONALCLAW_HOME` does *not* confine) in a home of the run's own, made with `mkdtemp` under the OS temp dir and removed when the run ends, never `~/.personalclaw` and never a folder another run could be using (`runPlaces.ts`). `dashboard.user_name` is pre-seeded into `config.json` because `onboarded` is **derived** from a non-empty *server-side* name — that skips the onboarding hijack with no PUT, so no CSRF/origin dance. Readiness is the `PERSONALCLAW_READY:` line, whose token Playwright's `wait.stdout` capture group hands to `auth.setup.ts` as `PW_TOKEN`.
2. **the preview server** — `PERSONALCLAW_PORT` points its `/api` proxy at that gateway.

**Auth stays ON.** No `PERSONALCLAW_AUTH_MODE=none`: that flag swaps `csrf_middleware` for `_dev_user_middleware`, so an a11y/CSRF-adjacent finding made under it would not describe a real user. `auth.setup.ts` performs the gateway's real `/?token=` handshake through the preview origin (`vite.config.ts`'s token-proxy plugin relays the `Set-Cookie`), asserts the **shell** mounted, and writes the cookie jar every spec reuses.

The cookie jar lives in a folder of the run's own BESIDE its home (`sign-in-for-<home>/state.json`, handed to `auth.setup.ts` as `STORAGE_STATE`), made 0700 because it holds a live owner token, and removed with the home; two runs never sign each other's specs out. It is not in the home because the gateway's Doctor audits the home's state paths, and a `.auth/` folder there is one no snapshot claims: every run read Settings' Doctor card as "durability degraded". A run that cannot make the folder fails before any server starts. A run with no home of its own (`PW_NO_SERVER=1`) keeps it at `e2e/.auth/`, which is gitignored (it holds a live token). To drive an **already-running** gateway instead, start one on a scratch `PERSONALCLAW_HOME` and a port of its own (never your install's: the suite drives real turns and writes the home it reaches), then pass that port and the token from its `PERSONALCLAW_READY:` line:

```bash
PW_TOKEN=<its owner token> PW_NO_SERVER=1 PW_BASE_URL=http://localhost:<its port> npm run e2e
```

The gateway's `HOME` is a folder of the run's own too, beside its home (`user-home-for-<home>/`, made and removed with it), and every variable that names a folder outside the home (`GATEWAY_UNSET` in `runPlaces.ts`: the other agent tools' setup folders, the Hugging Face and XDG folders, and PersonalClaw's own folder overrides) is taken out of its environment before it starts. Without that, whatever the gateway finds through the home folder was the machine's: at every start it lists the shared skills folder in the real home, and Settings › Security named the real folders of the machine that ran the suite. Beside the home rather than in it, for the cookie jar's reason. `tests/rails/test_e2e_gateway_home_is_the_runs_tree.py` runs the harness's own environment and holds every one of those folders to the run's places.

Two failure modes this closes, both of which read as success:

- **The shell floor.** `helpers.assertShellMounted` (called from `gotoRoute`, so visual *and* a11y get it) fails the test when `nav[data-tour="rail"]` is absent. The rail renders only once the server reports a name, so it proves reachable + authenticated + onboarded in one assertion. `auth.setup.ts` asserts the same thing — its old `#root.innerHTML.length > 100` check *passed* on the onboarding screen, i.e. on precisely the state it existed to prevent.
- **Skips that state their reason.** An opener returns `true` or `{ skip: <why> }`. "no list rows on this route", "no contenteditable composer", and "app shell not mounted" need different next actions from whoever reads the report; one hardcoded "no seeded data" message could not tell them apart.

## One run, three ways to share the gateway

The harness builds nothing. It serves the `web/dist` that `make web-build` (or CI's `web` job) built and stamped, and the preview refuses a dist whose stamp does not match the checked-out sources (`scripts/spa_dist_freshness.py check --require-stamp`), so a stale or unstamped build fails before any test runs. Every spec but the visual rail runs in one invocation, `npx playwright test --grep-invert @visual`, in this order:

1. **`smoke`** — `smoke.spec.ts`, the render smoke: the built bundle mounts on six routes with every `/api` call answered in the page, so it reaches no gateway. Its planted render crash, widget catch and uncaught error must each be caught. The local pre-push gate runs this project alone: `PW_ONLY_SMOKE=1 npx playwright test --project=smoke --no-deps`.
2. **`chromium`** — every spec `isolation.ts` calls *shared*, in parallel against the one gateway and home.
3. **`stateful`** — the specs `isolation.ts` calls *serial* (`staleWrite`, `deployedArtifacts`: each writes what other specs read), one worker, after the shared project has finished whatever it answered. Then **`stateful-again-1`** and **`stateful-again-2`** run them once more in an order drawn from a seed: `PW_STATEFUL_SEED` when set, so a run can be replayed, otherwise a random one.

Every test in the report carries an `isolation` annotation (its mode and why), every second-pass test a `stateful-seed` annotation, and the run's `metadata` carries the seed and the order. `isolationReporter.ts` fails a run in which a spec runs in a project its mode does not allow, and `playwright.config.ts` refuses to load when a spec has no mode at all; `src/design/e2eIsolation.test.ts` holds both to planted violations. A new spec is classified in `isolation.ts` before it can run.

## Commands (run from `web/`)

| Command | What it does |
|---|---|
| `npm run e2e` | Everything, as **two** invocations: the non-visual specs, then the visual rail on its own fresh gateway — **must be zero-diff / zero serious-critical** |
| `npm run e2e:visual` | Visual regression only |
| `npm run e2e:ui` | The UI quality check only (`surfaces.spec.ts` and its control); add `--update-snapshots` to rewrite its baselines |
| `npm run e2e:a11y` | The Session Map axe scans only |
| `npm run e2e:update` | **Regenerate** the visual baselines — visual-only, INTENTIONALLY, when a real visual change is expected |
| `npm run e2e:report` | Open the last HTML report |

First-time setup on a fresh machine/CI: `npx playwright install chromium`.

## The workflow

1. **Before touching a surface**, ensure its baseline exists (`npm run e2e:visual` green).
2. Make the change.
3. `npm run e2e` — expect **zero diff**. If a change forces a **real** visual change: implement it, run `npm run e2e:update` for that surface, and **say in the change's description which baselines moved and why**, so review sees it. Never silently keep or revert a visual change.

## Notes

- The harness serves the built app via `vite preview` (it builds nothing: run `make web-build` first), proxying `/api` to the gateway it starts (`PERSONALCLAW_PORT`). Data-backed routes still render their **empty** state — the gateway's home is fresh — which is a valid baseline: we guard *chrome*, not data. What is **not** a valid baseline is the onboarding screen, which is what "no gateway" actually produced.
- **That "fresh home" is true at BOOT, not for the whole run — which is why the visual rail gets its own invocation.** Each `playwright test` gets a new, empty home, but `a11y`, `chat` and `sessionMap` drive **real** scripted turns into the one gateway every spec shares, and a turn writes flywheel state twice over (`context.py` records an allocation sample per ambient render; `controller.py` calls `run_end.capture()` at run end). `#/learning` reads both back, so it renders "not measured yet" before any turn and live numbers after one — and `fullyParallel` decides which side a mixed run captures. Visual-only is 40/40 zero-diff; a mixed run reds `learning-light`/`learning-dark` at ~14,900 px each. So `e2e` and `e2e:update` both run `visual.spec.ts` alone (selected by its `@visual` tag), `helpers.assertPristineFlywheel` refuses to compare pixels if that contract is bypassed, and `src/design/visualRailInvocation.test.ts` keeps the two scripts honest. Raising the pixel tolerance was never an option here: the two renderings are different **content**.
- **Each run has a home and two ports of its own**, so two runs on one machine never meet. The first load of the config makes the home and picks a free port for each server, then hands all three on as `PW_E2E_HOME`, `PW_PORT` and `PW_GATEWAY_PORT`, so the workers, the servers and the specs read the same ones. Each run also writes its output to a folder of its own, `test-results/run-<random>/` (`PW_OUTPUT_DIR`), so it clears only its own output when it starts, and CI still uploads everything under `test-results/`; a run folder left empty is removed when the run ends. The run's `metadata` records them (`e2eHome`, `previewPort`, `gatewayPort`, `storageState`). `PW_PORT` and `PW_GATEWAY_PORT` name a port instead of a free one; neither may be your install's port, so the harness can never drive your real install. `PW_E2E_HOME` names an existing home instead, which the run then leaves in place.
- The harness never reuses a server it did not start. `PW_REUSE_SERVER=1` lets it reuse a preview already answering on `PW_PORT`. `PW_NO_SERVER=1` skips both built-in servers (use an already-running pair). `PW_BASE_URL` points at an external server; `STORAGE_STATE` at an existing cookie jar; `PW_OUTPUT_DIR` at an output folder of your choosing.
- The gateway is launched with `../.venv/bin/personalclaw` when that exists, else `personalclaw` from `PATH`. With neither, the run fails loudly on the webServer — it does **not** fall back to a backendless SPA.
- Screenshots disable animations; `seedTheme` runs before app boot so there's no theme-flash in the capture.
