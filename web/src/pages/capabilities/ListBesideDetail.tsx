import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { ArrowLeft, X } from 'lucide-react'
import { IconButton } from '../../ui/IconButton'
import { useFocusReturn } from '../../ui/useFocusReturn'

/** The windows below the 840 px width class, where a list and its detail show one at a time
 *  (`docs/design/information-architecture.md`, "A list beside a detail"). */
export const COMPACT_WINDOW = '(max-width: 839px)'

/** The list's width while a detail sits beside it. The detail takes the rest. */
export const LIST_WIDTH = '22.5rem'

/** The selected item's detail, as the page hands it over. The page keeps the selection in its own
 *  address (`?open=`), so a reload, Back and a copied link open the same detail. */
export interface CapabilityDetail {
  /** The selection; a new id is a new detail, mounted afresh. */
  id: string
  /** What the detail is, which names its pane. */
  title: string
  icon?: ReactNode
  /** Clears the selection. Close, Escape and the compact window's Back all call it. */
  onClose: () => void
  body: ReactNode
}

/** Whether the window is below the 840 px width class. A test window matches no media query, so
 *  it is a wide one. */
function useCompactWindow(): boolean {
  const query = () => typeof window.matchMedia === 'function' && window.matchMedia(COMPACT_WINDOW).matches
  const [compact, setCompact] = useState(query)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(COMPACT_WINDOW)
    const onChange = () => setCompact(mq.matches)
    mq.addEventListener('change', onChange)
    onChange()
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return compact
}

/** A Capabilities tab (#3817): its header, its list, and the selected item's detail beside the
 *  list, where the item's permissions are.
 *
 *  With nothing selected the list takes the whole width, and no pane is open. From the 840 px width
 *  class up a selection opens the detail to the right of the list, which keeps a fixed width while
 *  the detail takes the rest. Below it the two show one at a time: the detail in place of the list,
 *  and its Back returns to the list. The list stays mounted under a detail, so its scroll, its
 *  search and the row that opened the detail are where they were when it closes, and the focus goes
 *  back to that row.
 *
 *  `controls` is the list's own bar (search, filter, sort, its More menu); it sits over the list,
 *  never over the detail. */
export function ListBesideDetail({ header, controls, detail, noun, children }: {
  /** The tab's header: its title and the one action that adds to it. */
  header: ReactNode
  controls?: ReactNode
  detail: CapabilityDetail | null
  /** What the list holds, plural ("agents"), which names the way back to it. */
  noun: string
  /** The list. */
  children: ReactNode
}) {
  const compact = useCompactWindow()
  const beside = detail !== null && !compact
  return (
    <div className="flex h-full flex-col">
      {header}
      <div className="flex min-h-0 flex-1">
        <div data-capability-list hidden={detail !== null && compact}
          className={`@container flex min-w-0 flex-col ${beside ? 'shrink-0 border-r border-outline-variant/40' : 'flex-1'}`}
          style={beside ? { width: LIST_WIDTH } : undefined}>
          {controls}
          <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </div>
        {detail && <DetailPane key={detail.id} detail={detail} compact={compact} noun={noun} />}
      </div>
    </div>
  )
}

/** The detail's pane: a region named by its title, with Close (or, below the width class, Back to
 *  the list) and the page's body under it. */
function DetailPane({ detail, compact, noun }: { detail: CapabilityDetail; compact: boolean; noun: string }) {
  const titleId = useId()
  const focusReturnRef = useFocusReturn<HTMLElement>()
  const headingRef = useRef<HTMLHeadingElement>(null)
  const close = useRef(detail.onClose)
  close.current = detail.onClose
  // Below the width class the list leaves the screen as the detail takes its place, and with it the
  // row just chosen. The focus follows the choice to the detail's heading, so it is not left on a row
  // no one can see; a detail opened by an address, with nothing chosen, takes no focus. Back returns
  // it to the row (`useFocusReturn`).
  const chosenFromList = useRef(document.activeElement instanceof HTMLElement && !!document.activeElement.closest('[data-capability-list]'))
  useEffect(() => {
    if (compact && chosenFromList.current) headingRef.current?.focus()
  }, [compact])
  // Escape closes the detail, as it closes a side panel. A control that handles its own Escape (a
  // menu, a dialog) stops it before it reaches the window.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !e.defaultPrevented) close.current() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  return (
    <section ref={focusReturnRef} role="region" aria-labelledby={titleId} className="flex min-w-0 flex-1 flex-col bg-surface">
      <div className="flex shrink-0 items-center gap-s border-b border-outline-variant/40 px-l py-m">
        {compact && <IconButton icon={ArrowLeft} label={`Back to ${noun}`} size={34} iconSize={18} onClick={detail.onClose} />}
        {detail.icon}
        <h2 ref={headingRef} id={titleId} tabIndex={-1} data-type="title-l" className="min-w-0 flex-1 truncate text-on-surface" title={detail.title}>{detail.title}</h2>
        {!compact && <IconButton icon={X} label="Close" size={34} iconSize={18} onClick={detail.onClose} />}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>{detail.body}</div>
      </div>
    </section>
  )
}
