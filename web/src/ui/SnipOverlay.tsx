import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion, useReducedMotion } from 'framer-motion'
import { Crop } from 'lucide-react'
import { Button } from './Button'
import { useFocusTrap } from './useFocusTrap'
import { spring, physics, expr } from '../design/motion'
import { cropViewStyle, type SnipRect } from './composer/displayCapture'

/** How far one arrow-key press moves or resizes the selection, in SOURCE pixels.
 *  Coarse enough to cross a 4K frame in a reasonable number of presses, fine enough
 *  to land on a paragraph; Shift multiplies it for fast travel. */
const STEP = 24
const FAST = 8
/** Smallest selection a crop may produce — below this the PNG is not a snip of
 *  anything, and OCR has nothing to read. */
const MIN_SIDE = 16

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

/** Keep *rect* inside a `width`×`height` frame without changing its size, so a nudge
 *  at the edge stops rather than silently shrinking the crop. */
function contain(rect: SnipRect, width: number, height: number): SnipRect {
  const w = clamp(rect.width, MIN_SIDE, width)
  const h = clamp(rect.height, MIN_SIDE, height)
  return { x: clamp(rect.x, 0, width - w), y: clamp(rect.y, 0, height - h), width: w, height: h }
}

/** The rect two corner points describe, normalised so dragging in any direction works. */
function fromCorners(ax: number, ay: number, bx: number, by: number): SnipRect {
  return {
    x: Math.min(ax, bx),
    y: Math.min(ay, by),
    width: Math.abs(bx - ax),
    height: Math.abs(by - ay),
  }
}

/**
 * Crop overlay for a captured screen frame (CHAT-CRAFT S4a).
 *
 * The frame is already frozen and the capture already stopped by the time this mounts
 * — it is a still image, not a live preview, which is why cropping can take as long as
 * the user likes without anything watching their screen.
 *
 * **Keyboard-first, not mouse-with-keyboard-bolted-on.** The selection starts as the
 * WHOLE frame, so Enter is immediately a complete action and a keyboard user never has
 * to author a rectangle from nothing: arrows move it, Shift+arrows resize it, Enter
 * attaches, Escape cancels and leaves no attachment behind. Dragging does the same
 * thing with a pointer. The dimmed-outside look is a second copy of the image clipped
 * to the selection rather than a bespoke mask, so the selected pixels are literally
 * the ones that get cropped.
 */
export function SnipOverlay({ frame, width, height, onCancel, onConfirm }: {
  /** The captured frame as a data URL — a still, already-stopped capture. */
  frame: string
  /** Natural width of the frame in source pixels. */
  width: number
  /** Natural height of the frame in source pixels. */
  height: number
  /** Dismissed without attaching anything (Escape, Cancel, scrim). */
  onCancel: () => void
  /** Confirmed — the crop rectangle in SOURCE pixels. */
  onConfirm: (rect: SnipRect) => void
}) {
  const trapRef = useFocusTrap<HTMLDivElement>()
  const reduce = useReducedMotion()
  const stageRef = useRef<HTMLDivElement | null>(null)
  const dragRef = useRef<{ x: number; y: number } | null>(null)
  const [rect, setRect] = useState<SnipRect>(() => contain({ x: 0, y: 0, width, height }, width, height))

  // Escape is bound on the dialog root (focus is trapped inside it, so it always
  // lands) and stops propagating — otherwise one press would also close whatever
  // document-level layer sits behind this one.
  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      e.preventDefault()
      onCancel()
      return
    }
    if (e.key === 'Enter' && (e.target as HTMLElement)?.dataset?.snipStage === 'true') {
      e.preventDefault()
      onConfirm(rect)
      return
    }
    const step = e.shiftKey ? STEP * FAST : STEP
    let next: SnipRect | null = null
    // Alt+arrows resize from the bottom-right; plain arrows move the whole selection.
    // (Alt, not Shift: Shift is the fast-travel modifier for both gestures.)
    if (e.key === 'ArrowLeft') next = e.altKey ? { ...rect, width: rect.width - step } : { ...rect, x: rect.x - step }
    else if (e.key === 'ArrowRight') next = e.altKey ? { ...rect, width: rect.width + step } : { ...rect, x: rect.x + step }
    else if (e.key === 'ArrowUp') next = e.altKey ? { ...rect, height: rect.height - step } : { ...rect, y: rect.y - step }
    else if (e.key === 'ArrowDown') next = e.altKey ? { ...rect, height: rect.height + step } : { ...rect, y: rect.y + step }
    if (!next) return
    e.preventDefault()
    e.stopPropagation()
    setRect(contain(next, width, height))
  }, [height, onCancel, onConfirm, rect, width])

  // A pointer drag anywhere on the frame authors a new rectangle. The listeners live on
  // the window (not the image) so a drag that leaves the image still tracks — and still
  // ENDS — instead of freezing mid-selection with the button already released.
  useEffect(() => {
    const toSource = (e: PointerEvent): { x: number; y: number } => {
      const box = stageRef.current?.getBoundingClientRect()
      if (!box || !box.width || !box.height) return { x: 0, y: 0 }
      return {
        x: clamp(((e.clientX - box.left) / box.width) * width, 0, width),
        y: clamp(((e.clientY - box.top) / box.height) * height, 0, height),
      }
    }
    const move = (e: PointerEvent) => {
      const anchor = dragRef.current
      if (!anchor) return
      const p = toSource(e)
      setRect(contain(fromCorners(anchor.x, anchor.y, p.x, p.y), width, height))
    }
    const up = () => { dragRef.current = null }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [height, width])

  const beginDrag = (e: React.PointerEvent) => {
    const box = stageRef.current?.getBoundingClientRect()
    if (!box || !box.width || !box.height) return
    const x = clamp(((e.clientX - box.left) / box.width) * width, 0, width)
    const y = clamp(((e.clientY - box.top) / box.height) * height, 0, height)
    dragRef.current = { x, y }
    setRect(contain({ x, y, width: MIN_SIDE, height: MIN_SIDE }, width, height))
  }

  // Percentages, so the selection tracks the image at any rendered size — the frame is
  // laid out to fit the viewport, not at its native pixel size.
  const view = cropViewStyle(rect, width, height)
  const enterScale = reduce ? 1 : 1 - expr(0.04, 0.5)
  const enterY = reduce ? 0 : expr(10, 0.4)

  return createPortal(
    <motion.div className="fixed inset-0 z-[70] flex items-center justify-center p-2xl"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects}>
      <motion.div className="absolute inset-0 bg-canvas/80 backdrop-blur-sm" onClick={onCancel}
        initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={spring.effects} />
      <motion.div ref={trapRef} role="dialog" aria-modal="true" aria-label="Crop the captured screen"
        onKeyDown={onKeyDown}
        className="squircle relative flex max-h-full w-full flex-col overflow-hidden bg-surface shadow-sheet"
        style={{ maxWidth: 'calc(var(--content-width) + 320px)' }}
        initial={{ opacity: 0, scale: enterScale, y: enterY }} animate={{ opacity: 1, scale: 1, y: 0 }} exit={{ opacity: 0, scale: 0.98, y: 6 }}
        transition={reduce ? spring.effects : physics.playful}>
        <div className="flex shrink-0 items-center gap-s border-b border-outline-variant/40 px-l py-m">
          <Crop size={16} className="shrink-0 text-on-surface-var" />
          <span data-type="title-l" className="truncate text-on-surface">Crop the capture</span>
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-l py-l">
          {/* The selectable frame. `tabIndex` + the role/label make it a real keyboard
              target: without a focusable stage the arrow keys would have nowhere to
              land and the overlay would be mouse-only. */}
          <div
            ref={stageRef}
            data-snip-stage="true"
            role="group"
            tabIndex={0}
            aria-label="Captured screen. Arrow keys move the selection, Alt with arrow keys resizes it, Enter attaches, Escape cancels."
            onPointerDown={beginDrag}
            className="relative mx-auto block cursor-crosshair select-none overflow-hidden rounded-md outline-none ring-outline-variant/60 focus-visible:ring-2 focus-visible:ring-primary"
            // Width, not height, is what the aspect ratio can be driven from reliably —
            // so cap the frame by deriving its width from a viewport height budget. A
            // 4K frame otherwise fills the sheet and pushes the size readout and the
            // Attach button out of view, which is where a crop UI stops being one.
            style={{
              aspectRatio: `${Math.max(1, width)} / ${Math.max(1, height)}`,
              width: `min(100%, calc(58vh * ${Math.max(1, width) / Math.max(1, height)}))`,
            }}
          >
            {/* Base layer, dimmed: everything OUTSIDE the selection. */}
            <img src={frame} alt="" draggable={false} className="pointer-events-none block h-full w-full opacity-40" />
            {/* Selected region: the same image, clipped — so what looks selected and
                what gets cropped cannot drift apart. */}
            <div className="pointer-events-none absolute overflow-hidden ring-2 ring-primary" style={view.selection}>
              <img src={frame} alt="" draggable={false} className="block" style={view.image} />
            </div>
          </div>
          <p className="mt-m text-[0.75rem] text-on-surface-low">
            Drag on the capture to select a region, or use the arrow keys (hold Alt to resize).
            {' '}Selection: {Math.round(rect.width)}×{Math.round(rect.height)} px.
          </p>
        </div>

        <div className="flex shrink-0 items-center justify-end gap-s border-t border-outline-variant/40 px-l py-m">
          <Button variant="secondary" size="sm" onClick={onCancel}>Cancel</Button>
          <Button variant="primary" size="sm" onClick={() => onConfirm(rect)}>Attach selection</Button>
        </div>
      </motion.div>
    </motion.div>,
    document.body,
  )
}
