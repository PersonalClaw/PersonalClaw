import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { RelatedSection } from './KnowledgeDetailPage'
import type { KnowledgeItem } from '../../lib/api'

afterEach(cleanup)

// `related` rows are KnowledgeItems carrying the KL-13 cosine-similarity `score` (and, on a
// pre-edge response, the legacy `shared_entities`). The type declares neither as a required
// field, so — like readerInsightRail.test.tsx — the fixture casts.
const rel = (over: Partial<KnowledgeItem> & Record<string, unknown>): KnowledgeItem =>
  ({ id: 'k2', title: 'A neighbouring note', ...over } as unknown as KnowledgeItem)

// ── KL-16 clause 5: related rows show a similarity %, and the retrieval score is surfaced ──────────
//
// KL-13 replaced the unthresholded shared-entity COUNT with a cosine-similarity edge, so the row
// badge must read the retrieval `score` the API already returns and render it as a percentage — the
// count is only the fallback for a response that predates the edge table. The shipped code
// (KnowledgeDetailPage RelatedSection) does this; this pins the score→% branch, which no other test
// exercised (readerInsightRail.test.tsx uses the shared_entities fallback).
describe('RelatedSection surfaces the retrieval score as a similarity percentage', () => {
  it('renders "N%" from the score, and NOT a shared-entity count, when a score is present', () => {
    render(<RelatedSection related={[rel({ score: 0.87, shared_entities: 3 })]} onOpenItem={() => {}} />)
    expect(screen.getByText('87%')).toBeTruthy()
    // The score wins: the "3 shared" fallback chip must not render alongside it.
    expect(screen.queryByText('3 shared')).toBeNull()
    // The full retrieval score + entity overlap ride the tooltip, so the number stays accountable.
    expect(screen.getByTitle(/87% similar/)).toBeTruthy()
  })

  it('falls back to the shared-entity count only when there is no score', () => {
    render(<RelatedSection related={[rel({ shared_entities: 3 })]} onOpenItem={() => {}} />)
    expect(screen.getByText('3 shared')).toBeTruthy()
    expect(screen.queryByText(/%$/)).toBeNull()
  })
})
