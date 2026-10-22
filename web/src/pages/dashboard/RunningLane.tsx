import { useId, useState } from 'react'
import type { RunningRow } from '../../lib/api'
import { clockTime } from '../../lib/epoch'
import { rowSubject } from '../../lib/rowSubject'
import { INTERRUPTED_LINE, KIND_WORD, phaseOf } from '../../lib/runningRows'
import type { CutRow } from '../../lib/useCutOff'
import { Button } from '../../ui/Button'
import { StatusPill } from '../../ui/StatusPill'
import { TextLink } from '../../ui/TextLink'
import { RowHitTarget } from '../../ui/RowHitTarget'

// ── Home's Running Now: the running list ─────────────────────────────────────────────────────────
//
// Every row is one piece of live work as `GET /api/running` names it (`running_now`): its name, its
// exact phase as text (the tone only summarises it), what it is doing in the gateway's words, the
// agent doing it, when it last changed, and the route that opens it. A blocked row (queued for a
// slot, retrying, waiting on a busy model) says so apart from working, with its line as the reason,
// and a row that waits on you says how much does. Rows stay in the list's order, oldest first, so
// a phase change never moves one.
//
// The just-finished tail is Recent and completed's (`FinishedRows`, below): the gateway's last
// endings, each saying how it ended, and the work a restart cut off, which the gateway kept no
// ending of.

/** Rows shown before "Show all": a lane of fifty cards is a wall, and the count says the rest. */
export const LARGE_LIST = 25


export function RunningLane({
  rows,
  loading,
  failed,
  empty,
  onOpen,
  selected,
}: {
  rows: RunningRow[]
  loading: boolean
  /** The list could not be read at all: the group says so rather than that nothing runs. */
  failed: boolean
  /** What an empty group says. */
  empty: string
  /** Opens a row's detail, beside the groups or in their place. */
  onOpen?: (row: RunningRow) => void
  /** The work whose detail is open. */
  selected?: string
}) {
  const headingId = useId()
  const [all, setAll] = useState(false)
  const shown = all ? rows : rows.slice(0, LARGE_LIST)
  const blocked = rows.filter((r) => r.blocked).length
  return (
    <section aria-labelledby={headingId} data-home-group="running-now" className="flex min-w-0 flex-col gap-s">
      <div className="flex items-center gap-s">
        <h2 id={headingId} data-type="label-l" className="text-on-surface-var">Running Now</h2>
        {/* No counter reading 0: the group's sentence says nothing runs. */}
        {rows.length > 0 ? <span data-type="label-s" data-lane-count className="text-on-surface-low">{rows.length}</span> : null}
        {blocked > 0 ? (
          <span data-type="label-s" className="text-on-surface-low">{`${blocked} blocked`}</span>
        ) : null}
        <span className="h-px flex-1 bg-outline-variant/40" />
      </div>
      {rows.length === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">
          {loading ? 'Loading…' : failed ? 'Could not read what is running.' : empty}
        </p>
      ) : (
        <ul className="flex min-w-0 flex-col gap-s">
          {shown.map((r) => <RunningCard key={r.work} row={r} onOpen={onOpen} selected={selected === r.work} />)}
        </ul>
      )}
      {rows.length > LARGE_LIST ? (
        // One button that stays, so focus stays on it when it is pressed.
        <div>
          <Button size="xs" variant="ghost-accent" ariaExpanded={all} onClick={() => setAll((was) => !was)}>
            {all ? `Show the first ${LARGE_LIST}` : `Show all ${rows.length}`}
          </Button>
        </div>
      ) : null}
    </section>
  )
}

/** The just-finished tail: the gateway's last endings, newest first, and before them the work a
 *  restart cut off, which the gateway kept no ending of. An ending the gateway kept wins over the
 *  page's own word for the same work. Renders nothing when nothing has ended. */
export function FinishedRows({ finished, interrupted, onOpen, selected }: {
  finished: RunningRow[]
  /** Work a restart cut off (`useCutOff`). */
  interrupted: CutRow[]
  onOpen?: (row: RunningRow) => void
  /** The work whose detail is open. */
  selected?: string
}) {
  const tailId = useId()
  const ended = new Set(finished.map((r) => r.work))
  const cut = interrupted.filter((i) => !ended.has(i.row.work))
  if (finished.length + cut.length === 0) return null
  return (
    <div className="flex min-w-0 flex-col gap-xs">
      <p id={tailId} data-type="label-s" className="text-on-surface-low">Just finished</p>
      <ul aria-labelledby={tailId} className="flex min-w-0 flex-col gap-xs">
        {cut.map((i) => (
          <EndedCard key={`cut:${i.row.work}`} row={i.row} pill="Interrupted" tone="danger" line={INTERRUPTED_LINE} onOpen={onOpen} selected={selected === i.row.work} />
        ))}
        {finished.map((r) => <EndedCard key={r.work} row={r} onOpen={onOpen} selected={selected === r.work} />)}
      </ul>
    </div>
  )
}

/** What makes a row open its detail: the row-wide hit target (`RowHitTarget`) and its wrapper's
 *  click, with the focus ring drawn on the row. Nothing when the page has no detail to open. */
export function openable(name: string, onOpen?: () => void, selected = false) {
  if (!onOpen) return { props: {}, target: null, className: '' }
  return {
    props: { tabIndex: -1, onClick: onOpen },
    target: <RowHitTarget label={`Show the details of ${name}`} selected={selected} />,
    className: `relative cursor-pointer has-[>button:focus-visible]:ring-2 has-[>button:focus-visible]:ring-inset has-[>button:focus-visible]:ring-primary ${selected ? 'ring-1 ring-primary' : ''}`,
  }
}

function RunningCard({ row, onOpen, selected }: { row: RunningRow; onOpen?: (row: RunningRow) => void; selected?: boolean }) {
  const nameId = useId()
  const look = phaseOf(row)
  const updated = clockTime(row.since)
  const waiting = Array.isArray(row.waiting) ? row.waiting.length : 0
  const open = openable(row.name, onOpen ? () => onOpen(row) : undefined, selected)
  return (
    <li aria-labelledby={nameId} {...open.props}
      className={`flex min-w-0 flex-col gap-xs rounded-lg border border-outline-variant/40 bg-surface-low/60 p-s ${open.className}`}>
      {open.target}
      <p id={nameId} data-type="label-m" className="min-w-0 truncate text-on-surface-var">{row.name}</p>
      <div className="flex min-w-0 flex-wrap items-center gap-xs">
        {row.blocked ? <StatusPill tone="neutral">Blocked</StatusPill> : null}
        <StatusPill tone={look.tone}>{look.label}</StatusPill>
        {row.line && row.line !== look.label ? (
          <span data-type="body-s" className="min-w-0 text-on-surface">{row.line}</span>
        ) : null}
      </div>
      <div data-type="body-s" className="flex min-w-0 flex-wrap items-center gap-xs text-on-surface-low">
        {row.agent ? <span>{row.agent}</span> : null}
        {updated ? <span>{`Updated ${updated}`}</span> : null}
        {waiting > 0 ? <span className="text-on-surface">{`${waiting} waiting on you`}</span> : null}
      </div>
      {row.route ? (
        <TextLink href={row.route} ink="emphasis" size="sm" aria-label={`Open ${rowSubject([row.name, KIND_WORD[row.kind] ?? row.kind])}`}>Open</TextLink>
      ) : null}
    </li>
  )
}

/** One ending: how it ended (its phase, or the page's word for work a restart cut off), when, and
 *  the route that opens what it left. */
function EndedCard({ row, pill, tone, line, onOpen, selected }: {
  row: RunningRow
  pill?: string
  tone?: 'danger'
  line?: string
  onOpen?: (row: RunningRow) => void
  selected?: boolean
}) {
  const nameId = useId()
  const look = phaseOf(row)
  const at = pill ? '' : clockTime(row.since)
  const said = line ?? row.line
  const word = pill ?? look.label
  const open = openable(row.name, onOpen ? () => onOpen(row) : undefined, selected)
  return (
    <li aria-labelledby={nameId} {...open.props}
      className={`flex min-w-0 flex-col gap-xs rounded-lg border border-outline-variant/30 p-s ${open.className}`}>
      {open.target}
      <p id={nameId} data-type="label-m" className="min-w-0 truncate text-on-surface-var">{row.name}</p>
      <div className="flex min-w-0 flex-wrap items-center gap-xs">
        <StatusPill tone={tone ?? look.tone}>{word}</StatusPill>
        {said && said !== word ? <span data-type="body-s" className="min-w-0 text-on-surface">{said}</span> : null}
        {at ? <span data-type="body-s" className="text-on-surface-low">{`Ended ${at}`}</span> : null}
      </div>
      {row.route ? (
        <TextLink href={row.route} ink="emphasis" size="sm" aria-label={`Open ${rowSubject([row.name, word])}`}>Open</TextLink>
      ) : null}
    </li>
  )
}
