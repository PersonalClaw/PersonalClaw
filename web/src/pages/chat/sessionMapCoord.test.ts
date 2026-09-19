import { describe, it, expect } from 'vitest'
import { hydrateTurns, markCoordOf, type HistMsg } from './chatTypes'
import { sessionMapMarks } from './sessionMap'

// ── SSM-11 — THE JUMP COORDINATE (the defect that made the keystone's jump land wrong) ──────
//
// The rail owns no scroll machinery: it hands `onJumpTo` a mark's `visibleIndex` and reads
// `ChatPage`'s `turnNodes` DOM registry AT THAT SAME COORDINATE (SSM-5's observer). So the
// registry key and the mark coordinate are one rule — `markCoordOf` — and this file is the rail
// that keeps them one.
//
// 🔴 WHY THIS IS A REGRESSION TEST AND NOT A TAUTOLOGY. Before SSM-11 the page registered turn
// nodes under the ARRAY POSITION (`turnNodes.current.set(i, el)`) while every mark carried
// `visibleIndex`. Those two agree only on a transcript `hydrateTurns` did not collapse — which
// every existing Session Map test fixture happens to be, because each hand-written turn sets
// `visibleIndex: i`. On a REAL tool-using transcript they diverge, and the jump silently
// resolved an EARLIER turn's node. The fixture below is therefore built by running the real
// `hydrateTurns` over a real message list that collapses, not by hand — a hand-written fixture
// cannot fail this test, which is exactly how the defect survived six confirmed atoms.
//
// This is the same coordinate gap `branchLineage.ts` documents for the FORK coordinate. The two
// rules stay separate on purpose (see `markCoordOf`'s header): `branchIndexOf` counts
// text-bearing turns, so a streaming turn's value changes mid-answer and a DOM registry keyed on
// it would strand its node.

/** A transcript that COLLAPSES: two consecutive assistant messages merge into one turn, so the
 *  backend's visible-list cursor runs ahead of the rendered turn array from turn 2 onward. */
const COLLAPSING: HistMsg[] = [
  { role: 'user', content: 'run the build' },
  { role: 'assistant', content: 'starting' },
  { role: 'assistant', content: 'done — 0 errors' },
  { role: 'user', content: 'now the tests' },
  { role: 'assistant', content: 'all green' },
]

describe('the Session Map jump coordinate', () => {
  it('🔴 DIVERGES from the array position on a collapsed transcript — the defect this rule fixes', () => {
    const turns = hydrateTurns(COLLAPSING)
    // user, assistant(merged ×2), user, assistant → 4 turns for 5 messages.
    expect(turns).toHaveLength(4)
    const coords = turns.map((t, i) => markCoordOf(t, i))
    expect(coords).toEqual([0, 2, 3, 4])
    // The claim that matters: for at least one turn the coordinate is NOT its array position, so
    // a registry keyed by position resolves the wrong node. Two of the four here.
    const wrong = turns.filter((t, i) => markCoordOf(t, i) !== i)
    expect(wrong.length).toBeGreaterThan(0)
  })

  it('🔑 every mark jumps to a coordinate the node registry would be keyed under', () => {
    const turns = hydrateTurns(COLLAPSING)
    // The page registers exactly these keys (`turnNodes.current.set(markCoordOf(turn, i), el)`).
    const registryKeys = new Set(turns.map((t, i) => markCoordOf(t, i)))
    const marks = sessionMapMarks(turns)
    expect(marks.length).toBeGreaterThan(0)
    for (const mark of marks) {
      expect(registryKeys.has(mark.visibleIndex), `mark ${mark.markIndex} (${mark.kind}) would jump to ${mark.visibleIndex}, which no turn node is registered under`).toBe(true)
    }
  })

  // The Activity → Index anchors used to be asserted here too, as a SECOND list that had to carry
  // the identical coordinates (SSM-12's clause). SSM-13 deleted that list — the map is the session's
  // only index now — so there is no second coordinate to keep in step and the claim retired with it.
  // The `🔑` case above still covers every `user` mark, which is what those anchors mirrored.
  // `indexTabRetired.test.tsx` is the rail that fails if a second index surface comes back.

  it('falls back to the array position for a live turn that carries no coordinate yet', () => {
    // Turns appended from WS frames have no `visibleIndex` until a refetch stamps them. The
    // fallback has to be STABLE for the life of the turn (a registry key that moves strands its
    // node), which is why it is the array position and not a derivation over the turn's content.
    expect(markCoordOf({ visibleIndex: undefined }, 7)).toBe(7)
    expect(markCoordOf({ visibleIndex: 0 }, 7)).toBe(0)
  })
})
