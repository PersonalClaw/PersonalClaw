/** What the orb's dots become for each state that is not the sphere itself (`docs/design/agent-orb.md`,
 *  "The twenty states"). The orb stays on screen, smaller, with its ripple, and the work flows into it
 *  or out of it; for the jobs about the whole, planning and searching, it grows into a faint globe.
 *  Coral (the alive ink) is what is active and grey (the rest ink) what is passive; ok, danger, warn
 *  and info appear only where a scene names them. Every scene with phases starts from its beginning
 *  when its state is entered (`E.ts`, the seconds since), and its loops are calm, 2.4 to 6.4 s; a
 *  still (`E.still`) is drawn at fixed instants that tell the state apart.
 *
 *  The timings are the approved design's own, so they are written here as numbers of seconds rather
 *  than read from the motion tokens: a token's slider would change the drawing the owner approved. */

import type { OrbScene } from '../../lib/orbState'
import { TAU, clamp, fr, hash, lerp, level, mixRgb, orb, outline, rrect, sm, word, words, type Pen, type Rgb, type Scene } from './halftone'

type Pt = [number, number]
const TXT = 0.019
const pmod = (a: number, n: number) => ((a % n) + n) % n
const dotted = (E: Pen, poly: Pt[], n: number, a = 0.7, closed = true, r = 0.0105) => outline(E, poly, n, r, a, E.C.rest, 0, closed)
const quad = (A: Pt, Cp: Pt, B: Pt, e: number): Pt => [
  (1 - e) * (1 - e) * A[0] + 2 * (1 - e) * e * Cp[0] + e * e * B[0],
  (1 - e) * (1 - e) * A[1] + 2 * (1 - e) * e * Cp[1] + e * e * B[1],
]
/** `n` dots flowing along a curve from A to B while `on`; they shrink as they arrive. */
const stream = (E: Pen, A: Pt, Cp: Pt, B: Pt, t: number, per: number, n: number, on: boolean, r = 0.015) => {
  for (let m = 0; m < n; m++) {
    const e = fr(t / per + m / n), [x, y] = quad(A, Cp, B, e)
    E.dot(x, y, r * (1 - 0.45 * e), on ? Math.sin(Math.PI * e) : 0, E.C.alive)
  }
}
/** A document: a dotted page with its top-right corner folded. */
const doc = (E: Pen, x: number, y: number, w: number, h: number, n: number, f = 0.18) => {
  dotted(E, [[x, y], [x + w - f, y], [x + w, y + f], [x + w, y + h], [x, y + h]], n)
  dotted(E, [[x + w - f, y + 0.02], [x + w - f, y + f], [x + w - 0.02, y + f]], 6, 0.6, false)
}
/** A speech bubble whose tail is part of its outline, the tail's tip at `tip`. */
const bubble = (x: number, y: number, w: number, h: number, rad: number, t0: number, t1: number, tip: Pt): Pt[] => {
  const p = rrect(x, y, w, h, rad)
  return [...p.slice(0, 12), [t1, y + h], tip, [t0, y + h], ...p.slice(12)]
}
const ring = (E: Pen, x: number, y: number, R: number, n: number, r: number, a: number, col: Rgb, a0 = 0) => {
  for (let m = 0; m < n; m++) { const an = a0 + (m / n) * TAU; E.dot(x + Math.cos(an) * R, y + Math.sin(an) * R, r, a, col) }
}
type V3 = [number, number, number]
/** The globe: a unit vector from a latitude and a longitude. */
const vec = ([la, lo]: Pt): V3 => [Math.cos(la) * Math.sin(lo), -Math.sin(la), Math.cos(la) * Math.cos(lo)]
const slerp = (a: V3, b: V3, f: number): V3 => {
  const d = clamp(a[0] * b[0] + a[1] * b[1] + a[2] * b[2], -1, 1), w = Math.acos(d) || 1e-6
  const sa = Math.sin((1 - f) * w) / Math.sin(w), sb = Math.sin(f * w) / Math.sin(w)
  return [a[0] * sa + b[0] * sb, a[1] * sa + b[1] * sb, a[2] * sa + b[2] * sb]
}
/** Planning's waypoints, on the globe's surface. */
const WAY: V3[] = ([[-0.32, -0.95], [0.38, -0.32], [-0.18, 0.28], [0.36, 0.86]] as Pt[]).map(vec)
/** Searching's three finds. */
const HITS: Pt[] = [[-0.42, -0.24], [0.1, 0.38], [0.46, -0.3]]
const lineLens = (j: number, a = 0.22, b = 0.16, c = 0.12) => [a + 0.1 * hash(j, 1), b + 0.12 * hash(j, 2), c + 0.1 * hash(j, 3)]

export const SCENES: Readonly<Partial<Record<OrbScene, Scene>>> = {
  // it waits, dimmed and breathing slowly, behind a small grey orb whose turn it is; a quiet
  // ellipsis between
  queued(E) {
    const { C } = E, t = E.still ? 0.9 : E.t, br = E.still ? 1 : 1 + 0.02 * Math.sin(t * 1.5)
    orb(E, -0.32, 0, 0.5 * br, { ripple: 3.6, a: 0.78 })
    orb(E, 0.64, 0, 0.19, { own: 40, stride: 6, offset: 1, spin: 0.08, ripple: 1.6, col: C.rest })
    for (let k = 0; k < 3; k++) {
      const on = E.still ? (k === 1 ? 0.9 : 0.3) : 0.25 + 0.75 * Math.pow(Math.max(0, Math.sin(TAU * (t / 2 - k / 3))), 6)
      E.dot(0.24 + k * 0.08, 0, 0.012, on, C.rest)
    }
  },
  // the orb grows into a globe that turns a little and charts a great-circle route on its surface,
  // waypoint by waypoint, the last one green; then a pulse runs the route
  planning(E) {
    const { C } = E, q = E.still ? 0.78 : fr(E.ts / 6.4), t = E.still ? 0 : E.t
    const yaw = -0.1 + 0.26 * Math.sin(t * 0.28), R = 0.84, tip = 0.28, fa = 1 - sm((q - 0.93) / 0.07)
    orb(E, 0, 0, R, { stride: 2, v: 0.15, col: C.rest, a: 0.72, yaw })
    const P = (v: V3): V3 => {
      const x = v[0] * Math.cos(yaw) + v[2] * Math.sin(yaw), z0 = -v[0] * Math.sin(yaw) + v[2] * Math.cos(yaw)
      const y = v[1] * Math.cos(tip) - z0 * Math.sin(tip), z = v[1] * Math.sin(tip) + z0 * Math.cos(tip)
      return [x * R, y * R, clamp((z + 0.25) / 0.5)]
    }
    const drawn = 3 * sm(clamp(q / 0.6))
    for (let k = 0; k < 3; k++) for (let m = 0; m < 11; m++) {
      const f = (m + 0.5) / 11, [x, y, vis] = P(slerp(WAY[k], WAY[k + 1], f))
      E.dot(x, y, 0.011, fa * vis * 0.9 * sm((drawn - k - f) * 5), C.alive)
    }
    WAY.forEach((v, k) => {
      const [x, y, vis] = P(v), pop = sm((drawn - k + 0.12) * 3) * fa * vis, last = k === 3
      ring(E, x, y, 0.07 * (0.6 + 0.4 * pop), 8, 0.0085, pop * 0.9, last ? C.ok : C.alive)
      E.dot(x, y, last ? 0.026 : 0.02, pop, last ? C.ok : C.alive)
    })
    const pq = clamp((q - 0.64) / 0.25)
    for (let m = 0; m < 3; m++) {
      const pm = clamp(pq - m * 0.035), k = Math.min(2, Math.floor(pm * 3)), [px, py, pv] = P(slerp(WAY[k], WAY[k + 1], sm(pm * 3 - k)))
      E.dot(px, py, 0.03 - m * 0.008, fa * pv * (pq > 0 && pq < 1 ? 1 - m * 0.3 : 0), C.alive)
    }
  },
  // a scan line sweeps the globe, lighting the dots under it; what it finds pings once and stays ringed
  searching(E) {
    const { C } = E, q = E.still ? 0.62 : fr(E.ts / 3.6)
    const sx = -0.98 + 1.96 * sm(clamp(q / 0.76)), on = q < 0.8 ? 1 - sm((q - 0.72) / 0.08) : 0
    orb(E, 0, 0, 0.84, { stride: 2, v: 0.15, col: C.rest, a: 0.72, lit: (x) => on * clamp(1 - Math.abs(x * 0.84 - sx) / 0.1) })
    for (let m = 0; m < 14; m++) {
      const y = -0.78 + m * 0.12, hw = Math.sqrt(Math.max(0, 0.84 * 0.84 - y * y))
      E.dot(sx, y, 0.0105, on * (Math.abs(sx) < hw - 0.02 ? 0.85 : 0), C.alive)
    }
    HITS.forEach(([x, y]) => {
      const found = sx > x ? sm((sx - x) / 0.08) : 0, fade = 1 - sm((q - 0.9) / 0.1), a = found * fade
      ring(E, x, y, 0.085, 8, 0.0085, a * 0.9, C.alive)
      E.dot(x, y, 0.022, a, C.alive)
      const e = E.still ? 1 : clamp((sx - x) / 0.55)
      ring(E, x, y, 0.085 + 0.16 * e, 10, 0.007, found * (1 - e) * 0.6, C.alive)
    })
  },
  // a document; a highlight grows along the line being read, read words turn coral and stream into
  // the orb, and the page scrolls a line at a time
  reading(E) {
    const { C } = E, t = E.still ? 1 : E.t
    orb(E, 0.64, 0, 0.3, { ripple: 0.9 })
    doc(E, -0.94, -0.76, 1.12, 1.52, 50)
    const p = E.still ? 2.45 : (E.ts + 0.3) / 2.6, L0 = Math.floor(p), u = p - L0
    const sweep = sm(clamp(u / 0.82)), scroll = sm((u - 0.82) / 0.18), x0 = -0.82
    let head: Pt = [x0, -0.25]
    for (let b = 0; b < 6; b++) {
      const j = L0 - 1 + pmod(b - (L0 - 1), 6), s = j - L0 + 1 - scroll, y = -0.5 + s * 0.25
      const aS = clamp((s + 0.5) / 0.5) * clamp((4.5 - s) / 0.5)
      const lens = lineLens(j), W = lens.reduce((a, c) => a + c, 0) + 0.16, sx = x0 + sweep * W
      let xs = x0
      const starts = lens.map((l) => { const s0 = xs; xs += l + 0.08; return s0 })
      const lit = (k: number) => j < L0 || (j === L0 && starts[k] < sx - 0.02)
      if (j === L0) { E.seg(x0 - 0.03, y, Math.max(x0 - 0.02, sx), y, 0.044, 0.18 * aS, C.alive); head = [sx, y] } else E.dot(x0, y, 0.01, 0, C.alive)
      words(E, x0, y, lens, 1, (k) => (lit(k) ? C.alive : C.rest), (k) => aS * (lit(k) ? 0.9 : 0.5), TXT, 3)
    }
    stream(E, head, [(head[0] + 0.55) / 2, head[1] - 0.22], [0.52, -0.02], t, 0.75, 5, u < 0.82)
  },
  // a diff in place: a red band and "-" mark a line whose dots drop away; a caret types its
  // replacement from the orb's dots under a green band and "+", then it settles to ink
  editing(E) {
    const { C } = E, t = E.still ? 1 : E.t, q = E.still ? 0.6 : fr(E.ts / 4.4)
    orb(E, -0.68, 0, 0.26, { ripple: 1.1 })
    doc(E, -0.3, -0.78, 1.22, 1.56, 50)
    const x0 = -0.16, Y = [-0.44, -0.14, 0.16, 0.46], LT = [0.3, 0.38]
    words(E, x0, Y[0], [0.36, 0.24], 1, C.alive, 0.7, TXT, 3)
    words(E, x0, Y[2], [0.42, 0.2], 1, C.alive, 0.7, TXT, 3)
    words(E, x0, Y[3], [0.28, 0.3], 1, C.alive, 0.7, TXT, 3)
    const y = Y[1], W = 0.3 + 0.38 + 0.08
    const red = sm((q - 0.04) / 0.1) * (1 - sm((q - 0.34) / 0.06)), del = clamp((q - 0.2) / 0.16)
    E.seg(x0 - 0.04, y, x0 + W + 0.02, y, 0.044, 0.18 * red, C.danger)
    E.seg(-0.24, y, -0.2, y, 0.012, red, C.danger)
    let x = x0
    LT.forEach((len) => {
      const n = 3, gap = len / n
      for (let m = 0; m < n; m++) {
        const px = x + gap * (m + 0.5), e = del * del
        E.dot(px + 0.1 * e, y + 0.08 * e, TXT, q < 0.2 ? 0.8 : 0.8 * (1 - del), mixRgb(C.alive, C.danger, red * 0.6), (gap / 2) * (1 - clamp(del * 3)), 0)
      }
      x += len + 0.08
    })
    const g = clamp((q - 0.4) / 0.32), green = sm((q - 0.38) / 0.06) * (1 - sm((q - 0.8) / 0.12))
    const head = words(E, x0, y, LT, g, mixRgb(C.ok, C.alive, sm((q - 0.76) / 0.18)), 0.8, TXT, 3)
    E.seg(x0 - 0.04, y, Math.max(x0 - 0.03, head + 0.02), y, 0.044, 0.18 * green, C.ok)
    E.seg(-0.24, y, -0.2, y, 0.012, green, C.ok)
    E.seg(-0.22, y - 0.02, -0.22, y + 0.02, 0.012, green, C.ok)
    E.seg(head + 0.04, y - 0.065, head + 0.04, y + 0.065, 0.01, g > 0 && g < 1 ? 1 : 0, C.alive)
    stream(E, [-0.42, 0], [(head - 0.42) / 2, -0.12], [head, y], t, 0.55, 4, g > 0 && g < 1)
  },
  // a 3-dot stream goes from the orb through a ">"; output lines scroll up, the newest lit
  running(E) {
    const { C } = E, t = E.still ? 0.9 : E.t
    orb(E, -0.62, 0, 0.36, { ripple: 0.9, spin: 0.15 })
    for (let m = 0; m < 3; m++) { const e = fr(t / 0.6 + m / 3); E.dot(lerp(-0.24, -0.14, e), 0, 0.013, Math.sin(Math.PI * e), C.alive) }
    E.seg(-0.11, -0.14, 0.02, 0, 0.022, 1, C.alive)
    E.seg(0.02, 0, -0.11, 0.14, 0.022, 1, C.alive)
    const p = t / 0.46, L0 = Math.floor(p), u = sm(clamp((p - L0) / 0.5))
    for (let b = 0; b < 5; b++) {
      const j = L0 - 4 + pmod(b - (L0 - 4), 5), m = L0 - j, y = 0.48 - (m - 1 + u) * 0.21
      const a = m === 0 ? u : m === 4 ? 1 - u : 1
      words(E, 0.15, y, [0.18 + 0.3 * hash(j, 5), 0.12 + 0.22 * hash(j, 6)], 1, mixRgb(C.rest, C.alive, m === 0 ? 1 : m === 1 ? 1 - u : 0), 0.85 * a, 0.018, 3, 0.06)
    }
  },
  // the big orb feeds two little orbs over dotted links; each pulses as work reaches it
  delegating(E) {
    const { C } = E, t = E.still ? 0.9 : E.t
    orb(E, -0.42, 0, 0.46, { ripple: 1.4 })
    ;([[0.6, -0.5], [0.6, 0.5]] as Pt[]).forEach(([hx, hy], k) => {
      let pulse = 0
      const ph = (m: number) => fr(t / 1.5 + m / 3 + k * 0.5)
      for (let m = 0; m < 3; m++) { const e = ph(m); if (e > 0.92) pulse = Math.max(pulse, Math.sin((Math.PI * (e - 0.92)) / 0.08)) }
      const by = hy + 0.02 * Math.sin(t * 1.4 + k * 2)
      orb(E, hx, by, 0.23 * (1 + 0.08 * pulse), { own: 40, stride: 6, offset: 1 + k * 2, spin: 0.15, v: 0.5 + 0.3 * pulse })
      const A: Pt = [-0.02, hy * 0.42], B: Pt = [hx - 0.27, by]
      for (let m = 0; m < 7; m++) { const e = (m + 0.5) / 7; E.dot(lerp(A[0], B[0], e), lerp(A[1], B[1], e), 0.009, 0.55, C.rest) }
      for (let m = 0; m < 3; m++) { const e = ph(m); E.dot(lerp(A[0], B[0], e), lerp(A[1], B[1], e), 0.02, Math.sin(Math.PI * e), C.alive) }
    })
  },
  // it fills a speech bubble that points at it, word by word, then the bubble clears
  answering(E) {
    const { C } = E, t = E.still ? 1.9 : E.ts + 0.2, q = fr(t / 3.8)
    orb(E, -0.66, 0.52, 0.3, { ripple: 1.0 })
    dotted(E, bubble(-0.36, -0.84, 1.3, 1.0, 0.22, -0.16, 0.04, [-0.38, 0.36]), 56, 0.85)
    const g = clamp(q / 0.85), fa = 1 - sm((q - 0.9) / 0.1)
    const Ls = [[0.36, 0.3, 0.24], [0.42, 0.34], [0.3]], ys = [-0.6, -0.34, -0.08]
    let head: Pt = [-0.22, -0.6]
    Ls.forEach((lens, i) => {
      const gi = clamp(g * 3 - i)
      const hx = words(E, -0.22, ys[i], lens, gi, C.alive, 0.9 * fa, TXT)
      if (gi > 0 && gi < 1) head = [hx, ys[i]]
    })
    stream(E, [-0.52, 0.34], [-0.5, -0.1], head, t, 0.6, 4, g < 1 && fa > 0.5)
  },
  // a little orb circles the orb on a dotted track, lighting it behind; it backs up a quarter turn,
  // pauses, and goes on
  retrying(E) {
    const { C } = E, R = 0.66, D = 1.1 * Math.PI
    const th = (tt: number) => {
      const n = Math.floor(tt / 2.6), q = tt / 2.6 - n
      const f = q < 0.6 ? 1.6 * Math.PI * sm(q / 0.6) : q < 0.78 ? 1.6 * Math.PI - 0.5 * Math.PI * sm((q - 0.6) / 0.18) : D
      return n * D + f - Math.PI / 2
    }
    const t = E.still ? 1.0 : E.ts + 0.5, head = th(t)
    orb(E, 0, 0, 0.34, { ripple: 1.3 })
    orb(E, Math.cos(head) * R, Math.sin(head) * R, 0.1, { own: 30, stride: 6, offset: 1, v: 0.8, rs: 1.2 })
    for (let m = 0; m < 30; m++) {
      const a = (m / 30) * TAU - Math.PI / 2, d = pmod(head - a, TAU), lit = d < 2.6 ? 1 - d / 2.6 : 0
      E.dot(Math.cos(a) * R, Math.sin(a) * R, 0.009 + 0.007 * lit, 0.3 + 0.65 * lit, mixRgb(C.rest, C.alive, lit))
    }
    for (let m = 1; m <= 3; m++) { const a = head + 0.16 + m * 0.11; E.dot(Math.cos(a) * R, Math.sin(a) * R, 0.014 - m * 0.0025, 1 - m * 0.22, C.alive) }
  },
  // a tapered audio graph whose waves travel into the orb
  listening(E) {
    const { C } = E, L = E.lev(0)
    orb(E, 0.52, 0, 0.38 + 0.04 * L, { ripple: 0.9 })
    for (let k = 0; k < 10; k++) {
      const x = -0.94 + k * 0.105, env = Math.sin((Math.PI * (k + 0.8)) / 10.6)
      const Lk = E.still ? 0.3 + 0.6 * (0.5 + 0.5 * Math.sin(k * 1.4)) : level(E.t - (x + 0.94) * 0.45, 0)
      const h = (0.04 + 0.4 * Lk * env) * (E.still ? 1 : sm(E.since / 0.4))
      E.seg(x, -h, x, h, 0.014, 0.95, C.alive)
    }
  },
  // the orb becomes a small, dense red dot that pulses with the level, in a ring of 24 grey ticks
  recording(E) {
    const { C } = E, L = E.lev(0)
    orb(E, 0, 0, 0.27 + 0.07 * L, { v: 0.85, rs: 1.25, col: C.danger })
    for (let k = 0; k < 24; k++) {
      const a = (k / 24) * TAU - Math.PI / 2, lk = E.still ? 0.25 + 0.5 * hash(k, 2) : E.lev(k % 6, 0.02 * k)
      const r0 = 0.54, r1 = r0 + 0.03 + 0.15 * lk
      E.seg(Math.cos(a) * r0, Math.sin(a) * r0, Math.cos(a) * r1, Math.sin(a) * r1, 0.011, 0.7, C.rest)
    }
  },
  // sound bars go in on the left of the orb, and words come out on the right, as dots that join
  // into dashes
  transcribing(E) {
    const { C } = E, t = E.still ? 1.4 : E.t
    orb(E, -0.08, 0, 0.3, { ripple: 0.9 })
    for (let k = 0; k < 6; k++) {
      const x = -0.94 + k * 0.1, env = 1 - k / 7
      const Lk = E.still ? 0.3 + 0.6 * (0.5 + 0.5 * Math.sin(k * 1.4)) : level(t - (x + 0.94) * 0.45, 0)
      const h = 0.04 + 0.34 * Lk * env
      E.seg(x, -h, x, h, 0.014, 0.95, C.alive)
    }
    for (let k = 0; k < 4; k++) {
      const ph = t * 0.34 + k / 4, n = Math.floor(ph), x = 0.3 + fr(ph) * 0.92, len = 0.16 + 0.1 * hash(n * 4 + k, 3)
      const g = clamp((x - 0.3) / 0.22) * 0.5 + 0.5 * clamp((x - 0.42) / 0.12)
      word(E, x, x + len, 0, g, C.alive, clamp((1.02 - x - len) / 0.12), TXT, 4)
    }
  },
  // three dotted arcs go out from the orb, as loud as the voice
  speaking(E) {
    const { C } = E, t = E.still ? 0.9 : E.t, L = E.lev(0)
    orb(E, -0.55, 0, 0.38 + 0.04 * L, { ripple: 0.7 })
    for (let j = 0; j < 3; j++) {
      const f = fr(t / 1.6 + j / 3), R = 0.52 + 0.7 * f
      for (let m = 0; m < 13; m++) { const a = -0.66 + (m / 12) * 1.32; E.dot(-0.55 + Math.cos(a) * R, Math.sin(a) * R, 0.013, (1 - f) * (0.5 + 0.55 * L), C.alive) }
    }
  },
}
