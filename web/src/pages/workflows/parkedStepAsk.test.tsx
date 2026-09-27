/** A step that parked on the user asks through the ONE gate renderer, and says what it tried.
 *
 *  A browse step that stops at a sign-in page parks with an ask whose `rerun` is set: approving
 *  runs the step again once the user has signed in, rather than recording "approved" as its
 *  output. Its continuation's handoff carries what the step already tried — the card wording
 *  #3651 fixed ("opened example.com — it has never been signed in on this machine"). The run page
 *  and the Inbox both render it here, so this is the one place both surfaces get it right.
 *
 *  Pinned: the tried line is shown; the buttons say what they do; "Don't ask again" is not offered
 *  for a parked step (the engine keeps no such answer — nothing can sign in on the user's behalf);
 *  and a plain approval gate keeps the checkbox and shows no tried line. */
import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WorkflowAsk } from './WorkflowAsk'
import type { WorkflowContinuation } from '../../lib/api'

vi.mock('../../lib/api', () => ({ api: {} }))

const TRIED = 'opened 127.0.0.1-9 — it has never been signed in on this machine'

function continuation(over: Partial<WorkflowContinuation['ask']>, attempted?: string[]): WorkflowContinuation {
  return {
    resume_token: 'tok-1',
    node_id: 'fetch',
    instance_path: 'root.children[0]',
    ask: { kind: 'approval', prompt: 'Sign in to 127.0.0.1-9, then confirm — the browse run resumes with that session.', ...over },
    handoff: { outstanding: ['root.children[1]'], attempted },
    expires_at: Date.now() / 1000 + 600,
    expired: false,
  }
}

describe('a parked step asks through the gate renderer', () => {
  it('shows what the step tried and what approving does, and remembers no allow', () => {
    const onAnswer = vi.fn()
    const cont = continuation({ rerun: true }, [TRIED])
    const { getByText, queryByLabelText } = render(
      <WorkflowAsk continuation={cont} runId="r1" busy={false} onAnswer={onAnswer} />,
    )
    expect(getByText(`Tried: ${TRIED}`)).toBeInTheDocument()
    expect(getByText('Approve runs this step again. Deny ends it as failed.')).toBeInTheDocument()
    expect(queryByLabelText("Don't ask again for this step in this run")).toBeNull()
    fireEvent.click(getByText('Approve').closest('button')!)
    expect(onAnswer).toHaveBeenCalledWith(cont, true, false)
  })

  // The control: what must NOT change for a gate, which tried nothing and may be remembered.
  it('leaves a plain approval gate as it was', () => {
    const { getByLabelText, queryByText } = render(
      <WorkflowAsk continuation={continuation({ prompt: 'Publish the draft?' })} runId="r1" busy={false} onAnswer={vi.fn()} />,
    )
    expect(getByLabelText("Don't ask again for this step in this run")).toBeInTheDocument()
    expect(queryByText(/^Tried:/)).toBeNull()
    expect(queryByText('Approve runs this step again. Deny ends it as failed.')).toBeNull()
  })
})
