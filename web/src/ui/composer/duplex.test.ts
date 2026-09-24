import { describe, it, expect } from 'vitest'
import { accumulateTranscript, isConfirmation, isExit, stripTrailingPhrase } from './duplex'

const CONFIRM = ['do it', 'go ahead', 'send it', 'execute']
const EXIT = ['cancel', 'never mind', 'forget it']
const PHRASES = { confirmation: CONFIRM, exit: EXIT }

describe('isConfirmation / isExit', () => {
  it('matches a trailing phrase, case- and punctuation-insensitively', () => {
    expect(isConfirmation('Do it.', CONFIRM)).toBe(true)
    expect(isConfirmation('draft the email and send it', CONFIRM)).toBe(true)
    expect(isConfirmation('ok go ahead please', CONFIRM)).toBe(true)
    expect(isExit('oh never mind', EXIT)).toBe(true)
  })

  it('is tail-anchored — a confirmation mid-thought does not fire', () => {
    expect(isConfirmation('go ahead and tell me what you think about the plan', CONFIRM)).toBe(false)
  })

  it('rejects empty text, substrings and non-consecutive words', () => {
    expect(isConfirmation('', CONFIRM)).toBe(false)
    expect(isConfirmation('   ', CONFIRM)).toBe(false)
    expect(isConfirmation('execution plan', CONFIRM)).toBe(false)
    expect(isConfirmation('do you want it', CONFIRM)).toBe(false)
    expect(isExit('cancellation policy', EXIT)).toBe(false)
  })

  it('matches a multi-word phrase against a tail shorter than the window', () => {
    expect(isConfirmation('go ahead', CONFIRM, 1)).toBe(true)
  })

  it('fires on nothing when the phrase list is empty', () => {
    expect(isConfirmation('do it', [])).toBe(false)
    expect(isExit('cancel', [])).toBe(false)
  })
})

describe('stripTrailingPhrase', () => {
  it('removes the trigger words and their punctuation', () => {
    expect(stripTrailingPhrase('draft the email and send it', CONFIRM)).toBe('draft the email and')
    // The punctuation joining the thought to the trigger goes with it — a
    // dangling comma is not part of the instruction.
    expect(stripTrailingPhrase('deploy the beta, do it!', CONFIRM)).toBe('deploy the beta')
  })

  it('leaves text with no trailing phrase alone', () => {
    expect(stripTrailingPhrase('what do you think', CONFIRM)).toBe('what do you think')
  })

  it('reduces a bare confirmation to nothing', () => {
    expect(stripTrailingPhrase('go ahead', CONFIRM)).toBe('')
  })
})

describe('accumulateTranscript', () => {
  it('accumulates chunks without firing a turn', () => {
    let step = accumulateTranscript('', 'draft a reply to the release email', PHRASES)
    expect(step).toEqual({ buffer: 'draft a reply to the release email', action: 'accumulate' })
    step = accumulateTranscript(step.buffer, 'keep it short', PHRASES)
    expect(step.action).toBe('accumulate')
    expect(step.buffer).toBe('draft a reply to the release email keep it short')
  })

  it('submits the accumulated text when a confirmation lands, without the trigger', () => {
    const step = accumulateTranscript('draft a reply', 'keep it short, go ahead', PHRASES)
    expect(step).toEqual({ buffer: 'draft a reply keep it short', action: 'submit' })
  })

  it('clears on an exit phrase', () => {
    expect(accumulateTranscript('draft a reply', 'cancel', PHRASES)).toEqual({
      buffer: '',
      action: 'clear',
    })
  })

  it('lets exit win over a confirmation in the same chunk', () => {
    // "send it — no, cancel" must not send.
    const step = accumulateTranscript('draft a reply', 'send it no cancel', PHRASES)
    expect(step.action).toBe('clear')
  })

  it('ignores an empty or whitespace chunk without losing the buffer', () => {
    expect(accumulateTranscript('draft a reply', '   ', PHRASES)).toEqual({
      buffer: 'draft a reply',
      action: 'ignore',
    })
  })

  it('treats a stray confirmation with an empty buffer as a clear, not a turn', () => {
    expect(accumulateTranscript('', 'go ahead', PHRASES)).toEqual({ buffer: '', action: 'clear' })
  })
})
