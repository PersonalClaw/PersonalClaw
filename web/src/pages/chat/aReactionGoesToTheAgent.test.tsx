import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { hydrateTurns, withReactionRow, withAnswerStamp, assistantTurn, userTurn, type HistMsg } from './chatTypes'
import { AssistantActions } from './MessageActions'
import { reactionLine } from './reactions'

// ── A reaction to the agent's answer, as the page reads and draws it ───────────────────────────
//
// Every finished answer has React beside Copy and Branch: five reactions, one at a time. Picking
// one shows it on the answer and sends it to the agent, in the gateway's words, as a message with
// no words of its own (`meta.reaction`); the row it sends keeps `{ to, pick, previous }`. The
// answer's reaction is the last such row for it, so a reload reads it from the rows, and the row is
// drawn as a line where it was sent ("You reacted 👍 to “…”"), not as her bubble.

afterEach(() => cleanup())

const ASK = '2026-11-23T22:30:00+00:00'
const ANSWER = '2026-11-23T22:31:00+00:00'
const QUESTION = 'Should I go ahead and apply the fix to predict.py?'
const rows = (...more: HistMsg[]): HistMsg[] => [
  { role: 'user', content: 'Fix the off-by-one.', ts: ASK },
  { role: 'assistant', content: QUESTION, ts: ANSWER },
  ...more,
]
const reacted = (ts: string, pick: string | null, previous: string | null): HistMsg => ({
  role: 'user', content: `The user reacted … "${QUESTION}"`, ts, meta: { reaction: { to: ANSWER, pick, previous } },
})

describe('a reaction read back from the rows', () => {
  it('🔴 names each finished answer by its row time and shows the reaction its last row gave', () => {
    const turns = hydrateTurns(rows(reacted('2026-11-23T22:32:00+00:00', 'thumbs_up', null)))
    const answer = turns.find((t) => t.role === 'assistant')!
    expect(answer.answerTs).toBe(ANSWER)
    expect(answer.reaction).toBe('thumbs_up')
    const line = turns[turns.length - 1]
    expect(line.reacted).toEqual({ to: ANSWER, pick: 'thumbs_up', previous: null, quote: QUESTION })
  })

  it('🔴 a later row changes it, and one that takes it back leaves none', () => {
    const changed = hydrateTurns(rows(reacted('2026-11-23T22:32:00+00:00', 'thumbs_up', null), reacted('2026-11-23T22:33:00+00:00', 'heart', 'thumbs_up')))
    expect(changed.find((t) => t.role === 'assistant')!.reaction).toBe('heart')
    const removed = hydrateTurns(rows(reacted('2026-11-23T22:32:00+00:00', 'thumbs_up', null), reacted('2026-11-23T22:33:00+00:00', null, 'thumbs_up')))
    expect(removed.find((t) => t.role === 'assistant')!.reaction).toBeUndefined()
  })

  it("draws each row's line in the owner's words", () => {
    expect(reactionLine({ pick: 'thumbs_up', previous: null }, 'Should I go ahead')).toBe('You reacted 👍 to “Should I go ahead”')
    expect(reactionLine({ pick: 'thumbs_down', previous: 'thumbs_up' }, 'Should I go ahead')).toBe('You changed your reaction to “Should I go ahead” from 👍 to 👎')
    expect(reactionLine({ pick: null, previous: 'heart' }, 'Should I go ahead')).toBe('You removed your ❤️ from “Should I go ahead”')
  })
})

describe('a reaction told live', () => {
  it("🔴 a frame's reaction draws its line and shows on the answer it is to", () => {
    const prev = [userTurn('Fix it', ASK), { ...assistantTurn(QUESTION), answerTs: ANSWER }]
    const next = withReactionRow(prev, userTurn('The user reacted …', '2026-11-23T22:32:00+00:00'), { to: ANSWER, pick: 'celebrate', previous: null })
    expect(next[1].reaction).toBe('celebrate')
    expect(next[2].reacted?.quote).toBe(QUESTION)
    expect(withReactionRow(prev, userTurn('hello'), undefined)[2].reacted).toBeUndefined()
  })

  it("🔴 a finished answer takes its row time from the chat's read, which the stream never carries", () => {
    const live = [userTurn('Fix it', ASK), assistantTurn(QUESTION)]
    expect(withAnswerStamp(live, hydrateTurns(rows()))[1].answerTs).toBe(ANSWER)
  })
})

describe("an answer's React", () => {
  const actions = (props: Partial<Parameters<typeof AssistantActions>[0]> = {}) =>
    render(<AssistantActions text={QUESTION} isLast={false} onCopy={() => {}} onRegenerate={() => {}} onFork={() => {}} onSpeak={() => {}} {...props} />)

  it('🔴 offers the five reactions by name and sends the one picked', () => {
    const onReact = vi.fn()
    actions({ onReact })
    fireEvent.click(screen.getByRole('button', { name: 'React' }))
    for (const name of ['thumbs up', 'thumbs down', 'heart', 'celebrate', 'thinking']) expect(screen.getByRole('button', { name })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'thumbs up' }))
    expect(onReact).toHaveBeenCalledWith('thumbs_up')
  })

  it('🔴 shows the chosen one, pressed, says so as its description, and picking it again takes it back', () => {
    const onReact = vi.fn()
    actions({ onReact, reaction: 'thumbs_up' })
    const react = screen.getByRole('button', { name: 'React' })
    expect(react.getAttribute('aria-pressed')).toBe('true')
    expect(document.getElementById(react.getAttribute('aria-describedby')!)?.getAttribute('aria-label')).toBe('You reacted thumbs up')
    fireEvent.click(react)
    const chosen = screen.getByRole('button', { name: 'thumbs up' })
    expect(chosen.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(chosen)
    expect(onReact).toHaveBeenCalledWith(null)
  })

  it('offers no React on an answer that takes none (still being written)', () => {
    actions()
    expect(screen.queryByRole('button', { name: 'React' })).toBeNull()
  })
})
