import { useRef, useState } from 'react'
import { withWeight } from '../../../design/fontWeight'
import { Tag as TagIcon } from 'lucide-react'
import { CollapseColumnButton, CollapsedBoardColumn, boardGridTemplate, useBoardCollapse } from '../../../ui/BoardCollapse'
import { type ChatSessionSummary, type ChatTag } from '../../../lib/api'

/** Kanban board: one column per tag (+ untagged), each holding the matching
 *  sessions. Designed as a fixed SHELL (mirrors the Tasks board): the board fills
 *  the available height; columns flow in a responsive grid (auto-fit, min 260px)
 *  and share the height equally when they wrap, so no column hides off-screen.
 *  Only each column's session list scrolls independently — the page never
 *  overflows horizontally or vertically. Read+navigate (reuses the list card). */
export function TagBoard({ sessions, tags, card, onMove }: {
  sessions: ChatSessionSummary[]; tags: ChatTag[]; card: (s: ChatSessionSummary) => React.ReactNode
  /** Drop a chat onto a column — MOVE semantics: toTagId is the target column's
   *  tag (null for Untagged), fromTagId the column it was dragged out of. */
  onMove?: (key: string, toTagId: string | null, fromTagId: string | null) => void
}) {
  const [dragKey, setDragKey] = useState<string | null>(null)
  // The source COLUMN of the in-flight drag — the drop handler only knows the
  // target, but move semantics need to remove the source column's tag too.
  // A ref (not state): it must be readable at drop time without re-rendering.
  const dragFromCol = useRef<string | null>(null)
  const [overCol, setOverCol] = useState<string | null>(null)
  const collapse = useBoardCollapse('board-collapsed:chat')
  const columns: { id: string; tagId: string | null; label: string; color?: string; items: ChatSessionSummary[] }[] = [
    ...tags.map((t) => ({ id: t.id, tagId: t.id, label: t.name, color: t.color, items: sessions.filter((s) => (s.tags ?? []).includes(t.id)) })),
    { id: '_untagged', tagId: null, label: 'Untagged', items: sessions.filter((s) => !(s.tags ?? []).length) },
  ]
  // Empty columns AUTO-collapse to a slim rail so the populated column(s) get
  // real width; any column can be manually collapsed/expanded (header chevron
  // / click the rail; localStorage-persisted). Rails stay collapsed during a
  // drag and are themselves drop targets. Shared mechanism with the Tasks
  // board (ui/BoardCollapse) — the template is a pure function of data +
  // stored preference, never of drag state (a mid-drag DOM restructure makes
  // Chrome cancel the native drag).
  const template = boardGridTemplate(columns.map((c) => collapse.isCollapsed(c.id, c.items.length)))
  return (
    <div
      className="grid h-full gap-m overflow-x-auto"
      style={{ gridTemplateColumns: template, gridAutoRows: 'minmax(180px, 1fr)' }}
    >
      {columns.map((c) => {
        const collapsed = collapse.isCollapsed(c.id, c.items.length)
        const dropStyle = {
          background: overCol === c.id
            ? 'color-mix(in srgb, var(--color-primary) 12%, transparent)'
            : 'color-mix(in srgb, var(--color-surface-container) 40%, transparent)',
          outline: overCol === c.id ? '1.5px dashed color-mix(in srgb, var(--color-primary) 60%, transparent)' : 'none',
        }
        const dropHandlers = onMove ? {
          onDragOver: (e: React.DragEvent) => { e.preventDefault(); if (overCol !== c.id) setOverCol(c.id) },
          onDragLeave: (e: React.DragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOverCol((p) => p === c.id ? null : p) },
          onDrop: (e: React.DragEvent) => { e.preventDefault(); const k = e.dataTransfer.getData('text/plain') || dragKey; if (k) onMove(k, c.tagId, dragFromCol.current); setOverCol(null); setDragKey(null); dragFromCol.current = null },
        } : {}
        if (collapsed) {
          // Shared slim rail (ui/BoardCollapse): tag icon, count, rotated label.
          // The rail itself is the drop target — it highlights on dragover
          // (dropStyle); an auto-collapsed (empty) rail expands naturally once
          // a drop moves a chat in, a user-collapsed one stays (count ticks
          // up). Clicking the rail re-expands.
          return (
            <CollapsedBoardColumn key={c.id} icon={TagIcon} label={c.label} count={c.items.length}
              tone={c.color} style={dropStyle} {...dropHandlers}
              onExpand={() => collapse.toggle(c.id, c.items.length)} />
          )
        }
        return (
          <div key={c.id} className="flex min-h-0 flex-col rounded-xl p-2 transition-colors" style={dropStyle} {...dropHandlers}>
            <div className="mb-2 flex items-center gap-1.5 px-1 pt-1 shrink-0 text-[0.8125rem]" style={withWeight({ color: c.color || 'var(--color-on-surface)' }, 550)}>
              <TagIcon size={13} /> <span className="truncate flex-1">{c.label}</span> <span className="text-on-surface-low tabular-nums">{c.items.length}</span>
              <CollapseColumnButton onCollapse={() => collapse.toggle(c.id, c.items.length)} />
            </div>
            <div className="flex flex-1 min-h-0 flex-col gap-s overflow-y-auto pr-0.5">
              {c.items.length === 0
                ? <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-outline-variant/30 py-6 text-on-surface-low text-[0.75rem]">{onMove ? 'Drop a chat here' : 'No chats'}</div>
                : c.items.map((s) => onMove
                  // setData stays synchronous (required inside dragstart); the
                  // STATE set defers a frame — flushing a re-render while Chrome
                  // is still committing the native drag cancels it (instant
                  // dragend). select-none: selectable text is its own native
                  // drag source and steals the gesture from the wrapper, so the
                  // whole card (title, badges, anywhere) must be unselectable
                  // for the whole card to initiate the drag.
                  ? <div key={s.key} draggable
                      onDragStart={(e) => { e.dataTransfer.setData('text/plain', s.key); e.dataTransfer.effectAllowed = 'move'; dragFromCol.current = c.tagId; requestAnimationFrame(() => setDragKey(s.key)) }}
                      onDragEnd={() => { setDragKey(null); setOverCol(null); dragFromCol.current = null }}
                      className={`select-none cursor-grab active:cursor-grabbing ${dragKey === s.key ? 'opacity-40' : ''}`}>{card(s)}</div>
                  : card(s))}
            </div>
          </div>
        )
      })}
    </div>
  )
}
