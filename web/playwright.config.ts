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
// The dev/preview server: this harness drives the built SPA served by vite
// preview (proxying the gateway at PERSONALCLAW_PORT, default 10000). In CI
// with no live gateway, routes render their empty/loading shell — a valid
// baseline for consistency (chrome, not data, is what we're guarding).

const PORT = Number(process.env.PW_PORT || 4318)
const BASE_URL = process.env.PW_BASE_URL || `http://localhost:${PORT}`

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
    // Auth: point the harness at a TOKENLESS dev gateway — no storage state
    // needed. The recipe (validated 2026-07-23; the earlier "blank shell"
    // blocker was the v0.1.0 dual-React bug, not auth):
    //   PERSONALCLAW_HOME="$PWD/../.dev-home-e2e" PERSONALCLAW_AUTH_MODE=none \
    //     personalclaw gateway --seed empty --seed-replace --no-open --port 10400
    //   curl -X PUT :10400/api/dashboard/config -d '{"user_name":"E2E"}'  # skip onboarding
    //   PW_NO_SERVER=1 PW_BASE_URL=http://127.0.0.1:10400 npx playwright test e2e/visual.spec.ts
    // For a token-auth gateway instead, seed a session via e2e/auth.setup.ts
    // and pass STORAGE_STATE.
    storageState: process.env.STORAGE_STATE || undefined,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  // Serve the built app for the run. Reuse an already-running server locally.
  webServer: process.env.PW_NO_SERVER
    ? undefined
    : {
        command: `npm run build && npm run preview -- --port ${PORT} --strictPort`,
        url: BASE_URL,
        timeout: 180_000,
        reuseExistingServer: !process.env.CI,
      },
})
