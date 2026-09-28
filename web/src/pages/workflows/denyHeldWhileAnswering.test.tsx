/** Deny is held while an answer is out, like the gate's other buttons.
 *
 *  Approve, Wake and Submit were off while an answer was in flight, and Deny was not: it had no
 *  disabled state at all, so a Deny pressed after an Approve sent the opposite answer to the same
 *  gate while the first was still on its way. Pinned from both directions — the panel's own answer
 *  holds Deny, and so does the caller's shared `busy` — and a Deny in flight holds Approve without
 *  Approve claiming to be the one working. */
import { act, fireEvent, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowAsk } from './WorkflowAsk'
import type { WorkflowContinuation } from '../../lib/api'

vi.mock('../../lib/api', () => ({ api: {} }))

function continuation(): WorkflowContinuation {
  return {
    resume_token: 'tok-1',
    node_id: 'publish',
    instance_path: 'root.children[1]',
    ask: { kind: 'approval', prompt: 'Publish the draft?' },
    handoff: {},
    expires_at: Date.now() / 1000 + 600,
    expired: false,
  }
}

function deferred() {
  let release: () => void = () => {}
  const onAnswer = vi.fn(() => new Promise<void>((r) => { release = r }))
  return { onAnswer, release: () => release() }
}

describe('Deny while an answer is in flight', () => {
  it('is held while this panel’s Approve is out, and sends nothing when pressed', async () => {
    const { onAnswer, release } = deferred()
    const cont = continuation()
    const { getByRole } = render(<WorkflowAsk continuation={cont} runId="r1" busy={false} onAnswer={onAnswer} />)
    const deny = () => getByRole('button', { name: /Deny/ })

    fireEvent.click(getByRole('button', { name: /Approve/ }))
    expect(deny()).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(deny())
    expect(onAnswer, 'only the Approve was sent').toHaveBeenCalledTimes(1)
    expect(onAnswer).toHaveBeenLastCalledWith(cont, true, false)

    await act(async () => release())
    expect(deny(), 'and it is back once the answer landed').not.toHaveAttribute('aria-disabled')
  })

  it('is held while the caller has any answer out, and says why', () => {
    const onAnswer = vi.fn()
    const { getByRole } = render(<WorkflowAsk continuation={continuation()} runId="r1" busy onAnswer={onAnswer} />)
    const deny = getByRole('button', { name: /Deny/ })
    expect(deny).toHaveAttribute('aria-disabled', 'true')
    expect(deny.getAttribute('title') ?? '', 'the reason rides the title').toMatch(/Deny this step — .+/)
    fireEvent.click(deny)
    expect(onAnswer).not.toHaveBeenCalled()
  })

  it('holds Approve while its own answer is out, without Approve claiming to be working', async () => {
    const { onAnswer, release } = deferred()
    const cont = continuation()
    const { getByRole } = render(<WorkflowAsk continuation={cont} runId="r1" busy={false} onAnswer={onAnswer} />)
    const approve = () => getByRole('button', { name: /Approve/ })

    fireEvent.click(getByRole('button', { name: /Deny/ }))
    expect(onAnswer).toHaveBeenLastCalledWith(cont, false, false)
    expect(approve()).toHaveAttribute('aria-disabled', 'true')
    expect(approve(), 'a Deny in flight is not Approve working').not.toHaveAttribute('aria-busy')
    fireEvent.click(approve())
    expect(onAnswer, 'the Approve pressed during the Deny sent nothing').toHaveBeenCalledTimes(1)

    await act(async () => release())
    expect(approve()).not.toHaveAttribute('aria-disabled')
  })
})
