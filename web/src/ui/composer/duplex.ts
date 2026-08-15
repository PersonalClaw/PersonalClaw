/** Hands-free (duplex) transcript accumulation — MULTIMODAL-IO §4.1.
 *
 *  The frontend owns the microphone, so it owns the buffer: in hands-free mode a
 *  dictated transcript accumulates here and only becomes a turn once the operator
 *  says a confirmation phrase. An exit phrase throws the buffer away.
 *
 *  The two matchers mirror `is_confirmation` / `is_exit` in
 *  `src/personalclaw/voice/duplex.py`, including the tail-anchored window —
 *  keep the rules in the two files in step. The phrase lists come from
 *  `voice.confirmation_phrases` / `voice.exit_phrases`.
 */

/** The confirmation is the last thing the operator says; a phrase buried at the
 *  head of a long dictation is part of the thought, not the trigger. */
export const TAIL_WINDOW_WORDS = 6

export type HandsFreeAction = 'accumulate' | 'submit' | 'clear' | 'ignore'

export interface HandsFreeStep {
  /** The buffer after this chunk — the text to send on `submit`, `''` on `clear`. */
  buffer: string
  action: HandsFreeAction
}

function words(text: string): string[] {
  return (text.toLowerCase().match(/[a-z0-9]+(?:'[a-z]+)?/g) ?? [])
}

function phraseInTail(text: string, phrases: readonly string[], tailWords: number): string[] | null {
  const tokens = words(text)
  if (!tokens.length) return null
  for (const phrase of phrases) {
    if (typeof phrase !== 'string') continue
    const needle = words(phrase)
    if (!needle.length) continue
    // The window must stretch to hold a phrase longer than tailWords itself.
    const window = tokens.slice(-Math.max(tailWords, needle.length))
    for (let i = 0; i + needle.length <= window.length; i++) {
      if (needle.every((w, j) => window[i + j] === w)) return needle
    }
  }
  return null
}

/** True when `text` ends with a phrase that should fire the buffered turn. */
export function isConfirmation(text: string, phrases: readonly string[], tailWords = TAIL_WINDOW_WORDS): boolean {
  if (!text.trim()) return false
  return phraseInTail(text, phrases, tailWords) !== null
}

/** True when `text` ends with a phrase that should clear the buffer. */
export function isExit(text: string, phrases: readonly string[], tailWords = TAIL_WINDOW_WORDS): boolean {
  if (!text.trim()) return false
  return phraseInTail(text, phrases, tailWords) !== null
}

/** Drop the trailing confirmation phrase from a chunk — "draft it and send it"
 *  submits "draft it", not the trigger words. */
export function stripTrailingPhrase(text: string, phrases: readonly string[]): string {
  const matched = phraseInTail(text, phrases, TAIL_WINDOW_WORDS)
  if (!matched) return text.trim()
  // Walk word tokens backwards over the tail so punctuation/casing in the raw
  // text survives everything before the phrase.
  const re = new RegExp(
    `[\\s,.;:!?-]*${matched.map((w) => `${w}[\\s,.;:!?-]*`).join('')}$`,
    'i',
  )
  return text.replace(re, '').trim()
}

/** Fold one transcription chunk into the hands-free buffer.
 *
 *  Exit wins over confirmation: "send it — no, cancel" must not send. */
export function accumulateTranscript(
  buffer: string,
  chunk: string,
  phrases: { confirmation: readonly string[]; exit: readonly string[] },
): HandsFreeStep {
  const text = (chunk ?? '').trim()
  if (!text) return { buffer, action: 'ignore' }
  if (isExit(text, phrases.exit)) return { buffer: '', action: 'clear' }
  if (isConfirmation(text, phrases.confirmation)) {
    const tail = stripTrailingPhrase(text, phrases.confirmation)
    const full = [buffer, tail].filter(Boolean).join(' ').trim()
    // "go ahead" with nothing dictated yet is a stray confirmation, not a turn.
    return full ? { buffer: full, action: 'submit' } : { buffer: '', action: 'clear' }
  }
  return { buffer: [buffer, text].filter(Boolean).join(' ').trim(), action: 'accumulate' }
}
