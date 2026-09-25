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
 *  correction apply for free; `neutral` is the no-verdict grey.
 *
 *  🔴 `neutral` IS THE INK RAMP'S MUTED TIER, NOT A HAIRLINE TOKEN (#3493). It
 *  drew in `--color-outline-variant` — a border/divider value — and a hairline
 *  is designed to be *barely* separable from its surface, which is the opposite
 *  of what text needs. Measured over its own 16% tint on the four resting tiers,
 *  in both modes: **1.6346 dark** on `surface-container` and **1.1381 light** on
 *  the canvas, against AA's 4.5 for 12px text. All 8 cells failed, and no ground
 *  rescues it — the token is 1.07–2.04 against every tier even BEFORE the tint,
 *  which `knowledge/graphMarkContrast.test.ts` independently records (2.04 dark /
 *  1.17 light on the canvas) as its reason for deleting the same value from two
 *  graph marks. So this is an INK fix and NOT a compositing one: pinning which
 *  opaque tier the 16% tint resolves against (the remedy the model-fit chip took
 *  in #3441 for a 4.4543 reading) cannot move a ratio that is already under 2:1
 *  on every tier, with or without a tint.
 *
 *  `--color-on-surface-low` is the muted TEXT tier of the same ramp (0 of 8
 *  cells under AA, worst 4.5472 dark on `surface-container`, best 7.2320 light),
 *  and it is the value the tree's OTHER neutral pill already ships:
 *  `settings/bento.tsx`'s `muted` variant paints exactly this ink. Picking it
 *  makes the two agree rather than mint a third grey. `--color-on-surface-var`
 *  was measured and rejected — 4.4308 on the light canvas, still under. */
const TONE_VAR: Record<StatusPillTone, string> = {
  ok: 'var(--color-ok)',
  warn: 'var(--color-warn)',
  danger: 'var(--color-danger)',
  info: 'var(--color-info)',
  primary: 'var(--color-primary)',
  neutral: 'var(--color-on-surface-low)',
}

export function StatusPill({ tone, sized = true, pad = true, groundedOn, className, style, children, ...rest }: HTMLAttributes<HTMLSpanElement> & {
  /** Semantic tone from the closed set — picks BOTH the 16% tint ground and
   *  the ink, so the pair can never disagree. */
  tone: StatusPillTone
  /** Ride the caption type role (the seed 0.75rem tier). Default true; set
   *  false when the pill genuinely reads at another size and bring your own
   *  type role or text utility. */
  sized?: boolean
  /** Emit the seed padding (px-1.5). Default true; set false when the pill
   *  genuinely needs other metrics and bring your own — same race rule. */
  pad?: boolean
  /** Composite the 16% tint against THIS opaque tier instead of `transparent`,
   *  pinning the pill's ground so it no longer depends on what is painted
   *  beneath it.
   *
   *  🔴 For a pill on a tier that is not a reference ground, or on one that
   *  MOVES. `statusChipContrast.test.ts` measures the tint over the resting
   *  tiers and records its own exclusion of `--color-surface-high`, concluding
   *  that a ground which lifts toward the ink "is a GROUND problem, and the fix
   *  is for the chip's ground to stop moving under it" — this prop is that fix.
   *  Measured (OU-14, both themes, 16%): the `ok` tint reads **4.4543** dark /
   *  **4.4625** light over `surface-high` (axe `color-contrast`, serious, and it
   *  fired on Settings → Providers once a downloadable chat model existed), and
   *  **5.0903** / **5.0075** over `surface-container`. Same ink, same strength,
   *  same appearance — only the compositing base is pinned.
   *
   *  Default `undefined` keeps the translucent wash, so every existing pill is
   *  byte-identical and no scheme or contrast rail moves. Opt in only where the
   *  ground is measured to break AA. */
  groundedOn?: string
}) {
  const ink = TONE_VAR[tone]
  return (
    <span
      data-type={sized ? 'caption' : undefined}
      className={cx('inline-flex shrink-0 items-center rounded-pill', pad && 'px-1.5', className)}
      style={{
        background: `color-mix(in srgb, ${ink} 16%, ${groundedOn ?? 'transparent'})`,
        color: ink,
        ...style,
      }}
      {...rest}>
      {children}
    </span>
  )
}
