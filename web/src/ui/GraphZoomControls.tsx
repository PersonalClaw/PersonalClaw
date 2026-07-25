import { Plus, Minus, Maximize2 } from 'lucide-react'

/** Pan/zoom overlay controls shared by the two SVG graph canvases (MemoryGraph,
 *  KnowledgeGraph): a glass-panel zoom-in / zoom-out / reset-to-fit cluster pinned
 *  bottom-right. Extracted verbatim from the two identical hand-rolled copies.
 *
 *  These buttons deliberately keep their on-glass chrome (`rounded` /
 *  `text-on-surface-var` / `hover:bg-surface-container`) rather than the
 *  `SquareIconButton` primitive: they sit on a `bg-surface-high/90` blur panel,
 *  where the primitive's `hover:bg-surface-high` would be nearly invisible. This is
 *  a duplicate-component consolidation (T2.2), not a chrome migration. */
export function GraphZoomControls({ onZoomIn, onZoomOut, onReset }: {
  onZoomIn: () => void
  onZoomOut: () => void
  onReset: () => void
}) {
  return (
    <div className="absolute bottom-3 right-3 flex flex-col gap-1 rounded-lg bg-surface-high/90 p-1 backdrop-blur">
      <button type="button" onClick={onZoomIn} title="Zoom in" aria-label="Zoom in"
        className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Plus size={15} /></button>
      <button type="button" onClick={onZoomOut} title="Zoom out" aria-label="Zoom out"
        className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Minus size={15} /></button>
      <button type="button" onClick={onReset} title="Reset view" aria-label="Reset view"
        className="grid size-7 place-items-center rounded text-on-surface-var hover:bg-surface-container hover:text-on-surface"><Maximize2 size={14} /></button>
    </div>
  )
}
