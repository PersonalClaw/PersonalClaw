import { useCallback, useEffect, useRef, useState } from 'react'

/** A persisted, drag- AND keyboard-resizable panel SIZE — the app's one window-splitter
 *  primitive (WAI-ARIA `separator`). Lives in `ui/` because it is shared across areas:
 *  the Code cockpit's sidebars + terminal, and `ui/SidePanel`'s docked inspector. Size
 *  persists to localStorage at `${key}-w`. `side` says which edge the drag handle sits on
 *  so the delta is applied with the correct sign — and whether the axis is horizontal
 *  (left/right → width, clientX) or vertical (top/bottom → height, clientY). The returned
 *  `width` is the panel's size along its axis (px), named generically for both axes.
 *
 *  Collapse is OPT-IN (`collapsible: true`). It is a distinct concept from size — most
 *  resizable panels (every `SidePanel` docking, the composer, the chat file panel) are
 *  never "collapsed", they are opened and closed by a parent — and bundling it meant a
 *  width-only consumer wrote a dead `${key}-collapsed` key it never read. Off by default,
 *  the primitive serves a width-only panel cleanly; the Code cockpit opts in.
 *
 *  Attach `onHandleDown`/`onHandleKey` to a handle that carries `role="separator"`,
 *  `aria-orientation`, `tabIndex={0}`, an arrow-key-hinting `aria-label`, and
 *  `aria-valuenow`/`min`/`max` — the contract `ui/splitterContract.test.tsx` enforces.
 */
export function useResizablePanel(
  key: string,
  opts: { def: number; min: number; max: number; side: 'left' | 'right' | 'top' | 'bottom'; collapsible?: boolean },
) {
  const { def, min, max, side, collapsible = false } = opts
  const vertical = side === 'top' || side === 'bottom'
  const [width, setWidth] = useState<number>(() => {
    const v = Number(localStorage.getItem(`${key}-w`))
    return v >= min && v <= max ? v : def
  })
  const [collapsed, setCollapsed] = useState<boolean>(() => collapsible && localStorage.getItem(`${key}-collapsed`) === '1')

  // Persist the width, but DEBOUNCED: a pointer drag fires setWidth on every
  // pointermove (60+/sec), and an un-debounced effect did a synchronous localStorage
  // write per frame — a hot-path jank source for a value only the final resting size
  // of which matters. Coalesce rapid changes into one write ~200ms after the drag
  // settles. (collapsed is a discrete toggle → write immediately.)
  const widthRef = useRef(width); widthRef.current = width
  useEffect(() => {
    const t = setTimeout(() => localStorage.setItem(`${key}-w`, String(width)), 200)
    return () => clearTimeout(t)
  }, [key, width])
  // Flush the latest width on UNMOUNT so a resize-then-immediately-close (within the
  // 200ms debounce window) doesn't lose the final size. Unmount-only (empty dep) so it
  // doesn't reintroduce the per-frame write; reads the live width via a ref.
  useEffect(() => () => { localStorage.setItem(`${key}-w`, String(widthRef.current)) }, [key])
  // Only a collapsible panel persists (or writes) its collapsed flag; a width-only
  // consumer must not litter localStorage with a `${key}-collapsed` key nothing reads.
  useEffect(() => {
    if (!collapsible) return
    localStorage.setItem(`${key}-collapsed`, collapsed ? '1' : '0')
  }, [key, collapsed, collapsible])

  // Pointer-drag the handle. For a LEFT panel the handle is on its right edge, so a
  // rightward drag grows it; for a RIGHT panel the handle is on its left edge, so a
  // leftward drag grows it (delta sign flips).
  //
  // Uses POINTER CAPTURE on the handle element rather than window listeners: the
  // cockpit's center hosts a Monaco editor + sandboxed artifact iframes, which
  // SWALLOW window-level pointermove/up if the cursor crosses them mid-drag — the
  // drag would then stick (panel keeps resizing with no button held, userSelect
  // frozen app-wide). Capturing routes every move/up/cancel back to the handle
  // regardless of what's underneath, and `pointercancel` (OS gesture / touch
  // interruption — never handled before) cleanly tears down so userSelect is always
  // restored.
  const onHandleDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    const handle = e.currentTarget as HTMLElement
    const start = vertical ? e.clientY : e.clientX
    const startW = width
    try { handle.setPointerCapture(e.pointerId) } catch { /* unsupported → falls back to bubbling */ }
    const move = (ev: PointerEvent) => {
      // The handle sits on the panel's INNER edge; dragging away from that edge grows
      // the panel. left/top: delta = pos − start; right/bottom: delta = start − pos.
      const pos = vertical ? ev.clientY : ev.clientX
      const delta = side === 'left' || side === 'top' ? pos - start : start - pos
      setWidth(Math.max(min, Math.min(max, startW + delta)))
    }
    const up = () => {
      handle.removeEventListener('pointermove', move)
      handle.removeEventListener('pointerup', up)
      handle.removeEventListener('pointercancel', up)
      document.body.style.userSelect = ''
    }
    document.body.style.userSelect = 'none'
    handle.addEventListener('pointermove', move)
    handle.addEventListener('pointerup', up)
    handle.addEventListener('pointercancel', up)
  }, [width, min, max, side])

  // Keyboard resize (WAI-ARIA window-splitter pattern): arrows step the inner edge,
  // Home/End jump to min/max. Left/Right map to the visual direction the panel grows
  // (a left panel grows rightward, a right panel grows leftward), so the key always
  // matches what the user sees. Up/Down are accepted as orientation-agnostic ±steps.
  const onHandleKey = useCallback((e: React.KeyboardEvent) => {
    const STEP = e.shiftKey ? 48 : 16
    // The arrow that GROWS the panel points away from its inner-edge handle: a left
    // panel grows right, a right panel grows left, a bottom panel grows up, a top
    // panel grows down. The cross-axis arrows act as orientation-agnostic ±step.
    const grow = side === 'left' ? 'ArrowRight' : side === 'right' ? 'ArrowLeft' : side === 'bottom' ? 'ArrowUp' : 'ArrowDown'
    const shrink = side === 'left' ? 'ArrowLeft' : side === 'right' ? 'ArrowRight' : side === 'bottom' ? 'ArrowDown' : 'ArrowUp'
    const altGrow = vertical ? 'ArrowRight' : 'ArrowUp'
    const altShrink = vertical ? 'ArrowLeft' : 'ArrowDown'
    let next: number | null = null
    if (e.key === grow || e.key === altGrow) next = width + STEP
    else if (e.key === shrink || e.key === altShrink) next = width - STEP
    else if (e.key === 'Home') next = min
    else if (e.key === 'End') next = max
    if (next == null) return
    e.preventDefault()
    setWidth(Math.max(min, Math.min(max, next)))
  }, [width, min, max, side])

  return { width, collapsed, setCollapsed, onHandleDown, onHandleKey, min, max }
}
