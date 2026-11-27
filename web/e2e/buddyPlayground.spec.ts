import { test, expect, type Locator, type Page, type TestInfo } from '@playwright/test'
import { seedTheme, gotoRoute, settleEntranceAnimations } from './helpers'
import { overflowFindings } from './surfaceChecks'
import type { Theme } from './routes'

// ── The buddy playground's scripted day, recorded (#3892) ────────────────────────────────────────
//
// The playground's "A day" tab steps the scripted day (`src/pages/buddy/scriptedDay.ts`) through
// the real state machine and the real orb. This spec steps it end to end in the browser and saves
// the stage after every step, each a state change of the day, in seven legs: light and dark at the
// rest width, then 320, 390 and 412 px, high contrast, and 200% text zoom. Each screenshot comes
// with an overflow check (nothing past the right edge, no text cut off by a box that does not
// scroll, no control of the card that no scroll can bring into view, no list running into the
// next), and a leg reds on the first step that finds one.
//
// The screenshots are saved, never compared: they are the recording the owner reviews the day by
// (`buddy-day/<leg>/` in the test's output folder), with an index of each step's situation and
// family. The comparison waits for Linux baselines (#3943), so nothing here is platform-bound and
// the spec runs wherever the shared project does. The page reads nothing from the gateway.
//
// The Card tab (#3893) is recorded the same way: the expanded card in each of its states (every
// alert kind, five pieces of work in sight, the running list's own states), saved in light, dark
// and reduced motion, and at 320, 390 and 412 px, each card with the same overflow and cut-off
// check, so the card stacks at the compact widths with nothing clipped.
//
// Each shot is the whole stage or card as a person sees it scrolled to it, once its entrance has
// settled (`shoot` below): an alert's card fades in, and a shot taken at once caught it half drawn.

interface Leg {
  name: string
  theme: Theme
  width: number
  height: number
  contrast?: boolean
  zoom?: boolean
}

const LEGS: readonly Leg[] = [
  { name: 'light', theme: 'light', width: 1280, height: 900 },
  { name: 'dark', theme: 'dark', width: 1280, height: 900 },
  { name: '320', theme: 'light', width: 320, height: 844 },
  { name: '390', theme: 'light', width: 390, height: 844 },
  { name: '412', theme: 'light', width: 412, height: 844 },
  { name: 'high-contrast', theme: 'light', width: 1280, height: 900, contrast: true },
  { name: 'zoom-200', theme: 'light', width: 1280, height: 900, zoom: true },
]

/** The day's last steps and the hold after it are at most this many presses of Next step. */
const STEP_CAP = 60

/** Text a box cuts off: content past an edge that does not scroll and does not say it truncates
 *  (an ellipsis). The same reading as the UI quality check's 200% step, taken as it stands. */
async function clippedFindings(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const hidden = (v: string) => v === 'hidden' || v === 'clip'
    const out: string[] = []
    for (const el of document.querySelectorAll('[data-full-screen] *')) {
      const cs = getComputedStyle(el)
      const clipX = hidden(cs.overflowX) && cs.textOverflow !== 'ellipsis'
      const clipY = hidden(cs.overflowY)
      if (!clipX && !clipY) continue
      const e = el as HTMLElement
      if (!e.textContent?.trim() || cs.visibility !== 'visible' || !e.clientHeight) continue
      // Text for screen readers only (`sr-only`) is a 1 px box by design: it is not cut off.
      if (e.clientWidth <= 1 && e.clientHeight <= 1) continue
      if ((clipY && e.scrollHeight > e.clientHeight + 1) || (clipX && e.scrollWidth > e.clientWidth + 1)) {
        const text = (e.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40)
        out.push(`<${el.tagName.toLowerCase()}> "${text}" is cut off: ${e.scrollWidth}x${e.scrollHeight} in ${e.clientWidth}x${e.clientHeight}`)
      }
    }
    return out
  })
}

/** A control in the buddy's card that no scroll can bring into view: past the edge of a box that
 *  cuts it off and that nobody can scroll (`overflow: hidden`), once every scrolling box between it
 *  and that box has brought it as close as it can. A card taller than its space scrolls inside
 *  itself (`data-buddy-card-surface`), and that is fine; a control a box cuts off is not, as the
 *  own-words field and its Send at 200% text zoom were said to be, as a review of the 200% shots found. */
async function unreachableControls(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = []
    const name = (e: HTMLElement) => (e.getAttribute('aria-label') || e.textContent || e.getAttribute('placeholder') || e.tagName).replace(/\s+/g, ' ').trim().slice(0, 30)
    for (const el of document.querySelectorAll('[data-buddy-card] :is(button, input, textarea, select, a[href])')) {
      const e = el as HTMLElement
      if (!e.getClientRects().length) continue
      const r = e.getBoundingClientRect()
      // Where the control can be brought: its own box, until a box that scrolls holds it, which can
      // bring it to its own top (a control taller than that box shows its top).
      let reach = { top: r.top, bottom: r.bottom, left: r.left, right: r.right }
      for (let a = e.parentElement; a && a !== document.body; a = a.parentElement) {
        const cs = getComputedStyle(a)
        const scrollsY = /auto|scroll/.test(cs.overflowY)
        const scrollsX = /auto|scroll/.test(cs.overflowX)
        const clipsY = /hidden|clip/.test(cs.overflowY)
        const clipsX = /hidden|clip/.test(cs.overflowX)
        if (!scrollsY && !scrollsX && !clipsY && !clipsX) continue
        const b = a.getBoundingClientRect()
        const box = { top: b.top + a.clientTop, left: b.left + a.clientLeft, bottom: b.top + a.clientTop + a.clientHeight, right: b.left + a.clientLeft + a.clientWidth }
        if (scrollsY) reach = { ...reach, top: box.top, bottom: box.top + Math.min(reach.bottom - reach.top, a.clientHeight) }
        if (scrollsX) reach = { ...reach, left: box.left, right: box.left + Math.min(reach.right - reach.left, a.clientWidth) }
        const cutY = clipsY && (reach.top < box.top - 0.5 || reach.bottom > box.bottom + 0.5)
        const cutX = clipsX && (reach.left < box.left - 0.5 || reach.right > box.right + 0.5)
        if (cutY || cutX) {
          out.push(`"${name(e)}" is cut off by <${a.tagName.toLowerCase()}>, and nothing scrolls to it`)
          break
        }
      }
    }
    return out
  })
}

/** A list of the stage that spills sideways into the next: its content wider than its column. At
 *  200% text zoom the running list's lines ran over Focused work, as a review of the 200% shots found. */
async function spillingLists(page: Page): Promise<string[]> {
  return page.evaluate(() => [...document.querySelectorAll('[data-stage] section[role="region"]')]
    .filter((s) => s.scrollWidth > s.clientWidth + 1)
    .map((s) => `"${s.getAttribute('aria-label')}" spills past its column: ${s.scrollWidth} in ${s.clientWidth}`))
}

/** What of `target` is out of sight: each edge past the visible box of a scrolling ancestor or of
 *  the window, in px. An element shot draws that part as the scroller's own background. */
function outOfSight(target: Locator): Promise<string[]> {
  return target.evaluate((el) => {
    const r = el.getBoundingClientRect()
    const out: string[] = []
    const within = (name: string, top: number, bottom: number) => {
      if (r.top < top - 0.5) out.push(`${Math.round(top - r.top)} px above ${name}`)
      if (r.bottom > bottom + 0.5) out.push(`${Math.round(r.bottom - bottom)} px below ${name}`)
    }
    for (let a = el.parentElement; a; a = a.parentElement) {
      if (!/auto|scroll|hidden|clip/.test(getComputedStyle(a).overflowY)) continue
      const top = a.getBoundingClientRect().top + a.clientTop
      within(`<${a.tagName.toLowerCase()}>`, top, top + a.clientHeight)
    }
    within('the window', 0, window.innerHeight)
    return out
  })
}

/** Saves `target` whole, as a person sees it scrolled to it, once its entrance has settled.
 *
 *  The page scrolls inside the shell, not the document, and an element shot draws only what that
 *  scroller shows. At 390 px the stage is taller than the 844 px window, and its shots came out
 *  with the shell's background over the stage's top, the orb under it. So when the target is
 *  taller than its scroller, the window grows to fit it for the shot, at the same width (the
 *  stage's layout reads its width, never the window's height), and goes back after. The shot
 *  reds if any part of the target is still out of sight, or if its entrance has not settled:
 *  an alert's card fades in, and a shot taken at once caught it half drawn. The wait is the one
 *  the axe checks take for Home's entrance. */
async function shoot(page: Page, target: Locator, path: string, size: { width: number; height: number }): Promise<void> {
  const short = await target.evaluate((el) => {
    let a = el.parentElement
    while (a && !/auto|scroll/.test(getComputedStyle(a).overflowY)) a = a.parentElement
    return Math.max(0, Math.ceil(el.getBoundingClientRect().height - (a ? a.clientHeight : window.innerHeight)))
  })
  if (short) await page.setViewportSize({ width: size.width, height: size.height + short + 32 })
  await target.evaluate((el) => el.scrollIntoView({ block: 'center' }))
  // The mouse rests where it last pressed, and a scroll can bring the card under it: a hovered
  // answer drew as picked. The page's corner is in its margin, off the stage and the cards.
  await page.mouse.move(0, 0)
  expect(await settleEntranceAnimations(page, 5_000), `${path}: its entrance did not settle`).toBe(true)
  expect(await outOfSight(target), `${path}: part of it is out of sight`).toEqual([])
  await target.screenshot({ path })
  if (short) await page.setViewportSize({ width: size.width, height: size.height })
}

/** Where the stage is: its situation and family, as the page marks them. */
async function stageState(page: Page): Promise<{ situation: string; family: string }> {
  const stage = page.locator('[data-stage]')
  return { situation: (await stage.getAttribute('data-situation')) ?? '', family: (await stage.getAttribute('data-family')) ?? '' }
}

async function recordDay(page: Page, info: TestInfo, leg: Leg): Promise<void> {
  await page.setViewportSize({ width: leg.width, height: leg.height })
  await seedTheme(page, leg.theme)
  if (leg.contrast) await page.emulateMedia({ forcedColors: 'active', contrast: 'more' })
  await gotoRoute(page, 'buddy/playground?tab=day')
  if (leg.zoom) {
    await page.evaluate(() => {
      const root = document.documentElement
      root.style.fontSize = `${(parseFloat(getComputedStyle(root).fontSize) || 16) * 2}px`
    })
  }
  const next = page.getByRole('button', { name: 'Next step' })
  const clock = page.getByRole('group', { name: 'Play the day' }).getByRole('heading')
  const index: string[] = []
  const families = new Set<string>()
  for (let n = 1; n <= STEP_CAP && (await next.isEnabled()); n++) {
    const before = await clock.textContent()
    await next.click()
    await expect(clock, `step ${n} did not move the day's clock`).not.toHaveText(before ?? '')
    const { situation, family } = await stageState(page)
    if (family !== 'none') families.add(family)
    const shot = `${String(n).padStart(2, '0')}-${situation}.png`
    await shoot(page, page.locator('[data-stage]'), info.outputPath('buddy-day', leg.name, shot), leg)
    const found = [
      ...(await overflowFindings(page)), ...(await clippedFindings(page)),
      ...(await unreachableControls(page)), ...(await spillingLists(page)),
    ]
    index.push(`${shot}\t${situation}\t${family}${found.length ? `\t${found.join('; ')}` : ''}`)
    expect(found, `step ${n} (${situation}) on the ${leg.name} leg`).toEqual([])
  }
  await info.attach(`buddy-day-${leg.name}.txt`, { body: index.join('\n'), contentType: 'text/plain' })
  await expect(next, 'the day did not end within the cap').toBeDisabled()
  expect(index.length, 'the day took no step').toBeGreaterThan(20)
  // The day passes through every status family, at every width and in every look.
  expect([...families].sort()).toEqual(['active', 'blocked', 'quiet', 'waiting'])
}

test.describe('the buddy playground: the scripted day, recorded', () => {
  test.describe.configure({ timeout: 180_000 })
  for (const leg of LEGS) {
    test(`the day at ${leg.name}`, async ({ page }, info) => {
      await recordDay(page, info, leg)
    })
  }
})

const CARD_LEGS: readonly (Leg & { reduced?: boolean })[] = [
  { name: 'light', theme: 'light', width: 1280, height: 900 },
  { name: 'dark', theme: 'dark', width: 1280, height: 900 },
  { name: 'reduced-motion', theme: 'light', width: 1280, height: 900, reduced: true },
  { name: '320', theme: 'light', width: 320, height: 844 },
  { name: '390', theme: 'light', width: 390, height: 844 },
  { name: '412', theme: 'light', width: 412, height: 844 },
]

test.describe('the buddy playground: the expanded card, recorded', () => {
  test.describe.configure({ timeout: 120_000 })
  for (const leg of CARD_LEGS) {
    test(`the card at ${leg.name}`, async ({ page }, info) => {
      await page.setViewportSize({ width: leg.width, height: leg.height })
      await seedTheme(page, leg.theme)
      if (leg.reduced) await page.emulateMedia({ reducedMotion: 'reduce' })
      await gotoRoute(page, 'buddy/playground?tab=card')
      const fixtures = page.locator('[data-card-fixture]')
      const keys = await fixtures.evaluateAll((els) => els.map((e) => e.getAttribute('data-card-fixture') ?? ''))
      expect(keys.length, 'the card tab draws its fixtures').toBeGreaterThanOrEqual(10)
      for (const key of keys) {
        const fixture = page.locator(`[data-card-fixture="${key}"]`)
        await shoot(page, fixture, info.outputPath('buddy-card', leg.name, `${key}.png`), leg)
        const found = [...(await overflowFindings(page)), ...(await clippedFindings(page))]
        expect(found, `the ${key} card on the ${leg.name} leg`).toEqual([])
      }
      // Each card opens below the buddy's one orb and draws no orb of its own (#5216).
      await expect(page.locator('[data-card-fixture] [data-buddy-orb-slot]')).toHaveCount(keys.length)
      await expect(page.locator('[data-buddy-card] [data-buddy-orb]')).toHaveCount(0)
    })
  }
})

// ── The orb holds its box (#5216) ──────────────────────────────────────────────────────────────
//
// On the Play tab, every control of the matrix in its panel's order, each followed by the next
// timer firing, three rounds (the second one floating), and the clock run between rounds: wherever the orb shows, its box, read in the
// browser, is the one it had the first time it showed. Every card opens, grows, shrinks and closes
// on the way (an approval, a broad-reach approval, a question, an error, Running now and the step
// window), and the orb's box is read before and after each control too. Light at the rest width,
// and at 320 px with 200% text zoom.

/** The orb's box on the stage's screen, relative to the screen, or null while nothing shows. */
async function orbBox(page: Page): Promise<string | null> {
  return page.evaluate(() => {
    const slot = document.querySelector('[data-stage-screen] [data-buddy-orb-slot]')
    const screen = document.querySelector('[data-stage-screen]')
    if (!slot || !screen) return null
    const a = slot.getBoundingClientRect()
    const s = screen.getBoundingClientRect()
    return [a.left - s.left, a.top - s.top, a.width, a.height].map((n) => Math.round(n * 10) / 10).join(',')
  })
}

const BOX_LEGS = [
  { name: 'light', width: 1280, height: 900, zoom: false },
  { name: '320 at 200%', width: 320, height: 844, zoom: true },
]

test.describe('the buddy playground: the orb holds its box', () => {
  test.describe.configure({ timeout: 300_000 })
  for (const leg of BOX_LEGS) {
    test(`across the matrix, at ${leg.name}`, async ({ page }) => {
      await page.setViewportSize({ width: leg.width, height: leg.height })
      await seedTheme(page, 'light')
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await gotoRoute(page, 'buddy/playground?tab=play')
      if (leg.zoom) {
        await page.evaluate(() => {
          const root = document.documentElement
          root.style.fontSize = `${(parseFloat(getComputedStyle(root).fontSize) || 16) * 2}px`
        })
      }
      const labels = await page.locator('[data-playground-control]').evaluateAll((els) => els.map((e) => e.getAttribute('data-playground-control') ?? ''))
      expect(labels.length, 'the controls are marked').toBeGreaterThan(30)
      let first: string | null = null
      const seen = new Set<string>()
      const nextTimer = page.getByRole('group', { name: 'Clock' }).getByRole('button', { name: /^Next timer/ })
      const press = async (id: string) => {
        const button = id === 'timer' ? nextTimer : page.locator(`[data-playground-control="${id}"] button`)
        if (!(await button.isEnabled())) return
        const before = await orbBox(page)
        await button.click()
        const after = await orbBox(page)
        const room = (await page.locator('[data-stage-screen]').getAttribute('data-room')) ?? 'none'
        seen.add(room)
        for (const [when, box] of [['before', before], ['after', after]] as const) {
          if (box === null) continue
          first ??= box
          expect(box, `${id} (${when}), in ${room}`).toBe(first)
        }
      }
      for (let round = 0; round < 3; round++) {
        if (round === 1) for (const id of ['start', 'drag', 'dropFloating']) await press(id)
        for (const id of labels) {
          if (round === 1 && id === 'dropDocked') continue
          await press(id)
          await press('timer')
        }
        await page.getByRole('button', { name: '+10 min' }).click()
      }
      expect(first, 'the orb showed').not.toBeNull()
      for (const room of ['peek', 'compact', 'expanded', 'floating']) expect([...seen], room).toContain(room)
    })
  }
})
