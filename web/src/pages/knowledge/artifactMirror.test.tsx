import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { answers, resetGateway } from '../../test/libraryHarness'
import { ARTIFACT_TYPE, isArtifactItem, resolveType, typeLabel, TYPES } from './knowledgeMeta'
import { eventDrivenMetaLine } from './sourceMeta'
import type { WatchedSource } from '../../lib/api'

vi.mock('../../lib/api', async (orig) => {
  const { gatewayApi } = await import('../../test/libraryHarness')
  return { ...(await orig<typeof import('../../lib/api')>()), api: gatewayApi() }
})
const { SetupPanel } = await import('../library/setup/SetupPanel')
const { resetDataStore } = await import('../../lib/data/store')

// ── What the two artifact-mirror surfaces must NOT say ────────────────────────────────
//
// Both halves of this file are negative assertions, because both defects are of the shape
// that every positive test passes right through:
//
// 1. A feed's set-up row describes a POLLER. An event-driven source drawn with that vocabulary
//    reads "Nothing is set up to check this kind · not checked yet · every 1 hr", and the loudest
//    of those tells the user a mechanism that is working perfectly is broken. Every "the row
//    renders" assertion passes in that state. The mirror is a row in the Power user view only:
//    it is set in Settings, so the regular view lists the feeds you set up.
// 2. A mirrored artifact is not a note. Before `ARTIFACT_TYPE` existed, `resolveType` fell
//    through its `mime`/`url` cascade to `note`, so an artifact search hit rendered a
//    StickyNote labelled "Note": present, plausible, and wrong about what was found.

const KINDS = [{ provider: 'watched-page', display_name: 'Watched Page', kind: 'web_page', form: 'web_page', previewable: true, poll_interval_secs: 3600 }]

function source(over: Partial<WatchedSource> = {}): WatchedSource {
  return {
    id: 'src-art', name: 'Artifacts', provider: 'artifacts', kind: 'artifact',
    spec: { uri: 'artifact://' }, budget: {}, revision: 'r-src-art',
    enrichment: 'raw', poll_interval_secs: 0, poll_every_secs: 3600, item_type: 'artifact', enabled: true,
    health_status: 'ok', last_error_summary: '', last_escalations: [], last_new_count: 0,
    last_poll_at: null, enrolled: false, event_driven: true,
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

/** The source's set-up row, in the Power user view unless `detail` is false. */
async function renderRow(over: Partial<WatchedSource> = {}, detail = true) {
  const s = source(over)
  answers.knowledgeSources = { sources: [s], kinds: KINDS, health_statuses: [], raw_enrichment: 'raw' }
  render(<SetupPanel nav={{ form: vi.fn(), item: vi.fn() }} onClose={vi.fn()} detail={detail} />)
  if (!detail) { await screen.findByText('Feeds, folders and apps'); return null }
  const name = await screen.findByText(s.name)
  return within(name.closest('div.rounded-lg') as HTMLElement)
}

describe('an event-driven source is not described as a broken poller', () => {
  it('does NOT claim nothing will collect anything', async () => {
    const row = (await renderRow())!
    expect(row.queryByText(/Nothing is set up to check/), 'the mirror is fed by a listener, not a check').toBeNull()
  })

  it('still says so for a genuinely orphaned poller', async () => {
    // The vacuity guard: without this, saying nothing for EVERY unenrolled row would satisfy the
    // assertion above while hiding a real orphan.
    const row = (await renderRow({ provider: 'watched-page', event_driven: false, enrolled: false }))!
    expect(row.getByText('Nothing is set up to check this kind, so it brings nothing in.')).toBeTruthy()
  })

  it('does not report a check or a health it never earned', async () => {
    const row = (await renderRow())!
    expect(row.queryByText(/not checked yet|Healthy/)).toBeNull()
  })

  it('says how it IS fed, and where its one switch lives', async () => {
    const row = (await renderRow())!
    expect(row.getByText(eventDrivenMetaLine())).toBeTruthy()
    expect(eventDrivenMetaLine()).toMatch(/Settings → Automations → Watched sources/)
  })

  it('does not open by repeating the row title as a lowercase provider name', async () => {
    // The kinds catalogue holds poll-capable providers only, so this row has no entry, and a line
    // that opened with the raw provider string put "artifacts ·" directly under the row's own
    // "Artifacts", the same word twice in two casings.
    const row = (await renderRow())!
    expect(row.queryByText(/^artifacts/)).toBeNull()
    expect(eventDrivenMetaLine().split(' · ')[0]).toBe('Indexed as artifacts change')
  })

  it('offers no switch, because pausing the row would do nothing', async () => {
    // The row's `enabled` column is not what the mirror reads (`knowledge.auto_ingest_artifacts` is).
    // A switch here would save and change nothing, which is worse than none.
    await renderRow()
    expect(screen.queryByRole('switch', { name: /Artifacts/ })).toBeNull()
  })

  it('keeps the switch for a real poller', async () => {
    await renderRow({ name: 'Changelog', provider: 'watched-page', event_driven: false, enrolled: true })
    expect(screen.getByRole('switch', { name: 'Watching: Changelog' })).toBeTruthy()
  })

  it('is no row in the regular view, which lists the feeds you set up', async () => {
    await renderRow({}, false)
    expect(screen.queryByText('Artifacts')).toBeNull()
  })

  it('🔴 is no row there, yet it adds on its own: the regular view never says nothing does', async () => {
    await renderRow({}, false)
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]'), 'every read is in').toBeNull())
    expect(screen.queryByText('Nothing adds on its own yet.'), 'the mirror is on').toBeNull()
  })

  it('with the mirror off and nothing else set up, it says nothing adds', async () => {
    await renderRow({ enabled: false }, false)
    expect(await screen.findByText('Nothing adds on its own yet.')).toBeTruthy()
  })
})

describe('a mirrored artifact reads as an artifact', () => {
  it('resolves to the artifact type rather than falling through to note', () => {
    expect(resolveType({ item_type: 'artifact' })).toBe(ARTIFACT_TYPE)
    expect(typeLabel({ item_type: 'artifact' })).toBe('Artifact')
  })

  it('is NOT offered by the create picker', () => {
    // `TYPES` is the create page's catalog. An artifact is mirrored from the Artifacts
    // library, never authored in Knowledge, so an entry there would be a creatable type
    // whose create call the backend refuses.
    expect(TYPES.find((t) => t.key === 'artifact')).toBeUndefined()
  })

  it('recognises the mirror from either the vision type or the raw item_type', () => {
    expect(isArtifactItem({ type: 'artifact' })).toBe(true)
    expect(isArtifactItem({ item_type: 'ARTIFACT' })).toBe(true)
    expect(isArtifactItem({ item_type: 'note' })).toBe(false)
    expect(isArtifactItem({})).toBe(false)
  })
})
