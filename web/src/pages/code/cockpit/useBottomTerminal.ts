import { useEffect, useRef, useState } from 'react'
import { api } from '../../../lib/api'
import { runInTerminalWhenReady } from '../../terminal/terminalBridge'
import type { TermTab } from '../../terminal/TerminalPage'
import { useResizablePanel } from '../../../ui/useResizablePanel'

// ── The cockpit terminal's controller ──────────────────────────────────────────────────────────
//
// The workspace shell under the cockpit's editor: it opens a PTY in the workspace, follows a Restart
// to the new one, closes whichever is live when the drawer goes, says why one could not start and
// tries again, and runs a build or test command on its own session once that session takes input.
// `BottomTerminal.tsx` draws it.

export function useBottomTerminal({ ws, runCmd }: { ws: string; runCmd?: { cmd: string; n: number } | null }) {
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
    'code-term-h', { def: 260, min: 120, max: 640, side: 'bottom', collapsible: true })
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
  return { tab, err, setAttempt, height, onHandleDown, onHandleKey, min, max, liveSessionRef }
}
