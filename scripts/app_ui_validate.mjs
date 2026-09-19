#!/usr/bin/env node
// App-bundle UI validation harness — drive one or more app bundles through the
// real Store/Library/Tools surfaces in a real browser and report per-leg.
//
// WHY this exists as committed, reusable code rather than a one-off drive: every
// first-party app bundle carries the same acceptance clause ("added as a local
// Store source and driven in the real UI"), and hand-driving one bundle costs a
// full agent session. The legs below ARE that clause, mechanised once.
//
// The seven legs, per bundle:
//   1 store-source        register the bundle's staging dir as a local Store source
//   2 store-card          the Store card + detail panel describe the app
//   3 ui-install          install through the UI, clicking through the scanner's
//                         consent dialog as a user would (never bypassed)
//   4 library-and-tools   it lands in the Library and its tools render on #/tools
//   5 tool-invoke         run one of its tools from the UI and capture the result
//   6 reactivate          Deactivate → Activate round-trip
//   7 uninstall-preserves-data
//                         click the Library's real Uninstall control (confirm dialog
//                         included), reinstall, and read the app's own data back
//                         THROUGH THE TOOL REGISTRY — then prove the negative with
//                         Force uninstall. Deactivate is strictly weaker than removal,
//                         so before this leg nothing in the harness could tell an app
//                         removed-with-data-kept from one removed-and-wiped from one
//                         merely switched off (#2588).
//
// Reporting rules (see scripts/lib/app_validate_report.mjs, which owns them and is
// unit-tested): a leg that cannot run is SKIPPED **with a reason string** and never
// PASS. A leg the driver never reached is SKIPPED with an explicit not-reached
// reason, so an aborted run cannot read as a clean one.
//
// Isolation: a fresh PERSONALCLAW_HOME under the OS temp dir per run (never
// ~/.personalclaw), its own gateway on an ephemeral high port, and both torn down on
// exit. The first-party apps dir is neutralised so the Store contains ONLY the
// bundles under validation. Nothing is left behind except the screenshots and the
// report, which are the output.
//
// Usage:
//   node scripts/app_ui_validate.mjs <bundle-dir> [<bundle-dir>…] [options]
//
//   --out DIR              screenshot + report destination (default: a temp dir)
//   --port N               gateway port (default: an ephemeral free port)
//   --python PATH          interpreter that runs the gateway (default: ./.venv/bin/python)
//   --apps-root DIR        where sibling bundles live, for the model provider app
//                          (default: the parent of the first bundle)
//   --home-base DIR        parent of the throwaway PERSONALCLAW_HOME
//                          (default $PCLAW_HARNESS_HOME_BASE or the OS temp dir)
//   --model-endpoint URL   model endpoint (default $PCLAW_HARNESS_MODEL_ENDPOINT
//                          or http://127.0.0.1:11434)
//   --model NAME           model id to pin (default $PCLAW_HARNESS_MODEL)
//   --model-app NAME|DIR   provider app that wires the endpoint
//                          (default $PCLAW_HARNESS_MODEL_APP or ollama-models)
//   --model-type TYPE      provider type that app registers
//                          (default $PCLAW_HARNESS_MODEL_TYPE or ollama)
//   --no-model             skip model wiring entirely
//   --keep                 leave the temp home + gateway logs in place (debugging)
//   --headed               run the browser headed
//
// Requires a built SPA served by the gateway (`make web-build`) and the Playwright
// Chromium binary (`npx playwright install chromium`).

import { spawn } from 'node:child_process'
import { createServer } from 'node:net'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync, symlinkSync, readFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

import {
  LEGS, STATUS, APP_DATA, newLegs, passLeg, failLeg, skipLeg, noteLeg,
  classifyAppData, shapeBundleReport, shapeReport, formatReport,
} from './lib/app_validate_report.mjs'
import { fillRequiredArgs } from './lib/app_validate_form.mjs'

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const SHELL_SELECTOR = 'nav[data-tour="rail"]'
/** Environment-lacking signatures in a tool error. A tool that fails because this
 *  machine has no credential/daemon is a SKIPPED with a reason, not a product FAIL —
 *  and faking the credential is never an option. */
const ENV_LACKING = [
  /not authenticated/i, /gh auth/i, /auth(entication)? (required|failed)/i,
  /api[_ -]?key/i, /credential/i, /unauthorized/i, /\b401\b/, /\b403\b/,
  /no model provider/i, /model .*not (configured|available|found)/i,
  /connection refused/i, /ECONNREFUSED/, /could not connect/i,
  /not configured/i, /command not found/i, /executable .*not found/i,
  /no such file or directory: '?(gh|git|npm)/i,
]

function log(msg) { console.log(`[app-ui-validate] ${msg}`) }
function warn(msg) { console.error(`[app-ui-validate] ${msg}`) }

// ── argv ─────────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const opts = {
    bundles: [], out: '', port: 0, python: '', appsRoot: '',
    homeBase: process.env.PCLAW_HARNESS_HOME_BASE || os.tmpdir(),
    modelEndpoint: process.env.PCLAW_HARNESS_MODEL_ENDPOINT || 'http://127.0.0.1:11434',
    model: process.env.PCLAW_HARNESS_MODEL || '',
    modelApp: process.env.PCLAW_HARNESS_MODEL_APP || 'ollama-models',
    modelType: process.env.PCLAW_HARNESS_MODEL_TYPE || 'ollama',
    noModel: false, keep: false, headed: false,
  }
  const takesValue = new Set(['--out', '--port', '--python', '--apps-root', '--home-base',
    '--model-endpoint', '--model', '--model-app', '--model-type'])
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (takesValue.has(a)) {
      const v = argv[++i]
      if (v === undefined) throw new Error(`${a} needs a value`)
      if (a === '--out') opts.out = path.resolve(v)
      else if (a === '--port') opts.port = Number(v)
      else if (a === '--python') opts.python = path.resolve(v)
      else if (a === '--apps-root') opts.appsRoot = path.resolve(v)
      else if (a === '--home-base') opts.homeBase = path.resolve(v)
      else if (a === '--model-endpoint') opts.modelEndpoint = v
      else if (a === '--model') opts.model = v
      else if (a === '--model-app') opts.modelApp = v
      else if (a === '--model-type') opts.modelType = v
    } else if (a === '--no-model') opts.noModel = true
    else if (a === '--keep') opts.keep = true
    else if (a === '--headed') opts.headed = true
    else if (a.startsWith('--')) throw new Error(`unknown option ${a}`)
    else opts.bundles.push(path.resolve(a))
  }
  if (!opts.bundles.length) throw new Error('at least one bundle directory is required')
  return opts
}

// ── gateway lifecycle ────────────────────────────────────────────────────────

/** An ephemeral free loopback port. Never 10000: that port belongs to a
 *  long-running dev gateway on this machine and must not be disturbed. */
async function freePort() {
  for (let attempt = 0; attempt < 20; attempt++) {
    const port = await new Promise((resolve, reject) => {
      const srv = createServer()
      srv.once('error', reject)
      srv.listen(0, '127.0.0.1', () => {
        const p = srv.address().port
        srv.close(() => resolve(p))
      })
    })
    if (port !== 10000 && port >= 1024) return port
  }
  throw new Error('could not find a free ephemeral port')
}

function resolvePython(explicit) {
  const candidates = [
    explicit,
    process.env.PCLAW_HARNESS_PYTHON,
    path.join(REPO_ROOT, '.venv', 'bin', 'python'),
  ].filter(Boolean)
  for (const c of candidates) if (existsSync(c)) return c
  return 'python3'
}

/** Boot an isolated gateway. Resolves once the READY line lands (which also
 *  carries the owner token — the SPA renders onboarding without it, so a port
 *  probe would let the run proceed against an unauthenticated gateway). */
function startGateway({ python, home, port, firstPartyDir, logSink }) {
  const env = {
    ...process.env,
    PERSONALCLAW_HOME: home,
    PERSONALCLAW_WORKSPACE: path.join(home, 'workspace'),
    // A path that does not exist DISABLES the always-present first-party source
    // (catalog._default_local_sources), so the Store shows only what this harness
    // registers. Without it every sibling bundle in the apps tree floods the grid.
    PERSONALCLAW_FIRST_PARTY_APPS_DIR: firstPartyDir,
    PYTHONPATH: [path.join(REPO_ROOT, 'src'), process.env.PYTHONPATH].filter(Boolean).join(':'),
  }
  const proc = spawn(python, ['-m', 'personalclaw', 'gateway', '--port', String(port), '--no-open', '--json-ready'],
    { cwd: REPO_ROOT, env, stdio: ['ignore', 'pipe', 'pipe'] })

  return new Promise((resolve, reject) => {
    let buf = ''
    let settled = false
    const timer = setTimeout(() => {
      if (settled) return
      settled = true
      proc.kill('SIGKILL')
      reject(new Error(`gateway did not report ready within 180s. Output:\n${buf.slice(-4000)}`))
    }, 180_000)

    const onChunk = (chunk) => {
      const text = String(chunk)
      buf += text
      logSink.push(text)
      const m = buf.match(/PERSONALCLAW_READY:\s*(\{.*\})/)
      if (m && !settled) {
        settled = true
        clearTimeout(timer)
        let token = ''
        try { token = JSON.parse(m[1]).token ?? '' } catch { /* token stays empty */ }
        resolve({ proc, token })
      }
    }
    proc.stdout.on('data', onChunk)
    proc.stderr.on('data', onChunk)
    proc.on('exit', (code) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      reject(new Error(`gateway exited with code ${code} before reporting ready. Output:\n${buf.slice(-4000)}`))
    })
  })
}

async function stopGateway(proc) {
  if (!proc || proc.exitCode !== null) return
  proc.kill('SIGTERM')
  await new Promise((resolve) => {
    const t = setTimeout(() => { proc.kill('SIGKILL'); resolve() }, 10_000)
    proc.once('exit', () => { clearTimeout(t); resolve() })
  })
}

// ── gateway API (read-only checks + setup that is NOT part of a validated leg) ──

/** The gateway's own API. `token` rides the query string (the documented
 *  non-browser path); POSTs also carry an allowed Origin for the CSRF check.
 *  Used ONLY for setup and for cross-checking what the UI showed — never as a
 *  substitute for a UI leg. */
function apiClient(base, token) {
  const call = async (method, route, body) => {
    const sep = route.includes('?') ? '&' : '?'
    const res = await fetch(`${base}${route}${sep}token=${encodeURIComponent(token)}`, {
      method,
      headers: { 'Content-Type': 'application/json', Origin: base },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    let json = null
    try { json = await res.json() } catch { /* not every response is JSON */ }
    return { status: res.status, ok: res.ok, json }
  }
  return {
    get: (r) => call('GET', r),
    post: (r, b) => call('POST', r, b),
    del: (r) => call('DELETE', r),
  }
}

// ── screenshots ──────────────────────────────────────────────────────────────

function shotter(page, dir) {
  let n = 0
  mkdirSync(dir, { recursive: true })
  return async (name) => {
    n += 1
    const file = path.join(dir, `${String(n).padStart(2, '0')}-${name}.png`)
    try { await page.screenshot({ path: file, fullPage: false }); return file }
    catch (err) { warn(`screenshot ${name} failed: ${err.message}`); return '' }
  }
}

// ── UI helpers ───────────────────────────────────────────────────────────────

/** Navigate and wait for the app SHELL, not for the `load` event.
 *
 *  `waitUntil: 'load'` was measured to be the wrong gate (2026-09-19, load average 89 on the
 *  dev host): `document.readyState` sat at `interactive` for over 45s while the dashboard
 *  rendered, kept lazy-loading route chunks and kept polling, so the initial resource set
 *  never settled and the `load` event never arrived. Every leg then reported
 *  `harness error: page.goto timeout` — a false FAIL blamed on the bundle, on a page that was
 *  visibly usable. `domcontentloaded` plus the shell-visible wait below is both more tolerant
 *  of a busy machine AND a stronger claim: it asserts the product's own shell is on screen
 *  rather than that the browser stopped fetching. */
async function gotoRoute(page, base, route) {
  await page.goto(`${base}/#${route}`, { waitUntil: 'domcontentloaded', timeout: 45_000 })
  await page.locator(SHELL_SELECTOR).waitFor({ state: 'visible', timeout: 45_000 })
  await page.waitForTimeout(500)
}

/** The Store/Library card for `displayName`. Anchored on the card's own hit-target
 *  button (`<name> — details`), then folded up to the innermost element containing
 *  it — which is the card root. Anchoring on visible text instead would match the
 *  detail panel and the source rail too. */
function cardFor(page, displayName) {
  return page.locator('div').filter({ has: page.locator(`button[aria-label="${displayName} — details"]`) }).last()
}

/** The docked inspector, named by its title (SidePanel is a role=region landmark).
 *  Scoping to it matters: the Store grid behind the panel carries an "Install"
 *  button per card, so an unscoped button lookup can click the wrong app. */
function sidePanel(page, title) {
  return page.getByRole('region', { name: title })
}

/** A tool row's clickable body on the Tools page. The row button's accessible name
 *  is its whole subtree (name + description + param count), so a by-name role
 *  lookup never matches; the monospace identifier's `title` is the stable anchor. */
function toolRow(page, name) {
  return page.locator(`button:has(span[title="${name}"])`)
}

/** The Tools-page group header that owns `provider`, as rendered text — it carries
 *  the provenance badge, which is worth recording verbatim. */
async function toolGroupHeader(page, provider) {
  const label = page.getByText(provider, { exact: true }).first()
  if (!(await label.count())) return ''
  return (await label.locator('xpath=..').innerText().catch(() => '')).replace(/\s+/g, ' ').trim()
}

/** Click a candidate control if it is actually visible, then wait for the Manage
 *  Sources panel. Every step is visibility-gated: a hidden match (the overflow
 *  menu's copy of a header control) would otherwise burn a full click timeout.
 *
 *  The FIRST candidate is waited for rather than sampled. A bare `count()` reads the page
 *  in the frame it happens to run in, so on a loaded machine (measured at load average 88)
 *  the Store header had not rendered yet and leg 1 failed with "no Manage Sources control
 *  was reachable" — a false FAIL against a control that appears a second later. Later
 *  candidates are still sampled: by then the header exists and their absence is real. */
async function openManageSources(page) {
  const candidates = [
    page.locator('button[title="Manage Sources"]'),
    page.locator('button', { hasText: 'Add source' }),
  ]
  for (const [index, candidate] of candidates.entries()) {
    const visible = candidate.locator('visible=true').first()
    if (index === 0) {
      await visible.waitFor({ state: 'visible', timeout: 20_000 }).catch(() => {})
    }
    if (!(await visible.count())) continue
    await visible.click({ timeout: 5000 }).catch(() => {})
    const panel = sidePanel(page, 'Manage Sources').first()
    const opened = await panel.waitFor({ state: 'visible', timeout: 5000 }).then(() => true).catch(() => false)
    if (opened) return true
  }
  return false
}

async function closePanel(page) {
  const close = page.getByRole('button', { name: 'Close', exact: true })
  if (await close.count()) { await close.first().click().catch(() => {}) }
  else await page.keyboard.press('Escape')
  await page.waitForTimeout(500)
}

// ── the drive ────────────────────────────────────────────────────────────────

async function driveBundle({ browser, base, api, bundleDir, outDir, home, model }) {
  const bundle = path.basename(bundleDir)
  const legs = newLegs()
  const consoleErrors = []
  const pageErrors = []
  let manifest = null
  let notReached = ''

  try {
    manifest = JSON.parse(readFileSync(path.join(bundleDir, 'app.json'), 'utf8'))
  } catch (err) {
    failLeg(legs, 'store-source', `cannot read ${bundle}/app.json: ${err.message}`)
    return shapeBundleReport({ bundle, path: bundleDir, legs, consoleErrors, pageErrors,
      notReachedReason: 'the bundle manifest could not be read, so nothing could be driven' })
  }
  const appName = manifest.name ?? bundle
  const displayName = manifest.displayName ?? appName

  // A local Store source is a DIRECTORY OF app subdirs, so a single bundle needs a
  // staging parent. Symlinked, never copied: the bundle under validation must be the
  // bytes on disk, and the harness must not be able to modify it.
  const stagingRoot = path.join(home, 'harness-sources', bundle)
  mkdirSync(stagingRoot, { recursive: true })
  const link = path.join(stagingRoot, bundle)
  if (!existsSync(link)) symlinkSync(bundleDir, link)

  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 400)) })
  page.on('pageerror', (e) => pageErrors.push(String(e.message).split('\n')[0].slice(0, 400)))
  const shot = shotter(page, path.join(outDir, bundle))

  try {
    // `domcontentloaded`, not `load` — see gotoRoute for the measurement.
    await page.goto(`${base}/?token=${encodeURIComponent(api.token)}`, { waitUntil: 'domcontentloaded', timeout: 45_000 })
    await gotoRoute(page, base, '/apps?view=store')

    // ── leg 1: register the local source ──────────────────────────────────
    if (!(await openManageSources(page))) {
      failLeg(legs, 'store-source', 'no "Manage Sources" control was reachable on the Store tab')
      notReached = 'the local source could not be registered, so no later leg could run'
      throw new LegAbort()
    }
    const sourcesPanel = sidePanel(page, 'Manage Sources').first()
    const localInput = sourcesPanel.locator('input[name="app-local-source"]')
    await localInput.waitFor({ state: 'visible', timeout: 10_000 })
    await localInput.fill(stagingRoot)
    // The Local sources section's own Add button — the git section has one too, and
    // it sits earlier in the DOM, so anchor on the input rather than on the label.
    await localInput.locator('xpath=following::button[1]').click({ timeout: 10_000 })
    await page.waitForTimeout(1500)
    const sourcesShot = await shot('store-source')
    const registered = (await api.get('/api/apps/local-sources')).json?.sources ?? []
    if (!registered.includes(stagingRoot)) {
      failLeg(legs, 'store-source', `the UI accepted the path but /api/apps/local-sources does not list it (got ${JSON.stringify(registered)})`,
        { screenshots: [sourcesShot] })
      notReached = 'the local source was not registered, so no later leg could run'
      throw new LegAbort()
    }
    const listedInPanel = await page.getByText(stagingRoot, { exact: false }).count()
    passLeg(legs, 'store-source', {
      screenshots: [sourcesShot],
      details: { stagingRoot, listedInPanel: listedInPanel > 0 },
      notes: listedInPanel > 0 ? [] : ['the registered path is not echoed in the Manage Sources panel'],
    })
    await closePanel(page)

    // ── leg 2: the Store card + detail panel ──────────────────────────────
    // Filter the grid to THIS local source (the Store's own `ssrc` rail filter).
    // Two reasons: the gateway ships a non-removable git source carrying the same
    // app names, so an unfiltered grid can show the REMOTE copy of the bundle; and
    // the catalog read behind the filter includes a network registry fetch, so the
    // first read after registering a source can still be stale — hence the retry.
    const srcKey = `local:${stagingRoot}`
    const card = cardFor(page, displayName)
    let found = false
    for (let attempt = 0; attempt < 4 && !found; attempt++) {
      if (attempt === 0) await gotoRoute(page, base, `/apps?view=store&ssrc=${encodeURIComponent(srcKey)}`)
      else { await page.reload({ waitUntil: 'domcontentloaded' }); await page.locator(SHELL_SELECTOR).waitFor({ state: 'visible', timeout: 45_000 }) }
      found = await card.first().waitFor({ state: 'visible', timeout: 12_000 }).then(() => true).catch(() => false)
    }
    await page.waitForTimeout(700)
    if (!found) {
      const missShot = await shot('store-card-missing')
      failLeg(legs, 'store-card', `no Store card for "${displayName}" after registering the source`, { screenshots: [missShot] })
      notReached = 'the app never surfaced in the Store, so it could not be installed'
      throw new LegAbort()
    }
    const cardText = (await card.first().innerText()).replace(/\s+/g, ' ').trim()
    const dividerLabel = path.basename(stagingRoot)
    const dividerShown = await page.getByText(dividerLabel, { exact: true }).count()
    const cardShot = await shot('store-card')

    await card.first().click()
    await page.waitForTimeout(900)
    const panel = sidePanel(page, displayName)
    const panelPresent = await panel.count()
    const panelText = panelPresent ? (await panel.first().innerText()).replace(/\s+/g, ' ').trim() : ''
    const detailShot = await shot('store-card-detail')

    const catalogEntry = ((await api.get('/api/apps/catalog')).json?.localApps ?? [])
      .find((e) => e.name === appName) ?? null
    const declaredPerms = Object.entries(manifest.permissions ?? {})
    const missing = []
    if (!cardText.includes(displayName)) missing.push('the card does not show the display name')
    const desc = String(manifest.description ?? '').slice(0, 40)
    if (desc && !cardText.includes(desc.slice(0, 24))) missing.push('the card does not show the manifest description')
    if (!dividerShown) missing.push(`no source divider named "${dividerLabel}" groups the card`)
    if (!panelPresent) missing.push('the detail panel did not open')
    const permsShown = declaredPerms.length === 0
      || /Permissions the gateway enforces|granted no gateway capability/i.test(panelText)
    if (!permsShown) missing.push('the detail panel does not render the declared permissions')
    const typeShown = !catalogEntry?.isProvider || panelText.includes('provider')
    if (!typeShown) missing.push('the detail panel does not name the app type')

    if (missing.length) {
      failLeg(legs, 'store-card', missing.join('; '),
        { screenshots: [cardShot, detailShot], details: { cardText, panelText, sourceKind: catalogEntry?.sourceKind ?? null } })
    } else {
      passLeg(legs, 'store-card', {
        screenshots: [cardShot, detailShot],
        details: {
          cardText, panelText,
          sourceKind: catalogEntry?.sourceKind ?? null,
          providerType: catalogEntry?.providerType ?? '',
          declaredPermissions: Object.fromEntries(declaredPerms),
          sourceDivider: dividerLabel,
        },
      })
    }

    // ── leg 3: install through the UI, consent included ───────────────────
    const toolsBefore = new Set((((await api.get('/api/tools')).json?.tools) ?? []).map((t) => `${t.provider}::${t.name}`))
    const installBtn = panel.getByRole('button', { name: 'Install', exact: true })
    if (!(await installBtn.count())) {
      const noBtnShot = await shot('ui-install-no-button')
      failLeg(legs, 'ui-install', 'the Store detail panel offers no Install button', { screenshots: [noBtnShot] })
      notReached = 'the app was never installed, so no post-install leg could run'
      throw new LegAbort()
    }
    await installBtn.first().click()

    const consentModal = page.locator('[role="dialog"]').filter({ hasText: `Install ${displayName}` })
    const anywayBtn = page.getByRole('button', { name: 'Install anyway' })
    let consentShot = ''
    let consentText = ''
    let sawConsent = false
    let installedApp = null
    // Three outcomes to watch for at once: the scanner's consent dialog, an install
    // that went straight through, and a terminal refusal. Racing two fixed timeouts
    // instead made the no-dialog path a coin flip between them.
    for (let i = 0; i < 60; i++) {
      if (await anywayBtn.first().isVisible().catch(() => false)) { sawConsent = true; break }
      installedApp = ((await api.get('/api/apps')).json?.apps ?? []).find((a) => a.name === appName) ?? null
      if (installedApp) break
      if (await page.getByText(/cannot be installed|scanner flagged dangerous/i).count()) break
      await page.waitForTimeout(1000)
    }
    if (sawConsent) {
      // The dialog animates in (opacity/scale). Screenshotting the frame the button
      // first became visible captured a half-transparent ghost that reads as an
      // unreadable dialog — a harness artifact, not a product defect. Let it settle.
      await page.waitForTimeout(1200)
      consentText = (await consentModal.first().innerText().catch(() => '')).replace(/\s+/g, ' ').trim()
      consentShot = await shot('ui-install-consent')
      noteLeg(legs, 'ui-install', { screenshots: [consentShot], notes: ['the scanner raised a consent dialog; clicked through it as a user'] })
      await anywayBtn.first().click()
    }
    // Installation copies the bundle and may install python deps — give it room.
    for (let i = 0; i < 90 && !installedApp; i++) {
      await page.waitForTimeout(2000)
      installedApp = ((await api.get('/api/apps')).json?.apps ?? []).find((a) => a.name === appName) ?? null
      if (installedApp) break
      if (await page.getByText(/cannot be installed|scanner flagged dangerous/i).count()) break
    }
    // A TERMINAL refusal renders the same dialog with the findings and no "Install
    // anyway". Capture its text before screenshotting: the findings are the whole
    // reason the bundle cannot be driven, so a bare "not installed" is not a report.
    let refusalText = ''
    if (!installedApp) {
      await page.waitForTimeout(1200)
      refusalText = (await consentModal.first().innerText().catch(() => '')).replace(/\s+/g, ' ').trim()
    }
    const installShot = await shot('ui-install-result')
    if (!installedApp) {
      const errText = await page.locator('.text-negative, .text-danger').allInnerTexts().catch(() => [])
      failLeg(legs, 'ui-install', `the app is not installed after clicking Install${sawConsent ? ' and consenting' : ''}${errText.length ? `: ${errText.join(' | ').slice(0, 300)}` : ''}`,
        { screenshots: [installShot], details: { consentText, refusalText } })
      notReached = 'the install never completed, so no post-install leg could run'
      throw new LegAbort()
    }
    passLeg(legs, 'ui-install', {
      screenshots: [installShot],
      details: { consentDialogShown: sawConsent, consentText, version: installedApp.version ?? '' },
    })

    // ── leg 4: Library + Tools page ───────────────────────────────────────
    // Both pages fetch after mount, so both need a real wait rather than a count()
    // on the first frame — otherwise a slow render reads as a missing card, which is
    // exactly the false FAIL this harness exists to avoid.
    await gotoRoute(page, base, '/apps?view=library')
    const libCard = cardFor(page, displayName)
    const inLibrary = await libCard.first().waitFor({ state: 'visible', timeout: 20_000 })
      .then(() => true).catch(() => false)
    // Cards animate in (opacity 0 → 1 with a per-index delay); screenshotting the
    // first visible frame captured a dimmed card that reads as "disabled".
    await page.waitForTimeout(900)
    const libShot = await shot('library')

    const toolsAfter = (((await api.get('/api/tools')).json?.tools) ?? [])
    const contributed = toolsAfter.filter((t) => !toolsBefore.has(`${t.provider}::${t.name}`))
    const providers = [...new Set(contributed.map((t) => t.provider))]

    await gotoRoute(page, base, '/tools')
    const renderedToolNames = []
    const groupHeaders = {}
    if (contributed.length) {
      await toolRow(page, contributed[0].name).first().waitFor({ state: 'attached', timeout: 20_000 })
        .catch(() => {})
      for (const t of contributed) {
        if (await toolRow(page, t.name).count()) renderedToolNames.push(t.name)
      }
      for (const p of providers) groupHeaders[p] = await toolGroupHeader(page, p)
    }
    const toolsShot = await shot('library-and-tools')

    if (!inLibrary) {
      failLeg(legs, 'library-and-tools', `the installed app has no card in the Library tab`, { screenshots: [libShot, toolsShot] })
    } else if (!contributed.length) {
      skipLeg(legs, 'library-and-tools', 'the app landed in the Library, but it contributes no tools for the Tools page to render',
        { screenshots: [libShot, toolsShot], details: { library: 'PASS', toolProviders: providers } })
    } else if (!renderedToolNames.length) {
      failLeg(legs, 'library-and-tools', `the app registered ${contributed.length} tool(s) (${contributed.slice(0, 4).map((t) => t.name).join(', ')}) but none render on the Tools page`,
        { screenshots: [libShot, toolsShot], details: { library: 'PASS', toolProviders: providers } })
    } else {
      passLeg(legs, 'library-and-tools', {
        screenshots: [libShot, toolsShot],
        details: { library: 'PASS', toolProviders: providers, toolCount: contributed.length, renderedTools: renderedToolNames, groupHeaders },
      })
    }

    // ── leg 5: invoke a tool from the UI ──────────────────────────────────
    if (!contributed.length) {
      skipLeg(legs, 'tool-invoke', 'the app contributes no tools, so there is nothing to invoke from the UI')
    } else {
      const target = pickInvokableTool(contributed)
      if (!target) {
        skipLeg(legs, 'tool-invoke', `every tool this app contributes requires arguments the harness cannot synthesise (${contributed.map((t) => t.name).join(', ')})`)
      } else {
        const res = await runToolFromUi({ page, base, tool: target, shot })
        const details = { tool: target.name, provider: target.provider, args: res.args, output: res.output.slice(0, 1200) }
        if (res.status === 'ok') {
          passLeg(legs, 'tool-invoke', { screenshots: res.screenshots, details })
        } else if (res.status === 'env') {
          skipLeg(legs, 'tool-invoke', `${target.name} ran from the UI but this environment lacks what it needs: ${res.output.slice(0, 240)}`,
            { screenshots: res.screenshots, details })
        } else if (res.status === 'error') {
          failLeg(legs, 'tool-invoke', `${target.name} ran from the UI and returned an error: ${res.output.slice(0, 240)}`,
            { screenshots: res.screenshots, details })
        } else {
          failLeg(legs, 'tool-invoke', `${target.name} could not be run from the UI: ${res.output.slice(0, 240)}`,
            { screenshots: res.screenshots, details })
        }
      }
    }

    // ── leg 6: deactivate → reactivate ────────────────────────────────────
    const round = await deactivateReactivate({ page, base, api, appName, displayName, shot })
    if (round.ok) passLeg(legs, 'reactivate', { screenshots: round.screenshots, details: round.details })
    else if (round.skip) skipLeg(legs, 'reactivate', round.skip, { screenshots: round.screenshots, details: round.details })
    else failLeg(legs, 'reactivate', round.reason, { screenshots: round.screenshots, details: round.details })

    // ── leg 7: uninstall keeps the data; force uninstall does not ─────────
    const removal = await uninstallPreservesData({
      page, base, api, appName, displayName, source: link, shot, contributed,
    })
    if (removal.ok) passLeg(legs, 'uninstall-preserves-data', { screenshots: removal.screenshots, details: removal.details })
    else if (removal.skip) skipLeg(legs, 'uninstall-preserves-data', removal.skip, { screenshots: removal.screenshots, details: removal.details })
    else failLeg(legs, 'uninstall-preserves-data', removal.reason, { screenshots: removal.screenshots, details: removal.details })
  } catch (err) {
    if (!(err instanceof LegAbort)) {
      const crashShot = await shot('crash')
      const pending = legs.find((l) => l.status === STATUS.PENDING)
      if (pending) failLeg(legs, pending.id, `harness error: ${err.message.split('\n')[0]}`, { screenshots: [crashShot] })
      notReached = notReached || `the drive stopped early: ${err.message.split('\n')[0]}`
    }
  } finally {
    await page.close().catch(() => {})
    await context.close().catch(() => {})
    // Per-bundle cleanup so the next bundle sees a Store with only its own card.
    // `force=1` because a plain DELETE is "deactivate, keep files" — the app would
    // stay in the Library and the next bundle's Library assertion would read a
    // neighbour's card. Only ever applied to what this run installed.
    await api.del(`/api/apps/${encodeURIComponent(appName)}?force=1`).catch(() => {})
    await api.del(`/api/apps/local-sources?path=${encodeURIComponent(stagingRoot)}`).catch(() => {})
  }

  const report = shapeBundleReport({
    bundle, path: bundleDir, legs, consoleErrors, pageErrors,
    app: { name: appName, displayName, version: manifest.version ?? '' },
    notReachedReason: notReached,
  })
  if (model && !model.configured) {
    for (const leg of report.legs) {
      if (leg.id === 'tool-invoke' && leg.status === STATUS.SKIPPED) {
        leg.reason = `${leg.reason} (no model provider is wired: ${model.reason})`
      }
    }
  }
  return report
}

/** Signals "this leg failed and the rest of the bundle cannot be driven" without
 *  conflating it with an unexpected harness crash. */
class LegAbort extends Error {}

/** Every required argument is a primitive the harness can fill with a self-describing
 *  placeholder (see scripts/lib/app_validate_form.mjs). Vacuously true for a tool with no
 *  required arguments. */
function argsAreFillable(tool) {
  return (tool.parameters?.required ?? []).every((k) => {
    const s = tool.parameters?.properties?.[k]
    return s && ['string', 'integer', 'number', 'boolean'].includes(s.type)
  })
}

/** Prefer a tool with no required arguments — bundle-agnostic and side-effect-light.
 *  Otherwise a tool whose required arguments are all primitives the harness can fill
 *  with a self-describing placeholder. */
function pickInvokableTool(tools) {
  const noArgs = tools.filter((t) => (t.parameters?.required ?? []).length === 0)
  if (noArgs.length) return noArgs.find((t) => /list|status|ls|show|get|search/i.test(t.name)) ?? noArgs[0]
  return tools.filter(argsAreFillable)[0] ?? null
}

/** Open the tool inspector, expand "Try it", fill any required primitives, and go
 *  through "Run tool" → "Confirm & run" — the same two clicks a user makes. */
async function runToolFromUi({ page, base, tool, shot }) {
  const screenshots = []
  await gotoRoute(page, base, '/tools')
  const search = page.getByPlaceholder('Search tools').first()
  if (await search.count()) { await search.fill(tool.name); await page.waitForTimeout(900) }
  const row = toolRow(page, tool.name)
  await row.first().waitFor({ state: 'attached', timeout: 20_000 }).catch(() => {})
  if (!(await row.count())) {
    screenshots.push(await shot('tool-invoke-no-row'))
    return { status: 'blocked', output: `no clickable row for ${tool.name} on the Tools page`, args: {}, screenshots }
  }
  await row.first().click()
  await page.waitForTimeout(800)

  const tryIt = page.getByRole('button', { name: /Try it/ })
  if (!(await tryIt.count())) {
    screenshots.push(await shot('tool-invoke-no-try-it'))
    return { status: 'blocked', output: 'the tool inspector offers no "Try it" panel', args: {}, screenshots }
  }
  await tryIt.first().click()
  await page.waitForTimeout(500)

  // Fill by ACCESSIBLE NAME and read every value back — see scripts/lib/app_validate_form.mjs.
  // A required argument the harness cannot enter BLOCKS the leg: running the tool anyway
  // yields its own "needs an X" error, which the report would then blame on the bundle.
  const { args, unfilled } = await fillRequiredArgs(page, tool)
  screenshots.push(await shot('tool-invoke-form'))
  if (unfilled.length) {
    return {
      status: 'blocked',
      output: `the harness could not enter ${unfilled.length} required argument(s) in the "Try it" form, so the run would not have tested ${tool.name}: ${unfilled.join('; ')}`,
      args, screenshots,
    }
  }

  const runBtn = page.getByRole('button', { name: /^Run tool$/ })
  if (!(await runBtn.count())) {
    return { status: 'blocked', output: 'the "Try it" panel offers no "Run tool" button', args, screenshots }
  }
  await runBtn.first().click()
  const confirmBtn = page.getByRole('button', { name: /Confirm & run/ })
  await confirmBtn.first().waitFor({ state: 'visible', timeout: 10_000 })
  await confirmBtn.first().click()

  const okBlock = page.getByText('Success', { exact: true })
  const errBlock = page.getByText('Error', { exact: true })
  let outcome = ''
  for (let i = 0; i < 120; i++) {
    await page.waitForTimeout(1000)
    if (await okBlock.count()) { outcome = 'ok'; break }
    if (await errBlock.count()) { outcome = 'error'; break }
  }
  screenshots.push(await shot('tool-invoke-result'))
  if (!outcome) {
    return { status: 'blocked', output: 'the run never produced a Success or Error block (still spinning after 120s)', args, screenshots }
  }
  const panelText = await page.locator('[class*="max-h-96"]').last().innerText().catch(() => '')
  const output = panelText.replace(/\s+/g, ' ').trim()
  if (outcome === 'ok') return { status: 'ok', output, args, screenshots }
  const envLacking = ENV_LACKING.some((re) => re.test(output))
  return { status: envLacking ? 'env' : 'error', output, args, screenshots }
}

async function deactivateReactivate({ page, base, api, appName, displayName, shot }) {
  const screenshots = []
  const details = {}
  await gotoRoute(page, base, '/apps?view=library')
  const card = cardFor(page, displayName)
  await card.first().waitFor({ state: 'attached', timeout: 20_000 }).catch(() => {})
  if (!(await card.count())) {
    screenshots.push(await shot('reactivate-no-card'))
    return { reason: 'the app has no Library card to open, so the state toggle is unreachable', screenshots, details }
  }
  await card.first().click()
  await page.waitForTimeout(900)
  const panel = sidePanel(page, displayName)

  const deactivate = panel.getByRole('button', { name: 'Deactivate', exact: true })
  if (!(await deactivate.count())) {
    screenshots.push(await shot('reactivate-no-toggle'))
    return { reason: 'the app detail panel offers no Deactivate control', screenshots, details }
  }
  await deactivate.first().click()
  let off = false
  for (let i = 0; i < 30; i++) {
    await page.waitForTimeout(1000)
    const app = ((await api.get('/api/apps')).json?.apps ?? []).find((a) => a.name === appName)
    if (app && app.enabled === false) { off = true; break }
  }
  screenshots.push(await shot('reactivate-deactivated'))
  details.deactivated = off
  if (!off) return { reason: 'clicking Deactivate never moved the app out of the enabled state', screenshots, details }

  const activate = panel.getByRole('button', { name: 'Activate', exact: true })
  if (!(await activate.count())) {
    screenshots.push(await shot('reactivate-no-activate'))
    return { reason: 'after deactivating, no Activate control is offered to bring the app back', screenshots, details }
  }
  await activate.first().click()
  let on = false
  for (let i = 0; i < 30; i++) {
    await page.waitForTimeout(1000)
    const app = ((await api.get('/api/apps')).json?.apps ?? []).find((a) => a.name === appName)
    if (app && app.enabled === true) { on = true; break }
  }
  screenshots.push(await shot('reactivate-reactivated'))
  details.reactivated = on
  if (!on) return { reason: 'clicking Activate never brought the app back to the enabled state', screenshots, details }
  return { ok: true, screenshots, details }
}

// ── leg 7: removal, and what survives it ─────────────────────────────────────
//
// The clause this leg mechanises is "the app's data survives removal", and the reason it
// exists is that proving it by hand cost a full session and produced nothing reusable
// (PEP-16). The trap it must not fall into is proving the WRONG thing: a `Path.exists()`
// check shows a file is on disk, which is not the same claim as "the provider still
// resolves and still answers with the user's data". On 2026-09-06 those two questions gave
// DIFFERENT answers for the same app — the notebook directory was there and the notes were
// not readable. So every read-back here goes through the gateway's own tool registry and
// the same inspector a user drives, never through the filesystem.

/** Tool-name shapes for the two roles this leg needs.
 *
 *  Narrow and explicit on purpose: the leg would far rather SKIP with a reason than guess
 *  that some tool is a "read" and then report a preservation verdict derived from the wrong
 *  call. */
const READ_BACK_TOOL = /(^|[_-])(list|get|read|show|status|search|recent|all|dump)([_-]|$)/i
const WRITE_BACK_TOOL = /(^|[_-])(add|append|create|new|write|save|record|put|set|log|capture)([_-]|$)/i

/** The read-back tool. ARGUMENT-FREE only, and that restriction is load-bearing: a read
 *  whose arguments the harness invented would read a note nobody wrote (`note_read` with a
 *  placeholder `ref`), and "not found" both before and after a removal looks exactly like
 *  preserved data. */
function pickReadBackTool(tools) {
  return tools.filter((t) => (t.parameters?.required ?? []).length === 0)
    .find((t) => READ_BACK_TOOL.test(t.name)) ?? null
}

/** The write tool, used only to give the leg something to preserve. Placeholder arguments
 *  are fine here — the content does not matter, only that the same content comes back —
 *  so this accepts a fillable tool as well as an argument-free one. */
function pickWriteTool(tools, exclude) {
  const candidates = tools.filter((t) => t.name !== exclude?.name && WRITE_BACK_TOOL.test(t.name))
  return candidates.find((t) => (t.parameters?.required ?? []).length === 0)
    ?? candidates.filter(argsAreFillable)[0]
    ?? null
}

async function appEntry(api, appName) {
  return ((await api.get('/api/apps')).json?.apps ?? []).find((a) => a.name === appName) ?? null
}

/** The app's data facts, classified into the three states core reports separately.
 *  The classifier (and its skip reasons) lives in the unit-tested report module. */
async function appDataFacts(api, appName) {
  const res = await api.get(`/api/apps/${encodeURIComponent(appName)}/uninstall-preview`)
  return classifyAppData(res.json?.data)
}

/** Poll until `/api/tools` publishes `tool` again — the REGISTRY answer to "is the
 *  provider back", as opposed to "is there a file where it used to be". */
async function waitForToolInRegistry(api, page, tool, tries = 40) {
  for (let i = 0; i < tries; i++) {
    const tools = (await api.get('/api/tools')).json?.tools ?? []
    if (tools.some((t) => t.provider === tool.provider && t.name === tool.name)) return true
    await page.waitForTimeout(1000)
  }
  return false
}

/** Put the app back from the SAME source, and make sure it is enabled.
 *
 *  Through the API deliberately: `ui-install` already covers the browser install path, and
 *  here the reinstall is setup for the claim under test rather than the claim itself. The
 *  part only a browser can cover — the removal control and its confirm dialog — stays in
 *  `clickRemovalInLibrary`. */
async function reinstall({ page, api, appName, source }) {
  const res = await api.post('/api/apps', { source, confirm: true })
  if (res.status !== 201 && !res.json?.ok) {
    return { ok: false, reason: `POST /api/apps → ${res.status} ${res.json?.error ?? ''}`.trim() }
  }
  let nudged = false
  for (let i = 0; i < 40; i++) {
    const app = await appEntry(api, appName)
    if (app?.enabled) return { ok: true }
    if (app && app.enabled === false && !nudged) {
      nudged = true
      await api.post(`/api/apps/${encodeURIComponent(appName)}/enable`)
    }
    await page.waitForTimeout(1000)
  }
  return { ok: false, reason: 'the app never came back enabled after reinstall' }
}

/** Click a removal control in the Library detail panel and go through its confirm dialog.
 *
 *  `rung` is `keep-data` (the panel's own Uninstall button) or `force` (Advanced → Force
 *  uninstall). The dialog is clicked through rather than bypassed, exactly as `ui-install`
 *  clicks through the scanner's consent dialog: it is the part only a browser leg can
 *  cover, and until this leg existed nobody had clicked either of these in a browser at
 *  all — they were verified through the endpoint they call and through vitest. */
async function clickRemovalInLibrary({ page, base, api, appName, displayName, shot, rung }) {
  const screenshots = []
  const label = rung === 'force' ? 'Force uninstall' : 'Uninstall'
  await gotoRoute(page, base, '/apps?view=library')
  const card = cardFor(page, displayName)
  await card.first().waitFor({ state: 'attached', timeout: 20_000 }).catch(() => {})
  if (!(await card.count())) {
    screenshots.push(await shot(`${rung}-no-card`))
    return { reason: `the app has no Library card, so its ${label} control is unreachable`, screenshots }
  }
  await card.first().click()
  await page.waitForTimeout(900)
  const panel = sidePanel(page, displayName)

  if (rung === 'force') {
    // Force uninstall is deliberately behind an "Advanced" expander, so the leg has to
    // open it the way a user does rather than reaching for a hidden control.
    const advanced = panel.getByRole('button', { name: 'Advanced' })
    if (!(await advanced.count())) {
      screenshots.push(await shot('force-no-advanced'))
      return { reason: 'the Library detail panel offers no Advanced section, so Force uninstall is unreachable', screenshots }
    }
    await advanced.first().click()
    await page.waitForTimeout(500)
  }

  const trigger = panel.getByRole('button', { name: label, exact: true })
  if (!(await trigger.count())) {
    screenshots.push(await shot(`${rung}-no-control`))
    return { reason: `the Library detail panel offers no ${label} control`, screenshots }
  }
  await trigger.first().click()

  const dialog = page.locator('[role="dialog"]').filter({ hasText: `${label} ${appName}?` }).first()
  const opened = await dialog.waitFor({ state: 'visible', timeout: 10_000 }).then(() => true).catch(() => false)
  if (!opened) {
    screenshots.push(await shot(`${rung}-no-confirm`))
    return { reason: `clicking ${label} raised no confirm dialog — a destructive action must state what it is about to do`, screenshots }
  }
  // The dialog animates in; screenshotting the frame it became visible captures a
  // half-transparent ghost that reads as an unreadable dialog (a harness artifact).
  await page.waitForTimeout(1200)
  const dialogText = (await dialog.innerText().catch(() => '')).replace(/\s+/g, ' ').trim()
  screenshots.push(await shot(`${rung}-confirm`))

  const confirm = dialog.getByRole('button', { name: label, exact: true })
  if (!(await confirm.count())) {
    return { reason: `the ${label} confirm dialog offers no ${label} button`, screenshots, dialogText }
  }
  await confirm.first().click()
  for (let i = 0; i < 60; i++) {
    await page.waitForTimeout(1000)
    if (!(await appEntry(api, appName))) {
      screenshots.push(await shot(`${rung}-done`))
      return { ok: true, screenshots, dialogText }
    }
  }
  screenshots.push(await shot(`${rung}-still-installed`))
  return { reason: `confirming ${label} never removed the app — /api/apps still lists it`, screenshots, dialogText }
}

/** Leg 7. Uninstall keeps the app's data; Force uninstall does not.
 *
 *  Both arms are here because either alone is a false green: a check that only shows
 *  preservation cannot tell a working preserve from a broken wipe, and a read-back whose
 *  answer does not depend on the data would satisfy the first arm while failing the second.
 *  That is the vacuity floor — the force arm is what keeps the preserve arm meaning
 *  something. */
async function uninstallPreservesData({ page, base, api, appName, displayName, source, shot, contributed }) {
  const screenshots = []
  const details = {}

  const readBack = pickReadBackTool(contributed)
  if (!readBack) {
    const inventory = contributed.map((t) => t.name).join(', ') || 'it contributes no tools at all'
    return {
      skip: `no argument-free read tool to read this app's data back through the registry (${inventory}) — `
        + 'checking the filesystem instead would prove a file exists, not that the provider still answers',
      screenshots, details,
    }
  }
  details.readBackTool = `${readBack.provider}::${readBack.name}`

  // Give the leg something to preserve, through the app's OWN write tool when it has one.
  // Best-effort: when it has none, the leg falls back to whatever `tool-invoke` left behind
  // and SKIPs (never passes) if that turns out to be nothing.
  const writeTool = pickWriteTool(contributed, readBack)
  details.writeTool = writeTool ? `${writeTool.provider}::${writeTool.name}` : null
  if (writeTool) {
    const wrote = await runToolFromUi({ page, base, tool: writeTool, shot })
    details.writeStatus = wrote.status
    screenshots.push(...wrote.screenshots)
  }

  // Which of the three data states is this? `present` and `entries` are separate facts and
  // the leg reports which one it saw — collapsing absent into empty is the bug one level up.
  const before = await appDataFacts(api, appName)
  details.dataStateBefore = before.state
  details.dataEntriesBefore = before.entries
  if (before.state !== APP_DATA.PRESENT) return { skip: before.reason, screenshots, details }

  const pre = await runToolFromUi({ page, base, tool: readBack, shot })
  screenshots.push(...pre.screenshots)
  if (pre.status !== 'ok' || !pre.output.trim()) {
    return {
      skip: `the read-back tool ${readBack.name} produced no baseline before the removal `
        + `(${pre.status}: ${pre.output.slice(0, 200) || 'empty output'}), so "the data came back" has nothing to be compared against`,
      screenshots, details,
    }
  }
  details.readBackBefore = pre.output.slice(0, 800)

  // ── arm 1: Uninstall — files go, data/ stays ──────────────────────────────
  const removed = await clickRemovalInLibrary({ page, base, api, appName, displayName, shot, rung: 'keep-data' })
  screenshots.push(...removed.screenshots)
  details.keepDataDialog = removed.dialogText ?? ''
  if (!removed.ok) return { reason: removed.reason, screenshots, details }

  // GONE from the Library, not merely switched off — that difference is the whole reason
  // `reactivate` could never stand in for this leg.
  await gotoRoute(page, base, '/apps?view=library')
  await page.waitForTimeout(900)
  const stillCarded = await cardFor(page, displayName).count()
  screenshots.push(await shot('keep-data-library-after'))
  if (stillCarded) {
    return {
      reason: 'the app still has a Library card after Uninstall, so its files never left disk — '
        + "that is Deactivate wearing Uninstall's name",
      screenshots, details,
    }
  }

  const back = await reinstall({ page, api, appName, source })
  details.reinstalledAfterKeepData = back.ok
  if (!back.ok) {
    return { reason: `reinstalling from the same source after Uninstall failed (${back.reason}), so preservation could not be checked`, screenshots, details }
  }
  const resolved = await waitForToolInRegistry(api, page, readBack, 40)
  details.readBackToolResolvedAgain = resolved
  if (!resolved) {
    return {
      reason: `after reinstall the gateway's tool registry does not publish ${details.readBackTool} again, `
        + 'so the provider did not come back — the data question cannot even be asked',
      screenshots, details,
    }
  }
  const post = await runToolFromUi({ page, base, tool: readBack, shot })
  screenshots.push(...post.screenshots)
  details.readBackAfterKeepData = post.output.slice(0, 800)
  const afterKeep = await appDataFacts(api, appName)
  details.dataStateAfterKeepData = afterKeep.state
  details.dataEntriesAfterKeepData = afterKeep.entries
  if (post.status !== 'ok') {
    return { reason: `the read-back tool ${readBack.name} no longer runs after reinstall (${post.status}: ${post.output.slice(0, 240)})`, screenshots, details }
  }
  if (!post.output.includes(pre.output)) {
    return {
      reason: 'Uninstall promises to keep this app\'s data, but the app\'s OWN read-back tool answers '
        + `differently after reinstalling from the same source. Before: ${pre.output.slice(0, 240)} — `
        + `after: ${post.output.slice(0, 240) || '(empty)'}. Note what the data facts said: `
        + `${details.dataStateAfterKeepData} / ${details.dataEntriesAfterKeepData} entries — a directory `
        + 'coming back is not the same fact as the data being readable',
      screenshots, details,
    }
  }

  // ── arm 2: Force uninstall — everything goes, data included ───────────────
  // Without this arm the leg could pass on an app whose read-back answer does not depend on
  // its data at all, which is exactly the "green with no signal" shape it exists to end.
  const forced = await clickRemovalInLibrary({ page, base, api, appName, displayName, shot, rung: 'force' })
  screenshots.push(...forced.screenshots)
  details.forceDialog = forced.dialogText ?? ''
  if (!forced.ok) return { reason: `the negative arm could not run: ${forced.reason}`, screenshots, details }

  const back2 = await reinstall({ page, api, appName, source })
  details.reinstalledAfterForce = back2.ok
  if (!back2.ok) {
    return { reason: `reinstalling after Force uninstall failed (${back2.reason}), so the negative arm is unproven`, screenshots, details }
  }
  const resolvedAgain = await waitForToolInRegistry(api, page, readBack, 40)
  if (!resolvedAgain) {
    return { reason: `after the force-uninstall reinstall, ${details.readBackTool} is not in the tool registry`, screenshots, details }
  }
  const postForce = await runToolFromUi({ page, base, tool: readBack, shot })
  screenshots.push(...postForce.screenshots)
  details.readBackAfterForce = postForce.output.slice(0, 800)
  const afterForce = await appDataFacts(api, appName)
  details.dataStateAfterForce = afterForce.state
  details.dataEntriesAfterForce = afterForce.entries
  if (postForce.status === 'ok' && postForce.output.includes(pre.output)) {
    return {
      reason: 'Force uninstall states it removes everything the app stored, but after reinstalling '
        + `the app's own read-back tool still answers with the same data (${postForce.output.slice(0, 240)}). `
        + 'Either the force rung did not wipe it, or this read-back does not depend on the data — '
        + 'and in that second case the preserve arm above proved nothing either',
      screenshots, details,
    }
  }
  return { ok: true, screenshots, details }
}

// ── model wiring ─────────────────────────────────────────────────────────────

/** Probe the configured model endpoint. Returns a reason string when it is not
 *  usable — model-backed tools then report SKIPPED with that reason rather than
 *  failing for a cause that is not the bundle's. */
async function probeModelEndpoint(endpoint) {
  for (const route of ['/v1/models', '/api/tags']) {
    try {
      const res = await fetch(`${endpoint.replace(/\/$/, '')}${route}`, { signal: AbortSignal.timeout(5000) })
      if (res.ok) return { ok: true, route }
    } catch (err) { /* try the next route */ }
  }
  return { ok: false, reason: `no model endpoint answered at ${endpoint} (tried /v1/models and /api/tags)` }
}

async function wireModel({ api, opts, appsRoot }) {
  if (opts.noModel) return { configured: false, reason: 'model wiring was disabled with --no-model', endpoint: opts.modelEndpoint }
  const probe = await probeModelEndpoint(opts.modelEndpoint)
  if (!probe.ok) return { configured: false, reason: probe.reason, endpoint: opts.modelEndpoint }

  const appDir = path.isAbsolute(opts.modelApp) ? opts.modelApp : path.join(appsRoot, opts.modelApp)
  if (!existsSync(path.join(appDir, 'app.json'))) {
    return { configured: false, endpoint: opts.modelEndpoint, reason: `the model provider app ${opts.modelApp} is not at ${appDir}` }
  }
  const install = await api.post('/api/apps', { source: appDir, confirm: true })
  if (!install.json?.ok) {
    return { configured: false, endpoint: opts.modelEndpoint, reason: `installing the model provider app failed: ${install.json?.error ?? install.status}` }
  }
  return { configured: false, endpoint: opts.modelEndpoint, appDir, installed: true, reason: 'provider entry not created yet' }
}

async function createModelProvider({ api, opts }) {
  const created = await api.post('/api/model-providers', {
    name: 'harness-model',
    type: opts.modelType,
    model: opts.model,
    options: { endpoint: opts.modelEndpoint },
  })
  if (created.json?.ok || created.status === 200) {
    return { configured: true, endpoint: opts.modelEndpoint, model: opts.model, type: opts.modelType, reason: '' }
  }
  return {
    configured: false, endpoint: opts.modelEndpoint, model: opts.model, type: opts.modelType,
    reason: `could not register the model provider: ${created.json?.error ?? created.status}`,
  }
}

// ── main ─────────────────────────────────────────────────────────────────────

async function main() {
  const opts = parseArgs(process.argv.slice(2))
  if (!existsSync(path.join(REPO_ROOT, 'src', 'personalclaw', 'static', 'dist', 'index.html'))) {
    warn('the gateway has no built SPA to serve (src/personalclaw/static/dist/index.html missing) — run `make web-build`')
    process.exitCode = 2
    return
  }
  for (const b of opts.bundles) {
    if (!existsSync(path.join(b, 'app.json'))) {
      warn(`${b} is not an app bundle (no app.json)`)
      process.exitCode = 2
      return
    }
  }

  const appsRoot = opts.appsRoot || path.dirname(opts.bundles[0])
  const home = mkdtempSync(path.join(opts.homeBase, 'pclaw-app-ui-validate-'))
  const outDir = opts.out || path.join(home, 'report')
  mkdirSync(path.join(home, 'workspace'), { recursive: true })
  mkdirSync(outDir, { recursive: true })
  // `onboarded` is derived from a server-side user name; without it every route
  // renders the onboarding screen and every leg would assert against a surface no
  // user of an installed app ever sees.
  writeFileSync(path.join(home, 'config.json'), JSON.stringify({ dashboard: { user_name: 'Harness' } }))

  const port = opts.port || await freePort()
  if (port === 10000) throw new Error('refusing to bind port 10000')
  const python = resolvePython(opts.python)
  const firstPartyDir = path.join(home, 'no-first-party-apps')
  const gatewayLog = []

  log(`home ${home}`)
  log(`out  ${outDir}`)
  log(`port ${port} · python ${python}`)

  let gateway = null
  let bundles = []
  let model = null
  // An interrupted run must not orphan a gateway or leave a home behind. `finally`
  // does not run on a signal, so wire the two explicitly.
  const onSignal = (sig) => {
    if (gateway) gateway.kill('SIGKILL')
    if (!opts.keep && !outDir.startsWith(home)) rmSync(home, { recursive: true, force: true })
    warn(`interrupted by ${sig} — gateway stopped`)
    process.exit(130)
  }
  process.once('SIGINT', () => onSignal('SIGINT'))
  process.once('SIGTERM', () => onSignal('SIGTERM'))
  try {
    // Boot once for setup (installing a provider app registers a provider TYPE at
    // import time, which the running process may not pick up), then boot again so
    // the provider type is present before any bundle is driven.
    let started = await startGateway({ python, home, port, firstPartyDir, logSink: gatewayLog })
    gateway = started.proc
    let api = { ...apiClient(`http://127.0.0.1:${port}`, started.token), token: started.token }
    model = await wireModel({ api, opts, appsRoot })
    if (model.installed) {
      await stopGateway(gateway)
      started = await startGateway({ python, home, port, firstPartyDir, logSink: gatewayLog })
      gateway = started.proc
      api = { ...apiClient(`http://127.0.0.1:${port}`, started.token), token: started.token }
      model = await createModelProvider({ api, opts })
    }
    log(model.configured ? `model: ${model.type} → ${model.endpoint}${model.model ? ` (${model.model})` : ''}` : `model: NOT wired — ${model.reason}`)

    const base = `http://127.0.0.1:${port}`
    const browser = await chromium.launch({ headless: !opts.headed })
    try {
      for (const bundleDir of opts.bundles) {
        log(`driving ${path.basename(bundleDir)}…`)
        const report = await driveBundle({ browser, base, api, bundleDir, outDir, home, model })
        bundles.push(report)
        log(`  ${report.bundle}: ${report.verdict}`)
      }
    } finally {
      await browser.close().catch(() => {})
    }
  } finally {
    await stopGateway(gateway)
    const report = shapeReport({
      bundles,
      harness: { script: 'scripts/app_ui_validate.mjs', port, home, outDir, python, repoRoot: REPO_ROOT },
      model,
    })
    const reportPath = path.join(outDir, 'report.json')
    writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`)
    if (opts.keep) writeFileSync(path.join(outDir, 'gateway.log'), gatewayLog.join(''))
    console.log(formatReport(report))
    log(`report ${reportPath}`)
    if (!opts.keep) {
      // The home is the only thing this harness creates outside --out. Screenshots
      // and the report live under --out; when that defaults into the home, keep it.
      if (!outDir.startsWith(home)) rmSync(home, { recursive: true, force: true })
      else log(`kept ${home} (it holds the report)`)
    } else {
      log(`kept ${home}`)
    }
    process.exitCode = report.exitCode
  }
}

main().catch((err) => {
  warn(err.stack ?? String(err))
  process.exitCode = 1
})
