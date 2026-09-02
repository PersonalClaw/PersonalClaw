import { describe, expect, it } from 'vitest'
import { isRawSessionId, sessionTitle } from './sessionTitle'

describe('isRawSessionId', () => {
  it('treats absent / blank titles as non-human', () => {
    expect(isRawSessionId(undefined)).toBe(true)
    expect(isRawSessionId('')).toBe(true)
    expect(isRawSessionId('   ')).toBe(true)
    expect(isRawSessionId(null)).toBe(true)
  })

  it('treats the raw chat-N-epoch machine slug as non-human', () => {
    expect(isRawSessionId('chat-1-1788314931')).toBe(true)
    expect(isRawSessionId('chat-42-1700000000')).toBe(true)
  })

  it('treats a title equal to the session key as non-human', () => {
    expect(isRawSessionId('chat-1-1788314931', 'chat-1-1788314931')).toBe(true)
    // even a key that is not the standard slug shape
    expect(isRawSessionId('weird-key', 'weird-key')).toBe(true)
  })

  it('accepts genuine human titles', () => {
    expect(isRawSessionId('Fix the login bug')).toBe(false)
    expect(isRawSessionId('Fork of Deploy plan')).toBe(false)
    // a title that merely mentions "chat" but is not the slug shape
    expect(isRawSessionId('chatting about chat-1 ideas')).toBe(false)
  })
})

describe('sessionTitle', () => {
  it('passes a real user/auto title through unchanged', () => {
    expect(sessionTitle({ key: 'chat-1-1788314931', title: 'Fix the login bug' }))
      .toBe('Fix the login bug')
  })

  it('humanizes a raw-id title using the first-user-message / prompt preview', () => {
    expect(sessionTitle({
      key: 'chat-1-1788314931',
      title: 'chat-1-1788314931',
      prompt_preview: 'How do I add pagination to the users endpoint?',
    })).toBe('How do I add pagination to the users endpoint?')
  })

  it('prefers prompt_preview over last_message', () => {
    expect(sessionTitle({
      key: 'chat-2-1788314931',
      title: 'chat-2-1788314931',
      prompt_preview: 'first user message',
      last_message: 'a later reply',
    })).toBe('first user message')
  })

  it('falls back to last_message when there is no prompt preview', () => {
    expect(sessionTitle({
      key: 'chat-3-1788314931',
      last_message: 'a recent conversational line',
    })).toBe('a recent conversational line')
  })

  it('truncates a long first message cleanly on a word boundary', () => {
    const long =
      'Please walk me through refactoring the authentication middleware so that it supports both session cookies and bearer tokens without breaking the existing tests'
    const out = sessionTitle({ key: 'chat-4-1788314931', prompt_preview: long })
    expect(out.length).toBeLessThanOrEqual(61) // <= 60 chars + ellipsis
    expect(out.endsWith('…')).toBe(true)
    expect(out).not.toContain('  ') // whitespace collapsed
    expect(long).toContain(out.replace('…', '').trim()) // no mid-word garble
  })

  it('collapses interior whitespace/newlines in a snippet', () => {
    expect(sessionTitle({
      key: 'chat-5-1788314931',
      prompt_preview: 'line one\n\n   line two',
    })).toBe('line one line two')
  })

  it('falls back to "Untitled chat · <relative>" for an empty chat with a timestamp', () => {
    const created = new Date(Date.now() - 3 * 3600 * 1000).toISOString() // 3h ago
    expect(sessionTitle({ key: 'chat-6-1788314931', created }))
      .toBe('Untitled chat · 3h')
  })

  it('falls back to bare "Untitled chat" when no snippet and no timestamp', () => {
    expect(sessionTitle({ key: 'chat-7-1788314931' })).toBe('Untitled chat')
    expect(sessionTitle({ key: 'chat-7-1788314931', title: 'chat-7-1788314931' }))
      .toBe('Untitled chat')
  })

  it('never echoes the raw session key', () => {
    const out = sessionTitle({ key: 'chat-8-1788314931', title: 'chat-8-1788314931' })
    expect(out).not.toContain('chat-8-1788314931')
  })
})
