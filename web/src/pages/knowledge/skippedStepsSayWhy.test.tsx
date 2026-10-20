import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { KnowledgeDetail } from './KnowledgeDetail'
import { api, type KnowledgeIngestGraph, type KnowledgeItem } from '../../lib/api'
import * as store from './knowledgeStore'

// ── Every step an ingest did not run says why, and what would make it run ──────────────────
// A screen recording ingested with no model bound to Image · Modality listed Video classify, OCR
// and Vision as "skipped" and said why for none of them. Each step now records its own outcome
// (status, reason, fix) and the item's processing strip shows one line per step that was skipped
// or failed, each with its fix as a link. A step that does not apply to this item gets no line.

const MODELS_FIX = { text: 'Choose a model for Image · Modality in Settings → Models', href: '#/settings/models' }

function videoItem(): KnowledgeItem {
  return {
    id: 'k-video-1',
    title: 'release walkthrough.mov',
    content: 'Tag the release, then publish it.',
    item_type: 'video',
    mime_type: 'video/quicktime',
    processing_status: 'partial',
  } as KnowledgeItem
}

function graph(over: Partial<KnowledgeIngestGraph['node_phases']> = {}): KnowledgeIngestGraph {
  const nodes = ['av_split', 'transcription', 'frame_extract', 'video_classify', 'ocr', 'vision', 'video_consolidate', 'intents']
  return {
    item_type: 'video',
    nodes: nodes.map((node_type) => ({ node_type, label: node_type === 'ocr' ? 'OCR' : undefined })),
    edges: [],
    processing_status: 'partial',
    node_phases: {
      av_split: { status: 'done' },
      transcription: { status: 'done' },
      frame_extract: { status: 'done' },
      video_classify: { status: 'skipped', reason: 'No image model is set up.', fix: [MODELS_FIX], needs: ['image_modality'], ready: false },
      ocr: { status: 'skipped', reason: 'It needs Video classify first, which was skipped because no image model is set up.', fix: [MODELS_FIX], needs: ['image_modality'], ready: false },
      vision: { status: 'skipped', reason: 'It needs Video classify first, which was skipped because no image model is set up.', fix: [MODELS_FIX], needs: ['image_modality'], ready: false },
      video_consolidate: { status: 'done' },
      intents: { status: 'not_applicable', reason: 'You have no intents for it to look for.' },
      ...over,
    },
  }
}

function mount(g: KnowledgeIngestGraph) {
  vi.spyOn(store, 'getKnowledge').mockResolvedValue(null)
  vi.spyOn(api, 'knowledgeItemIntents').mockResolvedValue({ outcomes: [] } as never)
  vi.spyOn(api, 'knowledgeItemGraph').mockResolvedValue(g)
  vi.spyOn(api, 'knowledgeTags').mockResolvedValue([] as never)
  vi.spyOn(api, 'knowledgeStaleness').mockRejectedValue(new Error('not a synthesis'))
  return render(<KnowledgeDetail item={videoItem()} onChanged={() => {}} onDeleted={() => {}} />)
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('the processing strip of an item whose steps were skipped', () => {
  it('🔴 shows one line per skipped step, each with its own reason and its fix', async () => {
    mount(graph())
    const list = await waitFor(() => screen.getByRole('list', { name: 'Steps that did not run' }))
    const lines = within(list).getAllByRole('listitem')
    expect(lines.map((l) => l.textContent)).toEqual([
      'Video classify skipped: No image model is set up. Choose a model for Image · Modality in Settings → Models',
      'OCR skipped: It needs Video classify first, which was skipped because no image model is set up. Choose a model for Image · Modality in Settings → Models',
      'Vision skipped: It needs Video classify first, which was skipped because no image model is set up. Choose a model for Image · Modality in Settings → Models',
    ])
    for (const line of lines) {
      const link = within(line).getByRole('link', { name: MODELS_FIX.text })
      expect(link.getAttribute('href')).toBe('#/settings/models')
    }
  })

  it('a step that does not apply gets no line, and says so where its step is drawn', async () => {
    mount(graph())
    await waitFor(() => screen.getByRole('list', { name: 'Steps that did not run' }))
    expect(screen.queryByText(/Intents (skipped|not needed):/)).toBeNull()
    expect(screen.getByTitle('Intents: not needed — You have no intents for it to look for.')).toBeTruthy()
  })

  it('a failed step gets a line with its error', async () => {
    mount(graph({ transcription: { status: 'failed', reason: 'The speech-to-text model could not read this audio.' } }))
    const list = await waitFor(() => screen.getByRole('list', { name: 'Steps that did not run' }))
    expect(within(list).getByText(/Transcription failed:/).closest('li')?.textContent)
      .toBe('Transcription failed: The speech-to-text model could not read this audio.')
  })

  it('offers to run the item again once what a skipped step needed is set up', async () => {
    const ready = { ...MODELS_FIX }
    const g = graph({
      video_classify: { status: 'skipped', reason: 'No image model is set up.', fix: [ready], needs: ['image_modality'], ready: true },
    })
    const run = vi.spyOn(api, 'generateKnowledgeIntelligence').mockResolvedValue({ ...videoItem(), processing_status: 'queued' })
    // A queued item opens its progress stream; jsdom has none, so a silent one stands in.
    vi.stubGlobal('EventSource', class { addEventListener() {} close() {} onerror = null })
    mount(g)
    const button = await waitFor(() => screen.getByRole('button', { name: 'Run again' }))
    expect(screen.getByText('What a skipped step needed is set up now.')).toBeTruthy()
    fireEvent.click(button)
    await waitFor(() => expect(run).toHaveBeenCalledWith('k-video-1'))
    // While it runs again, the previous run's lines are not shown as if they were this run's.
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Steps that did not run' })).toBeNull())
  })

  it('🔴 offers to run the item again when a step ran out of time before it finished', async () => {
    const g = graph({
      frame_extract: { status: 'failed', reason: 'It did not finish within 2 minutes, so it was stopped.', retry: true },
    })
    const run = vi.spyOn(api, 'generateKnowledgeIntelligence').mockResolvedValue({ ...videoItem(), processing_status: 'queued' })
    // A queued item opens its progress stream; jsdom has none, so a silent one stands in.
    vi.stubGlobal('EventSource', class { addEventListener() {} close() {} onerror = null })
    mount(g)
    const button = await waitFor(() => screen.getByRole('button', { name: 'Run again' }))
    expect(screen.getByText('A step ran out of time before it finished.')).toBeTruthy()
    fireEvent.click(button)
    await waitFor(() => expect(run).toHaveBeenCalledWith('k-video-1'))
    // While it runs again, the stopped run's lines are not shown as if they were this run's.
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Steps that did not run' })).toBeNull())
  })

  it('a step that failed for another reason offers no run-again', async () => {
    mount(graph({ transcription: { status: 'failed', reason: 'The speech-to-text model could not read this audio.' } }))
    await waitFor(() => screen.getByRole('list', { name: 'Steps that did not run' }))
    expect(screen.queryByRole('button', { name: 'Run again' })).toBeNull()
  })

  it('offers no run-again while nothing a skipped step needed has been set up', async () => {
    mount(graph())
    await waitFor(() => screen.getByRole('list', { name: 'Steps that did not run' }))
    expect(screen.queryByRole('button', { name: 'Run again' })).toBeNull()
  })

  it('a fix that is not an in-app route is shown as words, never as a link', async () => {
    mount(graph({
      vision: { status: 'skipped', reason: 'No image model is set up.', fix: [{ text: 'Choose a model elsewhere', href: 'https://example.com/models' }] },
    }))
    const list = await waitFor(() => screen.getByRole('list', { name: 'Steps that did not run' }))
    expect(within(list).getByText('Choose a model elsewhere').closest('a')).toBeNull()
  })
})
