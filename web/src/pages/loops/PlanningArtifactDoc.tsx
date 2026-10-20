import { useRef } from 'react'
import { Markdown } from '../../ui/Markdown'
import { CommentLayer } from '../files/comments/CommentLayer'
import type { CommentTarget } from '../../ui/content/commentTarget'

/** A planning artifact's markdown body with the text-highlight comment layer —
 *  selecting a passage and commenting routes to the planning agent (the same path
 *  as the step's prose comment box, via the `commentTarget` the walkthrough wires
 *  to `api.uLoopPlanComment`). Read-only prose, so this is a thin Markdown +
 *  CommentLayer rather than the full editable ContentSurface. */
export function PlanningArtifactDoc({ markdown, docId, label, commentTarget }: {
  markdown: string
  docId: string
  label: string
  commentTarget?: CommentTarget
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  return (
    <div ref={scrollRef} className="relative text-on-surface-var">
      <Markdown>{markdown}</Markdown>
      {commentTarget && (
        <CommentLayer scrollRef={scrollRef} docId={docId} docLabel={label}
          content={markdown} onSubmit={(message, docPaths) => commentTarget.submit({ message, docPaths })} />
      )}
    </div>
  )
}
