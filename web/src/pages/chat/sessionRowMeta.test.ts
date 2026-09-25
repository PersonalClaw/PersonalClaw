import { describe, expect, it } from 'vitest'
import { sessionRowMeta } from './sessionRowMeta'

describe('sessionRowMeta', () => {
  it('states the real count, singular and plural', () => {
    expect(sessionRowMeta({ messages: 2 })).toBe('2 messages')
    expect(sessionRowMeta({ messages: 1 })).toBe('1 message')
    expect(sessionRowMeta({ messages: 0 })).toBe('0 messages')
  })

  it('keeps running and the model after the count', () => {
    expect(sessionRowMeta({ messages: 4, running: true, model: 'gemma3:4b' })).toBe(
      '4 messages · running · gemma3:4b',
    )
  })

  it('shows nothing for a count the server could not read — never a guess, never "null messages"', () => {
    expect(sessionRowMeta({ messages: null })).toBe('')
    expect(sessionRowMeta({ messages: null, model: 'gemma3:4b' })).toBe('gemma3:4b')
  })
})
