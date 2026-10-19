import { api, type CodeProject } from '../../../lib/api'
import { confirmDelete } from '../../../ui/dialog'
import { LOOP_ACTION_SOURCE_STATUSES } from '../../../lib/loopStatus'
import { type RuntimeGroup } from '../../../lib/agents'
import { runtimeFields, runtimeOf, showRuntime, type LoopRuntime } from '../../loop/loopRuntime'
import { codeDeleteBody } from '../codeMeta'
import { type CodeToastState } from '../CodeToast'
import { missingCommandNotices } from './cockpitCommands'
import { loopToCodeProject } from './codeProject'

// ── The code cockpit's actions ────────────────────────────────────────────────────────────────
//
// What the cockpit derives from a project it has, and what it does to it: start, pause, resume and
// stop behind one in-flight guard, move a project that has not started onto another runtime, run
// the build or test command in the cockpit's terminal, bind a brownfield draft's workspace and
// start it, and delete the project once asked. Each failure is said through the cockpit toast.
//
// Not a hook: the page calls it once it knows it has a project, after `useCodeCockpit`'s state.

export function cockpitActions({ id, project, onDeleted, acting, setActing, setProject, setToast, setTasksNonce, load, setPickWs, setShowTerm,
  setPendingRunCmd, pendingRunNonce, runtimes, runtimesLoaded }: {
  id: string; project: CodeProject; onDeleted: () => void
  acting: boolean; setActing: (on: boolean) => void; setProject: (p: CodeProject) => void
  setToast: (t: CodeToastState | null) => void; setTasksNonce: (f: (n: number) => number) => void; load: () => void
  setPickWs: (on: boolean) => void; setShowTerm: (on: boolean) => void
  setPendingRunCmd: (cmd: { cmd: string; n: number } | null) => void; pendingRunNonce: { current: number }
  runtimes: RuntimeGroup[]; runtimesLoaded: boolean
}) {
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
  return {
    p, ws, fileRoot, active, missingCommands, runsOn, runtimeEditable, missingBuild, missingTest, act, moveRuntime,
    runInWorkspaceTerminal, pickWorkspace, del,
  }
}
