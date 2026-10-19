import { useState } from 'react'
import { FileCode } from 'lucide-react'
import { TextLink } from '../../../ui/TextLink'
import { resolveTouchedPath } from './cockpitShared'

// ── The files a cockpit run touched ────────────────────────────────────────────────────────────
//
// The files a finding or an outcome names, as chips that open in the editor, capped with a
// "+N more" that shows the rest.

/** Clickable chips for the files a cycle touched. Only files under the project's
 *  workspace are openable (the worker also lists its own findings/ paths, which
 *  live outside the workspace and aren't editable here); those are dropped. Click
 *  opens the file in the center editor via the same event the file tree uses. */
export function FilesTouched({ files, ws, max = 8 }: { files?: string[]; ws: string; max?: number }) {
  const [expanded, setExpanded] = useState(false)
  if (!files || !files.length || !ws) return null
  const root = ws.replace(/\/$/, '')
  // Resolve each path via the shared helper (worktree remap + bare-relative + outside-
  // root rejection), then dedupe by resolved absolute path — different raw forms of the
  // SAME file (a worktree path that remaps to <ws>/foo.py AND a bare "foo.py") would
  // otherwise render two identical chips + collide on the React key={abs}.
  const seen = new Set<string>()
  const unique = files
    .map((raw) => resolveTouchedPath(raw, root))
    .filter((r): r is { abs: string; rel: string } => !!r && (seen.has(r.abs) ? false : (seen.add(r.abs), true)))
  if (!unique.length) return null
  const open = (path: string) => {
    const name = path.split('/').pop() || path
    window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name, path, is_dir: false } }))
  }
  // Cap the chips by default — a broad-refactor cycle can touch dozens of files, which
  // otherwise blew up the finding card's height (the OutcomeBanner pre-capped; the
  // per-finding cards didn't). A "+N more" toggle reveals the rest in place.
  const shown = expanded ? unique : unique.slice(0, max)
  const hidden = unique.length - shown.length
  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1">
      {shown.map(({ abs: p, rel }) => {
        return (
          <button key={p} type="button" onClick={() => open(p)} title={rel}
            data-type="caption" className="inline-flex max-w-full items-center gap-1 rounded bg-surface-high px-1.5 py-0.5 text-on-surface-low transition-colors hover:text-primary">
            <FileCode size={10} className="shrink-0" />
            <span className="truncate">{rel}</span>
          </button>
        )
      })}
      {hidden > 0 && (
        <TextLink size="xs" ink="emphasis" onClick={() => setExpanded(true)}
          title={`Show ${hidden} more file${hidden === 1 ? '' : 's'}`}>+{hidden} more</TextLink>
      )}
    </div>
  )
}
