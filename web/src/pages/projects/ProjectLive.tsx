import { useId } from 'react'
import type { RunningRow } from '../../lib/api'
import type { NeedsYouRow } from '../../lib/needsYou'
import { RunningLane } from '../dashboard/RunningLane'
import { NeedCard, NeedsUnreadable, rowKey, type CardActions, type Outcome } from '../dashboard/NeedCards'
import { GroupHeader } from './ProjectWork'
import { NEEDS_YOU_DOT, WORK_STATE_LABEL } from './ProjectWorkRow'

/** ── A project's page: Needs you and Running now, on Home's own cards ──────────────────────────
 *
 *  Needs you draws Home's cards (`NeedCards.tsx`, extracted for this), exactly as Home draws them,
 *  over this project's waiting items: Allow and Deny on Home's path with its second confirmations,
 *  stale handling and keys, a card opening its detail beside the work (`?open=need:<key>`), and no
 *  project chip inside its own project. While the waiting read's `unreadable` is not empty, Home's
 *  line sits first in the group, and the group is drawn even when it holds nothing else; then, with
 *  nothing else waiting here, its second line in the project's form ("Nothing else in Garden is
 *  waiting on you."), never Home's, which would speak for every project.
 *
 *  Running now is Home's `RunningLane` over this project's running rows, with `opens="work"`: a card
 *  opens its work at its route rather than a detail, named as a work row ("Code session: Fix the
 *  irrigation sensor tests, testing"), with no second Open link. Neither group is drawn when it
 *  holds nothing. While PersonalClaw can't be reached, each answer is off, saying so (`off`). */

export function ProjectNeedsYou({ name, rows, unreadable, loading, outcomes, actions, now, open, onOpen, off }: {
  name: string
  rows: NeedsYouRow[]
  unreadable: readonly string[]
  loading: boolean
  outcomes: Record<string, Outcome>
  actions: CardActions
  now: number
  /** The waiting item whose detail is open beside the work, by its key. */
  open?: string
  onOpen: (key: string) => void
  /** Why no answer can be sent now ("Can't reach PersonalClaw"): each answer is off, saying it. */
  off?: string
}) {
  const id = useId()
  if (rows.length === 0 && unreadable.length === 0) return null
  return (
    <section aria-labelledby={id} data-group="needs-you" className="flex min-w-0 flex-col gap-s">
      {/* Named by the state it holds, in the registry's word (`WORK_STATE_LABEL`, "Needs you"). */}
      <GroupHeader id={id} title={WORK_STATE_LABEL.needs_input} count={rows.length || undefined} dot={NEEDS_YOU_DOT} />
      <NeedsUnreadable unreadable={unreadable} nothingElse={rows.length === 0 && !loading} project={name} />
      {rows.length > 0 ? (
        <ul className="flex min-w-0 flex-col gap-s">
          {rows.map((r) => (
            <li key={r.key} className="min-w-0">
              <NeedCard row={r} outcome={outcomes[r.key]} actions={actions} now={now}
                selected={open !== undefined && (rowKey(r) === `need:${open}` || r.key === open)} onOpen={() => onOpen(r.key)} project={null} off={off} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  )
}

export function ProjectRunning({ rows, navigate }: { rows: RunningRow[]; navigate: (to: string) => void }) {
  if (rows.length === 0) return null
  return (
    <div data-group="running-now" className="min-w-0">
      <RunningLane rows={rows} loading={false} failed={false} empty="" opens="work" onOpen={(r) => { if (r.route) navigate(r.route) }} />
    </div>
  )
}
