import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ChatSessionSummary, ChatTag } from '../../lib/api'

// ── A chat's tags change one tag at a time, never as this page's copy of the list ─────────────────
//
// The chat list toggled a tag by sending the session's WHOLE tag list as it painted it. So a tag set
// since — in another tab, or by the gateway's auto-tag, re-tag run or bulk tag — was dropped the
// next time this page toggled any other tag, and neither screen said so. The toggle is now one tag
// in or out (`api.editSessionTags`), which the gateway applies to what is stored.

const TAGS: ChatTag[] = [
  { id: 't0', name: 'Pricing', order: 0 },
  { id: 't1', name: 'Urgent', order: 1 },
]
const SESSION: ChatSessionSummary = {
  key: 's1', title: 'Raise prices?', messages: 3, tags: ['t0'], origin: 'manual', lifecycle: 'active',
}

const { editSessionTags } = vi.hoisted(() => ({ editSessionTags: vi.fn() }))

vi.mock('../../lib/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../lib/api')>()
  return {
    ...mod,
    api: {
      ...mod.api,
      chatSessions: () => Promise.resolve([SESSION]),
      chatFolders: () => Promise.resolve([]),
      chatTags: () => Promise.resolve(TAGS),
      rooms: () => Promise.resolve({ rooms: [] }),
      retagStatus: () => Promise.resolve({ id: '', status: 'done', done: 0, total: 0, updated: 0, skipped: 0, errors: 0, current: '', error: '' }),
      editSessionTags,
    },
  }
})

import { ChatPage } from '../ChatPage'

/** Open the chat's Folder & tags menu and hand back the menu's tag row named `tag`. */
async function tagRow(tag: string) {
  render(<ChatPage sub="history" navigate={() => {}} query={{}} setQuery={() => {}} />)
  await screen.findByText('Raise prices?')
  fireEvent.click(screen.getAllByRole('button', { name: 'Organize chat' })[0])
  // The menu's own "Tags" heading scopes the rows, so a tag chip elsewhere on the page never matches.
  await waitFor(() => expect(screen.getAllByText('Tags').length).toBeGreaterThan(0))
  const menu = screen.getAllByText('Tags').map((h) => h.parentElement as HTMLElement)
    .find((m) => within(m).queryByRole('button', { name: tag }))
  expect(menu, 'the Folder & tags menu lists the tag').toBeTruthy()
  return within(menu as HTMLElement).getByRole('button', { name: tag })
}

beforeEach(() => {
  sessionStorage.clear()
  editSessionTags.mockReset()
})

describe('toggling a tag on a chat', () => {
  it('sends only that tag, applied to what is stored — never the painted list', async () => {
    // The gateway answers with the tags as stored after: another tab had added one meanwhile.
    editSessionTags.mockResolvedValue({ ok: true, tags: ['t0', 'x-elsewhere', 't1'] })
    const urgent = await tagRow('Urgent')
    await act(async () => { fireEvent.click(urgent) })
    await waitFor(() => expect(editSessionTags).toHaveBeenCalledTimes(1))
    expect(editSessionTags).toHaveBeenCalledWith('s1', { add: ['t1'] })
  })

  it('untagging sends only the removal', async () => {
    editSessionTags.mockResolvedValue({ ok: true, tags: [] })
    const pricing = await tagRow('Pricing')
    await act(async () => { fireEvent.click(pricing) })
    await waitFor(() => expect(editSessionTags).toHaveBeenCalledTimes(1))
    expect(editSessionTags).toHaveBeenCalledWith('s1', { remove: ['t0'] })
  })
})
