/** The pose set (`poseSet.ts`) drawn as layered SVG sources, and the contact sheets beside them
 *  (#3830). Each source is one whole orb, its pivot at its centre, holding the parts a renderer can
 *  move: the agent orb's halftone drawing for the pose's state, its dots and short lines, the very
 *  particles the canvas paints (`ui/buddy/buddyHalftone.ts`), and the mark on its plate where the
 *  state has one. Every colour is a token, set in a style, so one source is the pose in light and in
 *  dark and in every scheme. A template still is drawn in black, which the menu bar recolours. The
 *  whole orb only moves, dims or scales by one factor.
 *
 *  `poseSources.test.ts` writes them to `docs/design/buddy/poses/` and holds the committed files to
 *  what this draws. */

import { tokenValue, type Mode } from '../design/tokenSheet'
import { PAD, svgOrb } from '../ui/buddy/buddyHalftone'
import { PLATE_SCENES } from '../ui/orb/halftone'
import type { OrbScene } from '../lib/orbState'
import { FAMILIES, FAMILY_STATES, TRAY_SIZES, TRAY_STATES, familyOf, poseSet, stillOf, wordsOf, type Frame, type PoseSpec, type TrayStill } from './poseSet'

/** The sizes the contact sheet draws every pose at, and the size it draws motion frames at. */
export const SHEET_SIZES = [18, 24, 48, 96] as const
const MOTION_SIZE = 48
/** The ink every template image is drawn in. */
const TEMPLATE_INK = 'black'

const num = (n: number): string => String(Math.round(n * 100) / 100)
const attr = (s: string): string => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')

interface Orb {
  /** Nothing the orb needs in the document's defs: kept for the sheets, which gather them. */
  defs: string
  /** The whole orb's group, in the orb's own frame (its box is 0 to `size`). */
  body: string
}

/** The plate state a frame's mark is, the heaviest of its weights that has one, or `null`. */
export function markOfFrame(frame: Frame): OrbScene | null {
  const w = frame.weights ?? { [frame.scene]: 1 }
  const plates = PLATE_SCENES.filter((s) => (w[s] ?? 0) > 0.02)
  return plates.length ? plates.reduce((a, b) => ((w[b] ?? 0) > (w[a] ?? 0) ? b : a)) : null
}

/** One frame of an orb at `size`: the whole orb with its parts. */
export function drawOrb(frame: Frame, size: number, { template = false, plate = false }: { id?: string; template?: boolean; plate?: boolean } = {}): Orb {
  const drawn = svgOrb(frame.weights ?? { [frame.scene]: 1 }, size, { template, plate })
  const c = size / 2
  const parts: string[] = []
  if (drawn.plate) parts.push(`<g data-part="plate">${drawn.plate}</g>`)
  const dim = frame.dim === 1 ? '' : ` opacity="${num(frame.dim)}"`
  parts.push(`<g data-part="dots"${dim}>${drawn.dots}</g>`)
  const mark = markOfFrame(frame)
  if (drawn.mark && mark) parts.push(`<g data-part="mark" data-mark="${mark}"${dim}>${drawn.mark}</g>`)
  const dx = frame.sway * size
  const dy = frame.lift * size
  const transform = frame.swell === 1
    ? `translate(${num(dx)} ${num(dy)})`
    : `translate(${num(c + dx)} ${num(c + dy)}) scale(${num(frame.swell)}) translate(${num(-c)} ${num(-c)})`
  return { defs: '', body: `<g data-part="orb" data-pivot="${num(c)} ${num(c)}" data-orb="${num(size)}" data-scene="${frame.scene}" transform="${transform}">${parts.join('')}</g>` }
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
    'data-scene': frame.scene, 'data-mark': markOfFrame(frame), 'data-room': size,
  })
  return `<svg ${SVG} ${head}><title>${attr(pose.id)}: ${attr(pose.words)}</title><desc>${attr(`${which === 'motion' ? 'Its motion' : 'Its still for reduced motion'}. Moves: ${pose.motion}.`)}</desc>${orb.defs ? `<defs>${orb.defs}</defs>` : ''}${orb.body}</svg>\n`
}

/** Where a tray still's orb sits in its square: the whole square, its plate state's mark inside. */
export function trayLayout(size: number): { orb: number; dy: number } {
  return { orb: size, dy: 0 }
}

/** A tray or dock still: its family's still at `size`, in colour or as a template. */
export function traySvg(still: TrayStill, size: number, template: boolean): string {
  const state = TRAY_STATES[still]
  const frame = stillOf(state)
  const stem = `tray-${still}-${size}${template ? '-template' : ''}`
  const { orb, dy } = trayLayout(size)
  const drawn = drawOrb(frame, orb, { id: stem, template })
  const head = rootAttrs({
    width: size, height: size, viewBox: `0 0 ${size} ${size}`, role: 'img', 'aria-label': wordsOf(state),
    'data-pose': stem, 'data-frame': 'still', 'data-moves': 'false', 'data-group': 'tray',
    'data-family': familyOf(state), 'data-template': String(template), 'data-scene': frame.scene,
    'data-mark': markOfFrame(frame), 'data-room': size,
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
  '--color-ok', '--color-surface', '--color-on-surface', '--color-on-surface-var', '--color-outline-variant',
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
        const drawn = drawOrb(stillOf(state), orb, { id: stem, template })
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
