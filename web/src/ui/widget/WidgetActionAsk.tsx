/** The body of the shell's ask before a frame's action goes to a new chat: the exact text it
 *  would send, all of it, in a box a keyboard and a screen reader reach, with its size said
 *  above it and a cue while there is more below. The text is already in the form it is sent
 *  (`composeWidgetActionText` makes hidden characters visible), so what shows is what goes. */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

/** The lines *text* shows as: a final line break ends the last line, it does not start one. */
export function lineCount(text: string): number {
  const lines = text.split(/\r\n|\r|\n/)
  return lines.length > 1 && lines[lines.length - 1] === '' ? lines.length - 1 : lines.length
}

export function WidgetActionAsk({ text }: { text: string }) {
  const box = useRef<HTMLPreElement>(null)
  const [moreBelow, setMoreBelow] = useState(false)
  const measure = useCallback(() => {
    const el = box.current
    if (el) setMoreBelow(el.scrollTop + el.clientHeight < el.scrollHeight - 1)
  }, [])
  useLayoutEffect(measure, [text, measure])
  // A narrower or shorter window can hide what was in view: measure again.
  useEffect(() => {
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [measure])
  const chars = [...text].length
  const lines = lineCount(text)
  return (
    <span className="flex flex-col gap-s">
      <span>A widget asked to send this to a new chat as your message. It goes only if you send it.</span>
      <span data-type="caption" data-testid="widget-action-size" className="text-on-surface-low">
        {chars} {chars === 1 ? 'character' : 'characters'} · {lines} {lines === 1 ? 'line' : 'lines'}
      </span>
      <span className="relative block">
        <pre
          ref={box}
          tabIndex={0}
          role="region"
          aria-label="The message it would send"
          data-testid="widget-action-text"
          onScroll={measure}
          className="block max-h-[min(15rem,35dvh)] overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-surface-high px-m py-s text-on-surface"
        >
          {text}
        </pre>
        {moreBelow && (
          <span
            data-testid="widget-action-more"
            className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center rounded-b-md bg-gradient-to-t from-surface-high to-transparent pt-l text-on-surface-low"
            data-type="caption"
          >
            More below
          </span>
        )}
      </span>
    </span>
  )
}
