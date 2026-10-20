/**
 * The Memory studio lists each lesson once, as a lesson.
 *
 * 🔴 Before: a lesson is stored as a semantic row keyed `lesson.<hash>`, and the studio took every
 * semantic row for a fact, so each lesson was listed twice — once as the lesson, with its
 * confidence and standing, and once as a raw `lesson.0d07ddd7ad40` fact whose value was the rule.
 * Measured with four lessons: All 80, Facts 39 (the four among them), Lessons 4.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, waitFor, cleanup, within } from '@testing-library/react'
import type { Lesson, SemanticEntry } from '../../lib/api'

const RULE = 'Talk notes are conversational; no jokes about Kafka.'
const FACTS: SemanticEntry[] = [
  { key: 'lesson.0d07ddd7ad40', value_json: JSON.stringify(RULE), source: 'user_explicit', confidence: 0.9 },
  { key: 'lesson.ws.721a7d005120', value_json: JSON.stringify('carrier-webhooks dedupes in Postgres.'), source: 'user_explicit', confidence: 0.9 },
  { key: 'user.favorite_language', value_json: '"Python"', source: 'user_explicit', confidence: 1 },
]
const LESSONS: Lesson[] = [
  { rule: RULE, category: 'knowledge', ts: '2026-09-30T21:00:00Z' },
  { rule: 'carrier-webhooks dedupes in Postgres.', category: 'knowledge', ts: '2026-09-30T21:05:00Z' },
]

async function mount() {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        memoryStats: () => Promise.resolve(null),
        memorySemantic: () => Promise.resolve(FACTS),
        memoryEpisodic: () => Promise.resolve([]),
        lessons: () => Promise.resolve(LESSONS),
        memoryGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntityGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntities: () => Promise.resolve({ entities: [], summary: {}, enabled: true }),
        memorySlots: () => Promise.resolve({ slots: [] }),
        memoryEntityProposals: () => Promise.resolve({ proposals: [], enabled: true }),
        memorySettings: () => Promise.resolve({ history_idle_hours: 2, history_max_days: 90 }),
        memoryVaultStatus: () => Promise.resolve(null),
        memoryFacets: () => Promise.resolve([]),
      },
    }
  })
  const { MemoryPanel } = await import('./MemoryPanel')
  render(<MemoryPanel query={{}} setQuery={() => {}} />)
}

beforeEach(() => {
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})
afterEach(() => { cleanup(); vi.resetModules(); vi.doUnmock('../../lib/api') })

const chip = (name: RegExp) => within(screen.getByRole('group', { name: 'Filter by kind' })).getByRole('button', { name })

describe('the Memory studio', () => {
  it('lists a lesson once, as the lesson, never as a raw lesson.<hash> fact', async () => {
    await mount()
    await waitFor(() => expect(screen.getByText(RULE)).toBeTruthy())

    expect(screen.getAllByText(RULE), 'the rule is on one row').toHaveLength(1)
    expect(screen.queryByText('lesson.0d07ddd7ad40')).toBeNull()
    expect(screen.queryByText('lesson.ws.721a7d005120')).toBeNull()
    expect(chip(/^Lessons/).textContent).toBe('Lessons2')
    expect(chip(/^Facts/).textContent, 'the one fact that is a fact').toBe('Facts1')
  })
})
