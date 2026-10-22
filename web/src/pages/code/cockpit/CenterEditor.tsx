import { Code2, Loader2, X, Activity, GitBranch, PanelLeftOpen, PanelRightOpen } from 'lucide-react'
import { Button } from '../../../ui/Button'
import { IconButton } from '../../../ui/IconButton'
import { Centered } from '../../../ui/Centered'
import { PaneHost } from '../../../ui/PaneHost'
import { type FsEntry } from '../../../lib/api'
import { ReviewChanges, type ReviewComments } from './ReviewChanges'
import { FileViewer } from '../../files/browse/FileViewer'
import { TypingReveal } from '../TypingReveal'
import { DiffReveal } from '../DiffReveal'
import { BottomTerminal } from './BottomTerminal'
import { CommitView } from './CommitView'
import { useCenterEditor } from './useCenterEditor'
import type { RouteProps } from '../../../app/useQueryState'

// ── The cockpit editor ─────────────────────────────────────────────────────────────────────────
//
// The center of the code cockpit: the file tabs with a re-open chip for a collapsed side panel, and
// under them the open file in the editor, or a file the worker is writing as it lands, or a commit,
// with the diff under review in the pane host beside it and the terminal drawer beneath.
// `useCenterEditor` keeps what it shows and does.

export function CenterEditor({ ws, isProjectDir = false, showTerm, onCloseTerm, running, runCmd, comments, query, setQuery }: {
  ws: string; isProjectDir?: boolean; showTerm: boolean; onCloseTerm: () => void; running: boolean; runCmd?: { cmd: string; n: number } | null
  /** Where a comment on a change under review goes (see `ReviewChanges`). */
  comments?: ReviewComments
} & Partial<Pick<RouteProps, 'query' | 'setQuery'>>) {
  const {
    termOpened, closeTerm, tabs, active, activePath, dirty, open, closeNow, setActivePath, markDirty,
    requestClose, viewerRef, draftStore, collapsedLeft, collapsedRight, reopenPanel, saveFileAsArtifact,
    diff, setDiff, commit, setCommit, mode, reveal, setReveal, tabLabels, activeTabRef, changes, closeReview,
  } = useCenterEditor({ ws, isProjectDir, running, showTerm, onCloseTerm, query, setQuery })
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      {/* editor tabs + pull-out re-open tabs for any collapsed side panel. Render the
          bar whenever there are open file tabs OR a collapsed panel needs its re-open
          chip (so a collapsed Files/Tasks panel is always one click from returning,
          with the editor keeping the full width — no persistent vertical rail). */}
      {(tabs.length > 0 || collapsedLeft.length > 0 || collapsedRight.length > 0) && (
        <div className="flex shrink-0 items-center gap-0.5 border-b border-outline-variant/40 bg-surface-low/40 px-1">
          {/* LEFT: re-open chip(s) for collapsed left panel(s) (e.g. Files). */}
          {collapsedLeft.map(([key, v]) => (
            <Button key={key} variant="ghost" size="xs" onClick={() => reopenPanel(key)}
              title={`Show ${v.label}`} ariaLabel={`Show ${v.label}`} className="mr-0.5 shrink-0">
              <PanelLeftOpen size={14} /> {v.label}
            </Button>
          ))}
          <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
          {tabs.map((t) => (
            // Middle-click closes the tab (standard editor/browser-tab gesture) —
            // routed through requestClose so a dirty tab gets the themed confirm.
            <div key={t.path} ref={(!commit && t.path === activePath) ? activeTabRef : undefined}
              onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); requestClose(t.path, t.name) } }}
              data-type="body-s" className={`group flex shrink-0 items-center gap-1.5 rounded-t-md px-2.5 py-1.5 ${!commit && t.path === activePath ? 'bg-surface text-on-surface' : 'text-on-surface-low hover:text-on-surface'}`}>
              {/* Selecting a tab shows that file, as the one thing the center shows: it closes the
                  commit patch over the editor, and the diff under review beside it. */}
              {/* Raw: a tab in the editor's tab strip, which no primitive draws. */}
              <button type="button" onClick={() => { setDiff(null); setCommit(null); setActivePath(t.path) }} title={t.path} className="max-w-[180px] truncate">{tabLabels[t.path]}</button>
              {/* VSCode-style: a dirty tab shows a dot where the close-X sits, swapped
                  for the X on hover, so unsaved work is visible without hovering. */}
              {dirty[t.path] ? (
                // Raw: the unsaved dot that turns into a close on hover; IconButton draws one icon, and SquareIconButton is taller than the tab.
                <button type="button" onClick={() => requestClose(t.path, t.name)} aria-label={`Close ${t.name} (unsaved changes)`} title="Unsaved changes" className="shrink-0">
                  <span className="size-2 rounded-full group-hover:hidden" style={{ background: 'var(--color-primary)' }} />
                  <X size={12} className="hidden group-hover:block" />
                </button>
              ) : (
                <IconButton icon={X} label={`Close ${t.name}`} onClick={() => requestClose(t.path, t.name)}
                  size={18} iconSize={12}
                  className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100" />
              )}
            </div>
          ))}
          </div>
          {/* RIGHT: re-open chip(s) for collapsed right panel(s) (e.g. Tasks). */}
          {collapsedRight.map(([key, v]) => (
            <Button key={key} variant="ghost" size="xs" onClick={() => reopenPanel(key)}
              title={`Show ${v.label}`} ariaLabel={`Show ${v.label}`} className="ml-0.5 shrink-0">
              <PanelRightOpen size={14} /> {v.label}
            </Button>
          ))}
        </div>
      )}
      {/* The review pane docks beside the editor in the shared pane host, at the width the cockpit
          last left it: the uncommitted changes over the diff of the one under review. Its close
          gives focus back to the row it was opened from. Whether it is open, and on which change,
          is the address's `diff` (see `reviewState`); a session opened at its bare address opens
          it on its first change. */}
      <div className="flex min-h-0 flex-1">
        <PaneHost surface="code"
          pane={diff ? {
            id: diff.path, title: `Diff — ${diff.name}`, label: 'diff', icon: GitBranch, onClose: closeReview,
            body: <ReviewChanges ws={ws} diff={diff} changes={changes} comments={comments} />,
          } : null}>
        {reveal ? (
          <div className="flex h-full flex-col">
            <div data-type="caption" className="flex shrink-0 items-center gap-1.5 border-b border-outline-variant/40 bg-surface-low/40 px-2 py-1 text-on-surface-low">
              <Loader2 size={11} className="animate-spin text-primary" /> {reveal.kind === 'diff' ? 'Editing' : reveal.kind === 'erase' ? 'Deleting' : 'Writing'} {reveal.name}…
            </div>
            <div className="min-h-0 flex-1">
              {/* key by path+kind: DiffReveal captures oldText/newText into a useRef at
                  first render (never updates) — so without a key, a SECOND diff reveal
                  arriving while the first is still mounted reuses the instance + re-
                  animates the PREVIOUS file's diff, then onDone opens the new one (wrong
                  content shown). A fresh key remounts with the new content. (Mirrors the
                  TaskDetailView keying fix.) */}
              {reveal.kind === 'diff'
                ? <DiffReveal key={`d:${reveal.path}`} oldText={reveal.oldText} newText={reveal.newText} theme={mode === 'light' ? 'light' : 'dark'}
                    onDone={() => { open({ name: reveal.name, path: reveal.path, is_dir: false } as FsEntry); setReveal(null) }} />
                : reveal.kind === 'erase'
                ? <TypingReveal key={`e:${reveal.path}`} text={reveal.text} mode="erase" theme={mode === 'light' ? 'light' : 'dark'}
                    onDone={() => { closeNow(reveal.path); setReveal(null) }} />
                : <TypingReveal key={`w:${reveal.path}`} text={reveal.text} mode="write" theme={mode === 'light' ? 'light' : 'dark'}
                    onDone={() => { open({ name: reveal.name, path: reveal.path, is_dir: false } as FsEntry); setReveal(null) }} />}
            </div>
          </div>
        ) : commit ? (
          <CommitView ws={ws} hash={commit.hash} subject={commit.subject} onClose={() => setCommit(null)} />
        ) : active ? (
          <FileViewer key={active.path} ref={viewerRef} entry={{ name: active.name, path: active.path, is_dir: false }} draftStore={draftStore}
            onSaved={(content) => window.dispatchEvent(new CustomEvent('ne:code-file-saved', { detail: { path: active.path, content } }))} onSaveAsArtifact={saveFileAsArtifact}
            onMissing={(p) => closeNow(p)}
            onDirtyChange={(d) => { markDirty(active.path, d); if (d) window.dispatchEvent(new CustomEvent('ne:code-editing')) }} />
        ) : (
          <Centered>
            <div className="flex flex-col items-center gap-2 text-on-surface-low">
              <Code2 size={26} className="opacity-40" />
              <p data-type="body-s">Open a file from the tree to view + edit it.</p>
              {/* Re-engage "follow the worker" — auto-open whatever it edits next. Only
                  while a worker is actually RUNNING; on a terminal/idle project there's
                  nothing to follow, so the button would be a dead no-op. */}
              {running && (
                <Button variant="ghost" size="xs" className="mt-1"
                  onClick={() => window.dispatchEvent(new CustomEvent('ne:code-follow-worker'))}>
                  <Activity size={12} /> Follow the worker
                </Button>
              )}
            </div>
          </Centered>
        )}
        </PaneHost>
      </div>
      {/* Keep the terminal MOUNTED once first opened, toggling it with CSS (hidden
          prop) instead of mount/unmount — so hiding the panel preserves the PTY, any
          running process (a dev server / watch / long build), and the scrollback, like
          a real IDE's terminal-panel toggle. Unmounting on hide (the old behavior)
          killed the shell + lost everything; reopening spawned a fresh one. The PTY is
          torn down only when the cockpit (CenterEditor) unmounts. */}
      {termOpened && ws && <BottomTerminal ws={ws} hidden={!showTerm} onClose={closeTerm} runCmd={runCmd} />}
    </div>
  )
}
