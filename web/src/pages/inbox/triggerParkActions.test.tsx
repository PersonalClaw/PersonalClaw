/** A trigger whose action stopped for you is answered from its Inbox row (ledger 248).
 *
 *  🔴 Red on main: the row did not exist — a trigger's browse action at a sign-in page raised no
 *  question anyone could answer (an expired session raised a site-level row that resumed
 *  nothing). The row now carries the action's own card and the park's token, and is answered here
 *  with the renderer an in-run park uses, at the TRIGGER (`/api/triggers/store:<id>/answer`),
 *  which has no run to resume. */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { InboxItem } from '../../lib/api'
import { TriggerParkActions } from './TriggerParkActions'

const answerTriggerPark = vi.fn()

vi.mock('../../lib/api', () => ({
  api: { answerTriggerPark: (...a: unknown[]) => answerTriggerPark(...a) },
}))

const BLOCKER = 'Sign in to 127.0.0.1-9, then confirm — the browse run resumes with that session.'
const TRIED = 'opened 127.0.0.1-9 — it has never been signed in on this machine'

/** The row `triggers.parks._raise_row` writes, as the Inbox serves it. */
function parkRow(): InboxItem {
  return {
    id: 'inbox-park', channel: 'loop', channel_name: 'loop',
    message: `${BLOCKER}\n\nWaiting for your approval. Tried: ${TRIED}.`,
    sender_id: 'loop', sender_name: 'loop', classification: 'needs_reply', confidence: 'high',
    status: 'pending', item_kind: 'needs_input',
    refs: {
      trigger: 'balance', trigger_name: 'Check my balance', trigger_park: 'balance',
      resume_token: 'tok-t',
      needs_input: {
        run_id: '', node_id: '', block_kind: 'approval', blocker: BLOCKER, attempted: [TRIED],
        choices: [], resume_token: 'tok-t', actionable: true,
      },
    },
  } as InboxItem
}

beforeEach(() => vi.clearAllMocks())

describe('answering a trigger that stopped for you', () => {
  it('asks the card’s question, says what it tried and what each answer does', () => {
    render(<TriggerParkActions item={parkRow()} onChanged={vi.fn()} />)
    expect(screen.getByText(BLOCKER)).toBeInTheDocument()
    expect(screen.getByText(`Tried: ${TRIED}`)).toBeInTheDocument()
    expect(screen.getByText('Approve runs it again now. Deny leaves it until it next runs.')).toBeInTheDocument()
  })

  it('Approve runs it again at the trigger, with the park’s token', async () => {
    answerTriggerPark.mockResolvedValue({ ok: true, approved: true, waiting: false })
    const onChanged = vi.fn()
    render(<TriggerParkActions item={parkRow()} onChanged={onChanged} />)
    fireEvent.click(screen.getByText('Approve').closest('button')!)

    await waitFor(() => expect(answerTriggerPark).toHaveBeenCalledWith('balance', { resume_token: 'tok-t', answer: true }))
    expect(await screen.findByText('Ran it again — see its history for what it did.')).toBeInTheDocument()
    expect(onChanged).toHaveBeenCalled()
  })

  it('says so when the run Approve started stopped for you again', async () => {
    answerTriggerPark.mockResolvedValue({ ok: true, approved: true, waiting: true })
    render(<TriggerParkActions item={parkRow()} onChanged={vi.fn()} />)
    fireEvent.click(screen.getByText('Approve').closest('button')!)
    expect(
      await screen.findByText('Ran it again, and it stopped for you again — its new question is in your Inbox.'),
    ).toBeInTheDocument()
  })

  it('Deny closes the question and runs nothing', async () => {
    answerTriggerPark.mockResolvedValue({ ok: true, approved: false, result: 'declined' })
    render(<TriggerParkActions item={parkRow()} onChanged={vi.fn()} />)
    fireEvent.click(screen.getByText('Deny').closest('button')!)

    await waitFor(() => expect(answerTriggerPark).toHaveBeenCalledWith('balance', { resume_token: 'tok-t', answer: false }))
    expect(await screen.findByText('Declined. It asks again the next time it stops.')).toBeInTheDocument()
  })

  it('a refused Approve says why and leaves the question to answer', async () => {
    answerTriggerPark.mockResolvedValue({ ok: false, refused: 'incident mode is active' })
    render(<TriggerParkActions item={parkRow()} onChanged={vi.fn()} />)
    fireEvent.click(screen.getByText('Approve').closest('button')!)

    expect(await screen.findByText('incident mode is active')).toBeInTheDocument()
    expect(screen.getByText('Approve').closest('button')).toBeInTheDocument()
  })
})
