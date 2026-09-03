import type { ReactNode } from 'react'
import { cx } from './cx'

// tone → ink token. Exactly ONE color class is ever emitted, so an instance's
// tone can't collide with a stray `text-*` in `className` — `cx` is a plain
// joiner, not a Tailwind-conflict resolver, so two color utilities would race on
// stylesheet order rather than call-site order. A closed tone set is the fix.
const TONE = {
  muted: 'text-on-surface-low',
  info: 'text-info',
  primary: 'text-primary',
} as const

/** Eyebrow — the one canonical caption-tier micro-label.
 *
 *  PersonalClaw's Weight-First rule (`web/DESIGN.md` §3/§6) makes emphasis a
 *  variable-weight STEP, never uppercase-with-tracking. Section eyebrows, chip
 *  labels and meta tags across the app had drifted to
 *  `text-[0.75rem] uppercase tracking-wide` — the exact Don't the rule names.
 *  This renders the intended treatment instead: the `caption` type role
 *  (0.75rem / `wght` 470, `tokens.css`) in sentence case, tinted
 *  `--color-on-surface-low` by default. Migrate
 *  `<div className="… text-[0.75rem] uppercase tracking-wide">Label</div>` to
 *  `<Eyebrow>Label</Eyebrow>` (drop the size — the role sets it — and keep only
 *  layout/margin utilities). The `eyebrowWeightRole` ratchet
 *  (`design/eyebrowWeightRole.test.ts`) holds the remaining uppercase-tracked
 *  count down so it can only shrink. */
export function Eyebrow({
  children,
  as = 'div',
  tone = 'muted',
  id,
  className,
}: {
  children: ReactNode
  /** Element to render: a block `div`/`p` section label, or an inline `span`
   *  (a chip label, or an eyebrow sharing a flex row with a value). */
  as?: 'div' | 'span' | 'p'
  /** Ink tone. `muted` (default) is the section-label grey; `info`/`primary`
   *  are for the semantic eyebrows (a queued nudge, an active marker). */
  tone?: keyof typeof TONE
  /** DOM id for the rendered element, so a caption-tier label can be an
   *  accessible-name target: a labelless control names itself by pointing at
   *  this id with `aria-labelledby` (the canonical `Field` label does this). */
  id?: string
  /** Layout/spacing utilities for this instance (margins, a flex row, a chip
   *  fill). Never `uppercase`/`tracking-*` — that is the drift this replaces. */
  className?: string
}) {
  const Tag = as
  return (
    <Tag id={id} data-type="caption" className={cx(TONE[tone], className)}>
      {children}
    </Tag>
  )
}
