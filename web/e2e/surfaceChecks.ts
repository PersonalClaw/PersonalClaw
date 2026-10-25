import { type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import type { Theme } from './routes'
import { MOUSE_ONLY, CLIPPED_RING } from './nonAxeA11yScripts'
import { PALETTE_SEARCH_LABEL } from '../src/app/paletteSearchLabel'
import { realpathSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { harnessHome, userHomeFor } from './runPlaces'

/** The checkout and the home folder of the machine running the matrix: a page that prints a path
 *  under either would otherwise commit it into a baseline (published text, and read back by no
 *  other machine). Read from the run, never written out. */
const HOST_REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const HOST_HOME = homedir()
const escaped = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
/** A path at a folder boundary: followed by a separator, a delimiter or the end. */
const atFolder = (p: string) => new RegExp(`${escaped(p)}(?=[/\\s,"':)]|$)`, 'g')
/** The run's own two places, read from the run: the home it was handed or made (`PW_E2E_HOME`,
 *  which `choosePlaces` hands every worker, read through `harnessHome`) as `<e2e home>`, and the
 *  gateway's HOME beside it (`userHomeFor`) as `~`, each as given and as its real path. Read at
 *  each call, so a home named without the harness's prefix, or kept inside the checkout, folds as
 *  a made one does. A run with no home (a unit test) has none to fold. */
function runFolds(): [RegExp, string][] {
  let home = ''
  try { home = harnessHome(process.env) } catch { return [] }
  // The longer form first: a macOS temp folder's real path (`/private/var/folders/…`) holds the
  // path as given (`/var/folders/…`), which folded first left `/private<e2e home>` behind.
  const forms = (p: string) => {
    const given = resolve(p)
    let real = given
    try { real = realpathSync(given) } catch { /* not made yet: the path as given stands */ }
    return [...new Set([given, real])].sort((a, b) => b.length - a.length)
  }
  return [
    ...forms(userHomeFor(home)).map((p): [RegExp, string] => [atFolder(p), '~']),
    ...forms(home).map((p): [RegExp, string] => [atFolder(p), '<e2e home>']),
  ]
}
const HOST_FOLDS: [RegExp, string][] = [
  // The gateway's HOME (`user-home-for-<home>/` beside the run's home, `runPlaces.ts`): the folder
  // a page means by `~`, so it reads as `~`. First, because it may sit under either folder below.
  // `runFolds` reads both from the run; these patterns also catch a home the harness made, in
  // whatever spelling of its path a page prints.
  [/\/\S*?user-home-for-personalclaw-e2e-[^/\s]*/g, '~'],
  // The run's own home, before the checkout: a run may keep its temp folder inside the checkout.
  [/\/\S*?personalclaw-e2e-[^/\s]*/g, '<e2e home>'],
  // The checkout before the home folder: it usually sits inside it.
  [atFolder(HOST_REPO), '<repo>'],
  [atFolder(HOST_HOME), '~'],
]

// ── The UI quality matrix: every check, on one loaded surface ────────────────────────────────────
//
// `surfaces.spec.ts` loads each surface once per theme and hands the page to `runMatrix`, which runs
// the approved matrix as named steps. Each check RETURNS its findings and asserts nothing, so the
// spec can report them softly (one surface reports every check that failed on it) and
// `surfaceMatrix.control.spec.ts` can run the same steps over planted defects and read what each
// step found.
//
// The order is the point, because a visit is shared. A step that changes the page puts it back:
//   1. axe, then the two non-axe detectors, then the accessibility tree, on the page as it loaded;
//   2. reduced motion: emulate the preference, measure, emulate none again;
//   3. 200% text zoom: the root font size doubled, measured and restored in one synchronous pass;
//   4. the keyboard walk, with focus order and the rendered contrast of each focus indicator read
//      off the same walk; then Escape and a blur, so nothing it opened stays open;
//   5. the widths LAST, and WIDEST FIRST: 1440, then 1024 down to 320. Overflow at each, 44 px
//      targets at the compact ones, each read with every scroller back at its start.
//
// 🪤 WHY THE WIDTHS ONLY EVER STEP DOWN. A width reached by resizing must measure what a load at
// that width measures, and the responsive header does not promise that in both directions: it
// steps down to a narrower tier at once, but back up only with slack to spare (`HeaderActions`'
// hysteresis), so the strip that collapsed at 320 is still collapsed at 412. Measured on main over
// 70 surfaces, loaded at 390 and stepped to 320, 412 and 768 against a fresh load at each: 19 of
// 210 differed, in their small targets and their accessibility tree. Stepping only down keeps
// every compact width a state a phone actually loads into, and no step needs a reload.

/** The approved matrix's widths, and the ones where the 44 px touch-target rule applies. At 1024
 *  and 1440 the redesign wants dense rows for a mouse and keyboard, so 44 px does not apply. */
export const WIDTHS = [1440, 1024, 768, 412, 390, 320] as const
export const COMPACT_WIDTHS = [320, 390, 412, 768] as const
/** The viewport every surface loads at (playwright.config.ts) and returns to after the widths. */
export const REST_VIEWPORT = { width: 1280, height: 900 }
/** Phone height for the compact widths: the 390×844 the phone leg always used. */
const PHONE_HEIGHT = 844
export const MIN_TARGET_PX = 44
/** WCAG 1.4.11: a focus indicator and a text field's boundary against what is behind them. */
export const MIN_NON_TEXT_CONTRAST = 3
/** How many Tab presses a walk takes at most. The walk REPORTS when it stops here. */
export const TAB_CAP = 60

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']
/** The buddy's surfaces also run WCAG 2.2 AA (#3902), whose rule axe checks is `target-size`: Gate D
 *  holds the buddy to it now, and the 44 px compact check below goes further. The rest of the app
 *  joins it under the shared UI quality check (#3811). */
const BUDDY_AXE_TAGS = [...AXE_TAGS, 'wcag22aa']
export const isBuddyRoute = (route: string) => /^(buddy(\/|\?|$)|settings\/buddy$)/.test(route)
const BLOCKING = new Set(['serious', 'critical'])

export const STEPS = {
  axe: 'axe (WCAG 2 AA)',
  detectors: 'non-axe detectors',
  tree: 'accessibility tree',
  motion: 'reduced motion',
  zoom: '200% text zoom',
  keyboard: 'keyboard-only',
  order: 'focus order',
  contrast: 'rendered contrast',
} as const

export const overflowStep = (w: number): string => `overflow at ${w}px`
export const targetStep = (w: number): string => `44 px targets at ${w}px`
const isCompact = (w: number) => (COMPACT_WIDTHS as readonly number[]).includes(w)

/** The steps that measured defects on main when this check landed, so each holds a per-surface
 *  baseline of what it found then (the issue rules out changing a rule or threshold, and making
 *  them pass is product work, tracked apart). Shrink-only and self-clearing, like the 44 px
 *  targets: a finding the baseline does not name is a red, and so is a baselined one the page no
 *  longer has, until its entry is deleted. The detectors, reduced motion and keyboard-only were
 *  clean on every surface and stay at zero with no baseline at all. */
export const BASELINED_STEPS: readonly string[] = [
  STEPS.axe,
  STEPS.zoom,
  STEPS.order,
  STEPS.contrast,
  ...WIDTHS.map(overflowStep),
]

/** What a baseline keys a finding by: the finding with its measured numbers folded, so a ratio
 *  or a pixel size that moves a little is the same finding. */
export const findingKey = (f: string): string =>
  f
    .replace(/\bGood (morning|afternoon|evening)\b/g, 'Good <time of day>')
    // React's `useId` ids (`_r_c_`, `:r1:`) depend on render order, not on the surface.
    .replace(/_r_[a-z0-9]+_|:r[a-z0-9]+:/g, '<react id>')
    .replace(/\d+(\.\d+)?/g, '#')

/** Findings against a baseline of keys. `raw` is what the step measured. */
export function knownFindings(raw: string[], baseline: string[] | undefined, adopt = false): string[] {
  if (baseline === undefined) {
    if (adopt) return []
    return ['no baseline is committed for this step: run `npx playwright test e2e/surfaces.spec.ts --update-snapshots` and review the file it writes']
  }
  const base = new Set(baseline)
  const now = new Set(raw.map(findingKey))
  return [
    ...raw.filter((f) => !base.has(findingKey(f))),
    ...[...base].filter((k) => !now.has(k)).map((k) => `baselined but no longer found, delete it from the baseline: ${k}`),
  ]
}

/** Every step, in the order `runMatrix` runs them. `assert-no-route-skips.mjs` holds every scan in
 *  the report to this list, so a step that stops running is a red, not a quieter report. */
export const STEP_NAMES: readonly string[] = [
  STEPS.axe,
  STEPS.detectors,
  STEPS.tree,
  STEPS.motion,
  STEPS.zoom,
  STEPS.keyboard,
  STEPS.order,
  STEPS.contrast,
  ...WIDTHS.flatMap((w) => (isCompact(w) ? [overflowStep(w), targetStep(w)] : [overflowStep(w)])),
]

export interface SurfaceContext {
  /** How findings name the surface: `#/route (theme)` or `label [opened] (theme)`. */
  where: string
  theme: Theme
  kind: 'route' | 'opener'
  route: string
  /** The opener's label, which the keyboard allowances are keyed by. */
  opener?: string
}

export interface Baselines {
  /** The normalized accessibility tree this surface is held to, or null when none is committed. */
  tree: string | null
  /** Per compact width, the controls already measured under 44 px there. Shrink-only. */
  targets: Record<string, string[]>
  /** Per baselined step (`BASELINED_STEPS`), the findings main already had when the check landed,
   *  by `findingKey`. Shrink-only and self-clearing, as `targets` is. */
  known: Record<string, string[]>
  /** A missing baseline is being written from this run (`--update-snapshots`, or a first local
   *  run), so its absence is not a finding. In CI it is false, and a missing baseline is a red. */
  adopt: boolean
}

/** What the run measured, for the spec to write as the baseline when asked to. */
export interface Measured {
  tree: string
  targets: Record<string, string[]>
  known: Record<string, string[]>
}

/** Runs one step. `findings` is the step's measurement; the caller decides what a finding does. */
export type Step = (name: string, findings: () => Promise<string[]>) => Promise<void>

// ── In-page helpers, installed once per visit ─────────────────────────────────────────────────────

/** Installs `window.__pcq`: how a finding names an element, colour parsing through a canvas (the
 *  computed colours here are `oklch()` as often as `rgb()`), the background an element is drawn
 *  on, and WCAG contrast. Passed to `page.evaluate` as a function, so it must be self-contained. */
function install(): void {
  const w = window as unknown as { __pcq?: unknown }
  if (w.__pcq) return
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true }) as CanvasRenderingContext2D
  const rgba = (c: string): number[] => {
    ctx.clearRect(0, 0, 1, 1)
    ctx.fillStyle = '#000'
    ctx.fillStyle = c
    ctx.fillRect(0, 0, 1, 1)
    const d = ctx.getImageData(0, 0, 1, 1).data
    return [d[0], d[1], d[2], d[3] / 255]
  }
  const over = (top: number[], under: number[]) =>
    [0, 1, 2].map((i) => top[i] * top[3] + under[i] * (1 - top[3])).concat(1)
  const page = rgba(getComputedStyle(document.documentElement).backgroundColor)
  const canvasColour = page[3] > 0 ? page : [255, 255, 255, 1]
  /** The opaque colour `el` is painted on: its own background and its ancestors', composited. */
  const backdrop = (el: Element | null): number[] => {
    const layers: number[][] = []
    for (let e = el; e; e = e.parentElement) {
      const c = rgba(getComputedStyle(e).backgroundColor)
      if (c[3] > 0) layers.push(c)
      if (c[3] >= 0.99) break
    }
    let out = layers.length && layers[layers.length - 1][3] >= 0.99 ? layers.pop()! : canvasColour
    for (let i = layers.length - 1; i >= 0; i--) out = over(layers[i], out)
    return out
  }
  const lum = (c: number[]) => {
    const ch = c.slice(0, 3).map((v) => {
      const s = v / 255
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
  }
  const contrast = (a: number[], b: number[]) => {
    const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p)
    return (x + 0.05) / (y + 0.05)
  }
  const name = (el: Element) =>
    (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent || '').replace(/\s+/g, ' ').trim()
  const desc = (el: Element) => {
    const e = el as HTMLElement
    const id = e.id ? `#${e.id}` : ''
    const cls = typeof e.className === 'string' ? e.className.split(/\s+/).filter(Boolean).slice(0, 3).join('.') : ''
    const n = name(el).slice(0, 40)
    return `<${el.tagName.toLowerCase()}${id}${cls ? ' .' + cls : ''}>${n ? ` "${n}"` : ''}`
  }
  /** A name for an element that survives a restyle and a data refresh: tag, role and name with
   *  digits folded, and no classes. What a committed baseline keys a control by. */
  const key = (el: Element) => {
    const role = el.getAttribute('role')
    // A relative time is folded first, before the cut: the Doctor's snapshot row reads "last
    // snapshot under a minute ago" and then "… 4 minutes ago", and keyed whole it was two controls.
    let n = name(el).replace(/(just now|under a minute ago|\d+\s?(second|min|minute|hour|day|month)s? ago|\d+\s?[smhd] ago)/g, '<when>').slice(0, 60).replace(/\d+/g, '#')
    // A week-grid hour cell names the automations that fire in it after its date and hour
    // (`Mon, Nov 2 9:00 — 3 fires: …`), and that list moves with the clock. The cell IS its date
    // and hour, so that is what it is keyed by.
    const cell = name(el).match(/^(\w{3}, \w{3} \d+ \d+:\d+) — /)
    if (cell) n = cell[1]
    return `${el.tagName.toLowerCase()}${role ? `[role=${role}]` : ''}${n ? ` "${n}"` : ''}`
  }
  const shown = (el: Element) => {
    const r = el.getBoundingClientRect()
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility === 'visible'
  }
  const regions = new WeakMap<Element, string>()
  let regionCount = 0
  const REGION = 'nav,main,header,footer,aside,dialog,form,[role=dialog],[role=region],[role=navigation],' +
    '[role=main],[role=toolbar],[role=tablist],[role=menu],[role=listbox],[role=grid]'
  /** The landmark or composite widget a focus stop sits in, as an id unique to this visit. */
  const region = (el: Element) => {
    const r = el.parentElement?.closest(REGION) ?? document.body
    if (!regions.has(r)) regions.set(r, `${r.tagName.toLowerCase()}#${++regionCount}`)
    return regions.get(r)!
  }
  w.__pcq = { rgba, over, backdrop, contrast, name, desc, key, shown, region }
}

/** The in-page helper object `install` defines, as the evaluated functions see it. */
export interface Pcq {
  rgba(c: string): number[]
  over(top: number[], under: number[]): number[]
  backdrop(el: Element | null): number[]
  contrast(a: number[], b: number[]): number
  name(el: Element): string
  desc(el: Element): string
  key(el: Element): string
  shown(el: Element): boolean
  region(el: Element): string
}

// ── 1. axe, the non-axe detectors, the accessibility tree ────────────────────────────────────────

/** axe's serious and critical violations: the blocking bar, unchanged from the a11y sweep. */
export async function axeFindings(page: Page, route = ''): Promise<string[]> {
  // The cast is the type of the one argument: `@axe-core/playwright` declares its `Page` from a
  // second copy of playwright-core in this tree, structurally the same as the runner's.
  type AxePage = ConstructorParameters<typeof AxeBuilder>[0]['page']
  const results = await new AxeBuilder({ page: page as unknown as AxePage }).withTags(isBuddyRoute(route) ? BUDDY_AXE_TAGS : AXE_TAGS).analyze()
  return results.violations
    .filter((v) => BLOCKING.has(v.impact ?? ''))
    .map((v) => `[${v.impact}] ${v.id}: ${v.help} — ${v.nodes.length} node(s): ` +
      v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(', '))
}

/** A mouse-only control and a clipped focus ring: the two defects axe has no rule for. */
export async function detectorFindings(page: Page): Promise<string[]> {
  const mouseOnly = await page.evaluate<string[]>(`(${MOUSE_ONLY})()`)
  const clipped = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  return [...mouseOnly.map((m) => `mouse-only (WCAG 2.1.1): ${m}`), ...clipped.map((c) => `clipped focus ring (WCAG 2.4.7): ${c}`)]
}

/** Text that changes on its own between runs, folded so a baseline holds only what a change made. */
export function normalizeTree(raw: string): string {
  const folds = [...runFolds(), ...HOST_FOLDS]
  return raw
    .split('\n')
    .map((line) =>
      folds.reduce((l, [pattern, to]) => l.replace(pattern, to), line)
        .replace(/\bGood (morning|afternoon|evening)\b/g, 'Good <time of day>')
        // One baseline serves both themes, and the theme controls name the theme they are in
        // (`Theme: light — switch to system`, Settings › design's `Mode: Light [pressed]` and its
        // `strong: light`). With every theme word folded, the two themes give the same lines.
        .replace(/\b(light|dark|system)\b/gi, '<theme>')
        // The host the gateway runs on: one baseline is read on macOS and on Linux runners alike,
        // and the dashboard and Settings › system name the platform.
        .replace(/\b(darwin|linux|win32)\b/gi, '<platform>')
        // Every number, but not the `2` of the `<e2e home>` a host fold above already wrote.
        .replace(/(?<!<e)\d+/g, '#')
        // A clock time's half of the day flips with the hour the run starts (Settings › devices'
        // session expiry). Only one that follows a time is folded; `\s` takes the narrow no-break
        // space Chromium's `toLocaleString` puts before it.
        .replace(/(#:#)\s?(AM|PM)\b/g, '$1 <am/pm>')
        // A day strip labels its last days by weekday (Learning's capture strip: `Tue — not
        // started`), so the labels move on by one each day. Only a weekday the strip writes before
        // a day's count, never one that names a column or a date (`Mon #`, `Tue, Nov #`).
        .replace(/\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?= — )/g, '<day>')
        // The machine's CPU reads `—` until the gateway's first CPU sample lands: the Settings hub's
        // System tile (`CPU —`) and Settings › system's health strip (`— cpu`).
        .replace(/\bCPU (—|#%)/g, 'CPU <cpu>')
        .replace(/(?<=^|\s)(—|#%) cpu\b/g, '<cpu> cpu')
        // The gateway's uptime there is `#m #s` for its first hour and `#h #m` after it.
        .replace(/(?<=^|\s)#[hm] #[ms] uptime\b/g, '<uptime> uptime')
        // State the gateway's timing decides, not the surface: whether a popover is open when the
        // tree is read, and whether the bell has an unread count yet.
        .replace(/ \[expanded\]/g, '')
        .replace(/("Notifications), # unread"(: "#")?/g, '$1"')
        // Text the clock and the gateway's own work decide: a relative time, what the update
        // check has answered so far, and which scheduled jobs the week view lists for a day.
        // No leading `\b`: a `#` after a space has no word boundary before it, so `#m ago` slipped
        // through and was then read as a countdown.
        // Settings › durability spells minutes `# min ago` (`relativeTime`), after a "just now", and
        // the Doctor's snapshot row says "under a minute ago" before its first minute.
        .replace(/(just now|under a minute ago|#\s?[smhd] ago|#\s?(second|min|minute|hour|day|month)s? ago)\b/g, '<when>')
        // The run's own home, which the harness makes fresh per run in the OS temp folder (#4864),
        // so its path differs on every run and every machine.
        .replace(/\/\S*?personalclaw-e#e[^/\s]*/g, '<e2e home>')
        .replace(/\b(Up to date|Update available( — #\.#\.#)?)/g, '<update status>')
        .replace(/# fires?: .*?(?= (Sun|Mon|Tue|Wed|Thu|Fri|Sat), |"|$)/g, '<fires>')
        // An automation's last run on the Triggers page: the heartbeat runs every few seconds,
        // so whether it has run yet ("never") or not ("noop <when> · schedule") is timing.
        .replace(/runs on its own (never|\S+ <when> · \S+)$/g, 'runs on its own <last run>')
        // The countdown to an automation's next run: its unit moves with the clock (`in #m`, then
        // `in #h`), so a baseline names only that there is one.
        .replace(/ in <?#[smhd]\b(?! ago)/g, ' in <countdown>')
        .trimEnd(),
    )
    .join('\n')
}

/** The page's accessibility tree (Playwright's ARIA snapshot), normalized. */
export async function accessibilityTree(page: Page): Promise<string> {
  return normalizeTree(await page.locator('body').ariaSnapshot())
}

/** Every line the tree gained or lost against its baseline, as a multiset. */
export function treeFindings(actual: string, baseline: string | null, adopt = false): string[] {
  if (baseline === null) {
    if (adopt) return []
    return ['no accessibility-tree baseline is committed for this surface: run `npx playwright test e2e/surfaces.spec.ts --update-snapshots` and review the file it writes']
  }
  const count = (s: string) => {
    const m = new Map<string, number>()
    for (const l of s.split('\n').filter((x) => x.trim())) m.set(l, (m.get(l) ?? 0) + 1)
    return m
  }
  const [a, b] = [count(actual), count(baseline.trimEnd())]
  const out: string[] = []
  for (const [l, n] of a) for (let i = b.get(l) ?? 0; i < n; i++) out.push(`+ ${l.trim()}`)
  for (const [l, n] of b) for (let i = a.get(l) ?? 0; i < n; i++) out.push(`- ${l.trim()}`)
  return out.length > 30 ? [...out.slice(0, 30), `… and ${out.length - 30} more changed lines`] : out
}

// ── 2. reduced motion ────────────────────────────────────────────────────────────────────────────

/** Elements still declaring motion over 50 ms with the preference set, or a note that the
 *  preference was never in force. 🪤 `seedTheme`'s `emulateMedia({ colorScheme })` clears a
 *  `reducedMotion` emulation, so the step sets both itself, and proves it took before measuring:
 *  the leg this replaces once passed while measuring a user with no preference at all. `emulate:
 *  false` exists for the control spec, which holds that proof to a page without the preference. */
export async function reducedMotionFindings(page: Page, opts: { theme: Theme; emulate: boolean }): Promise<string[]> {
  await page.emulateMedia({ colorScheme: opts.theme, reducedMotion: opts.emulate ? 'reduce' : 'no-preference' })
  try {
    // Entrance motion may have STARTED; what must not happen is it still running or declared.
    await page.waitForTimeout(300)
    const m = await page.evaluate(() => {
      const long: string[] = []
      for (const el of document.querySelectorAll('*')) {
        const cs = getComputedStyle(el)
        const td = Math.max(...cs.transitionDuration.split(',').map((s) => parseFloat(s) || 0))
        const ad = Math.max(...cs.animationDuration.split(',').map((s) => parseFloat(s) || 0))
        const animated = ad > 0.05 && cs.animationName !== 'none'
        if (td > 0.05 || animated) {
          const e = el as HTMLElement
          const id = e.id ? `#${e.id}` : ''
          const cls = typeof e.className === 'string' ? e.className.split(/\s+/).filter(Boolean).slice(0, 3).join('.') : ''
          long.push(`<${el.tagName.toLowerCase()}${id}${cls ? ' .' + cls : ''}> transition=${td}s animation=${animated ? `${cs.animationName} ${ad}s` : 'none'}`)
        }
      }
      return { mq: matchMedia('(prefers-reduced-motion: reduce)').matches, long }
    })
    if (!m.mq) {
      return ['prefers-reduced-motion is NOT emulated on this page, so this step measured a user with no preference']
    }
    return m.long.slice(0, 12).concat(m.long.length > 12 ? [`… and ${m.long.length - 12} more`] : [])
  } finally {
    await page.emulateMedia({ colorScheme: opts.theme, reducedMotion: 'no-preference' })
  }
}

// ── 3. widths: overflow, and 44 px targets where the touch-target rule applies ─────────────────────

/** Whether the document pans sideways, and every visible element past the right edge that no
 *  horizontal scroller makes reachable. Calibrated by the phone leg this replaces: an element
 *  inside an `overflow-x: auto` row is a design, and a `visibility: hidden` measuring copy cannot
 *  overflow for the user. */
export async function overflowFindings(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const vw = window.innerWidth
    const out: string[] = []
    const doc = document.documentElement
    if (doc.scrollWidth > doc.clientWidth + 1) out.push(`the page scrolls sideways: scrollWidth ${doc.scrollWidth} > clientWidth ${doc.clientWidth}`)
    const inScroller = (el: Element) => {
      for (let p = el.parentElement; p && p !== doc; p = p.parentElement) {
        const ox = getComputedStyle(p).overflowX
        if (ox === 'auto' || ox === 'scroll') return true
      }
      return false
    }
    const off: string[] = []
    for (const el of document.querySelectorAll('*')) {
      const r = el.getBoundingClientRect()
      if (!r.width || !r.height || getComputedStyle(el).visibility !== 'visible') continue
      if (r.right > vw + 1 && !inScroller(el)) {
        const e = el as HTMLElement
        const id = e.id ? `#${e.id}` : ''
        const cls = typeof e.className === 'string' ? e.className.split(/\s+/).filter(Boolean).slice(0, 3).join('.') : ''
        off.push(`<${el.tagName.toLowerCase()}${id}${cls ? ' .' + cls : ''}> right=${Math.round(r.right)}px`)
      }
    }
    return out.concat(off.slice(0, 10), off.length > 10 ? [`… and ${off.length - 10} more past the edge`] : [])
  })
}

/** Every visible control whose hit area is under 44 px in either dimension, keyed for a baseline.
 *  The hit area is the control's box, or its nearest `label` / `[role=row]` / list-row wrapper when
 *  that wrapper is what takes the click, so a 16 px checkbox in a 48 px row is not a finding. */
export async function smallTargets(page: Page): Promise<string[]> {
  await page.evaluate(install)
  return page.evaluate((min) => {
    const q = (window as unknown as { __pcq: Pcq }).__pcq
    const SEL = 'a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),' +
      'textarea:not([disabled]),summary,[role=button],[role=link],[role=tab],[role=menuitem],[role=switch],' +
      '[role=checkbox],[role=radio],[role=option],[tabindex]:not([tabindex="-1"])'
    const out = new Set<string>()
    for (const el of document.querySelectorAll(SEL)) {
      if (!q.shown(el)) continue
      const hit = el.closest('label') ?? el
      const r = hit.getBoundingClientRect()
      // Off-screen or clipped to nothing: not something the user can be asked to hit here.
      if (r.right <= 0 || r.bottom <= 0 || r.left >= window.innerWidth) continue
      if (r.width < min - 0.5 || r.height < min - 0.5) out.add(q.key(el))
    }
    return [...out].sort()
  }, MIN_TARGET_PX)
}

/** Findings for one compact width: a small target the baseline does not name, and a baseline entry
 *  the page no longer has (the baseline is self-clearing, like the keyboard allowances). */
export function targetFindings(actual: string[], baseline: string[] | undefined, adopt = false): string[] {
  if (baseline === undefined) {
    if (adopt) return []
    return ['no 44 px target baseline is committed for this width: run `npx playwright test e2e/surfaces.spec.ts --update-snapshots` and review the file it writes']
  }
  const base = new Set(baseline)
  const now = new Set(actual)
  return [
    ...actual.filter((k) => !base.has(k)).map((k) => `under ${MIN_TARGET_PX} px: ${k}`),
    ...[...base].filter((k) => !now.has(k)).map((k) => `baselined as under ${MIN_TARGET_PX} px but now ${MIN_TARGET_PX} px or more: delete it from the baseline: ${k}`),
  ]
}

// ── 4. 200% text zoom ──────────────────────────────────────────────────────────────────────────────

/** Text that is cut off once the root font size doubles (WCAG 1.4.4). Read and restored in ONE
 *  synchronous pass, so no React render lands between: only text clipped by the zoom is reported,
 *  not text already clipped at 100% (an ellipsis truncation is a design). */
export async function zoomFindings(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    // Clipped, per axis: content past an edge that does not scroll is content the reader loses.
    // An axis that scrolls (`auto`, `scroll`) keeps it reachable, and a box that truncates with an
    // ellipsis says it is truncating; neither is a finding. Both were, in the first measurement:
    // the nav rail scrolls vertically and was reported on every surface for being tall.
    const hidden = (v: string) => v === 'hidden' || v === 'clip'
    const measure = () => {
      const cut = new Map<Element, string>()
      for (const el of document.querySelectorAll('body *')) {
        const cs = getComputedStyle(el)
        const clipX = hidden(cs.overflowX) && cs.textOverflow !== 'ellipsis'
        const clipY = hidden(cs.overflowY)
        if (!clipX && !clipY) continue
        const e = el as HTMLElement
        if (!e.textContent?.trim() || cs.visibility !== 'visible' || !e.clientHeight) continue
        if ((clipY && e.scrollHeight > e.clientHeight + 1) || (clipX && e.scrollWidth > e.clientWidth + 1)) {
          cut.set(el, `${e.scrollWidth}x${e.scrollHeight} in ${e.clientWidth}x${e.clientHeight}`)
        }
      }
      return cut
    }
    const root = document.documentElement
    const before = measure()
    const prior = root.style.fontSize
    root.style.fontSize = `${(parseFloat(getComputedStyle(root).fontSize) || 16) * 2}px`
    const after = measure()
    root.style.fontSize = prior
    const out: string[] = []
    for (const [el, size] of after) {
      if (before.has(el)) continue
      const e = el as HTMLElement
      const id = e.id ? `#${e.id}` : ''
      const text = (e.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40)
      out.push(`<${el.tagName.toLowerCase()}${id}> "${text}" clips at 200%: ${size}`)
    }
    return out.slice(0, 15).concat(out.length > 15 ? [`… and ${out.length - 15} more`] : [])
  })
}

// ── 5. the keyboard walk: keyboard-only, focus order, rendered contrast ───────────────────────────

/** Focus stops allowed to have no visible indicator. SELF-CLEARING: each entry must still match a
 *  real unannounced stop, so the fix turns the check red until the entry is deleted. An entry names
 *  the stop by its whole accessible name, imported from the module the control reads it from. */
export const KNOWN_UNANNOUNCED: { route?: string; opener?: string; name: string; why: string }[] = [
  {
    opener: 'command palette',
    name: PALETTE_SEARCH_LABEL,
    why: "ui/SearchField's inline variant, deliberately unringed — see SearchField.tsx around the OVERLAY_FOCUS constant.",
  },
]

export interface Stop {
  desc: string
  name: string
  /** Something visible changed on the element, an ancestor or a descendant when it took focus. */
  announced: boolean
  /** A contenteditable whose caret is its indicator, which no computed style can see. */
  credited: boolean
  rect: { left: number; top: number; right: number; bottom: number }
  /** How many line boxes the stop is drawn in: more than one for a link that wraps in running text. */
  lines: number
  region: string
  /** The contrast of the element's own outline or box-shadow ring against what it is drawn on,
   *  when it draws one. An indicator drawn any other way (a background, a border, an ancestor's
   *  `focus-within:`) has no single colour pair to measure, and is left to the keyboard step. */
  ring: number | null
  /** A text field's border against what is behind it, when the border is its only boundary. */
  boundary: number | null
  tag: string
}

/** Tab through the focus ring once, from the top of the page, reading every stop. Stops at the
 *  first repeat (the ring wrapped) or at TAB_CAP, and says which. */
export async function tabWalk(page: Page): Promise<{ stops: Stop[]; capped: boolean }> {
  await page.evaluate(install)
  // Start the walk at the top of the page. 🪤 A blur is not enough: Chromium keeps the sequential
  // focus starting point where the blurred element was, and the clipped-ring detector has just
  // focused every control in turn, leaving it on the LAST one. From there the first Tab leaves the
  // page and the walk reads nothing (measured: 155 of 189 surfaces). A `tabindex=-1` sentinel at
  // the start of <body>, focused and then left, puts the starting point before every stop.
  await page.evaluate(() => {
    const s = document.createElement('span')
    s.id = 'pcq-walk-start'
    s.tabIndex = -1
    document.body.prepend(s)
    s.focus({ preventScroll: true })
  })
  const stops: Stop[] = []
  const seen = new Set<string>()
  let presses = 0
  for (; presses < TAB_CAP; presses++) {
    await page.keyboard.press('Tab')
    const s = await page.evaluate(async () => {
      const el = document.activeElement
      if (!el || el === document.body) return null
      const q = (window as unknown as { __pcq: Pcq }).__pcq
      const e = el as HTMLElement
      // The EFFECTIVE indicator: an outline with style none or zero width paints nothing, though
      // tokens.css's `:focus-visible` still sets its width under Tailwind's `outline-none`.
      const effective = (cs: CSSStyleDeclaration) => {
        const ow = parseFloat(cs.outlineWidth) || 0
        const o = cs.outlineStyle !== 'none' && ow > 0 ? `o:${ow}:${cs.outlineStyle}:${cs.outlineColor}` : ''
        const sh = cs.boxShadow && cs.boxShadow !== 'none' ? `s:${cs.boxShadow}` : ''
        return [o, sh, `bd:${cs.borderColor}`, `bg:${cs.backgroundColor}`, `fg:${cs.color}`, `op:${cs.opacity}`].join('~')
      }
      // The element, its three nearest ancestors (`focus-within:`, `has-[…:focus-visible]`) and up
      // to 25 descendants (`group-focus-visible:` on the child that paints the seam).
      const sig = () => {
        const parts: string[] = []
        let cur: Element | null = el
        for (let i = 0; i < 4 && cur; i++, cur = cur.parentElement) parts.push(effective(getComputedStyle(cur)))
        for (const k of Array.from(el.querySelectorAll('*')).slice(0, 25)) parts.push(effective(getComputedStyle(k)))
        return parts.join('||')
      }
      const r = e.getBoundingClientRect()
      const base = {
        desc: q.desc(el), name: q.name(el), region: q.region(el), tag: el.tagName.toLowerCase(),
        rect: { left: r.left, top: r.top, right: r.right, bottom: r.bottom },
        lines: e.getClientRects().length,
      }
      // Read while focused: the ring, and a text field's border.
      const cs = getComputedStyle(e)
      const behind = q.backdrop(e.parentElement)
      let ring: number | null = null
      // A box-shadow ring is one layer of several (Tailwind writes inset shadow, inset ring, ring
      // offset, ring and shadow, most of them transparent or 0 px). The ring is the visible layer
      // with the widest spread; a 0 px ring-offset layer in the page colour is not the indicator.
      const ringLayer = (): string | null => {
        if (!cs.boxShadow || cs.boxShadow === 'none') return null
        const layers = cs.boxShadow.split(/,(?![^(]*\))/)
        let best: { colour: string; spread: number } | null = null
        for (const layer of layers) {
          const colour = layer.match(/(?:rgba?|oklch|oklab|lab|lch|hsla?|color)\([^)]*\)|#[0-9a-f]{3,8}/i)?.[0]
          if (!colour || q.rgba(colour)[3] === 0) continue
          const lengths = layer.replace(colour, '').match(/-?[\d.]+px/g)?.map(parseFloat) ?? []
          const spread = Math.max(lengths[3] ?? 0, lengths[2] ?? 0)
          if (spread > 0 && (!best || spread > best.spread)) best = { colour, spread }
        }
        return best?.colour ?? null
      }
      const readRing = (): number | null => {
        const indicator = cs.outlineStyle !== 'none' && (parseFloat(cs.outlineWidth) || 0) > 0 ? cs.outlineColor : ringLayer()
        if (!indicator) return null
        const c = q.rgba(indicator)
        return c[3] > 0 ? q.contrast(q.over(c, behind), behind) : null
      }
      ring = readRing()
      // A ring paints through a transition, and one read the instant focus lands is the colour it
      // is fading in from (measured: 1.00:1 and 1.11:1 on a run, the settled ring on the next).
      // Only a ring still under the bar once settled is a finding.
      if (ring !== null && ring < 3) {
        await new Promise((res) => setTimeout(res, 300))
        ring = readRing()
      }
      let boundary: number | null = null
      const textEntry = el.matches('input:not([type=checkbox]):not([type=radio]):not([type=range]),textarea,select')
      if (textEntry && (parseFloat(cs.borderTopWidth) || 0) >= 1) {
        const own = q.over(q.rgba(cs.backgroundColor), behind)
        // A field whose own fill already stands out has that fill as its boundary.
        if (q.contrast(own, behind) < 3) boundary = q.contrast(q.over(q.rgba(cs.borderTopColor), behind), behind)
      }
      if (e.isContentEditable && cs.caretColor !== 'transparent') return { ...base, announced: true, credited: true, ring, boundary }
      const settle = () => new Promise((res) => setTimeout(res, 260))
      const focused = sig()
      e.blur()
      const resting = sig()
      e.focus()
      if (focused !== resting) return { ...base, announced: true, credited: false, ring, boundary }
      // A settled re-check before flagging: focus treatments paint through `transition-colors`,
      // and an in-flight transition reads as the value it is leaving.
      await settle()
      const focusedSettled = sig()
      e.blur()
      await settle()
      const restingSettled = sig()
      e.focus()
      return { ...base, announced: focusedSettled !== restingSettled, credited: false, ring, boundary }
    })
    if (!s) break
    if (seen.has(s.desc)) break
    seen.add(s.desc)
    stops.push(s)
  }
  await page.evaluate(() => document.getElementById('pcq-walk-start')?.remove())
  return { stops, capped: presses >= TAB_CAP }
}

export function keyboardFindings(walk: { stops: Stop[]; capped: boolean }, ctx: SurfaceContext): string[] {
  if (walk.stops.length === 0) {
    return [`the Tab walk reached NO focusable element on ${ctx.where}: the walk broke, it did not find a clean surface`]
  }
  const allowed = KNOWN_UNANNOUNCED.filter((k) => (ctx.kind === 'opener' ? k.opener === ctx.opener : k.route === ctx.route))
  const silent = walk.stops.filter((s) => !s.announced)
  return [
    ...silent.filter((s) => !allowed.some((k) => k.name === s.name))
      .map((s) => `focus changes nothing visible (WCAG 2.4.7): ${s.desc}`),
    ...allowed.filter((k) => !silent.some((s) => s.name === k.name))
      .map((k) => `KNOWN_UNANNOUNCED names "${k.name}" here, but that stop now announces its focus: delete the entry (${k.why})`),
  ]
}

/** A stop that runs backwards (WCAG 2.4.3): within one landmark or composite widget, the next stop
 *  sits on the same visual row as the one before it but to its left. Rows that wrap, columns and a
 *  move into another region are a reading order, not a jump. */
export function focusOrderFindings(walk: { stops: Stop[] }): string[] {
  const out: string[] = []
  for (let i = 1; i < walk.stops.length; i++) {
    const [a, b] = [walk.stops[i - 1], walk.stops[i]]
    if (a.region !== b.region) continue
    // A link that wraps in a paragraph has one box spanning both lines, so the next link, on the
    // second line and to the left of where the first began, reads as "same row, backwards" when it
    // is simply the next word. Measured on #/settings/updates' release notes, both themes.
    if (a.lines > 1 || b.lines > 1) continue
    const overlap = Math.min(a.rect.bottom, b.rect.bottom) - Math.max(a.rect.top, b.rect.top)
    const sameRow = overlap > 0.5 * Math.min(a.rect.bottom - a.rect.top, b.rect.bottom - b.rect.top)
    if (sameRow && b.rect.right <= a.rect.left + 1) out.push(`focus moves backwards from ${a.desc} to ${b.desc}`)
  }
  return out
}

/** Focus indicators and text-field boundaries under 3:1 against what they are drawn on (WCAG
 *  1.4.11). Text contrast stays axe's `color-contrast` rule. */
export function contrastFindings(walk: { stops: Stop[] }): string[] {
  const out: string[] = []
  for (const s of walk.stops) {
    if (s.announced && s.ring !== null && s.ring < MIN_NON_TEXT_CONTRAST) {
      out.push(`focus indicator at ${s.ring.toFixed(2)}:1 against its backdrop: ${s.desc}`)
    }
    if (s.boundary !== null && s.boundary < MIN_NON_TEXT_CONTRAST) {
      out.push(`text field boundary at ${s.boundary.toFixed(2)}:1 against its backdrop: ${s.desc}`)
    }
  }
  return out
}

// ── The runner ────────────────────────────────────────────────────────────────────────────────────

/** Hide the toast stack for the rest of the visit. A toast arrives on a timer the surface does not
 *  own (the durability drill's notice lands on whichever page is open) and leaves after 5 s, so
 *  whether it is in the tree, among the small targets or mid-slide past the edge at 390 px depends
 *  on when the step ran. `visibility: hidden` takes it out of the tree and out of every geometric
 *  check. Found by its always-mounted polite live region (`ui/Toaster.tsx`), whose parent is the
 *  stack; on a page without one this does nothing. */
export async function hideToasts(page: Page): Promise<void> {
  await page.evaluate(() => {
    const live = document.querySelector('[role="status"][aria-live="polite"][aria-relevant="additions"]')
    const stack = live?.parentElement
    if (stack && stack.classList.contains('fixed')) stack.style.visibility = 'hidden'
  })
}

async function resize(page: Page, width: number, height: number): Promise<void> {
  await page.setViewportSize({ width, height })
  // Two frames for layout and `matchMedia` listeners (`useIsMobile`), then a beat for the render
  // they schedule.
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))
  await page.waitForTimeout(150)
}

/** Every scroller, and the window, back at its start. The keyboard walk scrolls whatever holds the
 *  stop it is on, and how far it got is timing, so a control it scrolled past sat above the viewport
 *  at the widths and dropped out of the 44 px count on one run and not the next (Workflows'
 *  definitions read its first row's Run button at 320 px on some runs only). Each width measures
 *  the page from its start instead. Two frames after, for a windowed list to draw the rows the
 *  scroll brought back. */
async function scrollToStart(page: Page): Promise<void> {
  await page.evaluate(() => {
    for (const el of document.querySelectorAll('*')) {
      if (el.scrollTop || el.scrollLeft) {
        el.scrollTop = 0
        el.scrollLeft = 0
      }
    }
    window.scrollTo(0, 0)
    return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
  })
}

/** Run every step of the matrix on the loaded page, in `STEP_NAMES` order, and return what the
 *  baselines would be. */
export async function runMatrix(page: Page, ctx: SurfaceContext, baselines: Baselines, step: Step): Promise<Measured> {
  const measured: Measured = { tree: '', targets: {}, known: {} }
  // A baselined step reports only what its baseline does not already name (and a baselined finding
  // that is gone); the raw findings are what the spec writes when it is asked to rebaseline.
  const baselined = (name: string, measure: () => Promise<string[]>) =>
    step(name, async () => {
      const raw = await measure()
      measured.known[name] = [...new Set(raw.map(findingKey))].sort()
      return knownFindings(raw, baselines.known[name], baselines.adopt)
    })
  await hideToasts(page)
  // A panel that is still loading its data is a state the gateway's timing decides, not the
  // surface: wait for the visible "Loading…" placeholders to go (up to 5 s) before any step reads
  // the page. One that never goes is measured as it stands, so a stuck panel still shows.
  await page
    .waitForFunction(() => !document.body.innerText.includes('Loading…'), null, { timeout: 5_000 })
    .catch(() => {})
  await baselined(STEPS.axe, () => axeFindings(page, ctx.route))
  await step(STEPS.detectors, () => detectorFindings(page))
  await step(STEPS.tree, async () => {
    measured.tree = await accessibilityTree(page)
    return treeFindings(measured.tree, baselines.tree, baselines.adopt)
  })
  await step(STEPS.motion, () => reducedMotionFindings(page, { theme: ctx.theme, emulate: true }))
  await baselined(STEPS.zoom, () => zoomFindings(page))
  let walk: { stops: Stop[]; capped: boolean } = { stops: [], capped: false }
  await step(STEPS.keyboard, async () => {
    walk = await tabWalk(page)
    return keyboardFindings(walk, ctx)
  })
  await baselined(STEPS.order, async () => focusOrderFindings(walk))
  await baselined(STEPS.contrast, async () => contrastFindings(walk))
  // Close whatever the walk opened (a mode pill opens on focus) before anything is measured again.
  await page.keyboard.press('Escape')
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur?.())
  await page.waitForTimeout(200)
  for (const w of WIDTHS) {
    await resize(page, w, isCompact(w) ? PHONE_HEIGHT : REST_VIEWPORT.height)
    await scrollToStart(page)
    await baselined(overflowStep(w), () => overflowFindings(page))
    if (isCompact(w)) {
      await step(targetStep(w), async () => {
        measured.targets[String(w)] = await smallTargets(page)
        return targetFindings(measured.targets[String(w)], baselines.targets[String(w)], baselines.adopt)
      })
    }
  }
  return measured
}
