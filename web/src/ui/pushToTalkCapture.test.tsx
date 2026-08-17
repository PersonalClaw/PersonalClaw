import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, act, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Composer } from './Composer'

/**
 * DESKTOP-CAPABILITIES S3 — push-to-talk capture, driven end to end through the real
 * `Composer` with the microphone and the desktop bridge faked at the boundary.
 *
 * ## What is real here and what is not
 *
 * REAL: the whole renderer path. A chord push travels through `usePushToTalk` into the
 * live `useMicRecorder`, opens a (fake) stream, records (fake) chunks, uploads through
 * the host's `onTranscribe`, and the transcript is inserted into the actual composer
 * input. The assertions below are made against the objects the production code touched,
 * not against a re-implementation of it.
 *
 * NOT REAL, and not claimed to be:
 *  - **The chord itself.** `globalShortcut` lives in the Electron main process. Nothing
 *    here presses a key on macOS; the push is injected at the bridge boundary, exactly
 *    as `preload.js` would deliver it. That the OS hands us the chord is V3's job and has
 *    not been performed.
 *  - **TCC.** The grant is a stub. A real permission dialog has never been raised by this
 *    code.
 *  - **A microphone.** jsdom implements neither `getUserMedia` nor `MediaRecorder`, so
 *    both are fakes. What IS therefore worth asserting is the thing a fake can prove
 *    honestly: that `stop()` is called on every track the code was handed.
 *
 * The privacy clause ("captures only while held/toggled") is checked as a claim about
 * TRACKS, never about a flag: a boolean flipping to false while a track stays live is the
 * precise defect the clause exists to prevent, and only the track spies can tell the
 * difference.
 */

// ── fakes ────────────────────────────────────────────────────────────────────────

vi.mock('../lib/api', () => ({
  api: {
    // The chord is read from config on mount. Kept minimal: this file is not testing
    // config plumbing, only that a chord gets bound.
    personalclawConfig: vi.fn(async () => ({ voice: { push_to_talk_chord: 'Alt+F13' } })),
  },
}))

/** A fake audio track whose `stop()` is observable — the whole privacy assertion. */
function fakeTrack() {
  return { kind: 'audio', readyState: 'live', stop: vi.fn(), addEventListener: vi.fn() }
}

let tracks: ReturnType<typeof fakeTrack>[] = []
let recorders: FakeRecorder[] = []

/** MediaRecorder, faithful in the one behaviour that matters here: `stop()` delivers a
 *  FINAL `ondataavailable` before `onstop`. That ordering is what carries the tail of a
 *  sentence spoken as the key comes up, so the fake must reproduce it or the tail test
 *  would be testing nothing. */
class FakeRecorder {
  state = 'inactive'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  /** Chunk handed over in the final flush, i.e. the tail. */
  tail = 'tail-audio'
  constructor(public stream: { getTracks: () => ReturnType<typeof fakeTrack>[] }) {
    recorders.push(this)
  }
  start() { this.state = 'recording' }
  stop() {
    this.state = 'inactive'
    // The final flush. A `stop()` that skipped this would silently drop the last words.
    this.ondataavailable?.({ data: new Blob([this.tail], { type: 'audio/webm' }) })
    this.onstop?.()
  }
}

/** The bridge as `preload.js` exposes it, with the push callback captured so a test can
 *  fire the chord. */
function installBridge() {
  let push: ((p: { action: string; reason?: string }) => void) | null = null
  const bridge = {
    capabilities: {
      names: () => ['audio_capture'],
      probe: vi.fn(async () => ({ available: true, granted: 'granted', requestable: false, reason: '' })),
      snapshot: vi.fn(async () => ({})),
      request: vi.fn(async () => ({ granted: true, state: 'granted', prompted: true, reason: '' })),
      on: vi.fn(() => () => {}),
    },
    pushToTalk: {
      bind: vi.fn(async (chord: string) => ({ ok: true, chord, conflict: false, reason: '' })),
      setCapturing: vi.fn(async () => true),
      on: (cb: (p: { action: string; reason?: string }) => void) => { push = cb; return () => { push = null } },
    },
  }
  ;(window as unknown as { pclawDesktop: unknown }).pclawDesktop = bridge
  return { bridge, fire: (p: { action: string; reason?: string }) => push?.(p) }
}

beforeEach(() => {
  tracks = []
  recorders = []
  // jsdom ships no matchMedia; the composer reads it for its mobile breakpoint.
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }),
  })
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: vi.fn(async () => {
        const t = [fakeTrack(), fakeTrack()]
        tracks.push(...t)
        return { getTracks: () => t, getAudioTracks: () => t }
      }),
    },
  })
  ;(globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeRecorder
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { pclawDesktop?: unknown }).pclawDesktop
  vi.clearAllMocks()
})

/** Mount a composer with a live host: `value` is held in a closure so an insertion is
 *  observable, and `onTranscribe` records the blob it was handed. */
function mountComposer(opts: { value?: string; transcript?: string } = {}) {
  const state = { value: opts.value ?? '' }
  const uploaded: Blob[] = []
  const errors: string[] = []
  const onTranscribe = vi.fn(async (blob: Blob) => {
    uploaded.push(blob)
    return opts.transcript ?? 'hello there'
  })
  const view = render(
    <Composer
      value={state.value}
      onChange={(v) => { state.value = v }}
      onSend={() => {}}
      onTranscribe={onTranscribe}
      onMicError={(m) => errors.push(m)}
    />,
  )
  return { state, uploaded, errors, onTranscribe, view }
}

/** Press the chord and let the async capture start settle. */
async function chord(fire: (p: { action: string }) => void) {
  await act(async () => { fire({ action: 'toggle' }) })
}

// ── the privacy claim ────────────────────────────────────────────────────────────

describe('capture stops with the gesture', () => {
  it('a second chord press stops EVERY track it opened', async () => {
    const { fire } = installBridge()
    mountComposer()

    await chord(fire)
    await waitFor(() => expect(tracks.length).toBe(2))
    // Precondition: the tracks really were opened, so the assertion below is not
    // vacuously passing over an empty list.
    expect(tracks.every((t) => t.stop.mock.calls.length === 0)).toBe(true)

    await chord(fire)

    // THE property. Not "a flag went false" — every track the code was handed must have
    // been stopped, because a live track outliving the gesture is the defect.
    await waitFor(() => {
      for (const t of tracks) expect(t.stop).toHaveBeenCalled()
    })
  })

  it('the shell’s runaway-capture stop also releases the tracks', async () => {
    const { fire } = installBridge()
    mountComposer()
    await chord(fire)
    await waitFor(() => expect(tracks.length).toBe(2))

    // The ceiling in desktop/pushToTalk.js sends this rather than assuming the renderer
    // stopped. If the renderer ignored it, a forgotten toggle would record forever.
    await act(async () => { fire({ action: 'stop', reason: 'capture-timeout' }) })

    await waitFor(() => {
      for (const t of tracks) expect(t.stop).toHaveBeenCalled()
    })
  })

  it('clicking the indicator stops the tracks too', async () => {
    const { fire } = installBridge()
    mountComposer()
    await chord(fire)
    const chip = await screen.findByRole('button', { name: /listening to your microphone/i })

    await act(async () => { chip.click() })

    // The visible indicator is also the off switch — one stop path, so noticing the chip
    // never means hunting for the control that clears it.
    await waitFor(() => {
      for (const t of tracks) expect(t.stop).toHaveBeenCalled()
    })
  })

  it('a press does not open a SECOND stream while one is live', async () => {
    const { fire } = installBridge()
    mountComposer()
    await chord(fire)
    await waitFor(() => expect(recorders.length).toBe(1))
    // Two owners of the microphone is how a track gets orphaned: the second start would
    // overwrite the reference the stop path uses.
    await chord(fire)
    await waitFor(() => expect(tracks[0].stop).toHaveBeenCalled())
    expect(recorders.length).toBe(1)
  })
})

// ── the indicator ────────────────────────────────────────────────────────────────

describe('the capturing indicator', () => {
  it('is absent at rest and present for as long as capture is live', async () => {
    const { fire } = installBridge()
    mountComposer()
    expect(screen.queryByRole('button', { name: /listening to your microphone/i })).toBeNull()

    await chord(fire)
    expect(await screen.findByRole('button', { name: /listening to your microphone/i })).toBeTruthy()

    await chord(fire)
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /listening to your microphone/i })).toBeNull())
  })

  it('does not depend on its animation', async () => {
    const { fire } = installBridge()
    mountComposer()
    await chord(fire)
    const chip = await screen.findByRole('button', { name: /listening to your microphone/i })

    // The pulse is decoration on the shared token. Strip every animated node and the
    // indicator must still be there, named and readable — the repo's standing rule that
    // an entrance must never gate content, applied where it matters most.
    chip.querySelectorAll('.status-pulse').forEach((n) => n.remove())
    expect(chip.getAttribute('aria-label')).toMatch(/listening/i)
    expect(chip.textContent).toMatch(/Listening/)
    expect(chip.isConnected).toBe(true)
  })

  it('reports the live state to the shell, which is what lights the menu bar', async () => {
    const { bridge, fire } = installBridge()
    mountComposer()
    await chord(fire)
    await waitFor(() => expect(bridge.pushToTalk.setCapturing).toHaveBeenCalledWith(true))
    await chord(fire)
    await waitFor(() => expect(bridge.pushToTalk.setCapturing).toHaveBeenCalledWith(false))
  })

  it('takes the shell indicator down when the composer unmounts mid-capture', async () => {
    const { bridge, fire } = installBridge()
    const { view } = mountComposer()
    await chord(fire)
    await waitFor(() => expect(bridge.pushToTalk.setCapturing).toHaveBeenCalledWith(true))

    bridge.pushToTalk.setCapturing.mockClear()
    await act(async () => { view.unmount() })

    // Navigating away kills the stream, so an indicator left lit in the menu bar would
    // outlive the capture it describes.
    expect(bridge.pushToTalk.setCapturing).toHaveBeenCalledWith(false)
  })
})

// ── the upload keeps the tail ────────────────────────────────────────────────────

describe('the transcript', () => {
  it('includes the audio flushed as the key came up', async () => {
    const { fire } = installBridge()
    const { uploaded } = mountComposer()

    await chord(fire)
    await waitFor(() => expect(recorders.length).toBe(1))
    // Mid-sentence audio, then the release. The recorder's final `dataavailable` (fired
    // from stop()) carries 'tail-audio'.
    act(() => { recorders[0].ondataavailable?.({ data: new Blob(['body-audio'], { type: 'audio/webm' }) }) })
    await chord(fire)

    await waitFor(() => expect(uploaded.length).toBe(1))
    const text = await uploaded[0].text()
    expect(text).toContain('body-audio')
    // THE tail property: a sentence finishing as the key is released must still reach
    // the endpoint. Dropping the final flush is the bug this pins.
    expect(text).toContain('tail-audio')
  })

  it('lands at the cursor rather than being appended, and keeps the draft', async () => {
    const { fire } = installBridge()
    // The discriminator: a freshly mounted input has its caret at offset 0, BEFORE the
    // seeded draft. So the two candidate implementations produce different strings and
    // the test can tell them apart:
    //
    //   insert-at-cursor (correct) → 'spoken wordsdraft '   (inserted at the caret)
    //   append (wrong)             → 'draft spoken words'   (concatenated at the end)
    //
    // Asserting the appended form would therefore have PASSED on an append
    // implementation and failed on the correct one, which is the trap this comment
    // exists to keep the next reader out of.
    const { state } = mountComposer({ value: 'draft ', transcript: 'spoken words' })

    await chord(fire)
    await waitFor(() => expect(recorders.length).toBe(1))
    await chord(fire)

    await waitFor(() => expect(state.value).toContain('spoken words'))
    // The draft is preserved in full — the transcript is inserted, never a replacement.
    expect(state.value).toContain('draft ')
    expect(state.value).toBe('spoken wordsdraft ')
    expect(state.value).not.toBe('draft spoken words')
  })

  it('routes the insertion through the composer’s caret API, not string concatenation', () => {
    // The behavioural test above pins WHERE the text lands for a caret at 0. This rail
    // pins the MECHANISM, which is what keeps the property true for a caret anywhere
    // else: `insertAtCaret` is CodeMirror's `replaceSelection`, so it also replaces a
    // selected range. A `value + text` rewrite would satisfy the caret-at-0 case only by
    // coincidence and silently break every other caret position — and jsdom cannot move
    // a CodeMirror caret for a test to observe that directly.
    const src = readFileSync(join(process.cwd(), 'src/ui/Composer.tsx'), 'utf8')
    expect(src).toMatch(/insertAtCaret\(text\)/)
    // No append path anywhere near the transcript handler.
    expect(src).not.toMatch(/onChange\(value \+ text\)/)
  })
})

// ── the deny-mic path ────────────────────────────────────────────────────────────

describe('a denied microphone degrades with something actionable', () => {
  it('says where to turn it on, and opens no stream', async () => {
    const { bridge, fire } = installBridge()
    bridge.capabilities.probe = vi.fn(async () => ({
      available: true, granted: 'denied', requestable: false, reason: '',
    }))
    const { errors } = mountComposer()

    await chord(fire)

    await waitFor(() => expect(errors.length).toBe(1))
    // macOS will not prompt twice, so "try again" would be a lie: the message must name
    // the one place that can actually change the answer.
    expect(errors[0]).toMatch(/System Settings/i)
    expect(errors[0]).toMatch(/Privacy/i)
    // And nothing was opened, so there is no indicator lying about a stream either.
    expect(tracks.length).toBe(0)
    expect(screen.queryByRole('button', { name: /listening to your microphone/i })).toBeNull()
  })

  it('a not-determined grant is requested through the bridge (the TCC leg)', async () => {
    const { bridge, fire } = installBridge()
    bridge.capabilities.probe = vi.fn(async () => ({
      available: true, granted: 'not-determined', requestable: true, reason: '',
    }))
    const { errors } = mountComposer()

    await chord(fire)

    // The grant is asked for BEFORE the stream is opened — that is what makes the OS
    // dialog appear at a moment the user connects to their own gesture.
    await waitFor(() => expect(bridge.capabilities.request).toHaveBeenCalledWith('audio_capture'))
    await waitFor(() => expect(tracks.length).toBe(2))
    expect(errors).toEqual([])
  })
})

// ── binding ──────────────────────────────────────────────────────────────────────

describe('the chord is bound from config', () => {
  it('binds the configured chord, not the default', async () => {
    const { bridge } = installBridge()
    mountComposer()
    // The call site is the property: a config field nothing reads is an inert control,
    // so this asserts the CONSUMER passed the stored value through.
    await waitFor(() => expect(bridge.pushToTalk.bind).toHaveBeenCalledWith('Alt+F13'))
  })

  it('binds nothing in a browser tab', async () => {
    // No bridge installed: the hook must be completely inert rather than throwing or
    // reaching for a shell that is not there.
    const { errors } = mountComposer()
    await new Promise((r) => setTimeout(r, 10))
    expect(errors).toEqual([])
    expect(screen.queryByRole('button', { name: /listening to your microphone/i })).toBeNull()
  })
})
