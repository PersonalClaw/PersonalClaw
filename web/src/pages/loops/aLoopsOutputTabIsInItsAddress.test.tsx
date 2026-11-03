import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { invalidateKeys } from '../../lib/data'
import type { Artifact, Loop } from '../../lib/api'
import type { RouteProps } from '../../app/useQueryState'

// ── A loop page's output tab is in its address ──────────────────────────────────────────────────
//
// The Outputs tabs always opened on the Deliverable, and no address picked another, so a link to
// a loop's findings or a reload on them landed on the Deliverable again. The tab is `?tab=` now:
// read on load, and rewritten in place when another tab is picked.

vi.mock('../../lib/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    uLoops: () => Promise.resolve([LOOP]),
    uLoop: () => Promise.resolve(LOOP),
    uLoopAction: vi.fn(() => Promise.resolve(null)),
    uLoopReport: () => Promise.resolve({ report: '# The packing note\n\nTwo shirts.', log: '' }),
    artifacts: () => Promise.resolve([FINDINGS]),
    task: () => Promise.resolve(null),
    project: () => Promise.resolve({ name: 'Test project' }),
    deleteULoop: () => Promise.resolve(),
    updateULoop: () => Promise.resolve(null),
    uLoopNudge: () => Promise.resolve(),
  },
}))
vi.mock('./useRunStream', () => ({ useRunStream: () => ({ connected: false }) }))

const LOOP = {
  id: 'g1', kind: 'goal', name: 'Packing note', task: 'write a packing note for a weekend trip',
  execution: 'solo', agent: 'claude-code', model: 'sonnet', attended: false, max_cycles: 30,
  idle_secs: 60, success_criteria: null, status: 'complete', total_cycles: 2, error_message: null,
  created_at: 1_780_000_000, started_at: 1_780_000_005, completed_at: 1_780_000_900,
  kind_config: { goal_type: 'open_ended' }, work_dir: '/home/me/personalclaw-workspace',
} as unknown as Loop

const FINDINGS = {
  slug: 'weather-findings', name: 'Weather findings', kind: 'markdown', source: 'loop', description: '',
  tags: ['loop:g1'], version: 1, created_at: '', updated_at: '', events: [], source_path: '',
  live_dirty: false, mime: '', project_id: '', collection: '', content: 'Rain on Sunday.',
} as unknown as Artifact

const { LoopCockpitPage } = await import('./LoopCockpitPage')
// An artifact tab renders through the content registry, which the app fills at start.
const { registerBuiltinContentTypes } = await import('../../ui/content/registerBuiltins')
registerBuiltinContentTypes()

const writes: Array<[Record<string, string | null | undefined>, { replace?: boolean } | undefined]> = []

/** The page under a router that keeps its query, as the app's does. */
function Routed({ initial }: { initial: Record<string, string> }) {
  const [query, setQuery] = useState(initial)
  const set: RouteProps['setQuery'] = (patch, opts) => {
    writes.push([patch, opts])
    setQuery((q) => {
      const next = { ...q }
      for (const [k, v] of Object.entries(patch)) { if (v == null || v === '') delete next[k]; else next[k] = v }
      return next
    })
  }
  return <LoopCockpitPage id="g1" onBack={() => {}} query={query} setQuery={set} />
}

const tab = (name: RegExp) => screen.findByRole('tab', { name })
const selected = () => screen.getAllByRole('tab').find((t) => t.getAttribute('aria-selected') === 'true')?.textContent

beforeEach(() => {
  invalidateKeys('loops')
  invalidateKeys('loop:', true)
  writes.length = 0
})

describe("a loop page's output tab is in its address", () => {
  it('opens on the Deliverable with no tab in the address', async () => {
    render(<Routed initial={{}} />)
    await tab(/Weather findings/)
    expect(selected()).toBe('Deliverable')
  })

  it('opens on the tab the address names', async () => {
    render(<Routed initial={{ tab: 'a:weather-findings' }} />)
    await tab(/Weather findings/)
    await waitFor(() => expect(selected()).toBe('Weather findings'))
  })

  it('picking a tab writes it to the address in place', async () => {
    render(<Routed initial={{}} />)
    fireEvent.click(await tab(/Weather findings/))
    expect(writes).toContainEqual([{ tab: 'a:weather-findings' }, { replace: true }])
    expect(selected()).toBe('Weather findings')
    fireEvent.click(await tab(/Deliverable/))
    expect(writes.at(-1)).toEqual([{ tab: 'deliverable' }, { replace: true }])
    expect(selected()).toBe('Deliverable')
  })

  it('an address naming a tab the loop does not have opens the first', async () => {
    render(<Routed initial={{ tab: 'a:gone' }} />)
    await tab(/Weather findings/)
    expect(selected()).toBe('Deliverable')
    // It is not rewritten behind the reader's back: the address changes only when a tab is picked.
    expect(writes.filter(([p]) => 'tab' in p)).toEqual([])
  })
})
