/** The buddy's orb on the halftone (`docs/design/agent-orb.md`, `docs/design/buddy/character.md`):
 *  the agent orb's own drawing for each state the buddy shows, and the one geometry its painters
 *  share. The window paints it on a canvas (`BuddyOrb.tsx`), a native banner's icon is a still of it
 *  (`stillOrbPng`), and the drawn poses and the tray's stills are SVG of the very same particles
 *  (`buddyRig/poseSources.ts`), so the window, the icon, the sources and the rasters are one drawing.
 *
 *  On the desktop the orb sits on a disc plate of its own, the surface with a hairline and a soft
 *  shadow, so it reads over any wallpaper or window (round 6's desktop strip). A pose moves the whole
 *  orb only (a rise or fall, a move to one side, a scale by one factor, a dim) and never redraws it. */

import { ORB_SCENE_OF, SCENE_MOVES, type OrbScene } from '../../lib/orbState'
import type { WorkPhase } from '../../lib/workPhase'
import { DOTS, PLATE_SCENES, css, drawFrame, particles, type Clock, type Inks, type Rgb, type Weights } from '../orb/halftone'
import { markOf } from '../orb/plateMarks'
import { SCENES } from '../orb/scenes'

/** What the buddy shows: a phase of the contract, or one of the system's own states. */
export type BuddyState = WorkPhase | 'restarting' | 'offline'

/** The halftone state the buddy draws for `state`. A phase is the agent orb's own, but for one: the
 *  buddy's listening is a push-to-talk capture, so it is the recording dot, as the tray's listening
 *  still is. An announced restart comes back, so it is retrying, never offline. */
export function buddySceneOf(state: BuddyState): OrbScene {
  if (state === 'restarting') return 'retrying'
  if (state === 'offline') return 'offline'
  if (state === 'listening') return 'recording'
  return ORB_SCENE_OF[state]
}

/** Whether the buddy's drawing moves for `state`: as its halftone state's does. */
export const buddyMoves = (state: BuddyState): boolean => SCENE_MOVES[buddySceneOf(state)]

/** How far the canvas reaches past the orb on each side, as a share of its size: room for the
 *  plate, its shadow and the burst mid-change. */
export const PAD = 0.2
/** The disc plate under the orb, as a share of its size: its radius, about the orb's centre. */
export const PLATE_R = 0.56

/** What a pose does to the whole orb: its rise (negative) or fall and its move to one side, as
 *  shares of its size, its scale, one factor for both axes, and its brightness. */
export interface Whole { lift: number; sway: number; swell: number; dim: number }
export const STILL_WHOLE: Whole = Object.freeze({ lift: 0, sway: 0, swell: 1, dim: 1 })

export interface BuddyPaint {
  size: number
  /** The canvas's room round the orb, in CSS pixels, and its pixel ratio. */
  pad: number
  dpr: number
  /** Whether the orb sits on its disc plate: the desktop's window does. */
  plate: boolean
  whole: Whole
  /** Whether done may play its swirl and bump. */
  bloom?: boolean
}

/** Paints one frame of the buddy into `ctx`: its disc, then the halftone frame, moved, scaled and
 *  dimmed by the pose as one. The disc is the orb's own seat, so it rises, sways and swells with
 *  it. Clears the canvas first. */
export function paintBuddy(ctx: CanvasRenderingContext2D, w: Weights, clock: Clock, C: Inks, p: BuddyPaint): void {
  const box = p.size + p.pad * 2
  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.clearRect(0, 0, box * p.dpr, box * p.dpr)
  const c = p.pad + p.size / 2
  const dx = p.whole.sway * p.size, dy = p.whole.lift * p.size
  // The pose's transform: the orb's centre moves by (dx, dy), and everything scales about it.
  const s = p.whole.swell
  ctx.setTransform(p.dpr * s, 0, 0, p.dpr * s, (c + dx - c * s) * p.dpr, (c + dy - c * s) * p.dpr)
  if (p.plate && C.plate) {
    ctx.save()
    ctx.shadowColor = C.dark ? 'rgba(0,0,0,0.55)' : 'rgba(20,30,40,0.22)'
    ctx.shadowBlur = Math.max(p.size * 0.14, 3)
    ctx.shadowOffsetY = Math.max(p.size * 0.03, 1)
    ctx.fillStyle = css(C.plate)
    ctx.beginPath(); ctx.arc(c, c, p.size * PLATE_R, 0, Math.PI * 2); ctx.fill()
    ctx.shadowColor = 'transparent'
    ctx.lineWidth = 1
    ctx.strokeStyle = css(C.hair)
    ctx.stroke()
    ctx.restore()
  }
  drawFrame(ctx, w, clock, C, SCENES, { size: p.size, x: p.pad, y: p.pad, bloom: p.bloom, alpha: p.whole.dim })
  ctx.setTransform(1, 0, 0, 1, 0, 0)
}

// ── the same frame as SVG ────────────────────────────────────────────────────────────────────

/** The inks an SVG source paints with, as the tokens a style resolves. */
const ROLE_TOKENS = {
  alive: '--color-orb-alive', rest: '--color-orb-rest', ok: '--color-ok', warn: '--color-warn', info: '--color-info', danger: '--color-danger',
} as const
type Role = keyof typeof ROLE_TOKENS
const ROLES = Object.keys(ROLE_TOKENS) as Role[]
/** Two runs of basis inks, three roles each: every colour a frame blends from them reads back as
 *  the share of each role it holds, so a source names its colours by token, never by value. */
const BASIS: [Partial<Record<Role, Rgb>>, Partial<Record<Role, Rgb>>] = [
  { alive: { r: 255, g: 0, b: 0 }, rest: { r: 0, g: 255, b: 0 }, ok: { r: 0, g: 0, b: 255 } },
  { warn: { r: 255, g: 0, b: 0 }, info: { r: 0, g: 255, b: 0 }, danger: { r: 0, g: 0, b: 255 } },
]
const BLACK: Rgb = { r: 0, g: 0, b: 0 }
const basisInks = (run: Partial<Record<Role, Rgb>>): Inks => ({
  alive: run.alive ?? BLACK, rest: run.rest ?? BLACK, ok: run.ok ?? BLACK, warn: run.warn ?? BLACK, info: run.info ?? BLACK, danger: run.danger ?? BLACK,
  plate: undefined, hair: BLACK, dark: false,
})
const num = (n: number): string => String(Math.round(n * 100) / 100)

/** A colour as its roles' shares: one token, or a `color-mix` of the two that hold the most. */
function colourOf(shares: Record<Role, number>, template: boolean): string {
  if (template) return 'black'
  const ranked = ROLES.map((r) => [r, shares[r]] as const).filter(([, v]) => v > 0.02).sort((a, b) => b[1] - a[1])
  if (!ranked.length) return `var(${ROLE_TOKENS.rest})`
  if (ranked.length === 1 || ranked[1][1] < 0.04) return `var(${ROLE_TOKENS[ranked[0][0]]})`
  const [[a, wa], [b, wb]] = ranked
  return `color-mix(in srgb, var(${ROLE_TOKENS[a]}) ${num((wa / (wa + wb)) * 100)}%, var(${ROLE_TOKENS[b]}))`
}

/** The roles' shares of each particle's colour, from the two basis runs. */
function particleShares(w: Weights, clock: Clock, small: boolean): Record<Role, number>[] {
  const [a, b] = BASIS.map((run) => particles(w, clock, basisInks(run), small, SCENES))
  return Array.from({ length: DOTS }, (_, i) => ({
    alive: a[i][6] / 255, rest: a[i][7] / 255, ok: a[i][8] / 255, warn: b[i][6] / 255, info: b[i][7] / 255, danger: b[i][8] / 255,
  }))
}

export interface SvgOrb {
  /** The plate's disc, drawn under the orb, or `''` with none. */
  plate: string
  /** The orb's dots and short lines. */
  dots: string
  /** The plate state's mark, or `''`. */
  mark: string
}

/** One still of the buddy's orb as SVG parts, in the orb's own frame (its box is 0 to `size`): the
 *  very particles the canvas paints, each a round dot or a round-capped line, in its tokens, or in
 *  black for a template the menu bar recolours. */
export function svgOrb(w: Weights, size: number, { template = false, plate = false }: { template?: boolean; plate?: boolean } = {}): SvgOrb {
  const clock: Clock = { t: 0.9, since: 1e6, still: true }
  const small = size < 30
  const P = particles(w, clock, basisInks(BASIS[0]), small, SCENES)
  const shares = particleShares(w, clock, small)
  const c = size / 2, sc = size * 0.44
  const dots: string[] = []
  P.forEach((q, i) => {
    const a = q[3]
    if (a < 0.03 || q[2] <= 0) return
    const rp = small ? Math.max(q[2] * size * 1.4, 0.55) : Math.max(q[2] * size, 0.45)
    const x = c + q[0] * sc, y = c + q[1] * sc
    const col = colourOf(shares[i], template)
    const op = a >= 0.999 ? '' : ` opacity="${num(a)}"`
    if (Math.abs(q[4]) + Math.abs(q[5]) < 0.003) dots.push(`<circle cx="${num(x)}" cy="${num(y)}" r="${num(rp)}" style="fill: ${col}"${op}/>`)
    else dots.push(`<line stroke-linecap="round" x1="${num(x - q[4] * sc)}" y1="${num(y - q[5] * sc)}" x2="${num(x + q[4] * sc)}" y2="${num(y + q[5] * sc)}" stroke-width="${num(rp * 2)}" style="stroke: ${col}"${op}/>`)
  })
  let mark = ''
  let plateDisc = ''
  if (plate) {
    plateDisc = `<circle cx="${num(c)}" cy="${num(c)}" r="${num(size * PLATE_R)}" stroke-width="1" style="fill: ${template ? 'none' : 'var(--color-surface)'}; stroke: ${template ? 'none' : 'var(--color-outline-variant)'}"/>`
  }
  const plates = PLATE_SCENES.filter((s) => (w[s] ?? 0) > 0.001)
  const plateW = plates.reduce((a, s) => a + (w[s] ?? 0), 0)
  if (plateW > 0.02) {
    const marks: Parameters<typeof markOf>[0] = {}
    for (const s of plates) marks[s] = w[s]
    const rest = (Object.entries(w) as [OrbScene, number][]).filter(([s]) => !(PLATE_SCENES as readonly OrbScene[]).includes(s)).reduce((a, [, v]) => a + v, 0)
    const m = markOf(marks, rest)
    const scene = plates.reduce((a, b) => ((w[b] ?? 0) > (w[a] ?? 0) ? b : a))
    const toneRole: Role = scene === 'done' ? 'ok' : scene === 'approval' ? 'warn' : scene === 'question' ? 'info' : scene === 'error' ? 'danger' : 'rest'
    const ink = template ? 'black' : `var(${ROLE_TOKENS[toneRole]})`
    const fade = plateW >= 0.999 ? '' : ` opacity="${num(Math.min(1, plateW))}"`
    const [mx, my, msc, mw] = small ? [size * 0.56, size * 0.56, size * 0.52, Math.max(size * 0.085, 1.4)] : [size * 0.73, size * 0.72, size * 0.25 * 0.92, Math.max(size * 0.04, 1.2)]
    const parts: string[] = []
    if (small) {
      // At 20 px and below the orb gives way to the mark: a dot at its upper left, the mark large.
      const alive = plates.some((p) => p === 'approval' || p === 'question')
      parts.push(`<circle cx="${num(size * 0.2)}" cy="${num(size * 0.2)}" r="${num(Math.max(size * 0.11, 1.6))}" style="fill: ${template ? 'black' : `var(${alive ? ROLE_TOKENS.alive : ROLE_TOKENS.rest})`}"/>`)
    } else if (!template) {
      parts.push(`<circle cx="${num(mx)}" cy="${num(my)}" r="${num(size * 0.25)}" stroke-width="1" style="fill: var(--color-surface); stroke: var(--color-outline-variant)"/>`)
    }
    m.s.forEach((line, k) => {
      if (m.a[k] < 0.02) return
      parts.push(`<path d="M${line.map(([x, y]) => `${num(mx + x * msc)} ${num(my + y * msc)}`).join('L')}" stroke-width="${num(mw)}" stroke-linecap="round" stroke-linejoin="round" style="fill: none; stroke: ${ink}"/>`)
    })
    for (const [x, y, r, a] of m.d) if (a >= 0.02 && r > 0) parts.push(`<circle cx="${num(mx + x * msc)}" cy="${num(my + y * msc)}" r="${num(Math.max(r * msc, mw * 0.5))}" style="fill: ${ink}"/>`)
    mark = fade ? `<g${fade}>${parts.join('')}</g>` : parts.join('')
  }
  return { plate: plateDisc, dots: dots.join(''), mark }
}
