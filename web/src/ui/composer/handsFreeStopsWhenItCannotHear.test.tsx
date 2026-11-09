import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

import { FakeRecorder, installFakeMicrophone } from '../../test/fakeMicrophone'
import { TranscriptionRefused } from './useMicRecorder'

// ── Hands-free turns off when it cannot hear you ────────────────────────────────────────────
//
// Hands-free stays on only while it can hear: a blocked microphone, one that stops mid-mode, or
// speech-to-text that refuses or fails a piece, turns it off, with one notice saying why, and the
// microphone is released. The
// assistant's own voice dropped as an echo is not a failure, so the mode stays on. Pressing the
// Listening chip turns the mode off, as the button does.

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, key) => (key === 'dashboardConfig'
        ? () => Promise.resolve({ send_on_enter: true })
        : (target as Record<string | symbol, unknown>)[key]),
    }),
  }
})

const { Composer } = await import('../Composer')

const CONTROLS = { agent: false, model: false, approval: false, reasoning: false, attach: false, mic: true, optimize: false }

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.restoreAllMocks()
})

function mount(onTranscribe: (blob: Blob) => Promise<string>) {
  const errors: string[] = []
  // What hands-free wrote into the box, so a row can see what was folded.
  const drafts: string[] = []
  render(
    <Composer value="" onChange={(v) => drafts.push(v)} onSend={() => {}} onStop={() => {}} controls={CONTROLS}
      onTranscribe={onTranscribe} onMicError={(m) => errors.push(m)} onHandsFreeSubmit={() => {}}
      handsFree={{ confirmationPhrases: ['go ahead'], exitPhrases: ['cancel'] }} />,
  )
  return { errors, drafts }
}

const handsFree = () => screen.getByRole('button', { name: /^Hands-free voice/ })
const on = () => handsFree().getAttribute('aria-pressed') === 'true'

describe('hands-free turns off when it cannot hear you', () => {
  it('a blocked microphone turns it off and says why', async () => {
    installFakeMicrophone({ deny: true })
    const { errors } = mount(async () => '')
    await act(async () => { fireEvent.click(handsFree()) })
    await waitFor(() => expect(errors).toHaveLength(1))
    expect(errors[0]).toMatch(/Microphone access was blocked/)
    await waitFor(() => expect(on()).toBe(false))
  })

  it('a refused transcription turns it off, with the host’s notice alone, and releases the microphone', async () => {
    const mic = installFakeMicrophone()
    const hostNotices: string[] = []
    const { errors } = mount(async () => {
      hostNotices.push('Speech-to-text is not set up.')
      throw new TranscriptionRefused('Speech-to-text is not set up.')
    })
    await act(async () => { fireEvent.click(handsFree()) })
    await waitFor(() => expect(mic.recorders.length).toBe(1))
    await act(async () => { mic.recorders[0].stop() })
    await waitFor(() => expect(on()).toBe(false))
    expect(hostNotices).toHaveLength(1)
    expect(errors).toEqual([])
    expect(mic.tracks[0].stop).toHaveBeenCalled()
    expect(mic.live()).toBeUndefined()
  })

  it('a failed transcription turns it off with one notice', async () => {
    const mic = installFakeMicrophone()
    const { errors } = mount(async () => { throw new Error('network down') })
    await act(async () => { fireEvent.click(handsFree()) })
    await waitFor(() => expect(mic.recorders.length).toBe(1))
    await act(async () => { mic.recorders[0].stop() })
    await waitFor(() => expect(on()).toBe(false))
    expect(errors).toEqual(['Couldn\'t transcribe the audio: network down'])
    expect(mic.tracks[0].stop).toHaveBeenCalled()
  })

  it('a microphone that stops turns it off with one notice, and the last piece is still heard', async () => {
    const mic = installFakeMicrophone()
    const said = vi.fn(async () => 'book the ferry')
    const { errors, drafts } = mount(said)
    await act(async () => { fireEvent.click(handsFree()) })
    await waitFor(() => expect(mic.recorders.length).toBe(1))
    // As a browser does when the track ends: the recorder stops itself.
    await act(async () => { mic.endTrack(); mic.recorders[0].stop() })
    await waitFor(() => expect(on()).toBe(false))
    expect(errors).toHaveLength(1)
    expect(errors[0]).toMatch(/microphone stopped/i)
    expect(mic.live()).toBeUndefined()
    expect(said).toHaveBeenCalledTimes(1)
    expect(drafts).toContain('book the ferry')
  })

  it('a microphone that stops ends the piece recording, which is still heard', async () => {
    const mic = installFakeMicrophone()
    const said = vi.fn(async () => 'book the ferry')
    const { errors, drafts } = mount(said)
    await act(async () => { fireEvent.click(handsFree()) })
    await waitFor(() => expect(mic.recorders.length).toBe(1))
    // Only the track ends: hands-free stops the piece itself, at once rather than on its timer.
    await act(async () => { mic.endTrack() })
    expect(mic.recorders[0].state).toBe('inactive')
    await waitFor(() => expect(on()).toBe(false))
    expect(errors).toHaveLength(1)
    expect(errors[0]).toMatch(/microphone stopped/i)
    expect(said).toHaveBeenCalledTimes(1)
    expect(drafts).toContain('book the ferry')
  })

  it('a piece that cannot start turns it off with one notice, and the microphone can be used again', async () => {
    const mic = installFakeMicrophone()
    vi.spyOn(FakeRecorder.prototype, 'start').mockImplementationOnce(() => {
      throw Object.assign(new Error('cannot record'), { name: 'NotSupportedError' })
    })
    const { errors } = mount(async () => '')
    await act(async () => { fireEvent.click(handsFree()) })
    await waitFor(() => expect(on()).toBe(false))
    expect(errors).toHaveLength(1)
    expect(errors[0]).toMatch(/microphone stopped/i)
    expect(mic.tracks[0].stop).toHaveBeenCalled()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Voice input' })) })
    await waitFor(() => expect(mic.getUserMedia).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(mic.live()).toBeDefined())
  })

  it('a piece with nothing to fold (silence, or the host’s echo drop) keeps it on', async () => {
    const mic = installFakeMicrophone()
    const { errors } = mount(async () => '')
    await act(async () => { fireEvent.click(handsFree()) })
    await waitFor(() => expect(mic.recorders.length).toBe(1))
    await act(async () => { mic.recorders[0].stop() })
    await waitFor(() => expect(mic.recorders.length).toBe(2))
    expect(on()).toBe(true)
    expect(errors).toEqual([])
  })

  it('pressing the Listening chip turns it off', async () => {
    const mic = installFakeMicrophone()
    mount(async () => '')
    await act(async () => { fireEvent.click(handsFree()) })
    await waitFor(() => expect(mic.recorders.length).toBe(1))
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /^Listening to your microphone/ })) })
    await waitFor(() => expect(on()).toBe(false))
    expect(mic.tracks[0].stop).toHaveBeenCalled()
    expect(mic.live()).toBeUndefined()
  })
})
