import { type ReactNode } from 'react'
import { Reorder } from 'framer-motion'
import { dragSpring } from '../../design/motion'

/** Delightful drag-to-reorder for a simple vertical list, built on Motion's
 *  `Reorder` (physics-y lift + spring settle). For KEYBOARD-accessible or
 *  multi-container DnD (kanban, nav), use dnd-kit instead — this is the
 *  lightweight path for single-list reordering where a mouse/touch drag suffices.
 *
 *  Generic over the item type; `getKey` yields a stable key per item. */
export function Reorderable<T>({
  items, onReorder, getKey, renderItem, className, axis = 'y', canDrag,
}: {
  items: T[]
  onReorder: (next: T[]) => void
  getKey: (item: T) => string
  renderItem: (item: T) => ReactNode
  className?: string
  axis?: 'x' | 'y'
  /** Per-item drag lock. Omit for the previous behaviour (everything drags). */
  canDrag?: (item: T) => boolean
}) {
  return (
    <Reorder.Group axis={axis} values={items} onReorder={onReorder} className={className} as="div">
      {items.map((item) => {
        // A locked item is rendered as a PLAIN div, not a `Reorder.Item` with `drag={false}`.
        // Measured (S61k): `Reorder.Item` makes the whole row draggable, so styling the grip as
        // disabled is cosmetic — the row still picks up and reorders. Keeping it out of the
        // reorder group is what actually locks it.
        const locked = canDrag ? !canDrag(item) : false
        if (locked) {
          return <div key={getKey(item)}>{renderItem(item)}</div>
        }
        return (
          <Reorder.Item
            key={getKey(item)}
            value={item}
            as="div"
            // The gesture-return spring: this transition governs both the rows shoving
            // aside mid-drag and the dropped row landing, so it is the drag's own physics
            // rather than the generic spatial default.
            transition={dragSpring()}
            whileDrag={{ scale: 1.03, zIndex: 10 }}
          >
            {renderItem(item)}
          </Reorder.Item>
        )
      })}
    </Reorder.Group>
  )
}
