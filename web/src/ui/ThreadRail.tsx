import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { Thread, ThreadPip } from '../lib/useThreads'
import type { PhaseFamily } from '../lib/workPhase'
import { withWeight } from '../design/fontWeight'
import { Button } from './Button'
import { Eyebrow } from './Eyebrow'
import { StateMark } from './StateMark'
import { WindowedList } from './WindowedList'
import { cx } from './cx'

/** One project's group: the project, the name it is shown under, and its threads in order. */
export interface ThreadRailGroup {
  projectId: string
  name: string
  threads: Thread[]
}

/** `loading`: nothing read yet. `failed`: the last read failed, and the groups are what the last
 *  good read said (none when no read has succeeded). */
export type ThreadRailStatus = 'loading' | 'ready' | 'failed'

/** Which groups the owner opened or closed, by project id. A group with no entry is open while it
 *  holds a thread and closed while it holds none. */
const OPEN_KEY = 'thread-groups-open'

/** Each pip's state family (the shared state tokens the orb and the buddy read) and its words. */
const PIP: Record<ThreadPip, { family: PhaseFamily; word: string }> = {
  waiting: { family: 'waiting', word: 'waiting on you' },
  running: { family: 'active', word: 'running' },
  failed: { family: 'blocked', word: 'failed' },
}

function readOpen(): Record<string, boolean> {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(OPEN_KEY) ?? '{}')
    if (!v || typeof v !== 'object' || Array.isArray(v)) return {}
    return Object.fromEntries(Object.entries(v).filter(([, b]) => typeof b === 'boolean')) as Record<string, boolean>
  } catch {
    return {}
  }
}

function writeOpen(open: Record<string, boolean>): Record<string, boolean> {
  try { localStorage.setItem(OPEN_KEY, JSON.stringify(open)) } catch { /* storage full or off: the choice lasts this session */ }
  return open
}

/** Scroll the rail (the row's nearest scrolling ancestor) just far enough to show *el*. Never
 *  `scrollIntoView`, which may scroll the page as well, and would for a drawer parked off-screen. */
function reveal(el: HTMLElement) {
  let sc = el.parentElement
  while (sc && !/(auto|scroll)/.test(getComputedStyle(sc).overflowY)) sc = sc.parentElement
  if (!sc) return
  const r = el.getBoundingClientRect()
  const s = sc.getBoundingClientRect()
  if (r.top < s.top) sc.scrollTop -= s.top - r.top
  else if (r.bottom > s.bottom) sc.scrollTop += r.bottom - s.bottom
}

const countLabel = (n: number) => (n === 0 ? 'no threads' : `${n} thread${n === 1 ? '' : 's'}`)

/** The sidebar's threads: each project a disclosure group, each thread a dense row that opens it.
 *
 *  A row says its state in words beside one static mark (running, waiting on you, failed) and in
 *  its accessible name; nothing in the rail moves. The open thread (`selectedKey`, which the shell
 *  reads from the address) carries `aria-current="page"` and the rail's selected fill, and its
 *  group opens so its row can be seen. A long name is cut short on screen, whole in the row's name
 *  and tooltip, and wraps while the row has keyboard focus. Loading, a failed read and an empty
 *  project each have a row of their own. A group's rows window past the shared threshold
 *  (`WindowedList`), so hundreds of threads keep the rail fast and keep their keyboard order. */
export function ThreadRail({ groups, selectedKey, status, failure, onOpen, onRetry }: {
  /** The groups in the order they show, each with its threads in order. */
  groups: ThreadRailGroup[]
  /** The key of the open thread, or '' when the page is no thread. */
  selectedKey: string
  status: ThreadRailStatus
  /** The sentence a failed read shows, beside Retry. */
  failure?: string
  /** Opens a thread at its route (no leading `#/`). */
  onOpen: (route: string) => void
  /** Reads the threads again after a failed read. */
  onRetry: () => void
}) {
  const base = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState<Record<string, boolean>>(readOpen)
  const isOpen = (g: ThreadRailGroup) => open[g.projectId] ?? g.threads.length > 0
  const toggle = (g: ThreadRailGroup) => setOpen((prev) => writeOpen({ ...prev, [g.projectId]: !(prev[g.projectId] ?? g.threads.length > 0) }))

  // The open thread's group opens when the open thread changes (an arrival, a reload, Back), so its
  // row can be seen. Only then: closing that group afterwards keeps it closed.
  const selectedGroup = groups.find((g) => g.threads.some((t) => t.key === selectedKey))
  const selectedProject = selectedGroup?.projectId
  useLayoutEffect(() => {
    if (selectedProject === undefined) return
    setOpen((prev) => (prev[selectedProject] === false ? writeOpen({ ...prev, [selectedProject]: true }) : prev))
  }, [selectedKey, selectedProject])

  const selectedShown = selectedGroup !== undefined && isOpen(selectedGroup)
  useEffect(() => {
    if (!selectedKey || !selectedShown) return
    const rows = rootRef.current?.querySelectorAll<HTMLElement>('[data-thread-key]') ?? []
    const row = [...rows].find((r) => r.dataset.threadKey === selectedKey)
    if (row) reveal(row)
  }, [selectedKey, selectedShown])

  const headingId = `${base}-heading`
  return (
    <div ref={rootRef} role="group" aria-labelledby={headingId} className="flex flex-col">
      <Eyebrow id={headingId} className="px-s pt-l pb-1">Threads</Eyebrow>
      {status === 'loading' && (
        <div role="status" data-type="body-s" className="px-s py-1 text-on-surface-low">Loading threads…</div>
      )}
      {status === 'failed' && (
        <div data-type="body-s" className="flex items-center gap-s px-s py-1">
          <span className="min-w-0 flex-1 text-on-surface-var">{failure || "Couldn't load your threads."}</span>
          <Button variant="ghost" size="xs" onClick={onRetry}>Retry</Button>
        </div>
      )}
      {status === 'ready' && groups.length === 0 && (
        <div data-type="body-s" className="px-s py-1 text-on-surface-low">No threads yet</div>
      )}
      {status !== 'loading' && groups.map((g, i) => {
        const opened = isOpen(g)
        const listId = `${base}-group-${i}`
        return (
          <div key={`project:${g.projectId}`} className="flex flex-col">
            <button
              type="button" onClick={() => toggle(g)}
              aria-expanded={opened} aria-controls={opened ? listId : undefined}
              aria-label={`${g.name}, ${countLabel(g.threads.length)}`} title={g.name}
              data-type="body-s"
              className="group flex w-full items-center gap-xs rounded-pill px-s py-1 text-left text-on-surface-var transition-colors duration-(--duration-short) hover:bg-surface-low/60 hover:text-on-surface"
              style={withWeight({ minHeight: 32 }, 470)}>
              <ChevronRight size={16} strokeWidth={2} aria-hidden className={cx('shrink-0', opened && 'rotate-90')} />
              <span className="min-w-0 flex-1 truncate group-focus-visible:whitespace-normal group-focus-visible:break-words">{g.name}</span>
              <span data-type="caption" className="shrink-0 tabular-nums">{g.threads.length}</span>
            </button>
            {opened && (
              <div id={listId}>
                {g.threads.length === 0
                  ? <div data-type="body-s" className="py-1 pl-3xl pr-s text-on-surface-low">No threads yet</div>
                  : (
                    <WindowedList
                      items={g.threads} rowKey={(t) => t.key}
                      // Variable: a row is 32 px, and grows while it has focus to show its whole name.
                      rowHeights="variable" estimateRowHeight={32}
                      noun={`threads in ${g.name}`}
                      findHint="use Search chats on the Chat history page, which searches every chat."
                      className="flex flex-col">
                      {(t) => <ThreadRow thread={t} selected={t.key === selectedKey} onOpen={onOpen} />}
                    </WindowedList>
                  )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function ThreadRow({ thread, selected, onOpen }: { thread: Thread; selected: boolean; onOpen: (route: string) => void }) {
  const pip = thread.pip ? PIP[thread.pip] : null
  return (
    <button
      type="button" onClick={() => onOpen(thread.route)}
      data-thread-key={thread.key}
      aria-label={pip ? `${thread.title}, ${pip.word}` : thread.title}
      title={thread.title}
      aria-current={selected ? 'page' : undefined}
      data-type="body-m"
      className={cx(
        'group relative flex w-full items-center gap-s rounded-pill py-1 pl-3xl pr-s text-left transition-colors duration-(--duration-short)',
        selected ? 'text-on-surface' : 'text-on-surface-var hover:bg-surface-low/60 hover:text-on-surface',
      )}
      style={withWeight({ minHeight: 32 }, selected ? 470 : 400)}>
      {/* The selected fill, the rail's own: quiet, and distinct from hover (a lighter fill) and from
          keyboard focus (the focus ring). */}
      {selected && <span aria-hidden className="absolute inset-0 rounded-pill bg-surface-low" />}
      <span className="relative z-10 min-w-0 flex-1 truncate group-focus-visible:whitespace-normal group-focus-visible:break-words">{thread.title}</span>
      {pip && (
        <span data-type="caption" className="relative z-10 shrink-0">
          <StateMark family={pip.family}>{pip.word}</StateMark>
        </span>
      )}
    </button>
  )
}
