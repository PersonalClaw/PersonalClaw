/** The pose set (`poseSet.ts`) drawn as layered SVG sources, and the contact sheets beside them
 *  (#3830). Each source is one whole orb, its pivot at its centre, holding three parts a renderer
 *  can move: the strokes (the agent orb's claw ring for the pose's state, as its pose moves it, in
 *  the one geometry the canvas paints, `ui/orb/paintRing.ts`'s `ringStrokes`), the wash and the
 *  badge. Every colour is a token, set in a style, so one source is the pose in light and in dark and
 *  in every scheme. A template still is drawn in black, which the menu bar recolours, and has no
 *  wash. The whole orb only moves or scales by one factor.
 *
 *  `poseSources.test.ts` writes them to `docs/design/buddy/poses/` and holds the committed files to
 *  what this draws. */

import type { PhaseTone } from '../lib/workPhase'
import { tokenValue, type Mode } from '../design/tokenSheet'
import { ringStrokes } from '../ui/orb/paintRing'
import { PAD, RING_FROM, SINK_DROP, SOLID_MARKS, badgeAt, formOfFrame, markStrokes, ringWidth, strokeOutline, strokePoints, washArcs } from '../ui/buddy/buddyRing'
import { FAMILIES, FAMILY_STATES, TRAY_SIZES, TRAY_STATES, familyOf, poseSet, stillOf, wordsOf, type Frame, type PoseSpec, type TrayStill } from './poseSet'

export { RING_FROM }

/** The sizes the contact sheet draws every pose at, and the size it draws motion frames at. */
export const SHEET_SIZES = [18, 24, 48, 96] as const
const MOTION_SIZE = 48
/** The ink every template image is drawn in. */
const TEMPLATE_INK = 'black'

const num = (n: number): string => String(Math.round(n * 100) / 100)
const attr = (s: string): string => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')

const inkToken = (frame: Frame): string => (frame.ink === 'alive' ? '--color-orb-alive' : '--color-orb-rest')
const toneToken = (tone: PhaseTone): string => (tone === 'neutral' ? '--color-orb-rest' : `--color-${tone}`)
const colour = (token: string, template: boolean): string => (template ? TEMPLATE_INK : `var(${token})`)
/** A part drawn in strokes: no fill, round caps, in its ink. */
const stroked = (token: string, template: boolean): string => `fill: none; stroke: ${colour(token, template)}`
const filled = (token: string, template: boolean): string => `fill: ${colour(token, template)}`

/** A stroke through `pts`: a line, a curve through its control point, or a point a hair long. */
function pathOf(pts: [number, number][]): string {
  const [x0, y0] = pts[0]
  if (pts.length === 1) return `M${num(x0)} ${num(y0)}h0.01`
  if (pts.length === 2) return `M${num(x0)} ${num(y0)}L${num(pts[1][0])} ${num(pts[1][1])}`
  return `M${num(x0)} ${num(y0)}Q${num(pts[1][0])} ${num(pts[1][1])} ${num(pts[2][0])} ${num(pts[2][1])}`
}
const disc = (x: number, y: number, r: number) => `M${num(x - r)} ${num(y)}a${num(r)} ${num(r)} 0 1 0 ${num(r * 2)} 0a${num(r)} ${num(r)} 0 1 0 ${num(-r * 2)} 0Z`
const polygon = (pts: [number, number][]) => `M${pts.map(([x, y]) => `${num(x)} ${num(y)}`).join('L')}Z`

interface Orb {
  /** The clip paths the strokes are knocked out of under the badge, and a solid badge's mark cut
   *  out of it, for the document's defs. */
  defs: string
  /** The whole orb's group, in the orb's own frame (its box is 0 to `size`). */
  body: string
}

/** One frame of an orb at `size`: the whole orb with its parts. `id` names its clip paths, which a
 *  sheet holding many orbs keeps apart. */
export function drawOrb(frame: Frame, size: number, { id, template = false }: { id: string; template?: boolean }): Orb {
  // An error's strokes settle low in their own place; the badge and the wash stay.
  const settle = SINK_DROP * frame.sink * size
  const strokes = ringStrokes(formOfFrame(frame), size, 1)
    .map((s) => `<path d="M${num(s.x0)} ${num(s.y0 + settle)}Q${num(s.cx)} ${num(s.cy + settle)} ${num(s.x1)} ${num(s.y1 + settle)}" stroke-width="${num(s.width)}"/>`)
  const c = size / 2
  const pad = size * PAD
  const parts: string[] = []
  let defs = ''
  let clip = ''
  if (frame.mark && frame.badge > 0) {
    const ring = size >= RING_FROM
    const solid = !ring && SOLID_MARKS.has(frame.mark)
    const { cx, cy, radius } = badgeAt(size, 0)
    const hole = radius * 1.15 * frame.badge
    const box = `M${num(-pad)} ${num(-pad)}H${num(size + pad)}V${num(size + pad)}H${num(-pad)}Z`
    defs = `<clipPath id="${id}-knockout"><path clip-rule="evenodd" d="${box}${disc(cx, cy, hole)}"/></clipPath>`
    clip = ` clip-path="url(#${id}-knockout)"`
    const fold = frame.badge === 1 ? '' : ` transform="translate(${num(cx)} ${num(cy)}) scale(${num(frame.badge)}) translate(${num(-cx)} ${num(-cy)})" opacity="${num(frame.badge)}"`
    const marks = markStrokes(size, frame.mark, 0, ring)
    let drawn: string
    if (solid) {
      // The mark is cut out of the one disc, as holes of the strokes' outlines: a template keeps
      // it, being one colour with holes.
      const holes = marks.map((m) => polygon(strokeOutline(strokePoints(m.pts), m.width / 2))).join('')
      defs += `<clipPath id="${id}-mark"><path clip-rule="evenodd" d="${disc(cx, cy, radius)}${holes}"/></clipPath>`
      drawn = `<circle cx="${num(cx)}" cy="${num(cy)}" r="${num(radius)}" clip-path="url(#${id}-mark)" style="${filled(toneToken(frame.tone), template)}"/>`
    } else {
      const ringPart = ring ? `<circle cx="${num(cx)}" cy="${num(cy)}" r="${num(radius * 0.92)}" stroke-width="${num(ringWidth(radius))}"/>` : ''
      drawn = `<g style="${stroked(toneToken(frame.tone), template)}" stroke-linecap="round">${ringPart}${marks.map((m) => `<path d="${pathOf(m.pts)}" stroke-width="${num(m.width)}"/>`).join('')}</g>`
    }
    parts.push(`<g data-part="badge" data-mark="${frame.mark}"${solid ? ' data-solid="true"' : ''}${fold}>${drawn}</g>`)
  }
  if (frame.wash > 0.01 && !template) {
    const arcs = washArcs(size, frame.wash).map(([r, from, to, alpha, width]) => {
      const [x0, y0] = [c + r * Math.cos(from), c + r * Math.sin(from)]
      const [x1, y1] = [c + r * Math.cos(to), c + r * Math.sin(to)]
      return `<path d="M${num(x0)} ${num(y0)}A${num(r)} ${num(r)} 0 0 1 ${num(x1)} ${num(y1)}" stroke-width="${num(width)}" opacity="${num(alpha)}"/>`
    })
    parts.unshift(`<g data-part="wash" style="${stroked(toneToken(frame.tone), template)}" stroke-linecap="round">${arcs.join('')}</g>`)
  }
  const dim = frame.dim === 1 ? '' : ` opacity="${num(frame.dim)}"`
  const strokesPart = `<g data-part="strokes"${clip} style="${stroked(inkToken(frame), template)}" stroke-linecap="round"${dim}>${strokes.join('')}</g>`
  const dx = frame.sway * size
  const dy = frame.lift * size
  const transform = frame.swell === 1
    ? `translate(${num(dx)} ${num(dy)})`
    : `translate(${num(c + dx)} ${num(c + dy)}) scale(${num(frame.swell)}) translate(${num(-c)} ${num(-c)})`
  const body = `<g data-part="orb" data-pivot="${num(c)} ${num(c)}" data-orb="${num(size)}" transform="${transform}">${strokesPart}${parts.join('')}</g>`
  return { defs, body }
}

const SVG = 'xmlns="http://www.w3.org/2000/svg"'

function rootAttrs(fields: Record<string, string | number | boolean | null | undefined>): string {
  return Object.entries(fields)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}="${attr(String(v))}"`)
    .join(' ')
}

/** A pose's source: its still, or its motion frame. */
export function poseSvg(pose: PoseSpec, which: 'still' | 'motion'): string {
  const frame = which === 'motion' && pose.key ? pose.key : pose.still
  const size = pose.room
  const pad = size * PAD
  const box = size + pad * 2
  const orb = drawOrb(frame, size, { id: pose.id })
  const family = pose.id.split('-')[0]
  const head = rootAttrs({
    width: num(box), height: num(box), viewBox: `${num(-pad)} ${num(-pad)} ${num(box)} ${num(box)}`,
    role: 'img', 'aria-label': pose.words, 'data-pose': pose.id, 'data-frame': which,
    'data-moves': String(Boolean(pose.key)), 'data-group': (FAMILIES as readonly string[]).includes(family) ? 'family' : family,
    'data-form': frame.form, 'data-badge': frame.badge > 0 ? frame.mark : null, 'data-room': size,
  })
  return `<svg ${SVG} ${head}><title>${attr(pose.id)}: ${attr(pose.words)}</title><desc>${attr(`${which === 'motion' ? 'Its motion' : 'Its still for reduced motion'}. Moves: ${pose.motion}.`)}</desc>${orb.defs ? `<defs>${orb.defs}</defs>` : ''}${orb.body}</svg>\n`
}

/** Where a tray still's orb sits in its square: as large as it can be with its badge inside. */
export function trayLayout(size: number): { orb: number; dy: number } {
  const radius = 4
  const orb = (size - radius) / 0.86
  return { orb, dy: radius - orb * 0.14 }
}

/** A tray or dock still: its family's still at `size`, in colour or as a template. */
export function traySvg(still: TrayStill, size: number, template: boolean): string {
  const state = TRAY_STATES[still]
  const frame = stillOf(state)
  const stem = `tray-${still}-${size}${template ? '-template' : ''}`
  const { orb, dy } = trayLayout(size)
  const drawn = drawOrb({ ...frame, wash: template ? 0 : frame.wash }, orb, { id: stem, template })
  const head = rootAttrs({
    width: size, height: size, viewBox: `0 0 ${size} ${size}`, role: 'img', 'aria-label': wordsOf(state),
    'data-pose': stem, 'data-frame': 'still', 'data-moves': 'false', 'data-group': 'tray',
    'data-family': familyOf(state), 'data-template': String(template), 'data-form': frame.form,
    'data-badge': frame.mark, 'data-room': size,
  })
  return `<svg ${SVG} ${head}><title>${attr(stem)}: ${attr(wordsOf(state))}</title>${drawn.defs ? `<defs>${drawn.defs}</defs>` : ''}<g transform="translate(0 ${num(dy)})">${drawn.body}</g></svg>\n`
}

/** Every source, by file name: each pose's still and its motion frame, and every tray still. */
export function poseSources(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const pose of poseSet()) {
    out[`${pose.id}.svg`] = poseSvg(pose, 'still')
    if (pose.key) out[`${pose.id}.motion.svg`] = poseSvg(pose, 'motion')
  }
  for (const still of Object.keys(TRAY_STATES) as TrayStill[]) {
    for (const size of TRAY_SIZES) for (const template of [false, true]) out[`tray-${still}-${size}${template ? '-template' : ''}.svg`] = traySvg(still, size, template)
  }
  return out
}

// ── the contact sheets ──────────────────────────────────────────────────────────────────────────

/** The tokens a sheet paints with, in each mode, read from the token sheets as the browser
 *  resolves them in the default scheme. */
const SHEET_TOKENS = [
  '--color-orb-alive', '--color-orb-rest', '--color-primary', '--color-warn', '--color-info', '--color-danger',
  '--color-ok', '--color-surface', '--color-on-surface', '--color-on-surface-var',
] as const
const MODES: readonly Mode[] = ['light', 'dark']

function sheetStyle(): string {
  const block = (mode: Mode) => SHEET_TOKENS.map((t) => `${t}: ${tokenValue(t, mode)};`).join(' ')
  return `<style>.light { ${block('light')} --sheet-template: ${tokenValue('--color-on-surface', 'light')}; } .dark { ${block('dark')} --sheet-template: ${tokenValue('--color-on-surface', 'dark')}; } text { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; font-size: 12px; fill: var(--color-on-surface); } .quiet { fill: var(--color-on-surface-var); } .head { font-size: 15px; font-weight: 600; }</style>`
}

/** One sheet cell: an orb symbol drawn at its box with its words beside it. */
interface Cell {
  symbol: string
  box: number
  words: string
  /** The cell's census attributes, or none for a cell the census does not count. */
  census?: { pose: string; size: number }
  note?: string
}

const GAP = 16
const LABEL = 250
const TEXT = 150

/** A sheet: a light panel above a dark one, each with a row per pose. */
function sheet(title: string, rows: { label: string; detail: string; cells: Cell[] }[], symbols: string[], defs: string[]): string {
  const rowHeight = (r: { cells: Cell[] }) => Math.max(40, ...r.cells.map((c) => c.box)) + GAP
  const rowWidth = (r: { cells: Cell[] }) => LABEL + r.cells.reduce((w, c) => w + c.box + TEXT + GAP, 0)
  const width = Math.ceil(Math.max(...rows.map(rowWidth)) + GAP * 2)
  const panelHeight = Math.ceil(48 + rows.reduce((h, r) => h + rowHeight(r), 0) + GAP)
  const panel = (mode: Mode, top: number): string => {
    let y = 48
    const body = rows.map((r) => {
      const h = rowHeight(r)
      let x = GAP + LABEL
      const cells = r.cells.map((c) => {
        const cy = (h - GAP) / 2
        const census = c.census ? ` data-sheet-pose="${c.census.pose}" data-mode="${mode}" data-size="${c.census.size}" data-words="${attr(c.words)}"` : ''
        const out = `<g${census} transform="translate(${num(x)} ${num(cy - c.box / 2)})"><use href="#${c.symbol}" width="${num(c.box)}" height="${num(c.box)}"/><text x="${num(c.box + 6)}" y="${num(c.box / 2 + 4)}">${attr(c.words)}</text>${c.note ? `<text class="quiet" x="${num(c.box + 6)}" y="${num(c.box / 2 + 19)}">${attr(c.note)}</text>` : ''}</g>`
        x += c.box + TEXT + GAP
        return out
      })
      const row = `<g transform="translate(0 ${num(y)})"><text x="${GAP}" y="${num((h - GAP) / 2)}">${attr(r.label)}</text><text class="quiet" x="${GAP}" y="${num((h - GAP) / 2 + 15)}">${attr(r.detail)}</text>${cells.join('')}</g>`
      y += h
      return row
    })
    return `<g class="${mode}" transform="translate(0 ${top})"><rect width="${width}" height="${panelHeight}" style="fill: var(--color-surface)"/><text class="head" x="${GAP}" y="30">${attr(title)}: ${mode}</text>${body.join('')}</g>`
  }
  const height = panelHeight * MODES.length
  return `<svg ${SVG} width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${attr(title)}">${sheetStyle()}<defs>${defs.join('')}${symbols.join('')}</defs>${MODES.map((m, i) => panel(m, i * panelHeight)).join('')}</svg>\n`
}

/** An orb as a sheet symbol, drawn at `size` with the pad its badge needs. */
function symbolOf(id: string, frame: Frame, size: number, template = false): { symbol: string; defs: string; box: number } {
  const pad = size * PAD
  const box = size + pad * 2
  const orb = drawOrb(frame, size, { id, template })
  const body = template ? orb.body.replaceAll(`: ${TEMPLATE_INK}`, ': var(--sheet-template)') : orb.body
  return { symbol: `<symbol id="${id}" viewBox="${num(-pad)} ${num(-pad)} ${num(box)} ${num(box)}">${body}</symbol>`, defs: orb.defs, box }
}

function poseSheet(title: string, poses: PoseSpec[]): string {
  const symbols: string[] = []
  const defs: string[] = []
  const rows = poses.map((pose) => {
    const cells: Cell[] = SHEET_SIZES.map((size) => {
      const id = `${pose.id}-${size}`
      const s = symbolOf(id, pose.still, size)
      symbols.push(s.symbol)
      defs.push(s.defs)
      return { symbol: id, box: s.box, words: pose.words, census: { pose: pose.id, size }, note: `${size} px` }
    })
    if (pose.key) {
      const id = `${pose.id}-motion`
      const s = symbolOf(id, pose.key, MOTION_SIZE)
      symbols.push(s.symbol)
      defs.push(s.defs)
      cells.push({ symbol: id, box: s.box, words: pose.words, note: `moving, ${MOTION_SIZE} px` })
    }
    return { label: pose.id, detail: pose.motion, cells }
  })
  return sheet(title, rows, symbols, defs)
}

function traySheet(): string {
  const symbols: string[] = []
  const defs: string[] = []
  const rows = (Object.keys(TRAY_STATES) as TrayStill[]).map((still) => {
    const state = TRAY_STATES[still]
    const cells: Cell[] = []
    for (const template of [false, true]) {
      for (const size of TRAY_SIZES) {
        const stem = `tray-${still}-${size}${template ? '-template' : ''}`
        const { orb, dy } = trayLayout(size)
        const frame = { ...stillOf(state), wash: template ? 0 : stillOf(state).wash }
        const drawn = drawOrb(frame, orb, { id: stem, template })
        const body = template ? drawn.body.replaceAll(`: ${TEMPLATE_INK}`, ': var(--sheet-template)') : drawn.body
        symbols.push(`<symbol id="${stem}" viewBox="0 0 ${size} ${size}"><g transform="translate(0 ${num(dy)})">${body}</g></symbol>`)
        defs.push(drawn.defs)
        cells.push({ symbol: stem, box: size, words: wordsOf(state), census: { pose: stem, size }, note: `${size} px${template ? ', template' : ''}` })
      }
    }
    const zoom = `tray-${still}-18`
    cells.push({ symbol: zoom, box: 18 * 4, words: wordsOf(state), note: '18 px, four times' })
    cells.push({ symbol: `${zoom}-template`, box: 18 * 4, words: wordsOf(state), note: '18 px template, four times' })
    return { label: `tray-${still}`, detail: `${familyOf(state)}: ${state}`, cells }
  })
  return sheet('The tray and the dock', rows, symbols, defs)
}

/** Every contact sheet, by file name: the four families, the focused card's orb, presence, the
 *  answers, and the tray's and the dock's stills. */
export function contactSheets(): Record<string, string> {
  const poses = poseSet()
  const group = (prefix: string) => poses.filter((p) => p.id.startsWith(prefix))
  const families = poses.filter((p) => FAMILIES.some((f) => FAMILY_STATES[f].some((s) => p.id === `${f}-${s}`)))
  return {
    'families.svg': poseSheet('The four families', families),
    'card.svg': poseSheet("The focused card's orb", group('card-')),
    'presence.svg': poseSheet('Presence', group('presence-')),
    'answers.svg': poseSheet('The answers', group('answer-')),
    'tray.svg': traySheet(),
  }
}
