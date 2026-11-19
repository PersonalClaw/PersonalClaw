// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stillOrbPng } from './stillOrbPng'

// ── An alert's still orb, as the PNG a native banner shows (#3897) ───────────────────────────
//
// The banner's icon is the alert kind's still: needs your OK with its "!" in the warn tone for an
// approval, asks you with its "?" in the info tone for a question, the error mark in the danger
// tone for an error. It is painted by the buddy orb's own painter, once, into a canvas of its own.

beforeEach(() => {
  // The page's tokens, as the app's sheet sets them: a still paints only in resolved inks.
  document.documentElement.style.setProperty('--color-orb-alive', '#3366cc')
  document.documentElement.style.setProperty('--color-orb-rest', '#8a8f98')
})
afterEach(() => {
  vi.restoreAllMocks()
  document.documentElement.style.removeProperty('--color-orb-alive')
  document.documentElement.style.removeProperty('--color-orb-rest')
})

describe('stillOrbPng', () => {
  it('is "" where nothing can paint, so the banner shows its line alone', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    expect(stillOrbPng('approval')).toBe('')
  })

  it('paints the still once and hands back its PNG', () => {
    const calls: string[] = []
    // A recording 2D context: every method records its name and does nothing.
    const ctx = new Proxy({}, {
      get: (_t, key) => (typeof key === 'string' && !['globalAlpha', 'globalCompositeOperation', 'fillStyle', 'strokeStyle', 'lineWidth', 'canvas'].includes(key)
        ? () => { calls.push(key); return key === 'createRadialGradient' || key === 'createLinearGradient' ? { addColorStop: () => {} } : undefined }
        : undefined),
      set: () => true,
    })
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D)
    const toDataURL = vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockReturnValue('data:image/png;base64,AAAA')
    expect(stillOrbPng('approval', 96)).toBe('data:image/png;base64,AAAA')
    expect(toDataURL).toHaveBeenCalledWith('image/png')
    expect(calls).toContain('arc')
    expect(calls.filter((c) => c === 'clearRect')).toHaveLength(1)
  })

  it('a canvas that refuses to export is "" too', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(new Proxy({}, { get: () => () => undefined, set: () => true }) as unknown as CanvasRenderingContext2D)
    vi.spyOn(HTMLCanvasElement.prototype, 'toDataURL').mockImplementation(() => { throw new Error('tainted') })
    expect(stillOrbPng('error')).toBe('')
  })
})
