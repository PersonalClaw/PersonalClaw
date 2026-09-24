import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SourceRow } from './SourcesPage'
import { ARTIFACT_TYPE, isArtifactItem, resolveType, typeLabel, TYPES } from './knowledgeMeta'
import { eventDrivenMetaLine } from './sourceMeta'
import type { WatchedSource } from '../../lib/api'

// ── PEP-7: what the two artifact-mirror surfaces must NOT say ─────────────────────────
//
// Both halves of this file are negative assertions, because both defects are of the shape
// that every positive test passes right through:
//
// 1. The Sources row describes a POLLER. An event-driven source rendered with that
//    vocabulary reads "No provider · never polled · every 1h" — and the loudest of those is
//    a DANGER chip telling the user a mechanism that is working perfectly is broken. Every
//    "the row renders" assertion passes in that state.
// 2. A mirrored artifact is not a note. Before `ARTIFACT_TYPE` existed, `resolveType` fell
//    through its `mime`/`url` cascade to `note`, so an artifact search hit rendered a
//    StickyNote labelled "Note" — present, plausible, and wrong about what was found.

const KINDS = {
  'artifacts': { display_name: 'Artifacts', form: 'artifact' },
  'watched-page': { display_name: 'Watched Page', form: 'web_page' },
}

function source(over: Partial<WatchedSource> = {}): WatchedSource {
  return {
    id: 'src-art', name: 'Artifacts', provider: 'artifacts', kind: 'artifact',
    spec: { uri: 'artifact://' }, budget: {},
    enrichment: 'raw', poll_interval_secs: 0, item_type: 'artifact', enabled: true,
    health_status: 'ok', last_error_summary: '', last_escalations: [], last_new_count: 0,
    last_poll_at: null, enrolled: false, event_driven: true,
    remediation: { kind: '', guidance: '', detail: '', action: '' },
    ...over,
  }
}

function renderRow(over: Partial<WatchedSource> = {}) {
  return render(<SourceRow source={source(over)} kinds={KINDS} onChanged={() => {}} />)
}

describe('an event-driven source is not described as a broken poller', () => {
  it('does NOT claim no provider will collect anything', () => {
    renderRow()

    expect(screen.queryByText('No provider'), 'the mirror is fed by a listener, not a poll').toBeNull()
  })

  it('still shows the danger chip for a genuinely orphaned poller', () => {
    // The vacuity guard: without this, suppressing the chip for EVERY unenrolled row would
    // satisfy the assertion above while hiding a real orphan.
    renderRow({ provider: 'watched-page', event_driven: false, enrolled: false })

    expect(screen.getByText('No provider')).toBeTruthy()
  })

  it('does not report a poll health it never earned', () => {
    renderRow()

    expect(screen.queryByText('Not polled yet')).toBeNull()
    expect(screen.queryByText('Healthy')).toBeNull()
  })

  it('says how it IS fed, and where its one switch lives', () => {
    renderRow()

    expect(screen.getByText(eventDrivenMetaLine())).toBeTruthy()
    expect(eventDrivenMetaLine()).toMatch(/Settings → Sources/)
  })

  it('does not open by repeating the row title as a lowercase provider name', () => {
    // Measured on the running page: `kinds` holds poll-capable providers only, so this row
    // had no entry and the line opened with the raw provider string — "artifacts ·" sitting
    // directly under the row's own "Artifacts" heading, the same word twice in two casings.
    renderRow()

    expect(eventDrivenMetaLine().startsWith('artifacts')).toBe(false)
    expect(eventDrivenMetaLine().split(' · ')[0]).toBe('Indexed as artifacts change')
  })

  it('offers no pause toggle, because pausing the row would do nothing', () => {
    // The row's `enabled` column is not what the mirror reads — `knowledge.auto_ingest_artifacts`
    // is. A toggle here would save successfully and change nothing, which is worse than absent.
    renderRow()

    expect(screen.queryByLabelText(/Pause Artifacts/)).toBeNull()
    expect(screen.queryByLabelText(/Resume Artifacts/)).toBeNull()
  })

  it('keeps the pause toggle for a real poller', () => {
    renderRow({ name: 'Changelog', provider: 'watched-page', event_driven: false, enrolled: true })

    expect(screen.getByLabelText('Pause Changelog')).toBeTruthy()
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
