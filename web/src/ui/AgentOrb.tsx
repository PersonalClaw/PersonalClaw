import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { animate, motionValue } from 'framer-motion'
import { computedToken, onEditorTokensChange } from '../design/editorTheme'
import { physics, prefersReducedMotion } from '../design/motion'
import { ORB_STATE_OF, type OrbBadge, type OrbState, type OrbStateName } from '../lib/orbState'
import { WORK_PHASE_LOOKS, phaseLabel, type WorkPhase } from '../lib/workPhase'
import { cx } from './cx'
import { MOVES, formAt, mixToward, restForm, transitionOf, type Form } from './orb/clawRing'
import { paintRing } from './orb/paintRing'
import { cappedDriver, runDots, tintOf, useOnScreen } from './orbDots'

/** AgentOrb — what an agent's turn is doing, as the claw ring (`docs/design/agent-orb.md`).
 *
 *  It is handed a phase of the contract (`lib/workPhase.ts`) and nothing else, and reads its state
 *  from the one mapping (`lib/orbState.ts`), so no page can show a state the gateway did not
 *  measure. The ring is six curved strokes drawn like the brand mark's two (`ui/orb/clawRing.ts`),
 *  and each of the five states has its own form and motion: thinking swells and working turns two
 *  rings; ready, waiting on you and done hold still. A change of state morphs the form on the
 *  `smooth` spring, and arriving at done from a completed turn plays one success bloom on the
 *  `playful` one, which a failure, a stop or a decline never does.
 *  The words beside the orb say which phase it is, and a wait or a failure carries the badge whose
 *  glyph, not its colour, tells which. Its ink is the orb tokens: `--color-orb-alive` while an
 *  agent works or waits on the owner, `--color-orb-rest` where nothing is alive.
 *
 *  It asks for frames only while it moves or morphs, on screen, in a visible tab, with motion
 *  allowed, and then through the buddy's capped loop (`orbDots.ts`): at most `ORB_FPS` a second,
 *  and none between two paints. Under reduced motion a change is an instant swap to the new
 *  state's still form. The 12 px mark never moves: shape and ink only. */

export type AgentOrbSize = 12 | 20 | 64

/** A badge's glyph: its shape, not its colour, says which wait or failure it is. */
export const BADGE_GLYPHS: Readonly<Record<OrbBadge, string>> = { approval: '!', input: '?', error: '×' }

/** The badge's box at each size, in CSS pixels: its side, its glyph's size, and how far it sits
 *  past the orb's upper corner. The 12 px mark's is a list's (`orb/OrbMark.tsx`). */
export function badgeBox(size: AgentOrbSize): { box: number; glyph: number; edge: number } {
  return size === 64 ? { box: 22, glyph: 14, edge: 2 } : size === 20 ? { box: 10, glyph: 8, edge: 2 } : { box: 7, glyph: 6, edge: 1 }
}

type Contrast = 'normal' | 'more' | 'forced'
const media = (query: string) => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches
const MEDIA_QUERIES = ['(prefers-reduced-motion: reduce)', '(forced-colors: active)', '(prefers-contrast: more)']
/** The contrast the platform asks of an orb's strokes: forced colours, more contrast, or neither. */
export function orbContrast(): Contrast {
  return media('(forced-colors: active)') ? 'forced' : media('(prefers-contrast: more)') ? 'more' : 'normal'
}
/** What the platform asks of the orb, read when it renders: `reduced|contrast`. */
function mediaSnapshot(): string {
  return `${prefersReducedMotion() ? 'reduced' : 'motion'}|${orbContrast()}`
}
/** Calls `cb` when reduced motion or the contrast asked for changes. */
export function onOrbMediaChange(cb: () => void): () => void {
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
 *  colour or a colour function is resolved by painting it once. `undefined` when the token resolves
 *  to nothing; the claw ring (`orb/paintRing.ts`) then draws nothing until it does. */
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

export function AgentOrb({ phase, size = 20, className, onSettled, bloom = true }: {
  /** The turn's phase, from the phase contract. Never an orb state or a status line's words. */
  phase: WorkPhase
  /** 20 in the turn's live line and the history peek; 64 for the empty chat and voice mode; 12 as
   *  a list row's still mark, as in the buddy card's rows. */
  size?: AgentOrbSize
  className?: string
  /** Called with the state the orb has settled on: when a change's morph or bloom ends, at once for
   *  a change that swaps, and on the first paint. A line that holds a turn's ending waits on it. */
  onSettled?: (state: OrbStateName) => void
  /** Whether arriving at done may play its bloom (default true). Read when the change starts: a turn
   *  that was stopped or declined closes to done's form without it. */
  bloom?: boolean
}) {
  const look = ORB_STATE_OF[phase]
  const [motion, contrast] = useSyncExternalStore(onOrbMediaChange, mediaSnapshot, mediaSnapshot).split('|') as ['reduced' | 'motion', Contrast]
  const hidden = useSyncExternalStore(onVisibilityChange, visibility, visibility) === 'hidden'
  const token = contrast === 'forced' ? 'CanvasText' : contrast === 'more' ? '--color-on-surface' : look.ink === 'alive' ? '--color-orb-alive' : '--color-orb-rest'
  const ink = useInk(token)
  const canvas = useRef<HTMLCanvasElement>(null)
  const onScreen = useOnScreen(canvas)
  const mark = size === 12
  const reduced = motion === 'reduced'
  const moves = MOVES[look.state] && !mark && !reduced && !hidden

  // The morph: from the form last painted to this state's, on the smooth spring; and done's bloom.
  // Both run on the capped clock, so they step in the frame the ring is painted in. It is keyed on
  // the state, so a phase that changes inside one state is no change; and a change cut short, by a
  // hidden tab, by reduced motion or by the orb leaving the screen, settles at its target.
  const lastForm = useRef<Form | null>(null)
  const prevLook = useRef<OrbState>(look)
  const morph = useRef<{ from: Form | null; kept: Form | null }>({ from: null, kept: null })
  const [k] = useState(() => motionValue(1))
  const [scale] = useState(() => motionValue(1))
  const [changing, setChanging] = useState<'none' | 'morph' | 'bloom'>('none')
  // A change under way, set in the effect that starts it: the render that changed the state still
  // reads `changing` as 'none', and must not say it has settled.
  const inFlight = useRef(false)
  const blooms = useRef(bloom)
  blooms.current = bloom
  useEffect(() => {
    const from = prevLook.current
    prevLook.current = look
    const settle = () => { inFlight.current = false; morph.current.from = null; morph.current.kept = null; k.set(1); scale.set(1); setChanging('none') }
    // No change of state (the platform or the screen changed, or a phase changed inside one state):
    // finish any change in flight at its target, so nothing is left half-formed or painting.
    if (from.state === look.state) { if (morph.current.from || scale.get() !== 1) settle(); return }
    const planned = transitionOf(from, look, { reduced: reduced || mark || hidden || !onScreen })
    const plan = { ...planned, bloom: planned.bloom && blooms.current }
    if (plan.instant || !lastForm.current) { settle(); return }
    morph.current.from = lastForm.current
    morph.current.kept = null
    inFlight.current = true
    k.set(0)
    setChanging(plan.bloom ? 'bloom' : 'morph')
    const driver = cappedDriver()
    const runs = [animate(k, 1, { ...physics.smooth, driver })]
    if (plan.bloom) runs.push(animate(scale, 1, { ...physics.playful, velocity: 2.4, driver }))
    // A bloom cut short by this change: its scale comes home on the morph's spring, never in a jump.
    else if (scale.get() !== 1) runs.push(animate(scale, 1, { ...physics.smooth, driver }))
    let live = true
    Promise.all(runs.map((r) => r.finished)).then(() => { if (live) settle() })
    return () => { live = false; for (const r of runs) r.stop() }
  }, [look.state, reduced, mark, hidden, onScreen]) // eslint-disable-line react-hooks/exhaustive-deps -- keyed on the state; the ink and the badge follow the look in the paint and the render

  // Settled: the change has ended, or the state was swapped at once.
  const settledRef = useRef(onSettled)
  settledRef.current = onSettled
  useEffect(() => { if (changing === 'none' && !inFlight.current) settledRef.current?.(look.state) }, [changing, look.state])

  const running = (moves || changing !== 'none') && onScreen

  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    const dpr = Math.min(2, (typeof devicePixelRatio !== 'undefined' && devicePixelRatio) || 1)
    el.width = Math.round(size * dpr)
    el.height = Math.round(size * dpr)
    let tint = tintOf(ink)
    const paint = (t: number) => {
      const target = moves ? formAt(look.state, t) : restForm(look.state)
      const from = morph.current.from
      let form = target
      if (from) {
        // Each stroke's way round is chosen once, from the target as the morph's first frame found it.
        morph.current.kept ??= target
        form = mixToward(from, target, morph.current.kept, Math.min(1, Math.max(0, k.get())))
      }
      lastForm.current = form
      paintRing(ctx, form, { size, dpr, ink: tint, scale: scale.get() })
    }
    return runDots(paint, { running, speed: 1, retint: () => { tint = tintOf(inkOf(token)) } })
  }, [look, size, ink, token, running, moves, changing]) // eslint-disable-line react-hooks/exhaustive-deps -- the morph is read through its refs at each paint

  const tone = `var(--color-${WORK_PHASE_LOOKS[phase].tone})`
  const badge = badgeBox(size)
  return (
    <span
      data-agent-orb={!MOVES[look.state] || mark ? 'still' : reduced ? 'instant' : 'animated'}
      data-orb-phase={phase} data-orb-family={WORK_PHASE_LOOKS[phase].family} data-orb-state={look.state}
      data-orb-size={size} data-orb-ink={ink ?? ''} data-orb-ink-token={token} data-orb-contrast={contrast}
      data-orb-change={changing}
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
