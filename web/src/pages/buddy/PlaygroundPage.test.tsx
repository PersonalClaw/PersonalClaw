import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { ThemeProvider } from '../../app/theme'
import { AppearanceProvider } from '../../app/appearance'
import { EVENTS, SITUATIONS, type MatrixEvent, type Situation } from '../../lib/buddy/machine'
import { PlaygroundPage } from './PlaygroundPage'

// ── The buddy playground reaches every situation and sends every event (#3892) ─────────────────
//
// Every situation of the interruption matrix the v0.3.0 machine is ever in is entered through the
// page's controls, never by setting the machine's state, and the stage says where the machine is.
// Every event outside the matrix's later-work list is sent from a control and logged as that event.
// A file drop is the one event left out: every cell of it is later work (#3895). And using every
// control reaches nothing on the network: the answers are written in the log and never sent.

/** The clock's control that fires the next timer, whatever it is due. */
const NEXT = 'Next timer'

const ENTER: Readonly<Record<Situation, readonly string[]>> = {
  hidden: [],
  peek: ['Pointer on the top edge'],
  compact: ['Start work'],
  expanded: ['Start work', 'Click the orb'],
  alert: ['Start work', 'An approval arrives'],
  finished: ['Start work', NEXT, 'It finishes'],
  floating: ['Start work', 'Start a drag', 'Drop anywhere else'],
  sleeping: ['Start work', 'Start a drag', 'Drop anywhere else', 'The owner goes away'],
  listening: ['Start listening'],
  dragging: ['Start work', 'Start a drag'],
  muted: ['Start work', 'Hide the buddy'],
  offline: ['Start work', 'Lose the connection', '+10 s'],
}

const SEND: Readonly<Record<Exclude<MatrixEvent, 'file'>, readonly string[]>> = {
  start: ['Start work'],
  phase: ['Start work', 'Set its phase'],
  other: ['Start work', 'Start work', 'Other work moves on'],
  alert: ['Start work', 'An approval arrives'],
  answered: ['Start work', 'An approval arrives', 'Allow'],
  elsewhere: ['Start work', 'An approval arrives', 'Answered elsewhere'],
  done: ['Start work', NEXT, 'It finishes'],
  failed: ['Start work', 'It fails'],
  lost: ['Start work', 'Lose the connection'],
  back: ['Start work', 'Lose the connection', 'Connection back'],
  listen: ['Start listening'],
  unlisten: ['Start listening', 'Stop listening'],
  hover: ['Pointer on the top edge'],
  click: ['Start work', 'Click the orb'],
  hits: ['Start work', 'Quick hits on the orb'],
  away: ['Start work', 'The owner goes away'],
  return: ['Start work', 'The owner goes away', 'The owner comes back'],
  collapse: ['Start work', 'Click the orb', '+1 min'],
  drag: ['Start work', 'Start a drag'],
}

let requests: string[] = []
const OpenSocket = globalThis.WebSocket

beforeEach(() => {
  requests = []
  localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
    requests.push(String(url))
    return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
  vi.stubGlobal('WebSocket', class { constructor(url: string) { requests.push(`ws ${url}`) } })
  vi.spyOn(XMLHttpRequest.prototype, 'open').mockImplementation(function (_m: string, url: string | URL) { requests.push(`xhr ${url}`) })
  if ('sendBeacon' in navigator) vi.spyOn(navigator, 'sendBeacon').mockImplementation((url) => { requests.push(`beacon ${url}`); return true })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  globalThis.WebSocket = OpenSocket
})

async function mount(tab = 'play') {
  render(
    <ThemeProvider>
      <AppearanceProvider>
        <PlaygroundPage sub="playground" navigate={() => {}} navEpoch={0} query={{ tab }} setQuery={() => {}} />
      </AppearanceProvider>
    </ThemeProvider>,
  )
  await act(async () => { await Promise.resolve() })
}

const stage = () => document.querySelector('[data-stage]') as HTMLElement
const controls = () => screen.getByRole('group', { name: 'Clock' }).parentElement as HTMLElement

/** Uses the control named `name`, which must be on. */
function press(name: string) {
  const button = within(controls()).getAllByRole('button').find((b) => (b.textContent ?? '').trim().startsWith(name) && (name !== NEXT || b.textContent!.trim() !== ''))
  expect(button, `no control named "${name}"`).toBeTruthy()
  expect(button!.hasAttribute('disabled') || button!.getAttribute('aria-disabled') === 'true', `"${name}" is off`).toBe(false)
  fireEvent.click(button!)
}

const logged = () => [...document.querySelectorAll('[data-event-log] li')].map((li) => li.getAttribute('data-event') ?? '')

describe('every situation is entered through the controls', () => {
  it.each(SITUATIONS)('%s', async (situation) => {
    await mount()
    for (const name of ENTER[situation]) press(name)
    expect(stage().dataset.situation).toBe(situation)
  })
})

describe('every event is sent from a control', () => {
  it('names every event but the file drop', () => {
    expect(Object.keys(SEND).sort()).toEqual(EVENTS.filter((e) => e !== 'file').sort())
  })

  it.each(Object.keys(SEND) as (keyof typeof SEND)[])('%s', async (event) => {
    await mount()
    const steps = SEND[event]
    for (const name of steps.slice(0, -1)) press(name)
    const before = logged().length
    press(steps[steps.length - 1])
    expect(logged().slice(0, logged().length - before), `the last control did not send "${event}"`).toContain(event)
  })
})

describe('nothing reaches the network', () => {
  it('uses every control in every situation, and the answers stay in the log', async () => {
    await mount()
    const settled = requests.length
    for (const situation of SITUATIONS) {
      cleanup()
      await mount()
      for (const name of ENTER[situation]) press(name)
      for (const button of within(controls()).getAllByRole('button')) {
        if (!button.hasAttribute('disabled') && button.getAttribute('aria-disabled') !== 'true') fireEvent.click(button)
      }
    }
    cleanup()
    await mount()
    for (const name of ['Start work', 'An approval arrives', 'Allow']) press(name)
    expect(document.querySelector('[data-event-log]')?.textContent).toMatch(/answer, not sent: allow on \/api\/approvals\/a\d+\/answer/)
    // The page's own provider read the saved themes once per mount; every control after that read nothing.
    const fromControls = requests.slice(settled).filter((r) => !r.includes('/api/themes'))
    expect(fromControls).toEqual([])
  })

  it('a control that did reach the network would be seen: the control', async () => {
    await mount()
    const before = requests.length
    await fetch('/api/approvals/a1/answer', { method: 'POST' })
    expect(requests.length).toBe(before + 1)
  })
})

describe('the tabs', () => {
  it('the pose sheet draws every family pose and the focused card, each with its words', async () => {
    await mount('poses')
    const poses = [...document.querySelectorAll('[data-pose]')]
    expect(poses.length).toBe(15 + 4)
    for (const pose of poses) {
      for (const orb of pose.querySelectorAll('[data-buddy-orb]')) {
        expect(orb.querySelector('[data-buddy-line]')?.textContent?.trim(), pose.getAttribute('data-pose')!).toBeTruthy()
      }
    }
  })

  it('the day steps through its timetable', async () => {
    await mount('day')
    const next = screen.getByRole('button', { name: 'Next step' })
    // Release notes starts, searches, Nightly digest starts, and Release notes asks to push.
    for (let i = 0; i < 4; i++) fireEvent.click(next)
    expect(stage().dataset.situation).toBe('alert')
    fireEvent.click(next)
    expect(stage().dataset.situation, 'the owner allowed it').toBe('compact')
  })
})
