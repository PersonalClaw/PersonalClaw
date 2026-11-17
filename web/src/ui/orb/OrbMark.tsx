import { useEffect, useRef, useSyncExternalStore, type ReactNode } from 'react'
import { markSceneOf, type OrbBadge, type OrbStateName } from '../../lib/orbState'
import type { PhaseFamily } from '../../lib/workPhase'
import { ORB_PAD, SCENE_GLYPHS, onOrbMediaChange, orbContrast, orbInkTokens, orbInksOf, useInk } from '../AgentOrb'
import { runDots } from '../orbDots'
import { drawFrame } from './halftone'
import { SCENES } from './scenes'

/** The mark's box, in CSS pixels: the orb's smallest size. */
export const MARK_SIZE = 12

/** A list row's state as the halftone orb's still mark (`docs/design/agent-orb.md`, "Rows and the
 *  tray"): the sphere's own form for the state at 12 px, in the list's own colour for it, with the
 *  failure's × or the wait's ! on its plate where it has one. A row knows its pip, not its phase, so
 *  it shows the form that says the pip (`lib/orbState.ts`, `markSceneOf`). Shape and
 *  colour only: it never moves, at any setting, so it asks for no frame at all, and it is
 *  decoration, since the row's name and tooltip say the state in words. In forced colours it takes
 *  the system's text colour, and its shape still tells the states apart. */
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
  const scene = markSceneOf({ state, badge })
  const pad = Math.ceil(MARK_SIZE * ORB_PAD)
  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    const dpr = Math.min(2, (typeof devicePixelRatio !== 'undefined' && devicePixelRatio) || 1)
    const box = MARK_SIZE + pad * 2
    el.width = Math.round(box * dpr)
    el.height = Math.round(box * dpr)
    // The list's colour for the state is every ink the mark is drawn in: its dots and its glyph.
    const tokensOf = () => {
      const base = orbInkTokens(contrast)
      return { ...base, alive: token, rest: token, ok: token, warn: token, info: token, danger: token }
    }
    let inks = orbInksOf(tokensOf())
    const paint = () => {
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, el.width, el.height)
      if (!inks) return
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      drawFrame(ctx, { [scene]: 1 }, { t: 0.9, since: 1e6, still: true }, inks, SCENES, { size: MARK_SIZE, x: pad, y: pad })
    }
    return runDots(paint, { running: false, speed: 1, retint: () => { inks = orbInksOf(tokensOf()) } })
  }, [scene, ink, token, contrast, pad])
  const mark = (
    <span
      data-orb-mark={state} data-orb-scene={scene} data-orb-ink-token={token}
      {...(badge ? { 'data-orb-badge': badge } : {})}
      {...(SCENE_GLYPHS[scene] ? { 'data-orb-glyph': SCENE_GLYPHS[scene] } : {})}
      className="relative inline-block shrink-0" style={{ width: MARK_SIZE, height: MARK_SIZE }}
    >
      <canvas
        ref={canvas} aria-hidden="true" className="pointer-events-none absolute block"
        style={{ width: MARK_SIZE + pad * 2, height: MARK_SIZE + pad * 2, left: -pad, top: -pad }}
      />
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
