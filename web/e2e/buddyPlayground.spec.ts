import { test, expect, type Page, type TestInfo } from '@playwright/test'
import { seedTheme, gotoRoute } from './helpers'
import { overflowFindings } from './surfaceChecks'
import type { Theme } from './routes'

// ── The buddy playground's scripted day, recorded (#3892) ────────────────────────────────────────
//
// The playground's "A day" tab steps the scripted day (`src/pages/buddy/scriptedDay.ts`) through
// the real state machine and the real orb. This spec steps it end to end in the browser and saves
// the stage after every step, each a state change of the day, in seven legs: light and dark at the
// rest width, then 320, 390 and 412 px, high contrast, and 200% text zoom. Each screenshot comes
// with an overflow check (nothing past the right edge, no text cut off by a box that does not
// scroll), and a leg reds on the first step that finds one.
//
// The screenshots are saved, never compared: they are the recording the owner reviews the day by
// (`buddy-day/<leg>/` in the test's output folder), with an index of each step's situation and
// family. The comparison waits for Linux baselines (#3943), so nothing here is platform-bound and
// the spec runs wherever the shared project does. The page reads nothing from the gateway.

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
      if ((clipY && e.scrollHeight > e.clientHeight + 1) || (clipX && e.scrollWidth > e.clientWidth + 1)) {
        const text = (e.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40)
        out.push(`<${el.tagName.toLowerCase()}> "${text}" is cut off: ${e.scrollWidth}x${e.scrollHeight} in ${e.clientWidth}x${e.clientHeight}`)
      }
    }
    return out
  })
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
    await page.locator('[data-stage]').screenshot({ path: info.outputPath('buddy-day', leg.name, shot) })
    const found = [...(await overflowFindings(page)), ...(await clippedFindings(page))]
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
