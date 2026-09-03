import type { HTMLAttributes } from 'react'
import { cx } from './cx'

/* The one canonical tinted status pill (audit AB-2). Pages hand-rolled this
 * exact pair ~90 times — `background: color-mix(in srgb, <tone> 16%,
 * transparent)` beside `color: <tone>` — and LocalModelManager had even
 * parameterized it as a local helper without vending it. This component IS
 * that helper, promoted: one sanctioned tint strength (16%, inside the 18%
 * ink-contrast budget tokens.css documents and statusChipContrast.test.ts
 * rails), one closed tone vocabulary, the seed metrics in one place.
 *
 *   <StatusPill tone="ok">frontier</StatusPill>
 *   <StatusPill tone="danger" role="img" aria-label={reason} title={reason}>
 *     Won't run
 *   </StatusPill>
 *
 * Emphasis and meaning stay in the TINT + INK pair; the pill never draws a
 * border or stripe (Tone-Not-Line). Layout (gaps for an icon, a denser type
 * size) stays with each consumer. */

export type StatusPillTone = 'ok' | 'warn' | 'danger' | 'info' | 'primary' | 'neutral'

/** The closed tone → ink var map. Everything routes through the semantic
 *  tokens, so scheme retints and the documented per-scheme info-ink
 *  correction apply for free; `neutral` is the no-verdict grey. */
const TONE_VAR: Record<StatusPillTone, string> = {
  ok: 'var(--color-ok)',
  warn: 'var(--color-warn)',
  danger: 'var(--color-danger)',
  info: 'var(--color-info)',
  primary: 'var(--color-primary)',
  neutral: 'var(--color-outline-variant)',
}

export function StatusPill({ tone, sized = true, pad = true, className, style, children, ...rest }: HTMLAttributes<HTMLSpanElement> & {
  /** Semantic tone from the closed set — picks BOTH the 16% tint ground and
   *  the ink, so the pair can never disagree. */
  tone: StatusPillTone
  /** Emit the seed type size (text-[0.75rem]). Default true; set false when
   *  the pill genuinely reads at another size and bring your own text utility
   *  — cx is a plain joiner, so two text-size utilities would race. */
  sized?: boolean
  /** Emit the seed padding (px-1.5). Default true; set false when the pill
   *  genuinely needs other metrics and bring your own — same race rule. */
  pad?: boolean
}) {
  const ink = TONE_VAR[tone]
  return (
    <span
      className={cx('inline-flex shrink-0 items-center rounded-pill', pad && 'px-1.5', sized && 'text-[0.75rem]', className)}
      style={{ background: `color-mix(in srgb, ${ink} 16%, transparent)`, color: ink, ...style }}
      {...rest}>
      {children}
    </span>
  )
}
