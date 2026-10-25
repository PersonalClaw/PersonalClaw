import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useState } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { Loop } from '../../lib/api'
import { loopRoute } from '../../lib/loopKind'

// ── A loops row the store can't open says why, opens nowhere, and can only be deleted ───────────
//
// A row whose id the store never makes (a seed wrote one into a real home) listed like any loop,
// opened as "not found", and its Delete answered "Invalid loop id". The gateway now lists it as
// `failed` with the reason in `broken`, and deletes it by that exact id alone. The page's half:
//   • the row shows the reason and "Can't be opened", and a click opens no peek;
//   • its menu has Delete and nothing that opens, runs or resumes it, and Delete reaches the route;
//   • a link to it (the sidebar's) lands on its list, never on a cockpit.

const { STORE, DELETED } = vi.hoisted(() => ({ STORE: { loops: [] as Loop[] }, DELETED: [] as string[] }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loops),
    uLoop: (id: string) => Promise.reject(new Error(`no such loop ${id}`)),
    uLoopAction: vi.fn(() => Promise.resolve(null)),
    deleteULoop: (id: string) => { DELETED.push(id); return Promise.resolve() },
  },
}))

const { LoopsListPage } = await import('./LoopsListPage')

const REASON = "Its id isn't one PersonalClaw makes, so it can't be opened or run. Delete removes this row only."

function row(over: Partial<Loop>): Loop {
  return {
    id: 'c0ckp1t0', kind: 'goal', name: 'Smoke cockpit', task: 'a seeded row',
    execution: 'solo', agent: '', model: '', attended: false, max_cycles: 6, idle_secs: 0,
    success_criteria: null, status: 'failed', total_cycles: 0, error_message: null,
    created_at: 1_780_000_000, started_at: null, completed_at: null, kind_config: {},
    findings: [], ...over,
  } as Loop
}

function Harness() {
  const [query, setQ] = useState<Record<string, string>>({ filter: 'all' })
  const setQuery = (patch: Record<string, string | null | undefined>) => setQ((q) => {
    const next = { ...q }
    for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
    return next
  })
  return (
    <>
      <LoopsListPage onOpen={() => {}} onCreate={() => {}} onOpenCode={() => {}} query={query} setQuery={setQuery} />
      <output data-testid="peek">{query.peek ?? ''}</output>
    </>
  )
}

beforeEach(() => {
  invalidateKeys('loops')
  STORE.loops = []
  DELETED.length = 0
})

describe('a loops row the store cannot open', () => {
  it('says why, and a click on it opens no peek', async () => {
    STORE.loops = [row({ broken: REASON })]
    render(<Harness />)
    expect(await screen.findByText('Smoke cockpit')).toBeTruthy()
    expect(screen.getByText(REASON)).toBeTruthy()
    expect(screen.getByText(/Can't be opened/)).toBeTruthy()
    fireEvent.click(screen.getByText('Smoke cockpit'))
    expect(screen.getByTestId('peek').textContent).toBe('')
  })

  it('offers Delete and nothing that opens it, and Delete reaches the route by its id', async () => {
    STORE.loops = [row({ broken: REASON })]
    render(<Harness />)
    const title = await screen.findByText('Smoke cockpit')
    fireEvent.contextMenu(title)
    const menu = await screen.findByRole('menu')
    expect(within(menu).queryByText('Open')).toBeNull()
    expect(within(menu).queryByText('Resume')).toBeNull()
    expect(within(menu).getByText('Delete')).toBeTruthy()
    const del = screen.getByRole('button', { name: 'Delete loop' })
    fireEvent.click(del)
    fireEvent.click(screen.getByRole('button', { name: 'Click again to delete' }))
    await waitFor(() => expect(DELETED).toEqual(['c0ckp1t0']))
  })

  it('a loop that opens still opens its peek (the control)', async () => {
    STORE.loops = [row({ id: 'a1b2c3d4', name: 'A real loop', broken: undefined })]
    render(<Harness />)
    fireEvent.click(await screen.findByText('A real loop'))
    await waitFor(() => expect(screen.getByTestId('peek').textContent).toBe('a1b2c3d4'))
  })
})

describe('a link to a row the store cannot open', () => {
  it('lands on its list, never on a cockpit', () => {
    expect(loopRoute({ id: 'c0ckp1t0', kind: 'code', broken: REASON })).toBe('code/history')
    expect(loopRoute({ id: 'c0ckp1t0', kind: 'goal', broken: REASON })).toBe('loops/history')
    expect(loopRoute({ id: 'a1b2c3d4', kind: 'code' })).toBe('code/a1b2c3d4')
  })
})
