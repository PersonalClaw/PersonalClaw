import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { api, type Loop } from '../../lib/api'
import { SdlcProgressCard, type SdlcRef } from './SdlcProgressCard'

// ── PP-16: the in-chat card offered a DIFFERENT action set than the cockpit ────────────────────
//
// The backend owns which status each lifecycle action accepts (`loop/loop.py:ACTION_SOURCE_STATES`,
// railed by `tests/test_loop_status_vocabulary.py`). This card hand-wrote its own copy of the
// resume set and got it wrong in a way no type could catch: it named three of the five states the
// backend accepts. The two it omitted are the two a user most wants back:
//
//   * a **blocked** loop could not be resumed from ANY surface, though the backend accepts it;
//   * a **failed** loop was resumable from the design cockpit and NOT from this card — the same
//     loop, the same moment, two different sets of buttons depending on where you looked at it.
//
// So these are behaviour assertions at the CALL SITE, not import assertions: a card that imports
// the derived set and then re-narrows it locally must still go red here.
//
// The exclusions are the vacuity floor. Without them "always render Resume" satisfies every
// positive case above, and the whole file would prove nothing. One exclusion per action, each
// naming a state the backend genuinely refuses.

const REF: SdlcRef = { kind: 'loop', id: 'abc123', created: false }

const NAME = 'Ship the widget'

/** A minimal fetched loop in the given raw backend status. */
const loopIn = (status: string): Loop => ({
  id: REF.id, kind: 'goal', name: NAME, task: NAME,
  execution: 'solo', agent: 'claude', model: 'sonnet', attended: true,
  max_cycles: 10, idle_secs: 60, success_criteria: null,
  status, total_cycles: 0, error_message: null,
  created_at: 0, started_at: null, completed_at: null,
} as unknown as Loop)

/** Render the controllable card for a loop in `status`, and prove it actually mounted.
 *  The positive control matters: an empty render passes every `queryBy…` exclusion below. */
async function mountIn(status: string) {
  vi.spyOn(api, 'uLoop').mockResolvedValue(loopIn(status))
  render(<SdlcProgressCard refObj={REF} controllable />)
  await waitFor(() => expect(screen.getByText(NAME), 'the card must have rendered this loop').toBeTruthy())
}

const control = (name: string) => screen.queryByRole('button', { name })

afterEach(() => vi.restoreAllMocks())

describe('the in-chat SDLC card offers exactly the lifecycle actions the backend accepts', () => {
  it('a BLOCKED loop can be resumed — the state that could be resumed from nowhere', async () => {
    await mountIn('blocked')
    expect(control('Resume'), 'the backend accepts resume from blocked').not.toBeNull()
  })

  it('a FAILED loop can be resumed — the state whose answer differed per surface', async () => {
    await mountIn('failed')
    expect(control('Resume'), 'the backend accepts resume from failed').not.toBeNull()
  })

  it.each(['paused', 'stagnant', 'needs_input'])('a %s loop can still be resumed', async (status) => {
    await mountIn(status)
    expect(control('Resume')).not.toBeNull()
  })

  it('a RUNNING loop is paused, not resumed', async () => {
    await mountIn('running')
    expect(control('Pause'), 'pause is accepted only from running').not.toBeNull()
    expect(control('Resume'), 'the backend refuses resume from running').toBeNull()
  })

  it('a PAUSED loop is resumed, not paused', async () => {
    await mountIn('paused')
    expect(control('Pause'), 'the backend refuses pause from anything but running').toBeNull()
  })

  it('a COMPLETE loop offers no lifecycle action at all', async () => {
    await mountIn('complete')
    expect(control('Resume'), 'a finished loop is not resumable').toBeNull()
    expect(control('Pause')).toBeNull()
    expect(control('Stop'), 'stop is refused once the loop is no longer active').toBeNull()
  })

  it('stop follows the active set, so it reaches BLOCKED', async () => {
    await mountIn('blocked')
    expect(control('Stop'), 'blocked is an active status').not.toBeNull()
  })

  it('…and stops short of FAILED, which is resumable without being active', async () => {
    await mountIn('failed')
    expect(control('Stop'), 'the backend refuses stop from failed — it 409s').toBeNull()
  })
})
