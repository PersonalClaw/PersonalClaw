import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { Ban, Check } from 'lucide-react'
import type { PendingApproval, WorkKind } from '../lib/api'
import { EMPTY_WINDOW, foldSteps, type StepEvent, type StepWindow } from '../lib/buddy/steps'
import { INTERRUPTED_LINE } from '../lib/runningRows'
import type { QuestionSegment } from '../pages/chat/chatTypes'
import type { ApprovalChoice } from './ApprovalPrompt'
import { BuddyCard, CARD_LINE, IN_SIGHT_LINE, IN_SIGHT_SHOWN, type CardAlert, type CardAnswer, type CardWork } from './BuddyCard'

// ── The buddy's expanded card (#3893) ───────────────────────────────────────────────────────────

const SINCE = '2026-10-21T10:42:00Z'
const work = (id: string, kind: WorkKind, over: Partial<CardWork> = {}): CardWork => ({
  id, kind, name: `${kind} work ${id}`, phase: 'tool', line: 'Reading the inbox', startedAt: Number(id.replace(/\D/g, '')) || 1,
  since: SINCE, route: `#/${kind}/${id}`, ...over,
})
const FOCUS = work('f1', 'chat', { name: 'Release notes', phase: 'searching', line: 'changelog style' })
const steps = (...events: StepEvent[]): StepWindow => events.reduce(foldSteps, EMPTY_WINDOW)
const RUN = steps(
  { type: 'begin', step: { id: 's1', kind: 'plan', label: 'Plan the notes' } },
  { type: 'begin', step: { id: 's2', kind: 'tool', label: 'Searching: changelog style' } },
  { type: 'next', step: { id: 's3', kind: 'result', label: 'Write the notes' } },
)

const ROW: PendingApproval = {
  id: 'ap-1', request_id: 'ap-1', source: 'cron', tool: 'Bash',
  tool_input: 'git push origin main', tool_purpose: 'Publish the release notes',
  session: 'cron:nightly', ts: Math.round(Date.now() / 1000) - 90, confirms: [],
  session_title: '', agent: '', risk: '', grant_agent: '', source_label: 'loop “Release notes”',
  deny_effect: 'Deny ends this turn.', asked_for: 'Asked for by the nightly digest.',
}

let answers: CardAnswer[] = []
let requests: string[] = []
beforeEach(() => {
  answers = []
  requests = []
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => { requests.push(String(url)); return new Response('{}') }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

function card(over: Partial<Parameters<typeof BuddyCard>[0]> = {}) {
  return render(
    <BuddyCard
      focus={FOCUS} line="Release notes: searching changelog style" steps={RUN} alert={null}
      inSight={[]} openHref="#/mission-control" onAnswer={(a) => answers.push(a)} {...over}
    />,
  )
}

const root = () => document.querySelector('[data-buddy-card]') as HTMLElement
const rows = () => [...document.querySelectorAll<HTMLElement>('[data-in-sight]')]

describe('the card, top to bottom', () => {
  it('says the focused work, its exact phase, its current step, the work in sight and Open in PersonalClaw', () => {
    card({ inSight: [work('w2', 'loop')] })
    expect(screen.getByRole('group', { name: 'Release notes' })).toBe(root())
    expect(root().querySelector('[data-card-line]')?.textContent).toBe('Release notes: searching changelog style')
    expect(root().querySelector('header [data-work-phase]')?.textContent).toBe('Searching')
    expect(root().querySelector('[data-step-place="current"]')?.textContent).toContain('Searching: changelog style')
    expect(rows()).toHaveLength(1)
    expect(screen.getByRole('link', { name: 'Open in PersonalClaw' }).getAttribute('href')).toBe('#/mission-control')
    // v0.3.0 has no drop target and no ask field.
    expect(root().querySelector('input[type="file"], textarea, [data-drop-target]')).toBeNull()
  })

  it('the step window draws three lines and the trail, the current one shimmering with no progress', () => {
    const long = steps(...Array.from({ length: 10 }, (_, i) => [
      { type: 'begin', step: { id: `s${i}`, kind: 'tool', label: `Step ${i}` } },
      { type: 'finish', id: `s${i}` },
    ] as StepEvent[]).flat(), { type: 'begin', step: { id: 'now', kind: 'tool', label: 'Step now' } }, { type: 'next', step: { id: 'n', kind: 'result', label: 'Step after' } })
    card({ steps: long })
    const places = [...root().querySelectorAll('[data-step-place]')].map((l) => l.getAttribute('data-step-place'))
    expect(places).toEqual(['before', 'current', 'next'])
    expect(root().querySelectorAll('[data-step-mark]')).toHaveLength(8)
    expect(root().querySelector('[data-step-trail]')?.textContent).toContain('+2')
    expect(root().querySelector('[data-step-trail]')?.textContent).toContain('10 steps done')
    expect(root().querySelector('[data-step-place="current"] .text-shimmer')).not.toBeNull()
  })

  it('a current step with progress says how far it is instead of shimmering', () => {
    card({ steps: foldSteps(RUN, { type: 'progress', id: 's2', progress: 0.4 }) })
    const current = root().querySelector('[data-step-place="current"]')!
    expect(current.textContent).toContain('40%')
    expect(current.querySelector('.text-shimmer')).toBeNull()
  })

  it('a helper batch is a row of orbs, one per member', () => {
    card({ steps: steps({ type: 'begin', step: { id: 'h', kind: 'step', label: 'Handing off', helpers: ['thinking', 'tool', 'done'] } }) })
    expect(within(screen.getByRole('group', { name: '3 helpers' })).getAllByRole('img')).toHaveLength(3)
  })
})

describe('in sight', () => {
  it('every kind of live work shows with its kind glyph and its line', () => {
    const kinds: WorkKind[] = ['chat', 'loop', 'workflow', 'subagent', 'trigger', 'webhook']
    card({ inSight: kinds.map((k, i) => work(`w${i + 2}`, k)) })
    for (const k of kinds) {
      const row = document.querySelector(`[data-in-sight][data-kind="${k}"]`)!
      expect(row, k).not.toBeNull()
      expect(row.querySelector(`[data-kind-glyph="${k}"]`), `${k}'s glyph`).not.toBeNull()
      expect(row.querySelector('[data-work-line]')?.textContent, `${k}'s line`).toMatch(new RegExp(`^${k} work`))
    }
  })

  it('every row says its name, its exact phase, its last update and the route that opens it', () => {
    card({ inSight: [work('w2', 'workflow', { phase: 'approval', line: 'git push' })] })
    const row = rows()[0]
    const link = within(row).getByRole('link')
    expect(link.getAttribute('aria-label'), 'its name').toContain('workflow work w2')
    expect(link.getAttribute('href'), 'its route').toBe('#/workflow/w2')
    expect(row.querySelector('[data-work-phase]')?.textContent, 'its exact phase').toBe('Needs your OK')
    expect(row.querySelector('[data-work-updated]')?.textContent, 'its last update').toMatch(/updated \d/)
  })

  it('the list says nothing else runs, that it is reconnecting, or that it could not be read', () => {
    card()
    expect(root().textContent).toContain('Nothing else is running.')
    cleanup()
    card({ list: 'reconnecting', inSight: [work('w2', 'chat')] })
    expect(screen.getByRole('status').textContent).toContain('Reconnecting')
    expect(rows()).toHaveLength(1)
    cleanup()
    card({ list: 'failed' })
    expect(root().textContent).toContain('Could not read what is running.')
    expect(root().textContent).not.toContain('Nothing else is running.')
  })

  it('a large list shows the first rows and all of them on request', () => {
    const many = Array.from({ length: 14 }, (_, i) => work(`w${i + 2}`, 'loop'))
    card({ inSight: many })
    expect(rows()).toHaveLength(IN_SIGHT_SHOWN)
    fireEvent.click(screen.getByRole('button', { name: 'Show all 14' }))
    expect(rows()).toHaveLength(14)
  })

  it('just finished and interrupted work show how each ended, and an ending wins over the restart', () => {
    card({
      finished: [work('w7', 'loop', { phase: 'done' })],
      interrupted: [work('w8', 'chat'), work('w7', 'loop')],
    })
    const finished = within(screen.getByRole('list', { name: 'Just finished' }))
    const items = finished.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(items[0].textContent).toContain(INTERRUPTED_LINE)
    expect(items[0].textContent).toContain('Interrupted')
    expect(items[1].textContent).toContain('Done')
    expect(items[1].querySelector('[data-work-updated]')?.textContent).toMatch(/ended \d/)
  })
})

describe('when the focused work finishes', () => {
  it('its step window gives way to the work in sight', () => {
    const many = Array.from({ length: 10 }, (_, i) => work(`w${i + 2}`, 'chat'))
    card({ focus: { ...FOCUS, phase: 'done' }, inSight: many })
    expect(root().querySelector('[data-step-window]')).toBeNull()
    expect(root().dataset.cardDone).toBe('true')
    expect(rows().length, 'the rows take the room').toBe(10)
  })
})

describe('the alert', () => {
  // The host's choices, as a host builds them on the app's answer path: the card only renders them.
  let chosen: string[] = []
  const CHOICES: ApprovalChoice[] = [
    { key: 'allow', icon: Check, label: 'Allow', approves: true, name: 'Allow Bash', onClick: () => { chosen.push('allow') } },
    { key: 'deny', icon: Ban, label: 'Deny', tone: 'danger', name: 'Deny Bash. Deny ends this turn.', onClick: () => { chosen.push('deny') } },
  ]
  const approval: CardAlert = { kind: 'approval', row: ROW, choices: CHOICES }
  beforeEach(() => { chosen = [] })

  it("shows the app's approval prompt with the row's fields unchanged", () => {
    card({ alert: approval, waiting: 2 })
    const brief = root().querySelector('[data-alert-brief]') as HTMLElement
    for (const field of [ROW.tool, String(ROW.tool_input), ROW.tool_purpose!, ROW.source_label, ROW.deny_effect!, ROW.asked_for!]) {
      expect(brief.textContent, field).toContain(field)
    }
    expect(root().textContent).toContain('+2 waiting')
  })

  it("its controls are the host's choices, unchanged: the card adds none and answers none itself", () => {
    card({ alert: approval })
    const prompt = within(root().querySelector('[data-alert-brief]') as HTMLElement)
    // The prompt's own controls (its arguments' expander, if any) aside, the choices are exactly the host's.
    const named = prompt.getAllByRole('button').map((b) => b.getAttribute('aria-label')).filter((n) => n?.startsWith('Allow') || n?.startsWith('Deny'))
    expect(named).toEqual(['Allow Bash', 'Deny Bash. Deny ends this turn.'])
    fireEvent.click(screen.getByRole('button', { name: 'Allow Bash' }))
    expect(chosen, "the host's own onClick answered").toEqual(['allow'])
    expect(answers, 'the card raised nothing of its own').toEqual([])
    cleanup()
    card({ alert: { ...approval, choices: [] } })
    expect(screen.queryByRole('button', { name: /^(Allow|Deny)/ }), 'with no choices from the host, the card has none').toBeNull()
    expect(requests).toEqual([])
  })

  it("a question is the chat's question card, and its Send raises the reply", () => {
    const question: QuestionSegment = {
      kind: 'question', id: 'q1', toolCallId: '', askedBy: 'Release notes', answerable: true,
      questions: [{ question: 'Which branch?', header: 'Branch', multiSelect: false, free_text: false, options: [{ label: 'main', description: '' }, { label: 'next', description: '' }] }],
    }
    card({ alert: { kind: 'input', question } })
    fireEvent.click(screen.getByRole('radio', { name: /main/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Send/ }))
    expect(answers).toEqual([{ kind: 'reply', reply: { answers: [{ selected: [0], other: '' }] } }])
    expect(requests).toEqual([])
  })

  it('an error has Open and Dismiss', () => {
    card({ alert: { kind: 'error', work: 'Nightly digest', reason: 'The tests failed', route: '#/loops/l1' } })
    expect(screen.getByRole('link', { name: 'Open Nightly digest' }).getAttribute('href')).toBe('#/loops/l1')
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(answers).toEqual([{ kind: 'dismiss' }])
  })
})

describe('room and order', () => {
  const approval: CardAlert = {
    kind: 'approval', row: ROW,
    choices: [
      { key: 'allow', icon: Check, label: 'Allow', approves: true, name: 'Allow Bash', onClick: () => {} },
      { key: 'deny', icon: Ban, label: 'Deny', tone: 'danger', name: 'Deny Bash', onClick: () => {} },
    ],
  }
  const NAME = Array.from({ length: 40 }, (_, i) => `word${i}`).join(' ').slice(0, 200)

  it('no line is longer than 72 on the card or 48 in sight, with 200-character names', () => {
    expect(NAME.length).toBe(200)
    const kinds: WorkKind[] = ['chat', 'loop', 'workflow', 'subagent']
    card({
      focus: { ...FOCUS, name: NAME },
      line: 'x'.repeat(CARD_LINE),
      steps: steps({ type: 'begin', step: { id: 'l', kind: 'tool', label: NAME } }),
      inSight: kinds.map((k, i) => work(`w${i + 2}`, k, { name: NAME })),
      interrupted: [work('w9', 'chat', { name: NAME })],
      alert: { kind: 'error', work: NAME, reason: NAME, route: '' },
    })
    expect(root().querySelector('h3')!.textContent!.length).toBeLessThanOrEqual(CARD_LINE)
    for (const step of root().querySelectorAll('[data-step-place]')) {
      expect(step.textContent!.replace(/^(Done|Next): /, '').length).toBeLessThanOrEqual(CARD_LINE)
    }
    expect(root().querySelector('[data-card-alert] p')!.textContent!.length).toBeLessThanOrEqual(CARD_LINE)
    const lines = [...root().querySelectorAll('[data-work-line]')]
    expect(lines).toHaveLength(5)
    for (const l of lines) expect(l.textContent!.length, l.textContent!).toBeLessThanOrEqual(IN_SIGHT_LINE)
  })

  it('Tab walks the brief, its controls, the rows in sight, then Open in PersonalClaw', () => {
    card({ alert: approval, inSight: [work('w2', 'loop'), work('w3', 'chat')] })
    const order = [...root().querySelectorAll<HTMLElement>('a[href], button:not([disabled]), [tabindex="0"]')]
      .map((el) => el.getAttribute('aria-label') ?? el.textContent!.trim())
    const at = (name: string) => order.findIndex((n) => n.startsWith(name))
    expect(at('Tool arguments'), 'the brief: its arguments come first').toBe(0)
    expect(at('Allow Bash')).toBeGreaterThan(at('Tool arguments'))
    expect(at('Deny Bash')).toBeGreaterThan(at('Allow Bash'))
    expect(at('Open loop work w2')).toBeGreaterThan(at('Deny Bash'))
    expect(at('Open chat work w3')).toBeGreaterThan(at('Open loop work w2'))
    expect(at('Open in PersonalClaw')).toBe(order.length - 1)
    expect(root().querySelector('[tabindex]:not([tabindex="0"]):not([tabindex="-1"])'), 'no positive tabindex reorders it').toBeNull()
  })

  it('says while the pointer or the focus is on it, so a fold waits, and the countdown says so', () => {
    const held: boolean[] = []
    card({ countdown: 10, onHold: (h) => held.push(h) })
    fireEvent.pointerEnter(root())
    fireEvent.pointerLeave(root())
    screen.getByRole('link', { name: 'Open in PersonalClaw' }).focus()
    expect(held).toEqual([true, false, true])
    expect(screen.getByRole('status').textContent).toContain('Folding in 10 seconds')
  })
})
