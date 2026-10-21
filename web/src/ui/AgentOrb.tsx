import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { MODE_FRAMES, paintFrame, resolvePreset, type OrbState } from 'thinking-orbs/engine'
import { useMode } from '../app/theme'
import { computedToken, onEditorTokensChange } from '../design/editorTheme'
import { prefersReducedMotion } from '../design/motion'
import { WORK_PHASE_LOOKS, phaseLabel, workPhaseOf, type WorkPhase } from '../lib/workPhase'
import { cx } from './cx'
import { STILL_AT, runDots, tintOf, useOnScreen } from './orbDots'

export { STILL_AT }

/** AgentOrb — what an agent's turn is doing, as one small orb of dots (`docs/design/agent-orb.md`).
 *
 *  It is handed a phase of the contract (`lib/workPhase.ts`) and nothing else, so no page can show a
 *  state the gateway did not measure. Each phase is one drawing in one of the four families: the
 *  active family moves (its drawings are variants of one family, and the words beside the orb say
 *  which phase it is), and waiting, blocked and quiet are still frames, a waiting or failed turn with
 *  a badge whose glyph, not its colour, tells which. The drawing is `thinking-orbs`' (MIT), tinted
 *  with the orb tokens: `--color-orb-alive` while an agent works or waits on the owner, and
 *  `--color-orb-rest` where nothing is alive.
 *
 *  It asks for animation frames only while it shows live work, on screen, in a visible tab, with
 *  motion allowed, and then through the buddy's capped loop (`orbDots.ts`): at most `ORB_FPS` a
 *  second, and none between two paints. Anything else is one painted still. */

export type AgentOrbSize = 20 | 64

/** Where the owner is needed, or the turn failed: the badge on the orb's upper edge. */
export type OrbBadge = 'approval' | 'input' | 'error'

export interface OrbLook {
  /** The `thinking-orbs` drawing. */
  drawing: OrbState
  /** Whether it animates: only in the active family. */
  moves: boolean
  /** Alive while an agent works or waits on the owner; at rest where nothing is alive. */
  ink: 'alive' | 'rest'
  badge?: OrbBadge
}

const active = (drawing: OrbState): OrbLook => ({ drawing, moves: true, ink: 'alive' })
const still = (ink: OrbLook['ink'], badge?: OrbBadge): OrbLook => ({ drawing: 'breathing', moves: false, ink, ...(badge ? { badge } : {}) })

/** Every phase of the contract, as the orb shows it (`docs/design/agent-orb.md`, "Phase to state"). */
export const ORB_LOOKS: Record<WorkPhase, OrbLook> = {
  queued: active('breathing'),
  thinking: active('breathing'),
  searching: active('searching'),
  tool: active('working'),
  planning: active('solving'),
  delegating: active('connecting'),
  streaming: active('composing'),
  listening: active('listening'),
  retrying: active('working'),
  waiting_model: active('breathing'),
  approval: still('alive', 'approval'),
  input: still('alive', 'input'),
  done: still('rest'),
  error: still('rest', 'error'),
  idle: still('rest'),
}

/** The orb's look for a wire value, or `null` for anything that is not a phase: a status line's
 *  words or an orb state never reach one. */
export function orbLookOf(value: unknown): OrbLook | null {
  const phase = workPhaseOf(value)
  return phase ? ORB_LOOKS[phase] : null
}

const BADGE_GLYPHS: Record<OrbBadge, string> = { approval: '!', input: '?', error: '×' }

type Contrast = 'normal' | 'more' | 'forced'
const media = (query: string) => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches
const MEDIA_QUERIES = ['(prefers-reduced-motion: reduce)', '(forced-colors: active)', '(prefers-contrast: more)']
/** What the platform asks of the orb, read when it renders: `reduced|contrast`. */
function mediaSnapshot(): string {
  const contrast: Contrast = media('(forced-colors: active)') ? 'forced' : media('(prefers-contrast: more)') ? 'more' : 'normal'
  return `${prefersReducedMotion() ? 'reduced' : 'motion'}|${contrast}`
}
function onMediaChange(cb: () => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {}
  const lists = MEDIA_QUERIES.map((q) => window.matchMedia(q))
  for (const l of lists) l.addEventListener?.('change', cb)
  return () => { for (const l of lists) l.removeEventListener?.('change', cb) }
}

const visibility = () => (typeof document === 'undefined' ? 'visible' : document.visibilityState)
function onVisibilityChange(cb: () => void): () => void {
  document.addEventListener('visibilitychange', cb)
  return () => document.removeEventListener('visibilitychange', cb)
}

/** The ink a token resolves to, in a form the painter takes (`#rgb`, `#rrggbb`, `rgb()`). A system
 *  colour or a colour function is resolved by painting it once; `undefined` leaves the painter's
 *  own grey. */
export function inkOf(name: string): string | undefined {
  const value = name.startsWith('--') ? computedToken(name).trim() : name
  if (!value) return undefined
  if (/^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(value) || /^rgba?\(/i.test(value)) return value
  try {
    const ctx = document.createElement('canvas').getContext('2d')
    if (!ctx) return undefined
    ctx.fillStyle = value
    ctx.fillRect(0, 0, 1, 1)
    const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
    return `rgb(${r}, ${g}, ${b})`
  } catch {
    return undefined
  }
}

/** The ink for `token`, read again whenever the scheme, the mode or an owner's colour moves it. */
export function useInk(token: string): string | undefined {
  const [ink, setInk] = useState(() => inkOf(token))
  useEffect(() => {
    setInk(inkOf(token))
    return onEditorTokensChange(() => setInk(inkOf(token)))
  }, [token])
  return ink
}

export function AgentOrb({ phase, size = 20, className }: {
  /** The turn's phase, from the phase contract. Never an orb state or a status line's words. */
  phase: WorkPhase
  /** 20 at the head of a reply; 64 only for empty states, voice mode and the buddy's expanded view. */
  size?: AgentOrbSize
  className?: string
}) {
  const look = ORB_LOOKS[phase]
  const { mode } = useMode()
  const [motion, contrast] = useSyncExternalStore(onMediaChange, mediaSnapshot, mediaSnapshot).split('|') as ['reduced' | 'motion', Contrast]
  const hidden = useSyncExternalStore(onVisibilityChange, visibility, visibility) === 'hidden'
  const token = contrast === 'forced' ? 'CanvasText' : contrast === 'more' ? '--color-on-surface' : look.ink === 'alive' ? '--color-orb-alive' : '--color-orb-rest'
  const ink = useInk(token)
  const canvas = useRef<HTMLCanvasElement>(null)
  const onScreen = useOnScreen(canvas)
  const animate = look.moves && motion === 'motion' && !hidden
  const running = animate && onScreen

  // The drawing `thinking-orbs`' own component paints, at the same pixel density, from the size's
  // own tuned preset; held at its still instant whenever it does not run.
  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    const dpr = Math.min(2, (typeof devicePixelRatio !== 'undefined' && devicePixelRatio) || 1)
    el.width = Math.round(size * dpr)
    el.height = Math.round(size * dpr)
    const preset = resolvePreset(look.drawing, size)
    const draw = MODE_FRAMES[preset.mode]
    const dark = mode === 'dark'
    let tint = tintOf(ink)
    const paint = (t: number) => {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, size, size)
      paintFrame(ctx, draw(size, t, preset.opts), dark, tint)
    }
    return runDots(paint, { running, speed: preset.speed, retint: () => { tint = tintOf(inkOf(token)) } })
  }, [look.drawing, size, mode, ink, token, running])

  const tone = `var(--color-${WORK_PHASE_LOOKS[phase].tone})`
  const badge = size === 64 ? { box: 22, glyph: 14, edge: 2 } : { box: 10, glyph: 8, edge: 2 }
  return (
    <span
      data-agent-orb={!look.moves ? 'still' : motion === 'reduced' ? 'instant' : 'animated'}
      data-orb-phase={phase} data-orb-family={WORK_PHASE_LOOKS[phase].family} data-orb-drawing={look.drawing}
      data-orb-size={size} data-orb-ink={ink ?? ''} data-orb-ink-token={token} data-orb-contrast={contrast}
      className={cx('relative inline-block shrink-0', className)}
      style={{ width: size, height: size }}
    >
      <canvas ref={canvas} role="img" aria-label={phaseLabel(phase)} style={{ width: size, height: size, display: 'block' }} />
      {look.badge && (
        <span
          aria-hidden="true" data-orb-badge={look.badge}
          className="absolute grid place-items-center rounded-pill border bg-surface font-semibold leading-none"
          style={{ width: badge.box, height: badge.box, top: -badge.edge, right: -badge.edge, fontSize: badge.glyph, color: tone, borderColor: tone }}
        >
          {BADGE_GLYPHS[look.badge]}
        </span>
      )}
    </span>
  )
}
