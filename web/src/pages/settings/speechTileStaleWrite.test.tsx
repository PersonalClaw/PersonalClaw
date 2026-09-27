import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ── The Settings hub's speech switches save over the file this tile read ────────────────────────
//
// The tile's two switches each wrote `{...settings, enabled}` from the tile's copy of the use
// case's settings file — the file the Voice page, the Slack voice modal and the routing levers also
// write — so a hub opened before any of them saved put its old values back over theirs.
//
// 🔑 THE NETWORK IS THE ASSERTION. `fetch` is a fake of the gateway's route, If-Match check
// included, so what is proved is the request the click sends — the precondition header and the
// body — and what the stored file ends up holding.

type Call = { method: string; url: string; ifMatch: string | null; body: unknown }
type Stored = { settings: Record<string, unknown>; revision: string }
let calls: Call[] = []
let stored: Record<string, Stored> = {}
let written = 0
const json = (v: unknown, status = 200) =>
  new Response(JSON.stringify(v), { status, headers: { 'Content-Type': 'application/json' } })
const notifySpy = vi.fn()

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  calls = []
  written = 0
  notifySpy.mockClear()
  stored = {
    stt: { settings: { enabled: false }, revision: 'rev-1' },
    tts: { settings: { enabled: false }, revision: 'rev-1' },
  }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const headers = new Headers(init?.headers)
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, url, ifMatch: headers.get('If-Match'), body })
    if (url === '/api/models/active') return json({ use_cases: { stt: ['whisper:base'], tts: ['piper:en'] }, revisions: {} })
    const route = url.match(/^\/api\/models\/use-cases\/(stt|tts)\/settings$/)
    if (!route) return json({})
    const useCase = route[1]
    if (method === 'GET') return json({ use_case: useCase, settings: stored[useCase].settings, revision: stored[useCase].revision })
    if (headers.get('If-Match') !== `"${stored[useCase].revision}"`) {
      return json({ error: { code: 'stale_write', message: `This write replaces the ${useCase} settings, which changed.` } }, 409)
    }
    stored[useCase] = { settings: body as Record<string, unknown>, revision: `rev-w${++written}` }
    return json({ ok: true, use_case: useCase, settings: stored[useCase].settings, revision: stored[useCase].revision })
  }))
  vi.doMock('../../app/appSdk', async (orig) => ({ ...(await orig<Record<string, unknown>>()), notify: notifySpy }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function mountSpeechTile() {
  const { SETTINGS_WIDGETS } = await import('./settingsWidgets')
  const tile = SETTINGS_WIDGETS.find((w) => w.id === 'voice')
  if (!tile) throw new Error('the Speech & Transcription tile is gone')
  function Host() { return <>{tile!.render('', () => {})}</> }
  render(<Host />)
  return await screen.findByRole('switch', { name: 'Speech-to-text' })
}
const writes = () => calls.filter((c) => c.method === 'PUT')

describe('the speech tile saves over the file it read', () => {
  it('a switch names the revision the tile read', async () => {
    fireEvent.click(await mountSpeechTile())
    await waitFor(() => expect(writes()).toHaveLength(1))
    expect(writes()[0]).toMatchObject({ url: '/api/models/use-cases/stt/settings', ifMatch: '"rev-1"', body: { enabled: true } })
    expect(stored.stt.settings).toEqual({ enabled: true })
  })

  it('a file saved elsewhere since is not overwritten: the change waits, then lands on top of it', async () => {
    const toggle = await mountSpeechTile()
    // The Slack voice modal saves a language after the hub painted.
    stored.stt = { settings: { enabled: false, language: 'fr' }, revision: 'rev-slack' }
    fireEvent.click(toggle)

    const said = await screen.findByText(/changed elsewhere/)
    expect(said.closest('[role="alert"]')).not.toBeNull()
    // Refused, so the file is as the modal left it, the switch still shows what is stored, and a
    // refusal the notice explains is not also reported as a failure.
    expect(stored.stt).toEqual({ settings: { enabled: false, language: 'fr' }, revision: 'rev-slack' })
    expect(screen.getByRole('switch', { name: 'Speech-to-text' }).getAttribute('aria-checked')).toBe('false')
    expect(notifySpy).not.toHaveBeenCalled()

    const reapply = await screen.findByRole('button', { name: 'Reload and reapply' })
    await waitFor(() => expect(reapply.getAttribute('aria-disabled')).not.toBe('true'))
    fireEvent.click(reapply)
    // The switch's one change on top of the file as stored now, over ITS revision.
    await waitFor(() => expect(writes()).toHaveLength(2))
    expect(writes()[1]).toMatchObject({ ifMatch: '"rev-slack"', body: { enabled: true, language: 'fr' } })
    expect(stored.stt.settings).toEqual({ enabled: true, language: 'fr' })
    await waitFor(() => expect(screen.queryByText(/changed elsewhere/)).toBeNull())
  })
})
