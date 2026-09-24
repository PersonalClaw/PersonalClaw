// Optimizer context assembly (CHAT-CRAFT CC-5).
//
// The prompt optimizer sees only what the composer hands it, so the quality of
// `/api/optimizer/optimize`'s rewrite IS the quality of this one string. Three
// properties carry that weight, and each fails silently if it slips:
//
//  1. **Role labels.** "that file from earlier" is resolvable only if the model can
//     tell who mentioned the file. An unlabeled blob reads as a single voice, so the
//     optimizer guesses at the referent instead of reading it.
//  2. **Newest-last.** The handler keeps the TAIL of an over-long context, so the
//     newest exchange is the half that survives a cut. Backwards and the truncation
//     throws away exactly the turns the draft is most likely to be about.
//  3. **Whole turns only.** Budgeting the turn count here — instead of handing over a
//     long string and letting the handler cut it — is what keeps a surviving turn's
//     role label attached to its body. A character-slice decapitates the oldest
//     survivor and attributes its words to nobody.
//
// `CTX_BUDGET_CHARS` is the worst case this module can emit. The handler's
// `MAX_CONTEXT_CHARS` (src/personalclaw/dashboard/handlers/optimizer.py) is derived
// from the same three numbers, so a well-formed composer context is never cut at all.
// If the two drift apart, the handler quietly starts cutting again.

import { turnText, type ChatTurn } from './chatTypes'

/** How many of the newest turns the optimizer gets to see. */
export const CTX_MAX_TURNS = 10
/** Per-turn body budget, before the clip marker. */
export const CTX_TURN_CHARS = 400
/** Appended to a clipped turn so the model reads the cut as "the quote ran out"
 *  rather than "the speaker stopped mid-sentence". */
const CLIP = '…'
const LABELS: Record<ChatTurn['role'], string> = { user: 'user: ', assistant: 'assistant: ' }
const WIDEST_LABEL = LABELS.assistant.length

/** The largest context this module can produce: every one of `CTX_MAX_TURNS` turns
 *  clipped at the per-turn budget, wearing the longer label, plus the N-1 newlines that
 *  join them. MUST equal `MAX_CONTEXT_CHARS` in dashboard/handlers/optimizer.py — that
 *  equality is what makes "survives the handler's cap" true by construction rather than
 *  by luck. Asserted exactly (not as an upper bound) in optimizerContext.test.ts and
 *  cross-checked against this file's own constants in test_optimizer.py. */
export const CTX_BUDGET_CHARS =
  CTX_MAX_TURNS * (CTX_TURN_CHARS + CLIP.length + WIDEST_LABEL) + (CTX_MAX_TURNS - 1)

/** Assemble the optimizer's `<context>` payload from the live transcript: the newest
 *  `CTX_MAX_TURNS` non-empty turns, role-labeled, one per line, newest LAST. */
export function buildOptimizerContext(turns: ChatTurn[]): string {
  const lines: string[] = []
  // Walk newest → oldest and stop at the turn budget, then reverse. Spending the
  // budget from the newest end is what guarantees that what gets dropped is always
  // the OLDEST turn, and always a whole one.
  for (let i = turns.length - 1; i >= 0 && lines.length < CTX_MAX_TURNS; i--) {
    const t = turns[i]
    const body = turnText(t)
    if (!body) continue // tool-only or still-empty turn: a bare role label is noise
    const clipped = body.length > CTX_TURN_CHARS ? body.slice(0, CTX_TURN_CHARS) + CLIP : body
    // One turn per line, so a line boundary always means a turn boundary. The handler
    // snaps its backstop truncation to newlines; an embedded newline here would let it
    // cut mid-turn while still looking boundary-aligned.
    lines.push(LABELS[t.role] + clipped.replace(/\s*\n\s*/g, ' '))
  }
  return lines.reverse().join('\n')
}
