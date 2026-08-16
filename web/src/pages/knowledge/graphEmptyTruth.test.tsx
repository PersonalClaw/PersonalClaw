import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { KnowledgeGraph } from './KnowledgeGraph'

// ── The Graph tab's two empty states were exactly inverted ────────────────────────────────────
//
// Measured against a home seeded from the `demo-home` fixture, with knowledge items authored
// through the API:
//
//   6 items, 0 entities → "No entities extracted yet. **Add documents to build the graph.**"
//   0 items             → the panel rendered NOTHING below the stat chips (116 chars of panel text,
//                         against 311 on the Library tab, which shows the real empty state)
//
// So the instruction to add documents appeared ONLY to users who had already added documents, and
// the state where it would have been true showed a void. The cause is a pair of gates:
// `KnowledgeListPage` renders the graph only when `!empty` (`stats.items > 0`), and gated the shared
// empty-state block on `view !== 'graph'` — which excluded the one view that needed it.
//
// 🔑 WHAT THE 0-NODE STATE ACTUALLY MEANS, therefore: "items exist, entities do not." What is
// missing is the enrichment pass — and its header control is `view === 'library'`-only, so from this
// tab it is off screen. The empty state carries the action rather than pointing at an invisible
// button, through the `EmptyState` primitive that every other empty state on the page already uses.

const SRC = (rel: string) => readFileSync(join(process.cwd(), 'src/pages/knowledge', rel), 'utf8')

describe('the graph empty state tells the truth about why it is empty', () => {
  const original = globalThis.fetch

  beforeEach(() => {
    globalThis.fetch = vi.fn(async () => ({ json: async () => ({ nodes: [], edges: [] }) })) as never
  })
  afterEach(() => { globalThis.fetch = original })

  it('says entities are missing, and never tells the user to add documents', async () => {
    render(<KnowledgeGraph />)
    await waitFor(() => expect(screen.getByText('No entities extracted yet')).toBeTruthy())
    // The defect, pinned: this view only renders when items already exist.
    expect(document.body.textContent).not.toMatch(/Add documents/i)
  })

  it('carries the enrichment action itself, because its header control is another tab away', async () => {
    const onRegenerate = vi.fn()
    render(<KnowledgeGraph onRegenerate={onRegenerate} />)
    const btn = await waitFor(() => screen.getByRole('button', { name: /Regenerate intelligence/i }))
    expect(btn).toBeTruthy()
    ;(btn as HTMLButtonElement).click()
    expect(onRegenerate).toHaveBeenCalledTimes(1)
  })

  it('shows progress instead of inviting a second run while one is in flight', async () => {
    render(<KnowledgeGraph onRegenerate={() => {}} regenerating />)
    await waitFor(() => expect(screen.getByText(/Extracting…/)).toBeTruthy())
  })

  it('omits the action when no handler is supplied rather than rendering a dead button', async () => {
    render(<KnowledgeGraph />)
    await waitFor(() => expect(screen.getByText('No entities extracted yet')).toBeTruthy())
    expect(screen.queryByRole('button', { name: /Regenerate intelligence/i })).toBeNull()
  })

  it('goes through the EmptyState primitive, not a hand-rolled centered div', () => {
    const src = SRC('KnowledgeGraph.tsx')
    expect(src).toMatch(/import \{ EmptyState \}/)
    expect(src, 'the hand-rolled empty div is gone').not.toMatch(/place-items-center text-on-surface-low text-\[0\.8125rem\]/)
  })
})

describe('the parent reaches the shared empty state from every view', () => {
  const src = SRC('KnowledgeListPage.tsx')

  it('the empty-state block is no longer excluded from the graph view', () => {
    // Vacuity floor for this assertion: the gate must still exist at all, in the shape we changed.
    expect(src, "the graph view's own render is still gated on !empty").toMatch(/view === 'graph' && !empty/)
    expect(src, 'and the shared empty block now admits it').toMatch(/\(view !== 'graph' \|\| empty\) &&/)
  })

  it('hands the enrichment action down, since the header control is library-only', () => {
    expect(src).toMatch(/onRegenerate=\{regenerate\}/)
    expect(src).toMatch(/regenerating=\{regenning\}/)
    // The reason the hand-down is needed — if this ever stops being library-only, revisit.
    expect(src, 'header control is still library-only').toMatch(/view === 'library' && \(items\?\.length \?\? 0\) > 0/)
  })
})
