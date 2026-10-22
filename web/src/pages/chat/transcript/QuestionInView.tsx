import { useEffect, useState, type RefObject } from 'react'
import { CornerLeftUp } from 'lucide-react'
import { api } from '../../../lib/api'
import { useQuery } from '../../../lib/data'
import { TileButton } from '../../../ui/TileButton'

/** "Keep my question in view" (Settings › Chat, off by default; #3820): while a reply taller than
 *  the window streams, the reading column pins a one-line banner with the owner's latest question.
 *
 *  A reply taller than the window is one that has scrolled its question out above the view, which
 *  the column's IntersectionObserver says without measuring anything itself. The banner shows the
 *  question's first line; a click scrolls back to the question. It leaves once the reply settles,
 *  the question is back in view, or the owner turns the setting off. It is static: it moves with
 *  the column's own scroll and has no animation of its own, so reduced motion changes nothing. */
export function QuestionInView({ streaming, question, node, scrollRef }: {
  /** A reply is streaming in this chat now. */
  streaming: boolean
  /** The latest question's text. */
  question: string
  /** The latest question's node in the column, once it is drawn. */
  node: HTMLElement | null
  /** The column's scroll container, the root the question's visibility is read against. */
  scrollRef: RefObject<HTMLDivElement | null>
}) {
  const { data: enabled } = useQuery('chat:keep-question-in-view', () => api.dashboardConfig().then((c) => !!c.keep_question_in_view), { persist: true })
  const [outAbove, setOutAbove] = useState(false)
  const watching = !!enabled && streaming && !!node
  useEffect(() => {
    if (!watching || !node || typeof IntersectionObserver === 'undefined') { setOutAbove(false); return }
    const observer = new IntersectionObserver((entries) => {
      for (const e of entries) {
        // Out of view ABOVE: scrolled past, which a growing reply does. Below the view is a
        // question not yet reached, which no banner should stand in for.
        const top = e.rootBounds?.top ?? 0
        setOutAbove(!e.isIntersecting && e.boundingClientRect.top < top)
      }
    }, { root: scrollRef.current })
    observer.observe(node)
    return () => observer.disconnect()
  }, [watching, node, scrollRef])
  if (!watching || !outAbove) return null
  const firstLine = question.split('\n').find((l) => l.trim())?.trim() ?? ''
  if (!firstLine) return null
  // A TileButton, not a Button: Button is a centred no-wrap pill whose label row cannot shrink, so a
  // long first line would be clipped rather than end in an ellipsis. The tile owns the border and the
  // hover; the row inside it lays out the glyph and the line.
  return (
    <TileButton onClick={() => node?.scrollIntoView({ block: 'start' })}
      ariaLabel={`Your question: ${firstLine}. Scroll to it`}
      className="sticky top-0 z-10 w-full min-w-0 text-on-surface-var shadow-sm hover:text-on-surface">
      <span data-type="body-s" className="flex min-w-0 items-center gap-s px-m py-s">
        <CornerLeftUp size={14} aria-hidden className="shrink-0 text-on-surface-low" />
        <span className="min-w-0 truncate">{firstLine}</span>
      </span>
    </TileButton>
  )
}
