import { useId, useLayoutEffect, useRef, type ReactNode } from 'react'
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

export function PaneHost({ surface, dock = 'right', size = DEFAULT_SIZE, pane, children }: {
  /** The surface the pane belongs to. Its size is remembered for this surface alone. */
  surface: string
  /** The side of the work the pane docks on. */
  dock?: 'left' | 'right'
  /** The pane's default, smallest and largest width, in px. */
  size?: PaneSize
  /** What the pane shows, or null while it is closed. */
  pane: PaneContent | null
  /** The surface's work, beside the pane. */
  children: ReactNode
}) {
  const { width, fitWidth, onHandleDown, onHandleKey, min, max } = useResizablePanel(`pane-host-${surface}`, {
    def: size.def, min: size.min, max: size.max, side: dock,
  })
  const titleId = useId()
  const paneRef = useRef<HTMLElement>(null)

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
  // The pane is drawn at the width chosen for this surface (`fitWidth` keeps it inside the window),
  // and gives way where the surface is narrower than that, so the work keeps its own 12rem beside
  // it and nothing spills past the surface. The chosen width is kept, and drawn again once there is
  // room for it.
  const paneEl = pane && (
    <section ref={paneRef} aria-labelledby={titleId}
      className={`relative flex min-h-0 min-w-0 flex-col ${dock === 'right' ? 'border-l' : 'border-r'} border-outline-variant/40 bg-surface`}
      style={{ width: fitWidth }}>
      <div className="flex shrink-0 items-center justify-between gap-1 border-b border-outline-variant/40 bg-surface-low/40 px-2 py-1">
        <h2 id={titleId} data-type="caption" className="inline-flex min-w-0 items-center gap-1.5 text-on-surface-low" title={pane.title}>
          {pane.icon && <pane.icon size={12} className="shrink-0" />} <span className="truncate">{pane.title}</span>
        </h2>
        <IconButton icon={X} label={`Close ${pane.label}`} onClick={pane.onClose} size={24} iconSize={13} className="shrink-0" />
      </div>
      <div className="min-h-0 flex-1">{pane.body}</div>
      {/* The handle sits on the edge that faces the work, so dragging it toward the work, or the
          arrow key that points there, grows the pane. */}
      <div onPointerDown={onHandleDown} onKeyDown={onHandleKey} role="separator" aria-orientation="vertical"
        tabIndex={0} aria-label={`Resize ${pane.label} — arrow keys to resize`}
        aria-valuenow={Math.round(width)} aria-valuemin={min} aria-valuemax={max}
        className="group/handle absolute top-0 bottom-0 z-20 w-2 cursor-col-resize outline-none focus-visible:bg-primary/30"
        style={{ [inner]: 0 } as React.CSSProperties}>
        <div className="mx-auto h-full w-0.5 bg-transparent transition-colors group-hover/handle:bg-primary/60 group-focus-visible/handle:bg-primary" />
      </div>
    </section>
  )
  // The work keeps one place in the tree whichever side the pane docks on, and whether or not a
  // pane is open, so opening or closing a pane never remounts it.
  return (
    <div className="flex min-h-0 min-w-0 flex-1">
      {dock === 'left' ? paneEl : null}
      <div className="min-h-0 min-w-48 flex-1">{children}</div>
      {dock === 'right' ? paneEl : null}
    </div>
  )
}
