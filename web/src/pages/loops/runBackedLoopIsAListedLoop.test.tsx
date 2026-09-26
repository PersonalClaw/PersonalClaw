import { describe, it, expect, vi, beforeEach } from 'vitest'
import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { invalidateKeys } from '../../lib/data'
import type { Loop } from '../../lib/api'

// ── A run-backed loop IS a loop: listed, controlled by ITS lifecycle, opened on its run page ────
//
// Measured 2026-09-25: a General loop was working while this list said "No loops yet". A ported
// kind (PP-16) runs as a workflow run and writes no loops-table row, and `GET /api/loops` read that
// table alone. The route now lists both homes and a run-backed row carries `run_id`. This file pins
// the frontend half:
//
//   • the row renders, with no invented "0 findings" (a run keeps its work in step outputs);
//   • its controls are the RUN's lifecycle — a failed run has one attempt and cannot resume, so
//     offering the loops-table Resume would buy the user a 409 — asserted against a loops-table
//     row in the same state, which must still offer it (the control);
//   • "Open" hands the list the loop itself, so the section can route it by `run_id`;
//   • a `#/loops/<id>` link to a run-backed loop — every notification, inbox row and chat tag
//     minted before the loop had a second home — redirects to its run page.

const { STORE } = vi.hoisted(() => ({ STORE: { loops: [] as Loop[] } }))

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve(STORE.loops),
    uLoop: (id: string) => {
      const hit = STORE.loops.find((l) => l.id === id)
      return hit ? Promise.resolve(hit) : Promise.reject(new Error('no such loop'))
    },
    uLoopAction: vi.fn(() => Promise.resolve(null)),
    deleteULoop: () => Promise.resolve(),
  },
}))

const { LoopsListPage } = await import('./LoopsListPage')
const { LoopsSection } = await import('./LoopsSection')

function row(over: Partial<Loop>): Loop {
  return {
    id: 'r1', kind: 'general', name: 'Weekly checklist', task: 'write a three-item checklist',
    execution: 'solo', agent: '', model: '', attended: false, max_cycles: 6, idle_secs: 0,
    success_criteria: null, status: 'running', total_cycles: 1, error_message: null,
    created_at: 1_780_000_000, started_at: 1_780_000_005, completed_at: null, kind_config: {},
    findings: [], ...over,
  } as Loop
}

/** The list with a LIVE query — the row click opens the peek through `?peek=`, which a constant
 *  query would swallow. `filter: all` because the default view hides the failed rows under test. */
function Harness({ onOpen }: { onOpen: (l: unknown) => void }) {
  const [query, setQ] = useState<Record<string, string>>({ filter: 'all' })
  const setQuery = (patch: Record<string, string | null | undefined>) => setQ((q) => {
    const next = { ...q }
    for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
    return next
  })
  return <LoopsListPage onOpen={onOpen} onCreate={() => {}} query={query} setQuery={setQuery} />
}

function mountList(loops: Loop[], onOpen = vi.fn()) {
  STORE.loops = loops
  render(<Harness onOpen={onOpen} />)
  return onOpen
}

beforeEach(() => {
  invalidateKeys('loops')
  STORE.loops = []
})

describe('the loops list holds run-backed loops too', () => {
  it('lists a run-backed loop, without a findings count it does not have', async () => {
    mountList([row({ run_id: 'r1' })])
    expect(await screen.findByText('Weekly checklist')).toBeTruthy()
    expect(screen.queryByText(/fnd/)).toBeNull()
    expect(screen.queryByText('No loops yet')).toBeNull()
  })

  it('a loops-table row keeps its findings count — the control', async () => {
    mountList([row({ id: 'g1', kind: 'goal', run_id: undefined })])
    expect(await screen.findByText('0 fnd')).toBeTruthy()
  })

  it('offers Pause to a running run-backed loop', async () => {
    mountList([row({ run_id: 'r1' })])
    await screen.findByText('Weekly checklist')
    expect(screen.getAllByRole('button', { name: 'Pause' })).toHaveLength(1)
  })

  it('does NOT offer Resume to a FAILED run-backed loop — a run has one attempt', async () => {
    mountList([row({ run_id: 'r1', status: 'failed' })])
    await screen.findByText('Weekly checklist')
    expect(screen.queryAllByRole('button', { name: 'Resume' })).toHaveLength(0)
  })

  it('a FAILED loops-table loop still offers Resume — the control', async () => {
    mountList([row({ id: 'g1', kind: 'goal', run_id: undefined, status: 'failed' })])
    await screen.findByText('Weekly checklist')
    expect(screen.getAllByRole('button', { name: 'Resume' })).toHaveLength(1)
  })

  it('opening a run-backed loop hands over the loop, so it can be routed by run_id', async () => {
    const onOpen = mountList([row({ run_id: 'r1' })])
    fireEvent.click(await screen.findByRole('button', { name: /Weekly checklist/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Open the run/ }))
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 'r1', run_id: 'r1' }))
  })
})

describe('a loop link to a run-backed loop lands on its run page', () => {
  it('redirects #/loops/<id> to #/workflows/runs/<id>, replacing the history entry', async () => {
    STORE.loops = [row({ run_id: 'r1' })]
    const navigate = vi.fn()
    render(<LoopsSection sub="r1" navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />)
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('workflows/runs/r1', { replace: true }))
  })
})
