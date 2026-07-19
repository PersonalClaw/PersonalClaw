import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Code2, Play, Pause, Square, Trash2, Loader2, ListChecks, FolderTree,
  TerminalSquare, X, Send, CircleDot, CheckCircle2, Circle, Wrench, Activity, GitBranch,
  ChevronDown, ChevronRight, ChevronLeft, Target, FileCode, HelpCircle, Folder, Repeat, Clock, XCircle, AlertTriangle,
  PanelLeftClose, PanelRightClose, PanelLeftOpen, PanelRightOpen, FilePlus2, FolderPlus, Rocket, Hand, Plus, RotateCcw, FolderKanban, CirclePlay, Search,
} from 'lucide-react'
import { TopBar } from '../../ui/TopBar'
import { HeaderActions, HeaderControl } from '../../ui/HeaderActions'
import { Button } from '../../ui/Button'
import { confirm } from '../../ui/dialog'
import { api, type CodeProject, type CodeStage, type CodeFinding, type FsEntry, type TaskItem, type Loop } from '../../lib/api'
import { useCachedData } from '../../lib/useCachedData'
import { useChatSocket, type WsMessage } from '../../lib/useChatSocket'
import { useVisiblePoll } from '../../lib/useVisiblePoll'
import { cleanSay, toolDetail } from '../../lib/agentFeed'
import { useRunStream } from '../loops/useRunStream'
import { foldReducer, emptyRunFlags, type RunFlags } from '../loops/runFold'
import { DiffView } from './DiffView'
import { WorkspacePicker } from './WorkspacePicker'
import { FileTree } from '../files/browse/FileTree'
import { FileViewer, type FileViewerHandle } from '../files/browse/FileViewer'
import { useFileTabs } from '../files/browse/useFileTabs'
import { useDirCache, useGitStatus } from '../files/filesData'
import { TerminalView } from '../terminal/TerminalView'
import { runInTerminalWhenReady } from '../terminal/terminalBridge'
import type { TermTab } from '../terminal/TerminalPage'
import { TypingReveal } from './TypingReveal'
import { DiffReveal } from './DiffReveal'
import { useResizablePanel } from './useResizablePanel'
import { CockpitPromptBar } from '../loops/CockpitPromptBar'
import { useMode } from '../../app/theme'
import { useQueryFlag, type RouteProps } from '../../app/useQueryState'
import { overlayEnter, messageEnter, listItemEnter, stagger, springs } from '../../design/motion'
import { Expandable } from '../../ui/motion'

const EMPTY_ARTIFACTS = new Set<string>()

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
  }
}

// Statuses where a steer can still be acted on: running (applies next cycle) or a
// resumable state (the nudge is queued / resumes the worker). Terminal states
// (complete/stopped) have no next cycle, so the steer box is hidden for them.
// `stagnant` is the state that most NEEDS a steer (the watchdog flags "stalled —
// needs direction") — a code loop can reach it (the shared stagnation check), so it
// must be steerable, not a dead end.
const STEERABLE = new Set(['running', 'paused', 'blocked', 'needs_input', 'failed', 'stagnant'])
// Terminal, non-resumable statuses — mirrors project.py TERMINAL_STATUSES.
const TERMINAL_STATUSES = new Set(['complete', 'stopped'])
// Above this content length the worker-driven write/erase reveal animation is
// skipped (the editor opens directly): char-by-char animating hundreds of KB mounts
// a giant DOM string + janks, with no readability payoff for machine-generated bulk.
const REVEAL_MAX_CHARS = 40_000

// Resolve a worker-recorded `files_touched` path against the workspace root — the ONE
// place this logic lives (was copy-pasted into the follow-worker open, the OutcomeBanner
// tally, and the FilesTouched chips, which had already drifted: only one deduped). A
// path comes in three forms: absolute under <root>; a parallel task-worker's worktree
// path (<root>/.pclaw-worktrees/<task_id>/<rel>, deleted post-merge → remap to the
// merged base <root>/<rel>); or a bare relative path (no-workspace/sequential mode
// records "greet.py"). Returns {abs, rel} when it resolves to a real file UNDER the
// root, or null (absolute-but-outside-root, or an unresolvable worktree path). `root`
// must already be trailing-slash-stripped.
function resolveTouchedPath(raw: string, root: string): { abs: string; rel: string } | null {
  if (typeof raw !== 'string' || !raw || !root) return null
  let p = raw
  const mk = '/.pclaw-worktrees/'
  const i = p.indexOf(mk)
  if (i >= 0) {
    const rel = p.slice(i + mk.length).split('/').slice(1).join('/')  // drop the <task_id> segment
    p = rel ? `${root}/${rel}` : p
  }
  if (p.includes(mk)) return null  // unresolvable worktree path → dead file
  if (p.startsWith(root + '/')) return { abs: p, rel: p.slice(root.length + 1) }
  if (p.startsWith('/')) {
    // An ABSOLUTE path that doesn't prefix-match `root` is usually a REALPATH form of a
    // file genuinely under a symlinked workspace (worker tools resolve symlinks: /tmp→
    // /private/tmp, /var→/private/var on macOS). A plain prefix check wrongly rejected it
    // as "outside the workspace" → the file's FilesTouched chip vanished, follow-worker
    // skipped it, and the OutcomeBanner tally under-counted. Recover via the workspace's
    // last segment (same marker approach as ChangesPanel/canonPath); re-root under `root`
    // so downstream open/canon paths stay consistent. Still null if truly outside.
    const wsBase = root.split('/').pop() || ''
    const marker = `/${wsBase}/`
    const mi = wsBase ? p.lastIndexOf(marker) : -1
    if (mi >= 0) { const rel = p.slice(mi + marker.length); return { abs: `${root}/${rel}`, rel } }
    return null  // absolute but genuinely OUTSIDE the workspace
  }
  return { abs: `${root}/${p.replace(/^\.?\//, '')}`, rel: p }  // bare relative → under root
}

// The effective key a stage is tracked under in stage_status / task_list_ids. The
// backend keys a STAGELESS phase (blank stage id — e.g. an unlabeled decomposition
// phase) by its TITLE everywhere (_stage_of/set_stage_status/ensure_stage_lists), so
// the FE must use the same `stage || title` key — indexing by the bare s.stage would
// miss a stageless phase's status + TaskList (it'd read 'pending'/'no tasks' forever).
const stageKey = (s: CodeStage): string => (s.stage || s.title || '')

// Truncate a build/test command for an overflow-menu label. A blunt slice(0,48) on
// the WHOLE "Run build (<cmd>)" string cut the command mid-word and dropped the
// closing paren (e.g. "Run build (python -m pytest --cov=src --c") — reads as broken.
// Cap just the command + add an ellipsis so the wrapper "(…)" always stays intact.
const cmdLabel = (cmd: string, max = 36): string => {
  const c = (cmd || '').trim()
  return c.length > max ? `${c.slice(0, max - 1)}…` : c
}

// Grow a steer textarea to fit its content (up to the CSS max-height, which then
// scrolls) so a multi-line steer is visible while composing — a rows={1} box hid
// everything past the first line. Reset to auto first so it can also SHRINK back.
function autoGrowTextarea(el: HTMLTextAreaElement | null) {
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

// One live worker-activity line: a tool call, a status/thinking event, or a
// 'say' line — the worker's own assistant narration (streamed chat_chunk text),
// which makes the panel read like an AI chat sidebar, not just a tool log.
interface ActivityItem { kind: 'tool' | 'status' | 'say'; label: string; detail?: string; rawLabel?: string }

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
  // A brownfield project whose bound workspace was moved/deleted on disk after binding.
  // Probed below; surfaces a proactive "re-pick the folder" banner instead of only
  // erroring when the user clicks Start against a vanished dir (C244 follow-on).
  const [wsMissing, setWsMissing] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  // Set by Escape so the blur that fires as the input unmounts skips the save.
  const cancelRename = useRef(false)
  // Guards the double commitRename (Enter → unmount → onBlur) from a double PUT.
  const renameInFlight = useRef(false)

  // Tracks an initial-load failure (transient 5xx/network on the FIRST fetch, before
  // any snapshot). Without it a failed first load left `project` null forever — an
  // eternal spinner with no recovery, since SSE only updates an already-loaded view.
  const [loadErr, setLoadErr] = useState<string | null>(null)
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
      else setLoadErr((e as Error).message || 'Could not load this project')
    })
  }, [id])
  useEffect(() => { load() }, [load])
  // Auto-retry while the FIRST load hasn't landed (project still null) and the last
  // attempt errored — so a transient blip recovers on its own without a manual Retry.
  useVisiblePoll(() => load(), project === null && loadErr ? 4000 : null)
  // Probe whether a bound brownfield workspace still exists on disk (the user may have
  // moved/deleted the repo). Only for a not-running brownfield with a path set; a
  // running worker already proves it exists. browseDirs throws if the dir is gone.
  useEffect(() => {
    const p = project
    if (!p || p === 'missing') return
    const w = (p.workspace_dir || '').trim()
    if (!w || p.project_kind !== 'brownfield' || p.status === 'running') { setWsMissing(false); return }
    let alive = true
    api.browseDirs(w).then(() => { if (alive) setWsMissing(false) })
      .catch((e) => {
        if (!alive) return
        // Only flag "no longer exists" when the dir is genuinely GONE. browseDirs also
        // fails on a permission/sensitive-path block (403) or a transient network/5xx —
        // where the dir likely DOES exist — and flagging those would wrongly nag the
        // user to re-pick a perfectly good workspace. The backend says "No such
        // directory" only for true non-existence; anything else → don't claim missing.
        const msg = (e instanceof Error ? e.message : '').toLowerCase()
        setWsMissing(msg.includes('no such directory'))
      })
    return () => { alive = false }
  }, [project])

  // Instant-paint seed: re-opening a project should show its last snapshot
  // immediately instead of a cold spinner. The authoritative mount fetch above +
  // the live SSE still own all subsequent state — this only fills the FIRST paint
  // while `project` is still null. In-memory only (persist:false): the SSE/poll
  // re-pulls fresh on mount anyway, so we never want a stale snapshot across reload.
  const { data: cachedProject } = useCachedData(`code:project:${id}`, () => api.uLoop(id).then(loopToCodeProject).catch(() => null), { persist: false })
  useEffect(() => {
    if (project === null && cachedProject) setProject(cachedProject)
  }, [cachedProject, project])

  // A failed supervisor gate check (build/lint/test the supervisor ran itself) —
  // the latest one, so the user sees WHY a stage isn't advancing instead of silent
  // re-cycling. Cleared when the project advances a stage / completes.
  // gate-failure + stall banners are TRANSIENT run flags folded from lifecycle events
  // by the shared pure `foldReducer` (P16 — one fold for every run surface; Code Cockpit
  // no longer hand-maintains its own inline gate/stall switch). The stall banner surfaces
  // when a stage keeps producing findings but its gate won't pass. `runFold.test.ts` locks
  // the parity fixes (gate-clears-on-pass, stall-kept-on-blocked, judge degraded).
  const [runFlags, setRunFlags] = useState<RunFlags>(emptyRunFlags)
  const gateFail = runFlags.gate
  const stalled = runFlags.stall
  // A prominent toast when the worker needs the user — a question (attended) or a
  // merge conflict. Surfaced over everything so the user attends even if scrolled
  // away from the activity panel; dismissable, with a jump-to-answer action.
  const [toast, setToast] = useState<{ kind: 'question' | 'conflict' | 'error' | 'ok'; text: string } | null>(null)
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
  useRunStream(id, project !== 'missing', {
    onSnapshot: (p) => setProject(loopToCodeProject(p)),
    onLifecycle: (event, data) => {
      // The transient gate/stall/judge banners fold through the ONE shared pure reducer
      // (P16) — gate_check clear-on-pass, stall-kept-on-blocked, judge degraded, etc. all
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
        return
      }
      if (event === 'stage_stalled') return
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
      // The precise question lives in the activity-panel banner; the toast grabs
      // attention + offers a jump. (load() below refreshes pending_question.)
      if (event === 'needs_input') {
        setToast({ kind: 'question', text: 'The worker has a question and is waiting for your answer.' })
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
  // chat_status / tool_call / activity_event over the shared WS as it works each
  // cycle. Surface them so a running cycle reads as ALIVE (tool calls, thinking),
  // not a frozen "starting…" between finding writes. Capped, cleared on new finding.
  // Activity is keyed BY SESSION so each task's detail view shows only its own
  // worker's events. The main worker is `loop-<id>` (sequential); parallel task-
  // workers are `loop-<id>-<taskid>` (the unified manager session keys — NOT the old
  // code-<id>, which silently matched nothing post-cutover so the panel stayed dead).
  // We bucket per session and slice the right one per task. Other sessions ignored.
  const [activityBySession, setActivityBySession] = useState<Record<string, ActivityItem[]>>({})
  const workerKey = `loop-${id}`
  const onWs = useCallback((m: WsMessage) => {
    const sess = String(m.data?.session ?? '')
    // accept the main worker AND any of this project's task-workers
    if (sess !== workerKey && !sess.startsWith(`${workerKey}-`)) return
    const push = (item: ActivityItem) => setActivityBySession((m0) => ({ ...m0, [sess]: [...(m0[sess] ?? []), item].slice(-50) }))
    // A repeated status line (the worker pings "Thinking…" many times a cycle) carries
    // no new info — appending each one fills the 50-item buffer with identical noise and
    // EVICTS the real tool calls / narration. Collapse a consecutive duplicate status
    // into the existing trailing one (no-op append) instead of stacking it.
    const pushStatus = (label: string) => setActivityBySession((m0) => {
      const a = m0[sess] ?? []
      const last = a[a.length - 1]
      if (last && last.kind === 'status' && last.label === label) return m0  // dup → skip
      return { ...m0, [sess]: [...a, { kind: 'status' as const, label }].slice(-50) }
    })
    if (m.type === 'chat_status') {
      const sgi = String(m.data.status ?? ''); if (sgi) pushStatus(sgi)
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
  }, [workerKey])
  useChatSocket(onWs)

  // After a queue/unqueue mutation, refetch the project (queued_task_ids) + bump
  // the task rail so the new queued/ready/blocked states render immediately.
  const refetchTasks = useCallback(() => { load(); setTasksNonce((n) => n + 1) }, [load])

  if (project === null) {
    return <Shell title="Project" onBack={onBack}><Centered>
      {loadErr ? (
        <div className="flex max-w-[360px] flex-col items-center gap-3 px-6 text-center">
          <AlertTriangle size={28} style={{ color: 'var(--color-warn)' }} />
          <div>
            <p data-type="title-m" className="text-on-surface">Couldn't load this project</p>
            <p className="mt-1 text-on-surface-low text-[0.8125rem]">{loadErr} — retrying…</p>
          </div>
          <Button variant="secondary" onClick={() => load()}><RotateCcw size={15} /> Try again</Button>
        </div>
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
          <p className="mt-1 text-on-surface-low text-[0.8125rem]">It may have been deleted, or the link is stale.</p>
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

  async function act(action: 'start' | 'pause' | 'resume' | 'stop') {
    if (acting) return  // ignore a double-click while a prior action is in flight
    // A brownfield draft saved without a workspace can't start until one is set —
    // surface the picker instead of a silent 422 (resumes the C22 draft flow).
    if (action === 'start' && p.project_kind === 'brownfield' && !ws) { setPickWs(true); return }
    // Surface a failed action (e.g. start re-validation 422 — workspace/agent went
    // away, spec invalid) instead of silently reverting via load(). The backend's
    // error message is the actionable reason; without this the user clicks Start and
    // nothing visibly happens.
    setActing(true)
    try { setProject(loopToCodeProject(await api.uLoopAction(id, action))); setToast(null) }
    catch (e) {
      setToast({ kind: 'error', text: `Couldn't ${action} this project: ${(e as Error).message || 'unknown error'}` })
      load()
    } finally { setActing(false) }
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
    // A bound (brownfield) workspace_dir is external + left untouched; a greenfield
    // project with no bound dir keeps its files in the loop's own managed folder,
    // which delete DESTROYS (store.delete rmtree's the loop dir). Don't promise
    // "files left untouched" there — it silently loses the generated code.
    const body = `${['running', 'planning', 'intake'].includes(p.status)
      ? `"${p.name}" is still working — deleting it stops the worker and removes its plan. `
      : `"${p.name}" and its plan will be removed. `}${p.workspace_dir
      ? 'Your workspace folder and its files are left untouched.'
      : 'This project keeps its files in its own managed folder — deleting it also removes those files. Move anything you want to keep out first.'}`
    if (!(await confirm({ title: `Delete project "${p.name}"?`, body, danger: true, confirmLabel: 'Delete' }))) return
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
            <button type="button" onClick={() => onOpenProject((p.tasks_project_id || p.project_id) as string)}
              title="Back to project" aria-label="Back to the project this loop belongs to"
              className="shrink-0 grid place-items-center size-7 rounded-md text-on-surface-low hover:text-on-surface hover:bg-surface-high transition-colors">
              <ChevronLeft size={18} />
            </button>
          )}
          <Code2 size={18} className="shrink-0 text-primary" />
          {editingTitle ? (
            <input autoFocus value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={commitRename} onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); else if (e.key === 'Escape') abortRename() }}
              aria-label="Rename project"
              className="min-w-[14rem] h-7 rounded-md bg-surface-high px-2 text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary/50" />
          ) : (
            <button type="button" onClick={startRename} title="Rename project"
              data-type="title-l" className="truncate text-on-surface text-left hover:text-on-surface-var">
              {p.name}
            </button>
          )}
          {/* StageTrail (phase-execution status) moved OUT of the title row into the
              dedicated status bar below the header (CockpitMeta) — item 14. */}
        </div>}
        right={<HeaderActions>
          {/* Run controls + terminal toggle are primary/default (frequent, contextual);
              everything else (run build/tests, reuse workspace, navigate away, delete)
              is low-priority so the cluster's own overflow menu absorbs it first. */}
          {active
            ? <HeaderControl icon={Pause} label="Pause" priority="primary" onClick={() => act('pause')} disabled={acting} />
            : (p.status === 'paused' || p.status === 'blocked' || p.status === 'needs_input' || p.status === 'failed' || p.status === 'stagnant')
              ? <HeaderControl icon={Play} label="Resume" priority="primary" onClick={() => act('resume')} variant="primary" disabled={acting} />
              : (p.status === 'ready' || p.status === 'review')
                ? <HeaderControl icon={Play} label="Start" priority="primary" onClick={() => act('start')} variant="primary" disabled={acting} />
                : null}
          {/* Stop is TERMINAL + non-resumable — confirm first, like Delete. `stagnant`
              is an active attention state (resume + stop both valid). */}
          {(active || ['paused', 'blocked', 'needs_input', 'stagnant'].includes(p.status)) &&
            <HeaderControl icon={Square} label="Stop" priority="primary" onClick={async () => {
              if (await confirm({ title: 'Stop this run?', body: "Stopping ends the project for good — it can't be resumed afterward (you'd start a new one). Work already written to the workspace is kept. Pause instead if you just want to step in.", danger: true, confirmLabel: 'Stop' })) act('stop')
            }} />}
          {/* A shell only makes sense in a real codebase (gate on `ws`). */}
          {!!ws && <HeaderControl icon={TerminalSquare} label={showTerm ? 'Hide terminal' : 'Terminal'} onClick={() => setShowTerm(!showTerm)} active={showTerm} />}
          {/* Run the configured build/test command in the cockpit terminal (only with a
              real workspace + the command configured). Low priority → overflow first. */}
          {!!ws && p.verify_command && <HeaderControl icon={Play} label={`Run build (${cmdLabel(p.verify_command)})`} priority="low" onClick={() => runInWorkspaceTerminal(p.verify_command || '')} />}
          {!!ws && p.test_command && <HeaderControl icon={Play} label={`Run tests (${cmdLabel(p.test_command)})`} priority="low" onClick={() => runInWorkspaceTerminal(p.test_command || '')} />}
          {/* Reuse this project's BOUND workspace for a fresh target — only with a real
              codebase dir + not mid-run. */}
          {onNewTarget && !active && !!ws && <HeaderControl icon={Target} label="New target" priority="low" onClick={() => onNewTarget(ws)} />}
          <HeaderControl icon={ListChecks} label="All projects" priority="low" onClick={onBack} />
          <HeaderControl icon={Trash2} label="Delete project" danger priority="low" onClick={() => { void del() }} />
        </HeaderActions>} />

      <CockpitMeta project={p} onOpenProject={onOpenProject} />

      {/* Expandable prompt bar (item 14 / Gap 2) — first line collapsed, full on expand. */}
      <CockpitPromptBar prompt={p.task || ''} />

      {/* A ready brownfield draft can't start until a workspace is chosen — make
          that explicit + actionable instead of a silent failed Start. */}
      {p.status === 'ready' && p.project_kind === 'brownfield' && !ws && (
        <motion.div variants={messageEnter} initial="initial" animate="animate"
          className="flex shrink-0 items-center justify-between gap-2 border-b border-outline-variant/40 bg-warn/10 px-l py-2 text-[0.8125rem]"
          style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
          <span>This brownfield project needs a workspace directory before it can start.</span>
          <button type="button" onClick={() => setPickWs(true)} className="shrink-0 rounded-md px-2 py-1 font-medium hover:bg-surface-high">Choose folder</button>
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
            className="flex shrink-0 items-center justify-between gap-2 border-b border-outline-variant/40 px-l py-2 text-[0.8125rem]"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
            <span>The workspace folder <span className="font-mono">{ws.split('/').slice(-1)[0]}</span> no longer exists — re-pick it to continue.</span>
            <button type="button" onClick={() => setPickWs(true)} className="shrink-0 rounded-md px-2 py-1 font-medium hover:bg-surface-high">Re-pick folder</button>
          </motion.div>
        ) : (
          <motion.div variants={messageEnter} initial="initial" animate="animate"
            className="flex shrink-0 items-center gap-2 border-b border-outline-variant/40 px-l py-2 text-[0.8125rem]"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)', color: 'var(--color-warn)' }}>
            <span>The workspace folder <span className="font-mono">{ws.split('/').slice(-1)[0]}</span> no longer exists, so this run can't continue. Its files are gone — Stop or Delete the project, or restore the folder and reopen.</span>
          </motion.div>
        )
      )}

      {/* body: Files (left) | Editor+tabs (center) | Tasks (right). Both side
          panels collapsible + resizable (useResizablePanel persists each). */}
      <div className="flex min-h-0 flex-1">
        <CollapsiblePanel side="left" panelKey="code-left" def={300} min={200} max={460}
          icon={FolderTree} label="Files">
          <FilesRail ws={fileRoot} isProjectDir={!ws} running={active} />
        </CollapsiblePanel>
        <CenterEditor ws={fileRoot} showTerm={showTerm} onCloseTerm={() => setShowTerm(false)} running={active} runCmd={pendingRunCmd} />
        <CollapsiblePanel side="right" panelKey="code-right" def={340} min={260} max={520}
          icon={ListChecks} label="Tasks">
          <RightPanel project={p} onTasksChanged={refetchTasks} tasksNonce={tasksNonce}
            activityBySession={activityBySession} gateFail={gateFail} stalled={stalled} onNudged={load} onStartNew={onStartNew} />
        </CollapsiblePanel>
      </div>

      {pickWs && (
        <WorkspacePicker mode="brownfield" onClose={() => setPickWs(false)} onPick={pickWorkspace} />
      )}
      <AnimatePresence>
        {toast && (
          <CodeToast kind={toast.kind} text={toast.text} onDismiss={() => setToast(null)}
            onRespond={(toast.kind === 'error' || toast.kind === 'ok') ? undefined : () => { setToast(null); window.dispatchEvent(new CustomEvent('ne:code-focus-steer')) }} />
        )}
      </AnimatePresence>
    </div>
  )
}

/** A prominent, dismissable toast (bottom-right) for when the worker needs the
 *  user — an attended question or a merge conflict. Portaled over everything so
 *  it's seen regardless of scroll position; "Respond" focuses the steer box. */
function CodeToast({ kind, text, onDismiss, onRespond }: {
  kind: 'question' | 'conflict' | 'error' | 'ok'; text: string; onDismiss: () => void; onRespond?: () => void
}) {
  // The `ok` toast auto-dismisses (a success confirmation — purely informational,
  // e.g. "saved as artifact"). error / conflict / question PERSIST: each needs the
  // user to read + act/acknowledge, so they stay until dismissed.
  // The timer keys off the toast IDENTITY (kind+text) only — NOT onDismiss, which the
  // parent passes as a fresh arrow each render. A running project re-renders often
  // (SSE/poll); depending on onDismiss restarted the timer every re-render, so a
  // transient toast could outlive its timeout indefinitely. A ref holds the latest
  // onDismiss so the fire-once timer still calls the current closure.
  const dismissRef = useRef(onDismiss); dismissRef.current = onDismiss
  useEffect(() => {
    if (kind !== 'ok') return
    const t = setTimeout(() => dismissRef.current(), 4000)
    return () => clearTimeout(t)
  }, [kind, text])
  const tone = kind === 'error' ? 'var(--color-danger)'
    : kind === 'conflict' ? 'var(--color-warn)' : kind === 'ok' ? 'var(--color-ok)' : 'var(--color-info)'
  const Icon = kind === 'error' ? XCircle : kind === 'conflict' ? AlertTriangle : kind === 'ok' ? CheckCircle2 : HelpCircle
  return createPortal(
    <motion.div role="alert" aria-live="assertive"
      variants={overlayEnter} initial="initial" animate="animate" exit="exit"
      className="fixed bottom-4 right-4 z-[200] w-[360px] max-w-[calc(100vw-2rem)] rounded-xl border border-outline-variant/50 bg-surface-container p-3.5 shadow-lg"
      style={{ borderLeft: `3px solid ${tone}` }}>
      <div className="flex items-start gap-2.5">
        <Icon size={18} className="mt-0.5 shrink-0" style={{ color: tone }} />
        <div className="min-w-0 flex-1">
          <p className="text-on-surface text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 600' }}>
            {kind === 'error' ? "That didn't work" : kind === 'conflict' ? 'Merge conflict — needs you' : kind === 'ok' ? 'Done' : 'The worker needs your input'}
          </p>
          <p className="mt-0.5 text-on-surface-var text-[0.78rem] leading-snug">{text}</p>
          <div className="mt-2 flex items-center gap-2">
            {onRespond && <button type="button" onClick={onRespond}
              className="rounded-md px-2.5 py-1 text-[0.75rem]" style={{ background: tone, color: 'var(--color-on-primary)' }}>Respond</button>}
            <button type="button" onClick={onDismiss}
              className="rounded-md px-2 py-1 text-on-surface-low text-[0.75rem] hover:bg-surface-high">Dismiss</button>
          </div>
        </div>
        <button type="button" onClick={onDismiss} aria-label="Dismiss" className="shrink-0 text-on-surface-low hover:text-on-surface"><X size={14} /></button>
      </div>
    </motion.div>,
    document.body,
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
              <span className="max-w-[7rem] truncate text-[0.7rem]" style={{ color }}>{s.title || s.stage}</span>
            </span>
          )
        })}
      </div>
      {/* compact "stage N/M · title" — always visible (incl. narrow screens where
          the full trail is hidden), so stage context is never lost. Pre-launch it
          shows the plan size as upcoming rather than a misleading "stage 1 active". */}
      <span className="ml-1 inline-flex items-center gap-1 text-[0.7rem] md:hidden"
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
function CockpitMeta({ project: p, onOpenProject }: { project: CodeProject; onOpenProject?: (projectId: string) => void }) {
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
  // Nothing to show → hide the strip entirely. (The prompt lives in its own bar below,
  // so it no longer keeps this strip alive.) StageTrail renders inside, but for a bare
  // pre-run draft with no stages/workspace/cycles/project there's nothing worth a bar.
  if (!wsBase && !showProj && !cyclesText && !elapsedText) return null
  return (
    <div className="flex shrink-0 items-center gap-3 border-b border-outline-variant/40 bg-surface-low/30 px-l py-1 text-[0.7rem] text-on-surface-low">
      {/* Elapsed leads at the far LEFT — "how long has this been running" is the first
          thing to read on the strip. */}
      {elapsedText && (
        <span className="inline-flex shrink-0 items-center gap-1" title="Elapsed run time">
          <Clock size={11} className="shrink-0 opacity-70" />
          <span className="font-mono">{elapsedText}</span>
        </span>
      )}
      {/* Phase-execution status — moved here from beside the title (item 14) so the
          status bar is the single place to read where the run is. */}
      <StageTrail project={p} />
      {/* Workspace · project · cycles all float to the far RIGHT edge (ml-auto pushes the
          whole group). The project chip is interactive (deep-links to the Project). */}
      {(wsBase || showProj || cyclesText) && (
        <span className="ml-auto inline-flex min-w-0 shrink-0 items-center gap-3">
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
              title={cap > 0 ? `${cycles} of a ${cap}-cycle budget run` : `${cycles} cycles run (uncapped)`}>
              <Repeat size={11} className="shrink-0 opacity-70" />
              <span className="font-mono">{cyclesText}</span>
            </span>
          )}
        </span>
      )}
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
  const { width, collapsed, setCollapsed, onHandleDown, onHandleKey } = useResizablePanel(panelKey, { def, min, max, side })
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
        <span className="inline-flex items-center gap-1.5 text-on-surface-var text-[0.8125rem]" style={{ fontVariationSettings: '"wght" 550' }}>
          <Icon size={14} /> {label}
        </span>
        <button type="button" onClick={() => setCollapsed(true)} aria-label={`Collapse ${label}`} title={`Collapse ${label}`}
          className="grid size-6 place-items-center rounded-md text-on-surface-low hover:bg-surface-high hover:text-on-surface">
          {side === 'left' ? <PanelLeftClose size={14} /> : <PanelRightClose size={14} />}
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      {handle}
    </div>
  )
}

// ── left rail: file tree + git changes (Tasks moved to the right panel) ──

function FilesRail({ ws, isProjectDir, running }: { ws: string; isProjectDir: boolean; running: boolean }) {
  const [tab, setTab] = useState<'files' | 'changes'>('files')
  // A light, rail-level git-status read JUST for the Changes-tab count badge — so the
  // user sees review-worthy changes are waiting while they're on the Files tab (the
  // ChangesPanel, which owns the full status + history, is unmounted then). A
  // no-workspace project (engine dir) or a non-repo workspace has no tracked changes,
  // so skip the fetch there. Refreshes on the same triggers the panels use: a slow
  // poll while the worker runs + a manual editor save.
  // Only fetch the badge git-status while the FILES tab is showing (the only time the
  // badge renders). On the Changes tab the ChangesPanel already fetches the full status,
  // so a second badge fetch there is pure duplication — gate it to null so the two don't
  // both spawn a `git status` subprocess every poll. (Skipped too for a no-workspace /
  // non-repo project, which has no tracked changes.)
  const gitWs = (isProjectDir || tab !== 'files') ? null : (ws || null)
  const [badgeNonce, setBadgeNonce] = useState(0)
  const { statuses: badgeStatuses } = useGitStatus(gitWs, badgeNonce)
  const changeCount = Object.keys(badgeStatuses).length
  useVisiblePoll(() => { if (gitWs) setBadgeNonce((n) => n + 1) }, running && gitWs ? 8000 : null)
  useEffect(() => {
    const onSaved = () => { if (gitWs) setBadgeNonce((n) => n + 1) }
    window.addEventListener('ne:code-file-saved', onSaved)
    return () => window.removeEventListener('ne:code-file-saved', onSaved)
  }, [gitWs])
  // ⌘P switches to the Files tab so the finder is visible when the event arrives. The
  // KEYBIND itself lives at cockpit level (always mounted, so it fires even when this
  // whole rail is collapsed); here we ensure the Files tab is showing. If we were on the
  // CHANGES tab, FileFinder isn't mounted yet when this focus-find fires, so its own
  // listener misses it — re-dispatch on the next tick (after the tab switch mounts it) so
  // the finder actually focuses. The ref avoids re-dispatching when already on Files
  // (FileFinder caught the original) → no double-fire.
  const tabRef = useRef(tab); tabRef.current = tab
  useEffect(() => {
    const onFocusFind = () => {
      if (tabRef.current !== 'files') {
        setTab('files')
        setTimeout(() => window.dispatchEvent(new CustomEvent('ne:code-focus-find')), 0)
      }
    }
    window.addEventListener('ne:code-focus-find', onFocusFind)
    return () => window.removeEventListener('ne:code-focus-find', onFocusFind)
  }, [])
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
          : <ChangesPanel ws={ws} running={running} isProjectDir={isProjectDir} />}
      </div>
    </div>
  )
}

/** Fuzzy find-file-by-name within the workspace, surfaced above the cockpit file tree.
 *  Debounced; a result opens in the center editor via the same event the tree uses. */
function FileFinder({ ws }: { ws: string }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<{ path: string; name: string }[]>([])
  const [open, setOpen] = useState(false)
  // Keyboard-highlighted result for ↑/↓ navigation (quick-open expectation). Reset to
  // the top hit on each new result set.
  const [hi, setHi] = useState(0)
  // `searching` gates the dropdown's empty state so a slow query doesn't flash a
  // premature "No files match" over the PRIOR results before the new ones land.
  const [searching, setSearching] = useState(false)
  // Debounce + sequence-guard the search so fast typing doesn't apply out-of-order
  // results (a slow earlier query resolving after a faster later one).
  const seq = useRef(0)
  useEffect(() => {
    const needle = q.trim()
    if (needle.length < 2) { setResults([]); setSearching(false); return }
    const mine = ++seq.current
    setSearching(true)
    const t = setTimeout(() => {
      api.fileSearch(needle, ws).then((r) => {
        if (mine === seq.current) { setResults((r.results || []).map((x) => ({ path: x.path, name: x.name }))); setHi(0); setOpen(true); setSearching(false) }
      }).catch(() => { if (mine === seq.current) { setResults([]); setSearching(false) } })
    }, 200)
    return () => clearTimeout(t)
  }, [q, ws])
  const openFile = (r: { path: string; name: string }) => {
    window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name: r.name, path: r.path, is_dir: false } }))
    setQ(''); setResults([]); setOpen(false)
  }
  // Dismiss the results dropdown on an outside click — else it floats over the tree
  // after the user clicks away (e.g. to pick a tree file instead) with no way to close
  // it but Escape/clear. Pointerdown so it closes before the click lands on the tree.
  const rootRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => { if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [open])
  // ⌘P focus signal from FilesRail — focus + select so the user can immediately type
  // (or retype over a prior query).
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    const onFocusFind = () => { inputRef.current?.focus(); inputRef.current?.select() }
    window.addEventListener('ne:code-focus-find', onFocusFind)
    return () => window.removeEventListener('ne:code-focus-find', onFocusFind)
  }, [])
  return (
    <div ref={rootRef} className="relative shrink-0 border-b border-outline-variant/40 px-2 py-1.5">
      <div className="flex items-center gap-1.5 rounded-md bg-surface-high px-2 py-1">
        <Search size={13} className="shrink-0 text-on-surface-low" />
        <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} onFocus={() => results.length && setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown' && results.length) { e.preventDefault(); setOpen(true); setHi((i) => Math.min(results.length - 1, i + 1)) }
            else if (e.key === 'ArrowUp' && results.length) { e.preventDefault(); setHi((i) => Math.max(0, i - 1)) }
            else if (e.key === 'Enter' && results.length) { e.preventDefault(); openFile(results[Math.min(hi, results.length - 1)]) }
            else if (e.key === 'Escape') { setQ(''); setResults([]); setOpen(false) }
          }}
          placeholder="Find file by name…  ⌘P" spellCheck={false} autoCapitalize="off" autoCorrect="off"
          className="min-w-0 flex-1 bg-transparent text-[0.78rem] text-on-surface outline-none placeholder:text-on-surface-low" />
        {q && <button type="button" onClick={() => { setQ(''); setResults([]); setOpen(false) }} aria-label="Clear" className="shrink-0 text-on-surface-low hover:text-on-surface"><X size={12} /></button>}
      </div>
      {open && q.trim().length >= 2 && (
        <div className="absolute inset-x-2 z-20 mt-1 max-h-[40vh] overflow-y-auto rounded-md border border-outline-variant/50 bg-surface-container shadow-lg">
          {results.length === 0 ? (
            // While a query is in flight, show "Searching…" rather than a premature
            // "No files match" (which would flash over the prior results until the new
            // ones land + read as a false empty).
            searching
              ? <p className="inline-flex items-center gap-1.5 px-2.5 py-2 text-on-surface-low text-[0.75rem]"><Loader2 size={11} className="animate-spin" /> Searching…</p>
              : <p className="px-2.5 py-2 text-on-surface-low text-[0.75rem]">No files match “{q.trim()}”.</p>
          ) : results.map((r, i) => {
            // fileSearch returns REALPATH-resolved paths (file_index realpaths the root),
            // so a naive startsWith(ws) fails on a symlinked workspace root (/tmp vs
            // /private/tmp on macOS) → it fell back to the bare basename, losing the
            // subdir context that's the whole point of showing a rel path in quick-open
            // (two `config.py`s become indistinguishable). Relativize by the workspace's
            // LAST segment, like the Changes panel + canonPath do.
            const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
            const marker = `/${wsBase}/`
            const mi = wsBase ? r.path.lastIndexOf(marker) : -1
            const rel = mi >= 0 ? r.path.slice(mi + marker.length) : (r.path.startsWith(ws) ? r.path.slice(ws.replace(/\/$/, '').length + 1) : r.name)
            return (
              <button key={r.path} type="button" onClick={() => openFile(r)} onMouseEnter={() => setHi(i)}
                ref={(el) => { if (i === hi && open) el?.scrollIntoView({ block: 'nearest' }) }}
                className={`flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-[0.78rem] ${i === hi ? 'bg-surface-high' : 'hover:bg-surface-high'}`}>
                <FileCode size={11} className="shrink-0 text-on-surface-low" />
                <span className="min-w-0 truncate text-on-surface-var" title={rel}>{rel}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── right panel: Tasks (agent loop events live IN TASK SCOPE — under each task
//    card — not a separate global feed) + project-level banners + a steer box. ──

function RightPanel({ project, onTasksChanged, tasksNonce, activityBySession, gateFail, stalled, onNudged, onStartNew }: {
  project: CodeProject; onTasksChanged: () => void; tasksNonce: number
  activityBySession: Record<string, ActivityItem[]>; gateFail: { label: string; command: string; output: string } | null
  stalled: { stage: string; title: string; findings: number } | null; onNudged: () => void; onStartNew?: () => void
}) {
  // Navigable Tasks panel: a list view (all tasks, grouped by stage) and a per-task
  // DETAIL view (task plan + its agent loop events + a task-scoped steer box). The
  // selected task makes "what is the user steering" unambiguous for both sides.
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  // The needs-input toast's "Respond" dispatches ne:code-focus-steer, which only the
  // project-level ProjectFooter steer box listens for. If a TASK is open, that footer
  // is unmounted (the detail view replaces it) → "Respond" focused nothing (dead click).
  // A project-level question belongs in the project footer anyway, so clear the
  // selection here first; the footer then mounts. The footer's OWN focus listener is
  // added on mount, AFTER this event already fired, so re-dispatch once on the next
  // tick for it to catch. Only when a task WAS open (else the footer is already mounted
  // and caught the original event — no re-dispatch needed, avoids a double-focus).
  const selectedRef = useRef<string | null>(selectedTaskId); selectedRef.current = selectedTaskId
  useEffect(() => {
    const onFocusSteer = () => {
      if (selectedRef.current !== null) {
        setSelectedTaskId(null)
        setTimeout(() => window.dispatchEvent(new CustomEvent('ne:code-focus-steer')), 0)
      }
    }
    window.addEventListener('ne:code-focus-steer', onFocusSteer)
    return () => window.removeEventListener('ne:code-focus-steer', onFocusSteer)
  }, [])
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
    Promise.all(lists.map((lid) => api.tasks({ task_list: lid, limit: 200 }).then((r) => [lid, r.tasks] as const)
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
  // stage id ("implementation") OR the stage title ("Implement greet.py …") depending
  // on how it was recorded — resolve either to the stage's task list.
  const stageTasksFor = (fstage: string): TaskItem[] => {
    const s = stages.find((sg) => sg.stage === fstage || sg.title === fstage)
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
        <div role="alert" className="mx-2 mt-2 flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[0.75rem]"
          style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)', color: 'var(--color-warn)' }}>
          <AlertTriangle size={12} className="shrink-0" />
          <span className="min-w-0 flex-1">Couldn't refresh some tasks — showing the last known list.</span>
          <button type="button" onClick={refresh} className="shrink-0 inline-flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-warn/15"><RotateCcw size={11} /> Retry</button>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto">
        <StageTasks project={project} onTasksChanged={onTasksChanged} loading={loading}
          tasksByList={tasksByList} onSelect={setSelectedTaskId} activeTaskIds={activeTaskIds}
          mainActivity={activityBySession[`loop-${project.id}`] ?? []} />
      </div>
      {/* Project-level signals + steer box (list view): not task-scoped. */}
      <ProjectFooter project={project} gateFail={gateFail} stalled={stalled} onNudged={onNudged} onStartNew={onStartNew} />
    </div>
  )
}

function RailTab({ icon: Icon, label, on, onClick, badge }: { icon: typeof ListChecks; label: string; on: boolean; onClick: () => void; badge?: number }) {
  return (
    <button type="button" onClick={onClick}
      className="inline-flex h-8 items-center gap-1.5 rounded-md px-2.5 text-[0.8125rem] transition-colors"
      style={on ? { background: 'var(--color-surface-high)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
      <Icon size={14} /> {label}
      {/* Pending-change count — lets the user see review-worthy changes are waiting
          while they're on another tab (a worker editing files updates this live). */}
      {!!badge && badge > 0 && (
        <span className="ml-0.5 inline-flex min-w-[1.1rem] items-center justify-center rounded-full px-1 text-[0.65rem] tabular-nums"
          style={{ background: on ? 'var(--color-primary)' : 'color-mix(in srgb, var(--color-primary) 22%, transparent)', color: on ? 'var(--color-on-primary)' : 'var(--color-primary)' }}>
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </button>
  )
}

/** Per-stage task groups — one group per stage's TaskList (the Tasks Project). */
// A task's execution state, derived from its status + dependencies + the project
// queue. Drives the row's icon, label, and which action (Queue / queued / blocked)
// it offers. This is the user-facing model of the task-driven scheduler.
// A task that's reached a TERMINAL status — done/completed OR cancelled. Used to
// build the "resolved" set that ungates dependents + the done-count, matching the
// backend (tasks/reconcile: a cancelled blocker is resolved). A cancelled task must
// NOT keep its dependents blocked forever.
const isTerminalTask = (t: TaskItem): boolean =>
  t.status === 'done' || t.status === 'completed' || t.status === 'cancelled'

type ExecState = 'done' | 'cancelled' | 'running' | 'queued' | 'blocked' | 'ready' | 'waiting'
// `stageActive` defaults true so callers that don't pass it (e.g. the task detail
// view) keep the prior behavior. A queued/ready task in a NOT-yet-active stage is
// 'waiting' — the phase barrier holds it until its stage opens, even if its own
// deps are satisfied; surfacing that avoids the user thinking it'll run now.
function execState(t: TaskItem, doneIds: Set<string>, queued: Set<string>, stageActive = true, knownIds?: Set<string>): ExecState {
  if (t.status === 'done' || t.status === 'completed') return 'done'
  // Cancelled is terminal (TERMINAL_STATUSES) — must NOT fall through to ready/queued,
  // which would wrongly invite the user to queue work that's been cancelled.
  if (t.status === 'cancelled') return 'cancelled'
  if (t.status === 'in_progress') return 'running'
  const deps = (t.dependencies?.map((d) => d.depends_on_task_id) ?? t.depends_on ?? [])
  // A dep blocks only if it's a KNOWN task that isn't terminal. An edge to an unknown id
  // (pruned in a re-plan, or a cross-stage target not in this set) is NOT an actionable
  // blocker — and the TaskDetailView's blocker LIST already drops it (byId.has filter).
  // Counting it here too made a task read 'blocked' with an EMPTY blocker list — a
  // dead-end the user can't act on + that never resolves. When knownIds is given, treat
  // an unknown dep as resolved so the row-state matches the detail view exactly.
  const blocked = deps.some((id) => !!id && !doneIds.has(id) && (!knownIds || knownIds.has(id)))
  if (blocked) return 'blocked'
  if (!stageActive) return 'waiting'  // phase barrier: its stage hasn't opened yet
  return queued.has(t.id) ? 'queued' : 'ready'
}

function StageTasks({ project, onTasksChanged, loading, tasksByList, onSelect, activeTaskIds, mainActivity }: {
  project: CodeProject; onTasksChanged: () => void; loading: boolean
  tasksByList: Record<string, TaskItem[]>; onSelect: (taskId: string) => void; activeTaskIds: Set<string>
  mainActivity: ActivityItem[]
}) {
  const links = project.task_list_ids ?? {}
  const stages = project.stage_plan ?? []
  const [busy, setBusy] = useState(false)
  const noLists = !Object.keys(links).length
  const isPreLaunch = project.status === 'ready' || project.status === 'review'
  const queuedSet = new Set(project.queued_task_ids ?? [])
  const allTasks: TaskItem[] = Object.values(tasksByList).flat()
  const doneIds = new Set(allTasks.filter(isTerminalTask).map((t) => t.id))
  // Every known task id — so execState can tell a real (still-pending) blocker from a
  // dependency edge to a pruned/cross-stage id that no longer exists (the latter isn't
  // a blocker we can show or act on; counting it left a task 'blocked' with no listed
  // blocker). Matches the TaskDetailView blocker-list's byId filter.
  const knownIds = new Set(allTasks.map((t) => t.id))
  const queueable = allTasks.filter((t) => !doneIds.has(t.id) && !queuedSet.has(t.id))
  const runningCount = allTasks.filter((t) => t.status === 'in_progress').length

  async function queue(ids: string[]) {
    if (!ids.length || busy) return
    setBusy(true)
    // Surface a failed queue (worker gone, backend error) via the cockpit toast —
    // a silent finally{} left the button un-busying with no sign nothing queued.
    try { await api.uLoopQueue(project.id, ids, 'queue'); onTasksChanged() }
    catch (e) { window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't queue ${ids.length > 1 ? 'those tasks' : 'that task'}: ${(e as Error).message || 'unknown error'}` } })) }
    finally { setBusy(false) }
  }
  const autopilot = project.autopilot !== false
  async function toggleAutopilot() {
    if (busy) return
    setBusy(true)
    try { await api.uLoopAutopilot(project.id, !autopilot); onTasksChanged() }
    catch (e) { window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't switch drive mode: ${(e as Error).message || 'unknown error'}` } })) }
    finally { setBusy(false) }
  }

  if (loading) return <Centered><Loader2 size={18} className="animate-spin text-on-surface-low" /></Centered>
  if (noLists && !stages.length) {
    // No task lists AND no stage plan. The message must match the project's actual
    // state: pre-launch is genuinely "not started yet", but a RUNNING/active project
    // with no breakdown is working straight off its brief (minimal intake / no
    // decomposition) — saying "once launched" there contradicts the live worker.
    // For a RUNNING no-breakdown project, render the worker's live activity right here
    // — there's no task card to host it, so the old "watch its activity on the right"
    // copy pointed at an empty panel (the activity was collected but never shown).
    if (!isPreLaunch && !TERMINAL_STATUSES.has(project.status) && mainActivity.length > 0) {
      return (
        <div className="flex flex-col gap-2 p-3">
          <p className="text-on-surface-low text-[0.75rem]">No task breakdown for this run — the worker is operating directly from the brief.</p>
          <div className="rounded-lg border border-primary/30 bg-primary/5 p-2.5">
            <div className="mb-1.5 inline-flex items-center gap-1.5 text-[0.7rem] text-primary"><Loader2 size={11} className="animate-spin" /> working now</div>
            <div className="flex flex-col gap-1">
              {mainActivity.map((it, i) => (
                it.kind === 'say'
                  ? <p key={i} className="whitespace-pre-wrap text-[0.78rem] leading-snug text-on-surface-var">{it.label}</p>
                  : <div key={i} className="flex items-start gap-1.5 text-[0.75rem] text-on-surface-low">
                      {it.kind === 'tool' ? <Wrench size={11} className="mt-0.5 shrink-0" /> : <Activity size={11} className="mt-0.5 shrink-0" />}
                      <span className="min-w-0"><span className="text-on-surface-var">{it.label}</span>{it.detail && <span className="text-on-surface-low/70"> · {it.detail.slice(0, 60)}</span>}</span>
                    </div>
              ))}
            </div>
          </div>
        </div>
      )
    }
    const msg = isPreLaunch
      ? 'Tasks appear here once the project is launched.'
      : TERMINAL_STATUSES.has(project.status)
        ? 'This project ran without a task breakdown — see the activity and workspace files for what it did.'
        : 'No task breakdown for this run — the worker is operating directly from the brief. Watch its activity below as it works.'
    return <p className="px-3 py-6 text-center text-on-surface-low text-[0.8125rem]">{msg}</p>
  }
  return (
    <div className="flex flex-col gap-1 p-2">
      {!noLists && (
        <div className="flex items-center justify-between gap-2 px-2 pb-1">
          <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.7rem]">
            {runningCount > 0 && (
              <span className="inline-flex items-center gap-1 text-primary" title="Tasks running in parallel worktrees">
                <Loader2 size={10} className="animate-spin" />{runningCount} running
              </span>
            )}
            {runningCount > 0 && (queuedSet.size > 0 || queueable.length > 0) && <span className="opacity-40">·</span>}
            {queuedSet.size > 0 && <span>{queuedSet.size} queued</span>}
            {queueable.length > 0 && <span className="opacity-70">{queueable.length} can queue</span>}
          </span>
          <div className="flex items-center gap-1.5">
            {/* Autopilot drives the phased plan itself; one-by-one hands queueing to
                the user. Toggle is live (any non-terminal state). */}
            <button type="button" disabled={busy} onClick={toggleAutopilot} aria-pressed={autopilot} aria-label="Autopilot"
              title={autopilot ? 'Autopilot on — the system queues + drives the phased tasks. Click for one-by-one.' : 'One-by-one — you queue tasks yourself. Click to let the system drive.'}
              className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[0.7rem] transition-colors disabled:opacity-50 ${autopilot ? 'bg-primary/15 text-primary hover:bg-primary/25' : 'text-on-surface-low hover:bg-surface-high'}`}>
              {autopilot ? <Rocket size={11} /> : <Hand size={11} />} {autopilot ? 'Autopilot' : 'One-by-one'}
            </button>
            {/* Manual Queue all only matters in one-by-one mode (autopilot auto-queues). */}
            {!autopilot && queueable.length > 0 && (
              <button type="button" disabled={busy} onClick={() => queue(queueable.map((t) => t.id))}
                className="inline-flex items-center gap-1 rounded-md bg-primary/15 px-2 py-1 text-[0.7rem] text-primary hover:bg-primary/25 disabled:opacity-50">
                <Play size={11} /> Queue all
              </button>
            )}
          </div>
        </div>
      )}
      {noLists && isPreLaunch && (
        <p className="px-2 pb-1 text-on-surface-low text-[0.7rem]">Planned — provisioned when you launch.</p>
      )}
      {stages.map((s, si) => {
        const key = stageKey(s)
        const lid = links[key]
        const tasks: TaskItem[] = (lid && tasksByList[lid])
          || (noLists ? (s.tasks ?? []).map((t, i) => ({ id: `plan-${key}-${i}`, title: t.title, status: 'open' } as TaskItem)) : [])
        const st = project.stage_status?.[key] ?? 'pending'
        return (
          // Always suffix the index: two stages can share an effective key (stage ||
          // title). The backend dedupes TaskLists by key at LAUNCH, but the plan array
          // can still hold both rows for a project created via the chat tool / API
          // (which bypass Plan Review's dup-guard) — `key || si` then emitted the SAME
          // React key for both, a duplicate-key warning + reconciliation glitch. The
          // index keeps each row's React identity unique regardless.
          <StageGroup key={`${key}:${si}`} stage={s} status={st} tasks={tasks}
            preview={noLists} doneIds={doneIds} queuedSet={queuedSet} knownIds={knownIds}
            onSelect={onSelect} activeTaskIds={activeTaskIds}
            attention={['blocked', 'needs_input', 'stagnant', 'failed', 'stopped'].includes(project.status)} />
        )
      })}
    </div>
  )
}

/** One stage's task group: an (optional) expandable objective + exit-criteria
 *  header, then its task rows. Rows NAVIGATE to the task detail view on click. */
function StageGroup({ stage: s, status: st, tasks, preview, doneIds, queuedSet, knownIds, onSelect, activeTaskIds, attention = false }: {
  stage: CodeStage; status: string; tasks: TaskItem[]; preview: boolean
  doneIds: Set<string>; queuedSet: Set<string>; knownIds: Set<string>; onSelect: (taskId: string) => void; activeTaskIds: Set<string>; attention?: boolean
}) {
  // The active stage's header reads primary (in-flight) — but when the PROJECT is in an
  // attention state (blocked/needs_input/stagnant/failed/stopped) it's parked AT this
  // stage, not progressing, so warn-tone it to agree with the StageTrail + status pill.
  const color = st === 'active' ? (attention ? 'var(--color-warn)' : 'var(--color-primary)')
    : st === 'done' ? 'var(--color-ok)' : 'var(--color-on-surface-low)'
  // A stage is "open" for execution when it's the active one (or already done — its
  // tasks just show done). A pending (not-yet-reached) stage holds its tasks behind
  // the phase barrier → their rows read 'waiting' rather than 'queued'/'ready'.
  const stageOpen = st === 'active' || st === 'done'
  // The stage's objective + exit criteria — shaped in Plan Review — were dropped at
  // execution time (header showed only the title). Surface them behind an expandable
  // header so the user can see what the stage is FOR and how it clears, without
  // cluttering the rail by default. Auto-open the active stage (the one in play).
  const objective = (s.objective || '').trim()
  const criteria = (s.exit_criteria ?? []).filter((c) => (c || '').trim())
  const hasDetail = !!objective || criteria.length > 0
  const [open, setOpen] = useState(st === 'active')
  // Auto-open when this stage BECOMES active during a live run (pending→active as the
  // worker advances) — not just on mount. Without this the newly-in-play stage stayed
  // collapsed mid-run, hiding the tasks now executing. Fire only on the transition INTO
  // active (track the prev status), so a user who manually collapsed a different stage
  // isn't fought, and a re-render at steady state doesn't re-open what they closed.
  const prevSt = useRef(st)
  const rootRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (st === 'active' && prevSt.current !== 'active') {
      setOpen(true)
      // Also bring the freshly-active stage into view: on a long multi-stage plan the
      // newly-in-play stage can be below the fold, so auto-opening it alone wasn't
      // enough — the user wouldn't see the tasks now executing without scrolling. Only
      // on the real transition (not mount), so opening the cockpit doesn't yank the
      // rail. rAF so it runs after the expand lays out.
      requestAnimationFrame(() => rootRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
    }
    prevSt.current = st
  }, [st])
  // Stage status (active/done/pending) is conveyed only by the header COLOR — invisible
  // to a screen reader, which would hear just the title + count. Fold it into the
  // accessible name so the stage's state is spoken (e.g. "Implementation — active, 3 tasks").
  const stLabel = st === 'active' ? 'active' : st === 'done' ? 'done' : 'pending'
  const a11yLabel = `${s.title || s.stage} — ${stLabel}, ${tasks.length} task${tasks.length === 1 ? '' : 's'}`
  return (
    <div ref={rootRef} className="rounded-lg">
      {hasDetail ? (
        // Morph the objective/exit-criteria open in place (height+opacity), rather than
        // popping the block in/out — the SAME stage row grows to reveal its detail
        // (§Goal "morph, don't mount"). Reduced-motion degrades to an instant swap.
        <Expandable open={open} header={
          <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} aria-label={a11yLabel}
            className="flex w-full items-center gap-1.5 px-2 py-1.5 text-[0.75rem] uppercase tracking-wide hover:bg-surface-high/40" style={{ color }}>
            <motion.span animate={{ rotate: open ? 90 : 0 }} transition={springs.snappy} className="shrink-0">
              <ChevronRight size={12} />
            </motion.span>
            <span className="truncate">{s.title || s.stage}</span>
            <span className="opacity-60">({tasks.length})</span>
          </button>
        }>
          <div className="mb-1 ml-4 flex flex-col gap-1.5 border-l border-outline-variant/40 pl-2.5">
            {objective && <p className="text-on-surface-var text-[0.78rem] leading-snug normal-case">{objective}</p>}
            {criteria.length > 0 && (
              <div className="flex flex-col gap-0.5">
                <span className="text-on-surface-low/70 text-[0.62rem] uppercase tracking-wide">Done when</span>
                {criteria.map((c, i) => (
                  <div key={i} className="flex items-start gap-1.5 text-on-surface-low text-[0.75rem] leading-snug normal-case">
                    <Target size={9} className="mt-[3px] shrink-0 opacity-60" /><span>{c}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </Expandable>
      ) : (
        <div role="group" aria-label={a11yLabel}
          className="flex w-full items-center gap-1.5 px-2 py-1.5 text-[0.75rem] uppercase tracking-wide" style={{ color }}>
          <span className="truncate">{s.title || s.stage}</span>
          <span className="opacity-60">({tasks.length})</span>
        </div>
      )}
      {tasks.length === 0
        ? <p className="px-3 pb-2 text-on-surface-low/70 text-[0.75rem]">no tasks yet</p>
        // Cascade the rows in on first paint (stagger + rise/fade) so a stage's tasks
        // read as arriving, not popping together. Keyed by task id, so the poll/SSE
        // refresh (a re-render, not a remount) never re-fires it — only a genuinely new
        // task animates in. Framer's `initial` runs once per mounted key.
        : <motion.div variants={{ animate: { transition: stagger() } }} initial="initial" animate="animate">
            {tasks.map((t) => (
              <motion.div key={t.id} variants={listItemEnter}>
                <TaskRow task={t} preview={preview}
                  state={execState(t, doneIds, queuedSet, stageOpen, knownIds)} active={activeTaskIds.has(t.id)} onSelect={() => onSelect(t.id)} />
              </motion.div>
            ))}
          </motion.div>}
    </div>
  )
}

const STATE_ICON: Record<ExecState, typeof Circle> = {
  // `ready` (the actionable state — queueable right now in one-by-one mode) gets a
  // distinct play-ready glyph so it stands out from the inert/blocked tasks, which
  // shared the bare Circle + the same muted color — making the one row inviting action
  // the LEAST distinguishable. blocked/waiting stay bare circles + muted.
  done: CheckCircle2, cancelled: XCircle, running: Loader2, queued: Clock, blocked: Circle, ready: CirclePlay, waiting: Clock,
}
const STATE_COLOR: Record<ExecState, string> = {
  done: 'var(--color-ok)', cancelled: 'var(--color-on-surface-low)', running: 'var(--color-primary)',
  // ready leans on the on-surface-var (clearer than the muted -low) so it reads as
  // available-to-act, not dormant like blocked/waiting.
  queued: 'var(--color-primary)', blocked: 'var(--color-on-surface-low)', ready: 'var(--color-on-surface-var)',
  waiting: 'var(--color-on-surface-low)',
}
// Human state word for the accessible name. done/ready carry no text badge (icon /
// strikethrough only), so without this a screen reader announces a completed and a
// ready task identically — just the title. The aria-label appends this so each row's
// state is spoken.
const STATE_LABEL: Record<ExecState, string> = {
  done: 'done', cancelled: 'cancelled', running: 'running', queued: 'queued', blocked: 'blocked', ready: 'ready to queue', waiting: 'waiting for its stage',
}

/** A navigable task row — clicking opens the task detail view in the sidebar. A
 *  chevron affords the navigation; a needs-input task flags an attention dot. */
function TaskRow({ task, state, preview, active, onSelect }: {
  task: TaskItem; state: ExecState; preview: boolean; active?: boolean; onSelect: () => void
}) {
  const Icon = STATE_ICON[state]
  // Both terminal states (done + cancelled) read struck-through + muted.
  const done = state === 'done' || state === 'cancelled'
  if (preview) {
    // pre-launch: no detail to navigate to yet, just show the planned row.
    return (
      <div className="flex items-start gap-1.5 px-2 py-1.5 text-[0.8125rem]">
        <Icon size={14} className="mt-0.5 shrink-0" style={{ color: STATE_COLOR[state] }} />
        <span className="min-w-0 flex-1 text-on-surface-var">{task.title}</span>
      </div>
    )
  }
  return (
    <button type="button" onClick={onSelect} aria-label={`${task.title} — ${STATE_LABEL[state]}`}
      className="group flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-[0.8125rem] hover:bg-surface-high/60">
      <Icon size={14} className={`shrink-0 ${state === 'running' ? 'animate-spin' : ''}`} style={{ color: STATE_COLOR[state] }} />
      <span className={`min-w-0 flex-1 truncate ${done ? 'text-on-surface-low line-through' : 'text-on-surface-var'}`}>{task.title}</span>
      {/* a live pulse when THIS task's worker is actively emitting events right now */}
      {active && <span className="reveal-caret size-1.5 shrink-0 rounded-full" style={{ background: 'var(--color-primary)' }} title="Active now" />}
      {state === 'cancelled' && <span className="shrink-0 text-[0.7rem] text-on-surface-low/70">cancelled</span>}
      {state === 'running' && <span className="shrink-0 text-[0.7rem] text-primary">running</span>}
      {state === 'queued' && <span className="shrink-0 text-[0.7rem] text-primary">queued</span>}
      {state === 'blocked' && <span className="shrink-0 text-[0.7rem] text-on-surface-low/70">blocked</span>}
      {state === 'waiting' && <span className="shrink-0 text-[0.7rem] text-on-surface-low/70" title="Waiting for its stage to start">waiting</span>}
      {/* `ready` is the actionable state — a quiet chip (matches the others' pattern)
          so it reads as available-to-queue, not just an unlabeled inert row. */}
      {state === 'ready' && <span className="shrink-0 text-[0.7rem] text-on-surface-low/70" title="Ready to queue">ready</span>}
      <ChevronRight size={13} className="shrink-0 text-on-surface-low opacity-0 transition-opacity group-hover:opacity-100" />
    </button>
  )
}

/** The named, clickable list of a blocked task's unresolved prerequisite tasks —
 *  so "blocked" says WHAT it's waiting on (not a generic "its dependency tasks")
 *  and the user can jump straight to a blocker. Empty → nothing renders. */
function BlockerList({ blockers, onOpenTask }: { blockers: { id: string; title: string }[]; onOpenTask: (id: string) => void }) {
  if (!blockers.length) return null
  return (
    <div className="mt-1 flex flex-col gap-0.5">
      {blockers.map((b) => (
        <button key={b.id} type="button" onClick={() => onOpenTask(b.id)} title={`Open “${b.title}”`}
          className="group/bl inline-flex max-w-full items-center gap-1 self-start rounded px-1 text-on-surface-var hover:text-primary">
          <Circle size={9} className="shrink-0 opacity-60" />
          <span className="truncate">{b.title}</span>
          <ChevronRight size={11} className="shrink-0 opacity-0 transition-opacity group-hover/bl:opacity-100" />
        </button>
      ))}
    </div>
  )
}

/** Task DETAIL view — the per-task scope: back nav, the task's plan + exit
 *  criteria, its agent loop events (findings + live in-flight activity), and a
 *  TASK-SCOPED steer box. Queue / un-queue happens here too, and an attended
 *  question for THIS task is answered right here. */
function TaskDetailView({ project, task, doneIds, stageOpen, knownIds, findings, liveActivity, blockers, onOpenTask, onBack, onChanged }: {
  project: CodeProject; task: TaskItem; doneIds: Set<string>; stageOpen: boolean; knownIds: Set<string>; findings: CodeFinding[]; liveActivity: ActivityItem[]
  blockers: { id: string; title: string }[]; onOpenTask: (taskId: string) => void
  onBack: () => void; onChanged: () => void
}) {
  const state = execState(task, doneIds, new Set(project.queued_task_ids ?? []), stageOpen, knownIds)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  // A long-running task accumulates many cycle findings; rendering every FindingCard
  // (summary + insight + files + collapsible evidence) balloons the activity column.
  // Show the most RECENT N by default with a "show earlier" toggle — the latest cycles
  // are what the user wants, and it bounds the DOM. Mirrors the FilesTouched cap pattern.
  const [showAllFindings, setShowAllFindings] = useState(false)
  // Synchronous guard for queue/steer: disabled={busy} + the `busy` state check are
  // async, so a rapid double Enter/click both pass before the re-render → a double
  // nudge/queue (double guidance write + SSE). The ref short-circuits the 2nd call
  // immediately (the C409/C415 acting-guard pattern).
  const actingRef = useRef(false)
  // Auto-grow the task-scoped steer box to fit multi-line input (mirrors the
  // project-level steer box).
  const steerRef = useRef<HTMLTextAreaElement>(null)
  useEffect(() => { autoGrowTextarea(steerRef.current) }, [text])
  // Optimistic echo of the latest task-scoped steer so the user sees it landed —
  // mirrors the project-level steer box (which showed an echo while the task one
  // gave no confirmation at all). Local-only: task nudges aren't in project.nudges.
  const [lastSteer, setLastSteer] = useState<{ text: string; failed?: boolean } | null>(null)
  const queued = (project.queued_task_ids ?? []).includes(task.id)
  const autopilot = project.autopilot !== false
  const done = task.status === 'done' || task.status === 'completed'
  // Cancelled is ALSO terminal (isTerminalTask) — its worker is gone, so the
  // scheduling note + the task-scoped steer box must treat it like a finished task,
  // not fall through to "Queued by autopilot." / a no-op steer box (guidance nothing
  // reads). `done` alone excluded cancelled; gate the terminal surfaces on this.
  const terminal = isTerminalTask(task)
  const running = task.status === 'in_progress'
  const live = running ? liveActivity : []
  const plan = task.action_plan ?? []
  const crit = task.exit_criteria ?? []
  const ws = project.workspace_dir || project.files_dir || ''

  // Auto-scroll the activity column to the newest finding / live line as the worker
  // streams — but only when the user is already near the bottom, so scrolling up to
  // read earlier cycles isn't yanked back down.
  const scrollRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [findings.length, live.length])

  async function queue(action: 'queue' | 'unqueue') {
    if (busy || actingRef.current) return
    actingRef.current = true
    setBusy(true)
    try { await api.uLoopQueue(project.id, [task.id], action); onChanged() }
    catch (e) { window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't ${action === 'queue' ? 'queue' : 'unqueue'} this task: ${(e as Error).message || 'unknown error'}` } })) }
    finally { setBusy(false); actingRef.current = false }
  }
  // Steering scoped to THIS task — prefix the nudge so the worker knows which task
  // the direction applies to (queued → applied when it runs; running → next cycle).
  // `explicit` lets a one-click action (e.g. the needs-input "Use your best judgment"
  // button) steer without using the textarea; otherwise the typed `text` is sent.
  async function steer(explicit?: string) {
    const t = (explicit ?? text).trim()
    if (!t || busy || actingRef.current) return
    actingRef.current = true
    setBusy(true)
    // Scope the steer to THIS task (task_id → guidance_<task_id>.txt for its
    // worker); keep the title prefix so the text is self-describing in the trail.
    // Clear the box only on SUCCESS — a pre-await clear lost the user's text with no
    // error if the send failed (project went terminal mid-send / network / 5xx).
    try {
      await api.uLoopNudge(project.id, `[For task "${task.title}"] ${t}`, task.id)
      if (explicit === undefined) setText('')
      setLastSteer({ text: t }); onChanged()
    } catch (e) {
      setLastSteer({ text: t, failed: true })
      window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't send that steer: ${(e as Error).message || 'unknown error'}` } }))
    } finally { setBusy(false); actingRef.current = false }
  }

  return (
    <div className="flex h-full flex-col">
      {/* header: back + status */}
      <div className="flex shrink-0 items-center gap-1.5 border-b border-outline-variant/40 px-2 py-1.5">
        <button type="button" onClick={onBack} aria-label="Back to tasks"
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[0.75rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface">
          <ChevronLeft size={14} /> Tasks
        </button>
        <span className="ml-auto inline-flex items-center gap-1 text-[0.7rem]" style={{ color: running ? 'var(--color-primary)' : done ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }}>
          {running && <Loader2 size={10} className="animate-spin" />}{running ? 'running' : queued ? 'queued' : done ? 'done' : task.status}
        </span>
      </div>
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto p-3">
        <h3 className="text-on-surface text-[0.9rem]" style={{ fontVariationSettings: '"wght" 600' }}>{task.title}</h3>
        {task.description && <p className="mt-1 text-on-surface-var text-[0.8125rem] leading-snug">{task.description}</p>}

        {/* attended question for THIS task — answer in the steer box below */}
        {project.status === 'needs_input' && project.pending_question?.question && (
          <div className="mt-3 rounded-lg p-2.5 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={{ color: 'var(--color-info)', fontVariationSettings: '"wght" 550' }}>
              <HelpCircle size={14} /> Needs your input
            </div>
            <p className="whitespace-pre-wrap text-on-surface">{project.pending_question.question}</p>
            {project.pending_question.why && (
              <p className="mt-1 whitespace-pre-wrap text-on-surface-low text-[0.75rem]">{project.pending_question.why}</p>
            )}
            <div className="mt-2 flex items-center gap-2">
              <p className="flex-1 text-on-surface-low text-[0.7rem]">Answer in the box below to resume.</p>
              {/* One-click unblock — matches the project-level footer's affordance so a
                  user steering from the task scope isn't forced to type a full answer. */}
              <button type="button" disabled={busy}
                onClick={() => steer('Proceed with your best judgment / the sensible default you proposed. Record the assumption in your finding and continue.')}
                className="shrink-0 rounded-md px-2 py-1 text-[0.7rem] transition-colors hover:bg-info/10 disabled:opacity-50"
                style={{ color: 'var(--color-info)' }}>
                Use your best judgment
              </button>
            </div>
          </div>
        )}

        {plan.length > 0 && (
          <div className="mt-3">
            <p className="text-on-surface-low text-[0.65rem] uppercase tracking-wide">Action plan</p>
            <ol className="mt-1 flex flex-col gap-0.5">
              {plan.map((a, i) => (
                <li key={i} className={`flex items-start gap-1.5 text-[0.78rem] leading-snug ${a.completed ? 'text-on-surface-low line-through' : 'text-on-surface-var'}`}>
                  <span className="mt-[1px] shrink-0 tabular-nums opacity-50">{i + 1}.</span><span>{a.content}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
        {crit.length > 0 && (
          <div className="mt-3">
            <p className="text-on-surface-low text-[0.65rem] uppercase tracking-wide">Done when</p>
            <ul className="mt-1 flex flex-col gap-0.5">
              {crit.map((c, i) => (
                <li key={i} className="flex items-start gap-1.5 text-on-surface-low text-[0.78rem] leading-snug">
                  <Target size={9} className="mt-[4px] shrink-0 opacity-60" /><span className={c.met ? 'line-through opacity-70' : ''}>{c.description}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* agent loop events for THIS task: completed cycles + the in-flight one */}
        <div className="mt-3">
          <p className="text-on-surface-low text-[0.65rem] uppercase tracking-wide">Agent activity{findings.length ? ` · ${findings.length} ${findings.length === 1 ? 'cycle' : 'cycles'}` : ''}</p>
          {findings.length === 0 && live.length === 0 && (
            <p className="mt-1 text-on-surface-low/70 text-[0.75rem]">{running ? 'Working…' : queued ? 'Queued — will run when ready.' : 'No activity yet.'}</p>
          )}
          <div className="mt-1 flex flex-col gap-1.5">
            {/* Cap to the most recent cycles (findings are appended oldest-first, so the
                tail is newest); a "show earlier" toggle reveals the rest in chronological
                order. Keeps a long task's activity column bounded. */}
            {(() => {
              const CAP = 6
              const hidden = showAllFindings ? 0 : Math.max(0, findings.length - CAP)
              const shown = hidden > 0 ? findings.slice(-CAP) : findings
              return (
                <>
                  {hidden > 0 && (
                    <button type="button" onClick={() => setShowAllFindings(true)}
                      className="self-start rounded px-1.5 py-0.5 text-[0.7rem] text-on-surface-low/80 hover:text-primary"
                      title={`Show ${hidden} earlier cycle${hidden === 1 ? '' : 's'}`}>↑ {hidden} earlier cycle{hidden === 1 ? '' : 's'}</button>
                  )}
                  {shown.map((f) => <FindingCard key={f.cycle ?? `${f.summary}`} finding={f} ws={ws} />)}
                </>
              )
            })()}
          </div>
          {live.length > 0 && (
            <div className="mt-2 rounded-lg border border-primary/30 bg-primary/5 p-2.5">
              <div className="mb-1.5 inline-flex items-center gap-1.5 text-[0.7rem] text-primary"><Loader2 size={11} className="animate-spin" /> working now</div>
              <div className="flex flex-col gap-1">
                {live.map((it, i) => (
                  it.kind === 'say'
                    ? <p key={i} className="whitespace-pre-wrap text-[0.78rem] leading-snug text-on-surface-var">{it.label}</p>
                    : <div key={i} className="flex items-start gap-1.5 text-[0.75rem] text-on-surface-low">
                        {it.kind === 'tool' ? <Wrench size={11} className="mt-0.5 shrink-0" /> : <Activity size={11} className="mt-0.5 shrink-0" />}
                        <span className="min-w-0"><span className="text-on-surface-var">{it.label}</span>{it.detail && <span className="text-on-surface-low/70"> · {it.detail.slice(0, 60)}</span>}</span>
                      </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* task actions: queue/unqueue + a task-scoped steer box. In autopilot the
          SYSTEM owns the queue (it re-queues each poll), so manual queue/unqueue is
          futile — hide it and show the active-stage scheduling state instead. */}
      <div className="shrink-0 border-t border-outline-variant/40 p-2">
        {autopilot ? (
          !terminal && (
            <>
              <p className="mb-2 inline-flex items-center gap-1 text-[0.7rem] text-on-surface-low">
                <Rocket size={11} className="text-primary" />
                {running ? 'Running on autopilot.'
                  : state === 'blocked' ? 'Autopilot will run it once its dependencies finish.'
                  : state === 'waiting' ? 'Autopilot will run it when its stage starts.'
                  : 'Queued by autopilot.'}
              </p>
              {state === 'blocked' && blockers.length > 0 && (
                <div className="mb-2 text-[0.7rem] text-on-surface-low">
                  <span>Waiting on:</span>
                  <BlockerList blockers={blockers} onOpenTask={onOpenTask} />
                </div>
              )}
            </>
          )
        ) : (
          <>
            {/* queueable: ready now, or waiting-for-its-stage (queue it for when the
                stage opens). Blocked tasks can't be queued usefully (deps not done). */}
            {(state === 'ready' || (state === 'waiting' && !queued)) && (
              <button type="button" disabled={busy} onClick={() => queue('queue')}
                className="mb-2 inline-flex items-center gap-1 rounded-md bg-primary/15 px-2 py-1 text-[0.7rem] text-primary hover:bg-primary/25 disabled:opacity-50">
                <Play size={11} /> Queue this task{state === 'waiting' ? ' (runs when its stage starts)' : ''}
              </button>
            )}
            {state === 'waiting' && queued && (
              <p className="mb-2 text-[0.7rem] text-on-surface-low">Queued · waiting for its stage to start.</p>
            )}
            {state === 'blocked' && (
              <div className="mb-2 text-[0.7rem] text-on-surface-low">
                <span>Blocked — waiting on{queued ? ' (queued; will run when they finish)' : ''}:</span>
                <BlockerList blockers={blockers} onOpenTask={onOpenTask} />
              </div>
            )}
            {queued && !running && (state === 'queued') && (
              <button type="button" disabled={busy} onClick={() => queue('unqueue')}
                className="mb-2 text-[0.7rem] text-primary hover:underline disabled:opacity-50">Remove from queue</button>
            )}
            {queued && !running && (state === 'blocked' || state === 'waiting') && (
              <button type="button" disabled={busy} onClick={() => queue('unqueue')}
                className="mb-2 ml-2 text-[0.7rem] text-primary hover:underline disabled:opacity-50">Remove from queue</button>
            )}
          </>
        )}
        {!STEERABLE.has(project.status) ? (
          // Not steerable. For a terminal project that's "finished"; for a pre-launch
          // one (a brief flash before the cockpit re-routes to Plan Review) it hasn't
          // started — say which, so the message is never a misleading "finished".
          <p className="px-1.5 py-1 text-center text-on-surface-low text-[0.75rem]">
            {TERMINAL_STATUSES.has(project.status) ? 'This project has finished.' : 'This project hasn’t started yet.'}
          </p>
        ) : terminal ? (
          // A finished (done) OR cancelled task's worker is gone — a task-scoped steer
          // would write guidance_<task_id>.txt that nothing reads (and parallel teardown
          // clears it). Don't offer a no-op box; point to where steering still lands.
          <p className="px-1.5 py-1 text-center text-on-surface-low text-[0.75rem]">
            This task is {task.status === 'cancelled' ? 'cancelled' : 'done'} — steer the project or another task to direct further work.
          </p>
        ) : (
          <>
            {/* echo the latest steer so the user sees it landed (mirrors the project
                steer box); a failed send is marked + the text was kept in the box. */}
            {lastSteer && (
              <div className="mb-2 self-end rounded-xl bg-primary/15 px-2.5 py-1.5 text-[0.8125rem] text-on-surface-var">
                {lastSteer.text}
                {lastSteer.failed && <span className="ml-1.5 text-[0.7rem] text-danger">· failed to send</span>}
              </div>
            )}
            <div className="flex items-end gap-1.5 rounded-xl bg-surface-container px-2.5 py-1.5">
              <textarea ref={steerRef} value={text} onChange={(e) => setText(e.target.value)} rows={1}
                placeholder={project.status === 'needs_input' ? 'Answer for this task…' : `Steer “${task.title.slice(0, 24)}${task.title.length > 24 ? '…' : ''}”…`}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); steer() } }}
                className="max-h-24 min-h-0 flex-1 resize-none overflow-y-auto bg-transparent text-on-surface text-[0.8125rem] outline-none placeholder:text-on-surface-low" />
              <button type="button" onClick={() => steer()} disabled={!text.trim() || busy} aria-label="Send steer"
                className="grid size-7 shrink-0 place-items-center rounded-full disabled:opacity-40"
                style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
                {busy ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// Engine bookkeeping that lives in a project's files dir alongside the worker's
// real deliverables — hidden from the cockpit tree when rooted there, so the user
// sees what they built (requirements.md etc.), not the plumbing.
const PROJECT_DIR_HIDDEN = new Set(['brief.md', 'status.json', 'findings', 'verdicts', 'FINDINGS.md', 'guidance.txt', 'questions.json', 'nudges.json', 'STOP', 'plan_session.json', 'plan_steps.json', 'step_artifact.json', 'plan.json'])
// Dynamically-named engine bookkeeping the exact set above can't enumerate: a
// per-task steer in parallel mode is written as `guidance_<task_id>.txt`. Hidden
// by prefix so it doesn't leak into a no-workspace project's file tree.
const PROJECT_DIR_HIDDEN_PREFIXES = new Set(['guidance_'])

// VCS/build/cache cruft suppressed at EVERY level of the cockpit's mini-IDE tree
// so the user sees their code, not tool plumbing (VSCode's default files.exclude
// spirit). Applies in both bound-workspace and project-dir modes.
const CODE_TREE_NOISE = new Set(['.git', '.hg', '.svn', '__pycache__', '.pytest_cache',
  '.mypy_cache', '.ruff_cache', '.tox', 'node_modules', '.venv', 'venv', '.DS_Store',
  '.idea', '.next', '.turbo', '.cache', '.gradle', '__snapshots__', '.terraform'])

/** Workspace file tree rooted at the project's workspace dir, or the project's own
 *  files dir when no workspace is bound (isProjectDir) — in which case engine
 *  bookkeeping files are hidden so only the deliverables show. */
function WorkspaceTree({ ws, running, isProjectDir }: { ws: string; running: boolean; isProjectDir: boolean }) {
  const dirs = useDirCache()
  const [gitNonce, setGitNonce] = useState(0)
  const { statuses, state: gitState } = useGitStatus(ws || null, gitNonce)
  // While the worker runs it writes new files across the workspace (root AND
  // subdirs like src/ tests/); the dir cache + git status would otherwise stay
  // stale until a manual reload. Slow-poll (visible only): invalidate the whole
  // loaded subtree (not just the root — else a file the worker drops into an
  // expanded subdir never appears there) + bump git status so freshly created
  // files surface with their badge. Files-page-style live refresh.
  useVisiblePoll(() => {
    if (ws) { dirs.invalidateSubtree(ws); setGitNonce((n) => n + 1) }
  }, running && ws ? 8000 : null)
  // A manual save in the editor → refresh git badges immediately. The poll above only
  // runs while RUNNING, so on a paused/idle project (where the USER is editing) a save
  // would otherwise leave the tree's git badges stale until something else triggered it.
  useEffect(() => {
    const onSaved = () => { if (ws) { dirs.invalidate(ws); setGitNonce((n) => n + 1) } }
    window.addEventListener('ne:code-file-saved', onSaved)
    return () => window.removeEventListener('ne:code-file-saved', onSaved)
  }, [ws, dirs])
  // Detect a worker DELETING a tracked file (git status code contains 'D'): play an
  // autonomous-backspace erase animation of its pre-delete (HEAD) content, once per
  // path. Only while running; the git original is fetched lazily.
  const erasedRef = useRef<Set<string>>(new Set())
  // Deletions present in the FIRST loaded status snapshot are PRE-EXISTING (the worker
  // removed them in an earlier cycle, before this mount) — they must NOT replay their
  // erase animation just because the cockpit was reopened on a still-running project
  // (erasedRef resets on remount, so without this every already-deleted file animates
  // as if it just happened). Seed them as already-erased on the first load; only a
  // deletion that appears in a LATER snapshot is genuinely live → animates. Mirrors the
  // write/diff-reveal path's "seed the baseline on first sight" guard.
  const eraseSeededRef = useRef(false)
  useEffect(() => {
    if (!running || gitState !== 'loaded') return
    const seeding = !eraseSeededRef.current
    for (const [path, code] of Object.entries(statuses)) {
      if (!code.includes('D') || erasedRef.current.has(path)) continue
      erasedRef.current.add(path)
      if (seeding) continue  // pre-existing deletion on first load → record, don't animate
      api.fileGitOriginal(path).then((r) => {
        if (r?.content?.trim()) {
          window.dispatchEvent(new CustomEvent('ne:code-erase-file', {
            detail: { path, name: path.split('/').pop(), text: r.content },
          }))
        }
      }).catch(() => {})
    }
    eraseSeededRef.current = true
  }, [statuses, running, gitState])
  // Bridge file-open to the center editor via a custom event (kept simple: the
  // center editor listens for ne:code-open-file). Avoids prop-drilling the tabs.
  const openFile = useCallback((entry: FsEntry) => {
    window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: entry }))
  }, [])
  // Track the latest-opened file so the tree auto-expands to reveal it (its own
  // clicks + the worker's follow-open both flow through ne:code-open-file). This
  // is what makes a deeply-nested worker-touched file visible in the tree, not
  // buried in collapsed folders.
  const [activePath, setActivePath] = useState<string | null>(null)
  useEffect(() => {
    const onOpen = (e: Event) => {
      const d = (e as CustomEvent).detail as { path?: string; is_dir?: boolean } | null
      if (d?.path && !d.is_dir) setActivePath(d.path)
    }
    window.addEventListener('ne:code-open-file', onOpen as EventListener)
    return () => window.removeEventListener('ne:code-open-file', onOpen as EventListener)
  }, [])
  // Inline create-new at the workspace root — the cockpit's mini-IDE equivalent of
  // the Files page's New file / New folder (the tree itself only renames/deletes).
  const [creating, setCreating] = useState<'file' | 'dir' | null>(null)
  const [newName, setNewName] = useState('')
  const [createErr, setCreateErr] = useState('')
  // In-flight guard: the create input fires submitCreate on BOTH Enter and onBlur, so
  // Enter (which can blur) — or any click-away during the async create — would fire a
  // second fileCreate that hits the just-created name as a 409 "already exists". Ignore
  // re-entry while a create is in flight (mirrors the C327/C386 acting-guard pattern).
  const creatingInFlight = useRef(false)
  // Surface rename/delete failures (permission denied, file vanished mid-op, ws
  // unmounted) instead of swallowing them as a silent no-op — matches the create
  // error above + the Files page, which reports "Delete failed: …".
  const [treeErr, setTreeErr] = useState('')
  // Delete a tree entry behind a themed confirm (the shared imperative dialog —
  // never a native, off-theme window.confirm).
  const doDelete = async (entry: FsEntry) => {
    if (!(await confirm({
      title: entry.is_dir ? `Delete folder "${entry.name}"?` : `Delete file "${entry.name}"?`,
      body: entry.is_dir
        ? "This deletes the folder and all its contents. This can't be undone."
        : "This can't be undone.",
      danger: true, confirmLabel: 'Delete',
    }))) return
    setTreeErr('')
    let ok = false
    try {
      await api.fileDelete(entry.path)
      ok = true
      // Close any editor tab(s) for the deleted file (or files under a deleted dir) —
      // ONLY on success. The close was firing unconditionally (before the await settled
      // its outcome), so a FAILED delete (permission / vanished / 5xx) still closed the
      // tab for a file that's still on disk, losing the user's open view + any edits.
      window.dispatchEvent(new CustomEvent('ne:code-close-file', { detail: { path: entry.path } }))
    }
    catch (e) { setTreeErr(`Couldn't delete "${entry.name}": ${(e as Error).message || 'unknown error'}`) }
    // Only refresh the tree/git when something actually changed — a failed delete left
    // the disk untouched, so re-listing is wasted work (and the entry correctly stays).
    if (ok) {
      dirs.invalidate(entry.path.slice(0, entry.path.length - entry.name.length).replace(/\/$/, '') || ws)
      dirs.invalidate(ws)
      window.dispatchEvent(new CustomEvent('ne:code-file-saved'))  // refresh git badges + Changes
    }
  }
  const submitCreate = async () => {
    if (creatingInFlight.current) return
    const name = newName.trim()
    if (!name || !creating) { setCreating(null); setNewName(''); setCreateErr(''); return }
    creatingInFlight.current = true
    try {
      const r = await api.fileCreate(ws, name, creating)
      if (creating === 'file') window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name, path: r.path, is_dir: false } }))
      setCreating(null); setNewName(''); setCreateErr(''); dirs.invalidate(ws)
      // Refresh git badges + the Changes tab (a new file is untracked → 'U' badge).
      window.dispatchEvent(new CustomEvent('ne:code-file-saved'))
    } catch (e) {
      // Keep the input open + show why (duplicate name → 409, slash/blocked → 400)
      // so the user can correct it instead of a silent no-op.
      const msg = (e as Error).message || 'Could not create'
      setCreateErr(/already exists/i.test(msg) ? 'A file or folder with that name already exists.'
        : /invalid name/i.test(msg) ? 'Invalid name — no slashes; pick a different name.'
        : msg)
      dirs.invalidate(ws)
    } finally {
      creatingInFlight.current = false
    }
  }
  const cancelCreate = () => { setCreating(null); setNewName(''); setCreateErr('') }
  if (!ws) return <p className="px-3 py-6 text-center text-on-surface-low text-[0.8125rem]">No files yet for this project.</p>
  return (
    <div className="p-1">
      {/* create-new row: icon buttons reveal an inline name input (Enter creates,
          Escape cancels). Hidden for a no-workspace project dir (isProjectDir). */}
      {!isProjectDir && (
        <div className="flex flex-col gap-1 px-1.5 pb-1">
         <div className="flex items-center gap-1">
          {creating ? (
            <input autoFocus value={newName} onChange={(e) => { setNewName(e.target.value); if (createErr) setCreateErr('') }}
              onKeyDown={(e) => { if (e.key === 'Enter') submitCreate(); if (e.key === 'Escape') cancelCreate() }}
              onBlur={() => { if (!createErr) submitCreate() }} placeholder={creating === 'file' ? 'new-file.ext' : 'new-folder'}
              className={`h-7 min-w-0 flex-1 rounded-md bg-surface-high px-2 text-[0.8125rem] text-on-surface outline-none focus:ring-2 placeholder:text-on-surface-low ${createErr ? 'focus:ring-danger/50 ring-2 ring-danger/40' : 'focus:ring-primary/40'}`} />
          ) : (
            <>
              <button type="button" onClick={() => { setCreating('file'); setNewName('') }} title="New file" aria-label="New file"
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[0.7rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface"><FilePlus2 size={13} /> File</button>
              <button type="button" onClick={() => { setCreating('dir'); setNewName('') }} title="New folder" aria-label="New folder"
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[0.7rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface"><FolderPlus size={13} /> Folder</button>
            </>
          )}
         </div>
         {createErr && <span role="alert" className="px-0.5 text-[0.7rem] text-danger">{createErr}</span>}
        </div>
      )}
      {/* rename/delete failure — dismissable, sits above the tree (works in both
          bound-workspace and project-dir modes, where the create row is hidden). */}
      {treeErr && (
        <div role="alert" className="mx-1.5 mb-1 flex items-start gap-1.5 px-0.5 text-[0.7rem] text-danger">
          <span className="min-w-0 flex-1">{treeErr}</span>
          <button type="button" onClick={() => setTreeErr('')} aria-label="Dismiss" className="shrink-0 hover:text-on-surface"><X size={11} /></button>
        </div>
      )}
      <FileTree dirs={dirs} rootPath={ws} activePath={activePath} gitStatuses={statuses}
        onOpenFile={openFile} artifactPaths={EMPTY_ARTIFACTS}
        hideNames={isProjectDir ? PROJECT_DIR_HIDDEN : undefined}
        hidePrefixes={isProjectDir ? PROJECT_DIR_HIDDEN_PREFIXES : undefined}
        hideNamesDeep={CODE_TREE_NOISE}
        emptyLabel={isProjectDir
          ? (running ? 'No files yet — the worker will create them here.' : 'No files yet — files the worker creates will appear here once it runs.')
          : 'Empty'}
        onRename={async (entry, nextName) => {
          const parent = entry.path.slice(0, entry.path.length - entry.name.length).replace(/\/$/, '')
          setTreeErr('')
          try {
            const dest = parent ? `${parent}/${nextName}` : nextName
            await api.fileMove(entry.path, dest)
            // Renaming an OPEN file shouldn't make it vanish from the editor — if it was
            // a tab, follow it to the new path (preserving active state); if not, this is
            // a no-op. A folder rename re-roots its open descendants too. (The handler
            // falls back to closing the stale tab when it can't re-root, never strands one.)
            window.dispatchEvent(new CustomEvent('ne:code-renamed', { detail: { from: entry.path, to: dest, isDir: !!entry.is_dir } }))
          }
          catch (e) {
            // Friendly copy for the common name-collision case (backend: "destination
            // already exists") — matches the create row's mapping rather than leaking the
            // terse server string.
            const msg = (e as Error).message || 'unknown error'
            setTreeErr(/already exists/i.test(msg)
              ? `Couldn't rename "${entry.name}" — a file or folder named "${nextName}" already exists here.`
              : `Couldn't rename "${entry.name}": ${msg}`)
          }
          // Invalidate the renamed item's PARENT (where the entry lived), not just the
          // workspace root — a rename in a subdir wouldn't refresh there otherwise.
          dirs.invalidate(parent || ws); dirs.invalidate(ws)
          window.dispatchEvent(new CustomEvent('ne:code-file-saved'))  // refresh git badges + Changes
        }}
        onDelete={(entry) => { void doDelete(entry) }}
        onUpload={async (dirEntry, files) => {
          // Surface an upload failure (too large, permission denied, dir gone) in the
          // same dismissable tree-error line as create/rename/delete — was a silent
          // no-op (drop files, nothing happens, no reason). fileUpload returns
          // {ok:false,error} on an HTTP error (doesn't throw) AND can throw on network.
          if (!files.length) return
          setTreeErr('')
          try {
            const r = await api.fileUpload(dirEntry.path, files)
            if (!r.ok) setTreeErr(`Couldn't upload to ${dirEntry.name}: ${r.error || 'unknown error'}`)
          } catch (e) {
            setTreeErr(`Couldn't upload to ${dirEntry.name}: ${(e as Error).message || 'unknown error'}`)
          }
          dirs.invalidate(dirEntry.path); dirs.invalidate(ws)
          window.dispatchEvent(new CustomEvent('ne:code-file-saved'))  // refresh git badges + Changes
        }}
        onCreate={async (dirEntry, name, kind) => {
          // Create a file/folder INSIDE the right-clicked dir (not just the root) — the
          // mini-IDE "New file/folder here". Opens a new file in the editor; surfaces
          // failures (dup/invalid) in the same dismissable tree-error line.
          setTreeErr('')
          try {
            const r = await api.fileCreate(dirEntry.path, name, kind)
            if (kind === 'file') window.dispatchEvent(new CustomEvent('ne:code-open-file', { detail: { name, path: r.path, is_dir: false } }))
          } catch (e) {
            const msg = (e as Error).message || 'unknown error'
            setTreeErr(/already exists/i.test(msg) ? `"${name}" already exists in ${dirEntry.name}.`
              : /invalid name/i.test(msg) ? `Invalid name "${name}" — no slashes.` : `Couldn't create "${name}": ${msg}`)
          }
          dirs.invalidate(dirEntry.path); dirs.invalidate(ws)
          window.dispatchEvent(new CustomEvent('ne:code-file-saved'))  // refresh git badges + Changes
        }} />
    </div>
  )
}

/** Git changes panel — the workspace's branch + changed files, so the human can
 *  review what the agent did. Clicking a file opens it in the editor. Polls on
 *  mount + a manual refresh; git status is keyed by path. */
function ChangesPanel({ ws, running, isProjectDir = false }: { ws: string; running: boolean; isProjectDir?: boolean }) {
  const [nonce, setNonce] = useState(0)
  // A no-workspace project's "ws" is its engine files dir, which is NOT a git repo,
  // so git status there is meaningless — running it would render a misleading
  // "(no branch) · working tree clean". Skip git entirely + say so plainly.
  const gitWs = isProjectDir ? null : (ws || null)
  const { branch, statuses, state, repoRoot } = useGitStatus(gitWs, nonce)
  // While the worker runs it edits files + the supervisor commits each stage; the
  // status + history would otherwise stay stale until a manual refresh. Slow-poll
  // (visible only) so the Changes tab tracks the work live, like the file tree.
  useVisiblePoll(() => { if (gitWs) setNonce((n) => n + 1) }, running && gitWs ? 8000 : null)
  // A manual editor save → refresh the Changes tab now (the poll only runs while
  // running; a save on a paused/idle project would otherwise show stale status).
  useEffect(() => {
    const onSaved = () => { if (gitWs) setNonce((n) => n + 1) }
    window.addEventListener('ne:code-file-saved', onSaved)
    return () => window.removeEventListener('ne:code-file-saved', onSaved)
  }, [gitWs])
  // The per-stage commit history (the supervisor checkpoints each passed stage,
  // C58) — a reviewable timeline of what each stage produced.
  const [commits, setCommits] = useState<{ hash: string; subject: string; relative: string }[]>([])
  useEffect(() => {
    if (!gitWs) { setCommits([]); return }
    let alive = true
    api.fileGitLog(gitWs, 20).then((r) => { if (alive) setCommits(r.commits || []) }).catch(() => { if (alive) setCommits([]) })
    return () => { alive = false }
  }, [gitWs, nonce])

  // A no-workspace (greenfield) project: its files live in the engine dir with no
  // git, so there's no diff/branch history to show — say so honestly instead of a
  // misleading "clean working tree".
  if (isProjectDir) return <p className="px-3 py-6 text-center text-on-surface-low text-[0.8125rem]">This project has no git workspace — changes aren't version-tracked. Files the worker created are in the Files tab.</p>
  if (!ws) return <p className="px-3 py-6 text-center text-on-surface-low text-[0.8125rem]">No workspace directory.</p>
  // A bound brownfield workspace that ISN'T a git repo (empty repoRoot once loaded):
  // there's no branch/diff/history to show, and "working tree is clean" would be a
  // lie (it's just not version-controlled). Say so honestly — distinct from a clean
  // repo + from a transient read error.
  if (state === 'loaded' && !repoRoot) return <p className="px-3 py-6 text-center text-on-surface-low text-[0.8125rem]">This workspace isn’t a git repository — changes aren’t version-tracked. Browse the files in the Files tab.</p>
  const entries = Object.entries(statuses)
  // git status codes → a short, readable label + tone.
  const label = (code: string): { text: string; color: string } => {
    const c = code.trim()
    if (c === '??') return { text: 'new', color: 'var(--color-ok)' }
    // UNMERGED (conflict) — porcelain XY where either side is 'U', or the AA/DD
    // double forms. The worker merges parallel task worktrees, so conflicts are a real
    // state here, and they need RESOLUTION — must not read as a bland "modified".
    // Checked before the D/A letter rules (DD/AU/UD all contain D or A).
    if (c.includes('U') || c === 'AA' || c === 'DD') return { text: 'conflict', color: 'var(--color-danger)' }
    if (c.includes('D')) return { text: 'deleted', color: 'var(--color-danger)' }
    if (c.includes('A')) return { text: 'added', color: 'var(--color-ok)' }
    if (c.includes('R')) return { text: 'renamed', color: 'var(--color-primary)' }
    return { text: 'modified', color: 'var(--color-warn)' }
  }
  // git-status keys are absolute paths whose root may differ from `ws` by a
  // realpath prefix (e.g. /tmp vs /private/tmp on macOS). Relativize by the
  // workspace's LAST path segment so the display is clean regardless.
  const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
  const rel = (p: string) => {
    const marker = `/${wsBase}/`
    const i = wsBase ? p.lastIndexOf(marker) : -1
    return i >= 0 ? p.slice(i + marker.length) : p
  }
  // Open a working-vs-HEAD DIFF (under `ws`, not the git realpath, so the editor's
  // workspace-scoped guards apply). A modifier-less click reviews the change as a
  // diff; this is the natural action in the Changes tab.
  const open = (gitPath: string, code: string) => {
    const r = rel(gitPath)
    const path = `${ws.replace(/\/$/, '')}/${r}`
    // A deleted file has no working copy — tell the diff so it treats the missing
    // read as expected (all-removed) rather than a load error.
    const deleted = code.includes('D')
    window.dispatchEvent(new CustomEvent('ne:code-open-diff', { detail: { name: r.split('/').pop(), path, deleted } }))
  }
  return (
    <div className="flex flex-col">
      <div className="flex items-center justify-between gap-2 px-3 py-2 text-[0.75rem] text-on-surface-low">
        <span className="inline-flex items-center gap-1.5 min-w-0">
          <GitBranch size={12} className="shrink-0" />
          <span className="truncate text-on-surface-var">{branch || (state === 'loaded' ? '(no branch)' : '…')}</span>
        </span>
        <button type="button" onClick={() => setNonce((n) => n + 1)} className="shrink-0 rounded px-1.5 py-0.5 hover:bg-surface-high hover:text-on-surface">refresh</button>
      </div>
      {entries.length === 0 ? (
        // Distinguish the three empty-map states: a failed/in-flight fetch must NOT
        // masquerade as a genuinely clean tree (the old bug — all three read "clean").
        state === 'loading' || state === 'idle' ? (
          <p className="px-3 py-6 text-center text-on-surface-low text-[0.8125rem]">Loading changes…</p>
        ) : state === 'error' ? (
          <div className="flex flex-col items-center gap-2 px-3 py-6 text-center text-[0.8125rem]">
            <span style={{ color: 'var(--color-warn)' }}>Couldn't read git status for this workspace.</span>
            <button type="button" onClick={() => setNonce((n) => n + 1)} className="rounded-md px-2 py-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface">Retry</button>
          </div>
        ) : (
          <p className="px-3 py-6 text-center text-on-surface-low text-[0.8125rem]">No uncommitted changes — the working tree is clean.</p>
        )
      ) : (
        <div className="flex flex-col">
          {/* count label — at-a-glance magnitude + parity with the "History" label
              below, and it distinguishes working changes from the commit list. */}
          <div className="px-3 pt-1 pb-1 text-[0.7rem] uppercase tracking-wide text-on-surface-low">
            Changes ({entries.length})
          </div>
          {entries.map(([path, code]) => {
            const l = label(code)
            return (
              <button key={path} type="button" onClick={() => open(path, code)}
                className="flex items-center gap-2 px-3 py-1.5 text-left text-[0.8125rem] transition-colors hover:bg-surface-high">
                <span className="w-[58px] shrink-0 text-[0.7rem]" style={{ color: l.color }}>{l.text}</span>
                <span className="min-w-0 truncate font-mono text-on-surface-var" title={path}>{rel(path)}</span>
              </button>
            )
          })}
        </div>
      )}
      {/* per-stage commit history — the reviewable timeline (supervisor + worker
          commits). Shown below the working changes. */}
      {commits.length > 0 && (
        <div className="mt-1 border-t border-outline-variant/40">
          <div className="flex items-baseline justify-between gap-2 px-3 pt-2 pb-1">
            <span className="text-[0.7rem] uppercase tracking-wide text-on-surface-low">History</span>
            {/* The log is capped server-side (20); when we get a full page back there
                are almost certainly older commits not shown — say so rather than let
                the list read as the repo's entire history. */}
            {commits.length >= 20 && <span className="text-[0.65rem] text-on-surface-low/70">latest 20</span>}
          </div>
          {commits.map((c) => (
            <button key={c.hash} type="button"
              onClick={() => window.dispatchEvent(new CustomEvent('ne:code-open-commit', { detail: { hash: c.hash, subject: c.subject, path: ws } }))}
              className="flex w-full items-baseline gap-2 px-3 py-1 text-left text-[0.8125rem] transition-colors hover:bg-surface-high">
              <span className="shrink-0 font-mono text-[0.7rem] text-on-surface-low">{c.hash}</span>
              <span className="min-w-0 flex-1 truncate text-on-surface-var" title={c.subject}>{c.subject}</span>
              <span className="shrink-0 text-[0.65rem] text-on-surface-low">{c.relative}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── center: Monaco editor with tabs + optional bottom terminal ──

function CenterEditor({ ws, showTerm, onCloseTerm, running, runCmd }: { ws: string; showTerm: boolean; onCloseTerm: () => void; running: boolean; runCmd?: { cmd: string; n: number } | null }) {
  // Latch: once the terminal is opened it stays MOUNTED (toggled via CSS) so the header
  // Hide/Show button preserves the PTY + running process + scrollback. The terminal's
  // own X is a real CLOSE → reset the latch to tear the PTY down. Header toggle just
  // hides (showTerm=false) but leaves termOpened true; the X resets termOpened.
  const [termOpened, setTermOpened] = useState(false)
  useEffect(() => { if (showTerm) setTermOpened(true) }, [showTerm])
  const closeTerm = useCallback(() => { setTermOpened(false); onCloseTerm() }, [onCloseTerm])
  // Tabs are scoped to THIS project's workspace, so they never restore a tab from
  // the Files page or another (possibly deleted) project's directory.
  const { tabs, active, activePath, dirty, open, closeNow, setActivePath, markDirty } = useFileTabs(ws)
  // Latest tabs for event listeners (which close over a render's `tabs` and would
  // otherwise act on a stale snapshot — e.g. the close-deleted-file handler).
  const tabsRef = useRef(tabs); tabsRef.current = tabs
  // Latest activePath for the (stably-bound) rename listener, which must restore focus
  // to whatever was active — without re-binding every listener on each tab focus.
  const activePathRef = useRef(activePath); activePathRef.current = activePath
  // A tab the user asked to close that has unsaved edits → confirm with the THEMED
  // dialog. Programmatic closes (ws cleanup, worker-erase) use closeNow and never prompt.
  const requestClose = useCallback(async (path: string, name: string) => {
    if (!dirty[path]) { closeNow(path); return }
    if (!(await confirm({
      title: 'Discard unsaved changes?',
      body: `"${name}" has edits that haven't been saved. Closing the tab discards them.`,
      danger: true, confirmLabel: 'Discard',
    }))) return
    draftStore.delete(path); closeNow(path)
  }, [dirty, closeNow])
  // (The browser-level unsaved-edits exit guard now lives in useFileTabs, so every
  // FileViewer host — cockpit, Files page, chat panel — gets it uniformly.)
  // ⌘S / Ctrl-S saves the active file — the most-used editor action, expected in a
  // mini-IDE (the FileViewer's Save button already advertises "⌘S"). Mirrors the
  // Files page + chat file panel wiring: a handle ref + a window keydown listener.
  const viewerRef = useRef<FileViewerHandle>(null)
  // Per-path unsaved-draft cache: only the ACTIVE tab's FileViewer is mounted, so a tab
  // switch unmounts the editor and would drop an in-progress edit (the dirty dot would
  // even lie — switching back showed clean disk content). The viewer mirrors its draft
  // here on edit + re-seeds from it on mount, so a dirty tab's content survives switches.
  const draftStore = useRef(new Map<string, { draft: string; base: string; warned: boolean }>()).current
  // Collapsed side panels surface as pull-out tabs on THIS editor bar (no persistent
  // vertical rail in the body). Each CollapsiblePanel broadcasts its collapsed state;
  // we track {panelKey → {side,label}} for the currently-collapsed ones + render a
  // re-open chip on the matching edge of the tab strip. Clicking dispatches the same
  // ne:code-expand-panel event the panel already listens for.
  // Seed from the persisted collapse flags (the two panel keys are fixed) so a panel
  // that STARTS collapsed shows its re-open chip immediately — without relying on
  // catching the panel's mount broadcast (which fires before this listener attaches).
  const [collapsedPanels, setCollapsedPanels] = useState<Record<string, { side: 'left' | 'right'; label: string }>>(() => {
    const seed: Record<string, { side: 'left' | 'right'; label: string }> = {}
    try {
      if (localStorage.getItem('code-left-collapsed') === '1') seed['code-left'] = { side: 'left', label: 'Files' }
      if (localStorage.getItem('code-right-collapsed') === '1') seed['code-right'] = { side: 'right', label: 'Tasks' }
    } catch { /* ignore */ }
    return seed
  })
  useEffect(() => {
    const onCollapsed = (e: Event) => {
      const d = (e as CustomEvent).detail as { panelKey: string; side: 'left' | 'right'; label: string; collapsed: boolean }
      setCollapsedPanels((prev) => {
        const next = { ...prev }
        if (d.collapsed) next[d.panelKey] = { side: d.side, label: d.label }
        else delete next[d.panelKey]
        return next
      })
    }
    window.addEventListener('ne:code-panel-collapsed', onCollapsed as EventListener)
    return () => window.removeEventListener('ne:code-panel-collapsed', onCollapsed as EventListener)
  }, [])
  const collapsedLeft = Object.entries(collapsedPanels).filter(([, v]) => v.side === 'left')
  const collapsedRight = Object.entries(collapsedPanels).filter(([, v]) => v.side === 'right')
  const reopenPanel = (key: string) => window.dispatchEvent(new CustomEvent('ne:code-expand-panel', { detail: key }))
  // Snapshot the open file into the Artifacts system (the editor's "Artifact"
  // button). Was a no-op here (dead button) — the Files page wires this via a naming
  // modal; the cockpit has no Artifacts tab, so create directly with the file's
  // basename + a kind inferred from its extension, and report via ne:code-toast.
  const saveFileAsArtifact = useCallback((entry: FsEntry, content: string) => {
    const base = entry.name || entry.path.split('/').pop() || 'file'
    const ext = base.includes('.') ? base.split('.').pop()!.toLowerCase() : ''
    // Map to an ALLOWED artifact kind (widget/html/react/markdown/svg/json/text).
    // Source code has no dedicated kind → use 'text' (renders as a <pre>); NOT 'code'
    // (rejected by the backend → normalized to 'widget', which renders the file in a
    // sandboxed IFRAME — a .py shown as a broken HTML doc). md/json/svg keep their real
    // kinds; html is deliberately 'text' too (a code file the user is reviewing, not a
    // live widget to execute).
    const kind = ext === 'md' ? 'markdown' : ext === 'json' ? 'json'
      : ext === 'svg' ? 'svg' : 'text'
    api.createArtifact({ name: base, content, source: 'manual', source_path: entry.path, kind })
      .then(() => window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'ok', text: `Saved “${base}” as an artifact.` } })))
      .catch((e) => window.dispatchEvent(new CustomEvent('ne:code-toast', { detail: { kind: 'error', text: `Couldn't save artifact: ${(e as Error).message || 'unknown error'}` } })))
  }, [])
  // Defensively drop any restored tab that isn't under this workspace (e.g. a
  // stale entry from before scoping) so a forbidden file-read can't 400.
  // The file tree lists REALPATH-resolved paths (macOS symlinks /tmp→/private/tmp,
  // /var→/private/var), which differ from the unresolved `workspace_dir` by a
  // prefix — so a plain startsWith(ws) would nuke every tree-opened tab. Match on
  // the workspace's last segment too (same approach as the Changes panel's `rel`).
  useEffect(() => {
    if (!ws) return
    const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
    const underWs = (p: string) => p.startsWith(ws) || (!!wsBase && p.includes(`/${wsBase}/`))
    for (const t of tabs) {
      if (!underWs(t.path)) closeNow(t.path)
    }
  }, [ws, tabs, closeNow])
  // Canonicalize an incoming path to the workspace (`ws`) form. Different sources
  // name the same file differently: the file tree emits REALPATH paths (macOS
  // /private/tmp/…), follow-the-worker emits unresolved `workspace_dir` paths
  // (/tmp/…). useFileTabs dedups by exact string, so without this the same file
  // opened from two sources becomes two tabs. Splice at the workspace's last
  // segment and re-root under `ws`.
  const canonPath = useCallback((p: string): string => {
    if (!ws || p.startsWith(ws)) return p
    const wsBase = ws.replace(/\/$/, '').split('/').pop() || ''
    const marker = `/${wsBase}/`
    const i = wsBase ? p.lastIndexOf(marker) : -1
    return i >= 0 ? `${ws.replace(/\/$/, '')}/${p.slice(i + marker.length)}` : p
  }, [ws])
  // A working-vs-HEAD diff shown over the editor (Changes tab). Opening a plain
  // file (tree) clears it; closing it returns to the editor.
  const [diff, setDiff] = useState<{ path: string; name: string; deleted?: boolean } | null>(null)
  // A whole-commit unified diff (History → click a commit), shown as a patch.
  const [commit, setCommit] = useState<{ hash: string; subject: string } | null>(null)
  const { mode } = useMode()
  // Autonomous-writing reveal: when the worker creates/changes a file (a _follow
  // open), play a typing animation before handing off to the live editor. A NEW
  // file animates as a write (TypingReveal); a MODIFIED file (we've seen it
  // before) animates as a diff — new lines in, then old lines out (DiffReveal).
  // `reveal` holds the file being animated; once done it opens normally.
  const [reveal, setReveal] = useState<
    | { path: string; name: string; kind: 'write'; text: string }
    | { path: string; name: string; kind: 'diff'; oldText: string; newText: string }
    | { path: string; name: string; kind: 'erase'; text: string }
    | null
  >(null)
  // Last content we've shown per path, so a re-edit can diff old→new.
  const lastContentRef = useRef<Map<string, string>>(new Map())
  // The editor (FileViewer) is the visible center surface only when nothing overlays
  // it — a diff/commit/reveal view replaces it. Mirrors the body render guard below.
  const editorShowing = !reveal && !commit && !diff && !!active
  // ⌘S / Ctrl-S saves the active file — but ONLY while the editor is showing, so a
  // diff/commit/reveal view doesn't swallow the browser save + call a stale viewer ref.
  // ⌘W / Ctrl-W closes the active tab (the universal IDE shortcut, expected in a mini-
  // IDE) — routed through requestClose so a dirty tab still gets the discard confirm.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Let the embedded terminal own its keystrokes: Ctrl-W is shell "delete word",
      // Ctrl-S is XOFF — hijacking them to close-tab / save while typing in the shell is
      // disruptive (Linux/Windows Ctrl; macOS ⌘ is safe but the guard is harmless there).
      if ((e.metaKey || e.ctrlKey) && (document.activeElement as HTMLElement | null)?.closest('.xterm')) return
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's' && editorShowing) {
        e.preventDefault(); viewerRef.current?.save()
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'w' && active) {
        // Only when a file tab is the active surface (not a diff/commit/reveal) — and
        // preventDefault so the browser doesn't close the whole tab/window.
        e.preventDefault(); requestClose(active.path, active.name)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [editorShowing, active, requestClose])
  // Listen for file-open + diff-open + commit-open requests.
  useEffect(() => {
    const onOpen = (e: Event) => {
      const raw = (e as CustomEvent).detail as FsEntry & { _follow?: boolean }
      if (!raw || raw.is_dir) return
      // Normalize to the workspace path form so every source maps to one tab.
      const entry = { ...raw, path: canonPath(raw.path) }
      setDiff(null); setCommit(null)
      // A USER-initiated open (tree click, FilesTouched chip — NOT _follow) must cancel
      // any in-progress worker reveal: the reveal overlay covers the editor (editorShowing
      // = !reveal), so leaving it up would show the PREVIOUS file's animation over the
      // file the user just clicked, and its onDone would re-open that file, stealing the
      // navigation. A _follow open starts its own reveal below (replacing this one), so
      // only clear for the non-follow path.
      if (!entry._follow) setReveal(null)
      // Worker-driven open → animate. New file (unseen) = write reveal; a file we've
      // shown before that changed = diff reveal (new lines in, old lines out).
      if (entry._follow) {
        const prev = lastContentRef.current.get(entry.path)
        api.fileRead(entry.path, true).then((r) => {
          const text = r.content ?? ''
          // Skip the typing/diff animation for content that can't or shouldn't be
          // animated: a BINARY file the worker wrote (compiled output, an image —
          // r.binary means the body is NUL-laden mojibake, not typeable text), or a
          // very LARGE file (animating hundreds of KB char-by-char mounts a giant
          // string + janks; the duration is already clamped, but the DOM cost isn't).
          // Record the baseline + open the real viewer directly, which renders binary
          // as a placeholder and large text normally. (DiffReveal already self-skips a
          // too-large diff; this guards the write-reveal + the binary case both paths
          // share.) TRUNCATED reads (r.truncated) also skip: a diff against a partial
          // body would falsely show the unread tail as removed.
          if (r.binary || r.truncated || text.length > REVEAL_MAX_CHARS) {
            lastContentRef.current.set(entry.path, text)
            open(entry)
            return
          }
          if (prev === undefined) {
            // first time we see it → write reveal (skip empty/binary)
            if (text.trim()) { lastContentRef.current.set(entry.path, text); setReveal({ path: entry.path, name: entry.name, kind: 'write', text }) }
            else { lastContentRef.current.set(entry.path, text); open(entry) }
          } else if (prev !== text && text.trim()) {
            // changed since last shown → diff reveal
            lastContentRef.current.set(entry.path, text)
            setReveal({ path: entry.path, name: entry.name, kind: 'diff', oldText: prev, newText: text })
          } else {
            open(entry)  // unchanged → just (re)open
          }
        }).catch(() => open(entry))
        return
      }
      // MANUAL open (tree click, FilesTouched chip): seed the diff-reveal baseline with
      // the current on-disk content (fire-and-forget) — else when the worker later edits
      // THIS file (a _follow open), prev is undefined → it animates as a write-from-empty
      // reveal instead of a diff of just the changed lines, misrepresenting an edit of a
      // file the user already had open. Skip if we already have a baseline for it.
      if (lastContentRef.current.get(entry.path) === undefined) {
        api.fileRead(entry.path, true).then((r) => {
          if (!r.binary && !r.truncated && typeof r.content === 'string') {
            lastContentRef.current.set(entry.path, r.content)
          }
        }).catch(() => {})
      }
      open(entry)
    }
    // Clear any in-progress reveal too: it renders ABOVE diff/commit (the body branch
    // order is reveal → commit → diff), so a user opening a diff/commit mid-reveal would
    // otherwise stay hidden behind the lingering animation.
    const onDiff = (e: Event) => { const d = (e as CustomEvent).detail as { path: string; name: string; deleted?: boolean }; if (d?.path) { setReveal(null); setCommit(null); setDiff(d) } }
    const onCommit = (e: Event) => { const c = (e as CustomEvent).detail as { hash: string; subject: string }; if (c?.hash) { setReveal(null); setDiff(null); setCommit(c) } }
    // The worker deleted a file → play an erase (autonomous-backspace) animation of
    // its last content, then close any tab for it. detail carries {path,name,text}.
    const onErase = (e: Event) => {
      const raw = (e as CustomEvent).detail as { path: string; name: string; text?: string }
      const d = raw?.path ? { ...raw, path: canonPath(raw.path) } : raw
      const text = d?.text || lastContentRef.current.get(d?.path)
      // Skip the erase animation for empty OR very-large last-content (a big generated
      // file animating char-by-char janks + mounts a giant string) — just close the tab.
      if (!d?.path || !text || !text.trim() || text.length > REVEAL_MAX_CHARS) {
        if (d?.path) { lastContentRef.current.delete(d.path); closeNow(d.path) }
        return
      }
      setDiff(null); setCommit(null)
      lastContentRef.current.delete(d.path)
      setReveal({ path: d.path, name: d.name, kind: 'erase', text })
    }
    // A file/folder was deleted or renamed in the tree → close its (stale) editor
    // tab(s) immediately, no animation. For a folder, close every open tab beneath
    // it. Matches both the canonical (ws) and raw path forms.
    const onClose = (e: Event) => {
      const p = (e as CustomEvent).detail?.path as string | undefined
      if (!p) return
      const cp = canonPath(p)
      for (const t of tabsRef.current) {
        if (t.path === p || t.path === cp || t.path.startsWith(cp.replace(/\/$/, '') + '/') || t.path.startsWith(p.replace(/\/$/, '') + '/')) {
          draftStore.delete(t.path)  // purge any cached draft for a closed/deleted tab
          closeNow(t.path)
        }
      }
    }
    // A USER save updates the diff-reveal baseline for that file: without this,
    // lastContentRef still holds the pre-save (worker-era) content, so the NEXT worker
    // touch would diff against stale text — showing the user's own just-saved lines as
    // "removed". Keep the baseline current with what's actually on disk now.
    const onSavedFile = (e: Event) => {
      const d = (e as CustomEvent).detail as { path?: string; content?: string } | undefined
      if (d?.path && typeof d.content === 'string') lastContentRef.current.set(canonPath(d.path), d.content)
    }
    // A file/folder was RENAMED in the tree → follow any open tab(s) to the new path so
    // renaming the file you're editing doesn't make it vanish from the editor. Matches
    // the renamed item itself (file) AND, for a folder rename, every open descendant;
    // re-roots each by swapping the `from` prefix → `to`. Preserves which tab was active.
    const onRenamed = (e: Event) => {
      const d = (e as CustomEvent).detail as { from?: string; to?: string; isDir?: boolean } | undefined
      if (!d?.from || !d?.to) return
      const from = canonPath(d.from), to = canonPath(d.to)
      const fromPfx = from.replace(/\/$/, '') + '/'
      const wasActive = activePathRef.current
      for (const t of tabsRef.current) {
        let next: string | null = null
        if (t.path === from || t.path === d.from) next = to
        else if (t.path.startsWith(fromPfx)) next = to.replace(/\/$/, '') + '/' + t.path.slice(fromPfx.length)
        else if (t.path.startsWith(d.from.replace(/\/$/, '') + '/')) next = to.replace(/\/$/, '') + '/' + t.path.slice(d.from.replace(/\/$/, '').length + 1)
        if (!next) continue
        const wasThisActive = t.path === wasActive
        closeNow(t.path)
        // carry the diff-reveal baseline to the new path so the next worker touch diffs right
        const base = lastContentRef.current.get(t.path)
        if (base !== undefined) { lastContentRef.current.set(next, base); lastContentRef.current.delete(t.path) }
        // Carry an UNSAVED draft to the new path too — otherwise renaming a file you're
        // editing re-seeds the remounted editor from disk and silently drops the edit
        // (the dirty dot followed the tab, but the content didn't). Re-key old → new.
        const d2 = draftStore.get(t.path)
        if (d2 !== undefined) { draftStore.set(next, d2); draftStore.delete(t.path) }
        open({ name: next.split('/').pop() || next, path: next, is_dir: false } as FsEntry)
        if (!wasThisActive) setActivePath(wasActive)  // open() focuses the new tab; restore if it wasn't the active one
      }
    }
    window.addEventListener('ne:code-open-file', onOpen as EventListener)
    window.addEventListener('ne:code-open-diff', onDiff as EventListener)
    window.addEventListener('ne:code-open-commit', onCommit as EventListener)
    window.addEventListener('ne:code-erase-file', onErase as EventListener)
    window.addEventListener('ne:code-close-file', onClose as EventListener)
    window.addEventListener('ne:code-file-saved', onSavedFile as EventListener)
    window.addEventListener('ne:code-renamed', onRenamed as EventListener)
    return () => {
      window.removeEventListener('ne:code-open-file', onOpen as EventListener)
      window.removeEventListener('ne:code-open-diff', onDiff as EventListener)
      window.removeEventListener('ne:code-open-commit', onCommit as EventListener)
      window.removeEventListener('ne:code-erase-file', onErase as EventListener)
      window.removeEventListener('ne:code-file-saved', onSavedFile as EventListener)
      window.removeEventListener('ne:code-close-file', onClose as EventListener)
      window.removeEventListener('ne:code-renamed', onRenamed as EventListener)
    }
  }, [open, closeNow, canonPath])

  // Disambiguate tabs whose basenames collide (e.g. two `index.ts`, two
  // `__init__.py`) by suffixing the parent dir segment — VSCode-style — so identical
  // names aren't indistinguishable. Only the colliding tabs get the suffix; unique
  // names stay clean.
  const tabLabels = (() => {
    const counts: Record<string, number> = {}
    for (const t of tabs) counts[t.name] = (counts[t.name] ?? 0) + 1
    const label: Record<string, string> = {}
    for (const t of tabs) {
      if (counts[t.name] > 1) {
        const parent = t.path.replace(/\/$/, '').split('/').slice(-2, -1)[0]
        label[t.path] = parent ? `${t.name} — ${parent}` : t.name
      } else label[t.path] = t.name
    }
    return label
  })()
  // Scroll the active tab into view when it changes — the strip scrolls horizontally
  // (cap 12 tabs), so opening a file whose tab is off-screen (a tree click on the Nth
  // file, or a worker follow-open) would otherwise leave the now-active tab hidden +
  // the user unsure anything happened. 'nearest' is a no-op when it's already visible.
  const activeTabRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    activeTabRef.current?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [activePath])
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
            <button key={key} type="button" onClick={() => reopenPanel(key)} title={`Show ${v.label}`} aria-label={`Show ${v.label}`}
              className="mr-0.5 inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1.5 text-[0.8125rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface">
              <PanelLeftOpen size={14} /> {v.label}
            </button>
          ))}
          <div className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto">
          {tabs.map((t) => (
            // Middle-click closes the tab (standard editor/browser-tab gesture) —
            // routed through requestClose so a dirty tab gets the themed confirm.
            <div key={t.path} ref={(!diff && !commit && t.path === activePath) ? activeTabRef : undefined}
              onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); requestClose(t.path, t.name) } }}
              className={`group flex shrink-0 items-center gap-1.5 rounded-t-md px-2.5 py-1.5 text-[0.8125rem] ${!diff && !commit && t.path === activePath ? 'bg-surface text-on-surface' : 'text-on-surface-low hover:text-on-surface'}`}>
              {/* Selecting a tab shows that file — clear BOTH the working-diff and the
                  commit-patch overlays (either would otherwise keep covering the editor). */}
              <button type="button" onClick={() => { setDiff(null); setCommit(null); setActivePath(t.path) }} title={t.path} className="max-w-[180px] truncate">{tabLabels[t.path]}</button>
              {/* VSCode-style: a dirty tab shows a dot where the close-X sits, swapped
                  for the X on hover, so unsaved work is visible without hovering. */}
              {dirty[t.path] ? (
                <button type="button" onClick={() => requestClose(t.path, t.name)} aria-label={`Close ${t.name} (unsaved changes)`} title="Unsaved changes" className="shrink-0">
                  <span className="size-2 rounded-full group-hover:hidden" style={{ background: 'var(--color-primary)' }} />
                  <X size={12} className="hidden group-hover:block" />
                </button>
              ) : (
                <button type="button" onClick={() => requestClose(t.path, t.name)} aria-label={`Close ${t.name}`} className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"><X size={12} /></button>
              )}
            </div>
          ))}
          </div>
          {/* RIGHT: re-open chip(s) for collapsed right panel(s) (e.g. Tasks). */}
          {collapsedRight.map(([key, v]) => (
            <button key={key} type="button" onClick={() => reopenPanel(key)} title={`Show ${v.label}`} aria-label={`Show ${v.label}`}
              className="ml-0.5 inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1.5 text-[0.8125rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface">
              <PanelRightOpen size={14} /> {v.label}
            </button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1">
        {reveal ? (
          <div className="flex h-full flex-col">
            <div className="flex shrink-0 items-center gap-1.5 border-b border-outline-variant/40 bg-surface-low/40 px-2 py-1 text-on-surface-low text-[0.75rem]">
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
        ) : diff ? (
          <div className="flex h-full flex-col">
            <div className="flex shrink-0 items-center justify-between border-b border-outline-variant/40 bg-surface-low/40 px-2 py-1">
              <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.75rem]"><GitBranch size={12} /> Diff — {diff.name}</span>
              <button type="button" onClick={() => setDiff(null)} aria-label="Close diff" className="rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface"><X size={13} /></button>
            </div>
            <div className="min-h-0 flex-1"><DiffView path={diff.path} name={diff.name} ws={ws} deleted={diff.deleted} /></div>
          </div>
        ) : active ? (
          <FileViewer key={active.path} ref={viewerRef} entry={{ name: active.name, path: active.path, is_dir: false }} draftStore={draftStore}
            onSaved={(content) => window.dispatchEvent(new CustomEvent('ne:code-file-saved', { detail: { path: active.path, content } }))} onSaveAsArtifact={saveFileAsArtifact}
            onMissing={(p) => closeNow(p)}
            onDirtyChange={(d) => { markDirty(active.path, d); if (d) window.dispatchEvent(new CustomEvent('ne:code-editing')) }} />
        ) : (
          <Centered>
            <div className="flex flex-col items-center gap-2 text-on-surface-low">
              <Code2 size={26} className="opacity-40" />
              <p className="text-[0.875rem]">Open a file from the tree to view + edit it.</p>
              {/* Re-engage "follow the worker" — auto-open whatever it edits next. Only
                  while a worker is actually RUNNING; on a terminal/idle project there's
                  nothing to follow, so the button would be a dead no-op. */}
              {running && (
                <button type="button" onClick={() => window.dispatchEvent(new CustomEvent('ne:code-follow-worker'))}
                  className="mt-1 inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[0.75rem] text-on-surface-low hover:bg-surface-high hover:text-on-surface">
                  <Activity size={12} /> Follow the worker
                </button>
              )}
            </div>
          </Centered>
        )}
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

/** A whole-commit unified diff (git show), rendered as a color-coded patch — for
 *  reviewing what a stage's checkpoint commit changed (History → click). */
function CommitView({ ws, hash, subject, onClose }: { ws: string; hash: string; subject: string; onClose: () => void }) {
  // null = loading; '' = loaded-but-empty (no textual diff, e.g. a merge or an
  // empty checkpoint); 'ERR' sentinel = load failed (offer Retry). A real diff is
  // any other string.
  const [diff, setDiff] = useState<string | null>(null)
  const [truncated, setTruncated] = useState(false)
  // The hash did not resolve to a commit in this repo (stale ref after a force-push/
  // rebase, or a workspace re-pointed away) — distinct from a real empty-diff commit.
  const [notFound, setNotFound] = useState(false)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let alive = true
    setDiff(null)
    setTruncated(false)
    setNotFound(false)
    api.fileGitCommit(ws, hash).then((r) => {
      if (!alive) return
      if (r.found === false) { setNotFound(true); setDiff('') }
      else { setDiff(r.diff || ''); setTruncated(!!r.truncated) }
    }).catch(() => { if (alive) setDiff('ERR') })
    return () => { alive = false }
  }, [ws, hash, attempt])
  const failed = diff === 'ERR'
  // Color each patch line by its leading marker so additions/removals/hunks read
  // at a glance (a lightweight diff highlighter, no Monaco needed for a patch).
  const lineColor = (l: string): string | undefined => {
    if (l.startsWith('+') && !l.startsWith('+++')) return 'var(--color-ok)'
    if (l.startsWith('-') && !l.startsWith('---')) return 'var(--color-danger)'
    if (l.startsWith('@@')) return 'var(--color-primary)'
    if (l.startsWith('diff ') || l.startsWith('index ') || l.startsWith('+++') || l.startsWith('---')) return 'var(--color-on-surface-low)'
    return undefined
  }
  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-outline-variant/40 bg-surface-low/40 px-2 py-1">
        <span className="inline-flex min-w-0 items-center gap-1.5 text-on-surface-low text-[0.75rem]">
          <GitBranch size={12} className="shrink-0" /> <span className="font-mono">{hash}</span> <span className="truncate text-on-surface-var">{subject}</span>
        </span>
        <button type="button" onClick={onClose} aria-label="Close commit" className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface"><X size={13} /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-surface p-2">
        {diff === null ? (
          <Centered><Loader2 size={16} className="animate-spin text-on-surface-low" /></Centered>
        ) : failed ? (
          <Centered>
            <div className="flex flex-col items-center gap-2 px-4 text-center text-[0.8125rem]">
              <span style={{ color: 'var(--color-danger)' }}>Couldn't load this commit.</span>
              <button type="button" onClick={() => setAttempt((n) => n + 1)} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-primary hover:bg-surface-high"><RotateCcw size={13} /> Try again</button>
            </div>
          </Centered>
        ) : notFound ? (
          <Centered><p className="px-4 text-center text-on-surface-low text-[0.8125rem]">This commit is no longer in the workspace — its history may have been rewritten (rebase / force-push) or the workspace re-pointed.</p></Centered>
        ) : diff === '' ? (
          <Centered><p className="px-4 text-center text-on-surface-low text-[0.8125rem]">This commit has no textual changes (e.g. a merge or an empty checkpoint).</p></Centered>
        ) : (
          <>
            <pre className="font-mono text-[0.72rem] leading-snug">
              {diff.split('\n').map((l, i) => <div key={i} style={{ color: lineColor(l) }}>{l || ' '}</div>)}
            </pre>
            {truncated && (
              <p className="mt-2 px-1 text-on-surface-low/80 text-[0.7rem]">
                Diff truncated — this commit is large; only the first part is shown. Use the workspace terminal (<span className="font-mono">git show {hash}</span>) for the full patch.
              </p>
            )}
          </>
        )}
      </div>
    </div>
  )
}

/** A terminal docked at the bottom of the editor, rooted at the workspace. The
 *  PTY session is created on mount and KILLED on unmount (hiding the panel or
 *  leaving the cockpit) — otherwise every open/close would orphan a PTY on the
 *  backend (the gateway wedges under accumulated PTYs). */
function BottomTerminal({ ws, hidden, onClose, runCmd }: { ws: string; hidden?: boolean; onClose: () => void; runCmd?: { cmd: string; n: number } | null }) {
  const [tab, setTab] = useState<TermTab | null>(null)
  // Surface a PTY-create failure (workspace dir gone, shell can't spawn) instead of
  // spinning forever — the old .catch(()=>{}) left an eternal loader with no recourse.
  const [err, setErr] = useState<string | null>(null)
  // A PTY spawn failure is often transient (shell momentarily busy, brief resource
  // contention). Bumping `attempt` re-runs the create effect so the user can retry in
  // place rather than toggling the whole terminal off and back on.
  const [attempt, setAttempt] = useState(0)
  // Drag-resizable height (mini-IDE expectation: pull the terminal taller for more
  // output, shorter for more editor). Persisted; grows upward (handle on the top edge).
  // Reuses the same pointer-capture hook as the side panels so a drag crossing the
  // Monaco editor above doesn't stick.
  const { width: height, onHandleDown, onHandleKey, min, max } = useResizablePanel(
    'code-term-h', { def: 260, min: 120, max: 640, side: 'bottom' })
  // The LIVE server-side PTY id this panel owns. Tracked in a ref (not the effect's
  // local) because a Restart inside TerminalView mints a NEW session — without following
  // it here, cleanup would delete the stale (dead) id and LEAK the restarted PTY, the
  // exact "gateway wedges under accumulated PTYs" failure this teardown exists to prevent.
  const liveSessionRef = useRef<string>('')
  useEffect(() => {
    let alive = true
    setErr(null); setTab(null)
    api.createTerminal(ws).then((r) => {
      liveSessionRef.current = r.session_id
      if (alive) setTab({ id: r.session_id, label: 'Terminal', cwd: r.cwd, shell: r.shell })
      else api.deleteTerminal(r.session_id).catch(() => {})  // unmounted mid-create → don't leak
    }).catch((e) => { if (alive) setErr((e as Error).message || 'Could not start a terminal here.') })
    return () => { alive = false; if (liveSessionRef.current) api.deleteTerminal(liveSessionRef.current).catch(() => {}) }
  }, [ws, attempt])
  // Run a requested build/test command on THIS terminal's own session, once it can
  // actually accept input — never the global "active" terminal, which could be an
  // unrelated one. Target the LIVE session id (liveSessionRef), not tab.id: a Restart
  // mints a new PTY, so tab.id goes stale + dead — running against it would silently
  // no-op. runInTerminalWhenReady retries until the SEND succeeds (not merely until
  // the session registers — registration happens synchronously at mount while the
  // WebSocket is still CONNECTING, so a send at that instant returned false and the
  // command was silently dropped on every cold open). Keyed on runCmd.n so a repeat
  // click re-fires.
  useEffect(() => {
    if (!runCmd || !tab) return
    return runInTerminalWhenReady(runCmd.cmd, () => liveSessionRef.current || tab.id)
  }, [runCmd?.n, tab?.id])  // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="relative flex shrink-0 flex-col border-t border-outline-variant/40"
      // Hide via CSS (not unmount) so the PTY + running process + scrollback survive a
      // header Hide/Show toggle. display:none collapses it; TerminalView's ResizeObserver
      // re-fits xterm when it's shown again (0-size → sized).
      style={{ height, display: hidden ? 'none' : undefined }} aria-hidden={hidden}>
      {/* top-edge resize handle (grows the terminal upward) */}
      <div onPointerDown={onHandleDown} onKeyDown={onHandleKey} role="separator" aria-orientation="horizontal"
        tabIndex={0} aria-label="Resize terminal — arrow keys to resize"
        aria-valuenow={Math.round(height)} aria-valuemin={min} aria-valuemax={max}
        className="group/th absolute inset-x-0 top-0 z-20 h-2 cursor-row-resize outline-none focus-visible:bg-primary/30">
        <div className="mx-auto h-0.5 w-full bg-transparent transition-colors group-hover/th:bg-primary/60 group-focus-visible/th:bg-primary" />
      </div>
      <div className="flex shrink-0 items-center justify-between border-b border-outline-variant/40 bg-surface-low/40 px-3 py-1">
        <span className="inline-flex items-center gap-1.5 text-on-surface-low text-[0.75rem]"><TerminalSquare size={12} /> Terminal · {ws.split('/').slice(-1)[0]}</span>
        <button type="button" onClick={onClose} aria-label="Hide terminal" className="rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface"><X size={13} /></button>
      </div>
      <div className="min-h-0 flex-1">
        {err ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center text-[0.8125rem]">
            <span style={{ color: 'var(--color-danger)' }}>{err}</span>
            <div className="flex items-center gap-2">
              <button type="button" onClick={() => setAttempt((n) => n + 1)} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-primary hover:bg-surface-high"><RotateCcw size={13} /> Try again</button>
              <button type="button" onClick={onClose} className="rounded-md px-2 py-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface">Close</button>
            </div>
          </div>
        ) : tab ? <TerminalView tab={tab} onExited={() => {}} onClose={onClose}
            onSession={(sid) => { liveSessionRef.current = sid }} /> : <Centered><Loader2 size={16} className="animate-spin text-on-surface-low" /></Centered>}
      </div>
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
    <div className="mb-3 rounded-lg p-2.5 text-[0.8125rem]"
      style={{ background: `color-mix(in srgb, ${meta.tone} 12%, transparent)` }}>
      <div className="inline-flex items-center gap-1.5" style={{ color: meta.tone, fontVariationSettings: '"wght" 550' }}>
        <Icon size={14} /> {meta.label}
      </div>
      {bits.length > 0 && <p className="mt-1 text-on-surface-var text-[0.75rem]">{bits.join(' · ')}</p>}
      {!meta.ok && p.error_message && <p className="mt-1 text-on-surface-low text-[0.7rem]">{p.error_message}</p>}
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
          <p className="text-on-surface-low text-[0.7rem]">What was built — click to open:</p>
          <FilesTouched files={[...files]} ws={ws} max={12} />
        </div>
      )}
    </div>
  )
}

/** One cycle's finding: summary + key insight + touched-file chips, with the
 *  worker's EVIDENCE (test output / command results — the proof it didn't
 *  self-certify) behind a collapsed disclosure since it can be verbose. */
// Evidence may arrive as a plain string, or as a dict/array of checks (e.g.
// {py_compile: "…", directory_listing: "…"}) depending on how the worker recorded
// the cycle — render any shape as readable text instead of crashing on .trim().
function evidenceToText(ev: unknown): string {
  if (ev == null) return ''
  if (typeof ev === 'string') return ev.trim()
  if (Array.isArray(ev)) return ev.map((v) => evidenceToText(v)).filter(Boolean).join('\n')
  if (typeof ev === 'object') {
    return Object.entries(ev as Record<string, unknown>)
      .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join('\n').trim()
  }
  return String(ev)
}

function FindingCard({ finding: f, ws }: { finding: CodeFinding; ws: string }) {
  const [showEvidence, setShowEvidence] = useState(false)
  const evidence = evidenceToText(f.evidence)
  return (
    <div className="rounded-lg border border-outline-variant/40 bg-surface-container/50 p-2.5">
      <div className="mb-1 flex items-center gap-1.5 text-[0.7rem] text-on-surface-low">
        <span className="rounded-pill bg-surface-high px-1.5 tabular-nums">cycle {f.cycle}</span>
        {f.stage && <span className="rounded-pill bg-surface-high px-1.5">{f.stage}</span>}
      </div>
      {f.summary && <p className="text-on-surface-var text-[0.8125rem]">{f.summary}</p>}
      {f.key_insight && <p className="mt-1 text-on-surface-low text-[0.75rem]">→ {f.key_insight}</p>}
      <FilesTouched files={f.files_touched} ws={ws} />
      {evidence && (
        <div className="mt-1.5">
          <button type="button" onClick={() => setShowEvidence((v) => !v)} aria-expanded={showEvidence}
            aria-label={showEvidence ? 'Hide evidence' : 'Show evidence'}
            className="inline-flex items-center gap-1 text-[0.7rem] text-on-surface-low hover:text-on-surface">
            {showEvidence ? <ChevronDown size={11} /> : <ChevronRight size={11} />} evidence
          </button>
          {showEvidence && (
            <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-surface-high/60 p-2 text-[0.7rem] leading-snug text-on-surface-var whitespace-pre-wrap break-words">{evidence}</pre>
          )}
        </div>
      )}
    </div>
  )
}

/** Project-level footer for the Tasks panel: signals that aren't task-scoped
 *  (attended question, gate failure, stall, terminal outcome) + the steer box.
 *  Per-task agent loop events live under each task card (StageTasks), not here. */
function ProjectFooter({ project, gateFail, stalled, onNudged, onStartNew }: { project: CodeProject; gateFail: { label: string; command: string; output: string } | null; stalled: { stage: string; title: string; findings: number } | null; onNudged: () => void; onStartNew?: () => void }) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  // Synchronous double-send guard (sending state is async — rapid double Enter/click
  // both pass before the re-render → a double nudge). Mirrors the task steer + C415.
  const sendingRef = useRef(false)
  // The steer textarea — focused when the user clicks "Respond" on the needs-input
  // toast, so answering is one click away wherever they were.
  const steerRef = useRef<HTMLTextAreaElement>(null)
  // Auto-grow the box to fit a multi-line steer (and shrink back after send clears it).
  useEffect(() => { autoGrowTextarea(steerRef.current) }, [text])
  useEffect(() => {
    const onFocus = () => { steerRef.current?.focus(); steerRef.current?.scrollIntoView({ block: 'nearest' }) }
    window.addEventListener('ne:code-focus-steer', onFocus)
    return () => window.removeEventListener('ne:code-focus-steer', onFocus)
  }, [])
  const [steers, setSteers] = useState<{ text: string; failed?: boolean }[]>([])
  const findings = project.findings ?? []

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
    <div className="shrink-0 border-t border-outline-variant/40">
      <div className="max-h-[40vh] overflow-y-auto px-2 pt-2">
        {/* Attended question — the call to action; answer in the steer box below. */}
        {project.status === 'needs_input' && project.pending_question?.question && (
          <div role="alert" className="mb-2 rounded-lg p-2.5 text-[0.8125rem]"
            style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={{ color: 'var(--color-info)', fontVariationSettings: '"wght" 550' }}>
              <HelpCircle size={14} /> The worker needs your input
            </div>
            <p className="whitespace-pre-wrap text-on-surface">{project.pending_question.question}</p>
            {/* The worker's reasoning for asking (what changes based on the answer) —
                context that helps the user answer well. */}
            {project.pending_question.why && (
              <p className="mt-1 whitespace-pre-wrap text-on-surface-low text-[0.75rem]">{project.pending_question.why}</p>
            )}
            <div className="mt-2 flex items-center gap-2">
              <p className="flex-1 text-on-surface-low text-[0.7rem]">Answer below to resume the build.</p>
              <button type="button" disabled={sending}
                onClick={() => steer('Proceed with your best judgment / the sensible default you proposed. Record the assumption in your finding and continue.')}
                className="shrink-0 rounded-md px-2 py-1 text-[0.7rem] transition-colors hover:bg-info/10 disabled:opacity-50"
                style={{ color: 'var(--color-info)' }}>
                Use your best judgment
              </button>
            </div>
          </div>
        )}
        {/* needs_input but NO structured question (the question file failed to write /
            was cleared, or a generic pause): the header pill says "needs input" but
            without this the panel gave zero explanation + no call to action. Fall back
            to a generic prompt so the user always knows what to do — steer to resume. */}
        {project.status === 'needs_input' && !project.pending_question?.question && (
          <div role="alert" className="mb-2 rounded-lg p-2.5 text-[0.8125rem]"
            style={{ background: 'color-mix(in srgb, var(--color-info) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={{ color: 'var(--color-info)', fontVariationSettings: '"wght" 550' }}>
              <HelpCircle size={14} /> The worker is waiting on you
            </div>
            <p className="text-on-surface-var text-[0.75rem]">It paused for input but didn't leave a specific question. Steer it below with direction (or tell it to use its best judgment), then it resumes.</p>
            <div className="mt-2 flex justify-end">
              <button type="button" disabled={sending}
                onClick={() => steer('Proceed with your best judgment / the sensible default. Record any assumption in your finding and continue.')}
                className="shrink-0 rounded-md px-2 py-1 text-[0.7rem] transition-colors hover:bg-info/10 disabled:opacity-50"
                style={{ color: 'var(--color-info)' }}>
                Use your best judgment
              </button>
            </div>
          </div>
        )}
        <OutcomeBanner project={project} findings={findings} />
        {/* BLOCKED — persisted (reload-safe) explanation of WHY the build paused (e.g.
            the stall-pause: a stage produced many cycles without clearing its gate).
            The transient `stalled` SSE banner below only shows while running; once the
            supervisor flips the project to blocked, this is what tells the user what
            happened + that a steer/Resume gets it going again. */}
        {project.status === 'blocked' && project.error_message && (
          <div className="mb-2 rounded-lg p-2.5 text-[0.8125rem]"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={{ color: 'var(--color-warn)', fontVariationSettings: '"wght" 550' }}>
              <AlertTriangle size={14} /> Paused — needs you
            </div>
            <p className="whitespace-pre-wrap text-on-surface-var">{project.error_message}</p>
            <p className="mt-1 text-on-surface-low text-[0.7rem]">Steer it below (or relax a stage criterion), then Resume.</p>
          </div>
        )}
        {gateFail && project.status === 'running' && (
          <div role="alert" className="mb-2 rounded-lg p-2.5 text-[0.8125rem]"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={{ color: 'var(--color-warn)', fontVariationSettings: '"wght" 550' }}>
              <XCircle size={14} /> Supervisor {gateFail.label} check failed — stage held
            </div>
            {gateFail.command && <p className="font-mono text-on-surface-low text-[0.7rem]">{gateFail.command}</p>}
            {gateFail.output && (
              <pre className="mt-1 max-h-40 overflow-auto rounded-md bg-surface-high/60 p-2 text-[0.7rem] leading-snug text-on-surface-var whitespace-pre-wrap break-words">{gateFail.output}</pre>
            )}
          </div>
        )}
        {/* Show while running (early stall warning) AND when blocked: the watchdog's
            stall escalation flips the project to BLOCKED, and this banner is what
            explains WHY — gating on 'running' alone hid it exactly when the user most
            needs it (the project is parked awaiting their steer). */}
        {stalled && !gateFail && (project.status === 'running' || project.status === 'blocked') && (
          <div role="status" className="mb-2 rounded-lg p-2.5 text-[0.8125rem]"
            style={{ background: 'color-mix(in srgb, var(--color-warn) 12%, transparent)' }}>
            <div className="mb-1 inline-flex items-center gap-1.5" style={{ color: 'var(--color-warn)', fontVariationSettings: '"wght" 550' }}>
              <AlertTriangle size={14} /> “{stalled.title}” {project.status === 'blocked' ? 'is stuck — paused for you' : 'seems stuck'}
            </div>
            <p className="text-on-surface-var text-[0.7rem]">{stalled.findings} cycles in and the gate still hasn't passed — steer it or relax a criterion{project.status === 'blocked' ? ', then Resume' : ''}.</p>
          </div>
        )}
        {/* the most recent steer, so the user sees their message landed */}
        {lastSteer && (
          <div className="mb-2 self-end rounded-xl bg-primary/15 px-2.5 py-1.5 text-[0.8125rem] text-on-surface-var">
            {lastSteer.text}
            {'applied_cycle' in lastSteer && (lastSteer as { applied_cycle?: number }).applied_cycle != null && (
              <span className="ml-1.5 text-[0.65rem] text-on-surface-low">· applied cycle {(lastSteer as { applied_cycle?: number }).applied_cycle}</span>
            )}
            {'failed' in lastSteer && (lastSteer as { failed?: boolean }).failed && <span className="ml-1.5 text-[0.7rem] text-danger">· failed to send</span>}
          </div>
        )}
      </div>
      {/* steer box — only while the worker can ACT on it. */}
      <div className="p-2">
        {STEERABLE.has(project.status) ? (
          <div className="flex items-end gap-1.5 rounded-xl bg-surface-container px-2.5 py-1.5">
            <textarea ref={steerRef} value={text} onChange={(e) => setText(e.target.value)} rows={1}
              placeholder={project.status === 'needs_input' ? 'Answer the worker…' : 'Steer the worker…'}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); steer() } }}
              className="max-h-24 min-h-0 flex-1 resize-none overflow-y-auto bg-transparent text-on-surface text-[0.8125rem] outline-none placeholder:text-on-surface-low" />
            <button type="button" onClick={() => steer()} disabled={!text.trim() || sending} aria-label="Send steer"
              className="grid size-7 shrink-0 place-items-center rounded-full disabled:opacity-40"
              style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}>
              {sending ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
            </button>
          </div>
        ) : project.status === 'ready' || project.status === 'review' ? (
          <p className="px-1.5 py-1 text-center text-on-surface-low text-[0.75rem]">Press Start to launch — steer the worker once it's running.</p>
        ) : (
          // Finished: the steer box is gone, so give the dangling "start a new one"
          // call-to-action a real button (was prose pointing nowhere — the only paths
          // out were buried in the overflow menu). Navigates to the new-project composer.
          <div className="flex flex-col items-center gap-1.5 px-1.5 py-1 text-center">
            <p className="text-on-surface-low text-[0.75rem]">This project has finished.</p>
            <button type="button" onClick={() => onStartNew?.()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary/15 px-3 py-1.5 text-[0.75rem] text-primary transition-colors hover:bg-primary/25">
              <Plus size={13} /> Start a new project
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

/** Clickable chips for the files a cycle touched. Only files under the project's
 *  workspace are openable (the worker also lists its own findings/ paths, which
 *  live outside the workspace and aren't editable here); those are dropped. Click
 *  opens the file in the center editor via the same event the file tree uses. */
function FilesTouched({ files, ws, max = 8 }: { files?: string[]; ws: string; max?: number }) {
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
            className="inline-flex max-w-full items-center gap-1 rounded bg-surface-high px-1.5 py-0.5 text-[0.7rem] text-on-surface-low transition-colors hover:text-primary">
            <FileCode size={10} className="shrink-0" />
            <span className="truncate">{rel}</span>
          </button>
        )
      })}
      {hidden > 0 && (
        <button type="button" onClick={() => setExpanded(true)}
          className="rounded px-1.5 py-0.5 text-[0.7rem] text-on-surface-low/80 hover:text-primary"
          title={`Show ${hidden} more file${hidden === 1 ? '' : 's'}`}>+{hidden} more</button>
      )}
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

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex h-full items-center justify-center">{children}</div>
}

// re-export for the section's type usage
export type { CodeStage }
