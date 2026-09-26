import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, render, screen, fireEvent, waitFor, within } from '@testing-library/react'

// ── settings B16: a failed check offers its Fix, or says there is none and what to do ────────────
//
// The validator's Doctor page showed two failed checks — "faiss index desync: 0 indexed vs 2
// embedded rows" and "4 unclaimed paths … in NO snapshot" — with no Fix on either and nothing on
// either row saying so; the only affordance was "Investigate in chat". Now every failed probe
// carries a registered `fix_id` or a `remedy` (railed server-side over every failure a probe can
// return), and the row renders whichever it has. Pressing Fix names THAT fix — its title, its
// impact and its dry-run preview — and afterwards the page re-reads a FRESH report and the
// Maintenance score, because a 30s-cached report is how a Fix that worked read as a Fix that did
// nothing.

const INVENTORY_REMEDY =
  'No automatic fix — a snapshot leaves out any path the state manifest does not claim, and claiming ' +
  'one ships with a release.'

const report = {
  ok: false,
  core_ok: true,
  worst: 'durability',
  restart_suggested: false,
  capabilities: {
    durability: {
      ok: false, tier: 3,
      probes: [{
        id: 'durability.inventory', capability: 'durability', tier: 3, ok: false,
        title: 'Every state path is claimed by the manifest',
        detail: '4 unclaimed paths and 0 undeclared databases — these are in NO snapshot',
        evidence: {}, remedy: INVENTORY_REMEDY,
      }],
    },
    memory: {
      ok: false, tier: 3,
      probes: [{
        id: 'memory.store', capability: 'memory', tier: 3, ok: false,
        title: 'Memory store + faiss consistency',
        detail: 'faiss index desync: 0 indexed vs 2 embedded rows',
        evidence: {}, fix_id: 'memory.rebuild-faiss-index',
      }],
    },
  },
  skipped_capabilities: [],
  generated_at: 0,
}

const FIX = {
  id: 'memory.rebuild-faiss-index',
  title: 'Rebuild the memory search index',
  impact: 'Rebuilds the faiss index semantic recall reads from the vectors already stored in memory.db.',
  preview: 'Would rebuild the search index from the memories embedded by the current model; it holds 0 of 2 now.',
}

const calls = {
  doctor: [] as boolean[],
  remediation: 0,
  applied: [] as string[],
  confirms: [] as Array<{ title: string; body: string }>,
  catalogFails: false,
}

async function mountPanel() {
  vi.resetModules()
  vi.doMock('../../ui/dialog', () => ({
    confirm: (opts: { title: string; body: string }) => { calls.confirms.push(opts); return Promise.resolve(true) },
  }))
  vi.doMock('../../app/appSdk', () => ({ notify: () => {} }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<typeof import('../../lib/api')>()
    return {
      ...real,
      api: {
        ...real.api,
        doctor: (fresh = false) => { calls.doctor.push(fresh); return Promise.resolve(report) },
        doctorRemediation: () => {
          calls.remediation += 1
          return Promise.resolve({ score: 70, target_score: 90, deficits: [], plan: [], recent_runs: [] })
        },
        doctorFixes: () => (calls.catalogFails
          ? Promise.reject(new Error('the gateway is restarting'))
          : Promise.resolve({ fixes: [FIX] })),
        doctorFixApply: (id: string) => { calls.applied.push(id); return Promise.resolve({ ok: true, fix_id: id, result: 'Rebuilt.' }) },
        doctorSimulateSurfacing: () => Promise.resolve({ candidates: [] }),
        doctorSimulateAutomation: () => Promise.resolve({ steps: [] }),
        triggers: () => Promise.resolve({ triggers: [] }),
      },
    }
  })
  const { DoctorPanel } = await import('./DoctorPanel')
  await act(async () => {
    render(<DoctorPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

function card(title: string): HTMLElement {
  const row = screen.getByText(title)
  const probe = row.closest('li, [data-probe], div')?.parentElement?.parentElement
  expect(probe, `no row for ${title}`).toBeTruthy()
  return probe as HTMLElement
}

beforeEach(() => {
  calls.doctor = []
  calls.remediation = 0
  calls.applied = []
  calls.confirms = []
  calls.catalogFails = false
})

describe('a failed check names its next step', () => {
  it('says plainly when there is no Fix, in the check’s own words', async () => {
    await mountPanel()
    expect(screen.getByText(INVENTORY_REMEDY)).toBeInTheDocument()
    expect(within(card('Every state path is claimed by the manifest')).queryByRole('button', { name: /^Fix$/ })).toBeNull()
  })

  it('offers the Fix when there is one, and no “no automatic fix” line beside it', async () => {
    await mountPanel()
    const memory = card('Memory store + faiss consistency')
    expect(within(memory).getByRole('button', { name: /Fix/ })).toBeInTheDocument()
    expect(memory.textContent).not.toContain('No automatic fix')
  })
})

describe('the Fix says what it will do, and the page reads what it did', () => {
  it('confirms with THIS fix’s title, impact and dry-run preview', async () => {
    await mountPanel()
    fireEvent.click(within(card('Memory store + faiss consistency')).getByRole('button', { name: /Fix/ }))
    await waitFor(() => expect(calls.applied).toEqual(['memory.rebuild-faiss-index']))
    expect(calls.confirms).toHaveLength(1)
    expect(calls.confirms[0].title).toBe('Rebuild the memory search index?')
    expect(calls.confirms[0].body).toContain(FIX.impact)
    expect(calls.confirms[0].body).toContain(FIX.preview)
  })

  it('says so when it cannot read what the fix does, instead of describing it generically', async () => {
    calls.catalogFails = true
    await mountPanel()
    fireEvent.click(within(card('Memory store + faiss consistency')).getByRole('button', { name: /Fix/ }))
    await waitFor(() => expect(calls.confirms).toHaveLength(1))
    expect(calls.confirms[0].body).toContain("Couldn't read what this fix does (the gateway is restarting)")
    expect(calls.confirms[0].body).not.toContain('never your content')
  })

  it('re-probes fresh and re-reads the Maintenance score after a Fix', async () => {
    await mountPanel()
    expect(calls.doctor).toEqual([false])     // the first read may use the server's cache
    const scoreReads = calls.remediation
    expect(scoreReads).toBeGreaterThan(0)
    fireEvent.click(within(card('Memory store + faiss consistency')).getByRole('button', { name: /Fix/ }))
    await waitFor(() => expect(calls.doctor).toEqual([false, true]))
    await waitFor(() => expect(calls.remediation).toBe(scoreReads + 1))
  })

  it('Re-run asks for a fresh report, not the cached one', async () => {
    await mountPanel()
    fireEvent.click(screen.getByRole('button', { name: /Re-run/ }))
    await waitFor(() => expect(calls.doctor).toEqual([false, true]))
  })
})
