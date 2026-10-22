import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, waitFor, cleanup } from '@testing-library/react'
import { useMicRecorder } from './useMicRecorder'
import { resetAudioPresence, subscribeAudioPresence, type AudioPresence } from '../../lib/audioPresence'

// ── The microphone in the audio presence is the stream the owner already opened (#4776) ───────
//
// The Session Map may react to the owner's voice only through a stream they opened for dictation
// or hands-free voice: no microphone is ever opened to decorate the rail. While the recorder holds
// its stream and a map subscribes, that SAME stream is measured; an ended or muted track is quiet
// at once; the recorder stopping takes the source down.
//
// VACUITY FLOOR. "no getUserMedia for decoration" passes for a recorder that never records, so the
// count is read beside a recording that DOES open one, and the analyser is asserted to be fed the
// recorder's own stream.

type Listener = () => void
function fakeTrack() {
  const listeners: Record<string, Listener[]> = {}
  return {
    kind: 'audio', readyState: 'live', enabled: true, muted: false, stop: vi.fn(),
    addEventListener: (type: string, fn: Listener) => { (listeners[type] ??= []).push(fn) },
    fire(type: string) { for (const fn of listeners[type] ?? []) fn() },
  }
}
let track: ReturnType<typeof fakeTrack>
let stream: { getTracks: () => unknown[]; getAudioTracks: () => unknown[] }
let gumCalls = 0
const wired: { stream: unknown; closed: number } = { stream: null, closed: 0 }

class FakeRecorder {
  state = 'inactive'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  constructor(public s: unknown) { recorders.push(this) }
  start() { this.state = 'recording' }
  stop() { this.state = 'inactive'; this.onstop?.() }
}
let recorders: FakeRecorder[] = []
class FakeNode { connect() {} disconnect() {} }
class FakeContext {
  createMediaStreamSource(s: unknown) { wired.stream = s; return new FakeNode() }
  createAnalyser() { return Object.assign(new FakeNode(), { fftSize: 512, getFloatTimeDomainData: (b: Float32Array) => b.fill(0.25) }) }
  close() { wired.closed++; return Promise.resolve() }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance'] })
  recorders = []
  gumCalls = 0
  wired.stream = null
  wired.closed = 0
  track = fakeTrack()
  stream = { getTracks: () => [track], getAudioTracks: () => [track] }
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn(async () => { gumCalls++; return stream }) },
  })
  ;(globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeRecorder
  ;(window as unknown as { AudioContext: unknown }).AudioContext = FakeContext
})
afterEach(() => {
  cleanup()
  resetAudioPresence()
  vi.useRealTimers()
  delete (window as unknown as { AudioContext?: unknown }).AudioContext
})

let toggle: () => void = () => {}
function Harness() {
  const mic = useMicRecorder(async () => '', undefined, () => {})
  toggle = mic.toggle
  return null
}

function watch() {
  const told: AudioPresence[] = []
  const stop = subscribeAudioPresence((p) => { told.push(p) })
  return { last: () => told.at(-1)!, stop }
}

describe('the microphone in the audio presence', () => {
  it('🔑 subscribing opens no microphone: only a recording the owner started is heard', async () => {
    const seen = watch()
    render(<Harness />)
    act(() => { vi.advanceTimersByTime(500) })
    expect(gumCalls, 'the map opened a microphone to decorate itself').toBe(0)
    expect(seen.last().microphone).toBeNull()
    await act(async () => { toggle() })
    await waitFor(() => expect(recorders).toHaveLength(1))
    expect(gumCalls, 'the recording itself opened one stream').toBe(1)
    act(() => { vi.advanceTimersByTime(50) })
    // The analyser hears the recorder's OWN stream, and nothing more was opened for it.
    expect(wired.stream).toBe(stream)
    expect(gumCalls).toBe(1)
    expect(seen.last().microphone!.level).toBeGreaterThan(0)
  })

  it('🔑 an ended or muted track is quiet at once', async () => {
    const seen = watch()
    render(<Harness />)
    await act(async () => { toggle() })
    await waitFor(() => expect(recorders).toHaveLength(1))
    act(() => { vi.advanceTimersByTime(50) })
    expect(seen.last().microphone).not.toBeNull()
    track.muted = true
    act(() => { track.fire('mute') })
    expect(seen.last().microphone, 'a muted track still showed as listening').toBeNull()
    track.muted = false
    act(() => { track.fire('unmute') })
    expect(seen.last().microphone).not.toBeNull()
    track.readyState = 'ended'
    act(() => { track.fire('ended') })
    expect(seen.last().microphone, 'an ended track still showed as listening').toBeNull()
  })

  it('the recorder stopping takes the source down, and its measuring with it', async () => {
    const seen = watch()
    render(<Harness />)
    await act(async () => { toggle() })
    await waitFor(() => expect(recorders).toHaveLength(1))
    act(() => { vi.advanceTimersByTime(50) })
    await act(async () => { recorders[0].stop() })
    expect(seen.last().microphone).toBeNull()
    expect(wired.closed, 'the analyser’s context outlived the recording').toBe(1)
  })

  it('with no map subscribed, a recording is never measured', async () => {
    render(<Harness />)
    await act(async () => { toggle() })
    await waitFor(() => expect(recorders).toHaveLength(1))
    act(() => { vi.advanceTimersByTime(200) })
    expect(wired.stream, 'a recording was measured with no map listening').toBeNull()
  })
})
