import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'

// ── Settings → Guardrails asks with the number, and shows what is stored ──────────────────────────
//
// Measured: the daily dollar cap stood at $33.50, a select-all missed, and the field read
// 10033.5. The gateway's question was "The agent may spend more money per day — 0 removes
// the limit." — the words a raise to $100 got — and Cancel left 10033.5 in the box while the cap in
// effect stayed $33.50, because the row painted its value BEFORE the save and nothing put it back.
// The same field marked a saved $33.50 invalid: its step was 1.
//
// Driven through the real `api.patchConfig` and its consent step against a stand-in gateway that
// answers the way `PATCH /api/config/personalclaw` does, so what is asserted is the dialog the owner
// reads and the field they are left looking at.

const confirmSpy = vi.hoisted(() => vi.fn(async (_opts: unknown) => false))
vi.mock('../../ui/dialog', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  confirm: (opts: unknown) => confirmSpy(opts),
}))
const notify = vi.hoisted(() => vi.fn())
vi.mock('../../app/appSdk', async (orig) => ({ ...(await orig<Record<string, unknown>>()), notify }))

const CAP_SENTENCE = 'The agent may spend more money per day — 0 removes the limit.'
const CAUTION = 'That is about 300 times the current limit, so check the number before you allow it.'

const reply = (status: number, o: unknown) =>
  new Response(JSON.stringify(o), { status, headers: { 'Content-Type': 'application/json' } })

/** A gateway holding a $33.50 cap and a "block" scan mode. A loosening write without `confirm` is
 *  asked about in the PATCH handler's words; `refuse` makes it refuse every write instead. */
function gateway({ refuse = false } = {}) {
  const stored = { budgets: { max_tokens_per_run: 0, max_tokens_per_day: 0, max_dollars_per_day: 33.5 }, scan_mode: 'block' }
  const writes: Record<string, unknown>[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/config/personalclaw' && init?.method === 'PATCH') {
      const body = JSON.parse(String(init.body)) as { path: string; value: unknown; confirm?: boolean }
      writes.push(body)
      if (refuse) return reply(400, { error: { code: 'invalid_request', message: 'must be between 0.0 and 100000.0' } })
      if (body.confirm !== true && body.path === 'guardrails.budgets.max_dollars_per_day') {
        const big = Number(body.value) >= 335
        return reply(400, {
          error: {
            code: 'confirmation_required',
            message: 'send {"confirm": true} to confirm',
            detail: {
              field: body.path,
              consent: CAP_SENTENCE,
              title: 'Loosen a security setting?',
              change: big ? '$33.50 → $10,033.50' : '$33.50 → $100.00',
              ...(big ? { caution: CAUTION } : {}),
            },
          },
        })
      }
      if (body.confirm !== true && body.path === 'guardrails.scan_mode') {
        return reply(400, {
          error: {
            code: 'confirmation_required',
            message: 'send {"confirm": true} to confirm',
            detail: {
              field: body.path,
              consent: "Secrets or personal data found in a prompt bound for a remote model provider get less protection — 'redact' replaces them, 'warn' only logs them.",
              title: 'Loosen a security setting?',
              change: 'Block → Warn',
            },
          },
        })
      }
      if (body.path === 'guardrails.budgets.max_dollars_per_day') stored.budgets.max_dollars_per_day = Number(body.value)
      if (body.path === 'guardrails.scan_mode') stored.scan_mode = String(body.value)
      return reply(200, { guardrails: stored })
    }
    if (url === '/api/config/personalclaw') return reply(200, { guardrails: stored })
    if (url === '/api/incident') return reply(200, { active: false, reason: '', started_at: '' })
    if (url === '/api/models/health') return reply(200, { providers: [], callers: [], generated_from: 0 })
    if (url === '/api/autonomy') return reply(200, { rungs: [], rung_meta: [], incident_active: false, types: [], reversals: [] })
    return reply(404, { error: { code: 'not_found', message: url } })
  }))
  return { stored, writes }
}

async function mount() {
  const { GuardrailsPanel } = await import('./GuardrailsPanel')
  await act(async () => { render(<GuardrailsPanel />) })
  return (await screen.findByRole('spinbutton', { name: 'Max dollars / day' })) as HTMLInputElement
}

/** Type a number into the field and leave it, which is what commits it. */
async function enter(box: HTMLInputElement, text: string) {
  await act(async () => {
    fireEvent.focus(box)
    fireEvent.change(box, { target: { value: text } })
    fireEvent.blur(box)
  })
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  confirmSpy.mockReset()
  confirmSpy.mockImplementation(async () => false)
  notify.mockReset()
})
afterEach(() => { vi.unstubAllGlobals() })

describe('the daily dollar cap', () => {
  it('asks with the number it changes from and to, and a second look at a typo', async () => {
    gateway()
    const box = await mount()
    await enter(box, '10033.5')

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1))
    const opts = confirmSpy.mock.calls[0][0] as { title: string; body: string }
    expect(opts.title).toBe('Loosen a security setting?')
    expect(opts.body).toBe(`${CAP_SENTENCE}\n\n$33.50 → $10,033.50\n\n${CAUTION}`)
  })

  it('Cancel puts the stored cap back in the box, and says nothing changed', async () => {
    const { writes } = gateway()
    const box = await mount()
    await enter(box, '10033.5')

    // 🔴 Before: the box kept 10033.5 while the cap in effect stayed 33.5.
    await waitFor(() => expect(box.value).toBe('33.5'))
    expect(writes, 'nothing was sent after the refusal').toHaveLength(1)
    expect(screen.queryByText('Saved ✓'), 'nothing was saved, so nothing says so').toBeNull()
    // A note in the owner's own terms, not an error: declining was their choice.
    expect(notify).toHaveBeenCalledWith('Not changed — you kept the current setting.')
    expect(notify.mock.calls.some((c) => c[1] === 'error')).toBe(false)
  })

  it('a refused save puts the stored cap back too, and says why', async () => {
    gateway({ refuse: true })
    const box = await mount()
    await enter(box, '50')

    await waitFor(() => expect(box.value).toBe('33.5'))
    expect(notify).toHaveBeenCalledWith(
      "Couldn't save Max dollars / day: must be between 0.0 and 100000.0", 'error',
    )
    expect(screen.queryByText('Saved ✓')).toBeNull()
  })

  it('Allow stores it, and the box keeps it with a Saved', async () => {
    confirmSpy.mockImplementation(async () => true)
    const { stored, writes } = gateway()
    const box = await mount()
    await enter(box, '100')

    await waitFor(() => expect(stored.budgets.max_dollars_per_day).toBe(100))
    expect(writes.map((w) => w.confirm)).toEqual([undefined, true])
    expect((confirmSpy.mock.calls[0][0] as { body: string }).body).toBe(`${CAP_SENTENCE}\n\n$33.50 → $100.00`)
    await waitFor(() => expect(screen.getByText('Saved ✓')).toBeTruthy())
    expect(box.value).toBe('100')
  })

  it('takes cents: a stored $33.50 is a valid value of the field', async () => {
    gateway()
    const box = await mount()

    expect(box.value).toBe('33.5')
    expect(box.getAttribute('step')).toBe('0.01')
    // 🔴 Before: step 1, so the browser held 33.5 to whole dollars and marked it invalid.
    expect(box.validity.stepMismatch).toBe(false)
    expect(box.checkValidity()).toBe(true)
  })
})

describe('the outbound scan mode', () => {
  it('a declined loosening leaves the stored mode chosen', async () => {
    const { stored } = gateway()
    await mount()
    const warn = screen.getByRole('button', { name: 'Scan mode: Warn' })
    await act(async () => { fireEvent.click(warn) })

    await waitFor(() => expect(confirmSpy).toHaveBeenCalledTimes(1))
    expect((confirmSpy.mock.calls[0][0] as { body: string }).body).toMatch(/\n\nBlock → Warn$/)
    await waitFor(() => expect(notify).toHaveBeenCalledWith('Not changed — you kept the current setting.'))
    expect(stored.scan_mode).toBe('block')
    // 🔴 Before: the pill moved to Warn before the save and stayed there.
    expect(screen.getByRole('button', { name: 'Scan mode: Block' }).getAttribute('aria-pressed')).toBe('true')
    expect(warn.getAttribute('aria-pressed')).toBe('false')
  })
})
