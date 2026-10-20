import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'

// ── The Speech settings say which ffmpeg transcription runs, or why there is none ───────────────
//
// ffmpeg cuts a long recording into parts and takes the sound out of a video before either is
// transcribed. It is found, by its absolute path, on the PATH the gateway started with and then in
// the folders package managers use, and the gateway's own PATH is never changed to find it. Where
// none is found, this is where the owner acts, so the row shows the gateway's own sentence: where
// it looked and what to do.

const MISSING =
  "ffmpeg isn't installed where PersonalClaw looks for it: the folders on the PATH the gateway " +
  'started with, then ~/.local/bin, /opt/homebrew/bin and /usr/local/bin. Install it with brew ' +
  'install ffmpeg and try again, or start the gateway with the folder that holds it on its PATH.'

let ffmpeg: () => Promise<{ path: string | null; message: string }>

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      useCaseSettings: (useCase: string) =>
        Promise.resolve({ value: { enabled: useCase === 'stt' }, revision: 'rev-1' }),
      saveUseCaseSettings: () => Promise.reject(new Error('not saved in this test')),
      modelsActive: () => Promise.resolve({ stt: ['whisper:base'], tts: [] }),
      sttFfmpeg: () => ffmpeg(),
      personalclawConfig: () => Promise.resolve({}),
      voiceLoopConfig: () => Promise.resolve({}),
      voiceProfiles: () => Promise.resolve({ profiles: [], bindings: {} }),
      voiceResolve: () => Promise.resolve({ surface: '', resolved: true, level: 'built-in' }),
      lexiconTerms: () => Promise.resolve({ terms: [], total: 0 }),
      lexiconCorrections: () => Promise.resolve({ corrections: [] }),
    },
  }
})

import { VoicePanel } from './VoicePanel'
import { resetDataStore } from '../../lib/data'

beforeEach(() => {
  resetDataStore()
  sessionStorage.clear()
})

/** The ffmpeg row, once the speech-to-text section has painted it. */
async function ffmpegRow(): Promise<HTMLElement> {
  render(<VoicePanel go={() => {}} />)
  const label = await screen.findByText('ffmpeg', { selector: 'div' })
  return label.parentElement as HTMLElement
}

describe('the Speech settings say which ffmpeg transcription runs', () => {
  it('shows the path of the one it found, and what it is for', async () => {
    ffmpeg = () => Promise.resolve({ path: '/opt/homebrew/bin/ffmpeg', message: '' })
    const row = await ffmpegRow()
    await waitFor(() => expect(within(row).getByText('/opt/homebrew/bin/ffmpeg')).toBeTruthy())
    expect(within(row).getByText(/found/)).toBeTruthy()
    expect(row.textContent).toContain('Cuts a long recording into parts')
  })

  it("says in the gateway's words where it looked, and what to do, when there is none", async () => {
    ffmpeg = () => Promise.resolve({ path: null, message: MISSING })
    const row = await ffmpegRow()
    await waitFor(() => expect(within(row).getByText(MISSING)).toBeTruthy())
    expect(within(row).getByText(/not found/)).toBeTruthy()
  })

  it('a read that failed says so in the row, and the speech settings around it still show', async () => {
    ffmpeg = () => Promise.reject(new Error('gateway unreachable'))
    const row = await ffmpegRow()
    await waitFor(() => expect(within(row).getByRole('alert').textContent).toContain('gateway unreachable'))
    expect(within(row).getByRole('button', { name: 'Retry' })).toBeTruthy()
    expect(screen.getByRole('switch', { name: 'Enable speech-to-text' })).toBeTruthy()
  })
})
