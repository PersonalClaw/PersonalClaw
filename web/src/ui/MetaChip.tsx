import type { LucideIcon } from 'lucide-react'
import type { MouseEvent } from 'react'
import { accentChip } from '../design/accent'

/** A metadata pill that narrows the list it sits in to itself — a task's project or one of its
 *  tags, on a row or a card.
 *
 *  ONE component because these were two implementations of one control, and the difference was
 *  invisible on purpose: the project pill was a real `<button>` while the tag chips beside it were
 *  inert `<span>`s with the same pill shape, size and hover-adjacent styling. They read as the same
 *  affordance and only one of them was (#477). Keeping them as one component is what stops that from
 *  drifting apart again.
 *
 *  Degrades to a `<span>` when no handler is given, so a surface that cannot offer the filter never
 *  renders a control that does nothing. The kanban card uses that form deliberately: its wrapper is
 *  the ONLY drag source, and `TaskBoard` already records that putting a control across a `draggable`
 *  element inserts something between the pointer and the drag.
 *
 *  `stopPropagation` because every one of these sits inside a row/card whose own click opens the
 *  record — filtering by a tag must not also open the task the tag happened to be attached to.
 *
 *  NOT called `FilterChip`, which is the obvious name and is already taken by a DIFFERENT control:
 *  `pages/knowledge`'s local `FilterChip` is an h-8 toggle carrying `aria-pressed`, one of a rail of
 *  filter states you switch between. This is an h-6 metadata pill that reports a value and narrows
 *  to it. Same family, different job — one name for both would force one height and one semantic
 *  onto each, which is why `design/primitiveShadowing` asks for this call to be recorded. */
export function MetaChip({ label, title, ariaLabel, icon: Icon, tone = 'neutral', onClick }: {
  label: string
  /** Native tooltip — the pointer affordance's own words ("Filter by tag “wedding”"). */
  title?: string
  /** Accessible name. Defaults to `title` stripped of its curly quotes, else the bare label. */
  ariaLabel?: string
  icon?: LucideIcon
  /** `accent` is the project pill's accent skin; `neutral` the tag chip's surface tone. */
  tone?: 'neutral' | 'accent'
  onClick?: (value: string) => void
}) {
  const cls = 'inline-flex shrink-0 items-center gap-1 rounded-pill px-2 h-6'
  const skin = tone === 'accent' ? accentChip : undefined
  const neutral = tone === 'neutral' ? ' bg-surface-high text-on-surface-var' : ''
  const body = (
    <>
      {Icon && <Icon size={10} className="shrink-0" />}
      {label}
    </>
  )

  if (!onClick) {
    return <span data-type="caption" className={cls + neutral} style={skin} title={title}>{body}</span>
  }
  return (
    <button type="button" data-type="caption" className={`${cls}${neutral} hover:brightness-125`}
      style={skin} title={title} aria-label={ariaLabel ?? title?.replace(/[“”]/g, '') ?? label}
      onClick={(e: MouseEvent) => { e.stopPropagation(); onClick(label) }}>{body}</button>
  )
}
