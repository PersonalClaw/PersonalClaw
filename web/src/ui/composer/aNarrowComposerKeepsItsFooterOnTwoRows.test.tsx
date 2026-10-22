import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import type { ComposerData } from './types'

// ── A phone's composer keeps its footer on two rows (#3808) ──────────────────────────────────────
//
// At 390 px the footer wrapped one control per row, with `+` alone on the first, so the composer
// grew to 316 px against #3804's 276. On a phone the footer is two rows: `+` and the
// agent-and-model pill on the first, beside the send cluster, the pill truncating its label rather
// than wrapping under `+`; the composer's other controls (the reply style, the host's task mode and
// Execution Boundary, and the Context control once there is a reading) on the second, their pills
// capped so all four share it. A host with no first
// group (the loop composer) and any wider composer keep the one wrapping row. Both layouts are one
// tree, so crossing the breakpoint remounts nothing. jsdom lays nothing out, so this pins the
// structure; `e2e/composerFooterGeometry.spec.ts` measures the rows in a browser.

vi.mock('../../lib/api', async (orig) => {
  const real = await orig<typeof import('../../lib/api')>()
  return {
    ...real,
    api: new Proxy(real.api, {
      get: (target, key) => (key === 'dashboardConfig'
        ? () => Promise.resolve({ send_on_enter: true })
        : (target as Record<string | symbol, unknown>)[key]),
    }),
  }
})

const { Composer } = await import('../Composer')
const { PillButton } = await import('./controls')

const DATA = {
  agents: [{ name: 'researcher' }],
  providers: [],
  models: [{ name: 'Acme:deep', model_name: 'Deep', provider: 'Acme', description: '' }],
  discovered: {},
} as unknown as ComposerData

const CHAT_CONTROLS = { agent: true, model: true, approval: false, reasoning: false, attach: true, mic: false, optimize: false }
/** The loop composer's: no `+`, no agent or model pill. */
const LOOP_CONTROLS = { agent: false, model: false, approval: false, reasoning: false, attach: false, mic: false, optimize: false }

/** `matchMedia('(max-width: 768px)')` (`useIsMobile`) answering `mobile`, which `flip` changes
 *  as a window crossing the breakpoint would, telling every listener. */
const viewport = { mobile: false, listeners: new Set<() => void>() }
function setViewport(mobile: boolean) {
  viewport.mobile = mobile
  vi.stubGlobal('matchMedia', (q: string) => ({
    get matches() { return /max-width:\s*768px/.test(q) ? viewport.mobile : false },
    media: q, onchange: null,
    // Only the breakpoint's own listeners hear a flip (the editor listens for print, too).
    addEventListener: (_: string, l: () => void) => { if (/max-width:\s*768px/.test(q)) viewport.listeners.add(l) },
    removeEventListener: (_: string, l: () => void) => { viewport.listeners.delete(l) },
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }))
}
function flip(mobile: boolean) {
  act(() => { viewport.mobile = mobile; for (const l of viewport.listeners) l() })
}

function composer(controls = CHAT_CONTROLS) {
  render(
    <Composer value="" onChange={() => {}} onSend={() => {}} onAttach={() => {}} onOpenPrompts={() => {}} controls={controls} data={DATA}
      selection={{ agent: 'researcher', model: 'Acme:deep', approval: 'normal', taskMode: 'agent', reasoning: '' }}
      replyStyle={{ choice: '', effective: 'default', source: 'default', agentDefault: 'default', styles: [{ id: 'default', title: 'Default', description: '' }], onSelect: () => {} }}
      hostPills={<>
        <PillButton icon={null} label="Agent" dimension="Task mode" open={false} toggle={() => {}} />
        <PillButton icon={null} label="Normal" dimension="Execution boundary" open={false} toggle={() => {}} />
      </>} />,
  )
}

const footer = () => document.querySelector('[data-composer-footer]') as HTMLElement
const button = (name: string | RegExp) => screen.getByRole('button', { name })
/** The footer row a control sits in, by the row's mark: `lead`, `rest`, or `actions`. */
function rowOf(name: string | RegExp): string | null {
  const el = button(name)
  if (el.closest('[data-composer-actions]')) return 'actions'
  return el.closest('[data-composer-row]')?.getAttribute('data-composer-row') ?? null
}
const isFlex = (el: Element) => /(^|\s)(inline-)?flex(\s|$)/.test(el.className)

beforeEach(() => { localStorage.clear(); viewport.listeners.clear() })
afterEach(() => { vi.unstubAllGlobals() })

describe('on a phone', () => {
  beforeEach(() => setViewport(true))

  it('puts + and the agent-and-model pill in the first row, which keeps room for the send cluster', () => {
    composer()
    expect(footer().getAttribute('data-composer-footer')).toBe('two-rows')
    expect(rowOf('Add to message')).toBe('lead')
    expect(rowOf(/^Agent and model: /)).toBe('lead')
    expect(rowOf('Send message')).toBe('actions')
    const lead = document.querySelector('[data-composer-lead]') as HTMLElement
    expect(lead.className, 'the first group never wraps').toMatch(/\bflex-nowrap\b/)
    expect(lead.className, 'it fills the row beside the actions from a small basis').toMatch(/\bflex-1\b.*\bbasis-\[6\.5rem\]/)
  })

  it('puts every other control in the second row, a line of its own after the send cluster', () => {
    composer()
    for (const name of ['Task mode: Agent', 'Execution boundary: Normal', /^Reply style/]) expect(rowOf(name), String(name)).toBe('rest')
    const rest = document.querySelector('[data-composer-row="rest"]') as HTMLElement
    expect(rest.className).toMatch(/\border-last\b/)
    expect(rest.className).toMatch(/\bbasis-full\b/)
  })

  it('caps the second row\'s pills so three share it with the Context control', () => {
    composer()
    const rest = document.querySelector('[data-composer-row="rest"]') as HTMLElement
    for (const cap of ['[--composer-pill-max:5.25rem]', '[--composer-pill-max-wide:5.25rem]', 'max-[359px]:[--composer-pill-max:4.25rem]', 'max-[359px]:[--composer-pill-max-wide:4.25rem]']) {
      expect(rest.className.split(/\s+/), cap).toContain(cap)
    }
    expect(button('Task mode: Agent').className).toContain('max-w-[var(--composer-pill-max,160px)]')
  })

  it('lets the pill give up width to its label: every box up to the first group is a shrinkable flex item', () => {
    composer()
    const lead = document.querySelector('[data-composer-lead]') as HTMLElement
    for (let el: HTMLElement | null = button(/^Agent and model: /); el && el !== lead; el = el.parentElement) {
      const what = el.tagName.toLowerCase() + (el.getAttribute('aria-label') ? ` "${el.getAttribute('aria-label')}"` : '')
      expect(el.className, `${what} can shrink`).toMatch(/\bmin-w-0\b/)
      expect(isFlex(el.parentElement!), `${what}'s parent lays it out as a flex item`).toBe(true)
    }
  })
})

describe('wider than a phone', () => {
  beforeEach(() => setViewport(false))

  it('keeps every control in one row beside the send cluster', () => {
    composer()
    expect(footer().getAttribute('data-composer-footer')).toBe('one-row')
    for (const name of ['Add to message', /^Agent and model: /, 'Task mode: Agent', 'Execution boundary: Normal', /^Reply style/]) {
      expect(rowOf(name), String(name)).toBe('lead')
    }
    expect(document.querySelector('[data-composer-row="rest"]')).toBeNull()
  })
})

describe('a host with no first group (the loop composer)', () => {
  it('keeps the one row on a phone too', () => {
    setViewport(true)
    composer(LOOP_CONTROLS)
    expect(footer().getAttribute('data-composer-footer')).toBe('one-row')
    expect(rowOf('Task mode: Agent')).toBe('lead')
  })
})

describe('crossing the breakpoint', () => {
  it('keeps every control mounted, so the focus stays where it was', () => {
    setViewport(false)
    composer()
    const plus = button('Add to message')
    const task = button('Task mode: Agent')
    const send = button('Send message')
    plus.focus()
    flip(true)
    expect(footer().getAttribute('data-composer-footer')).toBe('two-rows')
    expect(button('Add to message')).toBe(plus)
    expect(button('Task mode: Agent')).toBe(task)
    expect(button('Send message')).toBe(send)
    expect(document.activeElement).toBe(plus)
    flip(false)
    expect(button('Task mode: Agent')).toBe(task)
    expect(document.activeElement).toBe(plus)
  })
})
