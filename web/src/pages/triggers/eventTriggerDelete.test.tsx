import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { StoreTriggerDetail } from './StoreTriggerDetail'
import { api, type Trigger as WireTrigger } from '../../lib/api'

// Event-trigger Delete (NZ1). A data-event trigger created through this page's own form could once
// only be removed by hand-editing its store. It is a row in the one trigger store now, so it opens in
// the store inspector and deletes through the family's idiom (confirm → reportingWrite → onDeleted).
// This file pins the three behaviors that make that honest for an EVENT row:
//   1. the confirmed path deletes by the store id (the client re-namespaces `store:`),
//   2. a declined confirm writes nothing,
//   3. a foreign row offers no Delete at all (TSE-4 — absence, not `disabled`).

const confirmDelete = vi.fn<(entity: string, name?: string) => Promise<boolean>>()
vi.mock('../../ui/dialog', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  confirmDelete: (...args: [string, string?]) => confirmDelete(...args),
}))

const wire = {
  kind: 'store', store_kind: 'event', id: 'store:event:memo', raw_id: 'event:memo',
  name: 'On a memory write', enabled: true,
  spec: { source: 'memory', pattern: 'MemoryKeyPattern', key_glob: 'project.acme.*' }, run_count: 3,
  action: { provider: 'create-task', config: {} }, health: 'ok', state: 'active', broken: [], warnings: [],
} as unknown as WireTrigger

beforeEach(() => {
  confirmDelete.mockReset()
  vi.spyOn(api, 'triggerHistory').mockResolvedValue({ runs: [], total: 0 })
})

describe('an event trigger deletes from the store inspector', () => {
  it('confirmed → deletes by its store id and reports back through onDeleted', async () => {
    confirmDelete.mockResolvedValue(true)
    const del = vi.spyOn(api, 'deleteStoreTrigger').mockResolvedValue(undefined)
    const onDeleted = vi.fn()
    render(<StoreTriggerDetail trigger={wire} onChanged={() => {}} onDeleted={onDeleted} />)
    fireEvent.click(screen.getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(onDeleted).toHaveBeenCalled())
    // The store's own id, not the namespaced `store:event:memo`: `api.deleteStoreTrigger` adds the
    // prefix itself, so passing `id` would issue DELETE /api/triggers/store:store:event:memo — a 404.
    expect(del).toHaveBeenCalledWith('event:memo')
    expect(confirmDelete).toHaveBeenCalledWith('automation', 'On a memory write')
    del.mockRestore()
  })

  it('declined → nothing is written and the panel stays', async () => {
    confirmDelete.mockResolvedValue(false)
    const del = vi.spyOn(api, 'deleteStoreTrigger').mockResolvedValue(undefined)
    const onDeleted = vi.fn()
    render(<StoreTriggerDetail trigger={wire} onChanged={() => {}} onDeleted={onDeleted} />)
    fireEvent.click(screen.getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(confirmDelete).toHaveBeenCalled())
    expect(del).not.toHaveBeenCalled()
    expect(onDeleted).not.toHaveBeenCalled()
    del.mockRestore()
  })

  it('a foreign event trigger offers no Delete at all', () => {
    const foreign = { ...wire, read_only: true, author: 'alice' } as unknown as WireTrigger
    render(<StoreTriggerDetail trigger={foreign} onChanged={() => {}} onDeleted={() => {}} />)
    expect(screen.queryByRole('button', { name: /delete/i })).toBeNull()
  })
})
