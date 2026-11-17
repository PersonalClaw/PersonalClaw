/** The halftone orb (`docs/design/agent-orb.md`, "The form"): the agent as a sphere of 272 dots.
 *
 *  The sphere is a golden-angle spiral of dots tilted toward you, each dot as large and as solid as
 *  it is near. At rest it is still and in the rest ink; while it thinks a ripple travels out from its
 *  centre in the alive ink. Done, needs your OK, asks you, error and offline step the orb up and to
 *  the left, smaller, and put a bold mark on a small plate at its lower right (`plateMarks.ts`). Every
 *  other state is a scene the same dots fly out of the sphere to become (`scenes.ts`): a document
 *  being read, a diff being made, a globe being charted.
 *
 *  A particle is `[x, y, r, a, sx, sy, R, G, B]`: its centre in orb units (the sphere's radius is 1,
 *  0.44 of the box), its radius as a share of the box, its alpha, half the vector it is stretched
 *  along (0, 0 for a round dot, so any dot can stretch into a short line), and its colour. Every
 *  state places all `DOTS` particles, so a change of state moves each particle from its place in one
 *  state to its place in the next, and mid-change the dots fly: a short, gentle, streaked burst that
 *  peaks halfway and lands exactly.
 *
 *  Everything here is a pure function of the weights, the clock and the inks, so a frame can be
 *  drawn at any instant, a still is the same frame every time, and tests can read what is drawn. */

import type { OrbScene } from '../../lib/orbState'
import { drawMark, markOf, type MarkWeights } from './plateMarks'

export const DOTS = 272
export const TAU = Math.PI * 2
const GOLD = Math.PI * (3 - Math.sqrt(5))
/** How far the sphere is tipped toward you, in radians. */
const TILT = 0.32

export interface Rgb { r: number; g: number; b: number }

/** The inks a frame is drawn in: the scheme's, resolved (`AgentOrb.inkOf`). */
export interface Inks {
  alive: Rgb
  rest: Rgb
  ok: Rgb
  warn: Rgb
  info: Rgb
  danger: Rgb
  /** The plate's ground, when it has one (a mark alone without), and its hairline. */
  plate?: Rgb
  hair: Rgb
  /** Whether the scheme is dark: the plate's shadow is deeper on a dark ground. */
  dark: boolean
}

/** What a frame is drawn at. `t` is the page clock and `since` the seconds since the state was
 *  entered, both in seconds; a still frame is drawn at fixed instants, so it never changes. */
export interface Clock { t: number; since: number; still: boolean }

/** A particle: `[x, y, r, a, sx, sy, R, G, B]` (see above). */
export type Particle = [number, number, number, number, number, number, number, number, number]

// ── small maths ─────────────────────────────────────────────────────────────────────────────

export const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x))
export const fr = (x: number) => x - Math.floor(x)
/** Smoothstep: 0 below 0, 1 above 1, eased between. */
export const sm = (x: number) => { const c = clamp(x); return c * c * (3 - 2 * c) }
export const easeOut = (x: number) => 1 - Math.pow(1 - clamp(x), 3)
const bumpF = (x: number) => { const s = Math.sin(Math.PI * clamp(x)); return s * s }
/** A repeatable value in [0, 1) for a dot's index: its own phase, its own scatter. */
export const hash = (i: number, j = 0) => { const h = Math.sin(i * 12.9898 + j * 78.233) * 43758.5453; return h - Math.floor(h) }
export const lerp = (a: number, b: number, k: number) => a + (b - a) * k
export const mixRgb = (a: Rgb, b: Rgb, k: number): Rgb => ({ r: Math.round(lerp(a.r, b.r, k)), g: Math.round(lerp(a.g, b.g, k)), b: Math.round(lerp(a.b, b.b, k)) })
export const css = (c: Rgb, a = 1) => `rgba(${c.r},${c.g},${c.b},${a})`

/** A level that rises and falls like a voice, smooth but irregular, for the voice states. */
export const level = (t: number, k = 0) => 0.35 + 0.65 * Math.abs(Math.sin(t * 7.3 + k * 1.7) * Math.sin(t * 2.9 + k))

// ── the sphere ──────────────────────────────────────────────────────────────────────────────

interface SpherePoint { x: number; y: number; z: number; h: number }

/** `n` dots on a golden-angle spiral over the unit sphere, tipped toward you. */
function spiral(n: number, salt: number): SpherePoint[] {
  const ct = Math.cos(TILT), st = Math.sin(TILT)
  return Array.from({ length: n }, (_, i) => {
    const y = 1 - (2 * (i + 0.5)) / n, r = Math.sqrt(1 - y * y), a = i * GOLD
    const x = r * Math.cos(a), z0 = r * Math.sin(a)
    return { x, y: -(y * ct - z0 * st), z: y * st + z0 * ct, h: hash(i, salt) }
  })
}

export const SPHERE: readonly SpherePoint[] = spiral(DOTS, 4)

/** A sphere dot's radius, as a share of the box: larger the brighter (`v`) and the nearer it is. */
const sphereR = (v: number, front: number) => 0.03 * (0.16 + 0.84 * clamp(v)) * (0.45 + 0.55 * front)

/** The five states drawn as the orb with a mark on its plate. */
export const PLATE_SCENES = ['done', 'approval', 'question', 'error', 'offline'] as const satisfies readonly OrbScene[]
export type PlateScene = (typeof PLATE_SCENES)[number]
const isPlate = (s: OrbScene): s is PlateScene => (PLATE_SCENES as readonly string[]).includes(s)
/** The states drawn as the sphere itself: at rest, thinking, and the five with a plate. */
export const SPHERE_SCENES: ReadonlySet<OrbScene> = new Set<OrbScene>(['ready', 'thinking', ...PLATE_SCENES])
/** Where the orb sits beside its plate: smaller, up and to the left. */
const SIDE = { k: 0.74, x: -0.19, y: -0.21 }
/** Done's one swirl, in seconds: the orb turns once as it settles. */
export const DONE_SWIRL_S = 0.9
/** How long done moves after it is entered: its swirl, then its bump lands. */
export const DONE_MOVES_S = 1.0

/** The sphere, as each state that is the sphere draws it: still and grey at rest, the ripple while
 *  it thinks, and beside its plate for done, needs your OK, asks you, error and offline. */
function sphereScene(s: OrbScene, clock: Clock, C: Inks, small: boolean, out: (Particle | null)[]): void {
  const plate = isPlate(s)
  const sw = s === 'done' && !clock.still ? easeOut(clamp(clock.since / DONE_SWIRL_S)) * TAU : 0
  const cs = Math.cos(sw), sn = Math.sin(sw)
  for (let i = 0; i < DOTS; i++) {
    const p = SPHERE[i], front = (p.z + 1) / 2
    let x = p.x, y = p.y, v = 0.22 + 0.2 * front, a = 0.35 + 0.65 * front, col = C.rest, k = 1
    if (s === 'thinking') {
      const ph = (clock.still ? 0.9 : clock.t) / 1.9 - Math.hypot(p.x, p.y) * 1.15 + p.h * 0.18
      v = 0.12 + 0.12 * front + 0.66 * bumpF(fr(ph)) * front
      col = C.alive
    } else if (s === 'done') {
      if (i % 2) a = 0
      const x0 = x
      x = x0 * cs - y * sn
      y = x0 * sn + y * cs
    } else if (s === 'approval' || s === 'question') {
      v = 0.42 + 0.3 * front
      col = C.alive
      k = 0.84
    } else if (s === 'error') {
      y += Math.max(0, 0.95 - y) * 0.36
    } else if (s === 'offline') {
      a *= i % 2 ? 0 : 0.45
    }
    let r = sphereR(v, front)
    if (plate) {
      x = SIDE.x + x * SIDE.k * k
      y = SIDE.y + y * SIDE.k * k
      r *= SIDE.k
      if (small) a = 0
    }
    out[i] = [x, y, r, a, 0, 0, col.r, col.g, col.b]
  }
}

// ── what a scene draws with ─────────────────────────────────────────────────────────────────

/** A scene's pen: dots, stretched dots and the next free particle, at the frame's clock. */
export interface Pen {
  t: number
  /** Seconds since the state was entered, or 0 for a still: a scene with phases starts from its
   *  beginning each time it is entered. */
  ts: number
  since: number
  still: boolean
  small: boolean
  C: Inks
  /** A voice level for the `k`th band, `dt` seconds ago: steady values for a still. */
  lev: (k?: number, dt?: number) => number
  /** Puts particle `i` (the sphere's own dots, so a part of the sphere shrinks into a small orb). */
  put: (i: number, x: number, y: number, r: number, a: number, col: Rgb, sx?: number, sy?: number) => void
  /** Puts the next free particle. */
  dot: (x: number, y: number, r: number, a?: number, col?: Rgb, sx?: number, sy?: number) => void
  /** A dot stretched into the line from (x0, y0) to (x1, y1). */
  seg: (x0: number, y0: number, x1: number, y1: number, r: number, a?: number, col?: Rgb) => void
}

function pen(clock: Clock, C: Inks, small: boolean, out: (Particle | null)[]): Pen {
  let cur = 0
  const put: Pen['put'] = (i, x, y, r, a, col, sx = 0, sy = 0) => { if (i >= 0 && i < DOTS) out[i] = [x, y, r, clamp(a), sx, sy, col.r, col.g, col.b] }
  const dot: Pen['dot'] = (x, y, r, a = 1, col = C.alive, sx = 0, sy = 0) => {
    while (cur < DOTS && out[cur]) cur++
    if (cur < DOTS) put(cur++, x, y, r, a, col, sx, sy)
  }
  return {
    t: clock.t, since: clock.since, ts: clock.still ? 0 : clock.since, still: clock.still, small, C,
    lev: (k = 0, dt = 0) => (clock.still ? 0.4 + 0.45 * hash(k + 1, 3) : level(clock.t - dt, k)),
    put, dot,
    seg: (x0, y0, x1, y1, r, a = 1, col = C.alive) => dot((x0 + x1) / 2, (y0 + y1) / 2, r, a, col, (x1 - x0) / 2, (y1 - y0) / 2),
  }
}

type Of<T> = T | ((k: number) => T)
const val = <T>(f: Of<T>, k: number): T => (typeof f === 'function' ? (f as (k: number) => T)(k) : f)

/** A word: `n` dots from x0 to x1. As `g` goes 0 to 1 the dots appear left to right, then stretch
 *  and join into one clean dash (overlapping round caps would bead it); its other particles wait,
 *  hidden, where their dots were, so a word keeps its particles. */
export function word(E: Pen, x0: number, x1: number, y: number, g: number, col: Rgb, a = 1, r = 0.021, n = 4): void {
  const gap = (x1 - x0) / n, ga = clamp(g / 0.6), gj = sm((g - 0.5) / 0.5)
  if (gj >= 0.999) {
    E.dot((x0 + x1) / 2, y, r, a, col, (x1 - x0) / 2, 0)
    for (let k = 1; k < n; k++) E.dot(x0 + gap * (k + 0.5), y, r, 0, col)
    return
  }
  for (let k = 0; k < n; k++) {
    const ak = clamp(ga * n - k)
    E.dot(x0 + gap * (k + 0.5), y, r * (0.55 + 0.45 * ak), a * ak, col, (gap / 2) * gj, 0)
  }
}

/** A line of words of the given lengths, written in turn as `g` goes 0 to 1. Returns the write
 *  head's x. `col` and `a` may be functions of the word's index. */
export function words(E: Pen, x0: number, y: number, lens: number[], g: number, col: Of<Rgb>, a: Of<number> = 1, r = 0.021, n = 4, gapW = 0.08): number {
  let x = x0, head = x0
  const L = lens.length
  lens.forEach((len, k) => {
    const gk = clamp(g * L - k)
    word(E, x, x + len, y, gk, val(col, k), val(a, k), r, n)
    if (gk > 0) head = x + len * Math.min(1, gk / 0.6)
    x += len + gapW
  })
  return head
}

type Pt = [number, number]
/** Points evenly along a polyline (closed or not), each with its unit tangent, and its length. */
export function along(poly: Pt[], n: number, closed = false): { pts: [number, number, number, number][]; len: number } {
  const P = closed ? [...poly, poly[0]] : poly
  const L = [0]
  for (let i = 1; i < P.length; i++) L.push(L[i - 1] + Math.hypot(P[i][0] - P[i - 1][0], P[i][1] - P[i - 1][1]))
  const tot = L[L.length - 1] || 1e-6
  const pts: [number, number, number, number][] = []
  let j = 1
  for (let k = 0; k < n; k++) {
    const want = closed ? (k / n) * tot : n === 1 ? 0 : (k / (n - 1)) * tot
    while (j < P.length - 1 && L[j] < want) j++
    const f = (want - L[j - 1]) / (L[j] - L[j - 1] || 1e-6)
    const dx = P[j][0] - P[j - 1][0], dy = P[j][1] - P[j - 1][1], d = Math.hypot(dx, dy) || 1
    pts.push([P[j - 1][0] + dx * f, P[j - 1][1] + dy * f, dx / d, dy / d])
  }
  return { pts, len: tot }
}

/** A rounded rectangle's outline, as a polyline. */
export function rrect(x: number, y: number, w: number, h: number, rad: number): Pt[] {
  const pts: Pt[] = []
  const corner = (cx: number, cy: number, a0: number) => { for (let i = 0; i <= 5; i++) { const a = a0 + (i / 5) * (Math.PI / 2); pts.push([cx + Math.cos(a) * rad, cy + Math.sin(a) * rad]) } }
  corner(x + w - rad, y + rad, -Math.PI / 2); corner(x + w - rad, y + h - rad, 0)
  corner(x + rad, y + h - rad, Math.PI / 2); corner(x + rad, y + rad, Math.PI)
  return pts
}

/** Dots along a shape's outline; `join` 0 leaves them dots, 1 stretches them into one line. */
export function outline(E: Pen, poly: Pt[], n: number, r: number, a: number, col: Rgb, join = 1, closed = true): void {
  const { pts, len } = along(poly, n, closed), sp = len / (closed ? n : Math.max(1, n - 1))
  for (const [x, y, tx, ty] of pts) E.dot(x, y, r, a, col, tx * sp * 0.5 * join, ty * sp * 0.5 * join)
}

const evenSpheres = new Map<number, SpherePoint[]>()
/** A fresh, even sphere of `n` dots, for a small orb that needs its own (a strided subset of the big
 *  sphere's dots falls into spiral arms). */
function evenSphere(n: number): SpherePoint[] {
  let F = evenSpheres.get(n)
  if (!F) evenSpheres.set(n, (F = spiral(n, 5)))
  return F
}

export interface OrbOpts {
  /** Which of the sphere's own dots it takes: every `stride`th from `offset`. */
  stride?: number
  offset?: number
  /** An even sphere of its own, of this many dots, put at the strided particles. */
  own?: number
  col?: Rgb
  /** Its dots' brightness; with `ripple`, the thinking ripple's period in seconds instead. */
  v?: number
  ripple?: number
  /** Turns a second, or a fixed turn. */
  spin?: number
  yaw?: number
  /** Squash and stretch, x and y; the dots' radius scale; the alpha. */
  sq?: [number, number]
  rs?: number
  a?: number
  /** Lights a dot by where it is (0 to 1): brighter, and toward `litCol`. */
  lit?: (x: number, y: number, z: number) => number
  litCol?: Rgb
}

/** A copy of the orb at (cx, cy) of radius R, from the sphere's own dots, put at their own particles
 *  so the big sphere shrinks into it. */
export function orb(E: Pen, cx: number, cy: number, R: number, o: OrbOpts = {}): void {
  const stride = o.stride || 2, off = o.offset || 0, col = o.col || E.C.alive
  const yaw = o.yaw ?? (o.spin ? (E.still ? 0.6 : E.t * o.spin * TAU) : 0), cyw = Math.cos(yaw), syw = Math.sin(yaw)
  const own = o.own ? evenSphere(o.own) : null, count = own ? own.length : Math.ceil((DOTS - off) / stride)
  const sq = o.sq || [1, 1], rs = Math.min(1, R * Math.sqrt(DOTS / count)) * (o.rs || 1)
  for (let k = 0; k < count; k++) {
    const i = off + k * stride
    if (i >= DOTS) break
    const p = own ? own[k] : SPHERE[i]
    const x0 = p.x * cyw - p.z * syw, z = p.x * syw + p.z * cyw, y0 = p.y
    const front = (z + 1) / 2
    let v = o.v ?? 0.3 + 0.2 * front
    if (o.ripple) {
      const ph = (E.still ? 0.9 : E.t) / o.ripple - Math.hypot(x0, y0) * 1.15 + p.h * 0.18
      v = 0.14 + 0.12 * front + 0.62 * bumpF(fr(ph)) * front
    }
    const L = o.lit ? clamp(o.lit(x0, y0, z)) : 0
    E.put(i, cx + x0 * R * sq[0], cy + y0 * R * sq[1], sphereR(v + 0.55 * L, front) * rs,
      Math.min(1, (0.35 + 0.65 * front) * (o.a ?? 1) + 0.5 * L), L > 0 ? mixRgb(col, o.litCol || E.C.alive, L) : col)
  }
}

// ── a frame ─────────────────────────────────────────────────────────────────────────────────

/** A scene: what the dots become for a state that is not the sphere (`scenes.ts`). */
export type Scene = (E: Pen) => void

/** Each state's weight in a frame: one at 1 when settled, two while it morphs. */
export type Weights = Partial<Record<OrbScene, number>>

/** The burst's size and its angular scatter, mid-change: gentle, as round 6 tuned it. */
const BURST = 0.15
const JITTER = 0.8

/** Every particle of a frame, blended from each weighted state's places, with the mid-change burst. */
export function particles(w: Weights, clock: Clock, C: Inks, small: boolean, scenes: Partial<Record<OrbScene, Scene>>): Particle[] {
  const per: [number, Particle[]][] = []
  for (const [s, wt] of Object.entries(w) as [OrbScene, number][]) {
    if (!(wt > 0.001)) continue
    const out: (Particle | null)[] = new Array(DOTS).fill(null)
    const scene = scenes[s]
    if (SPHERE_SCENES.has(s) || !scene) sphereScene(s, clock, C, small, out)
    else scene(pen(clock, C, small, out))
    for (let i = 0; i < DOTS; i++) if (!out[i]) { const p = SPHERE[i]; out[i] = [p.x, p.y, 0.01, 0, 0, 0, C.rest.r, C.rest.g, C.rest.b] }
    per.push([wt, out as Particle[]])
  }
  let fly = 0
  if (per.length >= 2) { const ws = per.map(([wt]) => wt).sort((a, b) => b - a); fly = 4 * ws[0] * ws[1] }
  const P: Particle[] = new Array(DOTS)
  for (let i = 0; i < DOTS; i++) {
    const q: Particle = [0, 0, 0, 0, 0, 0, 0, 0, 0]
    let ca = 0
    for (const [wt, out] of per) {
      const o = out[i]
      for (let j = 0; j < 6; j++) q[j] += wt * o[j]
      const wa = wt * (o[3] + 0.001)
      ca += wa; q[6] += wa * o[6]; q[7] += wa * o[7]; q[8] += wa * o[8]
    }
    if (ca > 0) { q[6] /= ca; q[7] /= ca; q[8] /= ca }
    if (fly > 0.01) {
      const ang = Math.atan2(q[1], q[0]) + (hash(i, 7) - 0.5) * JITTER, b = fly * BURST * (0.4 + hash(i, 3))
      q[0] += Math.cos(ang) * b; q[1] += Math.sin(ang) * b; q[4] += Math.cos(ang) * b * 0.35; q[5] += Math.sin(ang) * b * 0.35
    }
    P[i] = q
  }
  return P
}

/** Done's bump: a quick hop that lands with a small settle, as a share of the box. */
export function bump(since: number, still: boolean): number {
  if (still || since > 0.75) return 0
  const p = since / 0.75
  return -0.1 * Math.sin(Math.PI * Math.min(1, p / 0.55)) * (p < 0.55 ? 1 : 0) + (p >= 0.55 ? 0.025 * Math.sin(Math.PI * (p - 0.55) / 0.45) : 0)
}

/** The states that are alive, for a small orb's plate dot: everything but rest, done and a stop. */
const NOT_ALIVE: ReadonlySet<OrbScene> = new Set<OrbScene>(['ready', 'done', 'error', 'offline'])

export interface FrameOpts {
  /** The orb's box, in CSS pixels, and where its top-left sits in the canvas. */
  size: number
  x?: number
  y?: number
  /** Whether done plays its swirl and bump on arrival: a turn stopped or declined settles without. */
  bloom?: boolean
}

/** Draws one frame of the orb into `ctx` (already scaled to CSS pixels): the dots, then the plate
 *  and its mark. The caller clears. */
export function drawFrame(ctx: CanvasRenderingContext2D, w: Weights, clock: Clock, C: Inks, scenes: Partial<Record<OrbScene, Scene>>, opts: FrameOpts): void {
  const S = opts.size, ox = opts.x ?? 0, oy = opts.y ?? 0
  const small = S < 30
  // A done that may not bloom is drawn as its settled self from its first frame.
  const swirlClock = opts.bloom === false ? { ...clock, since: 1e6 } : clock
  const P = particles(w, swirlClock, C, small, scenes)
  const dy = bump(swirlClock.since, swirlClock.still) * S * (w.done ?? 0)
  const c = S / 2, sc = S * 0.44
  ctx.lineCap = 'round'
  for (const q of P) {
    const a = q[3]
    if (a < 0.03 || q[2] <= 0) continue
    const rp = small ? Math.max(q[2] * S * 1.4, 0.55) : Math.max(q[2] * S, 0.45)
    const x = ox + c + q[0] * sc, y = oy + c + q[1] * sc + dy
    const col = `rgba(${q[6] | 0},${q[7] | 0},${q[8] | 0},${Math.min(1, a)})`
    if (Math.abs(q[4]) + Math.abs(q[5]) < 0.003) {
      ctx.fillStyle = col
      ctx.beginPath(); ctx.arc(x, y, rp, 0, TAU); ctx.fill()
    } else {
      ctx.strokeStyle = col
      ctx.lineWidth = rp * 2
      ctx.beginPath(); ctx.moveTo(x - q[4] * sc, y - q[5] * sc); ctx.lineTo(x + q[4] * sc, y + q[5] * sc); ctx.stroke()
    }
  }
  const plateW = PLATE_SCENES.reduce((acc, s) => acc + (w[s] ?? 0), 0)
  if (plateW <= 0.02) return
  const marks: MarkWeights = {}
  let rest = 0
  for (const [s, wt] of Object.entries(w) as [OrbScene, number][]) {
    if (!(wt > 0.001)) continue
    if (isPlate(s)) marks[s] = wt
    else rest += wt
  }
  const mark = markOf(marks, rest)
  const tone = mark.tone(C)
  ctx.save()
  if (small) {
    // At 20 px and below the orb gives way to the mark: a dot at its upper left, the mark large.
    let alive = 0
    for (const [s, wt] of Object.entries(w) as [OrbScene, number][]) if (!NOT_ALIVE.has(s)) alive += wt
    const ink = mixRgb(C.rest, C.alive, clamp(alive))
    ctx.globalAlpha = clamp(plateW)
    ctx.fillStyle = css(ink)
    ctx.beginPath(); ctx.arc(ox + S * 0.2, oy + S * 0.2 + dy, Math.max(S * 0.11, 1.6), 0, TAU); ctx.fill()
    ctx.restore()
    drawMark(ctx, mark, ox + S * 0.56, oy + S * 0.56 + dy, S * 0.52, Math.max(S * 0.085, 1.4), tone, clamp(plateW))
    return
  }
  const tx = ox + S * 0.73, ty = oy + S * 0.72 + dy, pr = S * 0.25
  if (C.plate) {
    // The plate: the surface, a hairline, and a soft drop shadow that lifts it off the orb.
    ctx.globalAlpha = clamp(plateW)
    ctx.shadowColor = C.dark ? 'rgba(0,0,0,0.5)' : 'rgba(20,30,40,0.18)'
    ctx.shadowBlur = S * 0.06
    ctx.shadowOffsetY = S * 0.015
    ctx.fillStyle = css(C.plate)
    ctx.beginPath(); ctx.arc(tx, ty, pr, 0, TAU); ctx.fill()
    ctx.shadowColor = 'transparent'
    ctx.lineWidth = 1
    ctx.strokeStyle = css(C.hair)
    ctx.stroke()
  }
  ctx.restore()
  drawMark(ctx, mark, tx, ty, pr * 0.92, Math.max(S * 0.04, 1.2), tone, clamp(plateW))
}
