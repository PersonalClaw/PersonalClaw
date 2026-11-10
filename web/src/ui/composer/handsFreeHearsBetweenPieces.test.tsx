import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, renderHook, act, waitFor, cleanup } from '@testing-library/react'
import { HANDS_FREE_SEGMENT_MS, useMicRecorder } from './useMicRecorder'
import { DEFAULT_PHRASES } from './duplex'
import { deferred, FakeRecorder, installFakeMicrophone } from '../../test/fakeMicrophone'

// ── Hands-free hears you between pieces ─────────────────────────────────────────────────────
//
// Hands-free records in short pieces, because a recording can only be read from its own start.
// The microphone stays open for the whole mode: the next piece starts the moment one ends, while
// the last is still being transcribed, and the transcripts are folded in the order the pieces were
// recorded, whatever order they come back in.

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

function Harness({ enabled, onTranscribe, onBuffer }: {
  enabled: boolean
  onTranscribe: (blob: Blob, opts?: { duplex?: boolean }) => Promise<string>
  onBuffer: (text: string) => void
}) {
  useMicRecorder(onTranscribe, undefined, () => {}, {
    enabled,
    confirmationPhrases: DEFAULT_PHRASES.confirmation,
    exitPhrases: DEFAULT_PHRASES.exit,
    onSubmit: () => {},
    onBuffer,
  })
  return null
}

describe('hands-free hears you between pieces', () => {
  it('opens the microphone once for the whole mode, and releases it when the mode ends', async () => {
    const mic = installFakeMicrophone()
    const onBuffer = vi.fn()
    const said = ['book the ferry', 'for Tuesday', 'at noon']
    const onTranscribe = vi.fn(async () => said.shift() ?? '')
    const { rerender } = render(<Harness enabled onTranscribe={onTranscribe} onBuffer={onBuffer} />)
    for (let n = 1; n <= 3; n++) {
      await waitFor(() => expect(mic.recorders.length).toBe(n))
      await act(async () => { mic.recorders[n - 1].stop() })
      await waitFor(() => expect(onBuffer).toHaveBeenCalledTimes(n))
    }
    expect(onBuffer).toHaveBeenLastCalledWith('book the ferry for Tuesday at noon')
    expect(mic.getUserMedia).toHaveBeenCalledTimes(1)
    expect(mic.tracks[0].stop).not.toHaveBeenCalled()
    await act(async () => { rerender(<Harness enabled={false} onTranscribe={onTranscribe} onBuffer={onBuffer} />) })
    expect(mic.tracks[0].stop).toHaveBeenCalled()
    expect(mic.live()).toBeUndefined()
  })

  it('records the next piece while the last is still being transcribed', async () => {
    const mic = installFakeMicrophone()
    const first = deferred<string>()
    const onTranscribe = vi.fn(() => first.promise)
    render(<Harness enabled onTranscribe={onTranscribe} onBuffer={() => {}} />)
    await waitFor(() => expect(mic.recorders.length).toBe(1))
    await act(async () => { mic.recorders[0].stop() })
    // The first piece is still being transcribed, and the second is already recording.
    await waitFor(() => expect(onTranscribe).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(mic.recorders.length).toBe(2), { timeout: 500 })
    expect(mic.recorders[1].state).toBe('recording')
    // On the same microphone: the mode never closed it.
    expect(mic.recorders[1].stream).toBe(mic.recorders[0].stream)
    expect(mic.getUserMedia).toHaveBeenCalledTimes(1)
    await act(async () => { first.resolve('') })
  })

  it('folds the pieces in the order they were recorded, when their transcripts come back out of order', async () => {
    const mic = installFakeMicrophone()
    const answers = [deferred<string>(), deferred<string>()]
    let call = 0
    const onTranscribe = vi.fn(() => answers[call++].promise)
    const onBuffer = vi.fn()
    render(<Harness enabled onTranscribe={onTranscribe} onBuffer={onBuffer} />)
    await waitFor(() => expect(mic.recorders.length).toBe(1))
    await act(async () => { mic.recorders[0].stop() })
    await waitFor(() => expect(mic.recorders.length).toBe(2))
    await act(async () => { mic.recorders[1].stop() })
    await waitFor(() => expect(onTranscribe).toHaveBeenCalledTimes(2))
    // The second piece's transcript comes back first.
    await act(async () => { answers[1].resolve('the Tuesday ferry') })
    expect(onBuffer).not.toHaveBeenCalled()
    await act(async () => { answers[0].resolve('book') })
    await waitFor(() => expect(onBuffer).toHaveBeenCalledTimes(2))
    expect(onBuffer.mock.calls.map(([t]) => t)).toEqual(['book', 'book the Tuesday ferry'])
  })
})

describe('hands-free keeps time, and lets go when the page does', () => {
  it('ends each piece on its timer and starts the next on the same microphone, and unmounting releases it', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const mic = installFakeMicrophone()
    const onTranscribe = vi.fn(async () => '')
    const { unmount } = render(<Harness enabled onTranscribe={onTranscribe} onBuffer={() => {}} />)
    await act(async () => {})
    expect(mic.recorders).toHaveLength(1)
    await act(async () => { vi.advanceTimersByTime(HANDS_FREE_SEGMENT_MS) })
    expect(mic.recorders[0].state).toBe('inactive')
    expect(mic.recorders[1].state).toBe('recording')
    expect(mic.recorders[1].stream).toBe(mic.recorders[0].stream)
    expect(mic.getUserMedia).toHaveBeenCalledTimes(1)
    unmount()
    await act(async () => { vi.advanceTimersByTime(HANDS_FREE_SEGMENT_MS * 3) })
    expect(mic.recorders).toHaveLength(2)
    expect(mic.getUserMedia).toHaveBeenCalledTimes(1)
    expect(mic.tracks[0].stop).toHaveBeenCalled()
    expect(mic.live()).toBeUndefined()
  })
})

describe('a press while the microphone opens', () => {
  it('opens one recording for two presses before the microphone answers', async () => {
    const opening = deferred<void>()
    const mic = installFakeMicrophone({ opening: opening.promise })
    const { result } = renderHook(() => useMicRecorder(async () => ''))
    act(() => { result.current.toggle(); result.current.toggle() })
    await act(async () => { opening.resolve() })
    await waitFor(() => expect(mic.recorders).toHaveLength(1))
    expect(mic.getUserMedia).toHaveBeenCalledTimes(1)
  })

  it('releases a microphone that answers after the page has gone', async () => {
    const opening = deferred<void>()
    const mic = installFakeMicrophone({ opening: opening.promise })
    const { result, unmount } = renderHook(() => useMicRecorder(async () => ''))
    act(() => { result.current.toggle() })
    unmount()
    await act(async () => { opening.resolve() })
    await waitFor(() => expect(mic.tracks).toHaveLength(1))
    expect(mic.tracks[0].stop).toHaveBeenCalled()
    expect(mic.recorders).toHaveLength(0)
  })

  it('releases the microphone it opened when hands-free takes it first', async () => {
    const opening = deferred<void>()
    const mic = installFakeMicrophone({ opening: opening.promise })
    const { result, rerender } = renderHook(({ on }) => useMicRecorder(async () => '', undefined, undefined, {
      enabled: on,
      confirmationPhrases: DEFAULT_PHRASES.confirmation,
      exitPhrases: DEFAULT_PHRASES.exit,
      onSubmit: () => {},
    }), { initialProps: { on: false } })
    // A dictation press waits on the permission prompt, and hands-free is turned on meanwhile.
    act(() => { result.current.toggle() })
    rerender({ on: true })
    await act(async () => { opening.resolve() })
    await waitFor(() => expect(mic.recorders).toHaveLength(1))
    expect(mic.tracks[0].stop).toHaveBeenCalled()
    expect(mic.recorders[0].stream.getTracks()[0]).toBe(mic.tracks[1])
  })
})

describe('a recorder that cannot start', () => {
  it('releases the microphone, says why, and leaves the next press free to try again', async () => {
    const mic = installFakeMicrophone()
    vi.spyOn(FakeRecorder.prototype, 'start').mockImplementationOnce(() => {
      throw Object.assign(new Error('cannot record'), { name: 'NotSupportedError' })
    })
    const errors: string[] = []
    const { result } = renderHook(() => useMicRecorder(async () => '', undefined, (m) => errors.push(m)))
    await act(async () => { result.current.toggle() })
    expect(errors).toHaveLength(1)
    expect(mic.tracks[0].stop).toHaveBeenCalled()
    await act(async () => { result.current.toggle() })
    expect(mic.getUserMedia).toHaveBeenCalledTimes(2)
    expect(mic.live()).toBeDefined()
  })
})
