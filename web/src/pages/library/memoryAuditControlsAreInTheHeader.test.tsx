// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryPage } from './MemoryPage'
import { api } from '../../lib/api'
import { resetDataStore } from '../../lib/data/store'

// ── Library › Memory's Audit view holds its filter and Reload in the page header ─────────────────
//
// The Studio's search and kind filter moved into the page's header with its views, and the Audit
// view kept its own filter field and Reload button in the body, above its list: two page controls
// beside the header that holds the rest. They are the header's now, while Audit is open.

const EVENTS = [
  { id: 1, event_type: 'write', memory_type: 'semantic', memory_key: 'coffee-order', created_at: '2026-11-04T09:00:00Z' },
  { id: 2, event_type: 'delete', memory_type: 'episodic', memory_key: 'standup-notes', created_at: '2026-11-04T10:00:00Z' },
]

beforeEach(() => {
  resetDataStore()
  vi.spyOn(api, 'memoryStats').mockRejectedValue(new Error('not needed'))
  vi.spyOn(api, 'memoryPartitions').mockResolvedValue([])
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function mount(tab = 'audit') {
  render(<MemoryPage query={{ tab }} setQuery={vi.fn()} />)
}

const header = () => document.querySelector('header') as HTMLElement

describe("the Audit view's controls", () => {
  it('are in the header while Audit is open, and filter the list there', async () => {
    const events = vi.spyOn(api, 'memoryEvents').mockResolvedValue(EVENTS as never)
    mount()
    expect(await screen.findByText(/coffee-order/)).toBeTruthy()
    const field = within(header()).getByRole('searchbox', { name: 'Filter memory audit log' })
    await userEvent.type(field, 'standup')
    await waitFor(() => expect(screen.queryByText(/coffee-order/)).toBeNull())
    expect(screen.getByText(/standup-notes/)).toBeTruthy()
    const before = events.mock.calls.length
    await userEvent.click(within(header()).getByRole('button', { name: 'Reload the audit log' }))
    await waitFor(() => expect(events.mock.calls.length).toBeGreaterThan(before))
  })

  it('are not in the body any more, and not in the header on another view', async () => {
    vi.spyOn(api, 'memoryEvents').mockResolvedValue(EVENTS as never)
    mount()
    await screen.findByText(/coffee-order/)
    const fields = screen.getAllByRole('searchbox', { name: 'Filter memory audit log' })
    expect(fields.every((f) => header().contains(f)), 'a filter field left in the body').toBe(true)
    cleanup()
    mount('health')
    expect(within(header()).queryByRole('button', { name: 'Reload the audit log' })).toBeNull()
  })
})
