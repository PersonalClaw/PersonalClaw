import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

// ── A secret too short to mask is refused, and one saved before the floor is marked (#4095) ──
//
// Every filled secret is masked out of what work prints, so a short one hides ordinary text
// wherever it appears. The server refuses a value under 8 characters or over 16,384 bytes and
// marks a short one saved before that floor (`too_short`, told from its length alone). The panel's
// part: the mark and its Replace, which puts the row's name and scope in the add form, and each
// refusal shown under the field in the server's own sentence.

const SHORT =
  'A secret must be at least 8 characters, so PersonalClaw can keep it out of what work prints; '
  + 'nothing was saved.'
const LARGE = 'A secret may be at most 16,384 bytes; nothing was saved.'

type Row = { name: string; scope: 'global' | 'project' | 'host'; project_id?: string; too_short?: boolean }

const payload = (rows: Row[]) => {
  const secrets = rows.map((r) => ({
    name: r.name,
    scope: r.scope,
    project_id: r.project_id ?? '',
    present: true as const,
    inherited_from_host: r.scope === 'host',
    consumers: [],
    too_short: r.too_short ?? false,
  }))
  return {
    secrets,
    counts: {
      total: secrets.length,
      global: secrets.filter((s) => s.scope === 'global').length,
      project: secrets.filter((s) => s.scope === 'project').length,
      host: secrets.filter((s) => s.scope === 'host').length,
    },
    empty_hint: '',
    store: { backend: 'dotenv' as const, keychain_namespace: '', keychain_scope: '' as const },
  }
}

const putSecret = vi.fn()
const deleteSecret = vi.fn()

async function mount(rows: Row[]) {
  vi.resetModules()
  sessionStorage.clear()
  putSecret.mockReset().mockResolvedValue({ secret: {}, secrets: [] })
  deleteSecret.mockReset().mockResolvedValue({ deleted: '', project_id: '', secrets: [] })
  vi.doMock('../../ui/dialog', () => ({ confirm: vi.fn(async () => true) }))
  vi.doMock('../../lib/api', () => ({
    // The shared load-error sentence asks whether a failure is an HTTP one.
    ApiError: class ApiError extends Error { status = 0 },
    api: {
      secrets: async () => payload(rows),
      projects: async () => [],
      // The add form's project picker lists the pinned projects first.
      projectSettings: async () => ({ default_project_id: '', pinned_project_ids: [] }),
      putSecret,
      deleteSecret,
    },
  }))
  const { SecretsPanel } = await import('./SecretsPanel')
  await act(async () => {
    render(<SecretsPanel />)
    await new Promise((res) => setTimeout(res, 0))
  })
}

beforeEach(() => { sessionStorage.clear() })
afterEach(() => { cleanup(); vi.resetModules(); vi.restoreAllMocks() })

describe('a secret saved before the floor is marked, with Replace, and kept', () => {
  it('marks only the short row', async () => {
    await mount([
      { name: 'OLD_PIN', scope: 'global', too_short: true },
      { name: 'GITHUB_TOKEN', scope: 'global' },
    ])
    // 🪤 Both rows rendered, or the absence of a second mark proves nothing.
    expect(screen.getByText('OLD_PIN')).toBeTruthy()
    expect(screen.getByText('GITHUB_TOKEN')).toBeTruthy()
    expect(screen.getAllByText('Too short to keep out of output reliably.')).toHaveLength(1)
    expect(screen.getAllByText('too short')).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: 'Replace' })).toHaveLength(1)
  })

  it('Replace puts the row in the add form and moves the focus to its value', async () => {
    await mount([{ name: 'OLD_PIN', scope: 'global', too_short: true }])
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Replace' })) })
    const name = screen.getByLabelText('Secret name') as HTMLInputElement
    const value = screen.getByLabelText('Secret value') as HTMLInputElement
    expect(name.value).toBe('OLD_PIN')
    expect(document.activeElement).toBe(value)
  })
})

describe('a refused value says why, under the field, in the server\'s sentence', () => {
  it.each([['too short', SHORT], ['too large', LARGE]])('a value %s', async (_case, sentence) => {
    await mount([{ name: 'GITHUB_TOKEN', scope: 'global' }])
    putSecret.mockRejectedValueOnce(new Error(sentence))
    fireEvent.change(screen.getByLabelText('Secret name'), { target: { value: 'DEMO_KEY' } })
    fireEvent.change(screen.getByLabelText('Secret value'), { target: { value: 'abc' } })
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Store secret' })) })
    expect(screen.getByRole('alert').textContent).toBe(sentence)
    // Nothing reads as stored.
    expect(screen.queryByRole('status')).toBeNull()
  })
})
