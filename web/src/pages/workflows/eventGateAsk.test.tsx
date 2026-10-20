/** An `event` gate asks nothing, so its panel only wakes it.
 *
 *  A monitor parks on an `event` gate between checks, until the trigger it armed wakes it. Its ask
 *  used to be an approval, so the run page and the Inbox offered Approve and Deny ("Deny ends the
 *  run here") and "Don't ask again", which would have woken every later park at once. Pinned: one
 *  "Wake it now" that sends `true` and remembers nothing, no Deny, no "Don't ask again", and an
 *  approval gate keeps all of them. */
import { act, fireEvent, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowAsk } from './WorkflowAsk'
import type { WorkflowContinuation } from '../../lib/api'

vi.mock('../../lib/api', () => ({ api: {} }))

const PARKED = 'This monitor is parked between checks. It wakes when its self-scheduled trigger fires.'

function continuation(kind: string, prompt = PARKED): WorkflowContinuation {
  return {
    resume_token: 'tok-1',
    node_id: 'park',
    instance_path: 'root.children[1].body@0.children[0]',
    ask: { kind, prompt },
    handoff: {},
    expires_at: Date.now() / 1000 + 600,
    expired: false,
  }
}

describe('an event gate', () => {
  it('offers only to wake it, and the wake remembers nothing', () => {
    const onAnswer = vi.fn()
    const cont = continuation('event')
    const { getByText, queryByText, queryByLabelText } = render(
      <WorkflowAsk continuation={cont} runId="r1" busy={false} onAnswer={onAnswer} />,
    )
    expect(getByText(PARKED)).toBeInTheDocument()
    expect(getByText(/waits for something to happen, and the run carries on when it does/)).toBeInTheDocument()
    expect(queryByText('Approve')).toBeNull()
    expect(queryByText('Deny')).toBeNull()
    expect(queryByText(/Deny ends the run here/)).toBeNull()
    expect(queryByLabelText("Don't ask again for this step in this run")).toBeNull()

    fireEvent.click(getByText('Wake it now').closest('button')!)
    expect(onAnswer).toHaveBeenCalledWith(cont, true, false)
  })

  // The control: an approval gate is a question for you, and keeps every answer it had.
  it('leaves an approval gate as it was', () => {
    const { getByText, getByLabelText, queryByText } = render(
      <WorkflowAsk continuation={continuation('approval', 'Publish the draft?')} runId="r1" busy={false} onAnswer={vi.fn()} />,
    )
    expect(getByText('Approve')).toBeInTheDocument()
    expect(getByText('Deny')).toBeInTheDocument()
    expect(getByLabelText("Don't ask again for this step in this run")).toBeInTheDocument()
    expect(queryByText('Wake it now')).toBeNull()
  })
})

// `busy` is the caller's ONE flag, shared by every gate a run page lists and set by Deny too, so it
// can hold these buttons but cannot say which one is working. Passed only as `disabled`, a button's
// own answer announced nothing (`aria-busy` comes from `loading`); the button that sent it can say so.
describe('an answer in flight is announced by the button that sent it, and only by it', () => {
  function deferred() {
    let release: () => void = () => {}
    const onAnswer = vi.fn(() => new Promise<void>((r) => { release = r }))
    return { onAnswer, release: () => release() }
  }

  it('Wake it now, for its own wake, and not for another gate', async () => {
    const { onAnswer, release } = deferred()
    const cont = continuation('event')
    const { getByRole, rerender } = render(<WorkflowAsk continuation={cont} runId="r1" busy={false} onAnswer={onAnswer} />)
    const wake = () => getByRole('button', { name: /Wake it now/ })
    fireEvent.click(wake())
    expect(wake()).toHaveAttribute('aria-busy', 'true')
    await act(async () => release())
    expect(wake()).not.toHaveAttribute('aria-busy')

    rerender(<WorkflowAsk continuation={cont} runId="r1" busy onAnswer={onAnswer} />)
    expect(wake()).toHaveAttribute('aria-disabled', 'true')
    expect(wake(), 'held by another answer, which is not this one working').not.toHaveAttribute('aria-busy')
  })

  it('Approve, for its own answer, and not for a Deny', async () => {
    const { onAnswer, release } = deferred()
    const cont = continuation('approval', 'Publish the draft?')
    const { getByRole, rerender } = render(<WorkflowAsk continuation={cont} runId="r1" busy={false} onAnswer={onAnswer} />)
    const approve = () => getByRole('button', { name: /Approve/ })
    fireEvent.click(approve())
    expect(approve()).toHaveAttribute('aria-busy', 'true')
    await act(async () => release())
    expect(approve()).not.toHaveAttribute('aria-busy')

    fireEvent.click(getByRole('button', { name: /Deny/ }))
    expect(onAnswer).toHaveBeenLastCalledWith(cont, false, false)
    expect(approve(), 'a Deny in flight is not Approve working').not.toHaveAttribute('aria-busy')
    rerender(<WorkflowAsk continuation={cont} runId="r1" busy onAnswer={onAnswer} />)
    expect(approve()).toHaveAttribute('aria-disabled', 'true')
    expect(approve()).not.toHaveAttribute('aria-busy')
  })
})
