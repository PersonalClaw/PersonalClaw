import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { transcriptionFailure } from './api'

// ── A recording that did not become text says why, keyed on the gateway's code ──────────────
//
// 🔴 Before: ChatPage matched `/not available/i` against the gateway's words and, on a match,
// replaced them with "Voice input needs a speech-to-text model — configure one". A bound
// provider's own reason that happened to say "not available" was thrown away for advice to set up
// a model that was already bound. The gateway now sends `code: 'stt_unavailable'` with a sentence
// that names what to change, and the composers key on the code. The loop composer dropped a
// failed transcription with nothing said at all.
//
// §1 is the decision; §2 is the call sites, because a right decision nothing calls changes nothing
// a user sees.

describe('§1 what a composer says for a transcription result', () => {
  it('shows the gateway’s sentence as it is when speech-to-text cannot run', () => {
    const reason = 'The Bedrock speech-to-text model is not available until an S3 Bucket is set. '
      + 'Set S3 Bucket on this Amazon Bedrock instance in Settings → Providers (under Advanced).'
    expect(transcriptionFailure({ error: reason, code: 'stt_unavailable' })).toBe(reason)
  })

  it('names a transcription that failed as one, with the failure’s own words', () => {
    expect(transcriptionFailure({ error: 'Amazon Transcribe couldn’t transcribe this audio.' }))
      .toBe('Couldn’t transcribe audio: Amazon Transcribe couldn’t transcribe this audio.')
  })

  it('does not read the words: "not available" without the code is a failure like any other', () => {
    expect(transcriptionFailure({ error: 'The upstream model is not available right now.' }))
      .toBe('Couldn’t transcribe audio: The upstream model is not available right now.')
  })

  it('says nothing for a transcription that worked, or one the echo filter dropped', () => {
    expect(transcriptionFailure({ text: 'hello' })).toBeNull()
    expect(transcriptionFailure({ text: '', filtered: 'echo' })).toBeNull()
  })
})

describe('§2 both composers say it', () => {
  const SRC = join(__dirname, '..')
  const read = (path: string) => readFileSync(join(SRC, path), 'utf8')

  it('the chat composer keys on the code, not the words', () => {
    const chat = read('pages/ChatPage.tsx')
    expect(chat).toMatch(/transcriptionFailure\(r\)/)
    expect(chat).not.toMatch(/\/not available\/i/)
  })

  it('the loop composer says why too, instead of dropping the recording', () => {
    expect(read('pages/loop/LoopComposer.tsx')).toMatch(
      /const failed = transcriptionFailure\(r\)\s*\n\s*if \(failed\) \{ notice\.showError\(failed\)/,
    )
  })
})
