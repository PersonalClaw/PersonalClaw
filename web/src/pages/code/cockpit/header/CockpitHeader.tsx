import { Code2, Play, Pause, Square, Trash2, ListChecks, TerminalSquare, ChevronLeft, Target } from 'lucide-react'
import { TopBar } from '../../../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../../../ui/HeaderActions'
import { IconButton } from '../../../../ui/IconButton'
import { confirmStopLoop } from '../../../loop/stopLoop'
import { type CodeProject } from '../../../../lib/api'
import { LOOP_ACTION_SOURCE_STATUSES } from '../../../../lib/loopStatus'
import { type CodeToastState } from '../../CodeToast'
import { cmdLabel, type MissingCommandNotice } from '../cockpitCommands'
import { useTitleRename } from './useTitleRename'

// ── The cockpit's header ──────────────────────────────────────────────────────────────────────
//
// The cockpit's title bar: back to the containing Project, the project's name (click to rename),
// whether the run's feed is live, and its actions: run controls, the terminal, build and tests,
// a new target on the same workspace, all projects, and delete. `useTitleRename` keeps the rename.

export function CockpitHeader({ id, project: p, setProject, setToast, onOpenProject, active, connected, acting, act, ws, showTerm, setShowTerm,
  missingBuild, missingTest, runInWorkspaceTerminal, onNewTarget, onBack, del }: {
  id: string; project: CodeProject; setProject: (p: CodeProject) => void; setToast: (t: CodeToastState | null) => void
  onOpenProject?: (projectId: string) => void
  active: boolean; connected: boolean; acting: boolean; act: (action: 'start' | 'pause' | 'resume' | 'stop') => Promise<void>
  ws: string; showTerm: boolean; setShowTerm: (on: boolean) => void
  missingBuild?: MissingCommandNotice; missingTest?: MissingCommandNotice; runInWorkspaceTerminal: (command: string) => void
  onNewTarget?: (workspaceDir: string) => void; onBack: () => void; del: () => Promise<void>
}) {
  const { editingTitle, titleDraft, setTitleDraft, startRename, abortRename, commitRename } = useTitleRename({ id, project: p, setProject, setToast })
  return (
    <TopBar
      left={<div className="flex min-w-0 items-center gap-2">
        {/* Back-to-project: left of the title, jumps to the Project this loop belongs to
            (tasks_project_id is the provisioned container; project_id a pre-launch scope).
            Only shown when the loop is actually bound to a project AND we can open it. */}
        {onOpenProject && (p.tasks_project_id || p.project_id) && (
          <IconButton icon={ChevronLeft} label="Back to the project this loop belongs to"
            onClick={() => onOpenProject((p.tasks_project_id || p.project_id) as string)}
            size={28} iconSize={18} className="shrink-0" />
        )}
        <Code2 size={18} className="shrink-0 text-primary" />
        {editingTitle ? (
          <input autoFocus value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)}
            onBlur={commitRename} onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); else if (e.key === 'Escape') abortRename() }}
            aria-label="Rename project"
            data-type="body-m" className="min-w-[14rem] h-7 rounded-md bg-surface-high px-2 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
        ) : (
          <button type="button" onClick={startRename} title="Rename project"
            data-type="title-l" className="truncate text-on-surface text-left hover:text-on-surface-var">
            {p.name}
          </button>
        )}
        {/* StageTrail (phase-execution status) moved OUT of the title row into the
            dedicated status bar below the header (CockpitMeta). */}
        {/* FEED liveness — the last of the four `useRunStream` consumers to get it, after the
            workflow run view, the loop cockpit and the design cockpit. A running code loop whose
            stream dropped keeps showing its last stage while nothing arrives, indistinguishable from
            a loop that is simply working. Same dot-plus-WORD form `settings/DiagnosticsPanel` ships,
            so the vocabulary is not re-invented and the colour only confirms the word. Shown only
            while `active` — a finished loop has no stream to be connected to. */}
        {active && (
          <span data-type="caption" className="inline-flex shrink-0 items-center gap-1 text-on-surface-low">
            <span className="inline-block size-1.5 rounded-pill"
              style={{ background: connected ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }} />
            {connected ? 'Streaming' : 'Connecting…'}
          </span>
        )}
      </div>}
      right={<HeaderActions>
        {/* Run controls + terminal toggle are primary/default (frequent, contextual);
            everything else (run build/tests, reuse workspace, navigate away, delete)
            is low-priority so the cluster's own overflow menu absorbs it first. */}
        {LOOP_ACTION_SOURCE_STATUSES.pause.has(p.status)
          ? <HeaderControl icon={Pause} label="Pause" priority="primary" onClick={() => act('pause')} disabled={acting} />
          : LOOP_ACTION_SOURCE_STATUSES.resume.has(p.status)
            ? <HeaderControl icon={Play} label="Resume" priority="primary" onClick={() => act('resume')} variant="primary" disabled={acting} />
            : LOOP_ACTION_SOURCE_STATUSES.start.has(p.status)
              ? <HeaderControl icon={Play} label="Start" priority="primary" onClick={() => act('start')} variant="primary" disabled={acting} />
              : null}
        {/* Stop is TERMINAL + non-resumable — confirm first, like Delete. `stagnant`
            is an active attention state (resume + stop both valid). */}
        {LOOP_ACTION_SOURCE_STATUSES.stop.has(p.status) &&
          <HeaderControl icon={Square} label="Stop" priority="primary" onClick={async () => {
            // The one Stop confirm, said once for every Stop control (`loop/stopLoop`).
            if (await confirmStopLoop(p.status, p.name)) act('stop')
          }} />}
        {/* A shell only makes sense in a real codebase (gate on `ws`). */}
        {!!ws && <HeaderControl icon={TerminalSquare} label={showTerm ? 'Hide terminal' : 'Terminal'} onClick={() => setShowTerm(!showTerm)} active={showTerm} ariaExpanded={showTerm} />}
        {/* Run the configured build/test command in the cockpit terminal (only with a
            real workspace + the command configured). Low priority → overflow first. */}
        {!!ws && p.verify_command && <HeaderControl icon={Play}
          label={missingBuild ? `Run build unavailable (${missingBuild.binary} not on PATH)` : `Run build (${cmdLabel(p.verify_command)})`}
          hint={missingBuild?.command} disabled={!!missingBuild} priority="low"
          onClick={() => runInWorkspaceTerminal(p.verify_command || '')} />}
        {!!ws && p.test_command && <HeaderControl icon={Play}
          label={missingTest ? `Run tests unavailable (${missingTest.binary} not on PATH)` : `Run tests (${cmdLabel(p.test_command)})`}
          hint={missingTest?.command} disabled={!!missingTest} priority="low"
          onClick={() => runInWorkspaceTerminal(p.test_command || '')} />}
        {/* Reuse this project's BOUND workspace for a fresh target — only with a real
            codebase dir + not mid-run. */}
        {onNewTarget && !active && !!ws && <HeaderControl icon={Target} label="New target" priority="low" onClick={() => onNewTarget(ws)} />}
        <HeaderControl icon={ListChecks} label="All projects" priority="low" onClick={onBack} />
        <HeaderControl icon={Trash2} label="Delete project" danger priority="low" onClick={() => { void del() }} />
      </HeaderActions>} />
  )
}
