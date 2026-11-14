/**
 * A lesson shows no category in the Memory studio, and one added there is sent without one.
 *
 * 🔴 Before: no lesson stores a category, yet `GET /api/lessons` answered "knowledge" for every
 * lesson, so the inspector's Category row read "knowledge" whatever had been taught. The studio's
 * add sent "knowledge" with each new lesson, which the route now refuses.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import type { Lesson } from '../../lib/api'

const RULE = 'Answer billing questions with the invoice number first.'
// The row as the route answers it, with no category.
const LESSONS: Lesson[] = [
  { rule: RULE, ts: '2026-11-12T09:00:00Z', standing: 'injected', confidence: 1, confidence_reason: 'You taught it.' },
]

async function mountStudio() {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        memoryStats: () => Promise.resolve(null),
        memorySemantic: () => Promise.resolve([]),
        memoryEpisodic: () => Promise.resolve([]),
        lessons: () => Promise.resolve(LESSONS),
        memoryGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntityGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntities: () => Promise.resolve({ entities: [], summary: {}, enabled: true }),
        memorySlots: () => Promise.resolve({ slots: [] }),
        memoryEntityProposals: () => Promise.resolve({ proposals: [], enabled: true }),
        memoryRecordLinks: () => Promise.resolve({ links: [] }),
      },
    }
  })
  const { MemoryPanel } = await import('./MemoryPanel')
  render(<MemoryPanel query={{}} setQuery={() => {}} />)
  await waitFor(() => expect(screen.getByText(RULE)).toBeTruthy())
}

beforeEach(() => {
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})
afterEach(() => { cleanup(); vi.resetModules(); vi.doUnmock('../../lib/api'); vi.unstubAllGlobals() })

describe('a lesson in the Memory studio', () => {
  it('is inspected with no category', async () => {
    await mountStudio()
    fireEvent.click(screen.getByText(RULE))
    await screen.findByText('Learned')
    expect(screen.queryByText('Category')).toBeNull()
    expect(screen.queryByText('knowledge')).toBeNull()
  })
})

describe('a lesson added from the studio', () => {
  it('is sent as its rule, with no category', async () => {
    const sent: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).includes('/api/lessons')) sent.push(JSON.parse(String(init?.body)))
      return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }))
    const { api } = await import('../../lib/api')
    await api.addLesson(RULE)
    expect(sent).toEqual([{ rule: RULE }])
  })
})
