import { useState, type HTMLAttributes } from 'react'
import { ChevronsRightLeft } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

/** Shared column-collapse mechanism for kanban boards (chat-history tag board
 *  + Tasks status board). Empty columns AUTO-collapse to a slim vertical rail
 *  so the populated column(s) get real width — otherwise N empty columns each
 *  steal an equal 1fr share and squash the one column that actually holds
 *  items — and the user can MANUALLY collapse a populated column (or re-expand
 *  an empty one) via the header chevron / clicking the rail. Rails stay
 *  collapsed during a drag: the rail ITSELF is the drop target (it highlights
 *  on dragover), and an auto-collapsed column naturally expands on the render
 *  after a drop lands because its count went ≥1 (a user-collapsed one stays
 *  collapsed, its rail count ticking up). Collapse state = explicit user
 *  override ?? derived default (empty). Overrides persist in localStorage
 *  (view preference, not data — the derived part still reproduces from data
 *  alone). Crucially the grid template never changes mid-drag: restructuring
 *  the DOM during dragstart makes Chrome cancel the native HTML5 drag (instant
 *  dragend — the "drag does nothing" bug), so the template is a pure function
 *  of data + stored preference, never of drag state. */

/** Width of a collapsed rail in the grid template. */
export const COLLAPSED_COL_WIDTH = '44px'

/** The DERIVED default: an empty column collapses. Deliberately independent
 *  of drag state — see module doc. */
export const isBoardColumnCollapsed = (itemCount: number): boolean => itemCount === 0

/** Explicit per-column grid template (NOT auto-fit — explicit widths are what
 *  let a collapsed rail take a slim fixed slot while populated columns share
 *  the rest). Pass each column's resolved collapsed flag in board order. */
export const boardGridTemplate = (collapsed: boolean[], minCol = '240px'): string =>
  collapsed.map((c) => (c ? COLLAPSED_COL_WIDTH : `minmax(${minCol}, 1fr)`)).join(' ')

/** Per-board collapse state: user overrides layered over the derived default.
 *  Overrides store BOTH directions (collapse a populated column / keep an
 *  empty one expanded) and are pruned whenever they merely restate the
 *  derived default, so storage stays minimal. Persisted under `storageKey`
 *  ('board-collapsed:tasks' / 'board-collapsed:chat') — survives reload. */
export function useBoardCollapse(storageKey: string) {
  const [overrides, setOverrides] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || '{}') as Record<string, boolean> } catch { return {} }
  })
  const isCollapsed = (colId: string, itemCount: number): boolean =>
    overrides[colId] ?? isBoardColumnCollapsed(itemCount)
  const toggle = (colId: string, itemCount: number) => {
    setOverrides((prev) => {
      const target = !(prev[colId] ?? isBoardColumnCollapsed(itemCount))
      const next = { ...prev }
      if (target === isBoardColumnCollapsed(itemCount)) delete next[colId]
      else next[colId] = target
      try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* private mode */ }
      return next
    })
  }
  return { isCollapsed, toggle }
}

/** Header affordance to manually collapse an expanded column. Always visible
 *  (a brand-new affordance hidden behind hover is undiscoverable) but quiet. */
export function CollapseColumnButton({ onCollapse }: { onCollapse: () => void }) {
  return (
    <button type="button" aria-label="Collapse column" title="Collapse column"
      onClick={(e) => { e.stopPropagation(); onCollapse() }}
      className="shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-highest hover:text-on-surface transition-colors">
      <ChevronsRightLeft size={13} />
    </button>
  )
}

/** The slim vertical rail a collapsed column renders as: column icon, count,
 *  rotated label. The WHOLE rail is a live drop target — spread the same
 *  drag/drop handlers (and dragover highlight style) the expanded column uses
 *  onto it; it highlights (never expands) while a card hovers over it.
 *  Clicking the rail re-expands the column (`onExpand`). */
export function CollapsedBoardColumn({ icon: Icon, label, count, tone, onExpand, ...rest }: {
  icon: LucideIcon; label: string; count: number
  /** Accent for the icon + label (tag color / status tone). */
  tone?: string
  /** Click-to-expand (manual collapse toggle). */
  onExpand?: () => void
} & HTMLAttributes<HTMLDivElement>) {
  return (
    <div title={`${label} · ${count}${onExpand ? ' — click to expand' : ''}`} {...rest}
      onClick={onExpand}
      role={onExpand ? 'button' : undefined}
      className={`flex min-h-0 flex-col items-center gap-1.5 rounded-xl px-1 py-2 transition-colors ${onExpand ? 'cursor-pointer hover:bg-surface-high' : ''}`}>
      <Icon size={13} style={{ color: tone || 'var(--color-on-surface-low)' }} />
      <span className="text-on-surface-low tabular-nums text-[0.6875rem]">{count}</span>
      <span className="min-h-0 flex-1 truncate text-on-surface-low text-[0.75rem]"
        style={{ writingMode: 'vertical-rl', fontVariationSettings: '"wght" 550', color: tone || undefined }}>{label}</span>
    </div>
  )
}
