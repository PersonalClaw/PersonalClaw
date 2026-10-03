import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { KnowledgeDetail } from './KnowledgeDetail'
import { api, type KnowledgeIngestGraph, type KnowledgeItem } from '../../lib/api'
import * as store from './knowledgeStore'

// ── A file the library refused offers no Retry ─────────────────────────────────────────────
// A file dropped in the memory vault's raw/ folder that the library refuses (text the content scan
// refuses, a kind of file it does not take) is filed as a failed item that says why and keeps
// nothing of the file (`file_metadata.refused`). Retry would only read that nothing again, so the
// item's page offers none; a failed item that kept its file still does.

const REFUSED = 'Its text failed the content safety scan, so nothing was made from it.'

function failedItem(over: Partial<KnowledgeItem> = {}): KnowledgeItem {
  return {
    id: 'k-refused-1',
    title: 'list.md',
    content: '',
    item_type: 'document',
    processing_status: 'failed',
    processing_error: REFUSED,
    ...over,
  } as KnowledgeItem
}

function graph(): KnowledgeIngestGraph {
  const phase = { status: 'not_applicable', reason: REFUSED }
  return {
    item_type: 'document',
    nodes: [{ node_type: 'document_read' }, { node_type: 'consolidate' }],
    edges: [],
    processing_status: 'failed',
    node_phases: { document_read: phase, consolidate: phase },
  } as KnowledgeIngestGraph
}

function mount(item: KnowledgeItem) {
  vi.spyOn(store, 'getKnowledge').mockResolvedValue(null)
  vi.spyOn(api, 'knowledgeItemIntents').mockResolvedValue({ outcomes: [] } as never)
  vi.spyOn(api, 'knowledgeItemGraph').mockResolvedValue(graph())
  vi.spyOn(api, 'knowledgeTags').mockResolvedValue([] as never)
  vi.spyOn(api, 'knowledgeStaleness').mockRejectedValue(new Error('not a synthesis'))
  return render(<KnowledgeDetail item={item} onChanged={() => {}} onDeleted={() => {}} />)
}

afterEach(() => { vi.restoreAllMocks() })

describe('the processing strip of a failed item', () => {
  it('a file the library refused says why and offers no Retry', async () => {
    mount(failedItem({ file_metadata: { refused: REFUSED } }))
    await waitFor(() => screen.getByText(REFUSED))
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
    // No step claims to be still to come: each says it does not run, and why.
    expect(screen.getByTitle(`Document read: not needed — ${REFUSED}`)).toBeTruthy()
  })

  it('a failed item that kept its file still offers Retry (positive control)', async () => {
    mount(failedItem({ file_path: '/library/files/k-refused-1.md' }))
    expect(await screen.findByRole('button', { name: /Retry/ })).toBeTruthy()
  })
})
