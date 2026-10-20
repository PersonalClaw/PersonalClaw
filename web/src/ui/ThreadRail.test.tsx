import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { Thread } from '../lib/useThreads'
import { ThreadRail, type ThreadRailGroup } from './ThreadRail'

// ── The thread rail: projects with their threads beneath (#3814) ─────────────────────────────────
//
// Each project is a disclosure group, and under it each thread is a dense row that opens it. A row
// can carry one static marker, and its state is said in words beside it and in its name: running,
// waiting on you, failed. Nothing in it moves. The open thread is lit (`aria-current="page"`), and
// its group opens so the lit row can be seen. A group's disclosure lasts across a reload. Loading,
// a failed read and an empty project each have a row of their own, so none of them reads as a
// project with nothing in it.

function thread(over: Partial<Thread> & Pick<Thread, 'key' | 'title'>): Thread {
  const id = over.key.split(':')[1]
  return { kind: 'chat', id, projectId: 'p-garden', route: `chat/${id}`, pip: null, ...over }
}

const GARDEN: ThreadRailGroup = {
  projectId: 'p-garden',
  name: 'Garden',
  threads: [
    thread({ key: 'chat:chat-1', title: 'Plan the garden' }),
    thread({ key: 'loop:a1b2c3d4', kind: 'loop', title: 'Find a seed supplier', route: 'loops/a1b2c3d4', pip: 'running' }),
    thread({ key: 'loop:e5f6a7b8', kind: 'code', title: 'Fence gate', route: 'code/e5f6a7b8', pip: 'failed' }),
    thread({ key: 'chat:chat-2', title: 'Order the soil', pip: 'waiting' }),
  ],
}
const EMPTY: ThreadRailGroup = { projectId: 'p-house', name: 'House move', threads: [] }
const LOOSE: ThreadRailGroup = {
  projectId: '',
  name: 'No project',
  threads: [thread({ key: 'chat:chat-3', title: 'A question about tax', projectId: '' })],
}

function rail(props: Partial<Parameters<typeof ThreadRail>[0]> = {}) {
  const onOpen = vi.fn()
  const onRetry = vi.fn()
  const utils = render(
    <ThreadRail groups={[GARDEN, EMPTY, LOOSE]} selectedKey="" status="ready" onOpen={onOpen} onRetry={onRetry} {...props} />,
  )
  return { ...utils, onOpen, onRetry }
}

const groupToggle = (name: RegExp) => screen.getByRole('button', { name })

beforeEach(() => { localStorage.clear() })
afterEach(() => { cleanup(); vi.restoreAllMocks() })

describe('the thread rail', () => {
  it('lists each project as a group with its threads beneath, in the order it is given', () => {
    rail()
    const toggles = screen.getAllByRole('button', { name: /threads?$/ })
    expect(toggles.map((b) => b.getAttribute('aria-label'))).toEqual(['Garden, 4 threads', 'House move, no threads', 'No project, 1 thread'])
    const garden = within(document.getElementById(groupToggle(/^Garden/).getAttribute('aria-controls')!)!)
    expect(garden.getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      'Plan the garden', 'Find a seed supplier, running', 'Fence gate, failed', 'Order the soil, waiting on you',
    ])
  })

  it('says each state in words beside its marker, never by the marker alone', () => {
    rail()
    for (const [name, word, family] of [
      [/^Find a seed supplier/, 'running', 'active'],
      [/^Fence gate/, 'failed', 'blocked'],
      [/^Order the soil/, 'waiting on you', 'waiting'],
    ] as const) {
      const row = screen.getByRole('button', { name })
      const mark = row.querySelector('[data-state-family]')
      expect(mark?.getAttribute('data-state-family')).toBe(family)
      expect(mark?.textContent).toBe(word)
    }
    expect(screen.getByRole('button', { name: 'Plan the garden' }).querySelector('[data-state-family]')).toBeNull()
  })

  it('a running row schedules no animation and carries no moving class', () => {
    const raf = vi.spyOn(window, 'requestAnimationFrame')
    rail()
    const row = screen.getByRole('button', { name: /^Find a seed supplier/ })
    expect(raf).not.toHaveBeenCalled()
    expect(row.outerHTML).not.toMatch(/animate-|pulse|spin/)
  })

  it('lights the open thread, and only it', () => {
    rail({ selectedKey: 'loop:e5f6a7b8' })
    const lit = [...document.querySelectorAll('[aria-current="page"]')]
    expect(lit).toHaveLength(1)
    expect(lit[0].getAttribute('aria-label')).toBe('Fence gate, failed')
  })

  it('opens a thread at its own address', () => {
    const { onOpen } = rail()
    fireEvent.click(screen.getByRole('button', { name: /^Fence gate/ }))
    expect(onOpen).toHaveBeenCalledWith('code/e5f6a7b8')
  })

  it('collapses a group, and keeps it collapsed after a reload', () => {
    const first = rail()
    fireEvent.click(groupToggle(/^Garden/))
    expect(groupToggle(/^Garden/)).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('button', { name: 'Plan the garden' })).toBeNull()
    first.unmount()
    rail()
    expect(groupToggle(/^Garden/)).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(groupToggle(/^Garden/))
    expect(screen.getByRole('button', { name: 'Plan the garden' })).toBeInTheDocument()
  })

  it('opens the group that holds the open thread, so its row can be seen', () => {
    const first = rail()
    fireEvent.click(groupToggle(/^Garden/))
    first.unmount()
    rail({ selectedKey: 'chat:chat-1' })
    expect(groupToggle(/^Garden/)).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: 'Plan the garden' })).toHaveAttribute('aria-current', 'page')
  })

  it('an empty project starts closed, and says it has no threads when opened', () => {
    rail()
    expect(groupToggle(/^House move/)).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(groupToggle(/^House move/))
    const list = document.getElementById(groupToggle(/^House move/).getAttribute('aria-controls')!)!
    expect(list.textContent).toBe('No threads yet')
  })

  it('a long name is cut short on screen and whole in its name and tooltip', () => {
    const title = 'Find a supplier for heritage tomato seeds that ships to the north before the frost'
    rail({ groups: [{ ...GARDEN, threads: [thread({ key: 'chat:long', title })] }] })
    const row = screen.getByRole('button', { name: title })
    expect(row).toHaveAttribute('title', title)
    expect(row.querySelector('.truncate')?.textContent).toBe(title)
  })

  it('says it is loading, rather than showing no threads', () => {
    rail({ groups: [], status: 'loading' })
    expect(screen.getByRole('status')).toHaveTextContent('Loading threads')
    expect(screen.queryByText('No threads yet')).toBeNull()
  })

  it('says a failed read failed and offers to read again, keeping the threads it last read', () => {
    const { onRetry } = rail({ status: 'failed', failure: "Couldn't load your threads." })
    expect(screen.getByText("Couldn't load your threads.")).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Plan the garden' })).toBeInTheDocument()
  })

  it('with no project and no thread at all, says there are none', () => {
    rail({ groups: [] })
    expect(screen.getByText('No threads yet')).toBeInTheDocument()
  })
})
