import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { ArrowLeftRight, Check, Maximize2, Minimize2, X, type LucideIcon } from 'lucide-react'
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
// The host also expands the pane over the work and collapses it back to the split, and moves it to
// the other side of the work: by pointer (Move, then Place here) or by a keyboard move mode (the
// arrow keys choose the side, Enter places, Esc cancels), each step said in its live region. The
// side it was moved to is remembered for the surface (`pane-host-<surface>-dock`), beside its width.
// Esc on the pane, outside a field, collapses an expanded pane and closes one beside the work. None
// of these is anything but presentation: the work stays mounted under an expanded pane.
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

type Side = 'left' | 'right'

/** The side a person moved this surface's pane to, or `fallback`. */
function readDock(surface: string, fallback: Side): Side {
  try {
    const v = localStorage.getItem(`pane-host-${surface}-dock`)
    return v === 'left' || v === 'right' ? v : fallback
  } catch { return fallback }
}

/** Whether a key press is a field's own: text, a choice, an editor. */
function inAField(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || typeof el.closest !== 'function') return false
  return !!el.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"], .monaco-editor')
}

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
  /** The side of the work the pane docks on until a person moves it (then the side they chose,
   *  remembered for the surface). */
  dock?: Side
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
  // The side it docks on: the one a person moved it to for this surface, else the feature's.
  const [placed, setPlaced] = useState<Side>(() => readDock(surface, dock))
  useEffect(() => { setPlaced(readDock(surface, dock)) }, [surface, dock])
  // While a move is under way, the side it would go to, drawn there until it is placed or cancelled.
  const [preview, setPreview] = useState<Side | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [said, setSaid] = useState('')
  const side: Side = preview ?? placed
  const { width, fitWidth, onHandleDown, onHandleKey, min, max } = useResizablePanel(`pane-host-${surface}`, {
    def: size.def, min: size.min, max: size.max, side,
  })
  const titleId = useId()
  const whereId = useId()
  const paneRef = useRef<HTMLElement>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  // The header's controls, so a move can hand the focus to Place and back to Move.
  const controlsRef = useRef<HTMLDivElement>(null)
  const focusControl = (name: string) => {
    controlsRef.current?.querySelector<HTMLButtonElement>(`button[aria-label="${name}"]`)?.focus()
  }
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

  // A pane that closes, or opens other content, opens again beside the work, with no move under way.
  useEffect(() => { setExpanded(false); setPreview(null); setSaid('') }, [id])
  // The pane is drawn on the side it would go to, before or after the work as it sits there, so
  // the reading order follows what is seen; drawing it there again takes the focus with it, which
  // goes back to Place while the move lasts.
  useEffect(() => { if (preview) focusControl(`Place ${pane?.label ?? 'pane'} here`) }, [preview]) // eslint-disable-line react-hooks/exhaustive-deps

  const what = pane?.label ?? 'pane'
  const startMove = () => {
    const to: Side = placed === 'right' ? 'left' : 'right'
    setPreview(to)
    setSaid(`Move preview: the ${what} on the ${to} of the work. Arrow keys choose a side, Enter places it, Esc cancels.`)
  }
  const choose = (to: Side) => {
    setPreview(to)
    setSaid(`Move preview: the ${what} on the ${to} of the work.`)
  }
  const place = () => {
    if (!preview) return
    setPlaced(preview)
    try { localStorage.setItem(`pane-host-${surface}-dock`, preview) } catch { /* kept for this page only */ }
    setSaid(`Moved: the ${what} is on the ${preview} of the work.`)
    setPreview(null)
    requestAnimationFrame(() => focusControl(`Move ${what}`))
  }
  const cancel = () => {
    setSaid(`Move cancelled: the ${what} stays on the ${placed} of the work.`)
    setPreview(null)
    requestAnimationFrame(() => focusControl(`Move ${what}`))
  }
  const onMoveKey = (e: KeyboardEvent) => {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); choose(e.key === 'ArrowLeft' ? 'left' : 'right') }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancel() }
    // Enter places it from either control: the move mode's commit, whichever button has the focus.
    else if (e.key === 'Enter') { e.preventDefault(); place() }
  }
  const onPaneKey = (e: KeyboardEvent) => {
    if (e.key !== 'Escape' || e.defaultPrevented || preview || inAField(e.target)) return
    e.preventDefault()
    if (expanded) setExpanded(false)
    else pane?.onClose()
  }
  const layout = expanded ? 'expanded' : stacked ? 'stacked' : 'beside'
  const where = expanded ? 'Expanded over the work' : stacked ? 'Under the work' : `Docked on the ${side} of the work`

  const inner = side === 'right' ? 'left' : 'right'
  // Beside the work, the pane is drawn at the width chosen for this surface (`fitWidth` keeps it
  // inside the window), and gives way where the surface is narrower than that, so the work keeps
  // its own 12rem beside it and nothing spills past the surface. The chosen width is kept, and
  // drawn again once there is room for it. Stacked, the pane takes the surface's width and half its
  // height, under the work.
  const edge = expanded ? 'flex-1' : stacked ? 'flex-1 border-t' : side === 'right' ? 'border-l' : 'border-r'
  const paneEl = pane && (
    <section ref={paneRef} aria-labelledby={titleId} aria-describedby={whereId} onKeyDown={onPaneKey}
      className={`relative flex min-h-0 min-w-0 flex-col ${edge} border-outline-variant/40 bg-surface`}
      style={stacked || expanded ? undefined : { width: fitWidth }}>
      <div className="flex shrink-0 items-center justify-between gap-1 border-b border-outline-variant/40 bg-surface-low/40 px-2 py-1">
        <h2 id={titleId} data-type="caption" className="inline-flex min-w-0 items-center gap-1.5 text-on-surface-low" title={pane.title}>
          {pane.icon && <pane.icon size={12} className="shrink-0" />} <span className="truncate">{pane.title}</span>
        </h2>
        <span id={whereId} className="sr-only">{where}</span>
        <span role="status" className="sr-only">{said}</span>
        {/* Presentation only: where the pane sits and how big. Nothing here acts on the work. */}
        <div ref={controlsRef} className="flex shrink-0 items-center gap-0.5" onKeyDown={preview ? onMoveKey : undefined}>
          {preview ? (
            <>
              <IconButton icon={Check} label={`Place ${pane.label} here`} onClick={place} size={24} iconSize={13} />
              <IconButton icon={X} label="Cancel the move" onClick={cancel} size={24} iconSize={13} />
            </>
          ) : (
            <>
              {!stacked && !expanded && <IconButton icon={ArrowLeftRight} label={`Move ${pane.label}`} onClick={startMove} size={24} iconSize={13} />}
              <IconButton icon={expanded ? Minimize2 : Maximize2} label={`${expanded ? 'Collapse' : 'Expand'} ${pane.label}`}
                onClick={() => setExpanded((x) => !x)} size={24} iconSize={13} />
              <IconButton icon={X} label={`Close ${pane.label}`} onClick={pane.onClose} size={24} iconSize={13} />
            </>
          )}
        </div>
      </div>
      <div className="min-h-0 flex-1">{pane.body}</div>
      {/* The handle sits on the edge that faces the work, so dragging it toward the work, or the
          arrow key that points there, grows the pane. Stacked or expanded, there is no width to
          choose. */}
      {!stacked && !expanded && <div onPointerDown={onHandleDown} onKeyDown={onHandleKey} role="separator" aria-orientation="vertical"
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
  const before = side === 'left' && !stacked
  const into = stacked && !expanded ? stackInto : null
  const workHidden = expanded && !!pane
  return (
    <div ref={boxRef} data-pane-host={surface} data-pane-layout={pane ? layout : undefined}
      className={`flex min-h-0 min-w-0 flex-1 ${stacked ? 'flex-col' : ''}`}>
      {before ? paneEl : null}
      <div hidden={workHidden} className={`min-h-0 flex-1 ${stacked ? 'min-w-0' : 'min-w-48'}`}>{children}</div>
      {before || into ? null : paneEl}
      {into && paneEl ? createPortal(paneEl, into) : null}
    </div>
  )
}
