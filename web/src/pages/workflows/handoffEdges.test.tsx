import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A declared graph edge that no surface rendered ─────────────────────────────
//
// `hands_off_to` exists so a template-to-template transition is a graph EDGE rather than something
// the user has to remember. The backend's own words (workflows/pool.py):
//
//   "without it, 'now run the bugfix SOP' is something a user has to remember, and what a user
//    remembers is not a procedure."
//
// It was fully wired on the producing side — `DefMetadata.from_dict` parses it on the bundled-def
// load path, `handoffs_from_def` builds `HandOff` edges from it, and it ships on BOTH the def
// payload and `/api/workflows/surfacing` — and read by nothing. Verified end to end before building
// this UI (a handoff parses, round-trips through `to_dict`, and becomes an edge), because the
// previous cycle's `AvailableModel.matrix` looked identical from the FE and turned out to have only
// a test-only producer. This one has a real one.
//
// The type was the reason it stayed invisible: `WorkflowDef.metadata` declared 3 of the backend's
// 20 metadata keys, so `hands_off_to` was not merely unread — it was untypeable.
//
// DELIBERATELY NOT changed, and pinned below:
//  · `WorkflowSurfacingRow.last_completed_at` — a DOCUMENTED distinction. `needsAttention()` reads
//    the backend's `overdue` flag instead, and says why: "the thresholds (DUE_SOON_AT,
//    STALE_MULTIPLE) live in one place, and a second comparison here would drift the first time one
//    of them moved." Re-deriving freshness in the FE is the bug that comment prevents.
//  · The handoff list is on the DEF PAGE, not the list row. An outgoing edge is a property of the
//    definition, and the row already carries freshness + mode + packs.

const DETAIL = join(process.cwd(), 'src/pages/workflows/WorkflowDefDetail.tsx')
const META = join(process.cwd(), 'src/pages/workflows/surfacingMeta.ts')

const HANDOFFS = [
  { target_def: 'bugfix-sop', condition: 'tests failed', context_fields: ['run_id', 'failing_test'], requires_user_request: false },
  { target_def: 'deploy-sop', condition: '', context_fields: [], requires_user_request: true },
]

async function mount(hands_off_to: unknown[] | undefined) {
  vi.resetModules()
  vi.doMock('../../lib/api', () => ({
    api: {
      workflowDef: () => Promise.resolve({
        definition: {
          name: 'code-project',
          root: { kind: 'sequence', id: 'root' },
          metadata: hands_off_to === undefined ? {} : { hands_off_to },
        },
        provider: 'bundled',
      }),
      startWorkflowRun: () => Promise.resolve({ run_id: 'r1' }),
      workflowVersions: () => Promise.resolve({
        versions: [], pinned: 0,
        maturity: { level: 0, label: 'draft', signals: {}, clean_runs: 0, evaluator_rejected: false },
      }),
    },
  }))
  const { WorkflowDefDetail } = await import('./WorkflowDefDetail')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<WorkflowDefDetail name="code-project" onBack={() => {}} onStarted={() => {}} />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

describe('declared handoffs reach the def page', () => {
  it('names each target definition', async () => {
    const text = (await mount(HANDOFFS)).container.textContent ?? ''
    expect(text).toContain('Hands off to')
    expect(text).toContain('bugfix-sop')
    expect(text).toContain('deploy-sop')
  })

  it('shows the condition — WHEN the edge is taken', async () => {
    // Without it a handoff reads as "always next", which is the improvisation the field replaces.
    expect((await mount(HANDOFFS)).container.textContent).toContain('tests failed')
  })

  it('shows what carries over to the next workflow', async () => {
    expect((await mount(HANDOFFS)).container.textContent).toContain('carries run_id, failing_test')
  })

  it('marks an edge the system may not take on its own', async () => {
    // "this can happen automatically" vs "only if you ask" is the whole autonomy question for a
    // chained workflow, so `requires_user_request` is marked rather than dropped.
    expect((await mount(HANDOFFS)).container.textContent).toContain('only on request')
  })

  it('does not mark an automatic edge', async () => {
    const { container } = await mount([HANDOFFS[0]])
    expect(container.textContent).not.toContain('only on request')
  })
})

describe('the FE agrees with the engine about which edges exist', () => {
  it('drops an entry with no target_def, exactly as handoffs_from_def does', async () => {
    // The backend skips these because "an edge pointing nowhere would render as a suggestion the
    // user cannot accept, and a dead affordance teaches them to ignore the live ones".
    const { container } = await mount([{ condition: 'orphaned', context_fields: ['x'] }])
    expect(container.textContent).not.toContain('Hands off to')
    expect(container.textContent).not.toContain('orphaned')
  })

  it('drops a blank/whitespace target_def too', async () => {
    const { container } = await mount([{ target_def: '   ', condition: 'blank' }])
    expect(container.textContent).not.toContain('Hands off to')
  })

  it('renders no section when a def declares no handoffs', async () => {
    // Most defs have none — an empty "Hands off to" heading would be permanent noise.
    expect((await mount([])).container.textContent).not.toContain('Hands off to')
  })

  it('survives metadata with no hands_off_to key at all', async () => {
    const { container } = await mount(undefined)
    expect(container.textContent).toContain('Steps')
    expect(container.textContent).not.toContain('Hands off to')
  })
})

describe('the surfacing row keeps its documented distinctions', () => {
  it('needsAttention still reads `overdue`, not a re-derived freshness', () => {
    // last_completed_at stays unread ON PURPOSE: the freshness thresholds live in the backend, and
    // a second comparison here would drift the first time one moved. Pinned so a later "surface
    // every unread field" pass does not helpfully recompute it.
    const meta = readFileSync(META, 'utf8')
    expect(meta).toMatch(/export function needsAttention\(row: Pick<WorkflowSurfacingRow, 'overdue'>/)
    expect(meta).not.toMatch(/row\.last_completed_at/)
  })

  it('the handoff list lives on the def page, not the list row', () => {
    expect(readFileSync(DETAIL, 'utf8')).toMatch(/Hands off to/)
    const list = readFileSync(join(process.cwd(), 'src/pages/workflows/WorkflowsListPage.tsx'), 'utf8')
    expect(list).not.toMatch(/hands_off_to/)
  })
})
