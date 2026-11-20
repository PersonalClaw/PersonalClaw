import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { computedToken, onEditorTokensChange } from '../design/editorTheme'
import { prefersReducedMotion } from '../design/motion'
import { ORB_STAGE_LABELS, ORB_STATE_OF, SCENE_MOVES, markSceneOfPhase, orbSceneOf, type OrbBadge, type OrbScene, type OrbStage } from '../lib/orbState'
import type { RunAct } from '../lib/api'
import { WORK_PHASE_LOOKS, phaseLabel, type WorkPhase } from '../lib/workPhase'
import { cx } from './cx'
import { drawFrame, type Inks, type Rgb } from './orb/halftone'
import { SCENES } from './orb/scenes'
import { useHeldToolStep } from './orb/useHeldToolStep'
import { useSceneMorph } from './orb/useSceneMorph'
import { runDots, tintOf, useOnScreen } from './orbDots'

/** AgentOrb — what an agent's turn is doing, as the halftone orb (`docs/design/agent-orb.md`).
 *
 *  It is handed a phase of the contract (`lib/workPhase.ts`), and a stage where a page has one that
 *  is not a phase (a capture, a transcription, a reply read aloud, the gateway away), and reads its
 *  state from the one mapping (`lib/orbState.ts`), so no page can show a state the gateway or the
 *  page did not measure. The orb is a sphere of 272 dots (`ui/orb/halftone.ts`): still at rest, a
 *  ripple while it thinks, a mark on a plate beside it when it is done, needs the owner or stopped,
 *  and for each kind of work a scene its dots fly out to become (`ui/orb/scenes.ts`). A change of
 *  state moves every dot on the `smooth` spring, and arriving at done from a completed turn plays
 *  one swirl and bump, which a failure, a stop or a decline never does.
 *  The words beside the orb say which phase it is, and a wait or a failure carries a mark whose
 *  glyph, not its colour, tells which. Its inks are the orb tokens: `--color-orb-alive` for what is
 *  active and `--color-orb-rest` for what is passive, with the status tones only where a state names
 *  one.
 *
 *  It asks for frames only while it moves or morphs, on screen, in a visible tab, with motion
 *  allowed, and then through the capped loop (`orbDots.ts`): at most `ORB_FPS` a second, and none
 *  between two paints. Under reduced motion every state is its still frame, and a change is an
 *  instant swap to the next. The 12 px mark never moves, and is drawn in the sphere's own form for
 *  its state (`markSceneOfPhase`). */

export type AgentOrbSize = 12 | 20 | 64

/** A badge's glyph: its shape, not its colour, says which wait or failure it is. The orb draws it on
 *  its plate; the root states it as `data-orb-badge` and `data-orb-glyph`. */
export const BADGE_GLYPHS: Readonly<Record<OrbBadge, string>> = { approval: '!', input: '?', error: '×' }
/** The glyph each state's plate carries, where it is one of the three. */
export const SCENE_GLYPHS: Readonly<Partial<Record<OrbScene, string>>> = { approval: '!', question: '?', error: '×' }

/** How far the canvas reaches past the orb's box on each side, as a share of its size: the burst
 *  mid-change and the plate's shadow draw there. */
export const ORB_PAD = 0.16

type Contrast = 'normal' | 'more' | 'forced'
const media = (query: string) => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(query).matches
const MEDIA_QUERIES = ['(prefers-reduced-motion: reduce)', '(forced-colors: active)', '(prefers-contrast: more)']
/** The contrast the platform asks of an orb's dots: forced colours, more contrast, or neither. */
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
 *  to nothing; the orb then draws nothing until it does. */
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

/** The token each of the orb's inks reads, under the contrast the platform asks for. Under more
 *  contrast what is active and what is passive take the words' ink; in forced colours everything
 *  takes the system's text colour on its canvas. */
export function orbInkTokens(contrast: Contrast): Record<Exclude<keyof Inks, 'dark'>, string> {
  if (contrast === 'forced') {
    return { alive: 'CanvasText', rest: 'CanvasText', ok: 'CanvasText', warn: 'CanvasText', info: 'CanvasText', danger: 'CanvasText', plate: 'Canvas', hair: 'CanvasText' }
  }
  const words = contrast === 'more'
  return {
    alive: words ? '--color-on-surface' : '--color-orb-alive',
    rest: words ? '--color-on-surface' : '--color-orb-rest',
    ok: '--color-ok', warn: '--color-warn', info: '--color-info', danger: '--color-danger',
    plate: '--color-surface', hair: words ? '--color-outline' : '--color-outline-variant',
  }
}

const luminance = ({ r, g, b }: Rgb) => (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255

/** Every ink the orb draws in, resolved from `tokens`, or `undefined` until what is active and what
 *  is passive both resolve. A status tone that resolves to nothing takes the alive ink, and a plate
 *  with no ground of its own is drawn as its mark alone. */
export function orbInksOf(tokens: Record<Exclude<keyof Inks, 'dark'>, string>): Inks | undefined {
  const alive = tintOf(inkOf(tokens.alive)), rest = tintOf(inkOf(tokens.rest))
  if (!alive || !rest) return undefined
  const tone = (token: string) => tintOf(inkOf(token)) ?? alive
  const plate = tintOf(inkOf(tokens.plate))
  return {
    alive, rest, ok: tone(tokens.ok), warn: tone(tokens.warn), info: tone(tokens.info), danger: tone(tokens.danger),
    plate, hair: tintOf(inkOf(tokens.hair)) ?? rest, dark: plate ? luminance(plate) < 0.5 : true,
  }
}

export function AgentOrb({ phase, act = null, stage = null, size = 20, className, onSettled, bloom = true }: {
  /** The turn's phase, from the phase contract. Never an orb state or a status line's words. */
  phase: WorkPhase
  /** What the call does while the phase is `tool`, as the gateway says it (`work_phase`'s `act`):
   *  it draws a call that reads as reading and one that edits as editing. Nothing in any other
   *  phase, and nothing at 12 px, where a row's mark is the sphere's own form. A tool step's state
   *  holds `TOOL_STEP_HOLD_MS` (1.2 s) from the step's start while the turn thinks or plans again;
   *  any other phase or a stage replaces it at once. */
  act?: RunAct | null
  /** What holds that is not a phase: a capture, a transcription, a reply read aloud, the gateway
   *  away. It wins over the phase while it is set. */
  stage?: OrbStage | null
  /** 20 in the turn's live line and the history peek; 64 for the empty chat and voice mode; 12 as
   *  a list row's still mark, as in the buddy card's rows. */
  size?: AgentOrbSize
  className?: string
  /** Called with the state the orb has settled on: when a change's morph or swirl ends, at once for
   *  a change that swaps, and on the first paint. A line that holds a turn's ending waits on it. */
  onSettled?: (scene: OrbScene) => void
  /** Whether arriving at done may play its swirl (default true). Read when the change starts: a turn
   *  that was stopped or declined settles into done without it. */
  bloom?: boolean
}) {
  const look = ORB_STATE_OF[phase]
  const mark = size === 12
  // At 12 px the orb is a still mark, in the sphere's own form for its state: a scene does not hold
  // at that size. A tool step's state holds 1.2 s from when the step began while the turn only thinks
  // again, so a call over in milliseconds is still seen (`orb/useHeldToolStep.ts`).
  const scene = useHeldToolStep(mark ? markSceneOfPhase(phase, stage) : orbSceneOf(phase, stage, act))
  const [motion, contrast] = useSyncExternalStore(onOrbMediaChange, mediaSnapshot, mediaSnapshot).split('|') as ['reduced' | 'motion', Contrast]
  const hidden = useSyncExternalStore(onVisibilityChange, visibility, visibility) === 'hidden'
  const tokens = orbInkTokens(contrast)
  // The ink the orb is mostly drawn in: alive while an agent works, listens or waits on the owner
  // (a stage, but for the gateway away), the rest ink where nothing is alive.
  const alive = stage ? stage !== 'offline' : look.ink === 'alive'
  const inkToken = alive ? tokens.alive : tokens.rest
  const ink = useInk(inkToken)
  const canvas = useRef<HTMLCanvasElement>(null)
  const onScreen = useOnScreen(canvas)
  const reduced = motion === 'reduced'
  const moves = SCENE_MOVES[scene] && !mark && !reduced && !hidden

  // The morph to each new state, and done's swirl (`orb/useSceneMorph.ts`), which `bloom` can hold
  // back. A change nobody can see, in a hidden tab or off screen, is an instant swap, and a change
  // cut short settles at its target.
  const morph = useSceneMorph(scene, reduced || mark || hidden || !onScreen, onSettled, bloom)
  const { changing } = morph
  const running = (moves || changing !== 'none') && onScreen
  const pad = Math.ceil(size * ORB_PAD)

  useEffect(() => {
    const el = canvas.current
    const ctx = el?.getContext('2d')
    if (!el || !ctx) return
    const dpr = Math.min(2, (typeof devicePixelRatio !== 'undefined' && devicePixelRatio) || 1)
    const box = size + pad * 2
    el.width = Math.round(box * dpr)
    el.height = Math.round(box * dpr)
    let inks = orbInksOf(tokens)
    const paint = (t: number) => {
      ctx.setTransform(1, 0, 0, 1, 0, 0)
      ctx.clearRect(0, 0, el.width, el.height)
      if (!inks) return
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      const still = !running
      const clock = still ? { t: 0.9, since: 1e6, still: true } : { t, since: morph.since(t), still: false }
      drawFrame(ctx, morph.weights(), clock, inks, SCENES, { size, x: pad, y: pad, bloom: morph.blooms() })
    }
    return runDots(paint, { running, speed: 1, retint: () => { inks = orbInksOf(tokens) } })
  }, [scene, size, pad, ink, contrast, running, moves, changing]) // eslint-disable-line react-hooks/exhaustive-deps -- the morph is read through its refs at each paint

  const label = stage ? ORB_STAGE_LABELS[stage] : phaseLabel(phase)
  const badge = stage ? undefined : look.badge
  return (
    <span
      data-agent-orb={!SCENE_MOVES[scene] || mark ? 'still' : reduced ? 'instant' : 'animated'}
      data-orb-phase={phase} data-orb-family={WORK_PHASE_LOOKS[phase].family} data-orb-state={look.state}
      data-orb-scene={scene} {...(stage ? { 'data-orb-stage': stage } : {})}
      data-orb-size={size} data-orb-ink={ink ?? ''} data-orb-ink-token={inkToken} data-orb-contrast={contrast}
      data-orb-change={changing}
      {...(badge ? { 'data-orb-badge': badge } : {})}
      {...(SCENE_GLYPHS[scene] ? { 'data-orb-glyph': SCENE_GLYPHS[scene] } : {})}
      className={cx('relative inline-block shrink-0', className)}
      style={{ width: size, height: size }}
    >
      <canvas
        ref={canvas} role="img" aria-label={label}
        className="pointer-events-none absolute block"
        style={{ width: size + pad * 2, height: size + pad * 2, left: -pad, top: -pad }}
      />
    </span>
  )
}
