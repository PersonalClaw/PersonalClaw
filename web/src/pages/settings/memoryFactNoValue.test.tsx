/**
 * A record that holds no value says so — it does not read as the word "null".
 *
 * The Studio listed "user.procedural.5aba2b4228aa null": the row's stored value was a JSON `null`,
 * and the list's preview line (and the inspector's Value box) printed the serializer's spelling of
 * "nothing" as if it were the record's content. Driven through the real panel, because the defect
 * was text on a surface.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import type { SemanticEntry } from '../../lib/api'

const FACTS: SemanticEntry[] = [
  { key: 'user.procedural.5aba2b4228aa', value_json: 'null', source: 'procedural', confidence: 0.85 },
  { key: 'user.favorite_language', value_json: '"Python"', source: 'user_explicit', confidence: 1 },
  { key: 'user.nickname', value_json: '"null"', source: 'user_explicit', confidence: 1 },
]

async function mountStudio() {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        memoryStats: () => Promise.resolve(null),
        memorySemantic: () => Promise.resolve(FACTS),
        memoryEpisodic: () => Promise.resolve([]),
        lessons: () => Promise.resolve([]),
        memoryGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntityGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntities: () => Promise.resolve({ entities: [], summary: {}, enabled: true }),
        memorySlots: () => Promise.resolve({ slots: [] }),
        memoryEntityProposals: () => Promise.resolve({ proposals: [], enabled: true }),
      },
    }
  })
  const { MemoryPanel } = await import('./MemoryPanel')
  render(<MemoryPanel query={{}} setQuery={() => {}} />)
  await waitFor(() => expect(screen.getByText('user.procedural.5aba2b4228aa')).toBeTruthy())
}

/** The explorer row that carries *key*: its title and its preview line. */
const rowOf = (key: string) => screen.getByText(key).closest('button, [role="option"], li') as HTMLElement

describe('a record with no value', () => {
  afterEach(() => { cleanup(); vi.resetModules(); vi.doUnmock('../../lib/api') })

  it('reads "No value" in the list and in the inspector, never "null"', async () => {
    await mountStudio()
    const row = rowOf('user.procedural.5aba2b4228aa')
    expect(row.textContent).toContain('No value')
    expect(row.textContent).not.toContain('null')
    fireEvent.click(screen.getByText('user.procedural.5aba2b4228aa'))
    await waitFor(() => expect(screen.getAllByText('No value').length).toBeGreaterThan(1))
  })

  it('still shows a value that is really there, including the text "null" someone stored', async () => {
    await mountStudio()
    expect(rowOf('user.favorite_language').textContent).toContain('Python')
    expect(rowOf('user.nickname').textContent).toContain('null')
    expect(rowOf('user.nickname').textContent).not.toContain('No value')
  })
})
