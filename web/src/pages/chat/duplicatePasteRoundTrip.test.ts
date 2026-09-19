import { describe, expect, it } from 'vitest'
import { hydrateTurns, turnText, type HistMsg } from './chatTypes'
import { expandPasteMarkers, markerFor, type PasteBlock } from './pasteBlocks'

/** #380 — two identical pastes in one message must survive a reload as two chips.
 *
 *  The send path EXPANDS each `[Paste #N]` marker to its block (the model sees the real
 *  text); the reload path re-collapses using `meta.pastes`. That re-collapse used to be
 *  `out.split(p.content).join(marker)` — a GLOBAL replace — so with two blocks of
 *  identical content the first pass rewrote BOTH occurrences to "[Paste #1]" and the
 *  second pass found nothing left. `PasteChip` then dropped the unresolvable marker, so
 *  the user's second block was simply gone from the bubble.
 *
 *  The property under test is the round trip: re-collapsing an expanded draft must give
 *  the draft back. */

const block = (seq: number, content: string): PasteBlock => ({
  id: `p${seq}`, seq, lines: content.split('\n').length, content,
})

/** The reload path, end to end: what the bubble renders for a persisted user message. */
function reloaded(stored: string, blocks: PasteBlock[]): string {
  const messages: HistMsg[] = [{
    role: 'user',
    content: stored,
    meta: { pastes: blocks.map(({ seq, lines, content }) => ({ seq, lines, content })) },
  }]
  return turnText(hydrateTurns(messages)[0])
}

const CODE = 'def a():\n    return 1\n\n# end\n'
const OTHER = 'SELECT 1\nFROM t\nWHERE x\n;\n'

describe('re-collapsing a reloaded message round-trips', () => {
  it('two IDENTICAL pastes come back as two distinct chips', () => {
    const blocks = [block(1, CODE), block(2, CODE)]
    const draft = `before ${markerFor(1)} middle ${markerFor(2)} after`
    const stored = expandPasteMarkers(draft, blocks)
    // The bug, pinned: this used to be "before [Paste #1] middle [Paste #1] after".
    expect(reloaded(stored, blocks)).toBe(draft)
  })

  it('three identical pastes each keep their own seq', () => {
    const blocks = [block(1, CODE), block(2, CODE), block(3, CODE)]
    const draft = `${markerFor(1)}|${markerFor(2)}|${markerFor(3)}`
    expect(reloaded(expandPasteMarkers(draft, blocks), blocks)).toBe(draft)
  })

  it('distinct pastes still round-trip — the control', () => {
    const blocks = [block(1, CODE), block(2, OTHER)]
    const draft = `a ${markerFor(1)} b ${markerFor(2)} c`
    expect(reloaded(expandPasteMarkers(draft, blocks), blocks)).toBe(draft)
  })

  it('a block whose content CONTAINS another block still round-trips (the longest-first pass)', () => {
    // Order matters here: the SHORT block's marker comes first in the draft, so a
    // naive seq-order pass would consume the short content out of the long block's
    // expansion and strand the long one. Longest-first is what keeps this green.
    const inner = 'return 1\n'
    const outer = `def a():\n    ${inner}`
    const blocks = [block(1, inner), block(2, outer)]
    const draft = `${markerFor(2)} then ${markerFor(1)}`
    expect(reloaded(expandPasteMarkers(draft, blocks), blocks)).toBe(draft)
  })

  it('emits exactly one marker per block — never a duplicate label', () => {
    const blocks = [block(1, CODE), block(2, CODE)]
    const out = reloaded(expandPasteMarkers(`x ${markerFor(1)} y ${markerFor(2)}`, blocks), blocks)
    expect(out.match(/\[Paste #1\]/g) ?? []).toHaveLength(1)
    expect(out.match(/\[Paste #2\]/g) ?? []).toHaveLength(1)
  })

  it('prose that duplicates a paste keeps BOTH copies — one chip, one literal', () => {
    // The comment-thread variant. The global replace destroyed the typed copy by turning
    // it into a second chip; a single-shot replacement cannot lose text. Which occurrence
    // becomes the chip is still content-matched (the mapping is not positional — the
    // persisted `meta.pastes` carries no offsets), so this asserts what DOES hold: the
    // marker count is right and no content disappears.
    const blocks = [block(1, CODE)]
    const stored = `compare ${CODE} with ${CODE}`
    const out = reloaded(stored, blocks)
    expect(out.match(/\[Paste #1\]/g) ?? []).toHaveLength(1)
    expect(out).toContain(CODE.trim())          // the other copy survives verbatim
  })
})
