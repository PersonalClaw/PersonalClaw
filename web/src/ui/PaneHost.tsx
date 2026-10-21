import { useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { X, type LucideIcon } from 'lucide-react'
import { IconButton } from './IconButton'
import { useResizablePanel } from './useResizablePanel'

// ── The pane host ──────────────────────────────────────────────────────────────────────────────
//
// The one host for the detail a surface opens beside its work: a review, a workflow's detail, an
// output, a background task. It docks the pane on one side of the work, resizes it by pointer and
// by key, remembers its size for that surface, and gives focus back to the control the pane was
// opened from when it closes.
//
// The host owns how the pane is presented and nothing else. What the pane shows, and whether it is
// open, are the feature's: a feature that keeps its open content in its address reads the address
// and hands the host what it names, so a reload, Back or a copied link opens the same pane under
// that feature's own address rules. The host writes no address. Its size is the only thing it
// keeps (`pane-host-<surface>-w`), and none of its controls touches the work: the work beside the
// pane stays mounted while the pane opens, changes and closes, and a consequential action stays in
// the content, never on the host's chrome.
//
// A compact surface stacks the pane under the work. Beside the work the pane takes at least its
// smallest width and the work keeps its own 12rem, so a surface narrower than the two together
// cannot hold them side by side without one giving way: there the pane sits under the work, each
// at the surface's full width, with no width handle. The room is the host's own box, not the
// window's, so a sidebar docked beside it takes its share first. A work that keeps something at its
// foot (the chat's composer) gives the host a slot (`stackInto`) above that foot, and a stacked pane
// goes there instead, so the foot stays last and the work's content and the pane share the rest.

/** What a pane shows. The feature builds it; the host only frames it. */
export type PaneContent = {
  /** Which content this is (a path, an id). A new one is a new place focus came from. */
  id: string
  /** The pane's title, which names its region. */
  title: string
  /** What the pane is, for its controls' names: `diff` gives "Close diff" and "Resize diff". */
  label: string
  icon?: LucideIcon
  /** The feature's close: the host calls it from its Close button. */
  onClose: () => void
  body: ReactNode
}

export type PaneSize = { def: number; min: number; max: number }

const DEFAULT_SIZE: PaneSize = { def: 480, min: 320, max: 960 }

/** The width the work keeps beside a pane (`min-w-48`, 12rem). */
const WORK_MIN = 192

/** Whether `box` is narrower than `room`. Read before the first paint and again whenever the box
 *  changes size. A box with no width (not laid out yet, or hidden while a page loads) has no room
 *  to read, so it keeps the last reading, which starts beside. */
function useNarrowerThan(box: RefObject<HTMLElement | null>, room: number): boolean {
  const [narrow, setNarrow] = useState(false)
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const read = () => {
      const width = el.getBoundingClientRect().width
      if (width > 0) setNarrow(width < room)
    }
    read()
    if (typeof ResizeObserver !== 'function') return
    const watch = new ResizeObserver(read)
    watch.observe(el)
    return () => watch.disconnect()
  }, [box, room])
  return narrow
}

export function PaneHost({ surface, dock = 'right', size = DEFAULT_SIZE, pane, stackInto = null, children }: {
  /** The surface the pane belongs to. Its size is remembered for this surface alone. */
  surface: string
  /** The side of the work the pane docks on. */
  dock?: 'left' | 'right'
  /** The pane's default, smallest and largest width, in px. */
  size?: PaneSize
  /** What the pane shows, or null while it is closed. */
  pane: PaneContent | null
  /** Where a stacked pane goes: an element inside the work, before what the work keeps at its foot
   *  (best a `display: contents` slot in the work's own column). Without one it goes under the work. */
  stackInto?: HTMLElement | null
  /** The surface's work, beside the pane. */
  children: ReactNode
}) {
  const { width, fitWidth, onHandleDown, onHandleKey, min, max } = useResizablePanel(`pane-host-${surface}`, {
    def: size.def, min: size.min, max: size.max, side: dock,
  })
  const titleId = useId()
  const paneRef = useRef<HTMLElement>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  const stacked = useNarrowerThan(boxRef, size.min + WORK_MIN)

  // ── Focus goes back where it came from ──
  //
  // The control a pane was opened from is read while the render that opens it (or changes what it
  // shows) runs, before any autofocus inside the pane can move focus, as `useFocusReturn` does.
  // Focus already inside the pane keeps the control it came from, and a pane opened with nothing
  // focused, as an address opens it, has none. When the pane closes and took focus with it (focus
  // fell to the page), focus goes back to that control if it is still there. Focus the person has
  // put somewhere else in the meantime stays where it is.
  const id = pane?.id ?? null
  const seen = useRef<string | null>(null)
  const opener = useRef<HTMLElement | null>(null)
  if (id !== seen.current) {
    if (id !== null) {
      const active = document.activeElement as HTMLElement | null
      const inPane = !!active && !!paneRef.current?.contains(active)
      if (active && active !== document.body && !inPane) opener.current = active
      else if (seen.current === null) opener.current = null
    }
    seen.current = id
  }
  const wasOpen = useRef(false)
  useLayoutEffect(() => {
    const open = id !== null
    const closed = wasOpen.current && !open
    wasOpen.current = open
    if (!closed) return
    const to = opener.current
    opener.current = null
    const active = document.activeElement
    if ((!active || active === document.body) && to?.isConnected) to.focus()
  }, [id])

  const inner = dock === 'right' ? 'left' : 'right'
  // Beside the work, the pane is drawn at the width chosen for this surface (`fitWidth` keeps it
  // inside the window), and gives way where the surface is narrower than that, so the work keeps
  // its own 12rem beside it and nothing spills past the surface. The chosen width is kept, and
  // drawn again once there is room for it. Stacked, the pane takes the surface's width and half its
  // height, under the work.
  const edge = stacked ? 'flex-1 border-t' : dock === 'right' ? 'border-l' : 'border-r'
  const paneEl = pane && (
    <section ref={paneRef} aria-labelledby={titleId}
      className={`relative flex min-h-0 min-w-0 flex-col ${edge} border-outline-variant/40 bg-surface`}
      style={stacked ? undefined : { width: fitWidth }}>
      <div className="flex shrink-0 items-center justify-between gap-1 border-b border-outline-variant/40 bg-surface-low/40 px-2 py-1">
        <h2 id={titleId} data-type="caption" className="inline-flex min-w-0 items-center gap-1.5 text-on-surface-low" title={pane.title}>
          {pane.icon && <pane.icon size={12} className="shrink-0" />} <span className="truncate">{pane.title}</span>
        </h2>
        <IconButton icon={X} label={`Close ${pane.label}`} onClick={pane.onClose} size={24} iconSize={13} className="shrink-0" />
      </div>
      <div className="min-h-0 flex-1">{pane.body}</div>
      {/* The handle sits on the edge that faces the work, so dragging it toward the work, or the
          arrow key that points there, grows the pane. Stacked, there is no width to choose. */}
      {!stacked && <div onPointerDown={onHandleDown} onKeyDown={onHandleKey} role="separator" aria-orientation="vertical"
        tabIndex={0} aria-label={`Resize ${pane.label} — arrow keys to resize`}
        aria-valuenow={Math.round(width)} aria-valuemin={min} aria-valuemax={max}
        className="group/handle absolute top-0 bottom-0 z-20 w-2 cursor-col-resize outline-none focus-visible:bg-primary/30"
        style={{ [inner]: 0 } as React.CSSProperties}>
        <div className="mx-auto h-full w-0.5 bg-transparent transition-colors group-hover/handle:bg-primary/60 group-focus-visible/handle:bg-primary" />
      </div>}
    </section>
  )
  // The work keeps one place in the tree whichever side the pane docks on, whether or not a pane is
  // open and whether the two sit beside each other or stack, so none of those remounts it. Stacked,
  // the pane goes under the work from either side. `data-pane-host` names the box whose room is
  // read, and `data-pane-layout` says which layout is drawn while a pane is open.
  const before = dock === 'left' && !stacked
  const into = stacked ? stackInto : null
  return (
    <div ref={boxRef} data-pane-host={surface} data-pane-layout={pane ? (stacked ? 'stacked' : 'beside') : undefined}
      className={`flex min-h-0 min-w-0 flex-1 ${stacked ? 'flex-col' : ''}`}>
      {before ? paneEl : null}
      <div className={`min-h-0 flex-1 ${stacked ? 'min-w-0' : 'min-w-48'}`}>{children}</div>
      {before || into ? null : paneEl}
      {into && paneEl ? createPortal(paneEl, into) : null}
    </div>
  )
}
