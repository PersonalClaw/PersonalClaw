import { AnimatePresence } from 'framer-motion'
import { Code2, Loader2, ListChecks, FolderTree, HelpCircle } from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { Button } from '../../ui/Button'
import { LoadError } from '../../ui/ListScaffold'
import { Centered } from '../../ui/Centered'
import { type CodeStage } from '../../lib/api'
import { WorkspacePicker } from './WorkspacePicker'
import { CockpitPromptBar } from '../loops/CockpitPromptBar'
import { CockpitToast } from './CodeToast'
import { type RouteProps } from '../../app/useQueryState'
import { CenterEditor } from './cockpit/CenterEditor'
import { FilesRail } from './cockpit/files/FilesRail'
import { StageTasks } from './cockpit/tasks/StageTasks'
import { resolveTouchedPath } from './cockpit/cockpitShared'
import { missingCommandNotices } from './cockpit/cockpitCommands'
import { ProjectFooter } from './cockpit/footer/ProjectFooter'
import { CollapsiblePanel } from './cockpit/CollapsiblePanel'
import { RightPanel } from './cockpit/RightPanel'
import { normalizeStageLabel } from './cockpit/useRightPanel'
import { CockpitMeta } from './cockpit/meta/CockpitMeta'
import { CockpitHeader } from './cockpit/header/CockpitHeader'
import { CockpitNotices } from './cockpit/header/CockpitNotices'
import { useCodeCockpit } from './cockpit/useCodeCockpit'
import { cockpitActions } from './cockpit/cockpitActions'

// The page's own tests still import these from it, where they used to live.
export { StageTasks, ProjectFooter, CockpitMeta, resolveTouchedPath, missingCommandNotices, normalizeStageLabel }

/** The Code cockpit — a mini-VSCode/Cursor execution view for one project:
 *   • LEFT   — per-stage task lists (the Tasks Project) + the workspace file tree.
 *   • CENTER — Monaco editor with tabs (reuses the Files page's FileViewer).
 *   • BOTTOM — an embedded terminal rooted at the workspace.
 *   • RIGHT  — the live agent activity + a steer box (nudge the autonomous worker).
 *  The autonomous worker runs the stage plan; this view lets the user watch, steer,
 *  and edit files alongside it. Live state rides the per-project SSE.
 *  Each region is a controller and its view under `cockpit/`; this page composes them around
 *  `useCodeCockpit`, the cockpit's state.
 */
export function CodeCockpitPage({ id, onBack, onDeleted, onNewTarget, onOpenProject, onStartNew, query, setQuery }: {
  id: string
  onBack: () => void
  onDeleted: () => void
  /** Start a NEW target reusing this project's workspace + context. Receives the
   *  bound workspace dir so the unified composer can pre-bind it (brownfield reuse). */
  onNewTarget?: (workspaceDir: string) => void
  /** Open the containing Project's detail page (Projects native entity). */
  onOpenProject?: (projectId: string) => void
  /** Start a brand-new project (finished-state CTA) — routes to the composer. */
  onStartNew?: () => void
} & Pick<RouteProps, 'query' | 'setQuery'>) {
  const {
    project, setProject, showTerm, setShowTerm, pendingRunCmd, setPendingRunCmd, pendingRunNonce, pickWs,
    setPickWs, acting, setActing, loadErr, load, wsMissing, runFlags, gateFail, stalled, toast, setToast,
    runtimes, runtimesLoaded, answerOnScreen, setAnswerOnScreen, answerWanted, setAnswerWanted, answerShown,
    tasksNonce, setTasksNonce, connected, activityBySession, refetchTasks,
  } = useCodeCockpit({ id, query, setQuery })
  if (project === null) {
    return <Shell title="Project" onBack={onBack}><Centered>
      {loadErr ? (
        <LoadError what="project" error={loadErr} onRetry={load} />
      ) : (
        <Loader2 size={22} className="animate-spin text-on-surface-low" />
      )}
    </Centered></Shell>
  }
  if (project === 'missing') {
    return <Shell title="Project" onBack={onBack}><Centered>
      <div className="flex max-w-[360px] flex-col items-center gap-3 px-6 text-center">
        <HelpCircle size={32} className="text-on-surface-low" />
        <div>
          <p data-type="title-m" className="text-on-surface">This project no longer exists</p>
          <p data-type="body-s" className="mt-1 text-on-surface-low">It may have been deleted, or the link is stale.</p>
        </div>
        <Button onClick={onBack}><ListChecks size={15} /> Back to projects</Button>
      </div>
    </Centered></Shell>
  }

  const {
    p, ws, fileRoot, active, missingCommands, runsOn, runtimeEditable, missingBuild, missingTest, act, moveRuntime,
    runInWorkspaceTerminal, pickWorkspace, del,
  } = cockpitActions({
    id, project, onDeleted, acting, setActing, setProject, setToast, setTasksNonce, load, setPickWs, setShowTerm,
    setPendingRunCmd, pendingRunNonce, runtimes, runtimesLoaded,
  })

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <CockpitHeader id={id} project={p} setProject={setProject} setToast={setToast} onOpenProject={onOpenProject}
        active={active} connected={connected} acting={acting} act={act} ws={ws} showTerm={showTerm} setShowTerm={setShowTerm}
        missingBuild={missingBuild} missingTest={missingTest} runInWorkspaceTerminal={runInWorkspaceTerminal}
        onNewTarget={onNewTarget} onBack={onBack} del={del} />

      <CockpitMeta project={p} onOpenProject={onOpenProject} onRuntimeChange={runtimeEditable ? moveRuntime : undefined} />

      {/* Expandable prompt bar — first line collapsed, full on expand. */}
      <CockpitPromptBar prompt={p.task || ''} />

      <CockpitNotices project={p} ws={ws} wsMissing={wsMissing} missingCommands={missingCommands} runsOn={runsOn}
        runtimeEditable={runtimeEditable} runFlags={runFlags} setPickWs={setPickWs} />

      {/* body: Files (left) | Editor+tabs (center) | Tasks (right). Both side
          panels collapsible + resizable (useResizablePanel persists each). */}
      <div className="flex min-h-0 flex-1">
        <CollapsiblePanel side="left" panelKey="code-left" def={300} min={200} max={460}
          icon={FolderTree} label="Files">
          <FilesRail ws={fileRoot} isProjectDir={!ws} running={active} merges={p.merges} query={query} setQuery={setQuery} />
        </CollapsiblePanel>
        <CenterEditor ws={fileRoot} showTerm={showTerm} onCloseTerm={() => setShowTerm(false)} running={active} runCmd={pendingRunCmd}
          query={query} setQuery={setQuery} />
        <CollapsiblePanel side="right" panelKey="code-right" def={340} min={260} max={520}
          icon={ListChecks} label="Tasks">
          <RightPanel project={p} onTasksChanged={refetchTasks} tasksNonce={tasksNonce}
            activityBySession={activityBySession} gateFail={gateFail} stalled={stalled} onNudged={load} onStartNew={onStartNew}
            onAnswerOnScreen={setAnswerOnScreen} answerWanted={answerWanted} onAnswerShown={answerShown}
            query={query} setQuery={setQuery} />
        </CollapsiblePanel>
      </div>

      {pickWs && (
        <WorkspacePicker mode="brownfield" onClose={() => setPickWs(false)} onPick={pickWorkspace} />
      )}
      <AnimatePresence>
        <CockpitToast toast={toast} answerOnScreen={answerOnScreen} question={p.pending_question?.question}
          onDismiss={() => setToast(null)}
          onRespond={() => {
            setToast(null)
            setAnswerWanted(true)
            window.dispatchEvent(new CustomEvent('ne:code-expand-panel', { detail: 'code-right' }))
          }} />
      </AnimatePresence>
    </div>
  )
}

// ── shells ──

function Shell({ title, onBack, children }: { title: string; onBack: () => void; children: React.ReactNode }) {
  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <TopBar left={<div className="flex items-center gap-2"><Code2 size={18} className="text-primary" /><span data-type="title-l" className="text-on-surface">{title}</span></div>}
        right={<HeaderActions><HeaderControl icon={ListChecks} label="All projects" onClick={onBack} /></HeaderActions>} />
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  )
}

// re-export for the section's type usage
export type { CodeStage }
