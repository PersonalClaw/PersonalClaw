import { type ReactNode } from 'react'
import { Reorder } from 'framer-motion'
import { spring } from '../../design/motion'

/** Delightful drag-to-reorder for a simple vertical list, built on Motion's
 *  `Reorder` (physics-y lift + spring settle). For KEYBOARD-accessible or
 *  multi-container DnD (kanban, nav), use dnd-kit instead — this is the
 *  lightweight path for single-list reordering where a mouse/touch drag suffices.
 *
 *  Generic over the item type; `getKey` yields a stable key per item. */
export function Reorderable<T>({
  items, onReorder, getKey, renderItem, className, axis = 'y',
}: {
  items: T[]
  onReorder: (next: T[]) => void
  getKey: (item: T) => string
  renderItem: (item: T) => ReactNode
  className?: string
  axis?: 'x' | 'y'
}) {
  return (
    <Reorder.Group axis={axis} values={items} onReorder={onReorder} className={className} as="div">
      {items.map((item) => (
        <Reorder.Item
          key={getKey(item)}
          value={item}
          as="div"
          transition={spring.spatialDefault}
          whileDrag={{ scale: 1.03, zIndex: 10 }}
        >
          {renderItem(item)}
        </Reorder.Item>
      ))}
    </Reorder.Group>
  )
}
