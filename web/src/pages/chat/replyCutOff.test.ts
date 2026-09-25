import { describe, it, expect } from 'vitest'
import { hydrateTurns, type HistMsg } from './chatTypes'

// A reply cut at the model's OUTPUT cap ends mid-sentence. The backend stamps
// `meta.finish_reason: 'length'` on the turn's LAST assistant message (and only then — a reply
// that finished on its own carries no key), and the chat renders a "Cut off" line under it.
// These pin the rehydration half: what a reload shows must be what the live turn showed.

const msg = (role: string, content: string, meta?: HistMsg['meta']): HistMsg =>
  ({ role, content, ts: `t-${content}`, ...(meta ? { meta } : {}) })

describe('hydrateTurns — a cut reply stays marked on reload', () => {
  it('marks the assistant turn whose message stopped at the length limit', () => {
    const turns = hydrateTurns([
      msg('user', 'tell me a long story'),
      msg('assistant', 'Once upon a time, in a land that', { finish_reason: 'length' }),
    ])
    expect(turns.find((t) => t.role === 'assistant')?.cutOff).toBe(true)
  })

  it('leaves a reply that finished on its own unmarked', () => {
    // Vacuity floor: a hydrate that set `cutOff` on every assistant turn passes the case above.
    const turns = hydrateTurns([msg('user', 'hi'), msg('assistant', 'Hello there.')])
    expect(turns.find((t) => t.role === 'assistant')?.cutOff).toBeUndefined()
  })

  it('does not treat any other finish reason as a cut', () => {
    const turns = hydrateTurns([
      msg('user', 'hi'),
      msg('assistant', 'Hello there.', { finish_reason: 'stop' }),
    ])
    expect(turns.find((t) => t.role === 'assistant')?.cutOff).toBeUndefined()
  })

  it('lets the turn\'s LAST message decide when several merge into one turn', () => {
    // Consecutive assistant messages merge into one turn; the stamp is on the last one, so an
    // earlier segment's mark must not latch onto an answer that went on to finish.
    const turns = hydrateTurns([
      msg('user', 'go'),
      msg('assistant', 'first part', { finish_reason: 'length' }),
      msg('assistant', 'and it finished.'),
    ])
    expect(turns.filter((t) => t.role === 'assistant')).toHaveLength(1)
    expect(turns.find((t) => t.role === 'assistant')?.cutOff).toBeUndefined()
  })
})
