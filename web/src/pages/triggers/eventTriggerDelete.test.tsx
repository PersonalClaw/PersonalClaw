import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { eventToTrigger } from './triggerMeta'
import { EventTriggerSummary } from './TriggersListPage'
import { api, type Trigger as WireTrigger } from '../../lib/api'

// Event-trigger Delete (NZ1). The backend DELETE branch has existed since EIAT and
// `api.deleteEventTrigger` sat in api.ts with ZERO callers — an event trigger created through
// this page's own form could only be removed by hand-editing the store. The inspector now
// carries the family's delete idiom (StoreTriggerDetail.remove(): confirm → reportingWrite →
// onDeleted), and this file pins the three behaviors that make it honest:
//   1. the confirmed path deletes by RAW id (the client re-namespaces `event:`),
//   2. a declined confirm writes nothing,
//   3. a foreign row offers no Delete at all (TSE-4 — absence, not `disabled`).

const confirmDelete = vi.fn<(entity: string, name?: string) => Promise<boolean>>()
vi.mock('../../ui/dialog', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  confirmDelete: (...args: [string, string?]) => confirmDelete(...args),
}))

const wire = {
  kind: 'event', id: 'event:memo', raw_id: 'memo', name: 'On a memory write', enabled: true,
  pattern: 'MemoryKeyPattern', key_glob: 'project.acme.*', fire_count: 3,
  action: { provider: 'create-task', config: {} },
} as unknown as WireTrigger

beforeEach(() => {
  confirmDelete.mockReset()
})

describe('EventTriggerSummary delete', () => {
  it('confirmed → deletes by raw id and reports back through onDeleted', async () => {
    confirmDelete.mockResolvedValue(true)
    const del = vi.spyOn(api, 'deleteEventTrigger').mockResolvedValue(undefined)
    const onDeleted = vi.fn()
    render(<EventTriggerSummary t={eventToTrigger(wire)} onDeleted={onDeleted} />)
    fireEvent.click(screen.getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(onDeleted).toHaveBeenCalled())
    // Raw id, not the namespaced `event:memo`: `api.deleteEventTrigger` adds the prefix itself,
    // so passing `t.id` would issue DELETE /api/triggers/event:event:memo — a silent 404.
    expect(del).toHaveBeenCalledWith('memo')
    expect(confirmDelete).toHaveBeenCalledWith('data-event trigger', 'On a memory write')
    del.mockRestore()
  })

  it('declined → nothing is written and the panel stays', async () => {
    confirmDelete.mockResolvedValue(false)
    const del = vi.spyOn(api, 'deleteEventTrigger').mockResolvedValue(undefined)
    const onDeleted = vi.fn()
    render(<EventTriggerSummary t={eventToTrigger(wire)} onDeleted={onDeleted} />)
    fireEvent.click(screen.getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(confirmDelete).toHaveBeenCalled())
    expect(del).not.toHaveBeenCalled()
    expect(onDeleted).not.toHaveBeenCalled()
    del.mockRestore()
  })

  it('a foreign event trigger offers no Delete at all', () => {
    const foreign = eventToTrigger({ ...wire, read_only: true, author: 'alice' } as unknown as WireTrigger)
    render(<EventTriggerSummary t={foreign} onDeleted={() => {}} />)
    expect(screen.queryByRole('button', { name: /delete/i })).toBeNull()
  })
})
