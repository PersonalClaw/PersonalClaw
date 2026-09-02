import { useState } from 'react'

/** CC-9 — one collapsible block of live model reasoning. `<details>` keeps the
 *  open/closed choice in the DOM (uncontrolled), so a user who folds a block mid-
 *  stream is not fought by re-renders; `defaultOpen` only seeds the initial state
 *  (open while the turn is streaming, folded when a finished transcript re-renders).
 *  Plain pre-wrapped text on purpose: thinking is not the answer, so it gets the
 *  muted activity styling, not the Markdown prose pipeline. */
export function ThinkingBlock({ text, defaultOpen }: { text: string; defaultOpen?: boolean }) {
  // Captured ONCE at mount: the prop stays constant across re-renders, so React
  // never re-writes the DOM `open` property and the user's manual fold/unfold wins.
  const [openInit] = useState(!!defaultOpen)
  return (
    <details data-testid="thinking-block" open={openInit}
      className="my-1 rounded-md border border-outline/40 bg-surface-high/40 px-2 py-1 text-[0.75rem] text-on-surface-low">
      <summary className="cursor-pointer select-none opacity-80">Thinking</summary>
      <div className="mt-1 whitespace-pre-wrap break-words">{text}</div>
    </details>
  )
}
