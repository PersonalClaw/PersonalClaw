/** A feed's set-up row says what it knows, and the Power user view says the rest.
 *
 *  1. The NO AI LINE IS A READOUT. The guarantee is structural (a raw source's items run through a
 *     graph whose model nodes are absent), so a line driven by anything other than the source's real
 *     `enrichment` would be a claim the page invented. The negative case is the one that matters.
 *  2. The TWO REMEDIATIONS STAY OPPOSITE. "You pointed at the wrong URL" and "this page is a
 *     JavaScript shell" have opposite fixes (a different URL, one budget knob), so the row is drawn in
 *     both states and the two must differ in words and in control.
 *  3. What a user needs to know whatever the view: nothing set up to check its kind, a folder's
 *     first scan, the links it left out. Its kind, its real cadence, its health, its next check and
 *     the costlier reads its last check made are the Power user view's lines.
 *
 *  The backend supplies the verdict (`remediation`) and the guidance text; the row renders what it
 *  is given rather than deciding it. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { answers, resetGateway } from '../../../test/libraryHarness'
import { HEALTH_META, HEALTH_NEEDS_RENDER, firstScanPromise, healthMeta } from '../../knowledge/sourceMeta'
import type { WatchedSource } from '../../../lib/api'

vi.mock('../../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../../test/libraryHarness')
  return { ...(await orig<typeof import('../../../lib/api')>()), api: gatewayApi() }
})
const { SetupPanel } = await import('./SetupPanel')
const { resetDataStore } = await import('../../../lib/data/store')

const KINDS = [
  { provider: 'watched-page', display_name: 'Watched Page', kind: 'web_page', form: 'web_page', previewable: true, poll_interval_secs: 3600 },
  { provider: 'watched-dir', display_name: 'Watched Folder', kind: 'dir', form: 'dir', previewable: false, poll_interval_secs: 3600 },
]

function source(over: Partial<WatchedSource> = {}): WatchedSource {
  return {
    id: 'src-1', name: 'Product changelog', provider: 'watched-page', kind: 'web_page',
    spec: { url: 'https://example.com/changelog' }, budget: {}, revision: 'r-src-1',
    enrichment: 'full', poll_interval_secs: 3600, poll_every_secs: 3600, item_type: 'bookmark', enabled: true,
    health_status: 'ok', last_error_summary: '', last_escalations: [], last_new_count: 2,
    last_poll_at: new Date().toISOString(), enrolled: true,
    remediation: { kind: '', guidance: '', detail: '', action: '' },
    ...over,
  }
}

beforeEach(() => {
  resetDataStore(); resetGateway()
  answers.knowledgeIntents = { intents: [] }
  answers.researchReports = { reports: [] }
})
afterEach(cleanup)

/** The feed's row, in the regular view or with the Power user view's lines. */
async function renderRow(over: Partial<WatchedSource> = {}, detail = false) {
  const s = source(over)
  answers.knowledgeSources = { sources: [s], kinds: KINDS, health_statuses: [], raw_enrichment: 'raw' }
  render(<SetupPanel nav={{ form: vi.fn(), item: vi.fn() }} onClose={vi.fn()} detail={detail} />)
  const name = await screen.findByText(s.name)
  return within(name.closest('div.rounded-lg') as HTMLElement)
}

describe('the no AI line is a readout of the source\'s enrichment', () => {
  it('🔑 a raw source says its items never reach a model', async () => {
    const row = await renderRow({ enrichment: 'raw' }, true)
    expect(row.getByText(/^No AI: .*never reach a model/)).toBeTruthy()
  })

  it('🔴 is ABSENT on an enriched source', async () => {
    // The falsifying half: make the line unconditional and this is the only test that reds.
    const row = await renderRow({ enrichment: 'full' }, true)
    expect(row.queryByText(/^No AI/), 'a full-enrichment source must not claim no AI').toBeNull()
  })
})

describe('the two remediations are opposite and never collapse into one', () => {
  const RENDER_GUIDANCE = 'This page builds its content with JavaScript, so a plain fetch sees an empty shell. Set budget.allow_render to true to let this source use the render tier.'
  const LISTING_GUIDANCE = 'No items found. Auto-detection reads LISTING pages — a changelog, a blog index, a category/tag/archive page, a newsroom. It cannot read a homepage or a single post.'

  it('🔑 a JS shell offers the render-tier knob as a button and no URL field', async () => {
    const row = await renderRow({
      health_status: HEALTH_NEEDS_RENDER,
      remediation: { kind: 'render_tier', guidance: RENDER_GUIDANCE, detail: '', action: 'allow_render' },
    })
    expect(row.getByRole('button', { name: 'Allow the render tier' })).toBeTruthy()
    expect(row.getByText(RENDER_GUIDANCE)).toBeTruthy()
    expect(row.queryByRole('textbox'), 'a JS shell is not fixed by a different URL').toBeNull()
  })

  it('🔑 a wrong URL offers a URL field and no render-tier button', async () => {
    const row = await renderRow({
      health_status: 'degraded',
      remediation: { kind: 'listing_page', guidance: LISTING_GUIDANCE, detail: '', action: 'edit_url' },
    })
    expect(row.getByRole('textbox', { name: /Listing-page URL for Product changelog/ })).toBeTruthy()
    expect(row.getByText(LISTING_GUIDANCE)).toBeTruthy()
    expect(row.queryByRole('button', { name: 'Allow the render tier' }), 'a wrong URL is not fixed by a render tier').toBeNull()
  })

  it('neither is offered on a healthy source', async () => {
    const row = await renderRow()
    expect(row.queryByRole('textbox')).toBeNull()
    expect(row.queryByRole('button', { name: 'Allow the render tier' })).toBeNull()
  })

  it("an already-allowed render tier shows the check's own reason and no knob", async () => {
    const row = await renderRow({
      health_status: HEALTH_NEEDS_RENDER,
      budget: { allow_render: true },
      remediation: { kind: 'render_tier', guidance: RENDER_GUIDANCE, detail: 'render tier unavailable; install personalclaw[js-render]', action: '' },
    })
    expect(row.getByText(/install personalclaw\[js-render\]/)).toBeTruthy()
    expect(row.queryByRole('button', { name: 'Allow the render tier' }), 'a button that re-sets a flag already set lies about what pressing it does').toBeNull()
  })
})

describe('what the row says whatever the view', () => {
  it('🔑 says plainly when nothing is set up to check its kind, before it was ever checked', async () => {
    // The engine records this as a health error only once it has RUN, so a never-checked row with
    // no provider would otherwise read as fine.
    const row = await renderRow({ enrolled: false, last_poll_at: null })
    expect(row.getByText('Nothing is set up to check this kind, so it brings nothing in.')).toBeTruthy()
    expect(row.getByText(/not checked yet/)).toBeTruthy()
  })

  it('names its switch after the source, so N switches are told apart', async () => {
    await renderRow()
    expect(screen.getByRole('switch', { name: 'Watching: Product changelog' })).toBeTruthy()
  })
})

describe("the Power user view's lines", () => {
  it('🔑 names its kind, its health in words and what that health means', async () => {
    const row = await renderRow({ health_status: 'degraded' }, true)
    expect(row.getByText(/^Watched Page · checked every 1 hr .*· Degraded$/)).toBeTruthy()
    expect(row.getByText(`Degraded: ${HEALTH_META.degraded.hint}`)).toBeTruthy()
  })

  it('does not report a health the source has not earned', async () => {
    // `sources.health_status` DEFAULTS to `ok` in the store, so a source saved seconds ago read
    // "Healthy · never polled" in one breath.
    const row = await renderRow({ last_poll_at: null, health_status: 'ok' }, true)
    expect(row.getByText(/^Watched Page · checked every 1 hr · not checked yet$/)).toBeTruthy()
    expect(row.queryByText(/Healthy/)).toBeNull()
  })

  it('states when the next check runs, since the health will not move until then', async () => {
    // MID-bucket, not a boundary: `relFuture` floors to whole minutes, so +30 s is unambiguous.
    const row = await renderRow({ next_poll_at: new Date(Date.now() + 20 * 60_000 + 30_000).toISOString() }, true)
    expect(row.getByText(/next in 20m/)).toBeTruthy()
  })

  it('promises no next check for a paused source', async () => {
    const row = await renderRow({ enabled: false, next_poll_at: new Date(Date.now() + 15 * 60_000).toISOString() }, true)
    expect(row.queryByText(/next in/)).toBeNull()
  })

  it('shows the costlier reads its last check had to make', async () => {
    const row = await renderRow({ last_escalations: ['escalated to render tier; extracted 3 item(s)'] }, true)
    expect(row.getByText(/escalated to render tier/)).toBeTruthy()
  })

  it('🔑 states the cadence the engine keeps, and why it is not the one chosen', async () => {
    // Measured: a source set to every 5 minutes said "every 5 min" while the engine, holding it to the
    // network floor, checked it every 15.
    const row = await renderRow({ poll_interval_secs: 300, poll_every_secs: 900 }, true)
    expect(row.getByText(/checked every 15 min/)).toBeTruthy()
    expect(row.getByText(/^Set to every 5 min, .* at most every 15 min/)).toBeTruthy()
  })

  it('adds no explanation when the chosen cadence is the one kept', async () => {
    const row = await renderRow({ poll_interval_secs: 300, poll_every_secs: 300 }, true)
    expect(row.queryByText(/^Set to every/)).toBeNull()
  })

  it('🔴 are none of them drawn in the regular view', async () => {
    const row = await renderRow({ enrichment: 'raw', health_status: 'degraded', last_escalations: ['escalated to render tier'] })
    expect(row.queryByText(/^No AI|Watched Page|Degraded|escalated/)).toBeNull()
  })
})

describe("a watched folder's first scan is stated on its row", () => {
  const folder = (over: Partial<WatchedSource>) => renderRow({ provider: 'watched-dir', kind: 'dir', spec: { path: '/home/me/Notes' }, ...over })

  it('says how many files are still waiting to be read in', async () => {
    const row = await folder({ first_scan: { found: 120, left_out: 0, waiting: 70 } })
    expect(row.getByText('70 more files are waiting to be read in.')).toBeTruthy()
  })

  it("says which files the scan's bound left for later", async () => {
    const row = await folder({ first_scan: { found: 1500, left_out: 500, waiting: 0 } })
    expect(row.getByText('The first scan took 1000 of 1500 files, the newest first; the other 500 come in when they change.')).toBeTruthy()
  })

  it('says nothing once the scan is complete', async () => {
    const row = await folder({ first_scan: { found: 12, left_out: 0, waiting: 0 } })
    expect(row.queryByText(/waiting to be read in|first scan/)).toBeNull()
  })

  it('says how many links lead outside the folder, and why they are not read in', async () => {
    const row = await folder({ links_outside: 2 })
    expect(row.getByText('2 links here lead outside this folder, so what they point to is not read in: a folder source reads only what is inside it.')).toBeTruthy()
  })

  it('says nothing about links when none lead out', async () => {
    const row = await folder({ links_outside: 0 })
    expect(row.queryByText(/outside this folder/)).toBeNull()
  })

  it('the create form states the bound the scan applies', () => {
    expect(firstScanPromise({ first_scan_max_files: 1000, first_scan_max_bytes: 100 * 1024 * 1024 }))
      .toBe('Its first check reads in what is already there, newest first — up to 1,000 files or 100 MB. The rest come in when they change.')
    expect(firstScanPromise({}), 'no bound shipped, no promise made').toBe('')
  })
})

describe('the status vocabulary cannot drift between the map and its named constant', () => {
  it('HEALTH_META has an entry for the constant the row branches on', () => {
    expect(HEALTH_META[HEALTH_NEEDS_RENDER]).toBeTruthy()
  })

  it('an unrecognised status is labelled honestly rather than rendered as healthy', () => {
    const meta = healthMeta('something the backend added later')
    expect(meta.label).toBe('Unknown')
    expect(meta.tone).not.toBe('ok')
  })
})
