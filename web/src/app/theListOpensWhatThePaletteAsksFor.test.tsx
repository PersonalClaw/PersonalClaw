import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { resetDataStore } from '../lib/data'
import { mountList, navigate, project } from '../test/projectListHarness'

// ── The list opens what the palette asks for ─────────────────────────────────────────────────────
//
// The palette's New project and Import a project reach the Projects list through its address:
// `?new=1` opens the New project dialog and `?import=1` the import's file picker. Each key is read
// once and dropped with a replace, so Back and a reload never reopen them. A file picker opens only
// from a person's press, so `?import=1` clicks the hidden input only while the press still counts.
// Offline, New project is off with its reason everywhere it is drawn, and `?new=1` opens nothing.

const connection = vi.hoisted(() => ({ state: 'connected' as 'connected' | 'disconnected' }))
vi.mock('./gatewayConnection', async (importActual) => ({
  ...(await importActual<typeof import('./gatewayConnection')>()),
  useGatewayConnection: () => ({ state: connection.state, lastReachedAt: 0 }),
}))

function activation(isActive: boolean): void {
  Object.defineProperty(navigator, 'userActivation', { value: { isActive }, configurable: true })
}

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); resetDataStore(); navigate.mockReset(); connection.state = 'connected' })
afterEach(() => { vi.restoreAllMocks(); resetDataStore(); delete (navigator as { userActivation?: unknown }).userActivation })

const GARDEN = project('p-garden', 'Garden')
const dropped = (writes: { patch: Record<string, unknown>; opts?: { replace?: boolean } }[], key: string) =>
  writes.some((w) => key in w.patch && w.patch[key] == null && w.opts?.replace === true)

describe('?new=1', () => {
  it('opens the New project dialog, and drops the key with a replace', async () => {
    const { writes } = mountList({ projects: [GARDEN], query: { new: '1' } })
    expect(await screen.findByRole('dialog', { name: /New project/ })).toBeInTheDocument()
    expect(dropped(writes, 'new')).toBe(true)
  })

  it('offline, opens nothing and still drops the key', async () => {
    connection.state = 'disconnected'
    const { writes } = mountList({ projects: [GARDEN], query: { new: '1' } })
    await screen.findByRole('heading', { level: 2, name: /^Garden/ })
    await waitFor(() => expect(dropped(writes, 'new')).toBe(true))
    expect(screen.queryByRole('dialog', { name: /New project/ })).toBeNull()
  })
})

describe('?import=1', () => {
  it.each([[true, 1], [false, 0]])('with a press that counts (%s), clicks the hidden input %i times, and drops the key', async (active, clicks) => {
    activation(active)
    const click = vi.spyOn(HTMLInputElement.prototype, 'click').mockImplementation(() => {})
    const { writes } = mountList({ projects: [GARDEN], query: { import: '1' } })
    await screen.findByRole('heading', { level: 2, name: /^Garden/ })
    await waitFor(() => expect(dropped(writes, 'import')).toBe(true))
    expect(click).toHaveBeenCalledTimes(clicks)
  })
})

describe('offline', () => {
  it("turns off the empty list's New project with its reason", async () => {
    connection.state = 'disconnected'
    mountList({ projects: [] })
    await screen.findByText('No projects yet')
    for (const b of screen.getAllByRole('button', { name: /New project/ })) {
      expect(b.getAttribute('aria-disabled') === 'true' || (b as HTMLButtonElement).disabled, b.textContent ?? '').toBe(true)
    }
  })

  it("turns off the first run's New project with its reason", async () => {
    connection.state = 'disconnected'
    mountList({ projects: [project('p-personal', 'Personal', { is_builtin: true })], sessions: [] })
    await screen.findByText(/Projects keep related work together/)
    for (const b of screen.getAllByRole('button', { name: /New project/ })) {
      expect(b.getAttribute('aria-disabled') === 'true' || (b as HTMLButtonElement).disabled, b.textContent ?? '').toBe(true)
    }
  })
})
