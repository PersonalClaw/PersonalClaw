import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatActivityPanel } from './ChatActivityPanel'
import type { SubagentCard } from './chatTypes'

// ── A batch's tasks are in the Activity panel of the chat that started it ───────────────────────
//
// 🔴 The panel listed only the subagents a chat started itself: a batch's tasks run as steps of the
// batch's run, under the run's own key, so a chat that started a batch of two had no Subagents tab
// at all. The gateway now hands their events to that chat, named by their step and marked with the
// batch's run, and the panel shows them, beside the chat's own.

const ACTIVITY = { files: [], links: [] }

const own: SubagentCard = { id: 'own1', task: 'Summarise the notes', agent: 'general-purpose', done: false }
const batchTask: SubagentCard = {
  id: 'bt1', task: 'List every caller of the retry helper in src/app …', agent: '', done: false,
  title: 'Find the retry callers', run: 'a1b2c3d4',
}

describe('the chat’s Activity panel', () => {
  it('🔴 has a Subagents tab for a batch’s tasks, each named by its step', async () => {
    render(<ChatActivityPanel activity={ACTIVITY} onOpenFile={() => {}} subagents={[batchTask]} onKillFanout={() => {}} />)
    await userEvent.click(screen.getByRole('tab', { name: 'Subagents' }))
    expect(screen.getByText('Find the retry callers')).toBeTruthy()
    expect(screen.getByText(/a task of a batch/)).toBeTruthy()
  })

  it('its Stop fan-out counts and stops the chat’s own subagents, not a batch’s, which stop with their run', async () => {
    render(<ChatActivityPanel activity={ACTIVITY} onOpenFile={() => {}} subagents={[own, batchTask]} onKillFanout={() => {}} />)
    await userEvent.click(screen.getByRole('tab', { name: 'Subagents' }))
    expect(screen.getByRole('button', { name: /Stop fan-out \(1\)/ })).toBeTruthy()
  })

  it('and offers no Stop fan-out when only a batch’s tasks run', async () => {
    const stop = vi.fn()
    render(<ChatActivityPanel activity={ACTIVITY} onOpenFile={() => {}} subagents={[batchTask]} onKillFanout={stop} />)
    await userEvent.click(screen.getByRole('tab', { name: 'Subagents' }))
    expect(screen.queryByRole('button', { name: /Stop fan-out/ })).toBeNull()
  })
})
