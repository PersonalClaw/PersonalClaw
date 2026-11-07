import { useEffect, useRef, useState } from 'react'
import { Clipboard, Quote } from 'lucide-react'
import { SelectionToolbar } from '../../../ui/SelectionPill'
import { copyText } from '../../../app/clipboard'

/** Select-to-quote — when the user selects text inside the transcript, float a
 *  toolbar (Quote + Copy) near the selection. Quote inserts an
 *  ATTRIBUTED blockquote into the composer (who said it, resolved from the
 *  enclosing turn); Copy copies the plain text. Positioning tracks BOTH mouseup
 *  and `selectionchange` so keyboard/touch selections get the toolbar too (not
 *  only mouse drags). */
export function SelectionQuote({ scrollRef, onQuote, attributionFor, widgetWordsIn }: {
  scrollRef: React.RefObject<HTMLDivElement | null>
  onQuote: (text: string, attribution?: string, widgetWords?: boolean) => void
  attributionFor: (node: Node | null) => string | undefined
  /** Whether the selection takes in any of a widget's messages: what it quotes is then a
   *  widget's words, wherever its other ends lie. */
  widgetWordsIn?: (range: Range) => boolean
}) {
  const [pos, setPos] = useState<{ x: number; y: number; text: string; attribution?: string; widgetWords?: boolean } | null>(null)
  const barRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const root = scrollRef.current
    if (!root) return
    const recompute = () => {
      const sel = window.getSelection()
      const text = sel?.toString().trim() ?? ''
      if (!text || !sel || sel.rangeCount === 0) { setPos(null); return }
      const range = sel.getRangeAt(0)
      // only within the transcript
      if (!root.contains(range.commonAncestorContainer)) { setPos(null); return }
      const r = range.getBoundingClientRect()
      const pr = root.getBoundingClientRect()
      // The toolbar is position:absolute inside the SCROLLING root, so its
      // coordinates are content-relative, not viewport-relative. Add scrollLeft/
      // scrollTop or it drifts far from the selection once the transcript scrolls.
      setPos({
        x: r.left - pr.left + root.scrollLeft + r.width / 2,
        y: r.top - pr.top + root.scrollTop - 8,
        text,
        attribution: attributionFor(range.commonAncestorContainer),
        widgetWords: widgetWordsIn?.(range) ?? false,
      })
    }
    const onUp = (e: MouseEvent) => {
      // a click ON the toolbar must NOT recompute/clear before its own handler runs.
      if (barRef.current && e.target instanceof Node && barRef.current.contains(e.target)) return
      recompute()
    }
    // selectionchange fires for keyboard + touch selection too; debounce a frame so
    // it settles (and never fights the mouseup path). No-selection clears the bar.
    let raf = 0
    const onSelChange = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const sel = window.getSelection()
        if (!sel || !sel.toString().trim()) { setPos(null); return }
        recompute()
      })
    }
    // clear on a fresh mousedown that ISN'T the toolbar (don't unmount mid-click).
    const onDown = (e: MouseEvent) => {
      if (barRef.current && e.target instanceof Node && barRef.current.contains(e.target)) return
      setPos(null)
    }
    document.addEventListener('mouseup', onUp)
    document.addEventListener('selectionchange', onSelChange)
    root.addEventListener('mousedown', onDown)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('selectionchange', onSelChange)
      root.removeEventListener('mousedown', onDown)
    }
  }, [scrollRef, attributionFor, widgetWordsIn])
  if (!pos) return null
  const clear = () => { setPos(null); window.getSelection()?.removeAllRanges() }
  return (
    <SelectionToolbar ref={barRef} x={pos.x} y={pos.y} actions={[
      { icon: Quote, label: 'Quote', onPress: () => { onQuote(pos.text, pos.attribution, pos.widgetWords); clear() } },
      { icon: Clipboard, label: 'Copy', onPress: () => { void copyText(pos.text, 'the selection'); clear() } },
    ]} />
  )
}
