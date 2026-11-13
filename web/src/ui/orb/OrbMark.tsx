import { useEffect, useRef, useSyncExternalStore, type ReactNode } from 'react'
import type { OrbBadge, OrbStateName } from '../../lib/orbState'
import type { PhaseFamily } from '../../lib/workPhase'
import { BADGE_GLYPHS, badgeBox, inkOf, onOrbMediaChange, orbContrast, useInk } from '../AgentOrb'
import { runDots, tintOf } from '../orbDots'
import { restForm } from './clawRing'
import { paintRing } from './paintRing'

/** The mark's box, in CSS pixels: the orb's smallest size. */
export const MARK_SIZE = 12

/** A list row's state as the claw ring's still mark (`docs/design/agent-orb.md`, "Sizes and
 *  places"): the state's rest form at 12 px, in the list's own colour for it, with the failure's ×
 *  where it has one. Shape and colour only: it never moves, at any setting, so it asks for no frame at all, and
 *  it is decoration, since the row's name and tooltip say the state in words. In forced colours it
 *  takes the system's text colour, and its shape still tells the states apart. */
export function OrbMark({ state, badge, tone, family, children }: {
  state: OrbStateName
  badge?: OrbBadge
  /** The colour token the list draws this state in, as a custom property's name (`--color-ok`). */
  tone: string
  /** The words that name the state, drawn beside the mark: a row that says its state in words. The
   *  mark stays decoration, so the words are what an assistive reading gets. */
  children?: ReactNode
  /** The state's family, stated on the mark and its words (`data-state-family`). */
  family?: PhaseFamily
}) {
  const contrast = useSyncExternalStore(onOrbMediaChange, orbContrast, orbContrast)
  const token = contrast === 'forced' ? 'CanvasText' : tone
  const ink = useInk(token)
  const canvas = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    const dpr = Math.min(2, (typeof devicePixelRatio !== 'undefined' && devicePixelRatio) || 1)
    el.width = Math.round(MARK_SIZE * dpr)
    el.height = Math.round(MARK_SIZE * dpr)
    let tint = tintOf(ink)
    const paint = () => paintRing(ctx, restForm(state), { size: MARK_SIZE, dpr, ink: tint, scale: 1 })
    return runDots(paint, { running: false, speed: 1, retint: () => { tint = tintOf(inkOf(token)) } })
  }, [state, ink, token])
  const colour = `var(${tone})`
  const b = badgeBox(MARK_SIZE)
  const mark = (
    <span data-orb-mark={state} data-orb-ink-token={token} className="relative inline-block shrink-0" style={{ width: MARK_SIZE, height: MARK_SIZE }}>
      <canvas ref={canvas} aria-hidden="true" style={{ width: MARK_SIZE, height: MARK_SIZE, display: 'block' }} />
      {badge && (
        <span
          aria-hidden="true" data-orb-badge={badge}
          className="absolute grid place-items-center rounded-pill border bg-surface font-semibold leading-none"
          style={{ width: b.box, height: b.box, top: -b.edge, right: -b.edge, fontSize: b.glyph, color: colour, borderColor: colour }}
        >
          {BADGE_GLYPHS[badge]}
        </span>
      )}
    </span>
  )
  if (children === undefined) return mark
  return (
    <span data-state-family={family} className="inline-flex items-center gap-xs">
      {mark}
      <span>{children}</span>
    </span>
  )
}
