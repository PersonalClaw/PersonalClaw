import { Folder, Repeat, Clock } from 'lucide-react'
import { type CodeProject } from '../../../../lib/api'
import { loopStatusLabel, loopStatusTone } from '../../../../lib/loopStatus'
import { LoopRunsOn } from '../../../loop/LoopRuntimePill'
import { type LoopRuntime } from '../../../loop/loopRuntime'
import { withArticle } from '../../../../lib/article'
import { ProjectChip } from '../../../../ui/ProjectChip'
import { StageTrail } from './StageTrail'
import { useCockpitMeta } from './useCockpitMeta'

// ── The cockpit's meta strip ───────────────────────────────────────────────────────────────────
//
// The strip under the cockpit's title: the run's status, its elapsed time, the stage trail, what
// it runs on, and its workspace, Project and cycles. `useCockpitMeta` keeps what it knows.

/** A compact meta strip under the header: workspace path · cycles run · elapsed.
 *  At-a-glance "where / how much work / how long" for the build the user watches.
 *  Renders only what it has; hidden entirely for a bare draft with none of it. */
/** The cockpit's status strip. Exported for `cockpitNamesItsStatus.test.tsx`: what broke in #671
 *  is which facts this strip states, and asserting that on the rendered component beats asserting
 *  it on the 3400-line page's source — mounting the whole cockpit would need the chat socket, the
 *  run stream and a dozen endpoints, and would test the mocks. */
export function CockpitMeta({ project: p, onRuntimeChange }: {
  project: CodeProject
  /** Given while the project's spec can still change: "Runs on" is then the picker. */
  onRuntimeChange?: (rt: LoopRuntime) => void
}) {
  const {
    ws, cycles, projId, projName, wsBase, cap, cyclesText, elapsedText, dispStatus,
  } = useCockpitMeta({ project: p })
  return (
    <div data-type="caption" className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-outline-variant/40 bg-surface-low/30 px-l py-1 text-on-surface-low">
      {/* Status leads: it is the one line always true about a run, and the answer to the question
          the rest of the strip qualifies. */}
      <span className="shrink-0 rounded-pill px-2 py-0.5" style={loopStatusTone(dispStatus)}>
        {loopStatusLabel(dispStatus)}
      </span>
      {/* Elapsed next — "how long has this been running" is the first thing to read after the
          state itself. */}
      {elapsedText && (
        <span className="inline-flex shrink-0 items-center gap-1" title="Elapsed run time">
          <Clock size={11} className="shrink-0 opacity-70" />
          <span className="font-mono">{elapsedText}</span>
        </span>
      )}
      {/* Phase-execution status — moved here from beside the title so the
          status bar is the single place to read where the run is. */}
      <StageTrail project={p} />
      {/* Runs on · workspace · project · cycles float to the far RIGHT edge (ml-auto pushes them).
          "Runs on" is always said, since PersonalClaw and an agent CLI are different programs doing
          her work. Where the strip is too narrow for its parts, the row wraps rather than running
          past its edge: the project and the cycle count go to the next line together, and the
          chip, cut to what that line leaves with the whole name in its tooltip, is the one part that
          shrinks. */}
      <span className="ml-auto inline-flex min-w-0 max-w-full items-center gap-3">
        <LoopRunsOn loop={p} onChange={onRuntimeChange} />
        {wsBase && (
          <span className="inline-flex min-w-0 items-center gap-1" title={ws}>
            <Folder size={11} className="shrink-0 opacity-70" />
            <span className="max-w-[220px] truncate font-mono">{wsBase}</span>
          </span>
        )}
      </span>
      <span className="inline-flex min-w-0 max-w-full items-center gap-3">
        {/* The strip's one way to the project: its chip, a link with a 44 px box on a phone. */}
        <ProjectChip id={projId} name={projName} />
        {cyclesText && (
          <span className="inline-flex shrink-0 items-center gap-1"
            title={cap > 0 ? `${cycles} of ${withArticle(`${cap}-cycle`)} budget run` : `${cycles} cycles run (uncapped)`}>
            <Repeat size={11} className="shrink-0 opacity-70" />
            <span className="font-mono">{cyclesText}</span>
          </span>
        )}
      </span>
      {/* The original task/prompt is NOT shown here — it has its own dedicated,
          expandable CockpitPromptBar ("Prompt") rendered immediately below this strip. */}
    </div>
  )
}
