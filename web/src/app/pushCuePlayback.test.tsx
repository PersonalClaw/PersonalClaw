/**
 * PUSH CUE PLAYBACK (MOBILE-COMPANION MC-6).
 *
 * A service worker cannot play audio, so it posts the per-kind VOICE to an open client and this
 * is where it lands. The contract proven here: a delivered voice reaches `playCue` (so it rides
 * every suppressor), the voice is used as its own anchor point when it is a cue point, an unknown
 * voice is dropped rather than played as a fallback tone, and the disposer really detaches.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const playCue = vi.fn()
// Mock the design module: only the runtime members `installPushCuePlayback` touches are needed
// (the types are erased). `CUES`/`CUE_POINTS` mirror the real closed sets so validation behaves.
vi.mock('../design/soundCues', () => ({
  playCue: (...args: unknown[]) => playCue(...args),
  CUES: { turn_complete: {}, approval_needed: {}, error: {}, coin_blip: {}, terminal_bell: {} },
  CUE_POINTS: ['turn_complete', 'approval_needed', 'error'],
}))

import { installPushCuePlayback } from './pushCuePlayback'
import { PUSH_CUE_MESSAGE } from './pushPolicy'

beforeEach(() => {
  playCue.mockClear()
  // jsdom has no navigator.serviceWorker; a bare EventTarget is enough to add a listener and
  // dispatch a 'message' at it.
  Object.defineProperty(navigator, 'serviceWorker', {
    value: new EventTarget(),
    configurable: true,
  })
})

function post(data: unknown): void {
  navigator.serviceWorker.dispatchEvent(new MessageEvent('message', { data }))
}

describe('installPushCuePlayback', () => {
  it('plays a delivered voice through the gated playCue', () => {
    const off = installPushCuePlayback()
    post({ type: PUSH_CUE_MESSAGE, cue: 'coin_blip' })
    // coin_blip is not itself a cue point, so it rides the neutral anchor.
    expect(playCue).toHaveBeenCalledWith('turn_complete', 'coin_blip')
    off()
  })

  it('uses a voice that IS a cue point as its own anchor', () => {
    const off = installPushCuePlayback()
    post({ type: PUSH_CUE_MESSAGE, cue: 'error' })
    expect(playCue).toHaveBeenCalledWith('error', 'error')
    off()
  })

  it('ignores an unknown voice and any unrelated message', () => {
    const off = installPushCuePlayback()
    post({ type: PUSH_CUE_MESSAGE, cue: 'ka-ching' })
    post({ type: 'something-else', cue: 'coin_blip' })
    post({ cue: 'coin_blip' })
    post(null)
    expect(playCue).not.toHaveBeenCalled()
    off()
  })

  it('the disposer detaches the listener', () => {
    const off = installPushCuePlayback()
    off()
    post({ type: PUSH_CUE_MESSAGE, cue: 'coin_blip' })
    expect(playCue).not.toHaveBeenCalled()
  })
})
