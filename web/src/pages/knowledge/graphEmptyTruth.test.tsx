import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { KnowledgeGraph, graphEmptyCopy } from './KnowledgeGraph'

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
    // `ok: true` matters MORE here than anywhere: this file's whole subject is what the empty state
    // is allowed to claim, and #532 is the discovery that a FAILED read used to reach it. The double
    // must therefore be an unambiguous 200 — a genuinely empty graph — or the file would be asserting
    // the empty copy against the very failure the component now refuses to render it for.
    globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ nodes: [], edges: [] }) })) as never
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
    // Matched inside the named-import list rather than against a brace-exact `{ EmptyState }`, which
    // was only ever true while EmptyState was the file's SOLE import from ListScaffold — #532 added
    // `LoadError` beside it and the exact form went red on a file that had not stopped going through
    // the primitive. The module path is now pinned too, so this is a tighter claim than it replaces:
    // a hand-rolled local `EmptyState` would no longer satisfy it.
    expect(src).toMatch(/import \{[^}]*\bEmptyState\b[^}]*\} from '\.\.\/\.\.\/ui\/ListScaffold'/)
    expect(src, 'the hand-rolled empty div is gone').not.toMatch(/place-items-center text-on-surface-low text-\[0\.8125rem\]/)
  })
})

// ── …and says WHICH truth, from the library's enrichment tally (B6, day-7 live validation) ─────────
//
// On a home with no model bound, every item had been through entity extraction and FAILED — each
// item's page showed Entities ✕ — while this state said "Your items have not been through entity
// extraction" and offered a run that failed the same way, silently. Never-tried, tried-and-failed,
// running and ran-found-nothing are different facts; `stats.enrichment` carries which one this is.

const tally = (over: Partial<{ ran: number; failed: number; running: number; skipped: number; not_run: number }>) =>
  ({ ran: 0, failed: 0, running: 0, skipped: 0, not_run: 0, ...over })

describe('the graph empty state names the cause from the enrichment tally', () => {
  const original = globalThis.fetch

  beforeEach(() => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ nodes: [], edges: [] }) })) as never
  })
  afterEach(() => { globalThis.fetch = original })

  it('tried and failed with no model: says so, and offers the fix that can work', async () => {
    const onRegenerate = vi.fn()
    const onSetupModel = vi.fn()
    render(<KnowledgeGraph onRegenerate={onRegenerate} onSetupModel={onSetupModel}
      enrichment={{ model_available: false, entities: tally({ failed: 2 }) }} />)
    await waitFor(() => expect(screen.getByText('Entity extraction failed')).toBeTruthy())
    const text = document.body.textContent ?? ''
    expect(text).toMatch(/It ran on 2 items and failed because no model is set up/)
    // The defect's exact claim, gone.
    expect(text).not.toMatch(/have not been through entity extraction/)
    // Regenerate cannot succeed without a model (the route refuses), so it is not what is offered.
    expect(screen.queryByRole('button', { name: /Regenerate intelligence/i })).toBeNull()
    ;(screen.getByRole('button', { name: /Connect a model/i }) as HTMLButtonElement).click()
    expect(onSetupModel).toHaveBeenCalledTimes(1)
    expect(onRegenerate).not.toHaveBeenCalled()
  })

  it('tried and failed with a model now bound: offers to run it again', async () => {
    const onRegenerate = vi.fn()
    render(<KnowledgeGraph onRegenerate={onRegenerate} onSetupModel={() => {}}
      enrichment={{ model_available: true, entities: tally({ failed: 1 }) }} />)
    await waitFor(() => expect(screen.getByText('Entity extraction failed')).toBeTruthy())
    expect(document.body.textContent).toMatch(/It ran on 1 item and failed — the model was unavailable/)
    ;(screen.getByRole('button', { name: /Regenerate intelligence/i }) as HTMLButtonElement).click()
    expect(onRegenerate).toHaveBeenCalledTimes(1)
  })

  it('never tried: the old sentence, now only where it is true', async () => {
    render(<KnowledgeGraph onRegenerate={() => {}}
      enrichment={{ model_available: true, entities: tally({ not_run: 3 }) }} />)
    await waitFor(() => expect(screen.getByText('No entities extracted yet')).toBeTruthy())
    expect(document.body.textContent).toMatch(/3 items have not been through entity extraction yet/)
    expect(screen.getByRole('button', { name: /Regenerate intelligence/i })).toBeTruthy()
  })

  it('running: shows progress, not an invitation to start another run', async () => {
    render(<KnowledgeGraph onRegenerate={() => {}}
      enrichment={{ model_available: true, entities: tally({ running: 2, failed: 1 }) }} />)
    await waitFor(() => expect(screen.getByText('Extracting entities…')).toBeTruthy())
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('ran and found nothing, or skipped by design: no action that cannot change the answer', async () => {
    const { unmount } = render(<KnowledgeGraph onRegenerate={() => {}}
      enrichment={{ model_available: true, entities: tally({ ran: 4 }) }} />)
    await waitFor(() => expect(screen.getByText('No entities found')).toBeTruthy())
    expect(screen.queryByRole('button')).toBeNull()
    unmount()
    render(<KnowledgeGraph onRegenerate={() => {}}
      enrichment={{ model_available: true, entities: tally({ skipped: 2 }) }} />)
    await waitFor(() => expect(screen.getByText('No entities to draw')).toBeTruthy())
    expect(document.body.textContent).toMatch(/set to skip AI enrichment/)
  })

  it('re-reads the graph when the parent says the counts moved', async () => {
    const { rerender } = render(<KnowledgeGraph reloadKey="0:0" />)
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(1))
    rerender(<KnowledgeGraph reloadKey="0:0" />)
    rerender(<KnowledgeGraph reloadKey="5:3" />)
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(2))
  })

  it('graphEmptyCopy claims nothing it cannot know when the tally is absent', () => {
    const copy = graphEmptyCopy(undefined)
    expect(copy.title).toBe('No entities extracted yet')
    expect(copy.hint).not.toMatch(/have not been through/)
    expect(copy.action).toBe('regenerate')
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
