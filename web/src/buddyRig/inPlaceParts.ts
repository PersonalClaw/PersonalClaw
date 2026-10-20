/** The parts the in-place candidate (#3887) draws with the agent orb's dots, in the alert's tone:
 *  the wash rising from the orb's lower edge, and the badge on its upper edge, a ring of dots with
 *  its mark inside. Pure geometry in the orb's own frame (its box is 0 to `size`), so the candidate
 *  paints these parts and the pose sources (#3830, `poseSources.ts`) draw the same ones. */

import type { Dot } from 'thinking-orbs/engine'
import type { BadgeMark } from './poses'

/** Every mark the badge draws: the rig's five, and the microphone and the offline mark the pose
 *  set adds for listening and offline (`docs/design/buddy/character.md`, "The poses"). */
export type Mark = BadgeMark | 'listening' | 'offline'

/** The badge's marks as dots: rows of a small matrix, 1 where a dot sits. */
export const GLYPHS: Record<Mark, readonly string[]> = {
  working: ['10101'],
  approval: ['010', '010', '010', '000', '010'],
  input: ['110', '001', '010', '000', '010'],
  error: ['101', '010', '101'],
  done: ['00001', '00010', '10100', '01000'],
  listening: ['01110', '01110', '10101', '01110', '00100'],
  offline: ['00001', '00010', '00100', '01000', '10000'],
}

/** How far the canvas reaches past the orb on each side, as a share of its size: room for the
 *  badge on the upper edge. */
export const PAD = 0.2

export const dot = (x: number, y: number, r: number, a: number): Dot => ({ x, y, z: 0, r, white: 0, a })

/** The wash: a soft field of dots rising from the orb's lower edge, strongest at the edge. */
export function washDots(size: number, strength: number, dy: number): Dot[] {
  const out: Dot[] = []
  const c = size / 2
  const step = size / 14
  const r = Math.max(0.35, size * 0.016)
  for (let y = c; y <= size; y += step) {
    const half = Math.sqrt(Math.max(0, c * c - (y - c) * (y - c)))
    const rise = (y - c) / c
    for (let x = c - half; x <= c + half; x += step) out.push(dot(x, y + dy, r, strength * 0.55 * rise ** 1.4))
  }
  return out
}

/** Where the badge sits on the orb's upper edge, and how large it is. */
export function badgeAt(size: number, dy = 0): { cx: number; cy: number; radius: number } {
  return { cx: size * 0.86, cy: size * 0.14 + dy, radius: Math.max(4, size * 0.17) }
}

/** The badge: a ring of dots on the orb's upper edge with its mark inside, drawn with the dots.
 *  Without its `ring` the mark fills the badge, as the pose sources draw it below 24 px, where a
 *  ring of dots this small reads as a blur. */
export function badgeDots(size: number, mark: Mark, dy: number, ring = true): { cx: number; cy: number; radius: number; dots: Dot[] } {
  const { cx, cy, radius } = badgeAt(size, dy)
  const dots: Dot[] = []
  if (ring) {
    const count = 14
    const r = Math.max(0.35, radius * 0.09)
    for (let i = 0; i < count; i++) {
      const a = (i / count) * 2 * Math.PI
      dots.push(dot(cx + Math.cos(a) * radius * 0.92, cy + Math.sin(a) * radius * 0.92, r, 1))
    }
  }
  const rows = GLYPHS[mark]
  const cols = rows[0].length
  const cell = (radius * (ring ? 1.15 : 1.5)) / Math.max(rows.length, cols)
  rows.forEach((row, j) => [...row].forEach((on, i) => {
    if (on !== '1') return
    dots.push(dot(cx + (i - (cols - 1) / 2) * cell, cy + (j - (rows.length - 1) / 2) * cell, Math.max(0.35, cell * (ring ? 0.4 : 0.42)), 1))
  }))
  return { cx, cy, radius, dots }
}
