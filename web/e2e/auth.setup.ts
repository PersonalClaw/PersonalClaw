import { test as setup } from '@playwright/test'
import { writeFileSync } from 'node:fs'

// ── Auth seeding for the harness (owner / CI one-time) ─────────────────────
// The built SPA gates its first render on an authenticated identity/config
// fetch — the gateway needs the owner `pc_token_<port>` cookie. A fresh
// Playwright context has no cookie, so every route renders a BLANK shell,
// which is a false visual baseline. This setup mints/injects a session so the
// app actually mounts, then exports a storageState the visual/a11y specs reuse.
//
// It is NOT wired into the default project (so `npm run e2e` stays runnable
// without a token). Enable it when a token source exists:
//
//   PW_TOKEN=<owner token>  npx playwright test e2e/auth.setup.ts
//   STORAGE_STATE=e2e/.auth/state.json  npm run e2e:update
//
// CI (plan 33 rails): mint a scoped test-owner token during gateway boot,
// pass it as PW_TOKEN, run this setup in a global-setup, then the visual +
// axe projects capture/verify against a REAL, mounted app.

const BASE = process.env.PW_BASE_URL || 'http://localhost:10000'
const TOKEN = process.env.PW_TOKEN || ''
const OUT = process.env.STORAGE_STATE || 'e2e/.auth/state.json'

setup('authenticate', async ({ page, context }) => {
  setup.skip(!TOKEN, 'PW_TOKEN not set — provide an owner token to seed the harness session')

  // The gateway's token flow: hitting /?token=<t> sets the pc_token cookie and
  // redirects to a clean /. Do that once, then persist the cookie jar.
  await page.goto(`${BASE}/?token=${encodeURIComponent(TOKEN)}`)
  await page.waitForLoadState('networkidle').catch(() => {})
  // Confirm the app actually mounted (non-empty #root) before trusting the state.
  await page.goto(`${BASE}/#/dashboard`)
  await page.waitForFunction(() => (document.getElementById('root')?.innerHTML.length ?? 0) > 100, null, {
    timeout: 15_000,
  })

  const state = await context.storageState()
  try { writeFileSync(OUT, JSON.stringify(state, null, 2)) } catch { /* dir may need mkdir */ }
  await context.storageState({ path: OUT })
})
