/** The Settings overview reads in the order it is drawn, under headings a screen reader can walk
 *  (#5897).
 *
 *  The cards are a grid of row bands with no dense packing, so the order on screen IS the source
 *  order, and so the order the keyboard moves in: a group's heading, then its cards left to right,
 *  top to bottom. Headings are h1 Settings, h2 for each group, h3 for each card. A folded page's
 *  own outline (h1 page, h2 section, `?section=` focusing that h2) is pinned in
 *  `everySettingStaysReachable.test.tsx`. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  const gateway = new Proxy({}, { get: () => () => new Promise(() => {}) })
  return { ...real, api: gateway }
})

const { SettingsPage } = await import('./SettingsPage')
const { SETTINGS_GROUPS, SETTINGS_PAGES } = await import('./settingsPages')
const { ThemeProvider } = await import('../../app/theme')
const { AppearanceProvider } = await import('../../app/appearance')
const { PersonalityProvider } = await import('../../app/personality')
const { IdentityProvider } = await import('../../app/identity')

afterEach(cleanup)

async function overview() {
  render(
    <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
      <SettingsPage sub="" navigate={() => {}} navEpoch={0} query={{}} setQuery={() => {}} />
    </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>,
  )
  await act(async () => { await new Promise((r) => setTimeout(r, 0)) })
}

describe('the overview\'s headings', () => {
  it('🔑 one h1, an h2 per group, an h3 per card, each in the registry\'s order', async () => {
    await overview()
    expect(screen.getAllByRole('heading', { level: 1 }).map((h) => h.textContent)).toEqual(['Settings'])
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual([...SETTINGS_GROUPS])
    expect(screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)).toEqual(SETTINGS_PAGES.map((p) => p.label))
  })

  it('each card sits under its group\'s heading', async () => {
    await overview()
    for (const p of SETTINGS_PAGES) {
      const card = screen.getByRole('heading', { level: 3, name: p.label })
      const group = card.closest('section[aria-labelledby]')
      expect(group?.querySelector('h2')?.textContent, p.label).toBe(p.group)
    }
  })
})

describe('a card\'s parts keep their titles', () => {
  it('🔑 at one column every part but the first is titled, on every card of several parts', async () => {
    await overview()
    // jsdom lays nothing out, so the grid measures 0 px and the overview is at one column.
    for (const g of document.querySelectorAll<HTMLElement>('section[aria-labelledby] > .grid')) {
      expect(g.style.gridTemplateColumns).toBe('repeat(1, minmax(0, 1fr))')
    }
    let checked = 0
    for (const p of SETTINGS_PAGES) {
      const parts = p.parts ?? p.sections
      if (parts.length < 2) continue
      const card = screen.getByRole('heading', { level: 3, name: p.label }).closest('.group') as HTMLElement
      // A part is its tile drawn inside the card; its title, when it has one, is its first child.
      const drawn = [...card.querySelectorAll<HTMLElement>('div.relative.flex.min-w-0.flex-col')]
      expect(drawn, `${p.label}: one drawn part per part`).toHaveLength(parts.length)
      const titled = drawn.map((el) => el.querySelector(':scope > [data-type="label-m"]') !== null)
      expect(titled, `${p.label}: the card's title names its first part, and every other part keeps its own`)
        .toEqual(parts.map((_, i) => i > 0))
      checked++
    }
    // Vacuity: the nine pages of several parts, Appearance to Health.
    expect(checked).toBe(9)
  })
})

describe('focus moves in the order the cards are drawn', () => {
  it('🔑 the cards\' buttons come in the registry\'s order: group by group, left to right, top to bottom', async () => {
    await overview()
    const opens = screen.getAllByRole('button', { name: /^Open .+ settings$/ }).map((b) => b.getAttribute('aria-label'))
    expect(opens).toEqual(SETTINGS_PAGES.map((p) => `Open ${p.label} settings`))
  })

  it('🔑 no grid backfills a hole with a later card: placement is sparse and in source order', async () => {
    await overview()
    const grids = [...document.querySelectorAll('section[aria-labelledby] > .grid')] as HTMLElement[]
    expect(grids).toHaveLength(SETTINGS_GROUPS.length)
    for (const g of grids) {
      expect(g.style.gridAutoFlow, 'dense packing moves a later card ahead of the ones before it').not.toMatch(/dense/)
      for (const cell of [...g.children] as HTMLElement[]) {
        expect(cell.style.gridRow, 'a cell placed by hand leaves source order').toBe('')
        expect(cell.style.order, 'an `order` leaves source order').toBe('')
        expect(cell.style.gridColumn, 'a cell asks for a span and is placed by the flow').toMatch(/^span \d$|^$/)
      }
    }
  })
})
