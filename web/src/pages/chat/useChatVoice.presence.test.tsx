import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import { userTurn, assistantTurn, markCoordOf, type ChatTurn } from './chatTypes'
import { resetAudioPresence, subscribeAudioPresence, type AudioPresence } from '../../lib/audioPresence'

// ── A spoken reply is an audio-presence source (#4776) ─────────────────────────────────────────
//
// Live only from the moment its audio really starts to the moment the last of it ends, is
// stopped, fails or the chat goes; owned by the reply being read; measured only while a Session
// Map subscribes. Before this, "speaking" began at the Speak press, seconds before a sound.

const h = vi.hoisted(() => ({
  voiceSynthesize: vi.fn(),
  voice: { voiceCfg: { confirmation_phrases: ['send it'], exit_phrases: ['never mind'], duplex_mute_enabled: true }, speakReplies: false },
}))
vi.mock('../../lib/api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../lib/api')>()
  return { ...real, api: { ...real.api, voiceSynthesize: h.voiceSynthesize } }
})
vi.mock('./voiceConfig', () => ({ useVoiceConfig: () => h.voice }))

/** A Web Audio stand-in: enough of a context for a reading to be decoded, scheduled and ended. */
const audio = { analysers: 0, sources: [] as { onended: (() => void) | null; startAt: number }[] }
class FakeNode { connect() {} disconnect() {} }
class FakeContext {
  state = 'running'
  currentTime = 10
  destination = new FakeNode()
  resume() { return Promise.resolve() }
  createGain() { return new FakeNode() }
  createAnalyser() {
    audio.analysers++
    return Object.assign(new FakeNode(), { fftSize: 512, getFloatTimeDomainData: (b: Float32Array) => b.fill(0.2) })
  }
  createBufferSource() {
    const src = Object.assign(new FakeNode(), { buffer: null as unknown, onended: null as (() => void) | null, startAt: 0,
      start(at: number) { this.startAt = at }, stop() { this.onended?.() } })
    audio.sources.push(src)
    return src
  }
  decodeAudioData() { return Promise.resolve({ duration: 2 }) }
}

let mod: typeof import('./useChatVoice')
const TURNS: ChatTurn[] = [userTurn('Read me the summary.'), assistantTurn('The feed refetches.')]
const OWNER = markCoordOf(TURNS[1], 1)

function mount() {
  const notice = { notice: null, showError: vi.fn(), showInfo: vi.fn(), clear: vi.fn() }
  return renderHook(() => mod.useChatVoice({ sessionRef: { current: 'chat-7-voice' }, turns: TURNS, notice }))
}

beforeEach(async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'requestAnimationFrame', 'cancelAnimationFrame', 'performance'] })
  audio.analysers = 0
  audio.sources = []
  ;(window as unknown as { AudioContext: unknown }).AudioContext = FakeContext
  h.voiceSynthesize.mockReset().mockResolvedValue({})
  mod = await import('./useChatVoice')
})
afterEach(() => {
  cleanup()
  resetAudioPresence()
  vi.useRealTimers()
  delete (window as unknown as { AudioContext?: unknown }).AudioContext
})

/** Watch the presence as the rail would. */
function watch(levels = true) {
  const told: AudioPresence[] = []
  const stop = subscribeAudioPresence((p) => { told.push(p) }, { levels })
  return { last: () => told.at(-1)!, stop }
}

const chunk = btoa('RIFF')

describe('a spoken reply in the audio presence', () => {
  it('🔑 is not live at the Speak press — only once its audio starts — and is owned by the reply', async () => {
    const seen = watch(false)
    const { result } = mount()
    await act(async () => { await result.current.speak('The feed refetches.', 1) })
    expect(seen.last().playback, 'Speak was pressed and no sound has played yet').toBeNull()
    await act(async () => { await result.current.enqueueAudio(chunk) })
    act(() => { vi.advanceTimersByTime(1) })
    expect(seen.last().playback).toEqual({ level: 0, owner: OWNER })
  })

  it('🔑 goes quiet when the last of it ends, when it is stopped, and when the chat goes', async () => {
    const seen = watch(false)
    const { result, unmount } = mount()
    await act(async () => { await result.current.speak('The feed refetches.', 1) })
    await act(async () => { await result.current.enqueueAudio(chunk) })
    act(() => { vi.advanceTimersByTime(1) })
    act(() => { audio.sources[0].onended?.() })
    expect(seen.last().playback, 'the reading ended and still showed as playing').toBeNull()
    // Again, and stopped by a second Speak on the same reply.
    await act(async () => { await result.current.speak('The feed refetches.', 1) })
    await act(async () => { await result.current.enqueueAudio(chunk) })
    act(() => { vi.advanceTimersByTime(1) })
    expect(seen.last().playback).not.toBeNull()
    act(() => { void result.current.speak('The feed refetches.', 1) })
    expect(seen.last().playback, 'a stopped reading still showed as playing').toBeNull()
    // And a reading cut off by the chat closing.
    await act(async () => { await result.current.speak('The feed refetches.', 1) })
    await act(async () => { await result.current.enqueueAudio(chunk) })
    act(() => { vi.advanceTimersByTime(1) })
    unmount()
    expect(seen.last().playback, 'the chat went and its reading still showed as playing').toBeNull()
  })

  it('a reading that never plays (the synthesis is refused) is never live', async () => {
    h.voiceSynthesize.mockRejectedValue(new Error('no model'))
    const seen = watch(false)
    const { result } = mount()
    await act(async () => { await result.current.speak('The feed refetches.', 1) })
    act(() => { vi.advanceTimersByTime(100) })
    expect(seen.last().playback).toBeNull()
  })

  it('🔑 is measured only while a Session Map subscribes for levels', async () => {
    const { result } = mount()
    await act(async () => { await result.current.speak('The feed refetches.', 1) })
    await act(async () => { await result.current.enqueueAudio(chunk) })
    act(() => { vi.advanceTimersByTime(100) })
    expect(audio.analysers, 'an analyser was made with no rail listening').toBe(0)
    const seen = watch(true)
    act(() => { vi.advanceTimersByTime(100) })
    expect(audio.analysers).toBe(1)
    expect(seen.last().playback!.level, 'the level never reached the rail').toBeGreaterThan(0)
    seen.stop()
  })
})
