import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { RunningRow } from '../../lib/api'
import { BackgroundRuns, besideOf } from './inspector/sections'

// ── A loop a chat started opens beside it from its Background tasks (#3824's follow-up) ───────────
//
// The running list names the chat a loop or a run was started from, so the chat's Background tasks
// lists them. A row opens what the detail tile shows of it: a run, and a loop backed by one, as the
// run; a loop as the loop, with its strip. Anything the tile shows nothing of opens its own page.

vi.mock('../../lib/useChatSocket', () => ({ useChatSocket: () => {} }))

const at = new Date(Date.now() - 60_000).toISOString()
const row = (over: Partial<RunningRow>): RunningRow => ({
  work: 'loop:feed01', kind: 'loop', session: 'chat-7', phase: 'working', line: 'Cycle 3', since: at, name: 'Watch the soil delivery',
  agent: '', started_at: at, route: '#/loops/feed01', blocked: false, waiting: [], ...over,
} as RunningRow)

describe('where a Background tasks row opens', () => {
  it('a loop, in the tile as the loop; a run and a run-backed loop, as the run; anything else, nowhere', () => {
    expect(besideOf(row({}))).toBe('loop:feed01')
    expect(besideOf(row({ kind: 'workflow', work: 'workflow:a1b2' }))).toBe('run:a1b2')
    expect(besideOf(row({ kind: 'loop', work: 'workflow:c3d4' }))).toBe('run:c3d4')
    expect(besideOf(row({ kind: 'trigger', work: 'trigger:t1' }))).toBeNull()
  })

  it('a press on a loop row opens it beside the chat, and the open one is marked', () => {
    const open = vi.fn()
    const navigate = vi.fn()
    const { rerender } = render(
      <BackgroundRuns running={[row({})]} finished={[]} loadError={null} read={() => {}} open={open} current={null} navigate={navigate} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Open Watch the soil delivery' }))
    expect(open).toHaveBeenCalledWith('loop:feed01')
    expect(navigate).not.toHaveBeenCalled()
    rerender(<BackgroundRuns running={[row({})]} finished={[]} loadError={null} read={() => {}} open={open} current="loop:feed01" navigate={navigate} />)
    const hit = screen.getByRole('button', { name: 'Open Watch the soil delivery' })
    expect(hit.getAttribute('aria-current')).toBe('true')
  })

  it('a row the tile shows nothing of opens its own page', () => {
    const open = vi.fn()
    const navigate = vi.fn()
    render(
      <BackgroundRuns running={[row({ kind: 'trigger', work: 'trigger:t1', name: 'Morning digest', route: '#/triggers?open=t1' })]}
        finished={[]} loadError={null} read={() => {}} open={open} current={null} navigate={navigate} />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Open Morning digest' }))
    expect(navigate).toHaveBeenCalledWith('triggers?open=t1')
    expect(open).not.toHaveBeenCalled()
  })
})
