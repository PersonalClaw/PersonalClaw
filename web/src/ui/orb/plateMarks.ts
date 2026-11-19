/** The marks on the orb's plate (`docs/design/agent-orb.md`, "The form"): a bold check for done, "!"
 *  for needs your OK, "?" for asks you, "×" for an error and a broken link for offline, each in its
 *  state's tone. A mark is round strokes and dots in a unit box (x, y about -0.7 to 0.7, y down). Every
 *  mark is resampled to the same strokes and dots, so a change between two plate states morphs one
 *  mark into the other, and a change to or from a state with no plate shrinks the mark to the centre
 *  as it fades. The glyph's shape, not its colour, tells the five apart. */

import type { Inks, Rgb } from './halftone'
import type { PlateScene } from './halftone'

type Pt = [number, number]
type Tone = 'ok' | 'warn' | 'info' | 'danger' | 'rest'
interface Glyph { s: Pt[][]; d: [number, number, number, number][]; tone: Tone }

const TAU = Math.PI * 2
const arc = (cx: number, cy: number, r: number, a0: number, a1: number, n = 18): Pt[] =>
  Array.from({ length: n + 1 }, (_, i) => { const a = a0 + ((a1 - a0) * i) / n; return [cx + Math.cos(a) * r, cy + Math.sin(a) * r] })
const seg = (x0: number, y0: number, x1: number, y1: number): Pt[] => [[x0, y0], [x1, y1]]
const pill = (x: number, y: number, w: number, h: number, r: number): Pt[] => [
  ...arc(x + w - r, y + r, r, -Math.PI / 2, 0, 4), ...arc(x + w - r, y + h - r, r, 0, Math.PI / 2, 4),
  ...arc(x + r, y + h - r, r, Math.PI / 2, Math.PI, 4), ...arc(x + r, y + r, r, Math.PI, Math.PI * 1.5, 4), [x + w - r, y],
]

const GLYPHS: Readonly<Record<PlateScene, Glyph>> = {
  done: { s: [[[-0.5, 0.02], [-0.15, 0.36], [0.54, -0.36]]], d: [], tone: 'ok' },
  approval: { s: [seg(0, -0.56, 0, 0.18)], d: [[0, 0.5, 0.11, 1]], tone: 'warn' },
  question: { s: [[...arc(0, -0.26, 0.28, Math.PI * 1.08, Math.PI * 2.32, 14), [0, 0.18]]], d: [[0, 0.5, 0.11, 1]], tone: 'info' },
  error: { s: [seg(-0.4, -0.4, 0.4, 0.4), seg(0.4, -0.4, -0.4, 0.4)], d: [], tone: 'danger' },
  offline: {
    s: [pill(-0.66, -0.16, 0.5, 0.32, 0.16), pill(0.16, -0.16, 0.5, 0.32, 0.16), seg(-0.06, -0.34, -0.02, -0.22), seg(0.06, 0.34, 0.02, 0.22)],
    d: [], tone: 'rest',
  },
}

/** Strokes, points per stroke and dots every mark is resampled to. */
const K = 9, NP = 18, ND = 12

function resample(line: Pt[], n: number): Pt[] {
  if (line.length === 1) return Array.from({ length: n }, () => [line[0][0], line[0][1]] as Pt)
  const L = [0]
  for (let i = 1; i < line.length; i++) L.push(L[i - 1] + Math.hypot(line[i][0] - line[i - 1][0], line[i][1] - line[i - 1][1]))
  const tot = L[L.length - 1] || 1e-6, out: Pt[] = []
  let j = 1
  for (let k = 0; k < n; k++) {
    const want = (k / (n - 1)) * tot
    while (j < line.length - 1 && L[j] < want) j++
    const f = (want - L[j - 1]) / (L[j] - L[j - 1] || 1e-6)
    out.push([line[j - 1][0] + (line[j][0] - line[j - 1][0]) * f, line[j - 1][1] + (line[j][1] - line[j - 1][1]) * f])
  }
  return out
}

interface Norm { s: Pt[][]; a: number[]; d: [number, number, number, number][] }

function norm(g: Glyph | null): Norm {
  let cx = 0, cy = 0, n = 0
  for (const l of g?.s ?? []) for (const p of l) { cx += p[0]; cy += p[1]; n++ }
  if (n) { cx /= n; cy /= n }
  const s: Pt[][] = [], a: number[] = [], d: [number, number, number, number][] = []
  for (let k = 0; k < K; k++) {
    const l = g?.s[k]
    if (l) { s.push(resample(l, NP)); a.push(1) } else { s.push(Array.from({ length: NP }, () => [cx, cy] as Pt)); a.push(0) }
  }
  for (let k = 0; k < ND; k++) { const p = g?.d[k]; d.push(p ? [p[0], p[1], p[2], p[3]] : [cx, cy, 0, 0]) }
  return { s, a, d }
}
const NORMS: Readonly<Record<PlateScene, Norm>> = Object.fromEntries(Object.entries(GLYPHS).map(([k, g]) => [k, norm(g)])) as Record<PlateScene, Norm>
const NONE = norm(null)

/** Each plate state's weight in the frame. */
export type MarkWeights = Partial<Record<PlateScene, number>>

export interface Mark extends Norm {
  /** The colour the marks' tones blend to, in a scheme. */
  tone: (C: Inks) => Rgb
}

/** The mark for a frame: each plate state's glyph by its weight, and `rest` (the weight of states
 *  with no plate) as nothing at the centre, in the rest tone. */
export function markOf(w: MarkWeights, rest = 0): Mark {
  const s: Pt[][] = Array.from({ length: K }, () => Array.from({ length: NP }, () => [0, 0] as Pt))
  const a = new Array<number>(K).fill(0)
  const d: [number, number, number, number][] = Array.from({ length: ND }, () => [0, 0, 0, 0])
  const tones: Partial<Record<Tone, number>> = {}
  const add = (g: Norm, tone: Tone, wt: number) => {
    for (let k = 0; k < K; k++) { a[k] += wt * g.a[k]; for (let i = 0; i < NP; i++) { s[k][i][0] += wt * g.s[k][i][0]; s[k][i][1] += wt * g.s[k][i][1] } }
    for (let k = 0; k < ND; k++) for (let q = 0; q < 4; q++) d[k][q] += wt * g.d[k][q]
    tones[tone] = (tones[tone] ?? 0) + wt
  }
  for (const [st, wt] of Object.entries(w) as [PlateScene, number][]) if (wt > 0.001) add(NORMS[st], GLYPHS[st].tone, wt)
  if (rest > 0.001) add(NONE, 'rest', rest)
  return {
    s, a, d,
    tone: (C) => {
      let r = 0, g = 0, b = 0, tot = 0
      for (const [k, v] of Object.entries(tones) as [Tone, number][]) { const col = C[k]; r += col.r * v; g += col.g * v; b += col.b * v; tot += v }
      tot = tot || 1
      return { r: Math.round(r / tot), g: Math.round(g / tot), b: Math.round(b / tot) }
    },
  }
}

/** Draws a mark as round strokes and dots: centred at (cx, cy), `sc` CSS pixels a unit. */
export function drawMark(ctx: CanvasRenderingContext2D, g: Norm, cx: number, cy: number, sc: number, width: number, color: Rgb, alpha = 1): void {
  const rgba = (a: number) => `rgba(${color.r},${color.g},${color.b},${Math.min(1, a)})`
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.lineWidth = width
  for (let k = 0; k < K; k++) {
    const a = g.a[k] * alpha
    if (a < 0.02) continue
    ctx.strokeStyle = rgba(a)
    ctx.beginPath()
    g.s[k].forEach(([x, y], i) => (i ? ctx.lineTo(cx + x * sc, cy + y * sc) : ctx.moveTo(cx + x * sc, cy + y * sc)))
    ctx.stroke()
  }
  for (const [x, y, r, a] of g.d) {
    if (a * alpha < 0.02 || r <= 0) continue
    ctx.fillStyle = rgba(a * alpha)
    ctx.beginPath(); ctx.arc(cx + x * sc, cy + y * sc, Math.max(r * sc, width * 0.5), 0, TAU); ctx.fill()
  }
}
