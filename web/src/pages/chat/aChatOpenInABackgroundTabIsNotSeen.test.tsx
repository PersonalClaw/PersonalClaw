import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../../lib/api'
import { useSeenWhileInView } from './useSeenWhileInView'

const said = vi.hoisted(() => [] as string[])
vi.mock('../../app/reportingWrite', () => ({
  reportActionFailure: (what: string) => () => { said.push(what) },
}))

// ── A chat is seen only while it is in front of you (#3814 B2) ─────────────────────────────────
//
// Marking a chat seen clears its unread mark on every window and the phone, so a chat open in a
// background tab, or in a window behind another app, is never marked: only one whose page is
// visible and whose window is focused. It is marked on open, on coming back into view, and when a
// turn ends while it is in view.

let visibility: DocumentVisibilityState = 'visible'
let focused = true

beforeEach(() => {
  visibility = 'visible'
  focused = true
  vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => visibility)
  vi.spyOn(document, 'hasFocus').mockImplementation(() => focused)
  vi.spyOn(api, 'markChatSeen').mockResolvedValue({ ok: true, moved: ['c1'] })
})

afterEach(() => { vi.restoreAllMocks() })

const marked = () => vi.mocked(api.markChatSeen).mock.calls.map((c) => c[0])

function comeBack(how: 'tab' | 'window') {
  if (how === 'tab') {
    visibility = 'visible'
    document.dispatchEvent(new Event('visibilitychange'))
  } else {
    focused = true
    window.dispatchEvent(new Event('focus'))
  }
}

describe('a chat is seen only while it is in front of you', () => {
  it('a chat opened in front of you is seen', () => {
    renderHook(() => useSeenWhileInView('c1', false))
    expect(marked()).toEqual(['c1'])
  })

  it('a chat open in a background tab is not seen, until its tab is shown', () => {
    visibility = 'hidden'
    renderHook(() => useSeenWhileInView('c1', false))
    expect(marked()).toEqual([])
    comeBack('tab')
    expect(marked()).toEqual(['c1'])
  })

  it('a chat open in a window behind another is not seen, until its window is focused', () => {
    focused = false
    renderHook(() => useSeenWhileInView('c1', false))
    expect(marked()).toEqual([])
    comeBack('window')
    expect(marked()).toEqual(['c1'])
  })

  it('a turn that ends while the chat is in view is seen', () => {
    const { rerender } = renderHook(({ running }) => useSeenWhileInView('c1', running), { initialProps: { running: true } })
    vi.mocked(api.markChatSeen).mockClear()
    rerender({ running: false })
    expect(marked()).toEqual(['c1'])
  })

  it('a turn that ends while the chat is in a background tab is not seen', () => {
    const { rerender } = renderHook(({ running }) => useSeenWhileInView('c1', running), { initialProps: { running: true } })
    vi.mocked(api.markChatSeen).mockClear()
    visibility = 'hidden'
    rerender({ running: false })
    expect(marked()).toEqual([])
  })

  it('a new chat with no name yet marks nothing', () => {
    renderHook(() => useSeenWhileInView(null, false))
    comeBack('window')
    expect(marked()).toEqual([])
  })

  it('a mark that fails is said once a page, not on every return to the window', async () => {
    vi.mocked(api.markChatSeen).mockRejectedValue(new Error('seen_unreadable'))
    renderHook(() => useSeenWhileInView('c1', false))
    comeBack('window')
    comeBack('tab')
    await waitFor(() => expect(marked()).toHaveLength(3))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(said).toEqual(['mark this chat as read'])
  })

  it('a chat left behind marks nothing more', () => {
    const { unmount } = renderHook(() => useSeenWhileInView('c1', false))
    unmount()
    vi.mocked(api.markChatSeen).mockClear()
    comeBack('tab')
    comeBack('window')
    expect(marked()).toEqual([])
  })
})
