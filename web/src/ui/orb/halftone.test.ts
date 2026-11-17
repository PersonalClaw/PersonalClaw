/**
 * The halftone orb's drawing (`docs/design/agent-orb.md`, "The form"): every state places all of the
 * sphere's 272 dots, at every size, in both modes, still and moving and mid-change, so a change of
 * state only ever moves dots; each state's still frame is its own, so under reduced motion and in a
 * list every state is told apart; a change moves each dot in small steps; and done swirls only on an
 * arrival that may bloom.
 */
import { describe, expect, it } from 'vitest'
import { ORB_SCENES, type OrbScene } from '../../lib/orbState'
import { DOTS, PLATE_SCENES, drawFrame, particles, word, type Clock, type Inks, type Particle, type Pen } from './halftone'
import { SCENES } from './scenes'

const rgb = (hex: string) => { const n = parseInt(hex.slice(1), 16); return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 } }
const LIGHT: Inks = {
  alive: rgb('#b12e18'), rest: rgb('#5f6368'), ok: rgb('#076e37'), warn: rgb('#954c19'), info: rgb('#1059bc'), danger: rgb('#a62621'),
  plate: rgb('#ffffff'), hair: rgb('#e1e3e1'), dark: false,
}
const DARK: Inks = {
  alive: rgb('#ff6b5b'), rest: rgb('#c4c7c5'), ok: rgb('#0ebc5f'), warn: rgb('#ff8d41'), info: rgb('#629efe'), danger: rgb('#ff8179'),
  plate: rgb('#131314'), hair: rgb('#333537'), dark: true,
}
const STILL: Clock = { t: 0.9, since: 1e6, still: true }
const MOVING: Clock[] = [{ t: 20.37, since: 0.63, still: false }, { t: 23.91, since: 2.31, still: false }, { t: 27.2, since: 5.7, still: false }]

const finite = (P: Particle[]) => P.every((q) => q.length === 9 && q.every(Number.isFinite))
/** The particles as drawn: those visible, rounded so two frames compare by what shows. */
const shown = (P: Particle[]) => P.map((q) => (q[3] < 0.03 ? 'hidden' : q.map((v) => v.toFixed(3)).join(',')))

describe('every state places all 272 dots', () => {
  it.each(ORB_SCENES.map((s) => [s]))('%s, at every size and in both modes, still and moving', (scene) => {
    for (const inks of [LIGHT, DARK]) for (const small of [false, true]) for (const clock of [STILL, ...MOVING]) {
      const P = particles({ [scene]: 1 }, clock, inks, small, SCENES)
      expect(P).toHaveLength(DOTS)
      expect(finite(P), `${scene} ${small ? 'small' : 'hero'} at ${clock.t}`).toBe(true)
      // At 20 px and below a plate state gives its dots up to the mark, which is drawn after them.
      if (!(small && (PLATE_SCENES as readonly OrbScene[]).includes(scene))) expect(P.some((q) => q[3] >= 0.03), `${scene} shows something`).toBe(true)
    }
  })

  it('and mid-change between any two states', () => {
    for (const a of ORB_SCENES) for (const b of ORB_SCENES) {
      if (a === b) continue
      const P = particles({ [a]: 0.45, [b]: 0.6 }, MOVING[0], LIGHT, false, SCENES)
      expect(P, `${a} → ${b}`).toHaveLength(DOTS)
      expect(finite(P), `${a} → ${b}`).toBe(true)
    }
  })
})

describe('each state is told apart by its still frame', () => {
  it('at 64 and 20 px every state\'s still differs from every other\'s', () => {
    for (const small of [false, true]) {
      const frames = new Map(ORB_SCENES.map((s) => [s, shown(particles({ [s]: 1 }, STILL, LIGHT, small, SCENES)).join('|')]))
      for (const a of ORB_SCENES) for (const b of ORB_SCENES) {
        if (a >= b) continue
        // Two plate states share the sphere's place and differ by the mark on their plate, drawn after the dots.
        const plates = (PLATE_SCENES as readonly OrbScene[]).includes(a) && (PLATE_SCENES as readonly OrbScene[]).includes(b)
        if (!plates) expect(frames.get(a), `${a} and ${b} at ${small ? 20 : 64} px`).not.toBe(frames.get(b))
      }
    }
  })

  it('the five plate states each draw a mark of their own', () => {
    const marks = new Map<OrbScene, string>()
    for (const s of PLATE_SCENES) {
      const calls: string[] = []
      const ctx = new Proxy({} as Record<string, unknown>, {
        get: (t, k: string) => (k in t ? t[k] : (...args: unknown[]) => { calls.push(`${k}(${args.map((a) => (typeof a === 'number' ? a.toFixed(2) : String(a))).join(',')})`) }),
        set: (t, k: string, v) => { t[k] = v; calls.push(`${k}=${String(v)}`); return true },
      }) as unknown as CanvasRenderingContext2D
      drawFrame(ctx, { [s]: 1 }, STILL, LIGHT, SCENES, { size: 64 })
      marks.set(s, calls.slice(calls.lastIndexOf('restore()')).join(';'))
    }
    expect(new Set(marks.values()).size).toBe(PLATE_SCENES.length)
  })
})

describe('a change lands exactly, and its burst stays on the canvas', () => {
  it.each([['ready', 'planning'], ['running', 'approval'], ['thinking', 'editing'], ['searching', 'error'], ['recording', 'speaking'], ['transcribing', 'delegating']] as [OrbScene, OrbScene][])(
    '%s → %s', (a, b) => {
      expect(shown(particles({ [a]: 1, [b]: 0 }, STILL, LIGHT, false, SCENES))).toEqual(shown(particles({ [a]: 1 }, STILL, LIGHT, false, SCENES)))
      expect(shown(particles({ [a]: 0, [b]: 1 }, STILL, LIGHT, false, SCENES))).toEqual(shown(particles({ [b]: 1 }, STILL, LIGHT, false, SCENES)))
      // The canvas reaches 0.16 of the orb's size past its box on each side: 1.5 of the sphere's
      // radius from the centre. Every dot that shows stays inside it, the burst at its peak included.
      let far = 0
      for (let k = 0; k <= 1.0001; k += 0.05) {
        for (const q of particles({ [a]: 1 - k, [b]: k }, MOVING[1], LIGHT, false, SCENES)) {
          if (q[3] >= 0.03) far = Math.max(far, Math.abs(q[0]) + Math.abs(q[4]), Math.abs(q[1]) + Math.abs(q[5]))
        }
      }
      expect(far).toBeLessThan(1.5)
    },
  )
})

describe('done swirls only on an arrival that may bloom', () => {
  const record = () => {
    const calls: string[] = []
    const ctx = new Proxy({} as Record<string, unknown>, {
      get: (t, k: string) => (k in t ? t[k] : (...args: unknown[]) => { calls.push(`${k}(${args.map((a) => (typeof a === 'number' ? a.toFixed(3) : String(a))).join(',')})`) }),
      set: (t, k: string, v) => { t[k] = v; calls.push(`${k}=${String(v)}`); return true },
    }) as unknown as CanvasRenderingContext2D
    return { ctx, calls }
  }
  const drawn = (clock: Clock, bloom: boolean) => { const r = record(); drawFrame(r.ctx, { done: 1 }, clock, LIGHT, SCENES, { size: 64, bloom }); return r.calls.join(';') }
  it('a completed turn\'s done is mid-swirl a third of a second in, and settled after', () => {
    const settled = drawn(STILL, true)
    expect(drawn({ t: 5.3, since: 0.3, still: false }, true)).not.toBe(settled)
    expect(drawn({ t: 7, since: 2, still: false }, true)).toBe(drawn({ t: 7, since: 1e6, still: false }, true))
  })
  it('a stopped or declined turn\'s done is settled from its first frame', () => {
    expect(drawn({ t: 5.3, since: 0.3, still: false }, false)).toBe(drawn({ t: 5.3, since: 1e6, still: false }, false))
  })
})

describe('words', () => {
  it('once written, a word is one clean dash; its other dots wait hidden where they were', () => {
    const out: (Particle | null)[] = new Array(DOTS).fill(null)
    let cur = 0
    const pen = {
      dot: (x: number, y: number, r: number, a = 1, _c = LIGHT.alive, sx = 0, sy = 0) => { out[cur++] = [x, y, r, a, sx, sy, 0, 0, 0] },
    } as unknown as Pen
    word(pen, 0, 0.4, 0, 1, LIGHT.alive, 1, 0.02, 4)
    const used = out.slice(0, cur) as Particle[]
    expect(used).toHaveLength(4)
    expect(used.filter((q) => q[3] > 0)).toHaveLength(1)
    expect(used[0][4]).toBeCloseTo(0.2)
  })
})
