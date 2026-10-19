import { useCallback, useEffect, useRef, useState } from 'react'
import { fvs, withWeight } from '../../design/fontWeight'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Code2, Play, Pause, Square, Trash2, Loader2, ListChecks, FolderTree,
  TerminalSquare, Send, CircleDot, CheckCircle2, Circle,
  ChevronLeft, Target, HelpCircle, Folder, Repeat, Clock, XCircle, AlertTriangle,
  PanelLeftClose, PanelRightClose, Plus, RotateCcw, FolderKanban,
} from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { Button } from '../../ui/Button'
import { SpendCapPause } from '../../ui/SpendCapPause'
import { NoToolsPause } from '../loops/NoToolsPause'
import { IconButton } from '../../ui/IconButton'
import { LoadError } from '../../ui/ListScaffold'
import { Centered } from '../../ui/Centered'
import { confirmDelete } from '../../ui/dialog'
import { confirmStopLoop } from '../loop/stopLoop'
import { api, type CodeProject, type CodeStage, type CodeFinding, type TaskItem, type Loop } from '../../lib/api'
import { useQuery } from '../../lib/data'
import { refreshKinds, useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { cleanSay, toolDetail } from '../../lib/agentFeed'
import { isStepPhase, phaseFeedLine } from '../../lib/workPhase'
import {
  LOOP_ACTION_SOURCE_STATUSES,
  effectiveLoopStatus,
  loopStatusLabel,
  loopStatusTone,
} from '../../lib/loopStatus'
import { useRunStream } from '../loops/useRunStream'
import { belongsToLoop } from '../workflows/containerKey'
import { foldReducer, emptyRunFlags, type RunFlags, type StallInfo } from '../loops/runFold'
import { WorkspacePicker } from './WorkspacePicker'
import { useWorkspaceMissing } from '../../lib/useWorkspaceMissing'
import { useRuntimeGroups } from '../../lib/agents'
import { LoopRunsOn } from '../loop/LoopRuntimePill'
import { runtimeFields, runtimeOf, showRuntime, type LoopRuntime } from '../loop/loopRuntime'
import { withArticle } from '../../lib/article'
import { codeDeleteBody } from './codeMeta'
import { KeptWork } from './KeptWork'
import { StageGateVerdict } from './StageGateVerdict'
import { RepromptNotice } from '../loops/RepromptNotice'
import { useResizablePanel } from '../../ui/useResizablePanel'
import { CockpitPromptBar } from '../loops/CockpitPromptBar'
import { LoopApprovals } from '../loops/LoopApprovals'
import { MergeReview } from './MergeReview'
import { MergeConflict } from './MergeConflict'
import { CockpitToast, useOnScreenReport, type CodeToastState } from './CodeToast'
import { useQueryFlag, type RouteProps } from '../../app/useQueryState'
import { messageEnter } from '../../design/motion'
import { BUSY_REASON } from '../../ui/unavailable'
import { CenterEditor } from './cockpit/CenterEditor'
import { PROJECT_DIR_HIDDEN, PROJECT_DIR_HIDDEN_PREFIXES } from './cockpit/files/WorkspaceTree'
import { FilesRail } from './cockpit/files/FilesRail'
import { isTerminalTask } from './cockpit/tasks/taskState'
import { TaskDetailView } from './cockpit/tasks/TaskDetailView'
import { StageTasks } from './cockpit/tasks/StageTasks'
import { STEERABLE, TERMINAL_STATUSES, stageKey, autoGrowTextarea, type ActivityItem, resolveTouchedPath } from './cockpit/cockpitShared'
import { FilesTouched } from './cockpit/FilesTouched'

// The page's own tests still import these from it, where they used to live.
export { StageTasks, resolveTouchedPath }

/** Strip a model-authored stage decoration — '1 — ', 'Stage 2/2 — ', '3: ' — down to
 *  the bare title, case-folded (issue 642). Mirrors the ingest-side canonicalizer in
 *  loop/files.py (_STAGE_DECOR_RE): the ingest stamps NEW findings with the plan's
 *  canonical stage; this normalize keeps already-ledgered legacy labels attributable. */
export function normalizeStageLabel(s: string): string {
  return s.replace(/^\s*(stage\s*)?\d+(\s*\/\s*\d+)?\s*[—:\-–]\s*/i, '').trim().toLowerCase()
}

// The Code cockpit is a code-shaped view-model over the unified Loop (kind=code).
// The wire format is the unified Loop (entry_stage/project_kind/verify_command/
// test_command/queued_task_ids under kind_config; the stage list as `plan`, per-stage
// status as `phase_status`); adapt it to the CodeProject shape the cockpit's many
// sub-components already render, so the migration is one boundary, not 2700 lines.
function loopToCodeProject(p: Loop): CodeProject {
  const kc = (p.kind_config || {}) as Record<string, unknown>
  return {
    ...(p as unknown as CodeProject),
    entry_stage: (kc.entry_stage as CodeProject['entry_stage']) ?? 'ideation',
    project_kind: (kc.project_kind as CodeProject['project_kind']) ?? 'greenfield',
    verify_command: String(kc.verify_command ?? ''),
    test_command: String(kc.test_command ?? ''),
    queued_task_ids: Array.isArray(kc.queued_task_ids) ? (kc.queued_task_ids as string[]) : [],
    stage_plan: (p.plan ?? []) as unknown as CodeStage[],
    stage_status: (p.phase_status ?? {}) as Record<string, string>,
    command_runnability: p.command_runnability,
  }
}

// The shared chrome for this page's full-width warn strips (missing command, brownfield
// needs a workspace, workspace vanished). One object, so the tint lives in ONE place
// instead of being re-typed per banner — the status-tint ratchet counts each spelling.
const WARN_STRIP: React.CSSProperties = {
  background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)',
  color: 'var(--color-warn)',
}

// Truncate a build/test command for an overflow-menu label. A blunt slice(0,48) on
// the WHOLE "Run build (<cmd>)" string cut the command mid-word and dropped the
// closing paren (e.g. "Run build (python -m pytest --cov=src --c") — reads as broken.
// Cap just the command + add an ellipsis so the wrapper "(…)" always stays intact.
const cmdLabel = (cmd: string, max = 36): string => {
  const c = (cmd || '').trim()
  return c.length > max ? `${c.slice(0, max - 1)}…` : c
}

export interface MissingCommandNotice {
  key: 'verify_command' | 'test_command'
  label: 'build' | 'test'
  command: string
  binary: string
}

/** Missing-binary diagnostics only. A missing project manifest is a stage-timing
 *  state, not a dead cockpit command, so it must not disable the user's run control. */
export function missingCommandNotices(
  checks: CodeProject['command_runnability'],
): MissingCommandNotice[] {
  const out: MissingCommandNotice[] = []
  for (const [key, label] of [
    ['verify_command', 'build'],
    ['test_command', 'test'],
  ] as const) {
    const check = checks?.[key]
    if (check?.runnable === false && check.reason === 'binary_not_on_path' && check.binary) {
      out.push({ key, label, command: check.command, binary: check.binary })
    }
  }
  return out
}

/** The Code cockpit — a mini-VSCode/Cursor execution view for one project:
 *   • LEFT   — per-stage task lists (the Tasks Project) + the workspace file tree.
 *   • CENTER — Monaco editor with tabs (reuses the Files page's FileViewer).
 *   • BOTTOM — an embedded terminal rooted at the workspace.
 *   • RIGHT  — the live agent activity + a steer box (nudge the autonomous worker).
 *  The autonomous worker runs the stage plan; this view lets the user watch, steer,
 *  and edit files alongside it. Live state rides the per-project SSE.
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
  const [project, setProject] = useState<CodeProject | null | 'missing'>(null)
  // The bottom terminal drawer rides the URL (?term=1, push → Back closes it;
  // refresh/deep-link reopens it beside the editor).
  const [showTerm, setShowTerm] = useQueryFlag(query, setQuery, 'term')
  // A build/test command to run in the cockpit terminal once it's live (set by the
  // Run build/tests action). The nonce lets the same command re-fire on a repeat click.
  const [pendingRunCmd, setPendingRunCmd] = useState<{ cmd: string; n: number } | null>(null)
  const pendingRunNonce = useRef(0)
  const [pickWs, setPickWs] = useState(false)
  // In-flight guard for run controls (start/pause/resume/stop) — without it a rapid
  // double-click fires two codeAction calls, racing the worker spawn/teardown.
  const [acting, setActing] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  // Set by Escape so the blur that fires as the input unmounts skips the save.
  const cancelRename = useRef(false)
  // Guards the double commitRename (Enter → unmount → onBlur) from a double PUT.
  const renameInFlight = useRef(false)

  // Tracks an initial-load failure (transient 5xx/network on the FIRST fetch, before
  // any snapshot). Without it a failed first load left `project` null forever — an
  // eternal spinner with no recovery, since SSE only updates an already-loaded view.
  const [loadErr, setLoadErr] = useState<Error | null>(null)
  const load = useCallback(() => {
    api.uLoop(id).then((proj) => { setProject(loopToCodeProject(proj)); setLoadErr(null) }).catch((e) => {
      // 404 (gone/deleted) OR 400 (malformed id — a truncated/garbled link, which the
      // backend rejects as an invalid project id) are both PERMANENT "no such project"
      // conditions → show the missing state. Neither recovers on retry, so leaving
      // `project` null (a forever loader / blank shell) would strand the user. A
      // transient blip / 5xx is NOT permanent — keep the current view + let the next
      // poll / SSE event recover.
      const status = (e as { status?: number })?.status
      if (status === 404 || status === 400) setProject('missing')
      else setLoadErr(new Error((e as Error).message || 'Could not load this project'))
    })
  }, [id])
  useEffect(() => { load() }, [load])
  // Auto-retry while the FIRST load hasn't landed (project still null) and the last
  // attempt errored — so a transient blip recovers on its own without a manual Retry.
  useVisiblePoll(() => load(), project === null && loadErr ? 4000 : null)
  // A brownfield project whose bound workspace was moved/deleted on disk after binding surfaces
  // a proactive "re-pick the folder" banner instead of only erroring when the user clicks Start
  // against a vanished dir. Only for a not-running brownfield with a path set —
  // a running worker already proves the dir exists — and only on a 404, which the shared hook
  // keys on the STATUS: it used to match the error text "no such directory".
  const probed = project && project !== 'missing' ? project : null
  const wsMissing = useWorkspaceMissing(probed?.workspace_dir, {
    enabled: !!probed && probed.project_kind === 'brownfield' && probed.status !== 'running',
    recheck: project,
  })

  // Instant-paint seed: re-opening a project should show its last snapshot
  // immediately instead of a cold spinner. The authoritative mount fetch above +
  // the live SSE still own all subsequent state — this only fills the FIRST paint
  // while `project` is still null. In-memory only (persist:false): the SSE/poll
  // re-pulls fresh on mount anyway, so we never want a stale snapshot across reload.
  const { data: cachedProject } = useQuery(`code:project:${id}`, () => api.uLoop(id).then(loopToCodeProject).catch(() => null), { persist: false })
  useEffect(() => {
    if (project === null && cachedProject) setProject(cachedProject)
  }, [cachedProject, project])

  // A failed supervisor gate check (build/lint/test the supervisor ran itself) —
  // the latest one, so the user sees WHY a stage isn't advancing instead of silent
  // re-cycling. Cleared when the project advances a stage / completes.
  // gate-failure + stall banners are TRANSIENT run flags folded from lifecycle events
  // by the shared pure `foldReducer` (one fold for every run surface; Code Cockpit
  // no longer hand-maintains its own inline gate/stall switch). The stall banner surfaces
  // when a stage keeps producing findings but its gate won't pass. `runFold.test.ts` locks
  // the parity fixes (gate-clears-on-pass, stall-kept-on-blocked, judge degraded).
  const [runFlags, setRunFlags] = useState<RunFlags>(emptyRunFlags)
  const gateFail = runFlags.gate
  const stalled = runFlags.stall
  // A prominent toast when the worker needs the user — a question (attended) or a
  // merge conflict — dismissable, with a jump-to-answer action. It stands only while the Tasks
  // rail's answer panel is off screen (`CodeToast`): over the panel, it covered the very
  // buttons it pointed at.
  const [toast, setToast] = useState<CodeToastState | null>(null)
  // The agent CLIs set up here, to say when the one this project runs on can't run it now.
  const { groups: runtimes, loaded: runtimesLoaded } = useRuntimeGroups()
  const [answerOnScreen, setAnswerOnScreen] = useState(false)
  // The toast's Respond: bring the rail's answer panel on screen (it may be collapsed, or a
  // task's detail open in its place) and put the cursor in its steer box. Held until the panel
  // has done it, so a panel that mounts after the click still does.
  const [answerWanted, setAnswerWanted] = useState(false)
  const answerShown = useCallback(() => setAnswerWanted(false), [])
  // Bumped on each lifecycle event (+ a slow poll while running) so the task rail
  // re-fetches — the worker marks tasks in_progress/done and creates new ones
  // mid-cycle, which the once-on-mount fetch would otherwise miss (stale rail).
  const [tasksNonce, setTasksNonce] = useState(0)

  // "Follow the worker": auto-open the file the worker most recently touched, so
  // the center editor tracks the code as it's written (mini-Cursor live view).
  // Stays on until the user manually opens something (their click wins); they can
  // re-engage from the editor's empty state. A ref so the SSE handler reads the
  // live value without re-subscribing.
  const followRef = useRef(true)

  // Live updates: snapshot replaces the project; lifecycle events refetch. A
  // gate_check is transient (not persisted) — record a failure, don't refetch;
  // any real lifecycle change clears a stale gate failure + refetches.
  const { connected } = useRunStream(id, project !== 'missing', {
    onSnapshot: (p) => setProject(loopToCodeProject(p)),
    onLifecycle: (event, data) => {
      // The transient gate/stall/judge banners fold through the ONE shared pure reducer
      // — gate_check clear-on-pass, stall-kept-on-blocked, judge degraded, etc. all
      // live in runFold.ts (parity-locked by runFold.test.ts), not a second inline switch.
      setRunFlags((f) => foldReducer(f, event, data))
      // The project was deleted elsewhere (another tab / the list) → flip to the
      // missing state now instead of showing it stale until the user hits a 404.
      if (event === 'deleted') { setProject('missing'); return }
      // gate_check / stage_stalled are pure fold events (handled above) with one bit of
      // Code-Cockpit-specific chrome: a merge-conflict gate needs a prominent toast.
      if (event === 'gate_check') {
        const d = (data ?? {}) as { ok?: boolean; label?: string; output?: string }
        if (d.ok === false && d.label === 'merge') setToast({ kind: 'conflict', text: d.output || 'A merge conflict needs your attention.' })
        // A stage's exit criteria were judged: the per-criterion verdict is on the loop's ledger.
        if (d.label === 'exit criteria') load()
        return
      }
      if (event === 'stage_stalled' || event === 'reprompt') return
      // A task finished (merged) → drop its per-task activity bucket. Buckets are
      // otherwise only cleared on a clean chat_done; a task-worker reaped on merge
      // doesn't emit one, so without this the bucket lingers for the whole cockpit
      // session — unbounded growth across a long parallel run. (The detail view +
      // list pulse already ignore a terminal task's stale bucket; this frees it.)
      if (event === 'task_done') {
        const tid = (data as { task_id?: string } | null)?.task_id
        if (tid) setActivityBySession((m0) => {
          const k = `loop-${id}-${tid}`
          if (!(k in m0)) return m0
          const { [k]: _drop, ...rest } = m0
          return rest
        })
        // fall through: a task_done also advances the rail/queue (load + tasksNonce)
      }
      // The worker paused for the user (attended question / conflict) → toast it.
      // The precise question lives in the rail's answer panel, and the toast repeats it from
      // the project as the panel shows it. (load() below refreshes pending_question.)
      if (event === 'needs_input') {
        setToast({ kind: 'question', text: '' })
      } else {
        setToast(null)  // any forward progress dismisses a stale toast
      }
      load()
      setTasksNonce((n) => n + 1)  // refresh the task rail on a new finding / stage advance
      // On a new finding, follow the worker to its latest touched file (in-ws only).
      if (event === 'new_finding' && followRef.current) {
        const f = (data as { finding?: CodeFinding } | null)?.finding
        const wsRoot = (project !== 'missing' && (project?.workspace_dir || project?.files_dir) || '').replace(/\/$/, '')
        const isProjDir = project !== 'missing' && !project?.workspace_dir
        // Resolve each touched path (shared helper: worktree-remap + bare-relative +
        // outside-root rejection), then skip engine bookkeeping in project-dir mode so
        // follow-the-worker never auto-opens status.json / guidance_<id>.txt etc.
        const touched = (f?.files_touched ?? [])
          .map((raw) => resolveTouchedPath(raw, wsRoot))
          .filter((r): r is { abs: string; rel: string } => {
            if (!r) return false
            if (!isProjDir) return true
            const top = r.rel.split('/')[0]
            return !PROJECT_DIR_HIDDEN.has(top) && ![...PROJECT_DIR_HIDDEN_PREFIXES].some((pre) => top.startsWith(pre))
          })
          .map((r) => r.abs)
        const latest = touched[touched.length - 1]
        if (latest) window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name: latest.split('/').pop(), path: latest, is_dir: false, _follow: true } }))
      }
    },
  })
  // A MANUAL file/diff/commit open (not our auto-follow) means the user has taken
  // the wheel → stop following the worker. They can re-engage from the empty state.
  useEffect(() => {
    const onManual = (e: Event) => { if (!(e as CustomEvent).detail?._follow) followRef.current = false }
    // Editing a file (dirty draft) also pauses follow — don't yank the user off
    // their unsaved changes when the worker writes its next file.
    const onEditing = () => { followRef.current = false }
    const onReFollow = () => { followRef.current = true }
    // CenterEditor lives below this component (no toast access of its own), so it
    // dispatches ne:code-toast for cross-cutting feedback like the artifact-save outcome.
    const onToast = (e: Event) => {
      const d = (e as CustomEvent).detail as { kind?: 'ok' | 'error'; text?: string } | null
      if (d?.text) setToast({ kind: d.kind === 'error' ? 'error' : 'ok', text: d.text })
    }
    window.addEventListener('ne:code-open-file', onManual as EventListener)
    window.addEventListener('ne:code-open-diff', onManual as EventListener)
    window.addEventListener('ne:code-open-commit', onManual as EventListener)
    window.addEventListener('ne:code-editing', onEditing as EventListener)
    window.addEventListener('ne:code-follow-worker', onReFollow as EventListener)
    window.addEventListener('ne:code-toast', onToast as EventListener)
    return () => {
      window.removeEventListener('ne:code-open-file', onManual as EventListener)
      window.removeEventListener('ne:code-open-diff', onManual as EventListener)
      window.removeEventListener('ne:code-open-commit', onManual as EventListener)
      window.removeEventListener('ne:code-editing', onEditing as EventListener)
      window.removeEventListener('ne:code-follow-worker', onReFollow as EventListener)
      window.removeEventListener('ne:code-toast', onToast as EventListener)
    }
  }, [])

  // ⌘P / Ctrl-P → quick-open the find-file box (mini-IDE convention; the editor binds
  // ⌘S/⌘W). Bound at COCKPIT level (always mounted) so it works even when the Files
  // panel is collapsed — it expands that panel (ne:code-expand-panel), then FilesRail
  // switches to the Files tab + FileFinder focuses (both on ne:code-focus-find). Harmless
  // for a no-workspace project (no finder renders → focus-find no-ops). preventDefault
  // the browser print dialog (no use in a code workspace).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'p') {
        // Don't hijack Ctrl-P while the embedded TERMINAL is focused — in a shell that's
        // readline "previous command", and quick-open stealing it would break history
        // nav (⌘P is safe on macOS, but Ctrl-P collides on Linux/Windows). xterm renders
        // into a .xterm container; if focus is inside one, let the shell have the key.
        if ((document.activeElement as HTMLElement | null)?.closest('.xterm')) return
        e.preventDefault()
        window.dispatchEvent(new CustomEvent('ne:code-expand-panel', { detail: 'code-left' }))
        setTimeout(() => window.dispatchEvent(new CustomEvent('ne:code-focus-find')), 0)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // While running, the worker updates its TaskList between SSE lifecycle events
  // (which fire only on a written finding) — a slow poll keeps the rail honest. Every
  // 3rd tick (~24s) also refetch the PROJECT itself as an SSE-drop safety net: if the
  // per-loop EventSource silently fails (gateway restart, proxy idle-timeout) the status
  // pill / findings / banners would otherwise freeze with only the task rail live — this
  // self-heals the project state without waiting for a user action to trigger load().
  const isRunning = project !== null && project !== 'missing' && project.status === 'running'
  const pollTick = useRef(0)
  useVisiblePoll(() => {
    setTasksNonce((n) => n + 1)
    if (++pollTick.current % 3 === 0) load()
  }, isRunning ? 8000 : null)

  // Live worker activity — the hidden worker session (loop-<id>) broadcasts
  // work_phase / tool_call / activity_event over the shared WS as it works each
  // cycle. Surface them so a running cycle reads as ALIVE (tool calls, thinking),
  // not a frozen "starting…" between finding writes. Capped, cleared on new finding.
  // Activity is keyed BY SESSION so each task's detail view shows only its own
  // worker's events. The main worker is `loop-<id>` (sequential); parallel task-
  // workers are `loop-<id>-<taskid>` (the unified manager session keys — NOT the old
  // code-<id>, which silently matched nothing post-cutover so the panel stayed dead).
  // We bucket per session and slice the right one per task. Other sessions ignored.
  const [activityBySession, setActivityBySession] = useState<Record<string, ActivityItem[]>>({})
  // The phase each worker last said: the gateway says a running turn's phase again every fifteen
  // seconds, and a phase said again is not a new line in its feed.
  const saidBySession = useRef<Record<string, string>>({})
  const workerKey = `loop-${id}`
  const onWs = useCallback((m: WsMessage) => {
    // The incident switch moved: re-read the project, whose `held` says whether it is held.
    if (refreshKinds(m).includes('incident')) { load(); return }
    const sess = String(m.data?.session ?? '')
    // Accept the main worker, this project's task-workers, AND a coexistence run-scoped
    // key (`run:<id>` / `workflow:run:<id>`) once a code loop runs as a template.
    // `belongsToLoop` matches all three; a raw `===`/prefix test dropped the
    // colon keys silently (stream connects, panel stays dead), which is the regression it
    // closes. Kept `sess` for the per-worker activity buckets below.
    if (!belongsToLoop(sess, id)) return
    const push = (item: ActivityItem) => setActivityBySession((m0) => ({ ...m0, [sess]: [...(m0[sess] ?? []), item].slice(-50) }))
    // A status line said again in a row (an activity note repeated) carries
    // no new info — appending each one fills the 50-item buffer with identical noise and
    // EVICTS the real tool calls / narration. Collapse a consecutive duplicate status
    // into the existing trailing one (no-op append) instead of stacking it.
    const pushStatus = (label: string) => setActivityBySession((m0) => {
      const a = m0[sess] ?? []
      const last = a[a.length - 1]
      if (last && last.kind === 'status' && last.label === label) return m0  // dup → skip
      return { ...m0, [sess]: [...a, { kind: 'status' as const, label }].slice(-50) }
    })
    if (m.type === 'work_phase') {
      // The worker's phase, as the gateway measured it, while its turn runs. A step's own
      // phases are its tool card and its writing is the say line, so the feed says the rest
      // ("Thinking" as the turn starts, "Needs your OK: …"), once per change; the thinking
      // said after each step's result adds no row after its card.
      const next = phaseFeedLine(m.data, saidBySession.current[sess] ?? '', (p) => isStepPhase(p) || p === 'streaming')
      saidBySession.current[sess] = next.said
      if (next.line) pushStatus(next.line)
    } else if (m.type === 'tool_call') {
      // Legible chip (the telltale arg field), not the raw JSON blob — shared with
      // the planning feed via toolDetail (see lib/agentFeed).
      const detail = toolDetail(String(m.data.input_preview ?? ''), String(m.data.purpose ?? ''))
      push({ kind: 'tool', label: String(m.data.tool ?? 'tool'), detail })
    } else if (m.type === 'chat_chunk') {
      // Coalesce consecutive chunks into the trailing 'say' line (typing effect),
      // accumulating on un-sanitized rawLabel so a tag/fence split across chunks
      // still sanitizes once whole; cleanSay strips markup/code-fences for display.
      const piece = String(m.data.content ?? '')
      if (piece) setActivityBySession((m0) => {
        const a = m0[sess] ?? []
        const last = a[a.length - 1]
        if (last && last.kind === 'say') {
          const raw = ((last.rawLabel ?? last.label) + piece).slice(-4000)
          return { ...m0, [sess]: [...a.slice(0, -1), { ...last, rawLabel: raw, label: cleanSay(raw) }] }
        }
        return { ...m0, [sess]: [...a, { kind: 'say' as const, label: cleanSay(piece), rawLabel: piece }].slice(-50) }
      })
    } else if (m.type === 'activity_event') {
      const text = String(m.data.text ?? ''); if (text) pushStatus(text)
    } else if (m.type === 'chat_done') {
      setActivityBySession((m0) => ({ ...m0, [sess]: [] }))  // that worker's cycle ended
    }
  }, [workerKey, load])
  useChatSocket(onWs, null)  // a live ticker: nothing replays it, and the project is polled above

  // After a queue/unqueue mutation, refetch the project (queued_task_ids) + bump
  // the task rail so the new queued/ready/blocked states render immediately.
  const refetchTasks = useCallback(() => { load(); setTasksNonce((n) => n + 1) }, [load])

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

  const p = project
  const ws = p.workspace_dir || ''
  // The root the file surfaces (tree / editor / follow-worker) display. Falls back
  // to the project's own files dir when no workspace is bound, so doc deliverables
  // (requirements.md, design.md from idea/requirements/design projects) are still
  // viewable. `ws` stays workspace-only — it gates the brownfield start picker.
  const fileRoot = ws || p.files_dir || ''
  const active = p.status === 'running'
  const missingCommands = missingCommandNotices(p.command_runnability)
  const runsOn = showRuntime(runtimeOf(p), runtimesLoaded ? runtimes : undefined)
  // What it runs on is part of the spec, so it can change until the project starts.
  const runtimeEditable = LOOP_ACTION_SOURCE_STATUSES.start.has(p.status)
  const missingBuild = missingCommands.find((notice) => notice.key === 'verify_command')
  const missingTest = missingCommands.find((notice) => notice.key === 'test_command')

  async function act(action: 'start' | 'pause' | 'resume' | 'stop') {
    if (acting) return  // ignore a double-click while a prior action is in flight
    // A brownfield draft saved without a workspace can't start until one is set —
    // surface the picker instead of a silent 422 (resumes the draft flow).
    if (action === 'start' && p.project_kind === 'brownfield' && !ws) { setPickWs(true); return }
    // Surface a failed action (e.g. start re-validation 422 — workspace/agent went
    // away, spec invalid) instead of silently reverting via load(). The backend's
    // error message is the actionable reason; without this the user clicks Start and
    // nothing visibly happens.
    setActing(true)
    try {
      setProject(loopToCodeProject(await api.uLoopAction(id, action))); setToast(null)
      // The action answers once the run has ended (a Stop puts a task its worker held back to
      // open), which is after the lifecycle event the rail refreshed on: read the tasks again.
      setTasksNonce((n) => n + 1)
    }
    catch (e) {
      setToast({ kind: 'error', text: `Couldn't ${action} this project: ${(e as Error).message || 'unknown error'}` })
      load()
    } finally { setActing(false) }
  }
  // Move a project that hasn't started onto another runtime — the recourse when the one it was
  // created on isn't ready. Reported, never swallowed: the chip would otherwise show a runtime the
  // project is not on.
  async function moveRuntime(rt: LoopRuntime) {
    try { setProject(loopToCodeProject(await api.updateULoop(id, runtimeFields(rt)))); setToast(null) }
    catch (e) { setToast({ kind: 'error', text: `Couldn't change what this project runs on: ${(e as Error).message || 'unknown error'}` }) }
  }
  // Run the project's configured build/test command in the cockpit terminal — the
  // command the user already set (+ the supervisor gates on) shouldn't have to be
  // re-typed. Opens the terminal if hidden + hands the command to the BottomTerminal,
  // which runs it on ITS OWN session once the PTY is live — never racing the global
  // "active" terminal (which could be an unrelated one on another page). Stays the
  // user's to read/interact with afterward.
  function runInWorkspaceTerminal(command: string) {
    const cmd = (command || '').trim()
    if (!cmd) return
    setShowTerm(true)
    setPendingRunCmd({ cmd, n: pendingRunNonce.current++ })  // nonce so re-running the SAME cmd re-fires
  }
  // Set the workspace on a ready brownfield draft, then start it. Reached ONLY from
  // pre-run states (the ready-draft "Choose folder" + the ready/review re-pick banner)
  // — a started project's spec is frozen, so workspace_dir can't change there — which
  // is why 'start' (not 'resume') is always the right follow-up action. Surface a
  // failure (bad dir, re-validation 422, agent gone) via the same toast as act().
  async function pickWorkspace(dir: string) {
    if (acting) return
    setPickWs(false)
    // Same worker-spawn path as act('start') → share the in-flight guard so the run
    // button is disabled during the bind+start and a re-entry can't double-spawn.
    setActing(true)
    try {
      await api.updateULoop(id, { workspace_dir: dir })
      setProject(loopToCodeProject(await api.uLoopAction(id, 'start'))); setToast(null)
    } catch (e) {
      setToast({ kind: 'error', text: `Couldn't start with that folder: ${(e as Error).message || 'unknown error'}` })
      load()
    } finally { setActing(false) }
  }
  async function del() {
    // Through the shared delete ritual: confirmDelete composes the identical
    // title/danger/label, so hand-rolling confirm() here was drift, not a design choice.
    //
    // 🔴 THE BODY WAS DUPLICATED FROM `CodeSection`, AND THE DUPLICATION IS WHY THE DEFECT SURVIVED.
    // The comment that used to sit here reasoned about `store.delete`'s rmtree — accurately, about the
    // wrong function — and so did its twin. Neither read `teardown_for_delete`, which runs FIRST and
    // force-deletes `pclaw/task-*` branches in the user's repository. `codeMeta.codeDeleteBody` owns
    // the sentence now, verified against the handler rather than the callee.
    // The dialog names the work an ended run kept unmerged, which the delete discards.
    const kept = p.workspace_dir ? await api.uLoopKeptWork(id).then((r) => r.kept, () => []) : []
    if (!(await confirmDelete('project', p.name, { body: codeDeleteBody(p, kept) }))) return
    // Only navigate away on a CONFIRMED delete — a swallowed failure used to call
    // onDeleted() regardless, so a failed delete (teardown error, 404, network) sent
    // the user back to a list where the "deleted" project was still present, with no
    // explanation. Surface the backend reason + stay put on failure.
    try { await api.deleteULoop(id); onDeleted() }
    catch (e) { setToast({ kind: 'error', text: `Couldn't delete this project: ${(e as Error).message || 'unknown error'}` }) }
  }
  // Rename works in ANY state — the name is pure metadata; the rest of the spec
  // freezes once started. updateCode with a name-only body routes to the rename
  // path server-side (PUT /api/code/{id} → store.rename), so a running or finished
  // project can still be retitled.
  function startRename() { cancelRename.current = false; setTitleDraft(p.name || ''); setEditingTitle(true) }
  function abortRename() { cancelRename.current = true; setEditingTitle(false) }
  async function commitRename() {
    setEditingTitle(false)
    if (cancelRename.current) { cancelRename.current = false; return }
    // Enter sets editingTitle=false, which unmounts the input → fires onBlur →
    // commitRename AGAIN. The 2nd call still sees the old p.name (setProject is
    // post-await), so the name-unchanged guard below doesn't catch it → a double PUT.
    // Guard re-entry while the rename is in flight.
    if (renameInFlight.current) return
    const name = titleDraft.trim()
    if (!name || name === (p.name || '')) return
    renameInFlight.current = true
    // Surface a failed rename instead of silently keeping the old name — a swallowed
    // catch left the user thinking the rename took when it didn't (network/5xx, or a
    // backend rejection). Toast the reason so the no-op isn't a mystery.
    try { setProject(loopToCodeProject(await api.updateULoop(id, { name }))) }
    catch (e) { setToast({ kind: 'error', text: `Couldn't rename: ${(e as Error).message || 'unknown error'}` }) }
    finally { renameInFlight.current = false }
  }

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
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

      <CockpitMeta project={p} onOpenProject={onOpenProject} onRuntimeChange={runtimeEditable ? moveRuntime : undefined} />

      {/* Expandable prompt bar — first line collapsed, full on expand. */}
      <CockpitPromptBar prompt={p.task || ''} />

      {missingCommands.length > 0 && (
        <motion.div variants={messageEnter} initial="initial" animate="animate"
          role="status" data-type="body-s"
          className="flex shrink-0 items-start gap-s border-b border-outline-variant/40 px-l py-s"
          style={WARN_STRIP}>
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>
            {missingCommands.map((notice, index) => (
              <span key={notice.key}>
                {index > 0 && ' '}
                The stored {notice.label} command <code>{notice.command}</code> cannot run here because <code>{notice.binary}</code> is not on PATH.
              </span>
            ))}{' '}
            Edit the stored {missingCommands.length === 1 ? 'command' : 'commands'} before resuming.
          </span>
        </motion.div>
      )}

      {/* The agent CLI this project runs on can't run it now: said where she is looking, with what
          she can do about it — before launch, choose another under Runs on. */}
      {runsOn.unavailable && !TERMINAL_STATUSES.has(p.status) && (
        <motion.div variants={messageEnter} initial="initial" animate="animate"
          role="status" data-type="body-s"
          className="flex shrink-0 items-start gap-s border-b border-outline-variant/40 px-l py-s"
          style={WARN_STRIP}>
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>
            {runsOn.unavailable}{' '}
            {runtimeEditable
              ? 'Choose another runtime under Runs on, or get it ready, then start it.'
              : 'Its workers run there, so their turns can fail until it is ready again.'}
          </span>
        </motion.div>
      )}

      {/* A worker asked again for its finding: why, and how many asks are left. */}
      <RepromptNotice reprompt={runFlags.reprompt} className="shrink-0 border-b border-outline-variant/40 px-l py-s" />

      {/* A ready brownfield draft can't start until a workspace is chosen — make
          that explicit + actionable instead of a silent failed Start. */}
      {p.status === 'ready' && p.project_kind === 'brownfield' && !ws && (
        <motion.div variants={messageEnter} initial="initial" animate="animate"
          data-type="body-s" className="flex shrink-0 items-center justify-between gap-2 border-b border-outline-variant/40 bg-warn/10 px-l py-2"
          style={WARN_STRIP}>
          <span>This brownfield project needs a workspace directory before it can start.</span>
          <Button variant="ghost" size="xs" onClick={() => setPickWs(true)} className="shrink-0">Choose folder</Button>
        </motion.div>
      )}
      {/* Bound workspace went missing on disk (moved/deleted after binding). A PRE-RUN
          draft (ready/review) can re-pick — its spec is still editable. A project that
          already STARTED (but is still mid-run/parked) has a frozen spec, so re-picking
          would 409: surface an honest "can't continue" message. Not shown while running
          (the worker proves the dir exists), and NOT shown for a TERMINAL project
          (complete/stopped): its work is finished + graduated, so a missing workspace
          afterward is expected — not an error, and there's no run left to "continue".
          Showing "Stop or Delete" on a completed project is contradictory + alarming. */}
      {ws && wsMissing && p.status !== 'running' && !TERMINAL_STATUSES.has(p.status) && (
        (p.status === 'ready' || p.status === 'review') ? (
          <motion.div variants={messageEnter} initial="initial" animate="animate"
            data-type="body-s" className="flex shrink-0 items-center justify-between gap-2 border-b border-outline-variant/40 px-l py-2"
            style={WARN_STRIP}>
            <span>The workspace folder <span className="font-mono">{ws.split('/').slice(-1)[0]}</span> no longer exists — re-pick it to continue.</span>
            <Button variant="ghost" size="xs" onClick={() => setPickWs(true)} className="shrink-0">Re-pick folder</Button>
          </motion.div>
        ) : (
          <motion.div variants={messageEnter} initial="initial" animate="animate"
            data-type="body-s" className="flex shrink-0 items-center gap-2 border-b border-outline-variant/40 px-l py-2"
            style={WARN_STRIP}>
            <span>The workspace folder <span className="font-mono">{ws.split('/').slice(-1)[0]}</span> no longer exists, so this run can't continue. Its files are gone — Stop or Delete the project, or restore the folder and reopen.</span>
          </motion.div>
        )
      )}

      {/* body: Files (left) | Editor+tabs (center) | Tasks (right). Both side
          panels collapsible + resizable (useResizablePanel persists each). */}
      <div className="flex min-h-0 flex-1">
        <CollapsiblePanel side="left" panelKey="code-left" def={300} min={200} max={460}
          icon={FolderTree} label="Files">
          <FilesRail ws={fileRoot} isProjectDir={!ws} running={active} merges={p.merges} />
        </CollapsiblePanel>
        <CenterEditor ws={fileRoot} showTerm={showTerm} onCloseTerm={() => setShowTerm(false)} running={active} runCmd={pendingRunCmd} />
        <CollapsiblePanel side="right" panelKey="code-right" def={340} min={260} max={520}
          icon={ListChecks} label="Tasks">
          <RightPanel project={p} onTasksChanged={refetchTasks} tasksNonce={tasksNonce}
            activityBySession={activityBySession} gateFail={gateFail} stalled={stalled} onNudged={load} onStartNew={onStartNew}
            onAnswerOnScreen={setAnswerOnScreen} answerWanted={answerWanted} onAnswerShown={answerShown} />
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

// ── header stage trail ──

function StageTrail({ project }: { project: CodeProject }) {
  const plan = project.stage_plan ?? []
  if (!plan.length) return null
  // While the project is RUNNING, the active stage's icon spins — an at-a-glance
  // "the worker is alive + cycling" cue (the header otherwise looks static).
  const running = project.status === 'running'
  // Pre-launch (ready/review/intake/planning), NO stage is "active" yet — work
  // hasn't begun, so highlighting stage 1 as active falsely implies the design
  // stage is underway before the user clicks Start. Treat the whole trail as
  // upcoming until the project has actually started.
  const started = !['ready', 'review', 'intake', 'planning'].includes(project.status)
  // The active stage = first not-done (matches the backend's active_stage_index),
  // so the header is right even if stage_status briefly lags a transition. -1 when
  // not started → every stage renders 'pending'.
  const activeIdx = started
    ? plan.findIndex((s) => (project.stage_status?.[stageKey(s)] ?? 'pending') !== 'done')
    : -1
  const allDone = started && activeIdx < 0
  const cur = allDone || !started ? null : plan[activeIdx]
  // The active stage's color must not imply healthy progress when the run is actually
  // parked. Only a genuinely RUNNING project's active stage gets the primary (in-flight)
  // color; an attention/halted state (blocked / needs_input / stagnant / failed / stopped)
  // renders it WARN so the trail agrees with the status pill + outcome banner instead of
  // showing a hopeful blue "active" while the worker waits on the user or has stopped.
  const ATTENTION = ['blocked', 'needs_input', 'stagnant', 'failed', 'stopped']
  const halted = ATTENTION.includes(project.status)
  return (
    <>
      {/* full trail on wide screens — min-w-0 + overflow-hidden so a long multi-stage
          plan can't push the project title out or overflow the header; each stage
          label truncates individually. */}
      <div className="ml-1 hidden min-w-0 items-center gap-1 overflow-hidden md:flex">
        {plan.map((s, i) => {
          const st = i < activeIdx || allDone ? 'done' : i === activeIdx ? 'active' : 'pending'
          // A halted (failed/stopped) project's active stage renders muted (not the
          // primary in-progress color) — it stopped here, it isn't working here.
          const color = st === 'done' ? 'var(--color-ok)'
            : st === 'active' ? (halted ? 'var(--color-warn)' : 'var(--color-primary)')
            : 'var(--color-on-surface-low)'
          const activeRunning = st === 'active' && running
          const Icon = st === 'done' ? CheckCircle2 : activeRunning ? Loader2 : st === 'active' ? CircleDot : Circle
          return (
            <span key={i} className="inline-flex min-w-0 items-center gap-1" title={`${s.title || s.stage} — ${st}`}>
              {i > 0 && <span className="shrink-0 text-on-surface-low/40">›</span>}
              <Icon size={12} className={`shrink-0 ${activeRunning ? 'animate-spin' : ''}`} style={{ color }} />
              <span data-type="caption" className="max-w-[7rem] truncate" style={{ color }}>{s.title || s.stage}</span>
            </span>
          )
        })}
      </div>
      {/* compact "stage N/M · title" — always visible (incl. narrow screens where
          the full trail is hidden), so stage context is never lost. Pre-launch it
          shows the plan size as upcoming rather than a misleading "stage 1 active". */}
      <span data-type="caption" className="ml-1 inline-flex items-center gap-1 md:hidden"
        style={{ color: allDone ? 'var(--color-ok)' : halted ? 'var(--color-warn)' : started ? 'var(--color-primary)' : 'var(--color-on-surface-low)' }}>
        {!started
          ? <><Circle size={12} /> {plan.length} stage{plan.length === 1 ? '' : 's'} planned</>
          : allDone
          ? <><CheckCircle2 size={12} /> all stages done</>
          : <>{running ? <Loader2 size={12} className="animate-spin" /> : <CircleDot size={12} />} stage {activeIdx + 1}/{plan.length} · {cur?.title || cur?.stage}</>}
      </span>
    </>
  )
}

/** A compact meta strip under the header: workspace path · cycles run · elapsed.
 *  At-a-glance "where / how much work / how long" for the build the user watches.
 *  Renders only what it has; hidden entirely for a bare draft with none of it. */
/** The cockpit's status strip. Exported for `cockpitNamesItsStatus.test.tsx`: what broke in #671
 *  is which facts this strip states, and asserting that on the rendered component beats asserting
 *  it on the 3400-line page's source — mounting the whole cockpit would need the chat socket, the
 *  run stream and a dozen endpoints, and would test the mocks. */
export function CockpitMeta({ project: p, onOpenProject, onRuntimeChange }: {
  project: CodeProject
  onOpenProject?: (projectId: string) => void
  /** Given while the project's spec can still change: "Runs on" is then the picker. */
  onRuntimeChange?: (rt: LoopRuntime) => void
}) {
  // The Folder chip represents the BOUND workspace only — NOT the engine files dir.
  // A no-workspace project's files_dir is internal bookkeeping (~/.personalclaw/loop/
  // <id>), so falling back to it rendered a folder chip labeled with the loop-id hash
  // (e.g. "da50a558") — a meaningless path that reads as a bug. No workspace → no chip.
  const ws = p.workspace_dir || ''
  const cycles = p.total_cycles || 0
  // Resolve the containing Project's name (the redacted code project carries only its
  // id) so the meta strip can show + link to it — the Code→Project direction, mirroring
  // the Projects page's →linked-work. Fetched once when the binding id is present.
  // tasks_project_id (the provisioned/containing project — set at launch, takes
  // precedence) OR project_id (an explicit pre-launch user scope, before provision runs).
  const projId = p.tasks_project_id || p.project_id || ''
  const [projName, setProjName] = useState('')
  useEffect(() => {
    if (!projId) { setProjName(''); return }
    let alive = true
    api.project(projId).then((pr) => { if (alive) setProjName(pr.name) }).catch(() => {})
    return () => { alive = false }
  }, [projId])
  // Tick every 30s WHILE RUNNING so the live elapsed clock actually advances. Without
  // this, liveStretch is recomputed only when `project` changes (an SSE event), which
  // can be minutes apart during one long worker cycle — the minute-granularity clock
  // would sit frozen then jump in chunks. Visible-only (pauses when the tab is hidden).
  const [, tick] = useState(0)
  useVisiblePoll(() => tick((n) => n + 1), p.status === 'running' ? 30000 : null)
  // elapsed = time banked from prior run stretches (elapsed_seconds, excludes
  // pauses) + the CURRENT stretch while running (server only banks it on
  // pause/stop, so add now − started_at live so a running build's clock ticks).
  const banked = p.elapsed_seconds || 0
  const liveStretch = p.status === 'running' && p.started_at ? Math.max(0, Date.now() / 1000 - p.started_at) : 0
  const elapsed = banked + liveStretch
  // Elapsed leads at the far LEFT; workspace · project · cycles all float to the far
  // RIGHT edge (a single ml-auto group), leaving the StageTrail alone in the middle.
  const wsBase = ws ? (ws.replace(/\/$/, '').split('/').pop() || ws) : ''
  // Show progress toward the cycle BUDGET when capped ("5 / 60 cycles") so the user
  // can gauge how close the run is to its ceiling; uncapped (max_cycles 0) stays a
  // bare count. Also render the cap pre-run (cycles 0) so the configured budget is
  // visible before launch, not only once cycles accrue.
  const cap = p.max_cycles || 0
  const cyclesText = (cycles > 0 || cap > 0)
    ? (cap > 0 ? `${cycles} / ${cap} cycles` : `${cycles} cycle${cycles === 1 ? '' : 's'}`)
    : ''
  const elapsedText = elapsed > 0 ? fmtDuration(elapsed) : ''
  // The project chip is interactive (deep-links to the Project), so it's rendered
  // separately from the plain info items. Show it whenever the work is bound.
  const showProj = !!(projId && projName)
  // The LIFECYCLE STATUS, in the project's own words from the one registry (#671). This strip
  // calls itself "the single place to read where the run is" and named everything except that:
  // elapsed, stage, workspace, project, cycles. On a loop the API reported as `blocked`, the word
  // "blocked" appeared NOWHERE on the page — the Code list shows a status pill (`CodeSection`'s
  // `statusPill`), so the state was legible until you opened the thing it described.
  //
  // Always rendered, including while running: the stage trail says which stage, the streaming dot
  // says whether the feed is live, and neither says whether the run is running, paused, blocked or
  // finished. It also means this strip now always has content, which is why it no longer returns
  // null — a bare pre-run draft reading "Ready" is exactly what the guard's "nothing worth a bar"
  // was mis-deciding.
  //
  // `effectiveLoopStatus` for the same reason every other surface uses it: a `complete` project
  // whose `stop_reason` names a ceiling rather than `done` finished non-genuinely, and must not
  // read as a green "Completed".
  const dispStatus = effectiveLoopStatus(p.status, p.stop_reason, p.held)
  return (
    <div data-type="caption" className="flex shrink-0 items-center gap-3 border-b border-outline-variant/40 bg-surface-low/30 px-l py-1 text-on-surface-low">
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
      {/* Runs on · workspace · project · cycles all float to the far RIGHT edge (ml-auto pushes
          the whole group). The project chip is interactive (deep-links to the Project). "Runs on"
          is always said, since PersonalClaw and an agent CLI are different programs doing her work. */}
      <span className="ml-auto inline-flex min-w-0 shrink-0 items-center gap-3">
        <LoopRunsOn loop={p} onChange={onRuntimeChange} />
        {wsBase && (
          <span className="inline-flex shrink-0 items-center gap-1" title={ws}>
            <Folder size={11} className="shrink-0 opacity-70" />
            <span className="max-w-[220px] truncate font-mono">{wsBase}</span>
          </span>
        )}
        {showProj && (
          onOpenProject ? (
            <button type="button" onClick={() => onOpenProject(projId)} title={`Project: ${projName} — open`}
              className="inline-flex min-w-0 items-center gap-1 text-on-surface-var hover:text-primary">
              <FolderKanban size={11} className="shrink-0 text-primary" />
              <span className="max-w-[180px] truncate">{projName}</span>
            </button>
          ) : (
            <span className="inline-flex min-w-0 items-center gap-1" title={`Project: ${projName}`}>
              <FolderKanban size={11} className="shrink-0 text-primary" />
              <span className="max-w-[180px] truncate">{projName}</span>
            </span>
          )
        )}
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

/** Humanize a duration in seconds → "3m", "1h 12m", "2d 4h". */
function fmtDuration(secs: number): string {
  const s = Math.floor(secs)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ${m % 60}m`
  const d = Math.floor(h / 24)
  return `${d}d ${h % 24}h`
}

// ── collapsible + resizable side panel shell ──

function CollapsiblePanel({ side, panelKey, def, min, max, icon: Icon, label, children }: {
  side: 'left' | 'right'; panelKey: string; def: number; min: number; max: number
  icon: typeof ListChecks; label: string; children: React.ReactNode
}) {
  const { width, collapsed, setCollapsed, onHandleDown, onHandleKey } = useResizablePanel(panelKey, { def, min, max, side, collapsible: true })
  // Allow a programmatic expand (e.g. ⌘P quick-open targeting a collapsed Files panel,
  // or a click on the editor-bar re-open tab), keyed by panelKey so each panel only
  // responds to its own request.
  useEffect(() => {
    const onExpand = (e: Event) => { if ((e as CustomEvent).detail === panelKey) setCollapsed(false) }
    window.addEventListener('ne:code-expand-panel', onExpand as EventListener)
    return () => window.removeEventListener('ne:code-expand-panel', onExpand as EventListener)
  }, [panelKey, setCollapsed])
  // Broadcast this panel's collapsed state so the editor tab bar can show a pull-out
  // re-open tab for it (no persistent vertical rail — the editor reclaims the full
  // width when collapsed). Fires on mount + every toggle; the bar reconciles by key.
  useEffect(() => {
    window.dispatchEvent(new CustomEvent('ne:code-panel-collapsed', { detail: { panelKey, side, label, collapsed } }))
  }, [panelKey, side, label, collapsed])
  const borderSide = side === 'left' ? 'border-r' : 'border-l'
  // Collapsed → render NOTHING (the editor reclaims the space). The re-open affordance
  // lives as a pull-out tab on the editor's tab bar (see CenterEditor), per the
  // VSCode-style activity model — not a persistent vertical slice in the body.
  if (collapsed) return null
  // Resize handle: a wider hit strip pinned to the panel's INNER editor-facing edge
  // (right edge of a left panel, left edge of a right panel). Kept inside the panel
  // bounds so the editor sibling can't paint over it.
  const handle = (
    <div onPointerDown={onHandleDown} onKeyDown={onHandleKey} role="separator" aria-orientation="vertical"
      tabIndex={0} aria-label={`Resize ${label} — arrow keys to resize`}
      aria-valuenow={Math.round(width)} aria-valuemin={min} aria-valuemax={max}
      className="group/handle absolute top-0 bottom-0 z-20 w-2 cursor-col-resize outline-none focus-visible:bg-primary/30"
      style={{ [side === 'left' ? 'right' : 'left']: 0 } as React.CSSProperties}>
      <div className="mx-auto h-full w-0.5 bg-transparent transition-colors group-hover/handle:bg-primary/60 group-focus-visible/handle:bg-primary" />
    </div>
  )
  return (
    <div className={`relative flex shrink-0 flex-col ${borderSide} border-outline-variant/40 bg-surface-low/40`} style={{ width }}>
      <div className="flex shrink-0 items-center justify-between gap-1 border-b border-outline-variant/40 px-2 py-1.5">
        <span data-type="label-s" className="inline-flex items-center gap-1.5 text-on-surface-var" style={fvs(550)}>
          <Icon size={14} /> {label}
        </span>
        <IconButton icon={side === 'left' ? PanelLeftClose : PanelRightClose} label={`Collapse ${label}`}
          onClick={() => setCollapsed(true)} size={24} iconSize={14} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      {handle}
    </div>
  )
}

// ── right panel: Tasks (agent loop events live IN TASK SCOPE — under each task
//    card — not a separate global feed) + project-level banners + a steer box. ──

function RightPanel({ project, onTasksChanged, tasksNonce, activityBySession, gateFail, stalled, onNudged, onStartNew, onAnswerOnScreen, answerWanted, onAnswerShown }: {
  project: CodeProject; onTasksChanged: () => void; tasksNonce: number
  activityBySession: Record<string, ActivityItem[]>; gateFail: { label: string; command: string; output: string } | null
  stalled: StallInfo | null; onNudged: () => void; onStartNew?: () => void
  /** Whether the answer panel (the project footer) is on screen, for the cockpit's toast. */
  onAnswerOnScreen?: (onScreen: boolean) => void
  /** The toast's Respond asked for the answer panel; `onAnswerShown` once its steer box has the cursor. */
  answerWanted?: boolean; onAnswerShown?: () => void
}) {
  // Navigable Tasks panel: a list view (all tasks, grouped by stage) and a per-task
  // DETAIL view (task plan + its agent loop events + a task-scoped steer box). The
  // selected task makes "what is the user steering" unambiguous for both sides.
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  // The toast's Respond is a project-level answer, and the project footer that takes it is
  // unmounted while a TASK is open (the detail view replaces it), so a Respond then closes the
  // task. The footer mounts with the request still standing and takes the cursor itself.
  useEffect(() => {
    if (answerWanted) setSelectedTaskId(null)
  }, [answerWanted])
  const links = project.task_list_ids ?? {}
  const [tasksByList, setTasksByList] = useState<Record<string, TaskItem[]>>({})
  const [loading, setLoading] = useState(true)
  // A task-list fetch that FAILED this refresh — so a transient error doesn't render
  // the stage as a (false) empty "no tasks" list, hiding real work in the core
  // execute-one-by-one surface. Tracked separately so successful stages still show.
  const [loadFailed, setLoadFailed] = useState(false)
  // Sequence guard: refresh fires on EVERY tasksNonce bump (each SSE lifecycle event +
  // the 8s poll), so during a busy run multiple fetches overlap. Without ordering, a
  // slow earlier fetch resolving AFTER a faster later one would clobber the rail with
  // stale tasks. Stamp each run + only the latest one's result is allowed to apply.
  const refreshSeq = useRef(0)
  const refresh = useCallback(() => {
    const lists = Object.values(links)
    if (!lists.length) { setLoading(false); return }
    const seq = ++refreshSeq.current
    let anyFailed = false
    Promise.all(lists.map((lid) => api.allTasks({ task_list: lid }).then((r) => [lid, r.tasks] as const)
      .catch(() => { anyFailed = true; return [lid, null] as const })))
      // A failed list yields null (NOT []): keep its PRIOR tasks shown (merge over the
      // previous map) rather than blanking the stage to a misleading "no tasks". Only
      // overwrite a list whose fetch succeeded.
      .then((pairs) => {
        if (seq !== refreshSeq.current) return  // a newer refresh superseded this one
        setTasksByList((prev) => {
          const next = { ...prev }
          for (const [lid, tasks] of pairs) if (tasks !== null) next[lid] = tasks
          return next
        })
        setLoadFailed(anyFailed)
      }).finally(() => { if (seq === refreshSeq.current) setLoading(false) })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(links)])
  useEffect(() => { refresh() }, [refresh, tasksNonce])

  const allTasks: TaskItem[] = Object.values(tasksByList).flat()
  const selected = selectedTaskId ? allTasks.find((t) => t.id === selectedTaskId) : null
  // Self-heal a dangling selection: if a task was open in the detail view and it's
  // since gone (deleted, or its list dropped) AFTER tasks have loaded, clear the id
  // so a refetch race can't snap a stale task back into focus. Only once loaded +
  // non-empty, so the normal "still fetching" window doesn't wrongly clear it.
  const selectionStale = !!selectedTaskId && !loading && allTasks.length > 0 && !selected
  useEffect(() => { if (selectionStale) setSelectedTaskId(null) }, [selectionStale])
  // findings per task (task_id, or sequential findings attributed to their stage's
  // active task) — the agent loop events shown in TASK SCOPE.
  const findingsByTask: Record<string, CodeFinding[]> = {}
  const stages = project.stage_plan ?? []
  const tasksByStage: Record<string, TaskItem[]> = {}
  for (const s of stages) tasksByStage[stageKey(s)] = (links[stageKey(s)] && tasksByList[links[stageKey(s)]]) || []
  // A sequential-mode cycle finding has no task_id; its `stage` field may carry the
  // stage id ("implementation"), the stage title, OR a model-decorated label like
  // '1 — Write bell_times.py' / 'Stage 2/2 — Verify & QA' (issue 642: the field is
  // unconstrained model output, and exact equality attributed ZERO of a 12-cycle
  // loop's findings). The ingest now canonicalizes new findings server-side; the
  // normalized fallback here keeps every already-ledgered legacy label attributable.
  const stageTasksFor = (fstage: string): TaskItem[] => {
    const s = stages.find((sg) => sg.stage === fstage || sg.title === fstage)
      ?? stages.find((sg) => normalizeStageLabel(sg.title) === normalizeStageLabel(fstage)
        || normalizeStageLabel(sg.stage ?? '') === normalizeStageLabel(fstage))
    return s ? (tasksByStage[stageKey(s)] || []) : []
  }
  for (const f of (project.findings ?? [])) {
    let tid = f.task_id
    if (!tid && f.stage) {
      const st = stageTasksFor(f.stage)
      // The active task if one is running; else the LAST task of the stage, so a
      // COMPLETED stage's cycle findings still surface under a task (sequential mode
      // attributes nothing, and every task is done → no in-progress to borrow).
      tid = (st.find((t) => t.status === 'in_progress')
        || st.find((t) => !isTerminalTask(t))
        || st[st.length - 1])?.id
    }
    if (tid) (findingsByTask[tid] ??= []).push(f)
  }

  if (selected) {
    const doneIds = new Set(allTasks.filter(isTerminalTask).map((t) => t.id))
    // This task's live events: its own task-worker session (parallel) if present;
    // else, only if THIS task is the one in-progress, the main worker's stream
    // (sequential mode runs the active task in loop-<id>). A non-running task gets
    // no borrowed activity.
    const taskSess = `loop-${project.id}-${selected.id}`
    // A TERMINAL task has no live activity. Its per-task bucket can LINGER though: it's
    // only cleared on a clean chat_done, so a worker torn down by an orphan-reap /
    // watchdog kill / conflict-pause leaves a populated bucket — which would render as
    // stale "live" events on the now-done task's detail view. Gate on terminal (same
    // reasoning as the list-row pulse's doneForPulse guard).
    const live = isTerminalTask(selected)
      ? []
      : activityBySession[taskSess]
        ?? (selected.status === 'in_progress' ? activityBySession[`loop-${project.id}`] : undefined)
        ?? []
    // Which stage owns this task, and is that stage open (active/done) for exec? A
    // task in a not-yet-reached stage is held by the phase barrier → 'waiting'.
    const ownerStage = stages.find((s) => (tasksByStage[stageKey(s)] || []).some((t) => t.id === selected.id))
    const ss = ownerStage ? (project.stage_status?.[stageKey(ownerStage)] ?? 'pending') : 'active'
    const stageOpen = ss === 'active' || ss === 'done'
    // The specific NOT-yet-resolved blockers for this task (its dependency edges whose
    // target isn't terminal), as {id,title} — so the detail view can NAME what it's
    // waiting on + let the user click through, instead of a generic "its dependency
    // tasks". Resolve titles against the full task set; an edge to an unknown id (cross-
    // stage / pruned) is dropped (it's not a blocker we can act on here).
    const byId = new Map(allTasks.map((t) => [t.id, t]))
    const blockers = (selected.dependencies ?? [])
      .map((d) => d.depends_on_task_id)
      .filter((bid): bid is string => !!bid && byId.has(bid) && !doneIds.has(bid))
      .map((bid) => ({ id: bid, title: byId.get(bid)!.title }))
    // key by task id so a task→task switch (e.g. clicking a blocker chip, which calls
    // onOpenTask WITHOUT unmounting) REMOUNTS with fresh local state — else the steer
    // draft + last-steer echo (component-local) bleed across tasks, and a half-typed
    // steer for task A could be sent scoped to task B. Mirrors CenterEditor's
    // `FileViewer key={active.path}` for the same reason.
    return <TaskDetailView key={selected.id} project={project} task={selected} doneIds={doneIds} stageOpen={stageOpen}
      knownIds={new Set(byId.keys())}
      findings={findingsByTask[selected.id] ?? []} liveActivity={live} blockers={blockers} onOpenTask={setSelectedTaskId}
      onBack={() => setSelectedTaskId(null)} onChanged={onTasksChanged} />
  }
  // Task ids whose own worker session has live events right now → a list-row pulse
  // so the user sees WHICH parallel tasks are actively emitting (not just statically
  // 'running'). Keyed off the per-session activity buckets.
  const activeTaskIds = new Set<string>()
  const pfx = `loop-${project.id}-`
  // A DONE task must never show the "active now" pulse: a task-worker reaped without
  // a clean chat_done (orphan reap, watchdog kill, conflict pause) leaves its activity
  // bucket populated, so a bare items.length check would pulse a finished/merged task
  // forever. Gate on the task not being done.
  const doneForPulse = new Set(
    allTasks.filter(isTerminalTask).map((t) => t.id),
  )
  for (const [sess, items] of Object.entries(activityBySession)) {
    if (!sess.startsWith(pfx) || !items.length) continue
    const tid = sess.slice(pfx.length)
    if (!doneForPulse.has(tid)) activeTaskIds.add(tid)
  }
  return (
    <div className="flex h-full flex-col">
      {/* A task-list fetch failed this refresh — say so (with a retry) instead of
          rendering the affected stage as a false-empty "no tasks". Any stages that DID
          load stay shown below; this is a non-blocking notice, not a wipe. */}
      {loadFailed && (
        <div role="alert" data-type="caption" className="mx-2 mt-2 flex items-center gap-2 rounded-md px-2.5 py-1.5"
          style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)', color: 'var(--color-warn)' }}>
          <AlertTriangle size={12} className="shrink-0" />
          <span className="min-w-0 flex-1">Couldn't refresh some tasks — showing the last known list.</span>
          {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
          <Button variant="ghost" size="xs" onClick={refresh} className="shrink-0 gap-1 px-1.5 text-[0.75rem] text-warn hover:bg-warn/15"><RotateCcw size={11} /> Retry</Button>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <StageTasks project={project} onTasksChanged={onTasksChanged} loading={loading}
          tasksByList={tasksByList} onSelect={setSelectedTaskId} activeTaskIds={activeTaskIds}
          mainActivity={activityBySession[`loop-${project.id}`] ?? []} />
      </div>
      {/* Project-level signals + steer box (list view): not task-scoped. */}
      <ProjectFooter project={project} gateFail={gateFail} stalled={stalled} onNudged={onNudged} onStartNew={onStartNew}
        onAnswerOnScreen={onAnswerOnScreen} answerWanted={answerWanted} onAnswerShown={onAnswerShown} />
    </div>
  )
}

// ── right: live activity + steer ──

/** A closing banner for a terminal project (complete / failed / stopped): the
 *  outcome + a one-line tally (stages done · cycles · distinct files touched).
 *  Nothing for an active/pre-run project — those have their own affordances. */
function OutcomeBanner({ project: p, findings }: { project: CodeProject; findings: CodeFinding[] }) {
  const TERMINAL: Record<string, { label: string; tone: string; ok: boolean }> = {
    complete: { label: 'Project complete', tone: 'var(--color-ok)', ok: true },
    // failed is NOT a frozen terminal state (the status machine allows failed →
    // running) — frame it as recoverable so the banner agrees with the Resume
    // button + steer box rather than reading like a dead end.
    failed: { label: 'Project failed — fix the issue + Resume to retry', tone: 'var(--color-danger)', ok: false },
    stopped: { label: 'Project stopped', tone: 'var(--color-on-surface-low)', ok: false },
  }
  let meta = TERMINAL[p.status]
  if (!meta) return null
  // A COMPLETE project carrying an error_message finished NON-genuinely (the cycle
  // budget ran out / the loop exhausted before all stages passed their gates). Re-
  // frame it so the user isn't misled by a green "complete" check: a warn tone +
  // honest label, with the persisted reason shown below (the !meta.ok branch).
  const incompleteFinish = p.status === 'complete' && !!p.error_message
  if (incompleteFinish) {
    meta = { label: 'Project ended before finishing', tone: 'var(--color-warn)', ok: false }
  }
  const stages = p.stage_plan?.length ?? 0
  const stagesDone = Object.values(p.stage_status ?? {}).filter((s) => s === 'done').length
  const cycles = p.total_cycles || findings.length
  // distinct files the worker touched across all cycles, scoped to the file root
  // (workspace, or the project files dir for no-workspace doc projects)
  const ws = (p.workspace_dir || p.files_dir || '').replace(/\/$/, '')
  // For a no-workspace project the root is the engine dir, so drop its bookkeeping
  // (findings/, brief.md, …) from "what was built" — only the real deliverables.
  const isProjectDir = !p.workspace_dir
  const root = (ws || '').replace(/\/$/, '')
  // Distinct files the worker actually built — resolve via the shared helper (worktree
  // remap + bare-relative + outside-root rejection), Set-dedup, and in project-dir mode
  // drop engine bookkeeping (incl. the dynamic guidance_<id>.txt prefix this tally
  // previously missed) so the count reflects real deliverables.
  const files = new Set<string>()
  for (const f of findings) for (const raw of (f.files_touched ?? [])) {
    const r = resolveTouchedPath(raw, root)
    if (!r) continue
    if (isProjectDir) {
      const top = r.rel.split('/')[0]
      if (PROJECT_DIR_HIDDEN.has(top) || [...PROJECT_DIR_HIDDEN_PREFIXES].some((pre) => top.startsWith(pre))) continue
    }
    files.add(r.abs)
  }
  const bits = [
    stages > 0 ? `${stagesDone}/${stages} stages` : '',
    cycles > 0 ? `${cycles} cycle${cycles === 1 ? '' : 's'}` : '',
    files.size > 0 ? `${files.size} file${files.size === 1 ? '' : 's'}` : '',
  ].filter(Boolean)
  const Icon = meta.ok ? CheckCircle2 : XCircle
  return (
    <div data-type="body-s" className="mb-3 rounded-lg p-2.5"
      style={{ background: `color-mix(in srgb, ${meta.tone} 12%, transparent)` }}>
      <div className="inline-flex items-center gap-1.5" style={withWeight({ color: meta.tone }, 550)}>
        <Icon size={14} /> {meta.label}
      </div>
      {bits.length > 0 && <p data-type="caption" className="mt-1 text-on-surface-var">{bits.join(' · ')}</p>}
      {!meta.ok && p.error_message && <p data-type="caption" className="mt-1 text-on-surface-low">{p.error_message}</p>}
      {/* Surface the files the worker produced as clickable chips — the payoff of the
          run is "open + read what was built". Shown for ANY terminal outcome that
          produced files: a genuine complete, an incomplete (budget-exhausted) finish,
          AND a stopped/failed run that still wrote real work (reviewing partial output
          is exactly why you'd open a stopped run). Only suppressed when nothing was
          built. */}
      {files.size > 0 && (
        <div className="mt-1.5">
          {/* FilesTouched now owns the cap + "+N more" reveal (max=12 here), so no
              external slice / static "showing 12" note — the toggle is self-describing. */}
          <p data-type="caption" className="text-on-surface-low">What was built — click to open:</p>
          <FilesTouched files={[...files]} ws={ws} max={12} />
        </div>
      )}
    </div>
  )
}

/** Project-level footer for the Tasks panel: signals that aren't task-scoped
 *  (attended question, gate failure, stall, terminal outcome) + the steer box.
 *  Per-task agent loop events live under each task card (StageTasks), not here. */
export function ProjectFooter({ project, gateFail, stalled, onNudged, onStartNew, onAnswerOnScreen, answerWanted, onAnswerShown }: { project: CodeProject; gateFail: { label: string; command: string; output: string } | null; stalled: StallInfo | null; onNudged: () => void; onStartNew?: () => void; onAnswerOnScreen?: (onScreen: boolean) => void; answerWanted?: boolean; onAnswerShown?: () => void }) {
  // The answer panel reports whether it is on screen, so the cockpit's toast that points here
  // never stands over it (`CodeToast`).
  const answerRef = useOnScreenReport(onAnswerOnScreen)
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  // Synchronous double-send guard (sending state is async — rapid double Enter/click
  // both pass before the re-render → a double nudge). Mirrors the task steer's guard.
  const sendingRef = useRef(false)
  // The steer textarea — focused when the user clicks "Respond" on the needs-input
  // toast, so answering is one click away wherever they were.
  const steerRef = useRef<HTMLTextAreaElement>(null)
  // Auto-grow the box to fit a multi-line steer (and shrink back after send clears it).
  useEffect(() => { autoGrowTextarea(steerRef.current) }, [text])
  // A standing Respond, whether this footer was on screen when it was pressed or mounted after it
  // (the rail opened, a task's detail closed): take the cursor, then say it is done.
  useEffect(() => {
    if (!answerWanted) return
    steerRef.current?.focus()
    steerRef.current?.scrollIntoView({ block: 'nearest' })
    onAnswerShown?.()
  }, [answerWanted, onAnswerShown])
  const [steers, setSteers] = useState<{ text: string; failed?: boolean }[]>([])
  const findings = project.findings ?? []
  const missingCommands = missingCommandNotices(project.command_runnability)
  // The worker asked something only when the pause is its question: a merge waiting, a conflict and
  // a spend cap are the scheduler's, each answered on its own card above, not in the steer box; a
  // wait after your Deny is the loop's, which your steer settles.
  const pq = project.pending_question
  const workerAsks = project.status === 'needs_input' && !pq?.merge && !pq?.conflict && !pq?.spend_cap && !pq?.declined

  // Resume a loop a spend cap paused, from the notice that says so (the header's Resume does the same).
  const [resuming, setResuming] = useState(false)
  async function resume() {
    if (resuming) return
    setResuming(true)
    try { await api.uLoopAction(project.id, 'resume'); onNudged() }
    catch (e) {
      window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't resume this project: ${(e as Error).message || 'unknown error'}` } }))
    } finally { setResuming(false) }
  }

  async function steer(explicit?: string) {
    const t = (explicit ?? text).trim()
    if (!t || sending || sendingRef.current) return
    sendingRef.current = true
    setSending(true)
    setSteers((s) => [...s, { text: t }])
    if (explicit === undefined) setText('')
    try { await api.uLoopNudge(project.id, t); onNudged() }
    catch {
      setSteers((s) => s.map((m, i) => (i === s.length - 1 ? { ...m, failed: true } : m)))
      // Restore the typed text so a failed send isn't lost — the user shouldn't have
      // to retype a (possibly long) steer because the worker was briefly unreachable.
      if (explicit === undefined) setText((cur) => cur || t)
    }
    finally { setSending(false); sendingRef.current = false }
  }

  const lastSteer = (() => {
    const persisted = project.nudges ?? []
    // The last optimistic steer is still "pending" (not yet in persisted) ONLY if it
    // hasn't been confirmed by the backend. Count by SUCCESSFUL optimistic sends, not
    // steers.length: a FAILED steer never enters `persisted`, so slicing by raw length
    // would drift after any failure — making later successful steers show their
    // optimistic copy (no "applied cycle N") forever instead of the confirmed one. A
    // failed steer surfaces only when it's genuinely the latest event.
    const lastOptimistic = steers[steers.length - 1]
    const succeededCount = steers.filter((s) => !s.failed).length
    // A trailing failure, or more successful sends than persisted yet → show optimistic.
    if (lastOptimistic && (lastOptimistic.failed || succeededCount > persisted.length)) return lastOptimistic
    return persisted[persisted.length - 1] || lastOptimistic || null
  })()

  return (
    <div ref={answerRef} className="shrink-0 border-t border-outline-variant/40">
      <div className="max-h-[40vh] overflow-y-auto px-2 pt-2">
        {/* An Attended build's workers ask before they act, and their asks are answered here. */}
        <LoopApprovals loopId={project.id} className="mb-2" />
        {/* An Attended build's finished work, waiting for you to merge it into your branch. */}
        {project.status === 'needs_input' && project.pending_question?.merge && (
          <MergeReview loopId={project.id} waiting={project.pending_question.merge} onMerged={onNudged} />
        )}
        {/* A finished task's work conflicts with your branch: kept on its branch until you redo it,
            resolve it yourself and Resume, or drop it. */}
        {project.status === 'needs_input' && project.pending_question?.conflict && (
          <MergeConflict loopId={project.id} conflict={project.pending_question.conflict} onChosen={onNudged} />
        )}
        {/* A spend cap refused the loop's next call: paused rather than asking, with the cap's
            own sentence, where it is changed, and Resume. */}
        {project.status === 'needs_input' && project.pending_question?.spend_cap && (
          <SpendCapPause className="mb-s" reason={project.pending_question.question}
            detail={project.pending_question.why} settings={project.pending_question.settings}
            onResume={() => void resume()} busy={resuming} />
        )}
        {/* Its worker's model can't use tools: paused rather than asking, naming the model, where
            another is chosen, and Resume. */}
        {project.status === 'needs_input' && project.pending_question?.no_tools && (
          <NoToolsPause className="mb-s" question={project.pending_question.question}
            why={project.pending_question.why} onResume={() => void resume()} busy={resuming} />
        )}
        {/* Attended question — the call to action; answer in the steer box below. */}
        {project.status === 'needs_input' && project.pending_question?.question && !project.pending_question.merge && !project.pending_question.spend_cap && !project.pending_question.no_tools && !project.pending_question.conflict && (
          <div role="alert" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-info)' }, 550)}>
              <HelpCircle size={14} /> The worker needs your input
            </div>
            <p className="whitespace-pre-wrap text-on-surface">{project.pending_question.question}</p>
            {/* The worker's reasoning for asking (what changes based on the answer) —
                context that helps the user answer well. */}
            {project.pending_question.why && (
              <p data-type="caption" className="mt-1 whitespace-pre-wrap text-on-surface-low">{project.pending_question.why}</p>
            )}
            <div className="mt-2 flex items-center gap-2">
              <p data-type="caption" className="flex-1 text-on-surface-low">Answer below to resume the build.</p>
              {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
              <Button variant="ghost" size="xs" disabled={sending} disabledReason={BUSY_REASON}
                onClick={() => steer('Proceed with your best judgment / the sensible default you proposed. Record the assumption in your finding and continue.')}
                className="shrink-0 px-2 text-[0.75rem] text-info hover:bg-info/10">
                Use your best judgment
              </Button>
            </div>
          </div>
        )}
        {/* needs_input but NO structured question (the question file failed to write /
            was cleared, or a generic pause): the header pill says "needs input" but
            without this the panel gave zero explanation + no call to action. Fall back
            to a generic prompt so the user always knows what to do — steer to resume. */}
        {project.status === 'needs_input' && !project.pending_question?.question && (
          <div role="alert" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-info)' }, 550)}>
              <HelpCircle size={14} /> The worker is waiting on you
            </div>
            <p data-type="caption" className="text-on-surface-var">It paused for input but didn't leave a specific question. Steer it below with direction (or tell it to use its best judgment), then it resumes.</p>
            <div className="mt-2 flex justify-end">
              {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
              <Button variant="ghost" size="xs" disabled={sending} disabledReason={BUSY_REASON}
                onClick={() => steer('Proceed with your best judgment / the sensible default. Record any assumption in your finding and continue.')}
                className="shrink-0 px-2 text-[0.75rem] text-info hover:bg-info/10">
                Use your best judgment
              </Button>
            </div>
          </div>
        )}
        <OutcomeBanner project={project} findings={findings} />
        {/* A run that ended with tasks still at work kept their work: what, where, and Merge / Discard. */}
        {project.workspace_dir && <KeptWork loopId={project.id} status={project.status} />}
        {/* The stage's exit criteria as its gate last judged them: which are met, which are not,
            and which the loop's records could not show. What a steer or a relaxed criterion is for. */}
        <StageGateVerdict project={project} />
        {/* BLOCKED — persisted (reload-safe) explanation of WHY the build paused (e.g.
            the stall-pause: a stage produced many cycles without clearing its gate).
            The transient `stalled` SSE banner below only shows while running; once the
            supervisor flips the project to blocked, this is what tells the user what
            happened + that a steer/Resume gets it going again. */}
        {/* `role="status"`, not `alert`: this banner is PERSISTED and reload-safe, so an
            assertive role would re-interrupt a screen-reader user on every visit to a state they
            already know about. Its sibling below (`gateFail`) is `alert` correctly — that one
            fires while the run is live. The two sat adjacent with only one announced at all,
            which is the half of issue 671 that made the explanation unreachable rather than
            just quiet. (Issue numbers in this block are spelled bare:
            `design/tokenLint` skips only lines that OPEN with a comment marker, so a hash
            followed by three digits on a continuation line reads as a raw hex colour.) The heading says "Blocked" (the registry's word for this status) instead of
            "Paused": `paused` is a DIFFERENT status the user chose, and calling this one by its
            name is what makes the state searchable on the page. */}
        {project.status === 'blocked' && project.error_message && (
          <div role="status" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
              <AlertTriangle size={14} /> {loopStatusLabel('blocked')} — needs you
            </div>
            <p className="whitespace-pre-wrap text-on-surface-var">{project.error_message}</p>
            <p data-type="caption" className="mt-1 text-on-surface-low">
              {missingCommands.length > 0
                ? `Edit the stored ${missingCommands.length === 1 ? 'command' : 'commands'}, then Resume.`
                : 'Steer it below (or relax a stage criterion), then Resume.'}
            </p>
          </div>
        )}
        {gateFail && project.status === 'running' && (
          <div role="alert" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
              <XCircle size={14} /> Supervisor {gateFail.label} check failed — stage held
            </div>
            {gateFail.command && <p data-type="caption" className="font-mono text-on-surface-low">{gateFail.command}</p>}
            {gateFail.output && (
              <pre data-type="caption" className="mt-1 max-h-40 overflow-auto rounded-md bg-surface-high/60 p-2 text-on-surface-var whitespace-pre-wrap break-words">{gateFail.output}</pre>
            )}
          </div>
        )}
        {/* Show while running (early stall warning) AND when blocked: the watchdog's
            stall escalation flips the project to BLOCKED, and this banner is what
            explains WHY — gating on 'running' alone hid it exactly when the user most
            needs it (the project is parked awaiting their steer). */}
        {stalled && !gateFail && (project.status === 'running' || project.status === 'blocked') && (
          <div role="status" data-type="body-s" className="mb-2 rounded-lg p-2.5"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={withWeight({ color: 'var(--color-warn)' }, 550)}>
              <AlertTriangle size={14} /> “{stalled.title}” {project.status === 'blocked' ? 'is stuck — paused for you' : 'seems stuck'}
            </div>
            <p data-type="caption" className="text-on-surface-var">
              {stalled.cause === 'binary'
                ? `${stalled.findings} cycles in and ${stalled.binary || 'the command binary'} is not on PATH — edit the stored command${project.status === 'blocked' ? ', then Resume' : ''}.`
                : `${stalled.findings} cycles in and the gate still hasn't passed — steer it or relax a criterion${project.status === 'blocked' ? ', then Resume' : ''}.`}
            </p>
          </div>
        )}
        {/* the most recent steer, so the user sees their message landed */}
        {lastSteer && (
          <div data-type="body-s" className="mb-2 self-end rounded-xl bg-primary/15 px-2.5 py-1.5 text-on-surface-var">
            {lastSteer.text}
            {'applied_cycle' in lastSteer && (lastSteer as { applied_cycle?: number }).applied_cycle != null && (
              <span data-type="caption" className="ml-1.5 text-on-surface-low">· applied cycle {(lastSteer as { applied_cycle?: number }).applied_cycle}</span>
            )}
            {'failed' in lastSteer && (lastSteer as { failed?: boolean }).failed && <span data-type="caption" className="ml-1.5 text-danger">· failed to send</span>}
          </div>
        )}
      </div>
      {/* steer box — only while the worker can ACT on it. */}
      <div className="p-2">
        {STEERABLE.has(project.status) ? (
          <div className="flex items-end gap-1.5 rounded-xl bg-surface-container px-2.5 py-1.5 focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
            <textarea ref={steerRef} value={text} onChange={(e) => setText(e.target.value)} rows={1}
              placeholder={workerAsks ? 'Answer the worker…' : 'Steer the worker…'}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); steer() } }}
              data-type="body-s" className="max-h-24 min-h-0 flex-1 resize-none overflow-y-auto bg-transparent text-on-surface outline-none placeholder:text-on-surface-low" />
            {/* Same split as the task-level steer box above: emptiness stays `disabled` (with the
                reason), in-flight becomes `loading` and the primitive owns the spinner. */}
            <IconButton icon={Send} label="Send steer" filled size={28} iconSize={13}
              disabled={!text.trim()} disabledReason={!text.trim() ? 'Type a steer first' : undefined}
              loading={sending} onClick={() => steer()} className="shrink-0" />
          </div>
        ) : project.status === 'ready' || project.status === 'review' ? (
          <p data-type="caption" className="px-1.5 py-1 text-center text-on-surface-low">Press Start to launch — steer the worker once it's running.</p>
        ) : (
          // Finished: the steer box is gone, so give the dangling "start a new one"
          // call-to-action a real button (was prose pointing nowhere — the only paths
          // out were buried in the overflow menu). Navigates to the new-project composer.
          <div className="flex flex-col items-center gap-1.5 px-1.5 py-1 text-center">
            <p data-type="caption" className="text-on-surface-low">This project has finished.</p>
            {/* The raw 0.75rem size stays: Button carries its own data-type role and takes no override — no call-site element here to carry caption (see typeScaleRatchet). */}
            <Button variant="tonal" size="xs" onClick={() => onStartNew?.()} className="gap-1.5 px-3 text-[0.75rem]">
              <Plus size={13} /> Start a new project
            </Button>
          </div>
        )}
      </div>
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
