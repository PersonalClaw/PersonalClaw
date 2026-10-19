import { useEffect } from 'react'
import { fvs } from '../../../design/fontWeight'
import { ListChecks, PanelLeftClose, PanelRightClose } from 'lucide-react'
import { IconButton } from '../../../ui/IconButton'
import { useResizablePanel } from '../../../ui/useResizablePanel'

// ── The cockpit's side panels ──────────────────────────────────────────────────────────────────
//
// The frame of the cockpit's left and right panels: a title with a collapse button, a resize handle
// on the editor's edge, and the collapsed state broadcast so the editor's tab strip can offer a
// re-open chip.

export function CollapsiblePanel({ side, panelKey, def, min, max, icon: Icon, label, children }: {
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
