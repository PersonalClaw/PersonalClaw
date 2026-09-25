import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useStreamCoalescer } from './useStreamCoalescer'
import { applyCoalescedFlush, TextRunOwnership } from './coalesceReducers'
import type { Segment } from './chatTypes'

/** K44 / issue #548 — a finished text run must not be re-emitted into the next turn.
 *
 *  The original K44 fix guarded ONE branch (`chat_chunk`) with a `breakText` ref whose clearing was
 *  DEFERRED to that branch. Six boundaries — `chat_thinking`, `chat_message` (error), `tool_call`,
 *  `approval`, `chat_segment`, `chat_done` — called a drain-only `flushNow()` without consulting it,
 *  and a drain moves the reveal cursor without emptying the buffer. So a turn whose FIRST frame was
 *  a tool call (an agent leading with a search or a file read: an ordinary turn shape) drained the
 *  PREVIOUS turn's entire answer into the new turn's bubble, above the tool card.
 *
 *  The fix is one mechanism instead of six special cases: a boundary ALWAYS clears the buffer, and
 *  the only choice is whether the tail lands first (`seal`) or is discarded (`reset`). These tests
 *  drive the REAL hook through the same two-line wiring `ChatPage` uses (`endTextRun` /
 *  `dropTextRun` + `TextRunOwnership.flush` over the trailing assistant turn), because the defect
 *  lived in the wiring and not in either piece on its own.
 */

const TURN_1 = 'Turn ONE full answer.'

/** The ChatPage stream wiring, minus React: a list of assistant turns, synchronous text-run
 *  ownership, and the two boundary primitives verbatim. */
function harness(opts: { immediate?: boolean } = { immediate: true }) {
  const turns: Segment[][] = [[]]
  const textRun = new TextRunOwnership()
  const { result } = renderHook(() =>
    useStreamCoalescer((revealed) => {
      turns[turns.length - 1] = textRun.flush(revealed)(turns[turns.length - 1])
    }, opts),
  )
  return {
    turns,
    last: () => turns[turns.length - 1],
    texts: (i: number) => turns[i].filter((s) => s.kind === 'text').map((s) => (s as { text: string }).text),
    push: (chunk: string) => act(() => { result.current.push(chunk) }),
    endTextRun: () => act(() => { result.current.seal(); textRun.release() }),
    dropTextRun: () => act(() => { result.current.reset(); textRun.release() }),
    reveal: () => act(() => { result.current.reveal() }),
    /** A new assistant turn arrives (the transcript tail moves). */
    newTurn: () => { turns.push([]) },
  }
}

describe('a boundary clears the coalesced run', () => {
  it('does NOT leak turn N into a tool-first turn N+1', () => {
    const h = harness()
    h.push(TURN_1)
    h.endTextRun()          // chat_done closes turn 1
    h.newTurn()
    h.endTextRun()          // turn 2 opens with tool_call → its boundary flush
    expect(h.texts(0)).toEqual([TURN_1])
    expect(h.texts(1)).toEqual([])                       // ← the leak: used to be [TURN_1]
    expect(h.last().some((s) => s.kind === 'text')).toBe(false)
  })

  it('lands the buffered tail in the run that produced it', () => {
    // Sealing must not DISCARD: the words the model already sent belong in the turn they came from.
    const h = harness()
    h.push('A ')
    h.push('B')
    h.endTextRun()
    expect(h.texts(0)).toEqual(['A B'])
  })

  it('opens a FRESH segment for text after a boundary, in the same turn', () => {
    // Prose, then a tool card, then more prose: two text segments, the second not glued to the first.
    const h = harness()
    h.push('before the tool')
    h.endTextRun()
    h.push('after the tool')
    expect(h.texts(0)).toEqual(['before the tool', 'after the tool'])
  })

  it('never writes an EMPTY text segment for a boundary on an empty run', () => {
    // `chat_thinking` fires a boundary per reasoning chunk, and a tool-first turn's boundary lands on
    // an empty run. An unconditional emit put a blank text segment above every tool card.
    const h = harness()
    h.endTextRun()
    h.endTextRun()
    h.endTextRun()
    expect(h.last()).toEqual([])
  })

  it('discards rather than lands when the CLIENT moved the tail first', () => {
    // A fresh send / regenerate / edit-resend pushes the new turn BEFORE the boundary, so landing
    // the tail would write the old answer into the new turn instead of the finished one.
    const h = harness()
    h.push(TURN_1)
    h.newTurn()
    h.dropTextRun()
    h.push('fresh')
    expect(h.texts(1)).toEqual(['fresh'])
    expect(h.texts(1).join('')).not.toContain(TURN_1)
  })

  it('still accumulates across pushes WITHIN a run in immediate mode', () => {
    // The clearing belongs to boundaries only. Clearing on every flush would make immediate mode
    // (each push paints synchronously) render just the newest chunk.
    const h = harness({ immediate: true })
    h.push('one ')
    h.push('two ')
    h.push('three')
    expect(h.texts(0)).toEqual(['one two three'])
  })

  it('reveals the whole tail at a boundary even mid-animation', () => {
    // Animated mode reveals on a per-frame budget; a boundary has to publish everything buffered,
    // not just what the reveal cursor had reached, or the turn closes with its last words missing.
    const h = harness({ immediate: false })
    h.push('a'.repeat(5000))
    h.endTextRun()
    expect(h.texts(0)).toEqual(['a'.repeat(5000)])
  })

  it('captures replacement ownership before chat_done releases a deferred terminal flush', () => {
    // The interleaving is the whole defect, so it is spelled out rather than approximated:
    // `endTextRun` calls `coalescer.seal()`, which emits the terminal flush and QUEUES a
    // React state updater, and only then clears ownership — synchronously, before React
    // applies that updater. A flush whose updaters all run *after* the release does not
    // reproduce it (the first apply re-sets the flag for the second), which is why the
    // release below lands BETWEEN the two applies.
    //
    // 🔴 In-test vacuity floor: both wirings are driven through the same sequence in the
    // same test. The old one must read TWO text segments — the answer painted twice — or
    // this test is measuring nothing and the assertion below is free.
    const drive = (onFlush: (revealed: string) => void, apply: () => void, release: () => void) => {
      onFlush('partial')          // a mid-stream rAF flush, applied normally
      apply()
      onFlush('complete answer')  // coalescer.seal() emits the tail and queues its updater
      release()                   // ...then endTextRun clears ownership, synchronously
      apply()                     // React finally applies the queued updater
    }

    const oldWiring = () => {
      const coalescing = { current: false }
      let segments: Segment[] = []
      let pending: (() => void) | null = null
      drive(
        (revealed) => {
          pending = () => {
            // The defect: ownership is read HERE, inside the deferred updater.
            segments = applyCoalescedFlush(segments, revealed, coalescing.current)
            coalescing.current = true
          }
        },
        () => { pending?.(); pending = null },
        () => { coalescing.current = false },
      )
      return segments
    }

    const newWiring = () => {
      const textRun = new TextRunOwnership()
      let segments: Segment[] = []
      let pending: (() => void) | null = null
      drive(
        (revealed) => {
          const update = textRun.flush(revealed)
          pending = () => { segments = update(segments) }
        },
        () => { pending?.(); pending = null },
        () => { textRun.release() },
      )
      return segments
    }

    expect(oldWiring()).toEqual([
      { kind: 'text', text: 'partial' },
      { kind: 'text', text: 'complete answer' },
    ])
    expect(newWiring()).toEqual([{ kind: 'text', text: 'complete answer' }])
  })
})

// `reveal` exists for the frames replayed onto an adopted snapshot (snapshotReplay.ts): text the
// tab was already showing must paint with the snapshot, not animate back in. It is the private
// drain made public, so it is pinned to NOT being a boundary.
describe('reveal paints the buffered run now, without ending it', () => {
  it('shows everything buffered, and the run goes on in the same segment', () => {
    const h = harness({ immediate: false })
    h.push('already on screen before the snapshot, ')
    h.reveal()
    expect(h.texts(0)).toEqual(['already on screen before the snapshot, '])
    h.push('and then more.')
    h.endTextRun()
    expect(h.texts(0)).toEqual(['already on screen before the snapshot, and then more.'])
  })

  it('writes nothing on an empty run', () => {
    const h = harness({ immediate: false })
    h.reveal()
    expect(h.last(), 'an emit on an empty run writes an empty text segment').toEqual([])
  })
})

