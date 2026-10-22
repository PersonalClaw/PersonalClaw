import { useEffect, useState } from 'react'
import { DiffView, type SelectedLines } from '../DiffView'
import { ChangeList } from './files/ChangesPanel'
import { changePath } from './files/changes'
import { ChangeComment } from './ChangeComment'
import type { CommentTarget } from '../../../ui/content/commentTarget'

// ── The review pane's changes ─────────────────────────────────────────────────────────────────
//
// What the cockpit's review pane shows: the workspace's uncommitted changes, the one under review
// marked, over that change's diff against HEAD, and under it a comment on that change for the
// agent, quoting the lines selected in the diff. Choosing another change shows its diff in place.

/** Where a comment on a change goes, and what it is kept under until sent. */
export type ReviewComments = {
  /** The loop the change belongs to, which the unsent comment is kept under. */
  loopId: string
  /** Where a comment on the change at `rel` goes. */
  targetFor: (rel: string) => CommentTarget
}

export function ReviewChanges({ ws, diff, changes, comments }: {
  ws: string
  diff: { path: string; name: string; deleted?: boolean }
  changes: [string, string][]
  comments?: ReviewComments
}) {
  const rel = changePath(ws, diff.path)
  const [selected, setSelected] = useState<SelectedLines | null>(null)
  // A selection is of one change's lines.
  useEffect(() => setSelected(null), [diff.path])
  return (
    <div className="flex h-full min-h-0 flex-col">
      {changes.length > 0 && (
        <ChangeList ws={ws} changes={changes} shown={rel}
          className="max-h-40 shrink-0 overflow-y-auto border-b border-outline-variant/40 py-1" />
      )}
      <div className="min-h-0 flex-1">
        <DiffView path={diff.path} name={diff.name} ws={ws} deleted={diff.deleted} onSelectLines={comments ? setSelected : undefined} />
      </div>
      {comments && (
        <ChangeComment key={rel} draftKey={`change:${comments.loopId}:${rel}`} rel={rel} selected={selected}
          onClearSelection={() => setSelected(null)} target={comments.targetFor(rel)} />
      )}
    </div>
  )
}
