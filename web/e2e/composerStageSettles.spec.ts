import { test, expect } from '@playwright/test'
import { gotoRoute } from './helpers'

// ── The hero's composer moves with the page without a spring, and still flies into the dock ─────
//
// The new-chat hero's composer is a shared-layout element (`ComposerStage`, `layoutId`), so every
// move of it animated: the column re-centring as the shell mounts, a strip landing above it, the
// stage arriving from another page. On CI's idle Chat the stage moved 52 times in the second before the
// window, and its spring's tail was still asking for frames inside it (#3905, R6). The hero's moves
// are now instant (`settled`), and only the flight from the hero into the dock springs.
//
// The plant is a move a person makes: Chat opened from the Loops front door, whose composer is a
// stage with the same `layoutId`, flew the stage from there into the hero on a spring. It now lands
// at once. Then a message is sent, and the chat starting still flies the stage into the dock.

const STAGE = '[data-composer-stage]'
test("the hero's composer takes its place at once when Chat opens from Loops, and flies into the dock when the chat starts", async ({ page }) => {
  await page.addInitScript((stage) => {
    const w = window as typeof window & { __stageWrites?: number }
    w.__stageWrites = 0
    new MutationObserver((list) => {
      for (const m of list) if ((m.target as Element).matches?.(stage)) w.__stageWrites! += 1
    }).observe(document, { subtree: true, attributes: true, attributeFilter: ['style'] })
  }, STAGE)
  const writes = () => page.evaluate(() => (window as typeof window & { __stageWrites?: number }).__stageWrites ?? 0)

  // The stage has one `layoutId` across pages, so opening Chat from the Loops front door, whose
  // composer is a stage too, flew it from the Loops composer's place into the hero.
  await gotoRoute(page, 'loops')
  const atLoops = await writes()
  await page.getByRole('button', { name: 'Chat', exact: true }).first().click()
  await expect(page.getByRole('heading', { level: 1 })).toContainText(/Good (morning|afternoon|evening)/)
  await page.waitForTimeout(1_500)
  // Landing at once writes the stage a handful of times (its transform set and cleared); the spring
  // wrote it 71 to 76 times.
  expect(await writes() - atLoops, 'the hero stage arrived without a spring').toBeLessThanOrEqual(10)
  await page.getByRole('textbox', { name: 'Message input' }).click()
  await page.keyboard.type('Fly the composer into the dock.')

  const typed = await writes()
  await page.keyboard.press('Enter')
  await expect.poll(async () => (await writes()) - typed, { message: 'the flight into the dock springs', timeout: 5_000 })
    .toBeGreaterThan(20)
})

// ── A move made while the composer alone redraws lands at once too ───────────────────────────────
//
// Inside the stage, the composer's own box (the one that rises on focus and drag-over) is a layout
// element of its own. A render of the composer alone, from its own state or a context it reads, takes
// that box's snapshot and not the stage's, so a move during it sprang the inner box for about a second
// while the stage held still. On CI's idle Chat that spring started 928 ms before the window and was
// still asking for frames inside it (#3905, R6). On the hero it now lands at once, as the stage does.
//
// The plant is that render with a move in it: files dragged over the composer, whose drag-over state
// redraws the composer and nothing above it, and a strip landing above the stage in the same update.
// The spring wrote the inner box 133 to 135 times; landing at once writes it once or not at all.

const RISE = `${STAGE} > div.relative.w-full > div.relative`
test("the hero's composer takes a move made while it alone redraws at once", async ({ page }) => {
  await page.addInitScript((rise) => {
    const w = window as typeof window & { __riseWrites?: number }
    w.__riseWrites = 0
    new MutationObserver((list) => {
      for (const m of list) if ((m.target as Element).matches?.(rise)) w.__riseWrites! += 1
    }).observe(document, { subtree: true, attributes: true, attributeFilter: ['style'] })
  }, RISE)
  const writes = () => page.evaluate(() => (window as typeof window & { __riseWrites?: number }).__riseWrites ?? 0)

  await gotoRoute(page, 'chat')
  const before = await writes()
  const moved = await page.evaluate((stageSel) => {
    const stage = document.querySelector<HTMLElement>(stageSel)!
    const card = stage.querySelector<HTMLElement>('.bg-surface-container.px-m.py-s')!
    const top = stage.getBoundingClientRect().top
    const strip = document.createElement('div')
    strip.style.height = '80px'
    // The drag-over render's first DOM write queues this observer's callback ahead of motion's
    // measuring step, so the strip lands between the box's snapshot and its measurement.
    const landed = new Promise<void>((resolve) => {
      const obs = new MutationObserver(() => {
        obs.disconnect()
        stage.parentElement!.insertBefore(strip, stage)
        resolve()
      })
      obs.observe(stage, { attributes: true, childList: true, subtree: true })
    })
    const files = new DataTransfer()
    files.items.add(new File(['x'], 'plant.txt', { type: 'text/plain' }))
    card.dispatchEvent(new DragEvent('dragenter', { bubbles: true, cancelable: true, dataTransfer: files }))
    return landed.then(() => stage.getBoundingClientRect().top - top)
  }, STAGE)
  // The plant took: the composer redrew for the drag, and the strip moved it.
  await expect(page.locator(STAGE).getByText('Drop files to attach')).toBeVisible()
  expect(moved, 'the strip moved the composer').toBeGreaterThan(0)
  await page.waitForTimeout(1_500)
  expect(await writes() - before, 'the move landed without a spring').toBeLessThanOrEqual(10)
})
