import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { ArrowLeft, X } from 'lucide-react'
import { IconButton } from '../../ui/IconButton'
import { ScrollFade } from '../../ui/ScrollFade'
import { TOUCH_TARGET } from '../../ui/Toggle'
import { useFocusReturn } from '../../ui/useFocusReturn'

/** The room, in px, from which a list and its detail sit side by side; below it they show one at a
 *  time (`docs/design/information-architecture.md`, "A list beside a detail"). It is the tab's own
 *  width, beside the sidebar, not the window's: an expanded sidebar at an 840 px window leaves the
 *  tab about 630 px, where a detail beside the list would get under 270. */
export const BESIDE_FROM = 840

/** The list's width while a detail sits beside it. The detail takes the rest. */
export const LIST_WIDTH = '22.5rem'

/** The selected item's detail, as the page hands it over. The page keeps the selection in its own
 *  address (`?open=`), so a reload, Back and a copied link open the same detail. */
export interface CapabilityDetail {
  /** The selection; a new id is a new detail, mounted afresh. */
  id: string
  /** What the detail is, as plain text: the pane's name and its heading's tooltip. */
  title: string
  /** The heading as drawn, where the title carries markdown (a `MarkdownLine` of it); `title` by
   *  default. */
  heading?: ReactNode
  icon?: ReactNode
  /** Clears the selection. Close, Escape and the narrow layout's Back all call it. */
  onClose: () => void
  body: ReactNode
  /** A line above the heading in the pane's fixed header (Needs you's tag row). */
  eyebrow?: ReactNode
  /** The detail follows a selection moved in the list (↑↓ with the pane open), so it takes no
   *  focus, whatever `focusOnOpen` says: the focus stays in the list. */
  followsSelection?: boolean
}

/** The opt-ins a page passes for its own pane (Needs you); a Capabilities tab passes none, so its
 *  list keeps `LIST_WIDTH` and its pane keeps its heading in the header. */
export interface PaneOptions {
  /** The list's width in px while a detail sits beside it, kept while the detail still gets
   *  `splitWidth`. */
  listWidth?: number
  /** The detail's floor in px, and the list's width on a tab too narrow for `listWidth` beside it
   *  (the Library's split). */
  splitWidth?: number
  /** The heading is the first thing in the pane's scroller, wrapping in full (no `truncate`, no
   *  native title), rather than a line of the fixed header. */
  headingScrolls?: boolean
  /** Back and Close are 44 px targets below 1024 px (`TOUCH_TARGET`), each with its tip. */
  compactTargets?: boolean
  /** An open takes the focus to the heading at every width (`preventScroll`), not only where the
   *  detail replaces the list; a detail that `followsSelection` does not. */
  focusOnOpen?: boolean
  /** Both scrollers fade at a cut edge (`ScrollFade`): the list from the canvas, the pane from
   *  `surface`. */
  edgeFade?: boolean
  /** The pane's scroller is a size container, so a body's decide bar (`data-decide-bar`) sticks to
   *  its foot only while the pane is 32rem tall or more; with `edgeFade`, the bottom fade lies on
   *  the bar's top edge while it is stuck. */
  decideBar?: boolean
}

/** The list's width beside a detail on a tab *room* px wide, with *options*: `listWidth` while
 *  the detail still gets `splitWidth`, else `splitWidth`; `LIST_WIDTH` with neither. */
export function besideWidth(room: number, options: PaneOptions): string {
  const { listWidth, splitWidth } = options
  if (listWidth === undefined && splitWidth === undefined) return LIST_WIDTH
  const floor = splitWidth ?? 0
  if (listWidth !== undefined && room - listWidth >= floor) return `${listWidth}px`
  return `${splitWidth ?? listWidth}px`
}

/** Whether `box` is narrower than `BESIDE_FROM`. It is read before the first paint and again
 *  whenever the box changes size: the window resizing, or the sidebar beside it collapsing or
 *  expanding. A box with no width (not laid out yet, or hidden while a page loads) has no room to
 *  read, so it keeps the last reading, which starts wide. */
function useNarrowerThanBeside(box: RefObject<HTMLElement | null>): [boolean, number] {
  const [narrow, setNarrow] = useState(false)
  const [room, setRoom] = useState(0)
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const read = () => {
      const width = el.getBoundingClientRect().width
      if (width > 0) { setNarrow(width < BESIDE_FROM); setRoom(width) }
    }
    read()
    if (typeof ResizeObserver !== 'function') return
    const watch = new ResizeObserver(read)
    watch.observe(el)
    return () => watch.disconnect()
  }, [box])
  return [narrow, room]
}

/** A Capabilities tab (#3817): its header, its list, and the selected item's detail beside the
 *  list, where the item's permissions are.
 *
 *  With nothing selected the list takes the whole width, and no pane is open. With `BESIDE_FROM` of
 *  room or more a selection opens the detail to the right of the list, which keeps a fixed width
 *  while the detail takes the rest. With less the two show one at a time: the detail in place of the
 *  list, and its Back returns to the list. The room is the tab's own width, so a collapsed and an
 *  expanded sidebar at one window width each get the layout the room they leave allows. The list
 *  stays mounted under a detail, so its scroll, its search and the row that opened the detail are
 *  where they were when it closes, and the focus goes back to that row.
 *
 *  The list's controls (its search, filter and secondary actions) are in the header with the one
 *  that adds, so no bar sits over the list. */
export function ListBesideDetail({ header, detail, noun, children, ...options }: {
  /** The tab's header: its title and every control of the tab, the one that adds last. */
  header: ReactNode
  detail: CapabilityDetail | null
  /** What the list holds, plural ("agents"), which names the way back to it. */
  noun: string
  /** The list. */
  children: ReactNode
} & PaneOptions) {
  const box = useRef<HTMLDivElement>(null)
  const [compact, room] = useNarrowerThanBeside(box)
  const beside = detail !== null && !compact
  return (
    <div ref={box} data-capability-layout className="flex h-full flex-col">
      {header}
      <div className="flex min-h-0 flex-1">
        <div data-capability-list hidden={detail !== null && compact}
          className={`@container flex min-w-0 flex-col ${beside ? 'shrink-0 border-r border-outline-variant/40' : 'flex-1'}`}
          style={beside ? { width: besideWidth(room, options) } : undefined}>
          {options.edgeFade
            ? <ScrollFade ground="canvas">{children}</ScrollFade>
            : <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>}
        </div>
        {detail && <DetailPane key={detail.id} detail={detail} compact={compact} noun={noun} options={options} />}
      </div>
    </div>
  )
}

/** The detail's pane: a region named by its title, with Close (or, with less room than
 *  `BESIDE_FROM`, Back to the list) and the page's body under it. */
function DetailPane({ detail, compact, noun, options }: { detail: CapabilityDetail; compact: boolean; noun: string; options: PaneOptions }) {
  const titleId = useId()
  const focusReturnRef = useFocusReturn<HTMLElement>()
  const headingRef = useRef<HTMLHeadingElement>(null)
  const close = useRef(detail.onClose)
  close.current = detail.onClose
  // With less room the list leaves the screen as the detail takes its place, and with it the
  // row just chosen. The focus follows the choice to the detail's heading, so it is not left on a row
  // no one can see; a detail opened by an address, with nothing chosen, takes no focus. Back returns
  // it to the row (`useFocusReturn`).
  const chosenFromList = useRef(document.activeElement instanceof HTMLElement && !!document.activeElement.closest('[data-capability-list]'))
  // A page that opts in takes the focus to the heading on every open, at every width: its list's
  // rows hold their own controls, so Tab from the list would cross every later row first. A
  // detail that follows the list's selection (↑↓) leaves the focus in the list.
  const focusOnOpen = options.focusOnOpen === true && detail.followsSelection !== true
  useEffect(() => {
    if (focusOnOpen) headingRef.current?.focus({ preventScroll: true })
    else if (compact && chosenFromList.current) headingRef.current?.focus()
  }, [compact, focusOnOpen])
  // Escape closes the detail, as it closes a side panel. A control that handles its own Escape (a
  // menu, a dialog) stops it before it reaches the window.
  // In a field, the first Escape only leaves it, and one that ends a composition (an IME) does
  // neither: a reply half typed in the pane is never closed away.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented || e.isComposing) return
      const at = e.target
      if (at instanceof HTMLElement && (at.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(at.tagName))) {
        at.blur()
        return
      }
      close.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  const target = options.compactTargets ? { className: TOUCH_TARGET } : {}
  const back = `Back to ${noun}`
  const scrolls = options.headingScrolls === true
  const heading = scrolls
    ? <h2 ref={headingRef} id={titleId} tabIndex={-1} data-type="title-l" className="min-w-0 break-words text-on-surface">{detail.heading ?? detail.title}</h2>
    : <h2 ref={headingRef} id={titleId} tabIndex={-1} data-type="title-l" className="min-w-0 flex-1 truncate text-on-surface" title={detail.title}>{detail.heading ?? detail.title}</h2>
  const body = scrolls
    ? <div className="flex flex-col gap-l px-l py-l">{heading}{detail.body}</div>
    : <div className="mx-auto px-l py-l">{detail.body}</div>
  return (
    <section ref={focusReturnRef} role="region" aria-labelledby={titleId} className="flex min-w-0 flex-1 flex-col bg-surface">
      <div className="flex shrink-0 items-center gap-s border-b border-outline-variant/40 px-l py-m">
        {compact && <IconButton icon={ArrowLeft} label={back} tip={options.compactTargets ? back : undefined} size={34} iconSize={18} onClick={detail.onClose} {...target} />}
        {detail.icon}
        {scrolls ? <div className="min-w-0 flex-1">{detail.eyebrow}</div> : heading}
        {!compact && <IconButton icon={X} label="Close" tip={options.compactTargets ? 'Close' : undefined} size={34} iconSize={18} onClick={detail.onClose} {...target} />}
      </div>
      {options.edgeFade
        ? <ScrollFade ground="surface" style={options.decideBar ? { containerType: 'size' } : undefined}>{body}</ScrollFade>
        : <div className="min-h-0 flex-1 overflow-y-auto" style={options.decideBar ? { containerType: 'size' } : undefined}>{body}</div>}
    </section>
  )
}
