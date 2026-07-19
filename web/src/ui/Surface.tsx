import type { ReactNode } from 'react'
import { cx } from './cx'

type Tone = 'surface' | 'low' | 'container' | 'high'
const tones: Record<Tone, string> = {
  surface: 'bg-surface',
  low: 'bg-surface-low',
  container: 'bg-surface-container',
  high: 'bg-surface-high',
}

/** Tonal surface — PersonalClaw elevation model (brand rebrand §3.1).
 *
 *  Two modes, one prop:
 *  - default (`glass=false`) = **neumorphic GROUND** — tone step + soft shadow, no
 *    hard border; for content-bearing/permanent surfaces.
 *  - `glass` = **glass SKY** — frosted translucent overlay for transient/floating
 *    UI (menus, popovers, palettes). The `backdrop-filter` lives ONLY here, on the
 *    OUTERMOST overlay, so the nested-blur bug can't recur (inner emphasis uses
 *    opacity/border, never a second blur). Falls back to a solid surface where
 *    backdrop-filter is unsupported or reduced-motion is on.
 *
 *  Default radius is `lg` (16px); cards use `xl`, large sheets use `squircle`.
 *  (A v2 `interactive` hover-lift variant was tried but had no consumers — the
 *  liftable-card treatment lives on the components that need it: ListRow, TaskCard,
 *  AppCard, BentoCard. Surface stays a pure static tonal container.) */
export function Surface({
  children, tone = 'container', radius = 'lg', className, glass, onClick,
}: {
  children: ReactNode
  tone?: Tone
  radius?: 'md' | 'lg' | 'xl' | 'squircle'
  className?: string
  glass?: boolean
  onClick?: () => void
}) {
  const r = radius === 'squircle' ? 'squircle'
    : radius === 'xl' ? 'rounded-xl' : radius === 'md' ? 'rounded-md' : 'rounded-lg'
  return (
    <div
      onClick={onClick}
      className={cx(glass ? 'glass' : tones[tone], r, className)}
    >
      {children}
    </div>
  )
}
