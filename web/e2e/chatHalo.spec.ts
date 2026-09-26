import { test, expect, type Page } from '@playwright/test'
import { gotoRoute, driveScriptedTurns, seedTheme } from './helpers'
import { THEMES } from './routes'

// ── The composer's halo on an ONGOING chat: no hard edge, no floating shapes ───────────────────
//
// Owner report (2026-09-25), on the chat session page: "weird shadow shapes that change from time
// to time", and the halo around the composer "gets cut off and I can see a hard edge where it
// should be a smooth halo". Both measured on a fresh home before the fix (1440×900, default design):
//
//  · THE HARD EDGE. The halo is `ui/DotGlow`'s bloom, reaching ~180px past the composer inside a
//    layer that clips at its own box. A docked composer sits one page gutter (16px) from that box's
//    bottom and, at the shipped `full` width, its sides — so the halo reached the page bottom and
//    the rail at ~94% strength and stopped in a straight line (dark: red 47 on a canvas of 15;
//    light: a pink field ending on a vertical line at the rail). Focused, the composer's own lift
//    shadow was cut the same way (light: (185,165,163) in the last row on a (240,244,248) canvas).
//  · THE SHAPES. While a reply streamed, a second light parked a ~180px smudge behind the
//    transcript and climbed the page with the auto-scroll.
//
// This spec measures the PAINTED result, which jsdom cannot: thin strips along the stage's edges
// must be the canvas colour (the halo has faded to nothing before the clip), with a positive
// control proving the same probe does see the halo where it is meant to be.

/** Median of the max-channel distance from the canvas colour over a screenshot region. A median,
 *  so the sparse wave dots the layer draws cannot move it — a cut halo is a uniform band and moves
 *  it by tens of levels. */
async function medianDeviation(page: Page, clip: { x: number; y: number; width: number; height: number }): Promise<number> {
  const png = await page.screenshot({ clip, animations: 'allow' })
  return page.evaluate(async (b64) => {
    const img = new Image()
    img.src = `data:image/png;base64,${b64}`
    await img.decode()
    const c = document.createElement('canvas')
    c.width = img.width
    c.height = img.height
    const g = c.getContext('2d')!
    g.drawImage(img, 0, 0)
    const { data } = g.getImageData(0, 0, c.width, c.height)
    const bg = (getComputedStyle(document.body).backgroundColor.match(/\d+/g) ?? []).slice(0, 3).map(Number)
    const devs: number[] = []
    for (let i = 0; i < data.length; i += 4) {
      devs.push(Math.max(Math.abs(data[i] - bg[0]), Math.abs(data[i + 1] - bg[1]), Math.abs(data[i + 2] - bg[2])))
    }
    devs.sort((a, b) => a - b)
    return devs[Math.floor(devs.length / 2)]
  }, png.toString('base64'))
}

type Box = { left: number; top: number; right: number; bottom: number }
const boxOf = (page: Page, selector: string) =>
  page.locator(selector).first().evaluate((el) => {
    const r = el.getBoundingClientRect()
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom }
  })

/** At most this many levels off the canvas in the median of an edge strip. Sub-perceptual; a cut
 *  halo measured 30+ on dark and 55+ on light. */
const AT_REST = 4

async function expectHaloContained(page: Page, label: string) {
  const stage: Box = await boxOf(page, '[data-dot-glow]')
  // `data-composer-stage` is the box DotGlow measures (the ComposerStage), not the wider
  // `data-tour="chat"` wrapper that also holds the chips above the composer.
  const composer: Box = await boxOf(page, '[data-composer-stage]')
  const bottom = await medianDeviation(page, { x: composer.left + 40, y: stage.bottom - 2, width: composer.right - composer.left - 80, height: 2 })
  const left = await medianDeviation(page, { x: stage.left, y: composer.top + 24, width: 2, height: Math.max(8, composer.bottom - composer.top - 48) })
  const right = await medianDeviation(page, { x: stage.right - 2, y: composer.top + 24, width: 2, height: Math.max(8, composer.bottom - composer.top - 48) })
  // POSITIVE CONTROL: the same probe, just above the composer, where the halo is at its brightest.
  // Without it a probe aimed at the wrong box would read "canvas" everywhere and pass for nothing.
  const lit = await medianDeviation(page, { x: composer.left + 40, y: composer.top - 8, width: composer.right - composer.left - 80, height: 2 })
  expect(lit, `${label}: the probe cannot see the halo even where it is brightest — the measurement is broken`).toBeGreaterThan(8)
  expect(bottom, `${label}: the stage's last rows are not the canvas colour — a glow or shadow runs into the page bottom and is CUT there, not faded`).toBeLessThanOrEqual(AT_REST)
  expect(left, `${label}: the stage's left edge is not the canvas colour — a glow or shadow is cut at the rail / viewport edge`).toBeLessThanOrEqual(AT_REST)
  expect(right, `${label}: the stage's right edge is not the canvas colour — a glow or shadow is cut there`).toBeLessThanOrEqual(AT_REST)
}

for (const theme of THEMES) {
  test.describe(`the composer halo on an ongoing chat (${theme})`, () => {
    test('fades out before every edge of its stage — at rest and focused', async ({ page }) => {
      await seedTheme(page, theme)
      await gotoRoute(page, 'chat')
      await driveScriptedTurns(page, 'Keep the halo whole', 2)
      await page.evaluate(() => { const a = document.activeElement as HTMLElement | null; a?.blur?.() })
      await page.waitForTimeout(600)
      await expectHaloContained(page, `${theme}, docked, at rest`)

      await page.getByRole('textbox', { name: 'Message input' }).click()
      await page.waitForTimeout(900)   // the focus spring and the 200ms aura fade
      await expectHaloContained(page, `${theme}, docked, focused`)
    })

    test('while a reply streams, the only light is the composer’s', async ({ page }) => {
      await seedTheme(page, theme)
      // Hold the stream open: frames queue until released, so the page sits in `streaming` long
      // enough to look at it (well inside the 3.5s grace before the stall reconciler reads the
      // server, so this is a live stream and not a healed one).
      let hold = false
      const held: (string | Buffer)[] = []
      let release: () => void = () => {}
      await page.routeWebSocket('**/api/ws', (ws) => {
        const server = ws.connectToServer()
        server.onMessage((m) => { if (hold) held.push(m); else ws.send(m) })
        release = () => { hold = false; for (const m of held.splice(0)) ws.send(m) }
      })
      await gotoRoute(page, 'chat')
      await driveScriptedTurns(page, 'Keep the halo whole', 1)

      hold = true
      const composer = page.getByRole('textbox', { name: 'Message input' })
      await composer.click()
      await composer.pressSequentially('And once more, please.', { delay: 3 })
      await page.getByRole('button', { name: 'Send message', exact: true }).click()
      await expect(page.getByRole('button', { name: 'Stop', exact: true }), 'the page never entered its streaming state').toBeVisible({ timeout: 10_000 })
      await page.waitForTimeout(1200)   // long enough for any light to glide to a target and settle

      const stray = await page.evaluate(() => {
        const c = document.querySelector('[data-composer-stage]')!.getBoundingClientRect()
        const out: string[] = []
        for (const el of Array.from(document.querySelectorAll<HTMLElement>('[data-dot-glow] > div'))) {
          const s = getComputedStyle(el)
          if (s.visibility === 'hidden' || Number(s.opacity) < 0.02) continue
          const r = el.getBoundingClientRect()
          const off = Math.max(Math.abs(r.left - c.left), Math.abs(r.top - c.top), Math.abs(r.right - c.right), Math.abs(r.bottom - c.bottom))
          if (off > 2) out.push(`${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}×${Math.round(r.height)} (opacity ${s.opacity})`)
        }
        return out
      })
      release()
      expect(stray, 'a light in the halo layer is not the composer’s — it sits behind the transcript').toEqual([])
      await expect(page.getByRole('button', { name: 'Speak', exact: true }), 'the held turn never completed').toHaveCount(2, { timeout: 30_000 })
    })
  })
}

test.describe('the composer halo at phone width', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })
  // Both themes, because the LIGHT one is where a second offender showed up once the halo stopped
  // covering it: the closed mobile NavRail drawer was parked at exactly `-100%`, and its
  // `shadow-2xl` (38px reach) left a grey strip down the viewport's left edge on every route —
  // (221,224,228) at x=0 on a (240,244,248) canvas. The left-edge probe below reads it.
  for (const theme of THEMES) {
    test(`fades out before the viewport edges on an ongoing chat (${theme})`, async ({ page }) => {
      await seedTheme(page, theme)
      await gotoRoute(page, 'chat')
      await driveScriptedTurns(page, 'Keep the halo whole', 2)
      await page.evaluate(() => { const a = document.activeElement as HTMLElement | null; a?.blur?.() })
      await page.waitForTimeout(600)
      await expectHaloContained(page, `phone, ${theme}, docked, at rest`)
    })
  }
})
