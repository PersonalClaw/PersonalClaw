import { defineConfig, devices } from '@playwright/test'

// ── Playwright visual-regression + a11y harness ────────────────────────────
// The S2/S3 SAFETY RAIL for the design-system consistency plan: every
// consistency fix must show ZERO unintended visual diff against a captured
// baseline. Mirrors the personalclaw.dev pattern — @playwright/test +
// toHaveScreenshot with platform-qualified baselines (the -<platform> suffix
// keeps CI/mac/linux baselines separate so font/AA rendering diffs don't
// cause false failures).
//
// Baselines live in e2e/__screenshots__/ (committed). Regenerate a touched
// surface's baseline INTENTIONALLY with `npm run e2e:update` and record the
// change in the plan's Execution log for owner review — never silently keep
// or revert a real visual change.
//
// The servers: this harness drives the built SPA served by vite preview, which
// proxies /api to a gateway the harness STARTS ITSELF — isolated, onboarded and
// token-authenticated. Without a gateway the SPA cannot resolve identity, so it
// renders the ONBOARDING screen for every route: no NavRail, no shell, no ⌘K
// listener. axe then reports a clean tree for 96 surfaces it never actually
// visited, and the one test that noticed (`command palette [opened]`) failed on
// its mounted-ness floor while naming the palette instead of the missing backend.

const PORT = Number(process.env.PW_PORT || 4318)
const BASE_URL = process.env.PW_BASE_URL || `http://localhost:${PORT}`

// The harness's OWN gateway — never the real one on 10000. Fixed (not auto) so
// the preview proxy target is known before the gateway prints its READY line; if
// something else squats it, the gateway fails to bind and auth.setup's
// shell-mounted assertion fails loudly rather than scanning an empty app.
const GATEWAY_PORT = Number(process.env.PW_GATEWAY_PORT || 10437)

// Where auth.setup.ts writes the authenticated cookie jar. Gitignored (it holds
// a live owner token for the throwaway gateway).
const STORAGE_STATE = process.env.STORAGE_STATE || 'e2e/.auth/state.json'

// An ISOLATED, ONBOARDED gateway, with AUTH LEFT ON.
//  - PERSONALCLAW_HOME under the OS temp dir, wiped per run. Never ~/.personalclaw.
//  - PERSONALCLAW_WORKSPACE too: PERSONALCLAW_HOME does NOT confine workspace_dir,
//    which otherwise falls back to the real ~/workplace/personalclaw-workspace.
//  - `dashboard.user_name` pre-seeded into config.json, because `onboarded` is
//    DERIVED from a non-empty SERVER-side name (web/src/app/identity.tsx). Seeding
//    the file skips the onboarding hijack without a PUT — so no CSRF/origin dance,
//    and nothing here weakens a security control. "Keyur" matches the committed
//    visual baselines' greeting.
//  - NOT PERSONALCLAW_AUTH_MODE=none: that swaps csrf_middleware for
//    _dev_user_middleware, so any a11y/CSRF-adjacent conclusion drawn under it
//    would not describe a real user. The token flow below is the real one.
const GATEWAY_COMMAND = [
  'D="${TMPDIR:-/tmp}"; D="${D%/}/personalclaw-e2e-home"',
  'rm -rf "$D"; mkdir -p "$D/workspace"',
  `printf '%s' '{"dashboard":{"user_name":"Keyur"}}' > "$D/config.json"`,
  'PC="../.venv/bin/personalclaw"; [ -x "$PC" ] || PC=personalclaw',
  `PERSONALCLAW_HOME="$D" PERSONALCLAW_WORKSPACE="$D/workspace" exec "$PC" gateway --port ${GATEWAY_PORT} --no-open --json-ready`,
].join('\n')

export default defineConfig({
  testDir: './e2e',
  // Baselines are platform-qualified (see snapshotPathTemplate) so a mac dev
  // and linux CI keep separate goldens.
  snapshotPathTemplate: '{testDir}/__screenshots__/{testFilePath}/{arg}-{platform}{ext}',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  expect: {
    // A small pixel tolerance absorbs sub-pixel AA noise while still catching
    // real chrome changes. Tune down as baselines stabilize.
    toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: 'disabled' },
  },
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    // Deterministic viewport for stable screenshots.
    viewport: { width: 1280, height: 900 },
    // Auth: the `setup` project below mints this cookie jar against the gateway
    // the harness starts. Point PW_BASE_URL/STORAGE_STATE at your own pair (plus
    // PW_NO_SERVER=1) to drive an already-running gateway instead.
    storageState: STORAGE_STATE,
  },
  projects: [
    // Mints the authenticated session ONCE, then every spec reuses the jar. The
    // setup project must start from an EMPTY jar — an inline state object, not
    // `undefined`: a project `use` of `undefined` does not override the top-level
    // path, so setup died trying to read the file it exists to create.
    { name: 'setup', testMatch: /auth\.setup\.ts$/, use: { storageState: { cookies: [], origins: [] } } },
    { name: 'chromium', use: { ...devices['Desktop Chrome'] }, dependencies: ['setup'] },
  ],
  // Two servers: the isolated gateway, then the built SPA that proxies to it.
  webServer: process.env.PW_NO_SERVER
    ? undefined
    : [
        {
          command: GATEWAY_COMMAND,
          // Readiness is the READY line, NOT a port probe: the named capture
          // group lands in process.env.PW_TOKEN (Playwright's documented
          // behaviour), which is exactly the input e2e/auth.setup.ts already
          // expects. Deliberately no `url`/`port` — either would let the run
          // proceed on a bound-but-unauthenticated gateway with no token.
          wait: { stdout: /PERSONALCLAW_READY:.*"token":\s*"(?<pw_token>[^"]+)"/ },
          timeout: 180_000,
        },
        {
          command: `npm run build && npm run preview -- --port ${PORT} --strictPort`,
          url: BASE_URL,
          timeout: 180_000,
          reuseExistingServer: !process.env.CI,
          // vite.config.ts reads PERSONALCLAW_PORT for its /api proxy target.
          env: { PERSONALCLAW_PORT: String(GATEWAY_PORT) },
        },
      ],
})
