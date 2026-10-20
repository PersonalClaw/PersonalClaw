import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

// ── The Settings hub's Usage tile: today's spend, or what fills it ────────────────────────────────
//
// The tile reads today's totals (`/api/usage/totals?window=day`) and has two states: a day with
// recorded turns shows the cost, the tokens and the turns; a day with none says what will land
// there. The browser check of the Settings hub answers this read with a fixed day of usage (the
// home it runs on is shared, so how many turns it holds depends on what ran before), which means
// the empty state is held here instead.
//
// Mounted over a stubbed `fetch` and the REAL api client, so the body is the wire's own shape.

const EMPTY_TEXT = /land here once usage is recorded/

let totals: unknown = null
const ok = (v: unknown) => new Response(JSON.stringify(v), { status: 200, headers: { 'Content-Type': 'application/json' } })

function day(over: Record<string, unknown> = {}) {
  return {
    input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_creation_tokens: 0,
    cost_usd: 0, turns: 0, priced: true, units: {}, ...over,
  }
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  totals = null
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/api/usage/totals')) {
      return ok({ since: '2026-10-19T07:00:00+00:00', until: '', session: '', totals })
    }
    return ok({})
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

async function mountUsage() {
  const { SETTINGS_WIDGETS } = await import('./settingsWidgets')
  const tile = SETTINGS_WIDGETS.find((w) => w.id === 'usage')
  if (!tile) throw new Error('the usage tile is gone')
  function Host() { return <>{tile!.render('', () => {})}</> }
  return render(<Host />)
}

describe('the Usage tile', () => {
  it('says what fills it on a day with no recorded turns', async () => {
    totals = day()
    await mountUsage()
    await waitFor(() => expect(screen.getByText(EMPTY_TEXT)).toBeInTheDocument())
    expect(screen.queryByText(/today/), 'no spend is claimed for a day with none').toBeNull()
  })

  it('says the same when the ledger has no totals for the day', async () => {
    totals = null
    await mountUsage()
    await waitFor(() => expect(screen.getByText(EMPTY_TEXT)).toBeInTheDocument())
  })

  it('shows the cost, tokens and turns once a turn is recorded (the control)', async () => {
    totals = day({ input_tokens: 840, output_tokens: 180, cost_usd: 0.0123, turns: 20 })
    await mountUsage()
    await waitFor(() => expect(screen.getByText('$0.0123')).toBeInTheDocument())
    expect(screen.getByText(/1k tokens · 20 turns/)).toBeInTheDocument()
    expect(screen.queryByText(EMPTY_TEXT)).toBeNull()
  })
})
