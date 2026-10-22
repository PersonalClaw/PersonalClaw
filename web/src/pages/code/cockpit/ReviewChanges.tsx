import { DiffView } from '../DiffView'
import { ChangeList } from './files/ChangesPanel'
import { changePath } from './files/changes'

// ── The review pane's changes ─────────────────────────────────────────────────────────────────
//
// What the cockpit's review pane shows: the workspace's uncommitted changes, the one under review
// marked, over that change's diff against HEAD. Choosing another change shows its diff in place.

export function ReviewChanges({ ws, diff, changes }: {
  ws: string
  diff: { path: string; name: string; deleted?: boolean }
  changes: [string, string][]
}) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      {changes.length > 0 && (
        <ChangeList ws={ws} changes={changes} shown={changePath(ws, diff.path)}
          className="max-h-40 shrink-0 overflow-y-auto border-b border-outline-variant/40 py-1" />
      )}
      <div className="min-h-0 flex-1">
        <DiffView path={diff.path} name={diff.name} ws={ws} deleted={diff.deleted} />
      </div>
    </div>
  )
}
