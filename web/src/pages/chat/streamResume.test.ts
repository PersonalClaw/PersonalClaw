import { describe, it, expect, vi } from 'vitest'
import { hydrateTurns, livePartialOf, turnText, type HistMsg } from './chatTypes'
import { CoalescerCore } from './useStreamCoalescer'
import { SnapshotReplay } from './snapshotReplay'
import { snapshotPredatesSend } from './liveRun'

// The pure pieces of "a live answer resumes from a snapshot whole and once". The ChatPage-level
// behaviour (a reload mid-answer, the remount, a turn that ends during the read) is driven in
// `streamedAnswerRendersOnce.test.tsx`; these pin each piece's own contract.

const user = (content: string, ts = 't-u'): HistMsg => ({ role: 'user', content, ts })
const streaming = (content: string): HistMsg => ({ role: 'streaming', content })

describe('hydrateTurns — the in-flight partial is part of the answer', () => {
  it('renders a `streaming` partial as the answer text (it used to be skipped)', () => {
    const turns = hydrateTurns([user('count to three'), streaming('one, two, ')], true)
    expect(turns.map((t) => t.role)).toEqual(['user', 'assistant'])
    expect(turns[1].segments).toEqual([{ kind: 'text', text: 'one, two, ' }])
    expect(turnText(turns[1])).toBe('one, two,')
  })

  it('keeps the partial the tail segment when a follow-up is queued behind it', () => {
    // The gateway grows ONE `streaming` entry in place, so a message queued mid-answer sits
    // after it — a row the transcript does not render.
    const turns = hydrateTurns([user('q'), streaming('one, two'), { role: 'queued', content: 'next' }], true)
    expect(turns[1].segments).toEqual([{ kind: 'text', text: 'one, two' }])
  })

  it('opens a fresh segment for text after a tool, as the live stream does', () => {
    const turns = hydrateTurns([
      user('q'),
      { role: 'assistant', content: 'Let me look.' },
      { role: 'tool', content: 'read_file', meta: { tool_call_id: 'c1', tool: 'read_file' } },
      streaming('It says'),
    ], true)
    expect(turns[1].segments.map((s) => s.kind)).toEqual(['text', 'tool', 'text'])
    expect(turns[1].segments[2]).toEqual({ kind: 'text', text: 'It says' })
  })
})

describe('livePartialOf — which text a resume continues', () => {
  it('is the streaming entry when no rendered row follows it', () => {
    expect(livePartialOf([user('q'), streaming('one, two')])).toBe('one, two')
    expect(livePartialOf([user('q'), streaming('one, two'), { role: 'queued', content: 'x' }])).toBe('one, two')
  })

  it('is null when the transcript does not END in one', () => {
    expect(livePartialOf([user('q'), { role: 'assistant', content: 'done' }])).toBeNull()
    expect(livePartialOf([user('q')])).toBeNull()
    // Behind a rendered row it is not the tail: the live text lands after that row, as it does
    // live, and continuing it in place would paint the partial a second time below the row.
    expect(livePartialOf([user('q'), streaming('before'), { role: 'error', content: 'boom' }])).toBeNull()
  })
})

describe('CoalescerCore — the chunk watermark', () => {
  it('drops a chunk stamped at or below the watermark and keeps the rest', () => {
    const c = new CoalescerCore()
    c.resume('one, two, ', 7)          // a snapshot showing every chunk stamped <= 7
    expect(c.push('two, ', 7)).toBe(false)   // already in the partial
    expect(c.push('three', 8)).toBe(true)
    expect(c.drainAll()).toBe('one, two, three')
  })

  it('counts the resumed partial as revealed, so it is not animated in again', () => {
    const c = new CoalescerCore()
    c.resume('already on screen', 3)
    expect(c.backlog()).toBe(0)
    expect(c.revealedText()).toBe('already on screen')
  })

  it('keeps the watermark across run boundaries — stamps are gateway-wide, not per run', () => {
    const c = new CoalescerCore()
    c.push('first run', 4)
    c.reset()                            // a boundary (tool call, fresh send)
    expect(c.push('first run', 4)).toBe(false)
    expect(c.push('second run', 5)).toBe(true)
  })

  it('takes the snapshot watermark even when it is LOWER — a late snapshot is replayed onto', () => {
    // A read that outlasted the hold lands after its frames were applied live (snapshotReplay.ts):
    // the transcript becomes that older snapshot, and the chunks it does not hold are applied
    // again on top of it, so they must be admitted again.
    const c = new CoalescerCore()
    c.push('one, ', 1); c.push('two, ', 2); c.push('three', 3)
    c.resume('one, ', 1)
    expect(c.push('two, ', 2)).toBe(true)
    expect(c.push('three', 3)).toBe(true)
    expect(c.drainAll()).toBe('one, two, three')
  })

  it('admits an unstamped chunk as-is', () => {
    const c = new CoalescerCore()
    c.resume(null, 10)
    expect(c.push('no seq')).toBe(true)
  })
})

describe('SnapshotReplay — a snapshot plus the frames after it', () => {
  const every = () => true

  it('holds frames only while a read is in flight, and hands them back in order', () => {
    const r = new SnapshotReplay<string>(every)
    expect(r.hold('before')).toBe(false)
    const gen = r.begin()
    expect(r.busy()).toBe(true)
    expect(r.hold('a')).toBe(true)
    expect(r.hold('b')).toBe(true)
    expect(r.settle(gen, () => true)).toEqual(['a', 'b'])
    expect(r.busy()).toBe(false)
    expect(r.hold('after')).toBe(false)
  })

  it('never lets a snapshot older than the one on screen replace it', () => {
    const r = new SnapshotReplay<string>(every)
    const older = r.begin()
    r.hold('x')
    const newer = r.begin()
    r.hold('y')
    // The newer read lands first and is adopted; the frames held so far replay onto it (`x`
    // arrived before it was issued, so its snapshot holds it — replay is idempotent).
    expect(r.settle(newer, () => true)).toEqual(['x', 'y'])
    expect(r.hold('z')).toBe(true)  // the older read still holds the stream
    const adopt = vi.fn(() => true)
    expect(r.settle(older, adopt)).toEqual(['z'])
    expect(adopt, 'the older snapshot was offered for adoption').not.toHaveBeenCalled()
  })

  it('lets an older read that lands first paint, then gives the newer one every frame since ITS issue', () => {
    const r = new SnapshotReplay<string>(every)
    const older = r.begin()
    r.hold('x')
    const newer = r.begin()
    r.hold('y')
    expect(r.settle(older, () => true)).toEqual(['x', 'y'])
    expect(r.hold('z')).toBe(true)
    // `y` is already on screen, but the newer snapshot paints over it: applied again.
    expect(r.settle(newer, () => true)).toEqual(['y', 'z'])
  })

  it('a failed read still hands back the frames it held', () => {
    const r = new SnapshotReplay<string>(every)
    const gen = r.begin()
    r.hold('kept')
    expect(r.settle(gen, null)).toEqual(['kept'])
  })

  it('a released read lets frames flow live, and its late adoption applies them again', () => {
    const r = new SnapshotReplay<string>((f) => f !== 'voice')
    const gen = r.begin()
    r.hold('c1')
    expect(r.release(gen), 'the held frames are handed back to be applied now').toEqual(['c1'])
    expect(r.hold('c2'), 'after the release, frames are applied live').toBe(false)
    expect(r.hold('voice')).toBe(false)
    expect(r.busy(), 'a released read is still in flight').toBe(true)
    // The snapshot paints over c1 and c2, so they go again on top; the voice chunk's effect is
    // outside the transcript and already happened.
    expect(r.settle(gen, () => true)).toEqual(['c1', 'c2'])
  })

  it('applies nothing twice when a released read is not adopted', () => {
    const r = new SnapshotReplay<string>(every)
    const gen = r.begin()
    r.hold('a')
    r.release(gen)
    r.hold('b')
    expect(r.settle(gen, () => false)).toEqual([])
  })

  it('releasing one read does not release another that still holds the stream', () => {
    const r = new SnapshotReplay<string>(every)
    const first = r.begin()
    r.hold('x')
    const second = r.begin()
    expect(r.release(first)).toEqual([])
    expect(r.hold('y')).toBe(true)
    expect(r.release(second)).toEqual(['x', 'y'])
    expect(r.release(second), 'a second release is a no-op').toEqual([])
  })
})

describe('snapshotPredatesSend — the remount read that beat its own send', () => {
  const seed = [user('hello', '2026-09-25T10:00:00.000Z')]

  it('is true when the snapshot does not hold the handed-off message yet', () => {
    expect(snapshotPredatesSend(seed, [])).toBe(true)
  })

  it('is false once it does — identity is the client stamp the gateway stores verbatim', () => {
    expect(snapshotPredatesSend(seed, [user('hello', '2026-09-25T10:00:00.000Z')])).toBe(false)
  })

  it('is false with no seed to compare against', () => {
    expect(snapshotPredatesSend(null, [])).toBe(false)
    expect(snapshotPredatesSend([{ role: 'user', content: 'no stamp' }], [])).toBe(false)
  })
})
