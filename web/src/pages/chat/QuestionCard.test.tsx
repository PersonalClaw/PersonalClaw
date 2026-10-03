import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ApiError } from '../../lib/api'
import { QuestionCard } from './QuestionCard'
import { applyQuestionFrame, applyQuestionResolved, graftPendingQuestions, questionSegmentOf } from './questionFrames'
import type { QuestionSegment, Segment } from './chatTypes'

// An agent's question to its owner, in the chat that asked it. The call that asked is waiting on
// it, so the card must let her answer by pointer or by keyboard — and must never answer for her:
// nothing is chosen when it arrives, focus chooses nothing, and only a press on Send sends.

const FRAME = {
  id: 'q1', session: 'chat-1', tool_call_id: 'toolu_01', asked_by: 'Claude Code in “Plan the service”',
  ts: 1, answerable: true,
  questions: [{
    question: 'Which database should the service use?', header: 'Database', multiSelect: false, free_text: true,
    options: [{ label: 'Postgres', description: 'Relational, like the rest' }, { label: 'SQLite', description: '' }],
  }],
}

const card = (over: Partial<QuestionSegment> = {}): QuestionSegment => ({ ...questionSegmentOf(FRAME)!, ...over })

afterEach(cleanup)

describe('the question card, waiting on her', () => {
  it('shows who asks, the header, the question and every option, with nothing chosen and nothing focused', () => {
    render(<QuestionCard seg={card()} onAnswer={vi.fn()} />)
    expect(screen.getByRole('group', { name: 'Question for you' })).toBeTruthy()
    expect(screen.getByText('From Claude Code in “Plan the service”')).toBeTruthy()
    expect(screen.getByText('Database')).toBeTruthy()
    const group = screen.getByRole('radiogroup', { name: 'Which database should the service use?' })
    const options = [...group.querySelectorAll('[role="radio"]')]
    expect(options.map((o) => o.textContent)).toEqual(['PostgresRelational, like the rest', 'SQLite'])
    expect(options.every((o) => o.getAttribute('aria-checked') === 'false')).toBe(true)
    expect(screen.getByRole('textbox', { name: 'Your own answer to: Which database should the service use?' })).toBeTruthy()
    expect(document.activeElement).toBe(document.body)
    // Send says why it is not ready, and stays reachable to say it.
    const send = screen.getByRole('button', { name: /Send answer/ })
    expect(send.getAttribute('aria-disabled')).toBe('true')
  })

  it('is answered by keyboard: Tab reaches an option, Space chooses it, and only Send sends', async () => {
    const onAnswer = vi.fn(() => Promise.resolve())
    const user = userEvent.setup()
    render(<QuestionCard seg={card()} onAnswer={onAnswer} />)
    await user.tab()
    const postgres = screen.getByRole('radio', { name: /Postgres/ })
    expect(document.activeElement).toBe(postgres)
    expect(postgres.getAttribute('aria-checked')).toBe('false')  // focus chose nothing
    await user.tab()
    const sqlite = screen.getByRole('radio', { name: 'SQLite' })
    expect(document.activeElement).toBe(sqlite)
    await user.keyboard(' ')
    expect(sqlite.getAttribute('aria-checked')).toBe('true')
    expect(onAnswer).not.toHaveBeenCalled()
    // Enter in her own-words box sends nothing either.
    await user.tab()
    await user.keyboard('with daily backups{Enter}')
    expect(onAnswer).not.toHaveBeenCalled()
    await user.tab()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /Send answer/ }))
    await user.keyboard('{Enter}')
    expect(onAnswer).toHaveBeenCalledWith('q1', { answers: [{ selected: [1], other: 'with daily backups' }] })
  })

  it('takes several choices where the question does, each a press that toggles it', async () => {
    const onAnswer = vi.fn(() => Promise.resolve())
    const user = userEvent.setup()
    const multi = card({ questions: [{ ...FRAME.questions[0], multiSelect: true, free_text: false }] })
    render(<QuestionCard seg={multi} onAnswer={onAnswer} />)
    expect(screen.getByRole('group', { name: 'Which database should the service use?' })).toBeTruthy()
    await user.click(screen.getByRole('checkbox', { name: 'SQLite' }))
    await user.click(screen.getByRole('checkbox', { name: /Postgres/ }))
    await user.click(screen.getByRole('button', { name: /Send answer/ }))
    expect(onAnswer).toHaveBeenCalledWith('q1', { answers: [{ selected: [0, 1], other: '' }] })
  })

  it('sends her Skip as one', async () => {
    const onAnswer = vi.fn(() => Promise.resolve())
    render(<QuestionCard seg={card()} onAnswer={onAnswer} />)
    await userEvent.click(screen.getByRole('button', { name: /^Skip these questions/ }))
    expect(onAnswer).toHaveBeenCalledWith('q1', { skip: true })
  })

  it('says why it was refused once the agent stopped waiting, and takes the verbs away', async () => {
    const said = 'The agent is no longer waiting for this answer: its turn was stopped.'
    const onAnswer = vi.fn(() => Promise.reject(new ApiError(said, 409, 'question_ended')))
    render(<QuestionCard seg={card()} onAnswer={onAnswer} />)
    await userEvent.click(screen.getByRole('radio', { name: 'SQLite' }))
    await userEvent.click(screen.getByRole('button', { name: /Send answer/ }))
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe(said))
    expect(screen.queryByRole('button', { name: /Send answer/ })).toBeNull()
  })

  it('keeps the verbs and says so when the answer could not be sent', async () => {
    const onAnswer = vi.fn(() => Promise.reject(new Error('the gateway did not answer')))
    render(<QuestionCard seg={card()} onAnswer={onAnswer} />)
    await userEvent.click(screen.getByRole('radio', { name: 'SQLite' }))
    await userEvent.click(screen.getByRole('button', { name: /Send answer/ }))
    const said = await screen.findByText('Your answer was not sent: the gateway did not answer')
    expect(said.getAttribute('role')).toBe('alert')
    expect(screen.getByRole('button', { name: /Send answer/ })).toBeTruthy()
  })
})

describe('the question card, once it ended', () => {
  it('says what she answered', () => {
    render(<QuestionCard seg={card({ outcome: 'answered', answers: [{ selected: [1], other: 'with backups' }] })} onAnswer={vi.fn()} />)
    expect(screen.getByText('You answered “Database”: SQLite; “with backups”')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('says why it was withdrawn when nobody answered it', () => {
    render(<QuestionCard seg={card({ outcome: 'cancelled', ended: 'its turn was stopped' })} onAnswer={vi.fn()} />)
    expect(screen.getByText(/The question was withdrawn: its turn was stopped\./)).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('shows a question she cannot answer here, saying so, with nothing to press', () => {
    const note = "Codex asked this through its own question tool, which PersonalClaw cannot send an answer to, so it goes on without one. Answer in your next message if it still needs one."
    const seg = questionSegmentOf({ ...FRAME, answerable: false, note })!
    render(<QuestionCard seg={seg} onAnswer={vi.fn()} />)
    expect(screen.getByText('Which database should the service use?')).toBeTruthy()
    expect(screen.getByText(note)).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('the question frames on a turn', () => {
  it('opens one card per question however often its frame arrives, and settles it', () => {
    let segs: Segment[] = []
    segs = applyQuestionFrame(segs, FRAME)
    segs = applyQuestionFrame(segs, FRAME)
    expect(segs).toHaveLength(1)
    segs = applyQuestionResolved(segs, { id: 'q1', outcome: 'answered', answers: [{ selected: [0], other: '' }] })
    expect(segs[0]).toMatchObject({ kind: 'question', outcome: 'answered', answers: [{ selected: [0], other: '' }] })
  })

  it('reads no card from a frame it cannot read', () => {
    expect(applyQuestionFrame([], { id: 'q1', questions: [{ question: 'Which?', options: [] }] })).toEqual([])
    expect(applyQuestionFrame([], { questions: FRAME.questions })).toEqual([])
  })

  it('grafts a waiting question onto the turn running now, once', () => {
    const turns = [{ role: 'user' as const, segments: [{ kind: 'text' as const, text: 'set it up' }] }]
    const grafted = graftPendingQuestions(turns, [FRAME])
    expect(grafted).toHaveLength(2)
    expect(grafted[1].segments[0]).toMatchObject({ kind: 'question', id: 'q1' })
    expect(graftPendingQuestions(grafted, [FRAME])).toBe(grafted)
  })
})
