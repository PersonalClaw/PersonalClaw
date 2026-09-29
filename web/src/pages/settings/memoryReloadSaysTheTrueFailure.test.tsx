/**
 * A failed Memory Studio read says what really happened: the server answered with an error, or it
 * did not answer at all.
 *
 * Memory Studio reloads every kind it lists after a save. One of those reads failed with a 500 — a
 * crash inside the handler, from a server that was up and answering — and the explorer said "The
 * server didn't respond". `LoadError` read every rejection it could not show that way, because the
 * 500's body is one it rightly will not print. The status is still a fact, and it is the true one.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { ApiError } from '../../lib/api'
import { LoadError } from '../../ui/ListScaffold'

async function mount(over: Record<string, unknown>) {
  vi.doMock('../../lib/api', async (orig) => {
    const real = await orig<Record<string, unknown>>()
    return {
      ...real,
      api: {
        ...(real.api as object),
        memoryStats: () => Promise.resolve(null),
        memorySemantic: () => Promise.resolve([]),
        memoryEpisodic: () => Promise.resolve([]),
        lessons: () => Promise.resolve([]),
        memoryGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntityGraph: () => Promise.resolve({ nodes: [], edges: [] }),
        memoryEntities: () => Promise.resolve({
          entities: [], summary: {}, enabled: true, ranking: { degraded: false, summary: '' },
        }),
        memorySlots: () => Promise.resolve({ slots: [] }),
        memoryEntityProposals: () => Promise.resolve({ proposals: [], enabled: true }),
        ...over,
      },
    }
  })
  const { MemoryPanel } = await import('./MemoryPanel')
  return render(<MemoryPanel query={{}} setQuery={() => {}} />)
}

beforeEach(() => {
  cleanup()
  vi.resetModules()
  sessionStorage.clear()
  Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})
afterEach(() => cleanup())

describe('Memory Studio, when a read fails', () => {
  it('a 500 is said as the answer it was, never as "the server didn\'t respond"', async () => {
    await mount({ memoryEntities: () => Promise.reject(new ApiError('HTTP 500', 500)) })
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/Couldn't load your memories/)
    expect(alert.textContent).toMatch(/The server answered with an error \(HTTP 500\)/)
    expect(alert.textContent, 'the server was up and answered').not.toMatch(/didn't respond/)
  })

  it('a fetch that never reached the server still says it did not respond', async () => {
    await mount({ memoryEntities: () => Promise.reject(new TypeError('Failed to fetch')) })
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toMatch(/The server didn't respond/)
  })
})

describe('LoadError tells an answer from no answer', () => {
  for (const [error, answered] of [
    [new ApiError('HTTP 500', 500), true],
    [new ApiError('HTTP 404', 404), true],
    // errEnvelope's own placeholder, thrown by a caller that kept only the sentence.
    [new Error('HTTP 500'), true],
    // What a proxy says when the server behind it did not answer.
    [new ApiError('HTTP 502', 502), false],
    [new ApiError('HTTP 503', 503), false],
    [new ApiError('HTTP 504', 504), false],
    [new TypeError('Failed to fetch'), false],
    [undefined, false],
  ] as [unknown, boolean][]) {
    it(`${error instanceof Error ? `${error.name} ${error.message}` : 'no error'} → ${answered ? 'answered' : 'no answer'}`, () => {
      render(<LoadError what="projects" error={error} />)
      const text = screen.getByRole('alert').textContent ?? ''
      expect(text).toMatch(answered ? /answered with an error \(HTTP \d{3}\)/ : /didn't respond/)
      expect(text).toMatch(/this is just a load error, and nothing was lost/)
    })
  }

  it('a message the server wrote still wins over both', () => {
    render(<LoadError what="projects" error={new ApiError('the project store is read-only', 500)} />)
    const text = screen.getByRole('alert').textContent ?? ''
    expect(text).toMatch(/the project store is read-only/)
    expect(text).not.toMatch(/answered with an error|didn't respond/)
  })
})
