import { useId, useRef } from 'react'
import { Bell } from 'lucide-react'
import { Popover, MenuRow } from '../../ui/Popover'
import { PillButton } from '../../ui/composer/controls'
import type { LoopReport } from '../../lib/api'
import { menuCursorKeydown, useMenuCursor } from '../../lib/useMenuCursor'

/** The three ways a loop may report (`Loop.report`, #3824), in the order they are offered. */
export const REPORT_CHOICES: readonly { id: LoopReport; label: string; meaning: string }[] = [
  { id: 'as_it_goes', label: 'As it goes', meaning: 'A note after each cycle, and when it ends.' },
  { id: 'when_done', label: 'Only when it’s done', meaning: 'One note when it completes or fails.' },
  { id: 'if_stuck', label: 'Only if it gets stuck', meaning: 'Nothing unless it fails or can’t confirm it finished.' },
]

/** What a new loop reports unless its owner says otherwise. */
export const DEFAULT_REPORT: LoopReport = 'when_done'

/** A loop's choice in words: the record's, or a new loop's when the gateway sent none. */
export const reportLabel = (report: LoopReport | undefined): string =>
  (REPORT_CHOICES.find((c) => c.id === report) ?? REPORT_CHOICES.find((c) => c.id === DEFAULT_REPORT)!).label

/** "Reports": how a loop tells its owner how it is going, asked once when it starts and changeable
 *  from its strip while it runs. Whatever the choice, a loop waiting on its owner still says so in
 *  the Inbox: the choice quiets reports, it never asks. Drawn as the composer's own pill; its menu
 *  is radio items with the shared cursor (`lib/useMenuCursor`), opening on the loop's choice. */
export function LoopReportPill({ value, onChange, placement = 'top', disabled = false, iconBelowMd = false }: {
  value: LoopReport
  onChange: (report: LoopReport) => void
  placement?: 'top' | 'bottom'
  disabled?: boolean
  /** The pill as its icon below 768 px (`PillButton`'s): the new loop's composer, whose one row
   *  at 600 px holds Runs on, the Execution Boundary, this and the send cluster. */
  iconBelowMd?: boolean
}) {
  return (
    <Popover portal width={300} placement={placement} trigger={(open, toggle) => (
      <PillButton icon={<Bell size={16} strokeWidth={2} />} label={reportLabel(value)} dimension="Reports"
        title={`Reports: ${reportLabel(value)}`} iconBelowMd={iconBelowMd}
        open={open} toggle={toggle} wide disabledReason={disabled ? 'Wait for the loop to finish starting' : undefined} />
    )}>
      {(close) => <ReportMenu value={value} close={close} onPick={(r) => { onChange(r); close() }} />}
    </Popover>
  )
}

function ReportMenu({ value, close, onPick }: { value: LoopReport; close: () => void; onPick: (report: LoopReport) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const noteId = useId()
  const { move, tabIndexFor } = useMenuCursor({
    containerRef: ref, count: REPORT_CHOICES.length, openKey: 'open',
    initialIndex: Math.max(0, REPORT_CHOICES.findIndex((c) => c.id === value)),
  })
  return (
    <div className="flex flex-col">
      <div ref={ref} role="menu" aria-label="How should it report?" aria-describedby={noteId} className="flex flex-col"
        onKeyDown={(e) => { menuCursorKeydown(e.nativeEvent, { move, dismiss: close }) }}>
        {REPORT_CHOICES.map((c, i) => (
          <MenuRow key={c.id} role="menuitemradio" tabIndex={tabIndexFor(i)} label={c.label} hint={c.meaning} nameIsLabel
            selected={c.id === value} onClick={() => onPick(c.id)} />
        ))}
      </div>
      <p id={noteId} data-type="caption" className="px-m pt-xs pb-s text-on-surface-low">
        Whatever you choose, a loop waiting on you still says so in your Inbox.
      </p>
    </div>
  )
}
