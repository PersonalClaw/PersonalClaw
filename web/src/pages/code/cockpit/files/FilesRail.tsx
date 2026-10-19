import { ListChecks, FolderTree, GitBranch } from 'lucide-react'
import { type LoopMerge } from '../../../../lib/api'
import { WorkspaceTree } from './WorkspaceTree'
import { ChangesPanel } from './ChangesPanel'
import { FileFinder } from './FileFinder'
import { useFilesRail } from './useFilesRail'

// ── The cockpit's files rail ───────────────────────────────────────────────────────────────────
//
// The left of the code cockpit: a Files tab with the find-a-file field over the workspace tree, and
// a Changes tab with the workspace's git changes and the run's merged work. `useFilesRail` keeps
// which tab is up and the Changes count shown on the Files tab.

export function FilesRail({ ws, isProjectDir, running, merges }: { ws: string; isProjectDir: boolean; running: boolean; merges?: LoopMerge[] }) {
  const { tab, setTab, changeCount } = useFilesRail({ ws, isProjectDir, running })
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-0.5 border-b border-outline-variant/40 px-2 py-1.5">
        <RailTab icon={FolderTree} label="Files" on={tab === 'files'} onClick={() => setTab('files')} />
        <RailTab icon={GitBranch} label="Changes" on={tab === 'changes'} onClick={() => setTab('changes')} badge={tab === 'files' ? changeCount : 0} />
      </div>
      {/* Find-file-by-name on the Files tab (a real workspace only) — a mini-IDE
          essential: a brownfield repo can have hundreds of files, and the lazy tree
          only shows expanded dirs, so scrolling to a file is impractical. Reuses the
          existing workspace-scoped fuzzy fileSearch endpoint. */}
      {tab === 'files' && !!ws && <FileFinder ws={ws} />}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {tab === 'files' ? <WorkspaceTree ws={ws} running={running} isProjectDir={isProjectDir} />
          : <ChangesPanel ws={ws} running={running} isProjectDir={isProjectDir} merges={merges} />}
      </div>
    </div>
  )
}

function RailTab({ icon: Icon, label, on, onClick, badge }: { icon: typeof ListChecks; label: string; on: boolean; onClick: () => void; badge?: number }) {
  // Raw: a tab that swaps the rail's pane and carries a count; no tab primitive exists (Segmented is a radiogroup).
  return (
    <button type="button" onClick={onClick}
      data-type="body-s" className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 transition-colors"
      style={on ? { background: 'var(--color-surface-high)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
      <Icon size={14} /> {label}
      {/* Pending-change count — lets the user see review-worthy changes are waiting
          while they're on another tab (a worker editing files updates this live). */}
      {!!badge && badge > 0 && (
        <span data-type="caption" className="ml-0.5 inline-flex min-w-[1.1rem] items-center justify-center rounded-full px-1 tabular-nums"
          style={{ background: on ? 'var(--color-primary)' : 'color-mix(in srgb, var(--color-primary) 22%, transparent)', color: on ? 'var(--color-on-primary)' : 'var(--color-primary)' }}>
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </button>
  )
}
