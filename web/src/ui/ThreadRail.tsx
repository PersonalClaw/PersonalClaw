import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, Edit3 } from 'lucide-react'
import type { Thread, ThreadPip } from '../lib/useThreads'
import type { PhaseFamily } from '../lib/workPhase'
import { bindingLabel } from '../app/keymap'
import { withWeight } from '../design/fontWeight'
import { Button } from './Button'
import { Eyebrow } from './Eyebrow'
import { ResultAnnouncement } from './ListControls'
import { SearchField } from './SearchField'
import { StateMark } from './StateMark'
import { WindowedList } from './WindowedList'
import { cx } from './cx'
import { RING_REACH } from './NavRail'

/** One project's group: the project, the name it is shown under, and its threads in order. */
export interface ThreadRailGroup {
  projectId: string
  name: string
  threads: Thread[]
}

/** `loading`: nothing read yet. `failed`: the last read failed, and the groups are what the last
 *  good read said (none when no read has succeeded). `offline`: the gateway is not answering at
 *  all, so the groups are what it said before it stopped, and no read is worth blaming. */
export type ThreadRailStatus = 'loading' | 'ready' | 'failed' | 'offline'

/** Which groups the owner opened or closed, by project id. A group with no entry is open while it
 *  holds a thread and closed while it holds none. */
const OPEN_KEY = 'thread-groups-open'

/** Each pip's state family (the shared state tokens the orb and the buddy read) and its words. A
 *  project's page lists its threads with the same marks (`pages/projects/ProjectThreads.tsx`). */
export const PIP: Record<ThreadPip, { family: PhaseFamily; word: string }> = {
  waiting: { family: 'waiting', word: 'waiting on you' },
  running: { family: 'active', word: 'running' },
  failed: { family: 'blocked', word: 'failed' },
  unread: { family: 'quiet', word: 'unread' },
}

/** Which groups the owner opened or closed, as stored: what the rail starts from, and what ⌘1 to ⌘9
 *  count by while no rail is mounted. */
export function readGroupsOpen(): Record<string, boolean> {
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

/** The rail's scroll area: *el*'s nearest scrolling ancestor (the sidebar, `NavRail`). */
function scrollerOf(el: HTMLElement): HTMLElement | null {
  let sc = el.parentElement
  while (sc && !/(auto|scroll)/.test(getComputedStyle(sc).overflowY)) sc = sc.parentElement
  return sc
}

/** Scroll the rail just far enough to show *el*, clear of the top the scroller keeps clear (its
 *  `scroll-padding-top`, the pinned bar's height: see `PINNED_BAR`). Never `scrollIntoView`, which
 *  may scroll the page as well, and would for a drawer parked off-screen. */
function reveal(el: HTMLElement) {
  const sc = scrollerOf(el)
  if (!sc) return
  const r = el.getBoundingClientRect()
  const s = sc.getBoundingClientRect()
  // The bar sticks flush with the scroller's top, so its height is what it covers there, whether it
  // is stuck, at rest above, or being pushed off by the end of the threads.
  const top = s.top + (parseFloat(getComputedStyle(sc).scrollPaddingTop) || 0)
  if (r.top < top) sc.scrollTop -= top - r.top
  else if (r.bottom > s.bottom) sc.scrollTop += r.bottom - s.bottom
}

/** A row's label and mark, raised over its selected fill. */
const ROW_LABEL_Z = 10

/** The pinned bar: stuck to the top of the rail's scroll area (the sidebar, `NavRail`) while the
 *  threads scroll under it, on the rail's own solid surface and above the rows' labels, so no row
 *  shows through it.
 *
 *  A sticky box sticks inside its scroller's padding, and the sidebar's scroll area pads its top by
 *  `--spacing-l` (`pt-l`), so at `top: 0` the rows passing under the bar showed in that band above it. The bar
 *  sticks that far higher, flush with the sidebar's top edge, and pads its own top by a focus ring's
 *  reach (`RING_REACH`) so New thread's ring is not cut by that edge.
 *
 *  While the rail shows the bar, the scroller's `scroll-padding-top` is the bar's height and a focus
 *  ring's reach (`RING_REACH`): the top the browser keeps clear when focus moves to a row (Tab,
 *  Shift+Tab, the arrow keys), and that `reveal` and `WindowedList` keep clear when they scroll one
 *  into view, so a row never takes the focus behind the bar, nor its ring under it (WCAG 2.4.11). */
const PINNED_BAR = {
  position: 'sticky', top: 'calc(-1 * var(--spacing-l))', zIndex: ROW_LABEL_Z + 1,
  background: 'var(--color-rail)', paddingTop: RING_REACH,
} as const

const countLabel = (n: number) => (n === 0 ? 'no threads' : `${n} thread${n === 1 ? '' : 's'}`)

/** A group as the rail draws it: the threads it shows (all of them, or a search's matches) and
 *  whether it is open. */
export interface RailSection {
  group: ThreadRailGroup
  threads: Thread[]
  open: boolean
}

/** The search's words, as rows are matched against them: trimmed, in any case. */
const searchWords = (query: string) => query.trim().toLocaleLowerCase()

/** The groups the rail draws, in order. With no search, every group with all its threads, open as
 *  the owner left it (`open`), or, never toggled, while it holds a thread. While a search is typed,
 *  only the groups holding a thread whose name matches, each with just its matches and open unless
 *  closed during this search (`searchOpen`): the stored disclosure is left as it was. */
export function railSections(
  groups: readonly ThreadRailGroup[], open: Record<string, boolean>, query: string, searchOpen: Record<string, boolean> = {},
): RailSection[] {
  const words = searchWords(query)
  if (words === '') return groups.map((g) => ({ group: g, threads: g.threads, open: open[g.projectId] ?? g.threads.length > 0 }))
  return groups.flatMap((g) => {
    const threads = g.threads.filter((t) => t.title.toLocaleLowerCase().includes(words))
    return threads.length > 0 ? [{ group: g, threads, open: searchOpen[g.projectId] ?? true }] : []
  })
}

/** The thread rows the rail shows, top to bottom: the threads of every open section. What ⌘1 to ⌘9
 *  count. */
export function shownThreads(sections: readonly RailSection[]): Thread[] {
  return sections.flatMap((s) => (s.open ? s.threads : []))
}

/** The sidebar's threads: New thread and a search at the top, then each project a disclosure
 *  group and each thread a dense row that opens it.
 *
 *  New thread and the search sit above every project whatever the read says, one bar that stays at
 *  the top of the rail while the threads scroll under it, and leaves with them once they have
 *  scrolled past. The search filters the rows by name as the owner types, over what the rail has
 *  read (`railSections`), and nothing matching has a row of its own. The rail says which rows it
 *  shows, top to bottom (`onShown`): the threads ⌘1 to ⌘9 open.
 *
 *  A row says its state in words beside one static mark (running, waiting on you, failed) and in
 *  its accessible name; nothing in the rail moves. The open thread (`selectedKey`, which the shell
 *  reads from the address) carries `aria-current="page"` and the rail's selected fill, and its
 *  group opens so its row can be seen. A long name is cut short on screen, whole in the row's name
 *  and tooltip, and wraps while the row has keyboard focus. Loading, a failed read and an empty
 *  project each have a row of their own. A group's rows window past the shared threshold
 *  (`WindowedList`), so hundreds of threads keep the rail fast and keep their keyboard order. */
export function ThreadRail({ groups, selectedKey, status, failure, onOpen, onRetry, onNewThread, onShown, search, onSearch }: {
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
  /** Opens a new thread, from New thread at the top. */
  onNewThread: () => void
  /** Told the rows the rail shows, top to bottom, whenever they change; `null` while it shows none it
   *  has read (loading, or a failed first read). Keep it stable. */
  onShown?: (threads: Thread[] | null) => void
  /** The search, when the caller keeps it: the shell does, so a collapse, which unmounts the rail,
   *  gives it back as it was typed. Unset, the rail keeps its own. */
  search?: string
  onSearch?: (query: string) => void
}) {
  const base = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const barRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState<Record<string, boolean>>(readGroupsOpen)
  const [ownQuery, setOwnQuery] = useState('')
  const query = search ?? ownQuery
  // The groups closed during this search, which the next change of the search forgets.
  const [searchOpen, setSearchOpen] = useState<Record<string, boolean>>({})
  const searching = searchWords(query) !== ''
  const changeQuery = (v: string) => { (onSearch ?? setOwnQuery)(v); setSearchOpen({}) }
  const sections = useMemo(() => railSections(groups, open, query, searchOpen), [groups, open, query, searchOpen])
  const toggle = (s: RailSection) => {
    const id = s.group.projectId
    if (searching) setSearchOpen((prev) => ({ ...prev, [id]: !s.open }))
    else setOpen((prev) => writeOpen({ ...prev, [id]: !(prev[id] ?? s.group.threads.length > 0) }))
  }

  // What the rail draws: nothing while loading; the last good read's groups after a failed one.
  const knows = status === 'ready' || (status === 'failed' && groups.length > 0)
  const shown = useMemo(() => (knows ? shownThreads(sections) : null), [knows, sections])
  useEffect(() => { onShown?.(shown) }, [shown, onShown])

  // The open thread's group opens when the open thread changes (an arrival, a reload, Back), so its
  // row can be seen. Only then: closing that group afterwards keeps it closed.
  const selectedGroup = groups.find((g) => g.threads.some((t) => t.key === selectedKey))
  const selectedProject = selectedGroup?.projectId
  useLayoutEffect(() => {
    if (selectedProject === undefined) return
    setOpen((prev) => (prev[selectedProject] === false ? writeOpen({ ...prev, [selectedProject]: true }) : prev))
  }, [selectedKey, selectedProject])

  // The scroller keeps the bar's height clear at its top (`PINNED_BAR`), as long as the bar is there.
  useEffect(() => {
    const bar = barRef.current
    const sc = bar && scrollerOf(bar)
    if (!bar || !sc) return
    const fit = () => { sc.style.scrollPaddingTop = `${bar.offsetHeight + RING_REACH}px` }
    fit()
    if (typeof ResizeObserver === 'undefined') return () => { sc.style.scrollPaddingTop = '' }
    const watch = new ResizeObserver(fit)
    watch.observe(bar)
    return () => { watch.disconnect(); sc.style.scrollPaddingTop = '' }
  }, [])

  const selectedShown = sections.some((s) => s.open && s.threads.some((t) => t.key === selectedKey))
  useEffect(() => {
    if (!selectedKey || !selectedShown) return
    const rows = rootRef.current?.querySelectorAll<HTMLElement>('[data-thread-key]') ?? []
    const row = [...rows].find((r) => r.dataset.threadKey === selectedKey)
    if (row) reveal(row)
  }, [selectedKey, selectedShown])

  const matches = sections.reduce((n, sec) => n + sec.threads.length, 0)
  const noMatch = `No threads match “${query.trim()}”`
  const headingId = `${base}-heading`
  return (
    <div ref={rootRef} role="group" aria-labelledby={headingId} className="flex flex-col">
      <Eyebrow id={headingId} className="px-s pt-l">Threads</Eyebrow>
      <div ref={barRef} className="flex flex-col" style={PINNED_BAR}>
        <button
          type="button" onClick={onNewThread} title={`New thread (${bindingLabel('new-chat')})`}
          data-type="body-m"
          className="flex w-full items-center gap-s rounded-pill px-s py-1 text-left text-on-surface-var transition-colors duration-(--duration-short) hover:bg-surface-low/60 hover:text-on-surface"
          style={withWeight({ minHeight: 32 }, 400)}>
          <Edit3 size={16} strokeWidth={2} aria-hidden className="shrink-0" />
          <span className="min-w-0 flex-1 truncate">New thread</span>
        </button>
        <div className="py-1">
          <SearchField size="sm" surface="container" value={query} onChange={changeQuery}
            placeholder="Search threads" ariaLabel="Search threads" name="thread-search" />
        </div>
      </div>
      <ResultAnnouncement count={matches} noun="threads" active={searching && status !== 'loading'} empty={noMatch} />
      {status === 'loading' && (
        <div role="status" data-type="body-s" className="px-s py-1 text-on-surface-low">Loading threads…</div>
      )}
      {status === 'failed' && (
        <div data-type="body-s" className="flex items-center gap-s px-s py-1">
          <span className="min-w-0 flex-1 text-on-surface-var">{failure || "Couldn't load your threads."}</span>
          <Button variant="ghost" size="xs" onClick={onRetry}>Retry</Button>
        </div>
      )}
      {status === 'offline' && (
        // Offline is not a failed read and not an empty list: the gateway is away, so the threads
        // are the last ones it listed, or none yet, and they come back with it.
        <div data-type="body-s" className="flex items-center gap-s px-s py-1">
          <span className="min-w-0 flex-1 text-on-surface-var">
            {groups.length ? 'Offline. These are the threads from before the gateway stopped answering.' : 'Offline. Your threads show when the gateway answers again.'}
          </span>
          <Button variant="ghost" size="xs" onClick={onRetry}>Retry</Button>
        </div>
      )}
      {status === 'ready' && groups.length === 0 && (
        <div data-type="body-s" className="px-s py-1 text-on-surface-low">No threads yet</div>
      )}
      {status !== 'loading' && searching && groups.length > 0 && sections.length === 0 && (
        // Said aloud by the announcement above; this is the row a sighted owner reads.
        <div data-type="body-s" className="px-s py-1 text-on-surface-low">{noMatch}</div>
      )}
      {status !== 'loading' && sections.map((section, i) => {
        const { group: g, threads, open: opened } = section
        const listId = `${base}-group-${i}`
        const total = g.threads.length
        return (
          <div key={`project:${g.projectId}`} className="flex flex-col">
            <button
              type="button" onClick={() => toggle(section)}
              aria-expanded={opened} aria-controls={opened ? listId : undefined}
              aria-label={searching ? `${g.name}, ${threads.length} of ${total} thread${total === 1 ? '' : 's'}` : `${g.name}, ${countLabel(total)}`}
              title={g.name}
              data-type="body-s"
              className="group flex w-full items-center gap-xs rounded-pill px-s py-1 text-left text-on-surface-var transition-colors duration-(--duration-short) hover:bg-surface-low/60 hover:text-on-surface"
              style={withWeight({ minHeight: 32 }, 470)}>
              <ChevronRight size={16} strokeWidth={2} aria-hidden className={cx('shrink-0', opened && 'rotate-90')} />
              <span className="min-w-0 flex-1 truncate group-focus-visible:whitespace-normal group-focus-visible:break-words">{g.name}</span>
              <span data-type="caption" className="shrink-0 tabular-nums">{threads.length}</span>
            </button>
            {opened && (
              <div id={listId}>
                {threads.length === 0
                  ? <div data-type="body-s" className="py-1 pl-3xl pr-s text-on-surface-low">No threads yet</div>
                  : (
                    <WindowedList
                      items={threads} rowKey={(t) => t.key}
                      // Variable: a row is 32 px, and grows while it has focus to show its whole name.
                      rowHeights="variable" estimateRowHeight={32}
                      noun={`threads in ${g.name}`}
                      findHint="use Search threads at the top of the threads, which searches every thread by name."
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
      title={thread.pipNote ? `${thread.title} — ${thread.pipNote}` : thread.title}
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
      <span className="relative min-w-0 flex-1 truncate group-focus-visible:whitespace-normal group-focus-visible:break-words" style={{ zIndex: ROW_LABEL_Z }}>{thread.title}</span>
      {pip && (
        <span data-type="caption" className="relative shrink-0" style={{ zIndex: ROW_LABEL_Z }}>
          <StateMark family={pip.family}>{pip.word}</StateMark>
        </span>
      )}
    </button>
  )
}
