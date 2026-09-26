import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { LexiconCorrection, LexiconTerm } from '../../lib/api'

// ── A learned speech correction can be deleted (Settings → Voice → Learned corrections) ───────────
//
// Each correction row had an Always/Suggest toggle and nothing else, and the backend had no route to
// remove one: a wrong fix set to Always rewrote that word in every dictation, and the only way out
// was Reset, which drops every term and every correction. The row now has a delete, named after the
// pair it removes, that asks first and says so when the delete fails. The term rows beside it had
// the same naming defect — "Delete" and "Disable (prune)" on every row — and are named per term now.

const CORRECTIONS: LexiconCorrection[] = [
  { id: 'corr_1', heard: 'cube control', meant: 'kubectl', count: 3, auto_apply: true, last_seen: '' },
  { id: 'corr_2', heard: 'post gress', meant: 'postgres', count: 1, auto_apply: false, last_seen: '' },
]
const TERMS: LexiconTerm[] = [
  { id: 'manual_kubernetes', canonical: 'Kubernetes', aliases: [], entity_type: 'manual', weight: 2, source: 'manual', enabled: true },
  { id: 'graph_redis', canonical: 'Redis', aliases: [], entity_type: 'tool', weight: 1, source: 'graph', enabled: true },
]

let remaining: LexiconCorrection[]
let deleteCorrection: Mock<(id: string) => Promise<{ ok: boolean }>>
let confirmDelete: Mock<(entity: string, name?: string) => Promise<boolean>>
let notified: string[]

async function mountVoice(over: { deleteFails?: boolean; confirmed?: boolean } = {}) {
  remaining = [...CORRECTIONS]
  notified = []
  deleteCorrection = vi.fn(async (id: string) => {
    if (over.deleteFails) throw new Error('lexicon.db is locked')
    remaining = remaining.filter((c) => c.id !== id)
    return { ok: true }
  })
  confirmDelete = vi.fn(() => Promise.resolve(over.confirmed ?? true))
  vi.doMock('../../app/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (msg: string) => { notified.push(msg) },
  }))
  vi.doMock('../../ui/dialog', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    confirmDelete,
  }))
  vi.doMock('../../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      useCaseSettings: () => Promise.resolve({ enabled: false }),
      modelsActive: () => Promise.resolve({}),
      personalclawConfig: () => Promise.resolve({}),
      voiceLoopConfig: () => Promise.resolve({}),
      voiceProfiles: () => Promise.resolve({ profiles: [], bindings: {} }),
      voiceResolve: () => Promise.resolve({ surface: '', resolved: true, level: 'built-in' }),
      lexiconTerms: () => Promise.resolve({ terms: TERMS, total: TERMS.length }),
      lexiconCorrections: () => Promise.resolve({ corrections: remaining }),
      lexiconDeleteCorrection: (id: string) => deleteCorrection(id),
    },
  }))
  const { VoicePanel } = await import('./VoicePanel')
  render(<VoicePanel go={() => {}} />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('a learned correction can be deleted', () => {
  it('🔴 each correction has a delete named after its pair, which asks first and removes it', async () => {
    await mountVoice()
    const del = await screen.findByRole('button', { name: 'Delete correction cube control → kubectl' })
    expect(screen.getByRole('button', { name: 'Delete correction post gress → postgres' })).toBeTruthy()
    fireEvent.click(del)
    await waitFor(() => expect(deleteCorrection).toHaveBeenCalledWith('corr_1'))
    expect(confirmDelete).toHaveBeenCalledWith('correction', 'cube control → kubectl')
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Delete correction cube control → kubectl' })).toBeNull())
    expect(screen.getByRole('button', { name: 'Delete correction post gress → postgres' })).toBeTruthy()
    expect(notified).toEqual([])
  })

  it('declining the dialog deletes nothing', async () => {
    await mountVoice({ confirmed: false })
    fireEvent.click(await screen.findByRole('button', { name: 'Delete correction cube control → kubectl' }))
    await waitFor(() => expect(confirmDelete).toHaveBeenCalled())
    expect(deleteCorrection).not.toHaveBeenCalled()
  })

  it('a failed delete says so and leaves the row to retry from', async () => {
    await mountVoice({ deleteFails: true })
    fireEvent.click(await screen.findByRole('button', { name: 'Delete correction cube control → kubectl' }))
    await waitFor(() => expect(notified.some((m) => m.includes('lexicon.db is locked'))).toBe(true))
    expect(notified[0]).toContain('cube control → kubectl')
    expect(screen.getByRole('button', { name: 'Delete correction cube control → kubectl' })).toBeTruthy()
  })
})

describe('each vocabulary term row names the term it acts on', () => {
  it('the delete and the prune toggle carry the term', async () => {
    await mountVoice()
    expect(await screen.findByRole('button', { name: 'Delete term Kubernetes' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Delete term Redis' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Disable (prune) Kubernetes' })).toBeTruthy()
    expect(screen.queryAllByRole('button', { name: 'Delete' }), 'no bare shared name').toHaveLength(0)
  })
})
