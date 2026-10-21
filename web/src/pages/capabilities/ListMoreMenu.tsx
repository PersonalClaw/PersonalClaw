import { MoreHorizontal, type LucideIcon } from 'lucide-react'
import { Button } from '../../ui/Button'
import { MenuRow, Popover } from '../../ui/Popover'

/** One action the list's More menu holds. */
export interface ListMoreAction {
  label: string
  icon: LucideIcon
  onSelect: () => void
  /** A row that cannot act right now (a sync already running). */
  disabled?: boolean
}

/** The list's More menu, in its controls bar (#3817). A Capabilities tab's header holds only the
 *  action that adds to it, so the tab's other actions (sync, export, re-probe, the Store's sources)
 *  sit here, one click away, beside the list they act on. */
export function ListMoreMenu({ actions }: { actions: readonly ListMoreAction[] }) {
  if (actions.length === 0) return null
  return (
    <Popover align="right" placement="bottom" width={240} portal
      trigger={(open, toggle) => (
        <Button variant="ghost" size="md" shape="pill" ariaLabel="More actions" title="More actions" ariaExpanded={open} onClick={toggle}>
          <MoreHorizontal size={18} />
        </Button>
      )}>
      {(close) => (
        <div className="flex flex-col gap-0.5">
          {actions.map((a) => (
            <MenuRow key={a.label} icon={<a.icon size={15} />} label={a.label} disabled={a.disabled}
              onClick={() => { if (a.disabled) return; close(); a.onSelect() }} />
          ))}
        </div>
      )}
    </Popover>
  )
}
