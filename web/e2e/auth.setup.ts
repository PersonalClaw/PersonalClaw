import { test as setup, expect } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { SHELL_SELECTOR } from './helpers'

// ── Auth seeding for the harness ────────────────────────────────────────────
// The built SPA gates its first render on an authenticated identity/config
// fetch — the gateway needs the owner `pc_token_<port>` cookie. A fresh
// Playwright context has no cookie, so every route renders the ONBOARDING
// screen, which is a false baseline AND a false axe pass. This setup performs
// the gateway's real token handshake once and exports a storageState the
// visual/a11y specs reuse.
//
// PW_TOKEN comes from playwright.config.ts's gateway webServer: its
// `wait.stdout` regex captures the token out of the `PERSONALCLAW_READY:` line
// into process.env.PW_TOKEN. Nothing to pass by hand for a normal run. To drive
// an already-running gateway instead:
//
//   PW_TOKEN=<owner token> PW_NO_SERVER=1 PW_BASE_URL=http://localhost:10000 \
//     npx playwright test
//
// Auth stays ON throughout — no PERSONALCLAW_AUTH_MODE=none. That flag swaps
// csrf_middleware for _dev_user_middleware, so an a11y/CSRF-adjacent finding
// made under it would not describe a real user.

const BASE = process.env.PW_BASE_URL || `http://localhost:${process.env.PW_PORT || 4318}`
const TOKEN = process.env.PW_TOKEN || ''
const OUT = process.env.STORAGE_STATE || 'e2e/.auth/state.json'

setup('authenticate', async ({ page, context }) => {
  // Write an EMPTY jar first: `use.storageState` points every project at this
  // path, and Playwright fails to launch a context if the file is missing. An
  // empty jar is exactly equivalent to no state, so the skip path below stays
  // runnable instead of erroring for an unrelated reason.
  mkdirSync(dirname(OUT), { recursive: true })
  writeFileSync(OUT, JSON.stringify({ cookies: [], origins: [] }, null, 2))

  setup.skip(!TOKEN, 'PW_TOKEN not set — no gateway token to seed the harness session with')

  // The gateway's token flow: hitting /?token=<t> sets the pc_token cookie and
  // redirects to a clean /. Through `vite preview` this is vite.config.ts's
  // token-proxy plugin relaying the gateway's Set-Cookie onto the preview
  // origin. Do it once, then persist the cookie jar.
  await page.goto(`${BASE}/?token=${encodeURIComponent(TOKEN)}`)
  await page.waitForLoadState('networkidle').catch(() => {})

  // Confirm the SHELL mounted — not merely that #root has content. The
  // onboarding screen is also several KB of #root innerHTML, so the old
  // `length > 100` check passed on the exact state this setup exists to
  // prevent. The NavRail only renders once the server reports a non-empty
  // user_name, which makes it the honest proof of "authenticated AND onboarded".
  await page.goto(`${BASE}/#/dashboard`)
  await expect(
    page.locator(SHELL_SELECTOR),
    `the app shell never mounted at ${BASE}. The gateway is unreachable, unauthenticated,\n` +
      `or not onboarded — every route would render the onboarding screen and axe would\n` +
      `report a clean tree for a surface no user ever sees.`,
  ).toBeVisible({ timeout: 15_000 })

  await context.storageState({ path: OUT })
})
