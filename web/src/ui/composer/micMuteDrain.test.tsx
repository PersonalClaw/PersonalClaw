import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, waitFor, cleanup } from '@testing-library/react'
import { useMicRecorder } from './useMicRecorder'

/**
 * MULTIMODAL-IO §4.2 — mute-during-playback drains the mic buffer.
 *
 * The property: while a spoken reply is playing (`handsFree.muted` true), the in-flight
 * hands-free segment is not merely paused — its captured audio, INCLUDING the final-flush
 * tail that `MediaRecorder.stop()` delivers, is thrown away and never transcribed, so the
 * assistant's own voice cannot loop back in as input. That is the whole point of the
 * `drain()` half of §4.2 (`useMicRecorder.ts` drain + the `discarded` branch of `onstop` +
 * the mute effect): without it the queued speech is merely delayed, then arrives a moment
 * later as a spurious user turn.
 *
 * jsdom implements neither `getUserMedia` nor `MediaRecorder`, so both are faked at the
 * boundary (the same technique as `pushToTalkCapture.test.tsx`). What a fake can prove
 * honestly is the CONTRAST: `onTranscribe` is NOT called for a segment drained by mute,
 * while it IS called — with the very same captured bytes — for a segment that stops
 * normally. Asserting only the "not called" half would pass vacuously on a hook that never
 * transcribed at all, so the second test is load-bearing, not decoration.
 */

function fakeTrack() {
  return { kind: 'audio', readyState: 'live', stop: vi.fn(), addEventListener: vi.fn() }
}

let tracks: ReturnType<typeof fakeTrack>[] = []
let recorders: FakeRecorder[] = []
let gumCalls = 0

/** MediaRecorder, faithful in the one behaviour that matters here: `stop()` delivers a
 *  FINAL `ondataavailable` (the tail) before `onstop`. The drain must discard even that
 *  tail, so the fake has to produce it or the test would not exercise the risky path. */
class FakeRecorder {
  state = 'inactive'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  tail = 'tail-audio'
  constructor(public stream: { getTracks: () => ReturnType<typeof fakeTrack>[] }) {
    recorders.push(this)
  }
  start() { this.state = 'recording' }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob([this.tail], { type: 'audio/webm' }) })
    this.onstop?.()
  }
}

beforeEach(() => {
  tracks = []
  recorders = []
  gumCalls = 0
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      // First segment opens a real (fake) stream; any loop continuation fails cleanly so a
      // test never leaves an indefinitely-recording second segment behind.
      getUserMedia: vi.fn(async () => {
        gumCalls += 1
        if (gumCalls > 1) throw Object.assign(new Error('no more'), { name: 'NotAllowedError' })
        const t = [fakeTrack()]
        tracks.push(...t)
        return { getTracks: () => t, getAudioTracks: () => t }
      }),
    },
  })
  ;(globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeRecorder
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function Harness({
  muted,
  onTranscribe,
}: {
  muted: boolean
  onTranscribe: (blob: Blob, opts?: { duplex?: boolean }) => Promise<string>
}) {
  useMicRecorder(onTranscribe, undefined, () => {}, {
    enabled: true,
    muted,
    confirmationPhrases: ['send it'],
    exitPhrases: ['stop listening'],
    onSubmit: () => {},
  })
  return null
}

describe('mute-during-playback drains the mic buffer (MULTIMODAL-IO §4.2)', () => {
  it('a segment muted mid-capture is discarded — onTranscribe never runs, the track is released', async () => {
    const onTranscribe = vi.fn(async () => 'assistant echo')
    const { rerender } = render(<Harness muted={false} onTranscribe={onTranscribe} />)

    // Hands-free opened its first segment.
    await waitFor(() => expect(recorders.length).toBe(1))
    expect(tracks.length).toBe(1)

    // Audio was captured mid-segment.
    act(() => {
      recorders[0].ondataavailable?.({ data: new Blob(['body-audio'], { type: 'audio/webm' }) })
    })

    // A spoken reply starts playing → the host flips muted true.
    await act(async () => {
      rerender(<Harness muted={true} onTranscribe={onTranscribe} />)
    })

    // The drain stopped the recorder and released the microphone track...
    await waitFor(() => expect(tracks[0].stop).toHaveBeenCalled())
    // ...and the captured audio — body AND the stop() tail flush — was thrown away, never
    // sent to be transcribed. This is the assistant-voice-cannot-re-enter guarantee.
    expect(onTranscribe).not.toHaveBeenCalled()
    // Muted, the loop does not open a replacement segment.
    expect(recorders.length).toBe(1)
  })

  it('contrast: a segment that stops normally IS transcribed (keeps the test honest)', async () => {
    const onTranscribe = vi.fn(async (_blob: Blob, _opts?: { duplex?: boolean }) => 'hello there')
    render(<Harness muted={false} onTranscribe={onTranscribe} />)

    await waitFor(() => expect(recorders.length).toBe(1))
    act(() => {
      recorders[0].ondataavailable?.({ data: new Blob(['body-audio'], { type: 'audio/webm' }) })
    })

    // A normal segment end (NOT a drain): the recorder stops on its own.
    await act(async () => { recorders[0].stop() })

    await waitFor(() => expect(onTranscribe).toHaveBeenCalledTimes(1))
    const [blob, opts] = onTranscribe.mock.calls[0]
    expect(await blob.text()).toContain('body-audio')
    // Hands-free transcription is duplex-tagged so the STT path can apply the echo filter.
    expect(opts?.duplex).toBe(true)
  })
})
