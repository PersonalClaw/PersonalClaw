import { useEffect, useMemo, useRef, useState } from 'react'
import { Archive, Blocks, Code2, Edit3, Maximize2, Pin, Repeat, type LucideIcon } from 'lucide-react'
import type { Thread, ThreadKind, ThreadPip } from '../lib/useThreads'
import type { PhaseFamily } from '../lib/workPhase'
import { bindingLabel } from '../app/keymap'
import { withWeight } from '../design/fontWeight'
import { prefersReducedMotion } from '../design/motion'
import { startedByName, startedByTitle } from '../pages/chat/StartedByApp'
import { Button } from './Button'
import { FilterMenu, type FilterSectionDef } from './FilterMenu'
import { headerFilterLabel } from './HeaderFilter'
import { ResultAnnouncement } from './ListControls'
import { SearchField } from './SearchField'
import { WindowedList } from './WindowedList'
import { cx } from './cx'
import { RING_REACH, SELECTED_PILL } from './NavRail'

/** `loading`: nothing read yet. `failed`: the last read failed, and the rows are what the last good
 *  read said (none when no read has succeeded). `offline`: the gateway is not answering at all, so
 *  the rows are what it said before it stopped, and no read is worth blaming. */
export type ThreadRailStatus = 'loading' | 'ready' | 'failed' | 'offline'

/** Where the list is drawn: the sidebar, on the rail's own ground, or the Chat history panel, on
 *  the dock's. */
export type ThreadRailSurface = 'rail' | 'panel'

/** What a goal loop's or code session's row says it is, in words and by its mark. A chat's row says
 *  nothing: a chat is what the chat history is. */
const KIND_MARK: Partial<Record<ThreadKind, { word: string; icon: LucideIcon }>> = {
  loop: { word: 'Goal loop', icon: Repeat },
  code: { word: 'Code session', icon: Code2 },
}

/** Each pip's state family (the shared state tokens the orb and the buddy read) and its words. A
 *  project's page lists its chats with the same marks (`pages/projects/ProjectThreads.tsx`). */
export const PIP: Record<ThreadPip, { family: PhaseFamily; word: string }> = {
  waiting: { family: 'waiting', word: 'waiting on you' },
  running: { family: 'active', word: 'running' },
  failed: { family: 'blocked', word: 'failed' },
  unread: { family: 'quiet', word: 'unread' },
}

/** Each surface's ground, which the pinned bar paints so no row shows through it, and its search
 *  field's name, which is the field's id: both lists are on screen on the New chat page with the
 *  panel open. */
const SURFACE: Record<ThreadRailSurface, { ground: string; field: string }> = {
  rail: { ground: 'var(--color-rail)', field: 'chat-search' },
  panel: { ground: 'var(--color-surface)', field: 'chat-panel-search' },
}

/** The list's scroll area: *el*'s nearest scrolling ancestor (the sidebar, `NavRail`, or the Chat
 *  history panel's body, `SidePanel`). */
function scrollerOf(el: HTMLElement): HTMLElement | null {
  let sc = el.parentElement
  while (sc && !/(auto|scroll)/.test(getComputedStyle(sc).overflowY)) sc = sc.parentElement
  return sc
}

/** Scroll the list's scroller just far enough to show *el*, clear of the top the scroller keeps
 *  clear (its `scroll-padding-top`, the pinned bar's height: see `pinnedBar`). Never
 *  `scrollIntoView`, which may scroll the page as well, and would for a drawer parked off-screen. */
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

/** The pinned bar: New chat and Open Chat history (the sidebar's), then the search with its Sort
 *  and filter menu, stuck to the top of the list's scroll area while the rows scroll under it, on
 *  its surface's own ground (`SURFACE`) and above the rows' labels, so no row shows through it.
 *
 *  The sidebar's scroll area pads nothing at its top; the panel's body pads its top, and a sticky box
 *  sticks inside a scroller's padding, so the bar's `top` is minus that padding (`fit`). In both it
 *  sticks flush with the scrollport's top edge, which `scroll-padding-top` counts from, and pads its
 *  own top by a focus ring's reach (`RING_REACH`) so a control's ring is not cut by that edge.
 *
 *  While the list shows the bar, the scroller's `scroll-padding-top` is the bar's height and a focus
 *  ring's reach (`RING_REACH`): the top the browser keeps clear when focus moves to a row (Tab,
 *  Shift+Tab, the arrow keys), and that `reveal` and `WindowedList` keep clear when they scroll one
 *  into view, so a row never takes the focus behind the bar, nor its ring under it (WCAG 2.4.11). */
const pinnedBar = (ground: string) => ({
  position: 'sticky', top: 0, zIndex: ROW_LABEL_Z + 1, background: ground, paddingTop: RING_REACH,
} as const)

/** The search's words, as rows are matched against them: trimmed, in any case. */
const searchWords = (query: string) => query.trim().toLocaleLowerCase()

/** The rows a search shows: those whose title holds its words, in the list's order. */
export function searched(rows: readonly Thread[], query: string): Thread[] {
  const words = searchWords(query)
  return words === '' ? [...rows] : rows.filter((t) => t.title.toLocaleLowerCase().includes(words))
}

/** The sidebar's first row: New chat, and Open Chat history at its end. */
export interface ThreadRailTop {
  onNewChat: () => void
  onOpenHistory: () => void
  /** The Chat history page is the page: Open Chat history is lit. */
  historyLit: boolean
}

/** The chat list (#5877, one list for the sidebar and the Chat history panel): at the top, the
 *  sidebar's New chat and Open Chat history (`top`), then a search with the Sort and filter menu at
 *  the end of its row; under them every chat, goal loop and code session the view lists, one flat
 *  list of dense rows that each open one.
 *
 *  The bar stays at the top of the list whatever the read says, while the rows scroll under it. The
 *  menu orders and filters the rows (`menu`, from `useChatList`); the search filters them by name as
 *  the owner types, over the rows in hand, and nothing matching has a row of its own. The list says
 *  which rows it shows, top to bottom (`onShown`): the rows ⌘1 to ⌘9 open.
 *
 *  A row says its state with one static mark (running, waiting on you, failed, unread) and in its
 *  accessible name; a goal loop's or code session's row also says its kind, a pinned chat its pin,
 *  an archived one (while the view lists both) its archive, and an app's chat the app's name. Nothing
 *  in the list moves. The open row (`selectedKey`, which the shell reads from the address) carries
 *  `aria-current="page"` and the selected fill. A long name is cut short on screen, whole in the
 *  row's name and tooltip, and wraps while the row has keyboard focus. Loading, a failed read, an
 *  empty list and an empty view each have a row of their own. The rows window past the shared
 *  threshold (`WindowedList`), so hundreds of chats keep the list fast and keep their keyboard order. */
export function ThreadRail({
  rows, selectedKey, status, failure, onOpen, onRetry, top, surface = 'rail', label = 'Chat history',
  onShown, search, onSearch, menu, filtered = false, onClearFilters, markArchived = false,
}: {
  /** The rows the view lists, in its order, before the search (`useChatList`). */
  rows: Thread[]
  /** The key of the open row, or '' when the page is no chat (always '' in the panel). */
  selectedKey: string
  status: ThreadRailStatus
  /** The sentence a failed read shows, beside Retry. */
  failure?: string
  /** Opens a row at its route (no leading `#/`). */
  onOpen: (route: string) => void
  /** Reads the rows again after a failed read. */
  onRetry: () => void
  /** The sidebar's first row; the panel passes none. */
  top?: ThreadRailTop
  /** Where the list is drawn: the bar's ground and the search field's name (`SURFACE`). */
  surface?: ThreadRailSurface
  /** The list's name. The panel passes "Chats": its region is already named Chat history. */
  label?: string
  /** Told the rows the list shows, top to bottom, whenever they change; `null` while it shows none it
   *  has read (loading, or a failed first read). Keep it stable. */
  onShown?: (threads: Thread[] | null) => void
  /** The search, when the caller keeps it: the shell does, so a collapse, which unmounts the rail,
   *  gives it back as it was typed. Unset, the list keeps its own. */
  search?: string
  onSearch?: (query: string) => void
  /** The Sort and filter menu's sections (`chatListView.viewSections`). Unset, no menu is drawn. */
  menu?: FilterSectionDef[]
  /** A filter is off its default, so an empty view says so and offers Clear filters. */
  filtered?: boolean
  /** Puts every filter back at its default, keeping the sort. */
  onClearFilters?: () => void
  /** The view lists archived chats beside active ones, so an archived row says it is. */
  markArchived?: boolean
}) {
  const rootRef = useRef<HTMLDivElement>(null)
  const barRef = useRef<HTMLDivElement>(null)
  const [ownQuery, setOwnQuery] = useState('')
  const query = search ?? ownQuery
  const searching = searchWords(query) !== ''
  const changeQuery = (v: string) => { (onSearch ?? setOwnQuery)(v) }
  const matches = useMemo(() => searched(rows, query), [rows, query])
  const { ground, field } = SURFACE[surface]

  // What the list draws: nothing while loading; the last good read's rows after a failed one.
  const knows = status === 'ready' || (status === 'failed' && rows.length > 0)
  const shown = useMemo(() => (knows ? matches : null), [knows, matches])
  useEffect(() => { onShown?.(shown) }, [shown, onShown])

  // The scroller keeps the bar's height clear at its top (`pinnedBar`), as long as the bar is there,
  // and knows that height (`--chat-list-bar`), which the sidebar's floor for the list follows
  // (NavRail's `HISTORY_FLOOR`) as the bar grows at large text.
  useEffect(() => {
    const bar = barRef.current
    const sc = bar && scrollerOf(bar)
    if (!bar || !sc) return
    const fit = () => {
      sc.style.scrollPaddingTop = `${bar.offsetHeight + RING_REACH}px`
      sc.style.setProperty('--chat-list-bar', `${bar.offsetHeight}px`)
      bar.style.top = `${-(parseFloat(getComputedStyle(sc).paddingTop) || 0)}px`
    }
    const unfit = () => { sc.style.scrollPaddingTop = ''; sc.style.removeProperty('--chat-list-bar') }
    fit()
    if (typeof ResizeObserver === 'undefined') return unfit
    const watch = new ResizeObserver(fit)
    watch.observe(bar)
    return () => { watch.disconnect(); unfit() }
  }, [])

  const selectedShown = matches.some((t) => t.key === selectedKey)
  useEffect(() => {
    if (!selectedKey || !selectedShown) return
    const found = rootRef.current?.querySelectorAll<HTMLElement>('[data-thread-key]') ?? []
    const row = [...found].find((r) => r.dataset.threadKey === selectedKey)
    if (row) reveal(row)
  }, [selectedKey, selectedShown])

  const noMatch = `No chats match “${query.trim()}”`
  const menuLabel = menu ? headerFilterLabel(menu, 'Sort and filter') : ''
  return (
    // Named, not labelled by a heading on screen: the list's rows say what it holds, and a label row
    // above them cost the sidebar a row's height for the word a screen reader still hears here.
    <div ref={rootRef} role="group" aria-label={label} data-chat-list={surface} className="flex flex-col">
      <div ref={barRef} data-chat-list-bar className="flex flex-col" style={pinnedBar(ground)}>
        {top && (
          // New chat is never cut short. At large text its label wraps to two lines beside Open
          // Chat history, and where even one word has no room there (the narrowest rail at 200%),
          // the row wraps and Open Chat history takes a line of its own, at the row's end. New chat
          // has no min-w-0, so it never narrows below its longest word. One line at 100%.
          <div className="mt-s flex flex-wrap items-center gap-xs">
            <button
              type="button" onClick={top.onNewChat} title={`New chat (${bindingLabel('new-chat')})`}
              data-type="body-m"
              className="flex flex-1 items-center gap-s rounded-pill px-s py-1 text-left text-on-surface-var transition-colors duration-(--duration-short) hover:bg-surface-low/60 hover:text-on-surface"
              style={withWeight({ minHeight: 32 }, 400)}>
              <Edit3 size={16} strokeWidth={2} aria-hidden className="shrink-0" />
              <span className="min-w-0 flex-1">New chat</span>
            </button>
            {/* The full page, beside New chat: the glyph the panel's own way there uses. */}
            <button
              type="button" onClick={top.onOpenHistory} aria-label="Open Chat history" title="Open Chat history"
              aria-current={top.historyLit ? 'page' : undefined}
              className={cx(
                'relative ml-auto inline-flex size-8 shrink-0 items-center justify-center rounded-pill transition-colors duration-(--duration-short)',
                top.historyLit ? 'text-on-surface' : 'text-on-surface-var hover:bg-surface-low/60 hover:text-on-surface',
              )}>
              {top.historyLit && <span aria-hidden className={SELECTED_PILL} />}
              <Maximize2 size={16} strokeWidth={2} aria-hidden className="relative" style={{ zIndex: ROW_LABEL_Z }} />
            </button>
          </div>
        )}
        {/* The search, and at its end the Sort and filter menu: after the field in tab order. */}
        <div className={cx('flex items-center gap-xs py-1', !top && 'mt-s')}>
          <div className="min-w-0 flex-1">
            <SearchField size="sm" surface="container" value={query} onChange={changeQuery}
              placeholder="Search" ariaLabel="Search chats" name={field} edge />
          </div>
          {menu && <FilterMenu size="sm" sections={menu} label={menuLabel} />}
        </div>
      </div>
      <ResultAnnouncement count={matches.length} noun="chats" active={searching && status !== 'loading'} empty={noMatch} />
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
            {rows.length ? 'Offline. These are the chats from before the gateway stopped answering.'
              : filtered ? 'Offline. No chats in this view.' : 'Offline. Your chats show when the gateway answers again.'}
          </span>
          <Button variant="ghost" size="xs" onClick={onRetry}>Retry</Button>
        </div>
      )}
      {status === 'ready' && rows.length === 0 && !filtered && (
        <div data-type="body-s" className="px-s py-1 text-on-surface-low">No chats yet</div>
      )}
      {status === 'ready' && rows.length === 0 && filtered && (
        // The row wraps: where the sentence and Clear filters do not fit on one line, Clear filters
        // drops below it, and the sentence keeps a line of its own rather than a narrow column.
        <div data-type="body-s" className="flex flex-wrap items-center gap-x-s px-s py-1">
          <span className="min-w-fit flex-1 text-on-surface-low">No chats in this view</span>
          {/* The button goes with the empty view it sits in, so the focus goes to the search. */}
          {onClearFilters && <Button variant="ghost" size="xs" onClick={() => { onClearFilters(); document.getElementById(field)?.focus() }}>Clear filters</Button>}
        </div>
      )}
      {status !== 'loading' && searching && rows.length > 0 && matches.length === 0 && (
        // Said aloud by the announcement above; this is the row a sighted owner reads.
        <div data-type="body-s" className="px-s py-1 text-on-surface-low">{noMatch}</div>
      )}
      {status !== 'loading' && matches.length > 0 && (
        <WindowedList
          items={matches} rowKey={(t) => t.key}
          // Variable: a row is 32 px, and grows while it has focus to show its whole name.
          rowHeights="variable" estimateRowHeight={32}
          // The open row, however far from the drawn window: the address lights it, so it is
          // brought into view.
          revealKey={selectedKey || undefined}
          noun="chats"
          findHint="use Search chats at the top of the list, which searches every chat by name."
          className="flex flex-col">
          {(t) => <ThreadRow thread={t} selected={t.key === selectedKey} archivedMark={markArchived && !!t.archived} onOpen={onOpen} />}
        </WindowedList>
      )}
    </div>
  )
}

/** One row of the list. Its title starts flush with the bar's rows (`px-s`, as New chat). A row's
 *  marks sit before its title in the kind icon's slot (a kind, a pin, an archive), an app's chat
 *  names the app at its end, and its state dot sits at its right end, so a dot pushes no title right
 *  and a row with no state keeps no room for one. */
function ThreadRow({ thread, selected, archivedMark, onOpen }: {
  thread: Thread; selected: boolean; archivedMark: boolean; onOpen: (route: string) => void
}) {
  const pip = thread.pip ? PIP[thread.pip] : null
  const kind = KIND_MARK[thread.kind]
  const app = thread.startedBy ? startedByName(thread.startedBy) : ''
  const said = [
    kind ? `${kind.word}: ${thread.title}` : thread.title,
    thread.pinned && 'pinned',
    archivedMark && 'archived',
    app && `started by ${app}`,
  ].filter(Boolean).join(', ')
  const name = pip ? `${said}, ${pip.word}` : said
  const tip = [said, app && startedByTitle(app), thread.pipNote].filter(Boolean).join(' — ')
  const marks: LucideIcon[] = [kind?.icon, thread.pinned ? Pin : undefined, archivedMark ? Archive : undefined]
    .filter((m): m is LucideIcon => !!m)
  return (
    <button
      type="button" onClick={() => onOpen(thread.route)}
      data-thread-key={thread.key}
      aria-label={name}
      title={tip}
      aria-current={selected ? 'page' : undefined}
      data-type="body-m"
      className={cx(
        'group relative flex w-full items-center gap-s rounded-pill px-s py-1 text-left transition-colors duration-(--duration-short)',
        selected ? 'text-on-surface' : 'text-on-surface-var hover:bg-surface-low/60 hover:text-on-surface',
      )}
      style={withWeight({ minHeight: 32 }, selected ? 470 : 400)}>
      {selected && <span aria-hidden className={SELECTED_PILL} />}
      {marks.map((Mark, i) => (
        <Mark key={i} size={14} strokeWidth={2} aria-hidden data-row-mark className="relative shrink-0 text-on-surface-low" style={{ zIndex: ROW_LABEL_Z }} />
      ))}
      <span className="relative min-w-0 flex-1 truncate group-focus-visible:whitespace-normal group-focus-visible:break-words" style={{ zIndex: ROW_LABEL_Z }}>{thread.title}</span>
      {app && (
        <span data-type="caption" data-started-by className="relative inline-flex min-w-0 max-w-[40%] shrink items-center gap-0.5 text-on-surface-low" style={{ zIndex: ROW_LABEL_Z }}>
          <Blocks size={12} strokeWidth={2} aria-hidden className="shrink-0" />
          <span className="min-w-0 truncate">{app}</span>
        </span>
      )}
      {pip && <StateDot family={pip.family} word={pip.word} />}
    </button>
  )
}

/** A row's dot colour, by its state's family: green while it runs (`--color-ok`), orange while it
 *  waits on you, grey when it finished unread and red when it failed. The rail's own reading of the
 *  states, the owner's: the orb, the buddy and the project page keep the shared state tokens, where
 *  running is the brand colour. */
const DOT_TONE: Record<PhaseFamily, string> = {
  active: 'var(--color-ok)',
  waiting: 'var(--color-state-waiting)',
  quiet: 'var(--color-state-quiet)',
  blocked: 'var(--color-state-blocked)',
}
/** The states where something is happening, whose dot glows. */
const GLOWS: ReadonlySet<PhaseFamily> = new Set(['active', 'waiting'])

/** A row's state as a dot in its colour (DOT_TONE), with the shared running glow (`.status-pulse`)
 *  in the same colour while its work runs or waits on you; unread and failed are steady dots. The
 *  words are the dot's tooltip and part of the row's name, so the colour is never the only cue; the
 *  row itself spends no width on them. Under reduced motion each glow is a still ring around its dot,
 *  in its own colour, so the two still read apart from the steady dots. Bordered in its own colour,
 *  as a state mark is, so forced colours still draw it. */
function StateDot({ family, word }: { family: PhaseFamily; word: string }) {
  const tone = DOT_TONE[family]
  const lively = GLOWS.has(family)
  const glow = lively && !prefersReducedMotion()
  return (
    <span data-state-family={family} data-state-dot={glow ? 'glowing' : 'still'} title={word.charAt(0).toUpperCase() + word.slice(1)}
      className="relative inline-flex size-2 shrink-0" style={{ zIndex: ROW_LABEL_Z }}>
      {glow && <span aria-hidden className="status-pulse absolute inset-0 rounded-pill" style={{ background: tone }} />}
      {lively && !glow && <span aria-hidden data-still-glow className="absolute rounded-pill border" style={{ inset: -3, borderColor: tone, opacity: 0.6 }} />}
      <span aria-hidden className="relative size-2 rounded-pill border" style={{ background: tone, borderColor: tone }} />
    </span>
  )
}
