import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ConfirmOptions } from '../../ui/dialog'

// ── The Settings hub's YOLO switch turned auto-approve-everything on in ONE click ────────────────
//
// Measured on a real gateway: one click on the Agent defaults tile's "YOLO auto-approve all" switch
// sent `PATCH {"path":"agent.yolo","value":true}` ~41 ms later (200), it persisted across a reload,
// and nothing asked — no dialog, no alert, no toast. The Agent defaults PANEL, one click away, asked
// first ("Turn on YOLO mode? Every tool-approval confirmation will be skipped…"). The dialog lived in
// one caller, so the second caller skipped it.
//
// 🔑 THE NETWORK IS THE ASSERTION, not a mocked API method. `fetch` is stubbed and every request is
// recorded, so "sends nothing" means nothing — whichever helper a future edit routes the write
// through, and including the re-read a save usually triggers. Mocking `api.patchConfig` would let a
// write sent any other way pass as silence.

type Call = { method: string; url: string; body: unknown }
let calls: Call[] = []
let routes: Record<string, unknown> = {}
const ok = (v: unknown) => new Response(JSON.stringify(v), { status: 200, headers: { 'Content-Type': 'application/json' } })
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0)) }

let answer: Promise<boolean>
const confirmSpy = vi.fn((_req: ConfirmOptions) => answer)
const notifySpy = vi.fn()
/** What a write answers; `null` is a 200. */
let writeRefusal: { status: number; body: unknown } | null = null

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  calls = []
  writeRefusal = null
  confirmSpy.mockClear()
  notifySpy.mockClear()
  routes = {
    '/api/config/personalclaw': { agent: { approval_mode: 'auto', yolo: false } },
    '/api/agents': { agents: [], default_agent: '' },
  }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    if (method !== 'GET' && writeRefusal) {
      return new Response(JSON.stringify(writeRefusal.body), { status: writeRefusal.status, headers: { 'Content-Type': 'application/json' } })
    }
    return ok(method === 'GET' ? (routes[url] ?? {}) : { ok: true })
  }))
  vi.doMock('../../ui/dialog', async (orig) => ({ ...(await orig<Record<string, unknown>>()), confirm: confirmSpy }))
  vi.doMock('../../app/appSdk', async (orig) => ({ ...(await orig<Record<string, unknown>>()), notify: notifySpy }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function mountAgentTile() {
  const { SETTINGS_WIDGETS } = await import('./settingsWidgets')
  const tile = SETTINGS_WIDGETS.find((w) => w.id === 'agent')
  if (!tile) throw new Error('the Agent defaults tile is gone')
  function Host() { return <>{tile!.render('', () => {})}</> }
  render(<Host />)
  return screen.findByRole('switch', { name: 'YOLO auto-approve all' })
}

/** Click the switch and return every request made BECAUSE of the click. */
async function click(sw: HTMLElement): Promise<Call[]> {
  const before = calls.length
  await act(async () => { fireEvent.click(sw); await flush() })
  return calls.slice(before)
}

describe('the hub tile asks before turning YOLO on', () => {
  it('one click opens the confirmation, and nothing is sent while it is open', async () => {
    answer = new Promise(() => {})   // the dialog is up and unanswered
    const sent = await click(await mountAgentTile())
    expect(confirmSpy, 'the click must open the YOLO confirmation').toHaveBeenCalledTimes(1)
    expect(sent, 'no request may leave while the owner has not answered').toEqual([])
  })

  it('cancelling sends no request at all, and the switch stays off', async () => {
    answer = Promise.resolve(false)
    const sw = await mountAgentTile()
    const sent = await click(sw)
    expect(confirmSpy).toHaveBeenCalledTimes(1)
    expect(sent, 'a declined dialog writes nothing — and re-reads nothing either').toEqual([])
    expect(sw.getAttribute('aria-checked')).toBe('false')
  })

  it('confirming sends exactly one PATCH, and it carries the consent the server requires', async () => {
    answer = Promise.resolve(true)
    const sent = await click(await mountAgentTile())
    expect(sent.filter((c) => c.method !== 'GET')).toEqual([
      { method: 'PATCH', url: '/api/config/personalclaw', body: { path: 'agent.yolo', value: true, confirm: true } },
    ])
  })

  it('asks with the panel’s words: what YOLO skips, as a danger confirmation', async () => {
    answer = Promise.resolve(false)
    await click(await mountAgentTile())
    const req = confirmSpy.mock.calls[0][0]
    expect(req.title).toBe('Turn on YOLO mode?')
    expect(String(req.body)).toMatch(/Every tool-approval confirmation will be skipped, for every session/)
    expect(String(req.body)).toMatch(/no expiry/)
    expect(req.danger).toBe(true)
  })

  it('a write the server refuses is reported once, and the switch does not claim it', async () => {
    answer = Promise.resolve(true)
    writeRefusal = { status: 400, body: { error: { code: 'confirmation_required', message: 'send {"confirm": true}' } } }
    const sw = await mountAgentTile()
    const sent = await click(sw)
    expect(sent.filter((c) => c.method !== 'GET')).toHaveLength(1)
    expect(notifySpy, 'one toast, from the one writer — not a second from the tile').toHaveBeenCalledTimes(1)
    expect(String(notifySpy.mock.calls[0][0])).toMatch(/^Couldn't turn on YOLO mode/)
    expect(notifySpy.mock.calls[0][1]).toBe('error')
    expect(sw.getAttribute('aria-checked'), 'the bypass is not live, so the switch must not say it is').toBe('false')
  })

  it('turning YOLO off never asks — the tightening direction stays one click', async () => {
    routes['/api/config/personalclaw'] = { agent: { approval_mode: 'auto', yolo: true } }
    answer = Promise.resolve(true)
    const sent = await click(await mountAgentTile())
    expect(confirmSpy, 'revoking the bypass must never wait on a dialog').not.toHaveBeenCalled()
    expect(sent.filter((c) => c.method !== 'GET')).toEqual([
      { method: 'PATCH', url: '/api/config/personalclaw', body: { path: 'agent.yolo', value: false } },
    ])
  })
})
