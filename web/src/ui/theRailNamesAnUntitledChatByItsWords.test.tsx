import { describe, it, expect, afterEach } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { toThreads, type Thread, type ThreadChat } from '../lib/useThreads'
import { runState } from '../test/runState'
import { ThreadRail } from './ThreadRail'

// ── The sidebar names a chat with no title by its first words, with no open mark ────────────────
//
// A chat whose title is its own key, a raw id, is named by its first words, cut short with "…".
// Home's Your chats named it so with the cut repaired, so a `**` the cut left open never reached a
// name or a tip; Your chats left Home, and the sidebar is where your chats are listed, so its row
// is held to the same: its words, in its name and in its tip, with no mark left open.

afterEach(cleanup)

const UNTITLED = {
  key: 'chat-raw', title: 'chat-raw', run_state: runState({ session: 'chat-raw' }), origin: 'manual', project_id: '',
  prompt_preview: 'Explain **why the build fails on the arm64 runner after the upgrade** today',
} as ThreadChat

const rows = (): Thread[] => toThreads({ sessions: [UNTITLED], loops: [], items: [], approvals: [] }).flatMap((g) => g.threads)

describe('a chat with no title, in the sidebar', () => {
  it('is named by its first words, never its raw id, and no mark is left open in its name or its tip', () => {
    render(<ThreadRail rows={rows()} selectedKey="" status="ready" onOpen={() => {}} onRetry={() => {}} />)
    const row = screen.getByRole('button', { name: /^Explain/ })
    const name = row.getAttribute('aria-label') ?? ''
    expect(name).not.toContain('chat-raw')
    expect(name, 'the name keeps an open mark').not.toContain('**')
    act(() => { fireEvent.focus(row) })
    const tip = document.querySelector('[data-tooltip-bubble]')?.textContent ?? ''
    expect(tip).toMatch(/^Explain/)
    expect(tip, 'the tip keeps an open mark').not.toContain('**')
    expect(row.textContent ?? '', 'the row draws a mark as text').not.toContain('**')
  })
})
