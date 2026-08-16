import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SourceRow } from './SourcesPage'
import { HEALTH_META, HEALTH_NEEDS_RENDER, healthMeta } from './sourceMeta'
import type { WatchedSource } from '../../lib/api'

// ── The Sources row's two promises (WATCHED-SOURCES §6.3, §2.3/§12, WS-9) ─────────────
//
// 1. The 'no AI' CHIP IS A READOUT, not decoration. §6.3's guarantee is structural — a raw
//    source's items run through a graph whose LLM nodes are ABSENT — so a chip driven by
//    anything other than the source's real `enrichment` would be a claim the UI invented.
//    The negative case is the one that matters: a chip rendered unconditionally satisfies
//    every "it appears" assertion.
//
// 2. The TWO REMEDIATIONS STAY OPPOSITE. WS-3 measures the discrimination between "you
//    pointed at the wrong URL" (the page rendered plenty of text) and "this page is a
//    JavaScript shell" precisely because the fixes are opposite — a different URL vs. one
//    budget knob. Collapsing them into a single "nothing found" strip would send half the
//    users the wrong way, and every per-case assertion would still pass. So the row is
//    rendered in BOTH states and the two are asserted to differ in message AND in control.
//
// The backend supplies the verdict (`remediation`) and the guidance text; these tests hold
// the row to RENDERING what it is given rather than deciding it locally.

const KINDS = { 'watched-page': { display_name: 'Watched Page', form: 'web_page' } }

function source(over: Partial<WatchedSource> = {}): WatchedSource {
  return {
    id: 'src-1', name: 'Product changelog', provider: 'watched-page', kind: 'web_page',
    spec: { url: 'https://example.com/changelog' }, budget: {},
    enrichment: 'full', poll_interval_secs: 3600, item_type: 'bookmark', enabled: true,
    health_status: 'ok', last_error_summary: '', last_escalations: [], last_new_count: 2,
    last_poll_at: new Date().toISOString(), enrolled: true,
    remediation: { kind: '', guidance: '', detail: '', action: '' },
    ...over,
  }
}

function renderRow(over: Partial<WatchedSource> = {}) {
  return render(<SourceRow source={source(over)} kinds={KINDS} onChanged={() => {}} />)
}

describe("the 'no AI' chip is a readout of the source's enrichment", () => {
  it('appears on a raw source', () => {
    renderRow({ enrichment: 'raw' })

    expect(screen.getByText('no AI')).toBeTruthy()
  })

  it('is ABSENT on an enriched source', () => {
    // The falsifying half: make the chip unconditional and this is the only test that reds.
    renderRow({ enrichment: 'full' })

    expect(screen.queryByText('no AI'), 'a full-enrichment source must not claim no AI').toBeNull()
  })

  it("explains what the promise IS, not just that it holds", () => {
    renderRow({ enrichment: 'raw' })

    expect(screen.getByText('no AI').getAttribute('title')).toMatch(/never reach a model/)
  })
})

describe('the two remediations are opposite and never collapse into one', () => {
  const RENDER_GUIDANCE = 'This page builds its content with JavaScript, so a plain fetch sees an empty shell. Set budget.allow_render to true to let this source use the render tier.'
  const LISTING_GUIDANCE = 'No items found. Auto-detection reads LISTING pages — a changelog, a blog index, a category/tag/archive page, a newsroom. It cannot read a homepage or a single post.'

  it('a JS shell offers the render-tier knob as a button and no URL field', () => {
    renderRow({
      health_status: HEALTH_NEEDS_RENDER,
      remediation: { kind: 'render_tier', guidance: RENDER_GUIDANCE, detail: '', action: 'allow_render' },
    })

    expect(screen.getByRole('button', { name: 'Allow the render tier' })).toBeTruthy()
    expect(screen.getByText(RENDER_GUIDANCE)).toBeTruthy()
    expect(screen.queryByRole('textbox'), 'a JS shell is not fixed by a different URL').toBeNull()
  })

  it('a wrong URL offers a URL field and no render-tier button', () => {
    renderRow({
      health_status: 'degraded',
      remediation: { kind: 'listing_page', guidance: LISTING_GUIDANCE, detail: '', action: 'edit_url' },
    })

    expect(screen.getByRole('textbox', { name: /Listing-page URL for Product changelog/ })).toBeTruthy()
    expect(screen.getByText(LISTING_GUIDANCE)).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'Allow the render tier' }),
      'a wrong URL is not fixed by a render tier',
    ).toBeNull()
  })

  it('neither is offered on a healthy source', () => {
    renderRow()

    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Allow the render tier' })).toBeNull()
  })

  it("an already-allowed render tier shows the poll's own reason and no knob", () => {
    renderRow({
      health_status: HEALTH_NEEDS_RENDER,
      budget: { allow_render: true },
      remediation: {
        kind: 'render_tier', guidance: RENDER_GUIDANCE,
        detail: 'render tier unavailable; install personalclaw[js-render]', action: '',
      },
    })

    expect(screen.getByText(/install personalclaw\[js-render\]/)).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'Allow the render tier' }),
      'a button that re-sets a flag already set lies about what pressing it does',
    ).toBeNull()
  })
})

describe('the row states the facts a user needs before the first poll', () => {
  it('names the health status in words, with its meaning as the tooltip', () => {
    renderRow({ health_status: 'degraded' })

    const chip = screen.getByText('Degraded')
    expect(chip.getAttribute('title')).toBe(HEALTH_META['degraded'].hint)
  })

  it('does not report a health the source has not earned', () => {
    // Found by driving the real thing: `sources.health_status` DEFAULTS to `ok` in the store,
    // so a source saved seconds ago read "Healthy · never polled" in one breath.
    renderRow({ last_poll_at: null, health_status: 'ok' })

    expect(screen.getByText('Not polled yet')).toBeTruthy()
    expect(screen.queryByText('Healthy')).toBeNull()
  })

  it('says plainly when nothing is registered to poll a source', () => {
    // The engine only records this as a health error once it has RUN, so a never-polled row
    // with no provider would otherwise read as healthy.
    renderRow({ enrolled: false, last_poll_at: null })

    expect(screen.getByText('No provider')).toBeTruthy()
    expect(screen.getByText(/never polled/)).toBeTruthy()
  })

  it('states when the next check runs, since the health rollup will not move until then', () => {
    // Found by driving the real thing: after fixing a source's URL the row still read
    // "Degraded" — correctly, since the rollup describes the last poll — with nothing saying
    // the fix would ever be tested.
    // MID-bucket, not a boundary: `relFuture` floors to whole minutes, so an exact 20-minute
    // offset lands on 20m or 19m depending on whether a millisecond elapsed between building
    // the fixture and rendering it. Measured: 1 red in 3 runs before this. +30s is unambiguous.
    renderRow({ next_poll_at: new Date(Date.now() + 20 * 60_000 + 30_000).toISOString() })

    expect(screen.getByText(/next in 20m/)).toBeTruthy()
  })

  it('does not promise a next check for a paused source', () => {
    renderRow({ enabled: false, next_poll_at: new Date(Date.now() + 15 * 60_000).toISOString() })

    expect(screen.queryByText(/next in/)).toBeNull()
  })

  it('shows the escalations the last poll had to climb', () => {
    renderRow({ last_escalations: ['escalated to render tier; extracted 3 item(s)'] })

    expect(screen.getByText(/escalated to render tier/)).toBeTruthy()
  })

  it('names the pause switch after the source, so N switches are distinguishable', () => {
    renderRow()

    expect(screen.getByRole('switch', { name: 'Pause Product changelog' })).toBeTruthy()
  })
})

describe('the status vocabulary cannot drift between the map and its named constant', () => {
  it('HEALTH_META has an entry for the constant the two pages branch on', () => {
    expect(HEALTH_META[HEALTH_NEEDS_RENDER]).toBeTruthy()
  })

  it('an unrecognised status is labelled honestly rather than rendered as healthy', () => {
    const meta = healthMeta('something the backend added later')

    expect(meta.label).toBe('Unknown')
    expect(meta.tone).not.toBe('ok')
  })
})
