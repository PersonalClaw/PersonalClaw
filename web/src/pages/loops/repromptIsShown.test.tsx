/** A worker asked again for its finding is shown on the loop's page: which worker, why, and how many
 *  asks are left.
 *
 *  Each re-prompt is a model turn its owner pays for and, on an Attended loop, an approval to answer.
 *  They used to be logged and nothing more, so a loop spent cycles re-checking finished tasks with no
 *  sign of it anywhere. The gateway now publishes each one (`loop/manager.announce_reprompt`), and the
 *  page holds it until the cycle moves on.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { emptyRunFlags, foldReducer } from './runFold'
import { RUN_LIFECYCLE } from './useRunStream'
import { RepromptNotice, repromptSentence } from './RepromptNotice'

afterEach(cleanup)

const ask = { loop_id: '5e1d0c47', task_id: 't-2b9c41fe', title: 'Add the Fixed line', attempt: 1, of: 3, left: 2, file: 'task_t-2b9c41fe_NNN.json' }

describe('the fold holds a re-prompt until the cycle moves on', () => {
  it('🔴 the event is one the page listens for', () => {
    expect(RUN_LIFECYCLE).toContain('reprompt')
  })

  it('records who, what and how many are left, and leaves a gate failure standing', () => {
    const gated = foldReducer(emptyRunFlags(), 'gate_check', { ok: false, label: 'tests', output: 'boom' })
    const f = foldReducer(gated, 'reprompt', ask)
    expect(f.reprompt).toEqual({ taskId: 't-2b9c41fe', title: 'Add the Fixed line', file: 'task_t-2b9c41fe_NNN.json', attempt: 1, of: 3, left: 2 })
    expect(f.gate, 'a re-prompt is not progress').not.toBeNull()
  })

  it('a new finding clears it', () => {
    const f = foldReducer(foldReducer(emptyRunFlags(), 'reprompt', ask), 'new_finding', {})
    expect(f.reprompt).toBeNull()
  })
})

describe('the page says it', () => {
  it('🔴 names the worker, the file it owes and the asks left', () => {
    const f = foldReducer(emptyRunFlags(), 'reprompt', ask)
    render(<RepromptNotice reprompt={f.reprompt} />)
    expect(screen.getByRole('status').textContent).toBe(
      'The worker on “Add the Fixed line” ended its turn without writing its finding (task_t-2b9c41fe_NNN.json), so it was asked again: re-prompt 1 of 3, 2 left.',
    )
  })

  it('the last ask says so, and a stage worker is the loop’s', () => {
    const last = foldReducer(emptyRunFlags(), 'reprompt', { ...ask, task_id: '', title: '', attempt: 3, left: 0, file: 'cycle_NNN.json' })
    expect(repromptSentence(last.reprompt!)).toBe(
      'The loop’s worker ended its turn without writing its finding (cycle_NNN.json), so it was asked again: re-prompt 3 of 3, the last one.',
    )
  })

  it('nothing to say, nothing shown', () => {
    const { container } = render(<RepromptNotice reprompt={null} />)
    expect(container.innerHTML).toBe('')
  })
})
