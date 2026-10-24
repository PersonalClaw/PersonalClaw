import { expect, type Page } from '@playwright/test'
import { CLIPPED_RING, MOUSE_ONLY } from './nonAxeA11yScripts'

// The two non-axe detectors, as assertions on whatever a spec has rendered. What each detects, and
// why, is `nonAxeA11yScripts.ts`.
export { CLIPPED_RING, MOUSE_ONLY }

/** Run both non-axe checks on whatever is already rendered — adds assertions, not page loads. */
export async function expectNoNonAxeA11yDefects(page: Page, where: string): Promise<void> {
  const mouseOnly = await page.evaluate<string[]>(`(${MOUSE_ONLY})()`)
  expect(
    mouseOnly,
    `${where}: element promises a click (cursor: pointer) with no tag, role or tab stop, and wraps no `
    + `control — mouse-only (WCAG 2.1.1). Render a real <button>, or drop the pointer cursor:\n  `
    + mouseOnly.join('\n  '),
  ).toEqual([])

  const clipped = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(
    clipped,
    `${where}: a focused control's outline falls outside a clipping ancestor, so the ring computes but `
    + `never paints (WCAG 2.4.7). Use a negative outline-offset — and match the parent's radius, or `
    + `the ring's corners clip instead:\n  ` + clipped.join('\n  '),
  ).toEqual([])
}
