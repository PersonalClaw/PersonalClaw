import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  LEVEL_STEPS, STALE_MS, quantize, registerAudioSource, resetAudioPresence, subscribeAudioPresence,
  type AttachedSource, type AudioPresence,
} from './audioPresence'

// ── The audio presence controller (#4776) ──────────────────────────────────────────────────────
//
// A source registers without being measured; a subscriber's arrival attaches it, the last one's
// departure detaches it; a frame loop runs only while someone subscribes AND a source is live;
// only a quantized level leaves; a level not refreshed for 250 ms is cleared, and a source that
// goes quiet clears at once.
//
// VACUITY FLOOR. "no frame while idle" passes against a loop that never runs, so each idle claim
// sits beside the live case that DOES run frames, counted by the same spy.

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance', 'setTimeout', 'Date'] })
})
afterEach(() => {
  resetAudioPresence()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

/** A source whose level a test sets, recording what the controller did to it. */
function fakeSource() {
  const calls = { attach: 0, detach: 0, reads: 0 }
  let level: number | null = 0.5
  const attach = (): AttachedSource => {
    calls.attach++
    return {
      read: () => { calls.reads++; return level },
      detach: () => { calls.detach++ },
    }
  }
  return { calls, attach, set: (l: number | null) => { level = l } }
}

const frames = () => vi.spyOn(window, 'requestAnimationFrame')
const advance = (ms: number) => vi.advanceTimersByTime(ms)

describe('nothing is measured until the rail asks', () => {
  it('🔑 a source is attached only while someone subscribes, and detached when the last leaves', () => {
    const src = fakeSource()
    const handle = registerAudioSource('playback', src.attach)
    handle.setLive(true)
    advance(100)
    expect(src.calls.attach, 'a source was measured with no one subscribed').toBe(0)
    expect(src.calls.reads).toBe(0)
    const first = subscribeAudioPresence(() => {})
    const second = subscribeAudioPresence(() => {})
    expect(src.calls.attach).toBe(1)
    first()
    expect(src.calls.detach, 'one subscriber left while another stayed').toBe(0)
    second()
    expect(src.calls.detach, 'the last subscriber left and the source stayed attached').toBe(1)
    const reads = src.calls.reads
    advance(500)
    expect(src.calls.reads, 'a detached source was still read').toBe(reads)
  })

  it('a source registered while the rail subscribes is attached at once; unregistering detaches it', () => {
    const stop = subscribeAudioPresence(() => {})
    const src = fakeSource()
    const handle = registerAudioSource('microphone', src.attach)
    expect(src.calls.attach).toBe(1)
    handle.unregister()
    expect(src.calls.detach).toBe(1)
    stop()
  })
})

describe('the frame loop runs only while it has something to show', () => {
  it('🔑 no frame while idle, subscribed or not; frames while a subscribed source is live; none after', () => {
    const spy = frames()
    const src = fakeSource()
    const handle = registerAudioSource('playback', src.attach)
    const stop = subscribeAudioPresence(() => {})
    advance(500)
    expect(spy, 'frames ran with no live source').not.toHaveBeenCalled()
    handle.setLive(true)
    advance(200)
    const live = spy.mock.calls.length
    expect(live, 'a live source ran no frames — the idle claims above would be vacuous').toBeGreaterThan(3)
    handle.setLive(false)
    advance(500)
    expect(spy.mock.calls.length, 'frames kept running after the source went quiet').toBeLessThanOrEqual(live + 1)
    const quiet = spy.mock.calls.length
    handle.setLive(true)
    stop()
    advance(500)
    expect(spy.mock.calls.length, 'frames ran with no one subscribed').toBeLessThanOrEqual(quiet + 1)
  })
})

describe('what leaves the capture path', () => {
  it('🔑 only a quantized level per kind, and whose a playback is — nothing else', () => {
    const told: AudioPresence[] = []
    const src = fakeSource()
    const handle = registerAudioSource('playback', src.attach)
    handle.setOwner(6)
    subscribeAudioPresence((p) => { told.push(p) })
    handle.setLive(true)
    src.set(0.53)
    advance(50)
    const last = told.at(-1)!
    expect(last).toEqual({ playback: { level: quantize(0.53), owner: 6 }, media: null, microphone: null })
    expect(Object.keys(last.playback!).sort()).toEqual(['level', 'owner'])
    expect(last.playback!.level * LEVEL_STEPS % 1).toBe(0)
    expect(quantize(7)).toBe(1)
    expect(quantize(-1)).toBe(0)
    expect(quantize(Number.NaN)).toBe(0)
  })

  it('tells a subscriber only when the presence changes, not every frame', () => {
    const sink = vi.fn()
    const src = fakeSource()
    const handle = registerAudioSource('microphone', src.attach)
    subscribeAudioPresence(sink)
    handle.setLive(true)
    src.set(0.5)
    advance(300)
    const calls = sink.mock.calls.length
    advance(300)
    expect(sink.mock.calls.length, 'an unchanged level was told again').toBe(calls)
  })
})

describe('stale and quiet levels clear', () => {
  it(`🔑 a level not refreshed for ${STALE_MS} ms is cleared`, () => {
    const told: AudioPresence[] = []
    const src = fakeSource()
    const handle = registerAudioSource('microphone', src.attach)
    subscribeAudioPresence((p) => { told.push(p) })
    handle.setLive(true)
    src.set(0.75)
    advance(50)
    expect(told.at(-1)!.microphone!.level).toBe(0.75)
    // The analyser stops answering: still "live", but nothing heard.
    src.set(null)
    advance(STALE_MS - 50)
    expect(told.at(-1)!.microphone!.level, 'cleared before it went stale').toBe(0.75)
    advance(100)
    expect(told.at(-1)!.microphone!.level, 'a stale level stayed lit').toBe(0)
  })

  it('🔑 a source that goes quiet (an ended reply, a muted microphone) clears at once', () => {
    const told: AudioPresence[] = []
    const src = fakeSource()
    const handle = registerAudioSource('playback', src.attach)
    subscribeAudioPresence((p) => { told.push(p) })
    handle.setLive(true)
    advance(50)
    expect(told.at(-1)!.playback).not.toBeNull()
    handle.setLive(false)
    expect(told.at(-1)!.playback, 'a quiet source was still reported').toBeNull()
  })

  it('a source that throws reads as nothing heard, and the loop survives it', () => {
    const told: AudioPresence[] = []
    const handle = registerAudioSource('playback', () => ({ read: () => { throw new Error('gone') }, detach: () => {} }))
    subscribeAudioPresence((p) => { told.push(p) })
    handle.setLive(true)
    advance(STALE_MS + 50)
    expect(told.at(-1)!.playback!.level).toBe(0)
  })
})

describe('a subscriber that wants no levels', () => {
  it('🔑 is told what is live and whose, at level 0, and measures nothing and runs no frame', () => {
    const spy = frames()
    const src = fakeSource()
    const handle = registerAudioSource('playback', src.attach)
    const told: AudioPresence[] = []
    const stop = subscribeAudioPresence((p) => { told.push(p) }, { levels: false })
    handle.setOwner(4)
    handle.setLive(true)
    advance(500)
    expect(told.at(-1)).toEqual({ playback: { level: 0, owner: 4 }, media: null, microphone: null })
    expect(src.calls.attach, 'a state-only subscriber attached an analyser').toBe(0)
    expect(spy, 'a state-only subscriber ran frames').not.toHaveBeenCalled()
    handle.setLive(false)
    expect(told.at(-1)!.playback).toBeNull()
    stop()
  })
})
