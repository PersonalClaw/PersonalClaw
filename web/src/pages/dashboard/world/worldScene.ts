import type { AgentActivityEntity, AgentActivityKind, AgentActivityState } from '../../../lib/useAgentActivity'

// ── The world's scene model (AMBIENT-SURFACES A2-3) ──────────────────────────
// Everything here is PURE: entities in, geometry out. No canvas, no DOM, no rAF,
// no fetch. That split is deliberate — a WebGL/canvas scene cannot be asserted on
// in jsdom (there is no GPU and `getContext` is a stub), so the parts that carry
// the actual claims ("attention sits at the centre", "a state change EASES, it
// never teleports", "reduced motion is a STATIC layout") live as functions a unit
// test can hold. `AgentWorld.tsx` is then a thin painter over this model.
//
// Coordinates are NORMALISED 0..1 around a centre of (0.5, 0.5), so the model is
// resolution- and device-pixel-ratio-independent and the same scene renders into a
// 320px widget or a 4K canvas.

/** Per-state presentation. `tone` is a design-token CUSTOM PROPERTY NAME, never a
 *  literal colour: a canvas cannot use a Tailwind class, so the painter resolves
 *  these off the document at draw time and the world inherits the active theme
 *  (light/dark/custom) for free. The tokens are the same ones `loopStatusMeta`
 *  uses for loop status, so the world's colour language matches the lists. */
export const STATE_VISUAL: Record<AgentActivityState, {
  tone: string
  /** Orbit radius, 0..1 of the half-min-dimension. SMALLER = closer to centre =
   *  more of the user's attention. Attention-demanding states are pulled inward. */
  ring: number
  /** Breathing amplitude, 0..1. `idle` is exactly 0 — a sleeping agent that
   *  throbs reads as busy. */
  pulse: number
  /** Angular speed, 0..1. `working` is the fastest thing on screen because it is
   *  the only state where something is actually happening. */
  speed: number
}> = {
  needs_input: { tone: '--color-info', ring: 0.20, pulse: 1.00, speed: 0.50 },
  waiting_approval: { tone: '--color-warn', ring: 0.32, pulse: 0.80, speed: 0.40 },
  error: { tone: '--color-danger', ring: 0.46, pulse: 0.30, speed: 0.15 },
  working: { tone: '--color-ok', ring: 0.62, pulse: 0.55, speed: 1.00 },
  idle: { tone: '--color-on-surface-low', ring: 0.80, pulse: 0.00, speed: 0.10 },
}

/** Node size by kind, 0..1 of the scene's base node radius. A loop is a whole run,
 *  a session is a conversation, a subagent is one delegated errand — the visual
 *  weight follows that nesting so a glance reads structure, not just count. */
export const KIND_SCALE: Record<AgentActivityKind, number> = {
  loop: 1.0, session: 0.72, subagent: 0.5,
}

/** Paint order — furthest/least salient first so attention states land on top. */
const RING_ORDER: AgentActivityState[] = ['idle', 'working', 'error', 'waiting_approval', 'needs_input']

/** Time constant of the state ease, in ms. A node covers ~63% of the distance to
 *  its target in one TAU. Tuned so a state change reads as a deliberate glide
 *  (~1s to settle) rather than a snap or a drift. */
export const EASE_TAU = 320

/** One entity's resolved place in the scene. */
export interface ScenePlacement {
  id: string
  kind: AgentActivityKind
  state: AgentActivityState
  title: string
  /** Normalised position, 0..1 (centre = 0.5, 0.5). */
  x: number
  y: number
  /** Node radius, 0..1 of the scene's base node radius. */
  r: number
  /** Design-token custom-property name for this node's tone. */
  tone: string
  /** Breathing amplitude, 0..1. */
  pulse: number
  /** Angular speed, 0..1. */
  speed: number
  /** Phase offset in radians, so same-ring nodes do not breathe in lockstep. */
  phase: number
  /** The entity's own progress, passed through (absent = unknown). */
  progress?: number
}

/** A node mid-ease. `from*` fields are what it is easing AWAY from, so the painter
 *  can crossfade a tone instead of hard-cutting a colour on a state change. */
export interface SceneNode extends ScenePlacement {
  /** Tone being eased away from — equals `tone` once settled. */
  fromTone: string
  /** 0..1 ease progress. 0 = just changed/entered, 1 = fully settled. Drives BOTH
   *  the tone crossfade and a new node's fade-in. */
  mix: number
}

/** Deterministic small hash → a stable per-entity phase. Deterministic matters: the
 *  scene must be identical across renders and across a refetch that returned the
 *  same entities, or every fold would visibly reshuffle the world. */
function phaseOf(id: string): number {
  let h = 2166136261
  for (let i = 0; i < id.length; i++) {
    h ^= id.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return ((h >>> 0) % 3600) / 3600 * Math.PI * 2
}

/** Where every entity belongs, right now, at rest. Concentric rings by state with
 *  even angular spacing inside each ring; the ring's own rotation comes from the
 *  painter's clock, so this stays pure and testable. */
export function layoutScene(entities: AgentActivityEntity[]): ScenePlacement[] {
  const out: ScenePlacement[] = []
  for (const state of RING_ORDER) {
    const inRing = entities.filter((e) => e.state === state)
    if (inRing.length === 0) continue
    const v = STATE_VISUAL[state]
    inRing.forEach((e, i) => {
      // A lone node on a ring sits at the top rather than at an arbitrary angle;
      // with several, spread evenly. The +phase keeps two rings from aligning.
      const angle = (i / inRing.length) * Math.PI * 2 - Math.PI / 2 + phaseOf(e.id) * 0.12
      out.push({
        id: e.id,
        kind: e.kind,
        state: e.state,
        title: e.title,
        x: 0.5 + Math.cos(angle) * v.ring * 0.5,
        y: 0.5 + Math.sin(angle) * v.ring * 0.5,
        r: KIND_SCALE[e.kind],
        tone: v.tone,
        pulse: v.pulse,
        speed: v.speed,
        phase: phaseOf(e.id),
        ...(e.progress === undefined ? {} : { progress: e.progress }),
      })
    })
  }
  return out
}

const lerp = (a: number, b: number, t: number) => a + (b - a) * t

/** Ease fraction for a frame of `dt` ms — a critically-damped approach, so the step
 *  is frame-rate independent (a 144Hz display and a 30Hz one settle in the same
 *  wall-clock time) and never overshoots. */
export function easeStep(dt: number): number {
  return 1 - Math.exp(-Math.max(0, dt) / EASE_TAU)
}

/** Advance the live scene one frame toward `target`.
 *
 *  This is the "smooth state interpolation" clause, as a function: a node whose
 *  state changed does not jump to its new ring — it MOVES there, and its tone
 *  crossfades while it travels. A node that is new to the scene enters from just
 *  outside its target radius at `mix: 0` so it grows in rather than popping.
 *  A node that left is dropped (a departure is not a teleport). */
export function interpolateScene(prev: SceneNode[], target: ScenePlacement[], dt: number): SceneNode[] {
  const t = easeStep(dt)
  const byId = new Map(prev.map((n) => [n.id, n]))
  return target.map((p) => {
    const was = byId.get(p.id)
    if (!was) {
      // Entering: same angle, pushed 35% further out, fully transparent.
      const dx = p.x - 0.5
      const dy = p.y - 0.5
      return { ...p, x: 0.5 + dx * 1.35, y: 0.5 + dy * 1.35, r: p.r * 0.4, fromTone: p.tone, mix: 0 }
    }
    // A tone change restarts the crossfade from where the node currently is.
    const changed = was.tone !== p.tone
    return {
      ...p,
      x: lerp(was.x, p.x, t),
      y: lerp(was.y, p.y, t),
      r: lerp(was.r, p.r, t),
      pulse: lerp(was.pulse, p.pulse, t),
      speed: lerp(was.speed, p.speed, t),
      fromTone: changed ? was.tone : was.fromTone,
      mix: changed ? 0 : Math.min(1, lerp(was.mix, 1, t)),
    }
  })
}

/** The scene as a STATIC layout — every node settled at its resting place with the
 *  breathing amplitude at zero.
 *
 *  This is what `prefers-reduced-motion: reduce` renders, and the distinction the
 *  audit checks is exact: reduced motion is not a slower animation, it is the
 *  ABSENCE of one. Nothing orbits, nothing pulses, nothing eases, and the painter
 *  runs no animation frame at all — one paint, then still. */
export function staticScene(entities: AgentActivityEntity[]): SceneNode[] {
  return layoutScene(entities).map((p) => ({ ...p, pulse: 0, speed: 0, fromTone: p.tone, mix: 1 }))
}

/** The scene's own summary line, for the accessible name and the visible caption.
 *  A canvas is invisible to assistive tech and to anyone who cannot read a moving
 *  dot field, so the same facts are always available as text. */
export function sceneSummary(entities: AgentActivityEntity[], truncated: number): string {
  if (entities.length === 0) return 'Nothing is running.'
  const n = (s: AgentActivityState) => entities.filter((e) => e.state === s).length
  const parts: string[] = []
  const say = (count: number, word: string) => { if (count > 0) parts.push(`${count} ${word}`) }
  say(n('needs_input'), 'waiting on you')
  say(n('waiting_approval'), 'waiting for approval')
  say(n('working'), 'working')
  say(n('error'), 'in error')
  say(n('idle'), 'idle')
  const tail = truncated > 0 ? `, and ${truncated} more not shown` : ''
  return `${parts.join(', ')}${tail}.`
}

/** Which rendering tier this browser can actually give us.
 *
 *  🔴 DELIBERATELY TWO TIERS, NOT THREE. The plan's clause reads "WebGL/shader-grade
 *  OR high-craft canvas" and this world takes the canvas half of that disjunction.
 *  A `'webgl'` tier was drafted and removed: a shader pipeline cannot be exercised
 *  in jsdom (no GPU, `getContext('webgl')` is a stub), so it would have shipped as a
 *  DECLARED TIER WITH AN UNTESTED RUNTIME — a black rectangle for any user whose
 *  shader compile failed, and this repo's most familiar failure shape. The craft is
 *  bought with layered additive glow, per-node tone crossfade and eased orbits in
 *  canvas 2D, all of which the scene model above makes assertable.
 *
 *  Asked as a function so the fallback is a TESTED decision rather than an
 *  incidental try/catch: `static` means no drawing context at all (headless, or a
 *  privacy extension blocking canvas), and the component then renders the DOM list
 *  — never a blank rectangle where the world should be. */
export type RenderTier = '2d' | 'static'

export function pickRenderTier(canvas: {
  getContext: (id: string, opts?: unknown) => unknown | null
} | null): RenderTier {
  if (!canvas) return 'static'
  // A blocked-canvas browser THROWS from getContext rather than returning null.
  try { return canvas.getContext('2d') ? '2d' : 'static' } catch { return 'static' }
}
