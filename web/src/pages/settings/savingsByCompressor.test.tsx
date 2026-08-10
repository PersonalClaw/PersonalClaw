import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import type { ToolsSavings } from '../../lib/api'

// ── Which compressor is actually earning its keep ─────────────────────────────
//
// `summary()` aggregates a per-compressor savings map and the card named only `top_compressor`.
// So "is my second compressor doing anything, or is one carrying everything?" had no answer — with
// one compressor dominating, the rest were indistinguishable from unused.
//
// Judged, not swept. Three decisions the test pins:
//
//   shown only when >1 compressor contributed   with a single entry the breakdown just restates
//                                               `top_compressor` on the line above it — noise.
//   zero-savings entries filtered               a compressor that ran and saved nothing is not a
//                                               contributor; listing it at ~0 implies it is.
//   chars converted to TOKENS                   the headline is in tokens. Two units in one card
//                                               invites comparing numbers that are not comparable.
//                                               Same ~4 chars/token estimate the backend uses for
//                                               `saved_tokens_estimated`.
//
// The other two ToolsSavings fields stay unread deliberately (see the session ledger):
// `estimated` is a hardcoded `True` — a constant is not a measurement, and the card already says
// "Estimated" in prose; `rows` is the raw ledger the summary is derived FROM.

const base: ToolsSavings = {
  saved_chars: 40_000,
  saved_tokens_estimated: 10_000,
  estimated: true,
  projection_count: 12,
  top_compressor: 'json_head',
  by_compressor: { json_head: 32_000, log_tail: 8_000 },
  rows: [],
}

async function mount(over: Partial<ToolsSavings> = {}) {
  vi.resetModules()
  vi.doMock('../../lib/api', () => ({
    api: {
      toolsSavings: () => Promise.resolve({ ...base, ...over }),
      personalclawConfig: () => Promise.resolve({}),
      patchConfig: () => Promise.resolve({}),
    },
  }))
  const { SavingsCard } = await import('./ProjectionRulesPanel')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<SavingsCard />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

describe('the per-compressor breakdown', () => {
  it('names every contributing compressor', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('json_head')
    expect(text).toContain('log_tail')
  })

  it('converts chars to tokens so both figures share one unit', async () => {
    // 32000 chars ≈ 8,000 tokens · 8000 ≈ 2,000 — NOT the raw char counts.
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('8,000')
    expect(text).toContain('2,000')
    expect(text).not.toContain('32,000')
  })

  it('orders by savings, biggest first', async () => {
    const text = (await mount()).container.textContent ?? ''
    // The breakdown's whole job is ranking; the top entry must lead even when the map does not.
    const reordered = await mount({ by_compressor: { log_tail: 8_000, json_head: 32_000 } })
    const t2 = reordered.container.textContent ?? ''
    expect(t2.indexOf('json_head')).toBeLessThan(t2.lastIndexOf('log_tail'))
    expect(text.indexOf('json_head')).toBeLessThan(text.lastIndexOf('log_tail'))
  })

  it('drops a compressor that saved nothing', async () => {
    // It ran and earned nothing — listing it at ~0 implies it contributed.
    const { container } = await mount({
      by_compressor: { json_head: 32_000, log_tail: 8_000, noop_thing: 0 },
    })
    expect(container.textContent).not.toContain('noop_thing')
  })
})

describe('the breakdown appears only when it adds something', () => {
  it('is omitted for a single compressor', async () => {
    // One entry would just restate `top_compressor` on the line above.
    const { container } = await mount({ by_compressor: { json_head: 40_000 } })
    const text = container.textContent ?? ''
    expect(text).toContain('top compressor: json_head')   // the headline still says it
    expect(text.match(/json_head/g)?.length).toBe(1)      // and only once
  })

  it('is omitted when every entry saved zero', async () => {
    // Named distinctively and asserted on the MONO element, not the card text: a bare
    // `not.toContain('a ')` matches "across" in the headline and fails for the wrong reason.
    const { container } = await mount({ by_compressor: { zeroish_one: 0, zeroish_two: 0 } })
    expect([...container.querySelectorAll('.font-mono')].map((e) => e.textContent))
      .not.toContain('zeroish_one')
  })

  it('survives a missing by_compressor entirely', async () => {
    const { container } = await mount({
      by_compressor: undefined as unknown as Record<string, number>,
    })
    // The headline must still render — a partial payload cannot blank the card.
    expect(container.textContent).toContain('TokenJuice saved')
  })
})

describe('the card still hides itself when there is nothing to report', () => {
  it('renders nothing at zero savings, breakdown or not', async () => {
    // A fresh install has no data; "0 saved" would be noise. Pre-existing behaviour, pinned so the
    // added rows cannot resurrect an empty card.
    const { container } = await mount({
      saved_chars: 0, saved_tokens_estimated: 0, projection_count: 0,
      top_compressor: null, by_compressor: { json_head: 5, log_tail: 5 },
    })
    expect(container.textContent).toBe('')
  })
})
