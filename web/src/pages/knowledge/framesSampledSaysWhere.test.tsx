import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { KnowledgeDetail } from './KnowledgeDetail'
import { api, type KnowledgeItem } from '../../lib/api'
import * as store from './knowledgeStore'
import { clock, framesSampled } from './framesSampled'

// ── A video says which part of it was looked at ──────────────────────────────────────────────
// Its frames were taken one every 10 seconds from the start, 8 at most, so a 6-minute walkthrough
// was seen only through its first 70 seconds, and nothing on the item said so. They are now spread
// across the whole video, the ingest records when each was taken, and the metadata row says it.

function item(over: Partial<KnowledgeItem> = {}): KnowledgeItem {
  return {
    id: 'k-video-2',
    title: 'release walkthrough.mov',
    content: 'Tag the release, then publish it.',
    item_type: 'video',
    mime_type: 'video/quicktime',
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

const SPREAD = [22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5]

describe('a video says where its frames came from', () => {
  it('🔴 how many frames, across how long, and when each was taken', async () => {
    stubMount()
    render(<KnowledgeDetail item={item({ file_metadata: { frames_sampled: 8, frame_times: SPREAD, video_seconds: 360 } })} onChanged={() => {}} onDeleted={() => {}} />)
    const chip = await waitFor(() => screen.getByText('8 frames across 6:00'))
    expect(chip.getAttribute('title')).toBe(
      '8 frames were taken at even points across all 6:00 of this video: '
      + '0:22, 1:07, 1:52, 2:37, 3:22, 4:07, 4:52 and 5:37.')
  })

  it('a video whose length could not be read says its frames are from its start', async () => {
    stubMount()
    const times = [0, 10, 20, 30, 40, 50, 60, 70]
    render(<KnowledgeDetail item={item({ file_metadata: { frames_sampled: 8, frame_times: times } })} onChanged={() => {}} onDeleted={() => {}} />)
    const chip = await waitFor(() => screen.getByText('8 frames from the first 1:10'))
    expect(chip.getAttribute('title')).toBe(
      "This video's length couldn't be read, so its 8 frames were taken every 10 seconds from its "
      + 'start: 0:00, 0:10, 0:20, 0:30, 0:40, 0:50, 1:00 and 1:10.')
  })

  it('an item with no frames recorded carries no such note', async () => {
    stubMount()
    render(<KnowledgeDetail item={item({ file_metadata: {} })} onChanged={() => {}} onDeleted={() => {}} />)
    await waitFor(() => expect(screen.getByText(/Tag the release/)).toBeTruthy())
    expect(screen.queryByText(/frames? (across|from the first)/)).toBeNull()
  })
})

describe('how the frames are told', () => {
  it('positions read as a player shows them, hours included', () => {
    expect(clock(22.5)).toBe('0:22')
    expect(clock(360)).toBe('6:00')
    expect(clock(3725)).toBe('1:02:05')
  })

  it('one frame is one frame, and a long list says how many more there are', () => {
    expect(framesSampled({ frames_sampled: 1, frame_times: [0.5], video_seconds: 1 })).toEqual({
      label: '1 frame across 0:01',
      title: '1 frame was taken from the middle of this 0:01 video, at 0:00.',
    })
    const many = Array.from({ length: 16 }, (_, i) => i * 22.5)
    expect(framesSampled({ frames_sampled: 16, frame_times: many, video_seconds: 360 })?.title).toBe(
      '16 frames were taken at even points across all 6:00 of this video: '
      + '0:00, 0:22, 0:45, 1:07, 1:30, 1:52, 2:15, 2:37, 3:00, 3:22 and 6 more.')
  })
})
