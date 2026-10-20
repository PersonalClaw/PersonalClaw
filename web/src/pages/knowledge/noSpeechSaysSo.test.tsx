import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { KnowledgeDetail } from './KnowledgeDetail'
import { api, type KnowledgeItem } from '../../lib/api'
import * as store from './knowledgeStore'

// ── A recording with no speech in it says so ──────────────────────────────────────
// The transcription of a silent recording is a true result (no speech), so its step reads done;
// but it has no transcript, and nothing on the item said why. The ingest now records
// `file_metadata.no_speech`, and the metadata row shows it. A transcription that could NOT run
// is a failed step with its reason instead, and never carries this flag.

function item(over: Partial<KnowledgeItem> = {}): KnowledgeItem {
  return {
    id: 'k-audio-1',
    title: 'room tone.m4a',
    content: '',
    item_type: 'audio',
    mime_type: 'audio/mp4',
    ...over,
  } as KnowledgeItem
}

function stubMount() {
  vi.spyOn(store, 'getKnowledge').mockResolvedValue(null)
  vi.spyOn(api, 'knowledgeItemIntents').mockResolvedValue({ outcomes: [] } as never)
  vi.spyOn(api, 'knowledgeItemGraph').mockRejectedValue(new Error('no graph'))
  vi.spyOn(api, 'knowledgeTags').mockResolvedValue([] as never)
  vi.spyOn(api, 'knowledgeStaleness').mockRejectedValue(new Error('not a synthesis'))
}

afterEach(() => { vi.restoreAllMocks() })

describe('a recording the transcription heard no speech in', () => {
  it('🔴 says "No speech found", and why, beside its steps', async () => {
    stubMount()
    render(<KnowledgeDetail item={item({ file_metadata: { no_speech: true } })} onChanged={() => {}} onDeleted={() => {}} />)
    const chip = await waitFor(() => screen.getByText('No speech found'))
    expect(chip.getAttribute('title')).toBe(
      'Transcription ran and heard no speech in this recording, so it has no transcript.')
  })

  it('a recording with a transcript carries no such note', async () => {
    stubMount()
    render(<KnowledgeDetail item={item({ content: 'Okay, release week.', file_metadata: {} })} onChanged={() => {}} onDeleted={() => {}} />)
    await waitFor(() => expect(screen.getByText(/release week/)).toBeTruthy())
    expect(screen.queryByText('No speech found')).toBeNull()
  })
})
