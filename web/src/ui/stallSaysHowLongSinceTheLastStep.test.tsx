import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import { SHOW_DELAY_MS, STALL_AFTER_MS, TurnStatusRow } from './TurnStatusRow'
import type { WorkPhase } from '../lib/workPhase'

// ── A turn that has gone quiet says for how long, from its last step ─────────────────────────────
//
// The gateway says a running turn's phase again every 15 s with `last_step_at`, when its runtime
// last did anything (`chat_phase`): a turn whose model is silent still says it runs. The row says
// "No new activity for 1m" once that step is a minute old, from that time, never a timer the page
// starts itself, and drops it when the turn steps again. A turn that waits on you (an approval, a
// question), or on a retry or a local model, says what it waits on instead.

const T0 = Date.UTC(2026, 9, 29, 9, 0, 0)

beforeEach(() => {
  vi.useFakeTimers({ now: T0 })
  window.matchMedia = ((q: string) => ({
    matches: false, media: q, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {}, addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
})
afterEach(() => { cleanup(); vi.useRealTimers() })

const quiet = () => document.querySelector('[data-turn-quiet]')?.textContent ?? null
const wait = async (ms: number) => { await act(async () => { vi.advanceTimersByTime(ms) }) }

function draw(phase: WorkPhase, lastStepAt: number | null) {
  const props = { running: true, line: '', startedAt: T0, usage: null, onStop: vi.fn() }
  const view = render(<TurnStatusRow {...props} phase={phase} lastStepAt={lastStepAt} />)
  return (next: number | null, nextPhase: WorkPhase = phase) =>
    view.rerender(<TurnStatusRow {...props} phase={nextPhase} lastStepAt={next} />)
}

it('says nothing for the first minute after the last step, then for how long it has been quiet', async () => {
  draw('thinking', T0)
  await wait(STALL_AFTER_MS - 2000)
  expect(quiet()).toBeNull()
  await wait(3000)
  expect(quiet()).toBe('No new activity for 1m')
  await wait(65_000)
  expect(quiet()).toBe('No new activity for 2m')
})

it('drops it when the turn steps again', async () => {
  const step = draw('tool', T0)
  await wait(STALL_AFTER_MS + 5000)
  expect(quiet()).toBe('No new activity for 1m')
  step(Date.now())
  await wait(1000)
  expect(quiet()).toBeNull()
})

it('counts from the turn’s start while it has taken no step', async () => {
  draw('thinking', null)
  await wait(STALL_AFTER_MS + 1000)
  expect(quiet()).toBe('No new activity for 1m')
})

it('says nothing while the turn waits on you, on a retry or on a local model', async () => {
  for (const phase of ['approval', 'input', 'retrying', 'waiting_model'] as const) {
    draw(phase, T0 - 10 * 60_000)
    await wait(SHOW_DELAY_MS + 1000)
    expect(quiet(), phase).toBeNull()
    cleanup()
  }
})
