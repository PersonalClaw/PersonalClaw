/** The code cockpit's strip fits every width below the desktop. The strip under a code session's
 *  title says its status, what it runs on, its folder, its project and its cycle count, and below
 *  1024 px the project's chip is a 44 px box. At 390 the strip used to run past the screen's edge:
 *  the chip was cut by the edge with no ellipsis and the cycle count was off the screen. Now the
 *  strip's row wraps, the project and the cycle count go to the next line together, and the chip is
 *  the one part that shrinks, with its ellipsis. From 320 to 1023 px, in both themes, every part
 *  of the strip is inside the screen and the only cut is the chip's name. The session's reads are
 *  answered with route stubs, so nothing is written. */
import { test, expect, type Page } from '@playwright/test'
import { gotoRoute, seedTheme } from './helpers'

const CODE = 'c0de5711'
const PROJECT = { id: 'p-irrigation', name: 'Garden irrigation, the spring beds and the greenhouse' }
const WORKSPACE = '/work/garden-site'
const LOOP = {
  id: CODE, kind: 'code', name: 'Fix the irrigation sensor tests', task: 'fix the sensor tests', status: 'pending',
  project_id: PROJECT.id, workspace_dir: WORKSPACE, files_dir: `${WORKSPACE}/.loop`, plan: [], phase_status: {},
  task_list_ids: {}, kind_config: {}, execution: 'solo', agent: 'default', model: '', attended: false,
  max_cycles: 30, cycles: 0, idle_secs: 0, created_at: '2026-11-11T09:00:00+00:00', updated_at: '2026-11-11T09:00:00+00:00',
}
const WIDTHS = [320, 390, 500, 700, 900, 1023] as const

async function answer(page: Page, path: string, body: unknown): Promise<void> {
  await page.route((url) => url.pathname === path, (route) => route.request().method() === 'GET'
    ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    : route.fallback())
}

async function answerTheSession(page: Page): Promise<void> {
  await answer(page, `/api/loops/${CODE}`, LOOP)
  await answer(page, '/api/loops', { loops: [LOOP] })
  await answer(page, '/api/projects', { projects: [PROJECT] })
  await answer(page, `/api/projects/${PROJECT.id}`, PROJECT)
  await answer(page, '/api/tasks', { tasks: [], total: 0, complete: true, limit: 200, offset: 0, owner: '' })
  await answer(page, '/api/file-list', { roots: [], entries: [], path: WORKSPACE })
  await answer(page, '/api/file-git-status', { repoRoot: WORKSPACE, branch: 'main', statuses: {} })
  await answer(page, '/api/file-git-log', { repoRoot: WORKSPACE, commits: [] })
}

/** Each part of the strip that is outside the screen, or cut other than the chip's name. */
async function partsThatDoNotFit(page: Page): Promise<string[]> {
  return page.getByRole('link', { name: `Open project ${PROJECT.name}` }).evaluate((chip) => {
    const strip = chip.closest('div.border-b') as HTMLElement
    const out: string[] = []
    const name = chip.querySelector('span.truncate')
    for (const part of strip.querySelectorAll<HTMLElement>('*')) {
      const r = part.getBoundingClientRect()
      // Nothing painted, or a label for screen readers alone (`sr-only`, a 1 px box): no part of the look.
      if (r.width <= 1 || r.height <= 1) continue
      const what = (part.getAttribute('aria-label') || part.getAttribute('title') || part.textContent || part.tagName).trim().slice(0, 40)
      if (r.left < -0.5) out.push(`${what}: starts ${Math.round(-r.left)} px before the screen`)
      if (r.right > window.innerWidth + 0.5) out.push(`${what}: ends ${Math.round(r.right - window.innerWidth)} px past the screen`)
      if (part !== name && getComputedStyle(part).overflowX !== 'visible' && part.scrollWidth > part.clientWidth + 1) {
        out.push(`${what}: cut, needs ${part.scrollWidth} px and has ${part.clientWidth}`)
      }
    }
    if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1) out.push('the page scrolls sideways')
    return out
  })
}

test.describe("The code cockpit's strip below 1024 px", () => {
  for (const theme of ['light', 'dark'] as const) {
    for (const width of WIDTHS) {
      test(`every part is inside the screen at ${width} in ${theme}`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 800 })
        await seedTheme(page, theme)
        await answerTheSession(page)
        await gotoRoute(page, `code/${CODE}`)
        const chip = page.getByRole('link', { name: `Open project ${PROJECT.name}` })
        await expect(chip).toBeVisible()
        // The chip is the strip's one way to the project, so it keeps its 44 px box.
        expect(Math.round((await chip.boundingBox())!.height)).toBe(44)
        const cycles = page.getByTitle(/budget run$/)
        await expect(cycles).toBeVisible()
        const box = (await cycles.boundingBox())!
        expect(box.x, 'the cycle count starts on the screen').toBeGreaterThanOrEqual(0)
        expect(box.x + box.width, 'the cycle count ends on the screen').toBeLessThanOrEqual(width)
        expect(await partsThatDoNotFit(page)).toEqual([])
        if (width === 320 || width === 390) {
          const name = `code-strip-${width}-${theme}.png`
          const file = info.outputPath(name)
          await chip.locator('xpath=ancestor::div[contains(@class,"border-b")][1]').screenshot({ path: file })
          await info.attach(name, { path: file, contentType: 'image/png' })
        }
      })
    }
  }
})
