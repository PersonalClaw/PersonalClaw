// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { stillOrbPng, stillTokens } from './paintOrb'

// ── An alert's still orb, as the PNG a native banner shows (#3897) ───────────────────────────
//
// The banner's icon is the alert kind's still: waiting with the "!" in the warn tone for an
// approval, waiting with the "?" in the info tone for a question, blocked with the danger mark for
// an error. It is painted by the buddy orb's own painter, once, into a canvas of its own.

afterEach(() => { vi.restoreAllMocks() })

describe('the still’s tones', () => {
  it('an approval is warn, a question info, an error danger', () => {
    expect(stillTokens('approval').tone).toBe('--color-warn')
    expect(stillTokens('input').tone).toBe('--color-info')
    expect(stillTokens('error').tone).toBe('--color-danger')
  })
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
