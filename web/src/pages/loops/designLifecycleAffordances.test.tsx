import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { api, type Loop } from '../../lib/api'
import { DesignCockpitPage } from './DesignCockpitPage'

// ── PP-16: the design cockpit's header hand-wrote the backend's transition table ────────────────
//
// `loop/loop.py:ACTION_SOURCE_STATES` is the authority for which status each lifecycle action
// accepts, and this header carried its own copy of the resume set naming four of the five states.
// The omission was `blocked` — the same state a sibling slice found missing from the ACTIVE set,
// which is the tell that hand-written literals, not carelessness, are the cause: every copy of a
// five-element set is one edit away from being a four-element set, and nothing goes red.
//
// The user-visible consequence: a blocked design loop had no Resume button anywhere in the product,
// though `POST /api/loops/<id>/actions {resume}` would have accepted it.
//
// Assertions are on the RENDERED HEADER, not on an import, so re-narrowing the derived set at the
// call site still fails here. The exclusions below are the vacuity floor — one per action, each
// naming a state the backend refuses — because "render every control always" satisfies every
// positive case on its own.
//
// The last block covers a DIFFERENT set on the same page: `specFrozen`, the token editor's
// pre-launch gate. It is asserted through the affordance it controls (an Override tile is a real
// button only while the spec can still be written) so that converting it to the derived pre-launch
// mirror is proven to change nothing about when the editor is live.

vi.mock('./useRunStream', () => ({
  useRunStream: () => ({ connected: true }),
  RUN_LIFECYCLE: [] as string[],
}))

const NAME = 'Northwind design system'

const loopIn = (status: string): Loop => ({
  id: 'd1', kind: 'design', name: NAME, task: 'Build a design system',
  execution: 'solo', agent: 'claude', model: 'sonnet', attended: true,
  max_cycles: 10, idle_secs: 60, success_criteria: null,
  status, total_cycles: 0, error_message: null,
  created_at: 0, started_at: null, completed_at: null,
  kind_config: { token_overrides: {} },
} as unknown as Loop)

/** The smallest token payload that still produces one Override tile (see
 *  `readOnlyTokensNotButtons.test.tsx`, which owns the read-only/editable rendering itself). */
const TOKENS = {
  resolved: {
    radius: { lg: '0.75rem' },
    typography: { family: { sans: 'Inter, sans-serif' }, size: {}, weight: {} },
    spacing: {}, shadow: {}, color: { semantic: {}, primitive: {} },
  },
}

/** Render the cockpit for a design loop in `status`, and prove it mounted past its
 *  loading state — otherwise every `queryBy…` exclusion below passes on an empty DOM. */
async function mountIn(status: string) {
  vi.spyOn(api, 'uLoop').mockResolvedValue(loopIn(status))
  vi.spyOn(api, 'uLoopDesignTokens').mockResolvedValue(TOKENS as never)
  vi.spyOn(api, 'artifacts').mockResolvedValue([])
  render(<DesignCockpitPage id="d1" onBack={() => {}} />)
  await waitFor(() => expect(screen.getByText(NAME), 'the cockpit must have rendered this loop').toBeTruthy())
}

const control = (name: string) => screen.queryByRole('button', { name })

afterEach(() => vi.restoreAllMocks())

describe('the design cockpit offers exactly the lifecycle actions the backend accepts', () => {
  it('a BLOCKED loop can be resumed — the state the whole product refused', async () => {
    await mountIn('blocked')
    expect(control('Resume'), 'the backend accepts resume from blocked').not.toBeNull()
  })

  it.each(['paused', 'stagnant', 'needs_input', 'failed'])('a %s loop can still be resumed', async (status) => {
    await mountIn(status)
    expect(control('Resume')).not.toBeNull()
  })

  it('a RUNNING loop is paused, not resumed or started', async () => {
    await mountIn('running')
    expect(control('Pause'), 'pause is accepted only from running').not.toBeNull()
    expect(control('Resume'), 'the backend refuses resume from running').toBeNull()
    expect(control('Start'), 'the backend refuses start once the loop is running').toBeNull()
  })

  it('a READY loop is started, not resumed or paused', async () => {
    await mountIn('ready')
    expect(control('Start'), 'start is accepted from ready').not.toBeNull()
    expect(control('Resume'), 'a never-launched loop has nothing to resume').toBeNull()
    expect(control('Pause'), 'the backend refuses pause from anything but running').toBeNull()
  })

  it('a loop in REVIEW is started too — the walkthrough finished here', async () => {
    await mountIn('review')
    expect(control('Start')).not.toBeNull()
  })

  it('an INTAKE loop is not launchable yet, but IS stoppable', async () => {
    await mountIn('intake')
    expect(control('Start'), 'the backend accepts start only from ready/review').toBeNull()
    expect(control('Resume')).toBeNull()
    // PP-16: this asserted `Stop` was absent, with the reason "stop 409s on a pre-launch loop" —
    // true when written, and it is what made the actionless-loop gap look intentional. `intake` and
    // `planning` were in NO action row, so a loop whose classifier died had no action anywhere and
    // Delete was its only exit. The backend now accepts `stop` from both.
    expect(control('Stop'), 'a wedged intake loop needs an exit that is not Delete').not.toBeNull()
  })

  it('a PLANNING loop is stoppable too', async () => {
    await mountIn('planning')
    expect(control('Start'), 'start is ready/review only').toBeNull()
    expect(control('Stop'), 'a dead planner must not strand the loop').not.toBeNull()
  })

  it('a COMPLETE loop offers no lifecycle action at all', async () => {
    await mountIn('complete')
    expect(control('Start')).toBeNull()
    expect(control('Pause')).toBeNull()
    expect(control('Resume')).toBeNull()
    expect(control('Stop')).toBeNull()
  })
})

describe('the token editor stays gated on pre-launch, exactly as before', () => {
  it.each(['intake', 'planning', 'review', 'ready'])('a %s loop can still edit its spec', async (status) => {
    await mountIn(status)
    const tile = screen.queryByTitle(/^Override radius\./)
    expect(tile, 'the spec is writable until a worker has run').not.toBeNull()
    expect(tile!.tagName, 'and the tile is a real action').toBe('BUTTON')
  })

  it.each(['running', 'paused', 'blocked', 'complete'])('a %s loop cannot — the backend froze it', async (status) => {
    await mountIn(status)
    expect(screen.queryByTitle(/^Override radius\./), 'a started loop 409s on a spec write').toBeNull()
    // Positive control for the exclusion: the tokens tab really did render its read-only view.
    expect(screen.getByText('lg · 0.75rem'), 'the value is on screen instead').toBeTruthy()
  })
})
