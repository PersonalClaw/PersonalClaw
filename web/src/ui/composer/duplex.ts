import phraseTable from '../../../../src/personalclaw/voice/phrases.json'

/** Hands-free (duplex) transcript accumulation.
 *
 *  The frontend owns the microphone, so it owns the buffer: in hands-free mode a
 *  dictated transcript accumulates here and only becomes a turn once the operator
 *  says a confirmation phrase. An exit phrase throws the buffer away.
 *
 *  The two matchers mirror `is_confirmation` / `is_exit` in
 *  `src/personalclaw/voice/duplex.py`, including the tail-anchored window. Matching is
 *  logic, so it exists in both languages; `duplexPhraseCases.json` beside this file pins
 *  its behaviour, and both languages' tests run every case. The phrase lists come from
 *  `voice.confirmation_phrases` / `voice.exit_phrases`.
 */

/** The confirmation is the last thing the operator says; a phrase buried at the
 *  head of a long dictation is part of the thought, not the trigger. */
export const TAIL_WINDOW_WORDS = 6

/** The shipped phrase lists, for a host that could not read `voice.*`. They come from
 *  `src/personalclaw/voice/phrases.json`, the table the backend's config defaults are read
 *  from too, so the two halves of the loop cannot ship different defaults. */
export const DEFAULT_PHRASES: { readonly confirmation: readonly string[]; readonly exit: readonly string[] } = {
  confirmation: phraseTable.confirmation,
  exit: phraseTable.exit,
}

export type HandsFreeAction = 'accumulate' | 'submit' | 'clear' | 'ignore'

export interface HandsFreeStep {
  /** The buffer after this chunk — the text to send on `submit`, `''` on `clear`. */
  buffer: string
  action: HandsFreeAction
}

interface Word {
  /** Lowercased, apostrophes removed. */
  text: string
  /** Where the word starts in the text as written. */
  start: number
}

// A word is a run of letters, digits and the apostrophes inside a contraction; it is
// compared lowercased with its apostrophes removed, so "don't", "don’t" and "dont" are
// one word. Spaces, hyphens and punctuation only separate words.
const WORD_RE = /[A-Za-z0-9'\u2018\u2019\u02bc]+/g
const APOSTROPHE_RE = /['\u2018\u2019\u02bc]/g

function words(text: string): Word[] {
  const out: Word[] = []
  for (const m of text.matchAll(WORD_RE)) {
    const folded = m[0].replace(APOSTROPHE_RE, '').toLowerCase()
    if (folded) out.push({ text: folded, start: m.index })
  }
  return out
}

/** Every run of whole words `[first, last]` inside the trailing window that spells `phrase`.
 *
 *  A run spells a phrase when the two are equal with the spaces between their words taken
 *  out: speech-to-text writes "never mind" as "Nevermind", "never-mind" or "Never mind.",
 *  and each of them is the phrase. Only whole words count, so "remind" or "whenever" never
 *  supplies part of one. With `splitWords` a single word of the phrase may also arrive as
 *  two ("never mind" for a "nevermind" phrase); without it a run may only join the
 *  phrase's own words together. */
function phraseRuns(tokens: readonly Word[], phrase: string, tailWords: number, splitWords: boolean): [number, number][] {
  const parts = words(phrase).map((w) => w.text)
  const key = parts.join('')
  if (!key) return []
  // Where the phrase itself has a boundary between two of its words, as offsets into `key`.
  const bounds = new Set<number>()
  let offset = 0
  for (const part of parts.slice(0, -1)) {
    offset += part.length
    bounds.add(offset)
  }
  const runs: [number, number][] = []
  for (let i = 0; i < tokens.length; i++) {
    let run = ''
    for (let j = i; j < tokens.length; j++) {
      run += tokens[j].text
      if (!key.startsWith(run)) break
      if (run.length === key.length) {
        // The window must stretch to hold a run longer than tailWords itself.
        if (i >= tokens.length - Math.max(tailWords, j - i + 1)) runs.push([i, j])
        break
      }
      if (!splitWords && !bounds.has(run.length)) break
    }
  }
  return runs
}

function phraseInTail(text: string, phrases: readonly string[], tailWords: number, splitWords: boolean): boolean {
  const tokens = words(text)
  if (!tokens.length) return false
  return phrases.some((phrase) => typeof phrase === 'string' && phraseRuns(tokens, phrase, tailWords, splitWords).length > 0)
}

/** True when `text` ends with a phrase that should fire the buffered turn.
 *
 *  A confirmation is matched more strictly than an exit: its words may run together
 *  ("Sendit.") but a word of the phrase is never assembled from two words that were said
 *  ("Goa head" is not "go ahead"). A false confirmation sends a half-finished thought; a
 *  false exit only discards one. */
export function isConfirmation(text: string, phrases: readonly string[], tailWords = TAIL_WINDOW_WORDS): boolean {
  if (!text.trim()) return false
  return phraseInTail(text, phrases, tailWords, false)
}

/** True when `text` ends with a phrase that should clear the buffer. */
export function isExit(text: string, phrases: readonly string[], tailWords = TAIL_WINDOW_WORDS): boolean {
  if (!text.trim()) return false
  return phraseInTail(text, phrases, tailWords, true)
}

/** Drop the trailing confirmation phrase from a chunk — "draft it and send it"
 *  submits "draft it", not the trigger words. */
export function stripTrailingPhrase(text: string, phrases: readonly string[]): string {
  const tokens = words(text)
  // The earliest-starting run that ends the chunk, so everything before it survives with
  // its own punctuation and casing.
  let first = -1
  for (const phrase of phrases) {
    if (typeof phrase !== 'string') continue
    for (const [i, j] of phraseRuns(tokens, phrase, TAIL_WINDOW_WORDS, false)) {
      if (j === tokens.length - 1 && (first < 0 || i < first)) first = i
    }
  }
  if (first < 0) return text.trim()
  // The punctuation joining the thought to the trigger goes with it.
  return text.slice(0, tokens[first].start).replace(/[\s,.;:!?-]+$/, '').trim()
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
