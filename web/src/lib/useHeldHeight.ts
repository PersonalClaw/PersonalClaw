import { useLayoutEffect, useState, type RefObject } from 'react'

/** The height *ref*'s element took, measured while *live* and kept once it isn't: what a card's
 *  answers held, so the outcome, or the one line a selected card shows, takes their place at their
 *  height and nothing under it moves. Measured, never a constant: answers are 28 px from 1024 px,
 *  44 px below, 56 px at 200% text, and wrap onto a second row at 320 px. */
export function useHeldHeight(ref: RefObject<HTMLElement | null>, live: boolean): number {
  const [height, setHeight] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!live || !el) return
    const read = () => setHeight(el.getBoundingClientRect().height)
    read()
    if (typeof ResizeObserver !== 'function') return
    const seen = new ResizeObserver(read)
    seen.observe(el)
    return () => seen.disconnect()
  }, [live, ref])
  return height
}
