import { type ReactNode } from 'react'
import { Reorder } from 'framer-motion'
import { dragSpring } from '../../design/motion'

/** Splice a reordered subset of DRAGGABLE items back into the full list, keeping every
 *  locked item at the index it already had.
 *
 *  A locked item is not a member of the reorder group (see below), so Motion's `onReorder`
 *  can only ever describe the draggable rows. Treating that partial array as the whole list
 *  DELETES every locked row — which is the exact opposite of what locking a row is for, and
 *  is why the merge lives here, in the primitive that removed them, rather than in each
 *  consumer's callback. Every consumer therefore receives what `onReorder`'s type promises:
 *  the full list, in its new order.
 *
 *  Exported for direct testing: the merge is pure list algebra and does not need a DOM.
 */
export function mergeLockedOrder<T>(
  items: T[],
  nextDraggable: T[],
  canDrag: (item: T) => boolean,
  getKey: (item: T) => string,
): T[] {
  // Defensive: a draggable row missing from `nextDraggable` must not vanish either. It keeps
  // its relative order after the rows Motion did report, so the worst case is a wrong ORDER
  // — never a lost row.
  const reported = new Set(nextDraggable.map(getKey))
  const order = [...nextDraggable, ...items.filter((it) => canDrag(it) && !reported.has(getKey(it)))]
  let next = 0
  return items.map((item) => (canDrag(item) ? (order[next++] ?? item) : item))
}

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
  // The group's `values` must be exactly the items it renders as `Reorder.Item`s — locked rows
  // are not among them — and the consumer's `onReorder` must still receive the whole list.
  const draggable = canDrag ? items.filter((item) => canDrag(item)) : items
  const handleReorder = canDrag
    ? (nextDraggable: T[]) => onReorder(mergeLockedOrder(items, nextDraggable, canDrag, getKey))
    : onReorder
  return (
    <Reorder.Group axis={axis} values={draggable} onReorder={handleReorder} className={className} as="div">
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
