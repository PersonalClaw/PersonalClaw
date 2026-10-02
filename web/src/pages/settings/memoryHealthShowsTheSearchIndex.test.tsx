/**
 * Settings → Memory → Health says whether semantic search reaches every embedded memory, and
 * offers the repair when it does not.
 *
 * Observability showed "0 faiss index size · 40 embedded count" with nothing saying whether the
 * two should agree, so an index that missed every memory carried no warning and no Fix. The tab
 * now shows the Doctor's own memory check — its sentence and its Fix, the same row the Doctor
 * page shows — and re-reads it after the Fix. The budget line beside it said "episodic 0" for a
 * preview read with no message, which read as episodic recall cut out of every turn.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent, within } from '@testing-library/react'
import type { DoctorProbe } from '../../lib/api'

const LINT = { flags: [], auto_fixed: {} }
const OBS = {
  stats: { faiss_index_size: 0, embedded_count: 40 },
  rejections: {},
  context_preview: { total_chars: 2404, semantic_chars: 1664, episodic_chars: 0, lessons_chars: 740 },
}
const FIX = {
  id: 'memory.rebuild-faiss-index',
  title: 'Rebuild the memory search index',
  impact: 'Rebuilds the faiss index semantic recall reads from the vectors already stored in memory.db.',
  preview: 'Would rebuild the search index from the memories embedded by the current model; it holds 0 of 40 now.',
}
const probe = (over: Partial<DoctorProbe>): DoctorProbe => ({
  id: 'memory.store', capability: 'memory', tier: 3, ok: true,
  title: 'Memory store + faiss consistency', detail: 'memory.db healthy', evidence: {}, ...over,
})
const DESYNC = probe({
  ok: false, detail: 'faiss index desync: 0 indexed vs 40 embedded rows', fix_id: FIX.id,
})
const HEALTHY = probe({})
const NO_FAISS = probe({
  detail: 'faiss is not installed — semantic recall searches the stored vectors directly',
})

const calls = { checks: 0, applied: [] as string[], confirms: [] as string[], stats: 0 }
/** What the check answers before the Fix is applied, and after. */
let answers: { before: DoctorProbe | 'off'; after: DoctorProbe | 'off' } = { before: 'off', after: 'off' }

async function mount() {
  vi.doMock('../../ui/dialog', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    confirm: (opts: { title: string }) => { calls.confirms.push(opts.title); return Promise.resolve(true) },
  }))
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: () => {},
  }))
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<typeof import('../../lib/api')>()
    return {
      ...real,
      api: {
        ...real.api,
        memoryStats: () => { calls.stats += 1; return Promise.resolve(null) },
        memoryLint: () => Promise.resolve(LINT),
        memoryObservability: () => Promise.resolve(OBS),
        memoryVolunteerStats: () => Promise.resolve(null),
        memoryEntities: () => Promise.resolve({
          entities: [], summary: {}, enabled: true, ranking: { degraded: false, summary: '' },
        }),
        memoryEntityProposals: () => Promise.resolve({ proposals: [], enabled: true }),
        doctorCapability: (capability: string) => {
          expect(capability).toBe('memory')
          const answer = calls.applied.length ? answers.after : answers.before
          calls.checks += 1
          if (answer === 'off') {
            return Promise.reject(new real.ApiError('The Doctor is switched off.', 404, 'doctor_disabled'))
          }
          return Promise.resolve({ capability: 'memory', ok: answer.ok, probes: [answer] })
        },
        doctorFixes: () => Promise.resolve({ fixes: [FIX] }),
        doctorFixApply: (id: string) => {
          calls.applied.push(id)
          return Promise.resolve({ ok: true, fix_id: id, result: 'Rebuilt the memory search index: 40 of 40 embedded memories indexed.' })
        },
      },
    }
  })
  const { MemoryPanel } = await import('./MemoryPanel')
  return render(<MemoryPanel query={{ tab: 'health' }} setQuery={() => {}} />)
}

const section = async () => {
  const heading = await screen.findByText('Search index')
  const box = heading.closest('section') ?? heading.parentElement?.parentElement
  expect(box, 'the Search index section').toBeTruthy()
  return box as HTMLElement
}

beforeEach(() => {
  cleanup()
  vi.resetModules()
  sessionStorage.clear()
  calls.checks = 0
  calls.applied = []
  calls.confirms = []
  calls.stats = 0
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})
afterEach(() => cleanup())

describe('the Search index section', () => {
  it('reports an index that misses embedded memories, and its Fix rebuilds it', async () => {
    answers = { before: DESYNC, after: HEALTHY }
    await mount()
    const box = await section()
    expect(await within(box).findByText('faiss index desync: 0 indexed vs 40 embedded rows')).toBeTruthy()

    fireEvent.click(within(box).getByRole('button', { name: /Fix/ }))
    await waitFor(() => expect(calls.applied).toEqual(['memory.rebuild-faiss-index']))
    expect(calls.confirms).toEqual(['Rebuild the memory search index?'])

    // The tab reads the check again, and what it says now is the repaired state.
    expect(await within(box).findByText('memory.db healthy')).toBeTruthy()
    expect(within(box).queryByRole('button', { name: /Fix/ })).toBeNull()
    expect(calls.checks).toBeGreaterThanOrEqual(2)
    await waitFor(() => expect(calls.stats).toBeGreaterThanOrEqual(2))
  })

  it('says that an install without faiss has no index to desync, with no Fix', async () => {
    answers = { before: NO_FAISS, after: NO_FAISS }
    await mount()
    const box = await section()
    expect(await within(box).findByText(NO_FAISS.detail)).toBeTruthy()
    expect(within(box).queryByRole('button', { name: /Fix/ })).toBeNull()
  })

  it('is not shown while the Doctor is switched off, and says no failure', async () => {
    answers = { before: 'off', after: 'off' }
    await mount()
    expect(await screen.findByText('Observability')).toBeTruthy()
    await waitFor(() => expect(calls.checks).toBeGreaterThanOrEqual(1))
    expect(screen.queryByText('Search index')).toBeNull()
    expect(screen.queryByText(/Couldn't load the search index check/)).toBeNull()
  })
})

describe('the injected-context line', () => {
  it('does not report episodic recall as zero for a preview read with no message', async () => {
    answers = { before: HEALTHY, after: HEALTHY }
    await mount()
    const line = await screen.findByText(/Injected-context budget before any message/)
    expect(line.textContent).toContain('2,404 chars')
    expect(line.textContent).toContain('semantic 1,664')
    expect(line.textContent).toContain('lessons 740')
    expect(line.textContent).toContain('Each message adds the episodic memories it recalls.')
    expect(line.textContent).not.toMatch(/episodic 0/)
  })
})
