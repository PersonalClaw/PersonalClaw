import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Code2, Edit3, Folder, Repeat, Tag, type LucideIcon } from 'lucide-react'
import type { Thread, ThreadKind, ThreadPip } from '../lib/useThreads'
import type { GroupBy, HistoryGroup } from '../app/chatHistoryGroups'
import type { PhaseFamily } from '../lib/workPhase'
import { bindingLabel } from '../app/keymap'
import { withWeight } from '../design/fontWeight'
import { Button } from './Button'
import { MenuRow, Popover } from './Popover'
import { ResultAnnouncement } from './ListControls'
import { SearchField } from './SearchField'
import { StateMark } from './StateMark'
import { WindowedList } from './WindowedList'
import { cx } from './cx'
import { RING_REACH } from './NavRail'

/** One group of the chat history: its key (a folder's or a tag's, `chatHistoryGroups.ts`), the name
 *  it is shown under, and its rows in order. */
export type ThreadRailGroup = HistoryGroup

/** `loading`: nothing read yet. `failed`: the last read failed, and the groups are what the last
 *  good read said (none when no read has succeeded). `offline`: the gateway is not answering at
 *  all, so the groups are what it said before it stopped, and no read is worth blaming. */
export type ThreadRailStatus = 'loading' | 'ready' | 'failed' | 'offline'

/** Which groups the owner opened or closed, by group key. A group with no entry is open while it
 *  holds a row and closed while it holds none. */
const OPEN_KEY = 'chat-history-groups-open'

/** What a goal loop's or code session's row says it is, in words and by its mark. A chat's row says
 *  nothing: a chat is what the chat history is. */
const KIND_MARK: Partial<Record<ThreadKind, { word: string; icon: LucideIcon }>> = {
  loop: { word: 'Goal loop', icon: Repeat },
  code: { word: 'Code session', icon: Code2 },
}

/** The two ways the chat history groups, as the Group by control names them. */
const GROUP_BY_WORD: Record<GroupBy, string> = { folder: 'Folders', tag: 'Tags' }
/** The Group by control's face: the grouping it shows, as the field's row has room for. */
const GROUP_BY_ICON: Record<GroupBy, LucideIcon> = { folder: Folder, tag: Tag }

/** Each pip's state family (the shared state tokens the orb and the buddy read) and its words. A
 *  project's page lists its chats with the same marks (`pages/projects/ProjectThreads.tsx`). */
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

/** The pinned bar: New chat, and the search with its Group by control, stuck to the
 *  top of the rail's scroll area (the chat history, between the sidebar's two blocks: `NavRail`)
 *  while the rows scroll under it, on the rail's own solid surface and above the rows' labels, so no
 *  row shows through it.
 *
 *  The scroll area pads nothing at its top, so the bar sticks flush with its top edge, and pads its
 *  own top by a focus ring's reach (`RING_REACH`) so a control's ring is not cut by that edge.
 *
 *  While the rail shows the bar, the scroller's `scroll-padding-top` is the bar's height and a focus
 *  ring's reach (`RING_REACH`): the top the browser keeps clear when focus moves to a row (Tab,
 *  Shift+Tab, the arrow keys), and that `reveal` and `WindowedList` keep clear when they scroll one
 *  into view, so a row never takes the focus behind the bar, nor its ring under it (WCAG 2.4.11). */
const PINNED_BAR = {
  position: 'sticky', top: 0, zIndex: ROW_LABEL_Z + 1,
  background: 'var(--color-rail)', paddingTop: RING_REACH,
} as const

const countLabel = (n: number) => (n === 0 ? 'no chats' : `${n} chat${n === 1 ? '' : 's'}`)

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
  if (words === '') return groups.map((g) => ({ group: g, threads: g.threads, open: open[g.id] ?? g.threads.length > 0 }))
  return groups.flatMap((g) => {
    const threads = g.threads.filter((t) => t.title.toLocaleLowerCase().includes(words))
    return threads.length > 0 ? [{ group: g, threads, open: searchOpen[g.id] ?? true }] : []
  })
}

/** The rows the rail shows, top to bottom: the rows of every open section. What ⌘1 to ⌘9 count, a
 *  chat listed under two tags once (`chatHistoryGroups.numberedRows`). */
export function shownThreads(sections: readonly RailSection[]): Thread[] {
  return sections.flatMap((s) => (s.open ? s.threads : []))
}

/** The sidebar's chat history (#5877): New chat and a search with the Group by control at the end
 *  of its row, at the top, then each folder (or tag) a disclosure group and each chat, goal loop or
 *  code session a dense row that opens it.
 *
 *  New chat and the search row sit above every group whatever the read says, one bar that
 *  stays at the top of the chat history while the rows scroll under it. The Group by control
 *  switches between the owner's folders and tags (`groupBy`, `onGroupBy`). The search filters the
 *  rows by name as the owner types, over what the rail has read (`railSections`), and nothing
 *  matching has a row of its own. The rail says which rows it shows, top to bottom (`onShown`): the
 *  rows ⌘1 to ⌘9 open.
 *
 *  A row says its state in words beside one static mark (running, waiting on you, failed) and in its
 *  accessible name; a goal loop's or code session's row also says its kind. Nothing in the rail
 *  moves. The open row (`selectedKey`, which the shell reads from the address) carries
 *  `aria-current="page"` and the rail's selected fill, under every group it is listed in, and its
 *  group opens so its row can be seen. A long name is cut short on screen, whole in the row's name
 *  and tooltip, and wraps while the row has keyboard focus. Loading, a failed read and an empty list
 *  each have a row of their own. A group's rows window past the shared threshold (`WindowedList`),
 *  so hundreds of chats keep the rail fast and keep their keyboard order. */
export function ThreadRail({ groups, selectedKey, status, failure, onOpen, onRetry, onNewThread, onShown, search, onSearch, groupBy = 'folder', onGroupBy }: {
  /** The groups in the order they show, each with its rows in order. */
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
  /** Opens a new chat, from New chat at the top. */
  onNewThread: () => void
  /** Told the rows the rail shows, top to bottom, whenever they change; `null` while it shows none it
   *  has read (loading, or a failed first read). Keep it stable. */
  onShown?: (threads: Thread[] | null) => void
  /** The search, when the caller keeps it: the shell does, so a collapse, which unmounts the rail,
   *  gives it back as it was typed. Unset, the rail keeps its own. */
  search?: string
  onSearch?: (query: string) => void
  /** How the groups are made, which the search row's Group by control says and changes. */
  groupBy?: GroupBy
  /** Called with the grouping the owner picks. Unset, the control is not drawn. */
  onGroupBy?: (by: GroupBy) => void
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
    const id = s.group.id
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
  const selectedGroupId = selectedGroup?.id
  useLayoutEffect(() => {
    if (selectedGroupId === undefined) return
    setOpen((prev) => (prev[selectedGroupId] === false ? writeOpen({ ...prev, [selectedGroupId]: true }) : prev))
  }, [selectedKey, selectedGroupId])

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
  const noMatch = `No chats match “${query.trim()}”`
  return (
    // Named, not labelled by a heading on screen: the list's rows say what it holds, and a label row
    // above them cost the sidebar a row's height for the word a screen reader still hears here.
    <div ref={rootRef} role="group" aria-label="Chat history" className="flex flex-col">
      <div ref={barRef} className="flex flex-col" style={PINNED_BAR}>
        <button
          type="button" onClick={onNewThread} title={`New chat (${bindingLabel('new-chat')})`}
          data-type="body-m"
          className="mt-s flex w-full items-center gap-s rounded-pill px-s py-1 text-left text-on-surface-var transition-colors duration-(--duration-short) hover:bg-surface-low/60 hover:text-on-surface"
          style={withWeight({ minHeight: 32 }, 400)}>
          <Edit3 size={16} strokeWidth={2} aria-hidden className="shrink-0" />
          <span className="min-w-0 flex-1 truncate">New chat</span>
        </button>
        {/* The search, and at its end how the list is grouped: after the field in tab order. */}
        <div className="flex items-center gap-xs py-1">
          <div className="min-w-0 flex-1">
            <SearchField size="sm" surface="container" value={query} onChange={changeQuery}
              placeholder="Search" ariaLabel="Search chats" name="chat-search" />
          </div>
          {onGroupBy && <GroupByControl value={groupBy} onChange={onGroupBy} />}
        </div>
      </div>
      <ResultAnnouncement count={matches} noun="chats" active={searching && status !== 'loading'} empty={noMatch} />
      {status === 'loading' && (
        <div role="status" data-type="body-s" className="px-s py-1 text-on-surface-low">Loading chats…</div>
      )}
      {status === 'failed' && (
        <div data-type="body-s" className="flex items-center gap-s px-s py-1">
          <span className="min-w-0 flex-1 text-on-surface-var">{failure || "Couldn't load your chats."}</span>
          <Button variant="ghost" size="xs" onClick={onRetry}>Retry</Button>
        </div>
      )}
      {status === 'offline' && (
        // Offline is not a failed read and not an empty list: the gateway is away, so the chats are
        // the last ones it listed, or none yet, and they come back with it.
        <div data-type="body-s" className="flex items-center gap-s px-s py-1">
          <span className="min-w-0 flex-1 text-on-surface-var">
            {groups.length ? 'Offline. These are the chats from before the gateway stopped answering.' : 'Offline. Your chats show when the gateway answers again.'}
          </span>
          <Button variant="ghost" size="xs" onClick={onRetry}>Retry</Button>
        </div>
      )}
      {status === 'ready' && groups.length === 0 && (
        <div data-type="body-s" className="px-s py-1 text-on-surface-low">No chats yet</div>
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
          <div key={`group:${g.id}`} className="flex flex-col">
            <button
              type="button" onClick={() => toggle(section)}
              aria-expanded={opened} aria-controls={opened ? listId : undefined}
              aria-label={searching ? `${g.name}, ${threads.length} of ${total} chat${total === 1 ? '' : 's'}` : `${g.name}, ${countLabel(total)}`}
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
                  ? <div data-type="body-s" className="py-1 pl-3xl pr-s text-on-surface-low">No chats yet</div>
                  : (
                    <WindowedList
                      items={threads} rowKey={(t) => t.key}
                      // Variable: a row is 32 px, and grows while it has focus to show its whole name.
                      rowHeights="variable" estimateRowHeight={32}
                      // The open row, however far from the drawn window, in the first group that
                      // lists it: the address lights it, so it is brought into view.
                      revealKey={g.id === selectedGroupId ? selectedKey : undefined}
                      noun={`chats in ${g.name}`}
                      findHint="use Search chats at the top of the chat history, which searches every chat by name."
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

/** The Group by control at the end of the search row: which of the two groupings shows, and the
 *  menu that picks the other. Named by its value ("Group by: Folders"), and the field's height. Its
 *  face is the grouping's icon, not its word: beside the word the field kept too little room to say
 *  what it is for. */
function GroupByControl({ value, onChange }: { value: GroupBy; onChange: (by: GroupBy) => void }) {
  const Icon = GROUP_BY_ICON[value]
  return (
    <Popover align="right" placement="bottom" width={168} portal
      trigger={(open, toggle) => (
        <button type="button" onClick={toggle} aria-expanded={open} aria-label={`Group by: ${GROUP_BY_WORD[value]}`}
          title={`Grouped by ${GROUP_BY_WORD[value].toLowerCase()}: group the chat history by your folders or your tags`}
          className="inline-flex h-8 shrink-0 items-center gap-0.5 rounded-md bg-surface-container px-1.5 text-on-surface-var transition-colors duration-(--duration-short) hover:bg-surface-high hover:text-on-surface">
          <Icon size={16} strokeWidth={2} aria-hidden />
          <ChevronDown size={12} strokeWidth={2} aria-hidden />
        </button>
      )}>
      {(close) => (['folder', 'tag'] as const).map((by) => (
        <MenuRow key={by} label={GROUP_BY_WORD[by]} nameIsLabel selected={by === value}
          icon={by === value ? <Check size={15} aria-hidden /> : <span className="inline-block w-[15px]" aria-hidden />}
          onClick={() => { onChange(by); close() }} />
      ))}
    </Popover>
  )
}

function ThreadRow({ thread, selected, onOpen }: { thread: Thread; selected: boolean; onOpen: (route: string) => void }) {
  const pip = thread.pip ? PIP[thread.pip] : null
  const kind = KIND_MARK[thread.kind]
  const name = kind ? `${kind.word}: ${thread.title}` : thread.title
  const KindIcon = kind?.icon
  return (
    <button
      type="button" onClick={() => onOpen(thread.route)}
      data-thread-key={thread.key}
      aria-label={pip ? `${name}, ${pip.word}` : name}
      title={thread.pipNote ? `${name} — ${thread.pipNote}` : name}
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
      {KindIcon && (
        <KindIcon size={14} strokeWidth={2} aria-hidden className="relative shrink-0 text-on-surface-low" style={{ zIndex: ROW_LABEL_Z }} />
      )}
      <span className="relative min-w-0 flex-1 truncate group-focus-visible:whitespace-normal group-focus-visible:break-words" style={{ zIndex: ROW_LABEL_Z }}>{thread.title}</span>
      {pip && (
        <span data-type="caption" className="relative shrink-0" style={{ zIndex: ROW_LABEL_Z }}>
          <StateMark family={pip.family}>{pip.word}</StateMark>
        </span>
      )}
    </button>
  )
}
