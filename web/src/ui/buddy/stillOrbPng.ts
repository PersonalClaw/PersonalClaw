import { orbInkTokens, orbInksOf } from '../AgentOrb'
import { buddySceneOf, paintBuddy, PAD, STILL_WHOLE, type BuddyState } from './buddyHalftone'

/** *state*'s still orb as a PNG data URL, at *size* points and twice their pixels, in the page's
 *  light or dark inks: what a native banner shows as its icon (#3897). It is the buddy's own still,
 *  the halftone state for it on no plate, painted once by the buddy's painter. "" where nothing can
 *  paint (no canvas, a test's DOM, a token that resolves to nothing), and the banner shows its line
 *  alone. */
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
  const inks = orbInksOf(orbInkTokens('normal'))
  if (!inks) return ''
  const pad = Math.round(size * PAD)
  const dpr = 2
  canvas.width = (size + pad * 2) * dpr
  canvas.height = (size + pad * 2) * dpr
  paintBuddy(ctx, { [buddySceneOf(state)]: 1 }, { t: 0.9, since: 1e6, still: true }, inks, { size, pad, dpr, plate: false, whole: STILL_WHOLE })
  try {
    return canvas.toDataURL('image/png')
  } catch {
    return ''
  }
}
