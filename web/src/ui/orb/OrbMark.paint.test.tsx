/**
 * The orb's 12 px still mark paints its state's rest form in its tone (#6117), and nothing else:
 * once, with no frame asked for. The mark alone, the sidebar's rows and a project page's rows draw
 * it here; Home's agent rows are painted in `AgentDay.test.tsx`. The paint is recorded and compared with the painter's own paint of the form, so a mark that
 * painted the wrong form, painted nothing or ran the loop would fail here.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { OrbMark } from './OrbMark'
import { restForm } from './clawRing'
import { paintRing } from './paintRing'
import { ORB_STATE_OF_PIP } from '../../lib/orbState'
import { ThreadRail } from '../ThreadRail'
import { ProjectThreadRow } from '../../pages/projects/ProjectThreads'
import type { Thread, ThreadPip } from '../../lib/useThreads'

type Entry = (string | number | boolean | undefined)[]
function recorder(log: Entry[]): CanvasRenderingContext2D {
  const state: Record<string, unknown> = { fillStyle: '', strokeStyle: '', lineWidth: 1, lineCap: 'butt', lineJoin: 'miter', globalAlpha: 1 }
  return new Proxy(state, {
    get(target, key: string) {
      if (key in target) return target[key]
      if (key === 'getImageData') return () => ({ data: [0, 0, 0, 0] })
      return (...args: (string | number | boolean)[]) => { log.push([key, ...args]) }
    },
    set(target, key: string, value) { target[key] = value; log.push(['=', key, value]); return true },
  }) as unknown as CanvasRenderingContext2D
}

const logs = new WeakMap<HTMLCanvasElement, Entry[]>()
const ORIGINAL = { getContext: HTMLCanvasElement.prototype.getContext, raf: window.requestAnimationFrame }
let asked = 0
const TONES: Record<string, string> = {
  '--color-ok': '#2e9e5b', '--color-state-waiting': '#e08a1e', '--color-state-quiet': '#8a8f98', '--color-state-blocked': '#d0453a',
  '--color-state-active': '#3366cc',
}
const hex = (h: string) => { const n = parseInt(h.slice(1), 16); return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 } }

beforeEach(() => {
  asked = 0
  HTMLCanvasElement.prototype.getContext = function getContext(this: HTMLCanvasElement) {
    let log = logs.get(this)
    if (!log) { log = []; logs.set(this, log) }
    return recorder(log)
  } as never
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: () => { asked += 1; return asked } })
  for (const [k, v] of Object.entries(TONES)) document.documentElement.style.setProperty(k, v)
})
afterEach(() => {
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  for (const k of Object.keys(TONES)) document.documentElement.style.removeProperty(k)
})

/** Every mark's last paint, after a second with motion allowed. */
async function marksOf(ui: ReactElement): Promise<{ el: HTMLElement; last: Entry[] }[]> {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const { container } = render(ui)
  await act(async () => { vi.advanceTimersByTime(1000) })
  vi.useRealTimers()
  return [...container.querySelectorAll<HTMLElement>('[data-orb-mark]')].map((el) => {
    const log = logs.get(el.querySelector('canvas')!) ?? []
    const starts = log.flatMap((e, i) => (e[0] === 'setTransform' ? [i] : []))
    return { el, last: starts.length ? log.slice(starts[starts.length - 1]) : [] }
  })
}
function want(state: Parameters<typeof restForm>[0], token: string): Entry[] {
  const out: Entry[] = []
  paintRing(recorder(out), restForm(state), { size: 12, dpr: 1, ink: hex(TONES[token]), scale: 1 })
  return out
}
const thread = (key: string, pip: ThreadPip | null): Thread => ({
  key, kind: 'chat', id: key, title: `Chat ${key}`, projectId: 'p1', route: `chat/${key}`,
  pip, pinned: false, activeAt: 0, folderId: '', tagIds: [],
})
const PIPS = Object.keys(ORB_STATE_OF_PIP) as ThreadPip[]

describe('the still mark paints its state in its tone, once', () => {
  it.each([
    ['working', '--color-ok'], ['waiting', '--color-state-waiting'], ['done', '--color-state-quiet'], ['ready', '--color-state-blocked'],
  ] as const)('%s in %s', async (state, tone) => {
    const [mark] = await marksOf(<OrbMark state={state} tone={tone} />)
    expect(mark.last.filter((e) => e[0] === 'stroke'), 'the control: it painted').toHaveLength(6)
    expect(mark.last).toEqual(want(state, tone))
    expect(asked, 'a still mark asked for a frame').toBe(0)
  })

  it('the sidebar’s rows paint each pip’s state in the rail’s colours', async () => {
    const rail = <ThreadRail rows={PIPS.map((p) => thread(p, p))} selectedKey="" status="ready" onOpen={() => {}} onRetry={() => {}} />
    const marks = await marksOf(rail)
    expect(marks).toHaveLength(PIPS.length)
    const RAIL: Record<ThreadPip, string> = { running: '--color-ok', waiting: '--color-state-waiting', unread: '--color-state-quiet', failed: '--color-state-blocked' }
    PIPS.forEach((pip, i) => expect(marks[i].last, pip).toEqual(want(ORB_STATE_OF_PIP[pip].state, RAIL[pip])))
    expect(asked).toBe(0)
  })

  it('a project page’s rows paint each pip’s state in its family’s shared token', async () => {
    const marks = await marksOf(<ul>{PIPS.map((p) => <ProjectThreadRow key={p} thread={thread(p, p)} onOpen={() => {}} />)}</ul>)
    const FAMILY: Record<ThreadPip, string> = { running: '--color-state-active', waiting: '--color-state-waiting', unread: '--color-state-quiet', failed: '--color-state-blocked' }
    expect(marks).toHaveLength(PIPS.length)
    PIPS.forEach((pip, i) => expect(marks[i].last, pip).toEqual(want(ORB_STATE_OF_PIP[pip].state, FAMILY[pip])))
    expect(asked).toBe(0)
  })
})
