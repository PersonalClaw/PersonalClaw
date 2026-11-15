/** The library's header holds at a phone's width: at 768 px and below
 *  List or Grid leaves the header for the Filter's Show as, which never counts as a filter set; the
 *  header's controls each take the 44 px box. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { answer, answers, atWidth, resetGateway, route, row } from '../../test/libraryHarness'
import { TOUCH_TARGET } from '../../ui/Toggle'

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
})
afterEach(() => { cleanup(); atWidth(1280) })

const mount = (query: Record<string, string> = {}) => {
  const r = route(query)
  render(<LibraryPage sub="" navEpoch={0} query={r.state.query} setQuery={r.setQuery} navigate={r.navigate} />)
  return r
}

describe('the library header holds at a phone', () => {
  it('🔴 control: at 1280 List or Grid is in the header', async () => {
    atWidth(1280)
    mount()
    await screen.findByRole('button', { name: /^Router manual/ })
    expect(screen.getByRole('radiogroup', { name: 'Show as' })).toBeTruthy()
  })

  it.each([320, 390, 412, 768])('🔑 at %i px List or Grid is not in the header, and the Filter has Show as', async (px) => {
    atWidth(px)
    mount()
    await screen.findByRole('button', { name: /^Router manual/ })
    expect(screen.queryByRole('radiogroup', { name: 'Show as' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: /^Filter/ }))
    expect(await screen.findByText('Show as')).toBeTruthy()
  })

  it('🔑 Show as never sets the filter: Grid leaves the Filter named as unset', async () => {
    atWidth(390)
    mount({ layout: 'grid' })
    await screen.findAllByRole('button')
    const filter = screen.getByRole('button', { name: /^Filter/ })
    expect(filter.getAttribute('aria-label') ?? filter.textContent).not.toMatch(/Grid/)
  })

  it('🔑 at 390 each header control takes the 44 px box', async () => {
    atWidth(390)
    mount()
    await screen.findByRole('button', { name: /^Router manual/ })
    for (const name of [/^Filter/, /^Add$/, 'More actions']) {
      const b = screen.getByRole('button', { name })
      expect(b.className, String(name)).toContain(TOUCH_TARGET.split(' ')[0])
    }
  })
})
