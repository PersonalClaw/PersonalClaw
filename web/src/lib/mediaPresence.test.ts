import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetAudioPresence, subscribeAudioPresence, type AudioPresence } from './audioPresence'
import { audiblyPlaying, resetMediaPresence, watchInAppMedia } from './mediaPresence'

// ── PersonalClaw's own media elements in the audio presence (#4776) ────────────────────────────
//
// While a map watches, an element is a source only while it AUDIBLY plays — not paused, ended,
// muted, silent or a display capture — owned by the chat turn it sits in; measured through a
// capture of its own output, never rerouted; forgotten when it ends, leaves the page or the watch
// stops.
//
// VACUITY FLOOR. Every exclusion sits beside the audible element that IS reported, on one setup.

/** A media element in a turn, its playing state set as a test says. */
function element(kind: 'audio' | 'video' = 'audio', coord: number | null = 7) {
  const turn = document.createElement('div')
  if (coord !== null) turn.setAttribute('data-turn-coord', String(coord))
  const el = document.createElement(kind)
  const state = { paused: true, ended: false, muted: false, volume: 1, srcObject: null as unknown }
  for (const key of Object.keys(state) as (keyof typeof state)[]) {
    Object.defineProperty(el, key, { configurable: true, get: () => state[key], set: (v) => { (state as Record<string, unknown>)[key] = v } })
  }
  turn.appendChild(el)
  document.body.appendChild(turn)
  const play = () => { state.paused = false; el.dispatchEvent(new Event('playing')) }
  return { el, turn, state, play }
}

const audio = { captures: 0, rerouted: 0 }
class FakeNode { connect() {} disconnect() {} }
class FakeContext {
  createMediaStreamSource() { return new FakeNode() }
  createMediaElementSource() { audio.rerouted++; return new FakeNode() }
  createAnalyser() { return Object.assign(new FakeNode(), { fftSize: 512, getFloatTimeDomainData: (b: Float32Array) => b.fill(0.3) }) }
  close() { return Promise.resolve() }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance'] })
  audio.captures = 0
  audio.rerouted = 0
  ;(window as unknown as { AudioContext: unknown }).AudioContext = FakeContext
  ;(HTMLMediaElement.prototype as unknown as { captureStream: () => unknown }).captureStream = function () {
    audio.captures++
    const track = { stop: () => {} }
    return { getAudioTracks: () => [track], getTracks: () => [track] }
  }
})
afterEach(() => {
  resetMediaPresence()
  resetAudioPresence()
  document.body.innerHTML = ''
  vi.useRealTimers()
  delete (window as unknown as { AudioContext?: unknown }).AudioContext
  delete (HTMLMediaElement.prototype as unknown as { captureStream?: unknown }).captureStream
})

function watch(levels = true) {
  const told: AudioPresence[] = []
  const stop = subscribeAudioPresence((p) => { told.push(p) }, { levels })
  return { last: () => told.at(-1)!, stop }
}

describe('in-app media in the audio presence', () => {
  it('🔑 an element audibly playing in a chat turn is a media source owned by that turn', () => {
    const seen = watch(false)
    const unwatch = watchInAppMedia()
    const a = element('audio', 7)
    expect(seen.last().media, 'a paused element was reported').toBeNull()
    a.play()
    expect(seen.last().media).toEqual({ level: 0, owner: 7 })
    a.state.paused = true
    a.el.dispatchEvent(new Event('pause'))
    expect(seen.last().media, 'a paused element still showed as playing').toBeNull()
    unwatch()
  })

  it('🔑 muted, silent and display-capture elements are not playing', () => {
    const seen = watch(false)
    const unwatch = watchInAppMedia()
    const muted = element('video', 1)
    muted.state.muted = true
    muted.play()
    const silent = element('audio', 2)
    silent.state.volume = 0
    silent.play()
    const shared = element('video', 3)
    class FakeStream { getVideoTracks() { return [{ getSettings: () => ({ displaySurface: 'monitor' }) }] } }
    ;(globalThis as unknown as { MediaStream: unknown }).MediaStream = FakeStream
    shared.state.srcObject = new FakeStream()
    shared.play()
    expect(seen.last().media, 'an inaudible element was reported as playing').toBeNull()
    expect([muted.el, silent.el, shared.el].map(audiblyPlaying)).toEqual([false, false, false])
    // The control: an audible one in the same page is.
    const heard = element('audio', 4)
    heard.play()
    expect(seen.last().media).toEqual({ level: 0, owner: 4 })
    delete (globalThis as unknown as { MediaStream?: unknown }).MediaStream
    unwatch()
  })

  it('🔑 measured through a capture of its own output, never rerouted, and only while a map wants levels', () => {
    const unwatch = watchInAppMedia()
    const a = element('audio', 5)
    a.play()
    expect(audio.captures, 'measured with no map wanting levels').toBe(0)
    const seen = watch(true)
    vi.advanceTimersByTime(50)
    expect(audio.captures).toBe(1)
    expect(audio.rerouted, 'the element’s sound was rerouted through the analyser').toBe(0)
    expect(seen.last().media!.level).toBeGreaterThan(0)
    seen.stop()
    unwatch()
  })

  it('🔑 forgotten when it leaves the page mid-play, though its pause fires outside the document', () => {
    const seen = watch(false)
    const unwatch = watchInAppMedia()
    const a = element('audio', 6)
    a.play()
    expect(seen.last().media).not.toBeNull()
    a.turn.remove()
    a.state.paused = true
    a.el.dispatchEvent(new Event('pause'))
    expect(seen.last().media, 'an element gone from the page still showed as playing').toBeNull()
    unwatch()
  })

  it('stops listening to the page when the last watcher leaves', () => {
    const seen = watch(false)
    const first = watchInAppMedia()
    const second = watchInAppMedia()
    first()
    const a = element('audio', 8)
    a.play()
    expect(seen.last().media, 'one watcher left while another stayed, and media went unheard').not.toBeNull()
    second()
    expect(seen.last().media, 'the watch ended and the element stayed reported').toBeNull()
    const b = element('audio', 9)
    b.play()
    expect(seen.last().media, 'with no watcher, a newly playing element was reported').toBeNull()
  })

  it('an element outside any chat turn plays with no owner', () => {
    const seen = watch(false)
    const unwatch = watchInAppMedia()
    element('video', null).play()
    expect(seen.last().media).toEqual({ level: 0, owner: null })
    unwatch()
  })
})
