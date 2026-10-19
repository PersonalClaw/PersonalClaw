import { motion } from 'framer-motion'
import { AlertTriangle } from 'lucide-react'
import { Button } from '../../../../ui/Button'
import { type CodeProject } from '../../../../lib/api'
import { type RunFlags } from '../../../loops/runFold'
import { RepromptNotice } from '../../../loops/RepromptNotice'
import { messageEnter } from '../../../../design/motion'
import { TERMINAL_STATUSES } from '../cockpitShared'
import { type MissingCommandNotice } from '../cockpitCommands'

// The shared chrome for this page's full-width warn strips (missing command, brownfield
// needs a workspace, workspace vanished). One object, so the tint lives in ONE place
// instead of being re-typed per banner — the status-tint ratchet counts each spelling.
export const WARN_STRIP: React.CSSProperties = {
  background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)',
  color: 'var(--color-warn)',
}

// ── The cockpit's notices ─────────────────────────────────────────────────────────────────────
//
// The strips under the cockpit's title that say why it cannot do something: a build or test command
// whose program is not on PATH, a runtime that cannot run the project now, a worker asked again for
// its finding, a brownfield draft with no workspace yet, and a workspace folder that is gone.

export function CockpitNotices({ project: p, ws, wsMissing, missingCommands, runsOn, runtimeEditable, runFlags, setPickWs }: {
  project: CodeProject; ws: string; wsMissing: boolean; missingCommands: MissingCommandNotice[]
  runsOn: { unavailable: string }; runtimeEditable: boolean; runFlags: RunFlags; setPickWs: (on: boolean) => void
}) {
  return (
    <>
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
    </>
  )
}
