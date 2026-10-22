import { paintFrame, resolvePreset } from 'thinking-orbs/engine'
import { modeOf, poseFrame } from '../../buddyRig/arrange'
import { PAD, RING_FROM, SOLID_MARKS, badgeDots, dot, solidBadge, washDots } from '../../buddyRig/inPlaceParts'
import { tunedDrawingFor } from '../../buddyRig/poses'
import type { Frame } from '../../buddyRig/poseSet'
import { tintOf } from '../orbDots'
import { inkOf } from '../AgentOrb'
import { poseOf, stillOf, type BuddyState } from './orbPoses'

/** One frame of the orb's dots, as a drawing's `ModeFrame` returns it. */
type Drawn = Parameters<typeof paintFrame>[1]
type Tint = ReturnType<typeof tintOf>

/** What one paint of the buddy orb needs besides its dots. */
export interface OrbPaint {
  size: number
  /** The canvas's room around the orb, in points, and its pixel ratio. */
  pad: number
  dpr: number
  dark: boolean
  ink: Tint
  tone: Tint
  /** The dots' brightness, the whole orb's rise (a share of its size) and the alert's wash. */
  dim: number
  lift: number
  wash: number
  mark: Frame['mark']
}

/** Paint the buddy orb into *ctx*: its dots, the alert's wash, and the badge cut out of the dots,
 *  in the badge's tone. The one painter: `BuddyOrb` calls it every frame it draws, and
 *  `stillOrbPng` once, for a still. */
export function paintOrb(ctx: CanvasRenderingContext2D, dots: Drawn, p: OrbPaint): void {
  const box = p.size + p.pad * 2
  ctx.setTransform(p.dpr, 0, 0, p.dpr, 0, 0)
  ctx.clearRect(0, 0, box, box)
  ctx.setTransform(p.dpr, 0, 0, p.dpr, p.pad * p.dpr, p.pad * p.dpr)
  ctx.globalAlpha = p.dim
  paintFrame(ctx, dots, p.dark, p.ink)
  ctx.globalAlpha = 1
  const dy = p.lift * p.size
  if (p.wash > 0.01) paintFrame(ctx, { dots: washDots(p.size, p.wash, dy), lines: [] }, p.dark, p.tone)
  if (!p.mark) return
  const solid = p.size < RING_FROM && SOLID_MARKS.has(p.mark)
  const badge = solid ? solidBadge(p.size, p.mark, dy) : badgeDots(p.size, p.mark, dy, p.size >= RING_FROM)
  ctx.globalCompositeOperation = 'destination-out'
  ctx.beginPath()
  ctx.arc(badge.cx, badge.cy, badge.radius * 1.15, 0, Math.PI * 2)
  ctx.fill()
  ctx.globalCompositeOperation = 'source-over'
  if ('holes' in badge) {
    // A waiting badge below 24 px is one solid dot in the tone with its mark cut out of it.
    paintFrame(ctx, { dots: [dot(badge.cx, badge.cy, badge.radius, 1)], lines: [] }, p.dark, p.tone)
    ctx.globalCompositeOperation = 'destination-out'
    for (const hole of badge.holes) {
      ctx.beginPath()
      ctx.arc(hole.x, hole.y, hole.r, 0, Math.PI * 2)
      ctx.fill()
    }
    ctx.globalCompositeOperation = 'source-over'
  } else {
    paintFrame(ctx, { dots: badge.dots, lines: [] }, p.dark, p.tone)
  }
}

/** The ink token a still's dots take, and its badge's tone token, as `BuddyOrb` reads them at the
 *  platform's ordinary contrast. */
export function stillTokens(state: BuddyState): { ink: string; tone: string } {
  const f = stillOf(state)
  return {
    ink: f.ink === 'alive' ? '--color-orb-alive' : '--color-orb-rest',
    tone: f.tone === 'neutral' ? '--color-orb-rest' : `--color-${f.tone}`,
  }
}

/** *state*'s still orb as a PNG data URL, at *size* points and twice their pixels, in the page's
 *  light or dark ink: what a native banner shows as its icon (#3897). "" where nothing can paint
 *  (no canvas, a test's DOM), and the banner shows its line alone. */
export function stillOrbPng(state: BuddyState, size: 48 | 96 = 96): string {
  if (typeof document === 'undefined') return ''
  const canvas = document.createElement('canvas')
  let ctx: CanvasRenderingContext2D | null = null
  try {
    ctx = canvas.getContext('2d')
  } catch {
    return ''
  }
  if (!ctx) return ''
  const pad = Math.round(size * PAD)
  const dpr = 2
  canvas.width = (size + pad * 2) * dpr
  canvas.height = (size + pad * 2) * dpr
  const f = stillOf(state)
  const pose = poseOf(state)
  const preset = resolvePreset(f.drawing, tunedDrawingFor(size))
  const frame = poseFrame(modeOf(f.drawing), () => pose, { still: true, lift: true })
  const tokens = stillTokens(state)
  paintOrb(ctx, frame(size, 0, preset.opts), {
    size, pad, dpr,
    dark: document.documentElement.dataset.mode === 'dark',
    ink: tintOf(inkOf(tokens.ink)),
    tone: tintOf(inkOf(tokens.tone)),
    dim: f.dim, lift: pose.lift, wash: pose.wash, mark: f.mark,
  })
  try {
    return canvas.toDataURL('image/png')
  } catch {
    return ''
  }
}
