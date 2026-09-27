import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

// ── A speech use case's settings are saved over the copy this page painted ──────────────────────
//
// `PUT /api/models/use-cases/{use_case}/settings` replaces the whole file, and the same file is
// written by the Slack voice modal (through the SDK) and by the routing levers. Each control here
// used to send `{...settings, <its field>}` from this page's copy — so a page opened before either
// of those saved put its old values back over theirs. The save now names the revision it was built
// from; a stale one is refused, and the change waits in the notice to be re-applied on top.

type Doc = { value: Record<string, unknown>; revision: string }

const notified: string[] = []
vi.mock('../../app/appSdk', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  notify: (msg: string) => { notified.push(msg) },
}))

/** The gateway's copy of each file — what a save is checked against. */
let stored: Record<string, Doc> = {}
let written = 0
const saveUseCaseSettings = vi.fn((useCase: string, next: Record<string, unknown>, base: string) => {
  if (base !== stored[useCase].revision) {
    return Promise.reject(new ApiError(`The ${useCase} settings changed.`, 409, 'stale_write'))
  }
  stored[useCase] = { value: next, revision: `rev-w${++written}` }
  return Promise.resolve({ ok: true, use_case: useCase, settings: next, revision: stored[useCase].revision })
})

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: {
      useCaseSettings: (useCase: string) => Promise.resolve(stored[useCase]),
      saveUseCaseSettings: (u: string, n: Record<string, unknown>, b: string) => saveUseCaseSettings(u, n, b),
      // A model bound for both, so the switches are operable.
      modelsActive: () => Promise.resolve({ stt: ['whisper:base'], tts: ['piper:en'] }),
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
import { ApiError } from '../../lib/api'
import { resetDataStore } from '../../lib/data'

beforeEach(() => {
  resetDataStore()
  sessionStorage.clear()
  notified.length = 0
  written = 0
  saveUseCaseSettings.mockClear()
  stored = {
    stt: { value: { enabled: false }, revision: 'rev-1' },
    tts: { value: { enabled: false }, revision: 'rev-1' },
  }
})

const sttSwitch = () => screen.getByRole('switch', { name: 'Enable speech-to-text' })

describe('the speech settings are saved over the copy the page painted', () => {
  it('a save names the revision the page read', async () => {
    render(<VoicePanel go={() => {}} />)
    fireEvent.click(await waitFor(sttSwitch))
    await waitFor(() => expect(saveUseCaseSettings).toHaveBeenCalledWith('stt', { enabled: true }, 'rev-1'))
    expect(screen.queryByText(/changed elsewhere/)).toBeNull()
  })

  it('a file saved elsewhere since is not overwritten: the change is kept, then re-applied on top', async () => {
    render(<VoicePanel go={() => {}} />)
    const toggle = await waitFor(sttSwitch)
    // The Slack voice modal saves a language after this page painted.
    stored.stt = { value: { enabled: false, language: 'fr' }, revision: 'rev-slack' }
    fireEvent.click(toggle)

    const said = await screen.findByText(/changed elsewhere/)
    expect(said.closest('[role="alert"]')).not.toBeNull()
    // The change is KEPT — on screen, not rolled back — and nothing is reported as a failure.
    expect(sttSwitch().getAttribute('aria-checked')).toBe('true')
    expect(notified).toEqual([])
    expect(stored.stt).toEqual({ value: { enabled: false, language: 'fr' }, revision: 'rev-slack' })

    const reapply = await screen.findByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.getAttribute('aria-disabled')).not.toBe('true'))
    fireEvent.click(reapply)
    // Saved over the file as stored now: the language set elsewhere survives beside this change.
    await waitFor(() => expect(saveUseCaseSettings).toHaveBeenLastCalledWith('stt', { enabled: true, language: 'fr' }, 'rev-slack'))
    await waitFor(() => expect(screen.queryByText(/changed elsewhere/)).toBeNull())
    expect(stored.stt.value).toEqual({ enabled: true, language: 'fr' })
  })
})
