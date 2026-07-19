import { lazy, Suspense, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { api } from '../../lib/api'
import { useMode } from '../../app/theme'
import { monacoLang } from '../files/fileMeta'

// Monaco's side-by-side diff editor, lazy like the main editor (shares the
// locally-bundled monaco from monacoSetup).
const MonacoDiff = lazy(() => import('@monaco-editor/react').then((m) => ({ default: m.DiffEditor })))

/** Working-vs-HEAD diff for a changed file in the cockpit's Changes tab.
 *  Left = committed (HEAD) version (empty for a newly-added file), right = the
 *  current working copy. Read-only — this is a review surface; edits happen in
 *  the normal editor tab. */
export function DiffView({ path, name, ws, deleted = false }: { path: string; name: string; ws: string; deleted?: boolean }) {
  const { mode } = useMode()
  const [original, setOriginal] = useState<string | null>(null)
  const [modified, setModified] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Either side capped at the 512KB read limit — surfaced so a large file's diff
  // isn't misread as "the tail was deleted" (no-silent-caps; mirrors fileRead +
  // the commit view, C407/C408).
  const [truncated, setTruncated] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    setOriginal(null); setModified(null); setError(null); setTruncated(false)
    // The HEAD side legitimately 404s for an untracked/new file (no committed
    // version) → fall back to '' so it diffs as all-added. The WORKING copy read
    // must NOT be swallowed to '' for a modified/new file — the Changes tab only
    // lists git-modified files, so a failed read there is a real error, not an
    // empty file. The EXCEPTION is a DELETED file: its working copy is genuinely
    // gone, so the read failing IS expected → fall back to '' (all-removed diff).
    const workRead = deleted
      ? api.fileRead(path, true).then((r) => ({ content: r.content, truncated: r.truncated })).catch(() => ({ content: '', truncated: false }))
      : api.fileRead(path, true).then((r) => ({ content: r.content, truncated: r.truncated }))
    Promise.all([
      api.fileGitOriginal(path).then((r) => ({ content: r.content, truncated: !!r.truncated })).catch(() => ({ content: '', truncated: false })),
      workRead,
    ]).then(([orig, work]) => {
      if (!alive) return
      setOriginal(orig.content); setModified(work.content); setTruncated(orig.truncated || work.truncated)
    }).catch(() => { if (alive) setError('Could not load the diff.') })
    return () => { alive = false }
  }, [path, attempt, deleted])

  if (error) return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-on-surface-low text-[0.875rem]">
      <span>{error}</span>
      <button type="button" onClick={() => setAttempt((n) => n + 1)}
        className="rounded-md bg-surface-high px-3 py-1 text-on-surface-var hover:bg-surface-container hover:text-on-surface">Try again</button>
    </div>
  )
  if (original === null || modified === null) {
    return <div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>
  }
  const rel = (() => {
    const base = ws.replace(/\/$/, '').split('/').pop() || ''
    const marker = `/${base}/`; const i = base ? path.lastIndexOf(marker) : -1
    return i >= 0 ? path.slice(i + marker.length) : name
  })()
  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-outline-variant/40 bg-surface-low/40 px-3 py-1.5 text-on-surface-var text-[0.8125rem]">
        {/* An untracked/new file has no committed (HEAD) version — every line shows as
            added — so "working vs HEAD" misreads. Label it honestly as a new file. */}
        <span className="font-mono">{rel}</span>{' '}
        {/* Label honestly: a deleted file, a new (no-HEAD) file, an empty file (both
            sides blank — "working vs HEAD" would imply a diff that doesn't exist), else
            a real working-vs-HEAD change. */}
        <span className="text-on-surface-low text-[0.7rem]">· {
          deleted ? 'deleted — removed from working tree'
          : original === '' && modified !== '' ? 'new file — not yet committed'
          : original === '' && modified === '' ? 'empty file — nothing to compare'
          : 'working vs HEAD'}</span>
        {truncated && (
          <span className="ml-2 text-[0.7rem]" style={{ color: 'var(--color-warn)' }}>
            · large file — diff truncated at 512&nbsp;KB; later changes aren't shown
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1">
        <Suspense fallback={<div className="flex h-full items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>}>
          <MonacoDiff
            original={original} modified={modified}
            language={monacoLang(name)}
            theme={mode === 'light' ? 'light' : 'vs-dark'}
            // ignoreTrimWhitespace defaults to true, which SILENTLY hides
            // whitespace-only line changes — exactly when the worker re-indents code
            // (reformatting, nesting changes, Python especially). For an honest
            // review of what the agent did, always show whitespace diffs.
            // renderSideBySideInlineBreakpoint: below this panel width Monaco collapses
            // the two columns into a single inline diff — the cockpit center panel is
            // narrow (file tree + tasks flank it), where side-by-side is unreadable.
            options={{ readOnly: true, renderSideBySide: true, renderSideBySideInlineBreakpoint: 700, automaticLayout: true, fontSize: 13, minimap: { enabled: false }, ignoreTrimWhitespace: false }}
          />
        </Suspense>
      </div>
    </div>
  )
}
