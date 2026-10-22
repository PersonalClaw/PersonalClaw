/** The selected automation's detail sits beside the list, or in its place where the room is short (#3816).
 *
 *  The Automations list is a list beside a detail (`ListBesideDetail`): from 840 px of the page's
 *  own room the list keeps a fixed width and the detail with its run history takes the rest; below
 *  it the detail shows in the list's place, and its Back returns to the list, which closes the
 *  selection in the address.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import type { ScheduleJob } from '../../lib/api'

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {}, refreshKinds: () => [] }))
vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    automations: () => Promise.resolve({
      jobs: [{
        id: 'nightly', name: 'Nightly digest', message: '', enabled: true, schedule: 'every day', state: 'active',
        action: { provider: 'notify', config: { message: 'hi' } }, group: 'next_to_run', rank: 0,
        next_run_ts: Math.floor(Date.now() / 1000) + 600,
      } as ScheduleJob],
      hooks: [], stores: [], callbacks: [], server_tz: 'UTC', unreadable: [],
    }),
    triggerReview: () => Promise.resolve([]),
    actionProviders: () => Promise.resolve([]),
    autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
    triggerHistory: () => Promise.resolve({ runs: [], total: 0 }),
    channels: () => Promise.resolve([]),
    channelRuntimes: () => Promise.resolve([]),
  },
}))

const { TriggersSection } = await import('../triggers/TriggersSection')
const { invalidateKeys } = await import('../../lib/data')

/** The page's own room, as its layout box measures it. */
let room = 1200

function Page() {
  const [query, setQuery] = useState<Record<string, string>>({ open: 'schedule:nightly' })
  const patch = (p: Record<string, string | null>) => setQuery((q) => {
    const next = { ...q }
    for (const [k, v] of Object.entries(p)) { if (v === null || v === '') delete next[k]; else next[k] = v }
    return next
  })
  return (
    <>
      <output data-testid="address">{new URLSearchParams(query).toString()}</output>
      <TriggersSection sub="" navigate={vi.fn()} navEpoch={0} query={query} setQuery={patch as never} />
    </>
  )
}

const list = () => document.querySelector('[data-capability-list]') as HTMLElement

beforeEach(() => {
  sessionStorage.clear()
  invalidateKeys('automations:list')
  const box = HTMLElement.prototype.getBoundingClientRect
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    if (!this.hasAttribute('data-capability-layout')) return box.call(this)
    return { x: 0, y: 0, top: 0, left: 0, width: room, height: 800, right: room, bottom: 800, toJSON: () => ({}) } as DOMRect
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('the selected automation opens beside the list', () => {
  it('with the room, beside the list, which stays in view', async () => {
    room = 1200
    render(<Page />)
    await waitFor(() => expect(screen.getByRole('region', { name: 'Nightly digest' })).toBeInTheDocument())
    expect(list().hidden).toBe(false)
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Back to automations' })).toBeNull()
  })

  it('without it, in the list\'s place, and Back returns to the list and closes the selection', async () => {
    room = 600
    render(<Page />)
    await waitFor(() => expect(screen.getByRole('region', { name: 'Nightly digest' })).toBeInTheDocument())
    expect(list().hidden, 'the detail takes the list\'s place').toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Back to automations' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Nightly digest' })).toBeNull())
    expect(list().hidden).toBe(false)
    expect(screen.getByTestId('address').textContent).toBe('')
  })
})
