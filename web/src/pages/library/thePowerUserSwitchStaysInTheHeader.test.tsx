/** The Power user view's switch stays in the header.
 *
 *  It is a toggle button named "Power user view" that announces `aria-pressed`, and never sheds into
 *  the "…" (`keep="row"`). From 769 px it draws its switch inside the pill (`asSwitch`, a decorative
 *  `Toggle`); at 768 px and below it is its gauge alone in a 44 px box, filled while on, and the "…"
 *  holds the words: one row named "Power user view", whose `aria-pressed` follows the setting and
 *  which draws the decorative switch (`OverflowRow`'s `checked`). A row without `checked` stays a plain
 *  row. Its on state is drawn by the current outline, which forced colours keep. (Whether the 390 px
 *  title stays whole and nothing overflows at 320 is a layout claim, the visual matrix's.) */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answer, answers, atWidth, resetGateway, route, row } from '../../test/libraryHarness'
import { CURRENT_OUTLINE } from '../../ui/RowHitTarget'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { LibraryPage } = await import('./LibraryPage')
const { resetDataStore } = await import('../../lib/data/store')

beforeEach(() => {
  resetDataStore(); resetGateway(); localStorage.clear()
  answers.library = answer([row('knowledge:k1', 'Router manual')])
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
  answers.knowledgeStats = { items: 1, entities: 0, relations: 0, embeddings: { enabled: false } }
  answers.knowledgeConflicts = { conflicts: [] }
  answers.librarySettings = { power_user_view: false }
})
afterEach(() => { cleanup(); atWidth(1280) })

const mount = (query: Record<string, string> = {}) => {
  const r = route(query)
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}
/** The decorative switch: an `aria-hidden` track with its knob inside. */
const drawsSwitch = (el: Element) => !!el.querySelector('span[aria-hidden] > span')
const theSwitch = () => screen.getByRole('button', { name: 'Power user view' })

describe('the Power user view switch', () => {
  it('🔑 is a toggle button named "Power user view", drawn with its switch from 769 px', async () => {
    atWidth(1280)
    mount()
    await screen.findByRole('button', { name: /^Router manual/ })
    expect(theSwitch().getAttribute('aria-pressed')).toBe('false')
    expect(drawsSwitch(theSwitch()), 'the pill draws its switch').toBe(true)
  })

  it('🔑 is never in the "…" at a desktop width', async () => {
    atWidth(1280)
    mount()
    await screen.findByRole('button', { name: /^Router manual/ })
    await userEvent.click(screen.getByRole('button', { name: 'More actions' }))
    await screen.findByText('Feeds, intents and reports')
    expect(screen.getAllByRole('button', { name: /Power user view/ }), 'the header\'s own, and no row').toHaveLength(1)
  })

  it.each([768, 390, 320])('🔑 at %i px it is its gauge alone, and the "…" holds its labelled row', async (px) => {
    atWidth(px)
    answers.librarySettings = { power_user_view: true }
    mount()
    const gauge = await screen.findByRole('button', { name: 'Power user view', pressed: true })
    expect(drawsSwitch(gauge), 'no pill at a phone').toBe(false)
    expect(gauge.className, 'its 44 px box').toMatch(/\bsize-11\b/)
    expect(gauge.className, 'filled while on').toMatch(/bg-primary-container/)
    await userEvent.click(screen.getByRole('button', { name: 'More actions' }))
    const rows = screen.getAllByRole('button', { name: /Power user view/ }).filter((el) => el !== gauge)
    expect(rows, 'one labelled row').toHaveLength(1)
    expect(rows[0].getAttribute('aria-pressed'), 'its state follows the setting').toBe('true')
    expect(drawsSwitch(rows[0]), 'and it draws the switch').toBe(true)
  })

  it('🔴 a "…" row without checked stays a plain row', async () => {
    atWidth(390)
    mount()
    await screen.findByRole('button', { name: /^Router manual/ })
    await userEvent.click(screen.getByRole('button', { name: 'More actions' }))
    const plain = (await screen.findByText('Feeds, intents and reports')).closest('button')!
    expect(plain.hasAttribute('aria-pressed')).toBe(false)
    expect(drawsSwitch(plain)).toBe(false)
  })

  it('🔑 its on state is drawn by the current outline, which forced colours keep', async () => {
    atWidth(1280)
    answers.librarySettings = { power_user_view: true }
    mount()
    const on = await screen.findByRole('button', { name: 'Power user view', pressed: true })
    for (const c of CURRENT_OUTLINE.split(/\s+/).filter(Boolean)) expect(on.className).toContain(c)
  })
})
