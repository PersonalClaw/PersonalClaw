import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type CodeProject, type CodeFinding } from '../../../lib/api'
import { useQuery } from '../../../lib/data'
import { refreshKinds, useChatSocket, type WsMessage } from '../../../lib/useChatSocket'
import { useVisiblePoll } from '../../../lib/useVisiblePoll'
import { cleanSay, toolDetail } from '../../../lib/agentFeed'
import { isStepPhase, phaseFeedLine } from '../../../lib/workPhase'
import { useRunStream } from '../../loops/useRunStream'
import { belongsToLoop } from '../../workflows/containerKey'
import { foldReducer, emptyRunFlags, type RunFlags } from '../../loops/runFold'
import { useWorkspaceMissing } from '../../../lib/useWorkspaceMissing'
import { useRuntimeGroups } from '../../../lib/agents'
import { type CodeToastState } from '../CodeToast'
import { useQueryFlag, type RouteProps } from '../../../app/useQueryState'
import { PROJECT_DIR_HIDDEN, PROJECT_DIR_HIDDEN_PREFIXES } from './files/WorkspaceTree'
import { type ActivityItem, resolveTouchedPath } from './cockpitShared'
import { loopToCodeProject } from './codeProject'

// ── The code cockpit's controller ─────────────────────────────────────────────────────────────
//
// What the cockpit keeps for one code loop: the project, read on mount and from a cached snapshot
// for the first paint, kept current from the run stream and a poll while it runs, and "missing"
// once the loop is gone; whether its workspace folder still exists; the run's gate, stall and
// re-prompt flags; the worker's live activity per session from the chat socket, and the files it
// touches followed into the editor; and the terminal, the toast, the answer panel and the action
// guard the regions share. `CodeCockpitPage.tsx` composes the regions around it.

export function useCodeCockpit({ id, query, setQuery }: { id: string } & Pick<RouteProps, 'query' | 'setQuery'>) {
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
  return {
    project, setProject, showTerm, setShowTerm, pendingRunCmd, setPendingRunCmd, pendingRunNonce, pickWs,
    setPickWs, acting, setActing, loadErr, load, wsMissing, runFlags, gateFail, stalled, toast, setToast,
    runtimes, runtimesLoaded, answerOnScreen, setAnswerOnScreen, answerWanted, setAnswerWanted, answerShown,
    tasksNonce, setTasksNonce, connected, activityBySession, refetchTasks,
  }
}
