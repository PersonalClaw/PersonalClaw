import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act, waitFor, cleanup } from '@testing-library/react'
import { useMicRecorder } from './useMicRecorder'
import { DEFAULT_PHRASES } from './duplex'

/**
 * A retraction at the end of a hands-free chunk discards the dictation, however
 * speech-to-text spells it.
 *
 * Driven through the live loop with recorded transcript text: transcription wrote a spoken
 * "Never mind." as "Nevermind", so "send it, never mind" arrives as "Send it. Nevermind.",
 * one chunk holding both a confirmation and an exit. Exit wins, so nothing is sent and the
 * draft empties. The contrast, the same dictation confirmed with a plain "Send it.", must
 * send, or the first test would pass on a loop that never submits at all.
 *
 * jsdom has neither getUserMedia nor MediaRecorder; both are faked at the boundary, as in
 * micMuteDrain.test.tsx. Each segment is ended by hand (the hook's own 4 s timer does it in
 * the app), and the microphone refuses once the recorded transcripts run out, which ends the
 * loop instead of leaving a segment recording after the test.
 */

let recorders: FakeRecorder[] = []

class FakeRecorder {
  state = 'inactive'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  constructor() {
    recorders.push(this)
  }
  start() {
    this.state = 'recording'
  }
  stop() {
    if (this.state === 'inactive') return
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['speech'], { type: 'audio/webm' }) })
    this.onstop?.()
  }
}

function fakeMicrophone(segments: number) {
  let opened = 0
  const track = { kind: 'audio', stop: vi.fn() }
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: vi.fn(async () => {
        opened += 1
        if (opened > segments) throw Object.assign(new Error('no more'), { name: 'NotAllowedError' })
        return { getTracks: () => [track], getAudioTracks: () => [track] }
      }),
    },
  })
}

beforeEach(() => {
  recorders = []
  ;(globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeRecorder
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** Say each recorded transcript as one hands-free segment; return what the loop did. */
async function dictate(transcripts: string[]) {
  fakeMicrophone(transcripts.length)
  const queue = [...transcripts]
  const onSubmit = vi.fn()
  const onBuffer = vi.fn()
  function Harness() {
    useMicRecorder(async () => queue.shift() ?? '', undefined, () => {}, {
      enabled: true,
      confirmationPhrases: DEFAULT_PHRASES.confirmation,
      exitPhrases: DEFAULT_PHRASES.exit,
      onSubmit,
      onBuffer,
    })
    return null
  }
  render(<Harness />)
  for (let n = 1; n <= transcripts.length; n++) {
    await waitFor(() => expect(recorders.length).toBe(n))
    await act(async () => {
      recorders[n - 1].stop()
    })
    await waitFor(() => expect(onBuffer).toHaveBeenCalledTimes(n))
  }
  return { onSubmit, onBuffer }
}

describe('hands-free: a retraction speech-to-text writes as one word', () => {
  it('"Send it. Nevermind." sends nothing and empties the draft', async () => {
    const { onSubmit, onBuffer } = await dictate(['Book the Tuesday ferry to the island', 'Send it. Nevermind.'])
    expect(onBuffer.mock.calls.map(([text]) => text)).toEqual(['Book the Tuesday ferry to the island', ''])
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('contrast: the same dictation confirmed with "Send it." is sent, without the trigger', async () => {
    const { onSubmit } = await dictate(['Book the Tuesday ferry to the island', 'Send it.'])
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith('Book the Tuesday ferry to the island')
  })
})
