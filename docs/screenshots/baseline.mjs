#!/usr/bin/env node
// The redesign baseline (docs/design/baseline.md): every screen, light and dark, 1280 and 390 px
// wide, with the figures each redesign PR quotes as its "before".
//
// For every capture it records the regions' boxes (rail, top band, page header, composer, right
// pane), the work surface's share of the window, what is visible (controls, words, distinct text
// and control treatments, counters reading 0), and how long the route took to become actionable
// from a cold load. scripts/baseline_images.py then compresses each snapshot and adds its ink and
// coral shares. Once per run it also measures the idle cost of the default look: requestAnimation-
// Frame callbacks over 5 s on a focused, idle Home, Chat and Settings, in Chromium and in an
// Electron window, with the Electron processes' CPU from app.getAppMetrics().
//
// The screens are web/e2e/routes.ts's ROUTES, SETTINGS_ROUTES, VIEW_ROUTES and NON_NAV_ROUTES; the
// two shell routes App.tsx renders itself (onboarding, companion); and three detail routes that
// need a record: the seeded loop's cockpit, a code session (a READY one is created through
// POST /api/loops when the home has none) and a chat with one turn, driven through the composer.
//
// Prereqs — a gateway on a SCRATCH home seeded with demo-home, serving this checkout's web/dist
// (make web-build), with the offline scripted model answering from baseline-chat.json; and, for
// the onboarding screen, a second one on a home seeded empty, because an onboarded home sends
// #/onboarding to Home. Several screens print paths (the workspace, the backups folder, the
// command the core tool server starts), and a home or checkout under a personal folder puts its
// name into every snapshot. So the homes, HOME itself and the checkout's path are neutral ones:
//
//   ln -sfn "$PWD" /tmp/pc-baseline-checkout && cd /tmp/pc-baseline-checkout && mkdir -p /tmp/pc-baseline-user
//   HOME=/tmp/pc-baseline-user PYTHONPATH=$PWD/src PERSONALCLAW_HOME=/tmp/pc-baseline \
//     PERSONALCLAW_WORKSPACE=/tmp/pc-baseline/workspace PERSONALCLAW_BIND_HOST=127.0.0.1 \
//     PERSONALCLAW_SCRIPTED_MODEL_SCRIPT=$PWD/docs/screenshots/baseline-chat.json \
//     .venv/bin/python -m personalclaw gateway --no-open --port 10200 --json-ready --seed demo-home --seed-replace
//   HOME=/tmp/pc-baseline-user PYTHONPATH=$PWD/src PERSONALCLAW_HOME=/tmp/pc-baseline-onboarding \
//     PERSONALCLAW_WORKSPACE=/tmp/pc-baseline-onboarding/workspace PERSONALCLAW_BIND_HOST=127.0.0.1 \
//     .venv/bin/python -m personalclaw gateway --no-open --port 10201 --json-ready --seed empty --seed-replace
//
// Usage:
//   node docs/screenshots/baseline.mjs --home /tmp/pc-baseline --onboarding-home /tmp/pc-baseline-onboarding
//   node docs/screenshots/baseline.mjs --home /tmp/pc-baseline --only dashboard,chat   # print, write nothing
//   node docs/screenshots/baseline.mjs --home /tmp/pc-baseline --out /tmp/after        # an "after" capture
//
// The gateway is the one that home's runtime record names, signed in through its local secret
// (scripts/lib/named_home.mjs); no home, or the default one, is refused before a browser starts.
// --only prints the named screens' figures and writes nothing, to check a report section against
// a fresh capture. The contributed-UI route needs web/e2e/fixtures/app-ui installed in the home:
// it is copied in for that one capture and removed after, so it never joins the other screens'
// rail.

import { spawnSync } from 'node:child_process'
import { cp, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { homeArg, scratchGatewayOrExit, signIn } from '../../scripts/lib/named_home.mjs'

const gateway = scratchGatewayOrExit(homeArg())

/** `--name VALUE` or `--name=VALUE`, if given. */
function option(name, argv = process.argv.slice(2)) {
  const at = argv.findIndex((a) => a === name || a.startsWith(`${name}=`))
  if (at < 0) return undefined
  return argv[at].includes('=') ? argv[at].slice(name.length + 1) : argv[at + 1]
}

// Held to the same rule as --home: a scratch home with a running gateway, or a refusal.
const onboardingHome = option('--onboarding-home')
const onboardingGateway = onboardingHome ? scratchGatewayOrExit(onboardingHome) : null

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const WIDTHS = [{ width: 1280, height: 900 }, { width: 390, height: 844 }]
const THEMES = ['light', 'dark']
const READY_TIMEOUT_MS = 15000
const ACTIONABLE_AFTER_MS = 5000
const SETTLE_MS = 800
const IDLE_WINDOW_MS = 5000

// Where the snapshots and figures.json go: the committed baseline by default, or a scratch folder
// for an "after" capture that should not overwrite it.
const OUT = path.resolve(option('--out') ?? path.join(REPO, 'docs', 'design', 'baseline'))
const only = option('--only') === undefined ? null : new Set(option('--only').split(',').filter(Boolean))

const { ROUTES, SETTINGS_ROUTES, VIEW_ROUTES, NON_NAV_ROUTES } = await import('../../web/e2e/routes.ts')
const { chromium, _electron } = await import('playwright')

const fileId = (entry) => entry.id ?? entry.route.replace(/\//g, '-')
const screens = [
  ...ROUTES.map((e) => ({ id: fileId(e), route: e.route, label: e.label, group: 'rail' })),
  ...SETTINGS_ROUTES.map((e) => ({ id: fileId(e), route: e.route, label: e.label, group: 'settings' })),
  ...VIEW_ROUTES.map((e) => ({ id: fileId(e), route: e.route, label: e.label, group: 'view' })),
  ...NON_NAV_ROUTES.map((e) => ({ id: fileId(e), route: e.route, label: e.label, group: 'non-nav' })),
  { id: 'onboarding', route: 'onboarding', label: 'Onboarding', group: 'shell' },
  { id: 'companion', route: 'companion', label: 'Companion', group: 'shell' },
]

const browser = await chromium.launch()
const api = await browser.newContext()
await signIn(api, gateway)

// ── the three detail routes, each found or made through the product's own routes ────────────────

async function json(url) {
  const answer = await api.request.get(`${gateway.url}${url}`)
  if (!answer.ok()) throw new Error(`GET ${url}: HTTP ${answer.status()}`)
  return answer.json()
}

async function detailRoutes() {
  const loops = (await json('/api/loops')).loops
  const goalLike = loops.find((l) => l.kind !== 'code')
  let code = loops.find((l) => l.kind === 'code')
  if (!code && (!only || only.has('code-session'))) {
    const made = await api.request.post(`${gateway.url}/api/loops`, {
      headers: { Origin: gateway.url },
      data: { kind: 'code', task: 'Add an export button to the weekly reading digest page', project_id: goalLike?.project_id ?? '' },
    })
    if (!made.ok()) throw new Error(`POST /api/loops: HTTP ${made.status()} ${await made.text()}`)
    code = await made.json()
  }
  const out = []
  if (goalLike) out.push({ id: 'loop-cockpit', route: `loops/${goalLike.id}`, label: 'Loop cockpit', group: 'detail' })
  if (code) out.push({ id: 'code-session', route: `code/${code.id}`, label: 'Code session', group: 'detail' })
  if (!only || only.has('chat-turn')) out.push({ id: 'chat-turn', route: await chatWithATurn(), label: 'Chat with a turn', group: 'detail' })
  return out
}

/** One turn through the composer, the way an owner sends one; the session's own route is what the
 *  shell moves to once it exists. */
async function chatWithATurn() {
  const page = await api.newPage()
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(`${gateway.url}/#/chat`, { waitUntil: 'networkidle' })
  const composer = page.getByRole('textbox', { name: 'Message input' })
  await composer.click()
  await composer.fill("Plan this week's reading digest")
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await page.getByText("Here is the plan for this week's reading digest", { exact: false }).first().waitFor({ timeout: 30000 })
  await page.getByRole('button', { name: 'Send message', exact: true }).waitFor({ timeout: 30000 })
  const route = new URL(page.url()).hash.replace(/^#\//, '')
  await page.close()
  if (!route.startsWith('chat/')) throw new Error(`the turn did not move the shell to a session route: #/${route}`)
  return route
}

screens.push(...(await detailRoutes()))
const chosen = only ? screens.filter((s) => only.has(s.id)) : screens
if (only && chosen.length !== only.size) {
  const known = new Set(screens.map((s) => s.id))
  console.error(`unknown screen id: ${[...only].filter((id) => !known.has(id)).join(', ')}`)
  process.exit(1)
}

// ── in the page: is it actionable yet, and what does it hold ────────────────────────────────────

// Two moments of a cold load. `settled`: the page has painted its content (a visible heading or
// text in the page) and nothing visible is still busy. `actionable`: settled, and a control in the
// work area (not the rail, not the toolbar corners) can be used. A screen with nothing to act on
// never becomes actionable, and the figures say so rather than timing a wait.
const settled = () => {
  const visible = (el) => {
    const r = el.getBoundingClientRect()
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth &&
      el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
  }
  if (document.readyState !== 'complete') return false
  if ([...document.querySelectorAll('[aria-busy="true"]')].some(visible)) return false
  const page = [...document.querySelectorAll('main')].find((m) => !m.parentElement.closest('main')) ?? document.body
  return [...page.querySelectorAll('h1, h2, p, li, label, [data-type]')].some((el) => el.textContent.trim() && visible(el))
}

const ready = () => {
  const visible = (el) => {
    const r = el.getBoundingClientRect()
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth &&
      el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
  }
  if (document.readyState !== 'complete') return false
  if ([...document.querySelectorAll('[aria-busy="true"]')].some(visible)) return false
  const chrome = (el) => el.closest('nav[data-tour="rail"]') || el.closest('main > .absolute.top-0')
  return [...document.querySelectorAll('main button, main a[href], main input, main textarea, main select, main [contenteditable="true"], main [role="tab"], main [role="button"], body > div button')]
    .some((el) => !chrome(el) && visible(el))
}

const measure = () => {
  const vw = innerWidth
  const vh = innerHeight
  const box = (el) => {
    if (!el) return null
    const r = el.getBoundingClientRect()
    const x = Math.max(0, r.left), y = Math.max(0, r.top)
    const w = Math.min(vw, r.right) - x, h = Math.min(vh, r.bottom) - y
    return w > 0 && h > 0 ? { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) } : null
  }
  const visible = (el) => !!box(el) && el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
  const main = [...document.querySelectorAll('main')].find((m) => !m.parentElement.closest('main') && visible(m))
  const rail = document.querySelector('nav[data-tour="rail"]')
  const railBox = rail && visible(rail) ? box(rail.parentElement) : null
  const mainBox = box(main) ?? { x: 0, y: 0, w: vw, h: vh }
  const corners = main ? [...main.children].filter((c) => c.matches('.absolute.top-0') && visible(c)) : []
  const header = main ? [...main.querySelectorAll('header')].find((h) => visible(h) && h.getBoundingClientRect().top < 80) : null
  const composer = [...document.querySelectorAll('[data-composer-stage]')].find(visible) ?? null
  // The docked right pane is SidePanel's named region (`role="region"` labelled by its title), held
  // to the main area's right edge. At a compact width it can cover most of the page, and then the
  // work surface is what it leaves.
  const pane = main ? [...main.querySelectorAll('[role="region"][aria-labelledby]')].find((r) => {
    const b = box(r)
    return visible(r) && b && Math.abs(b.x + b.w - (mainBox.x + mainBox.w)) <= 2 && b.h > vh * 0.5
  }) : null
  const headerBox = box(header)
  const cornerBottom = Math.max(0, ...corners.map((c) => { const b = box(c); return b.y + b.h }))
  const topBand = Math.max(cornerBottom, headerBox ? headerBox.y + headerBox.h : 0)
  const composerBox = box(composer)
  const paneBox = box(pane)
  const workW = (paneBox ? paneBox.x : mainBox.x + mainBox.w) - mainBox.x
  const workH = vh - topBand - (composerBox ? composerBox.h : 0)
  const work = { x: mainBox.x, y: topBand, w: Math.max(0, workW), h: Math.max(0, workH) }

  // Where a point sits, for splitting what is visible between the chrome and the work.
  const zone = (x, y) => {
    if (railBox && x < railBox.x + railBox.w) return 'rail'
    if (y < topBand) return 'top'
    if (composerBox && y >= composerBox.y && y < composerBox.y + composerBox.h && x >= composerBox.x && x < composerBox.x + composerBox.w) return 'composer'
    if (paneBox && x >= paneBox.x) return 'pane'
    return 'work'
  }
  const tally = () => ({ rail: 0, top: 0, composer: 0, pane: 0, work: 0 })
  const controls = tally()
  const controlStyles = new Set()
  const interactive = 'button, a[href], input:not([type="hidden"]), textarea, select, [contenteditable="true"], [role="button"], [role="tab"], [role="switch"], [role="checkbox"], [role="radio"], [role="menuitem"], [role="combobox"], [role="slider"]'
  for (const el of document.querySelectorAll(interactive)) {
    if (!visible(el) || el.parentElement.closest(interactive)) continue
    const b = box(el)
    controls[zone(b.x + b.w / 2, b.y + b.h / 2)]++
    if (el.matches('button, [role="button"]')) {
      const s = getComputedStyle(el)
      controlStyles.add([s.backgroundColor, s.borderTopColor, s.borderTopWidth, s.borderTopLeftRadius, Math.round(b.h), s.fontSize, s.fontWeight].join('|'))
    }
  }
  const words = tally()
  const textStyles = new Set()
  const sizes = new Set()
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
  const range = document.createRange()
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent.trim()
    if (!text || !node.parentElement || !visible(node.parentElement)) continue
    range.selectNodeContents(node)
    const r = range.getBoundingClientRect()
    if (r.width === 0 || r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) continue
    words[zone(r.left + r.width / 2, r.top + r.height / 2)] += text.split(/\s+/).length
    const s = getComputedStyle(node.parentElement)
    textStyles.add([s.fontSize, s.fontWeight, s.color].join('|'))
    sizes.add(s.fontSize)
  }
  const zeros = [...document.querySelectorAll('body *')].filter((el) => el.children.length === 0 && el.textContent.trim() === '0' && visible(el)).length
  const doc = document.scrollingElement
  return {
    hash: location.hash,
    h1: [...document.querySelectorAll('h1')].filter(visible).map((h) => h.textContent.trim()).slice(0, 2),
    regions: { rail: railBox, main: mainBox, topBand, header: headerBox, composer: composerBox, pane: paneBox, work },
    share: Math.round((work.w * work.h) / (vw * vh) * 1000) / 10,
    railShare: railBox ? Math.round((railBox.w / vw) * 1000) / 10 : 0,
    controls,
    words,
    textTreatments: textStyles.size,
    fontSizes: sizes.size,
    buttonTreatments: controlStyles.size,
    zeroCounters: zeros,
    overflowX: doc.scrollWidth > doc.clientWidth,
  }
}

// ── capture ─────────────────────────────────────────────────────────────────────────────────────

const raw = only ? null : await mkdtemp(path.join(os.tmpdir(), 'pc-baseline-'))
const figures = Object.fromEntries(chosen.map((s) => [s.id, { ...s, captures: {} }]))
const fixtureHome = path.join(gateway.home, 'apps', 'e2e-ui-fixture')
let fixtureAdded = false

for (const size of WIDTHS) {
  for (const theme of THEMES) {
    const contexts = []
    const pageOn = async (gw) => {
      const ctx = await browser.newContext({ viewport: size, deviceScaleFactor: 1, colorScheme: theme })
      contexts.push(ctx)
      await signIn(ctx, gw)
      // How the SPA persists the theme (localStorage `mode` + data-mode), as capture.mjs sets it.
      await ctx.addInitScript((mode) => {
        localStorage.setItem('mode', mode)
        document.documentElement.setAttribute('data-mode', mode)
      }, theme)
      return ctx.newPage()
    }
    const main = await pageOn(gateway)
    const firstRun = onboardingGateway ? await pageOn(onboardingGateway) : null
    for (const screen of chosen) {
      const onboarding = screen.id === 'onboarding' && firstRun
      const page = onboarding ? firstRun : main
      const origin = onboarding ? onboardingGateway.url : gateway.url
      if (screen.id === 'app-host-contributed-ui' && !existsSync(fixtureHome)) {
        await cp(path.join(REPO, 'web', 'e2e', 'fixtures', 'app-ui'), fixtureHome, { recursive: true })
        fixtureAdded = true
      }
      const key = `${theme}-${size.width}`
      await page.goto('about:blank')
      const started = Date.now()
      await page.goto(`${origin}/#/${screen.route}`, { waitUntil: 'commit' })
      const painted = await page.waitForFunction(settled, null, { timeout: READY_TIMEOUT_MS, polling: 50 }).then(() => true, () => false)
      const settledMs = Date.now() - started
      const actionable = await page.waitForFunction(ready, null, { timeout: ACTIONABLE_AFTER_MS, polling: 50 }).then(() => true, () => false)
      const readyMs = Date.now() - started
      await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {})
      await page.waitForTimeout(SETTLE_MS)
      const m = await page.evaluate(measure)
      figures[screen.id].captures[key] = { settledMs: painted ? settledMs : null, readyMs: actionable ? readyMs : null, ...m }
      if (raw) await page.screenshot({ path: path.join(raw, `${screen.id}-${key}.png`) })
      console.log(`${screen.id.padEnd(28)} ${key.padEnd(10)} work ${String(m.share).padStart(5)}%  rail ${String(m.railShare).padStart(4)}%  ` +
        `settled ${painted ? `${settledMs} ms` : 'never'}  actionable ${actionable ? `${readyMs} ms` : 'never'}  controls ${Object.values(m.controls).reduce((a, b) => a + b, 0)}  ${m.hash}`)
      if (screen.id === 'app-host-contributed-ui' && fixtureAdded) {
        await rm(fixtureHome, { recursive: true, force: true })
        fixtureAdded = false
      }
    }
    for (const ctx of contexts) await ctx.close()
  }
}

// ── idle cost of the default look ───────────────────────────────────────────────────────────────

/** Counts every requestAnimationFrame callback the page schedules. Installed before any page
 *  script, so a library that captures the function at load captures the counting one. */
function countFrames() {
  const original = window.requestAnimationFrame.bind(window)
  window.__frames = 0
  window.requestAnimationFrame = (cb) => { window.__frames++; return original(cb) }
}

const idleProbe = async (page) => {
  await page.waitForFunction(ready, null, { timeout: READY_TIMEOUT_MS, polling: 100 })
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {})
  await page.waitForTimeout(3000) // entrances finish; what is left is what idles
  const before = await page.evaluate(() => {
    window.__frames = 0
    return { focused: document.hasFocus(), visibility: document.visibilityState, running: document.getAnimations().filter((a) => a.playState === 'running').length }
  })
  await page.waitForTimeout(IDLE_WINDOW_MS)
  const after = await page.evaluate(() => ({ frames: window.__frames, running: document.getAnimations().filter((a) => a.playState === 'running').length }))
  return { ...before, frames: after.frames, runningAfter: after.running }
}

async function idleInChromium(route) {
  const ctx = await browser.newContext({ viewport: WIDTHS[0], deviceScaleFactor: 1, colorScheme: 'light' })
  await signIn(ctx, gateway)
  await ctx.addInitScript(countFrames)
  await ctx.addInitScript(() => localStorage.setItem('mode', 'light'))
  const page = await ctx.newPage()
  await page.goto(`${gateway.url}/#/${route}`, { waitUntil: 'commit' })
  await page.bringToFront()
  const out = await idleProbe(page)
  await ctx.close()
  return out
}

/** A plain Electron window on the same gateway: the renderer the desktop app runs the dashboard
 *  in, without the desktop shell's own gateway, tray and preload. */
async function idleInElectron(route) {
  let binary
  try {
    binary = (await import('electron')).default
  } catch {
    return { skipped: 'electron is not installed (npm ci, then node node_modules/electron/install.js)' }
  }
  if (typeof binary !== 'string' || !existsSync(binary)) return { skipped: 'the electron binary is not downloaded (node node_modules/electron/install.js)' }
  const dir = await mkdtemp(path.join(os.tmpdir(), 'pc-baseline-electron-'))
  const main = path.join(dir, 'main.cjs')
  await writeFile(main, [
    "const { app, BrowserWindow } = require('electron')",
    'app.whenReady().then(() => {',
    '  const win = new BrowserWindow({ width: 1280, height: 900, show: true })',
    "  win.loadURL('about:blank')",
    '})',
  ].join('\n'))
  const app = await _electron.launch({ executablePath: binary, args: [main] })
  try {
    const page = await app.firstWindow()
    await signIn(page.context(), gateway)
    await page.context().addInitScript(countFrames)
    await page.context().addInitScript(() => localStorage.setItem('mode', 'light'))
    await page.goto(`${gateway.url}/#/${route}`, { waitUntil: 'commit' })
    await app.evaluate(({ BrowserWindow }) => { const w = BrowserWindow.getAllWindows()[0]; w.show(); w.focus() })
    await page.waitForFunction(ready, null, { timeout: READY_TIMEOUT_MS, polling: 100 })
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {})
    await page.waitForTimeout(3000)
    // getAppMetrics' CPU figure is the share of one core since the previous call, so the first
    // call opens the window and the second closes it.
    await app.evaluate(({ app: a }) => a.getAppMetrics())
    const before = await page.evaluate(() => { window.__frames = 0; return { focused: document.hasFocus(), visibility: document.visibilityState } })
    await page.waitForTimeout(IDLE_WINDOW_MS)
    const metrics = await app.evaluate(({ app: a }) => a.getAppMetrics())
    const frames = await page.evaluate(() => window.__frames)
    const of = (type) => metrics.filter((p) => p.type === type).map((p) => ({ cpu: Math.round(p.cpu.percentCPUUsage * 100) / 100, wakeups: p.cpu.idleWakeupsPerSecond }))
    return { ...before, frames, renderer: of('Tab'), gpu: of('GPU'), browser: of('Browser') }
  } finally {
    await app.close()
    await rm(dir, { recursive: true, force: true })
  }
}

let idle = null
if (!only) {
  idle = {}
  // Settings is the control: a page that animates nothing, so what Home and Chat add is theirs.
  for (const route of ['dashboard', 'chat', 'settings']) {
    idle[route] = { chromium: await idleInChromium(route), electron: await idleInElectron(route) }
    console.log(`idle #/${route}: ${JSON.stringify(idle[route])}`)
  }
}

await browser.close()
if (!raw) process.exit(0)

// ── compress, read the pixels, write the figures ────────────────────────────────────────────────

await mkdir(OUT, { recursive: true })
const py = process.env.PERSONALCLAW_PY || path.join(REPO, '.venv', 'bin', 'python')
const run = spawnSync(py, [path.join(REPO, 'scripts', 'baseline_images.py'), raw, OUT], { encoding: 'utf8' })
if (run.status !== 0) {
  console.error(run.stderr || run.stdout)
  process.exit(1)
}
for (const line of run.stdout.trim().split('\n')) {
  const px = JSON.parse(line)
  const name = px.file.replace(/\.png$/, '')
  const screen = chosen.find((s) => name.startsWith(`${s.id}-`) && /^(light|dark)-\d+$/.test(name.slice(s.id.length + 1)))
  Object.assign(screen && figures[screen.id].captures[name.slice(screen.id.length + 1)], { ink: px.ink, coral: px.coral, bytes: px.bytes })
}
await rm(raw, { recursive: true, force: true })

const commit = spawnSync('git', ['rev-parse', '--short=9', 'HEAD'], { cwd: REPO, encoding: 'utf8' }).stdout.trim()
await writeFile(path.join(OUT, 'figures.json'), JSON.stringify({
  about: 'Written by docs/screenshots/baseline.mjs; read by docs/design/baseline.md. Boxes are CSS px in the viewport; shares are percent of the viewport area; ink and coral are fractions of its pixels.',
  commit,
  viewports: WIDTHS,
  themes: THEMES,
  screens: Object.values(figures),
  idle,
}, null, 1) + '\n')
console.log(`wrote ${chosen.length * WIDTHS.length * THEMES.length} snapshots and figures.json to ${path.relative(process.cwd(), OUT) || '.'}`)
