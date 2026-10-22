// ── The Card tab's fixtures (#3893) ─────────────────────────────────────────────────────────────
//
// The expanded card in every state it must be seen in before the window ships: each alert kind,
// five pieces of work in sight, the focused work just finished, and the running list's own states
// (nothing else running, a large list, reconnecting, could not be read, interrupted and just
// finished). The playground's Card tab draws each one, and the browser recording and the UI quality
// check take the card from there. Made up, like the rest of the playground: nothing is read. The
// approval's choices are the tab's to give, as they are any host's.

import type { PendingApproval, WorkKind } from '../../lib/api'
import type { Alert, AlertKind } from '../../lib/buddy/inputs'
import { lineOf } from '../../lib/buddy/lines'
import type { Truth } from '../../lib/buddy/machine'
import { EMPTY_WINDOW, foldSteps, type StepEvent, type StepWindow } from '../../lib/buddy/steps'
import type { WorkPhase } from '../../lib/workPhase'
import type { BuddyCard, CardWork } from '../../ui/BuddyCard'

type Fixture = Omit<Parameters<typeof BuddyCard>[0], 'onAnswer'>

const SINCE = '2026-10-21T09:42:00Z'

const work = (id: string, kind: WorkKind, name: string, phase: WorkPhase, line = ''): CardWork => ({
  id, kind, name, phase, line, startedAt: Number(id.replace(/\D/g, '')), since: SINCE, route: `#/buddy/playground?tab=card&open=${id}`,
})

const run = (...events: StepEvent[]): StepWindow => events.reduce(foldSteps, EMPTY_WINDOW)

const FOCUS = work('w1', 'chat', 'Release notes', 'searching', 'changelog style')
const STEPS = run(
  ...['Read the last release', 'List the merged changes', 'Group them by area'].flatMap((label, i) => [
    { type: 'begin', step: { id: `s${i}`, kind: i ? 'tool' : 'plan', label } },
    { type: 'finish', id: `s${i}` },
  ] as StepEvent[]),
  { type: 'begin', step: { id: 's3', kind: 'tool', label: 'Searching: changelog style' } },
  { type: 'next', step: { id: 's4', kind: 'result', label: 'Write the notes' } },
)

const IN_SIGHT: readonly CardWork[] = [
  work('w2', 'loop', 'Nightly digest', 'tool', 'Reading the inbox'),
  work('w3', 'workflow', 'Weekly report', 'planning'),
  work('w4', 'subagent', 'Check the links', 'thinking'),
  work('w5', 'chat', 'Trip to Lisbon', 'streaming'),
  work('w6', 'trigger', 'Tidy downloads', 'queued'),
]

const APPROVAL: PendingApproval = {
  id: 'ap-1', request_id: 'ap-1', source: 'chat', tool: 'Bash', tool_input: 'git push origin release-notes',
  tool_purpose: 'Publish the release notes branch', session: 'chat:release-notes', ts: Math.round(Date.parse(SINCE) / 1000),
  session_title: 'Release notes', agent: '', risk: '', grant_agent: '', source_label: 'chat “Release notes”', confirms: [],
  deny_effect: 'Deny ends this turn.',
}

/** A truth's line on the card, in the buddy's own words (`lib/buddy/copy.ts`). */
const said = (t: Truth) => lineOf(t, 'expandedCard')
const alertOn = (kind: AlertKind, brief: string): Truth => {
  const alert: Alert = { id: `a-${kind}`, kind, workId: FOCUS.id, work: FOCUS.name, brief, route: '', consequence: 2, raisedAt: 0 }
  return { kind: 'alert', alert }
}

const base: Fixture = {
  focus: FOCUS, line: said({ kind: 'phase', phase: FOCUS.phase, work: FOCUS }), steps: STEPS, alert: null, inSight: IN_SIGHT.slice(0, 2),
  openHref: '#/mission-control',
}

export const CARD_FIXTURES: readonly { key: string; label: string; card: Fixture }[] = [
  {
    key: 'approval', label: 'An approval, two more waiting',
    card: { ...base, focus: { ...FOCUS, phase: 'approval', line: 'Bash' }, line: said(alertOn('approval', 'Bash')), state: 'approval', alert: { kind: 'approval', row: APPROVAL, choices: [] }, waiting: 2 },
  },
  {
    key: 'question', label: 'A question',
    card: {
      ...base, focus: { ...FOCUS, phase: 'input', line: 'Which branch should I use?' }, line: said(alertOn('input', 'Which branch should I use?')), state: 'input',
      alert: {
        kind: 'input',
        question: {
          kind: 'question', id: 'q1', toolCallId: '', askedBy: 'Release notes', answerable: true,
          questions: [{ question: 'Which branch should I use?', header: 'Branch', multiSelect: false, free_text: true, options: [{ label: 'main', description: 'The release branch' }, { label: 'next', description: 'The next minor' }] }],
        },
      },
    },
  },
  {
    key: 'error', label: 'An error',
    card: { ...base, focus: { ...FOCUS, phase: 'error', line: 'The tests failed' }, line: said(alertOn('error', 'The tests failed')), state: 'error', alert: { kind: 'error', work: 'Release notes', reason: 'The tests failed', route: '#/buddy/playground?tab=card&open=w1' } },
  },
  { key: 'five', label: 'Five pieces of work in sight', card: { ...base, inSight: IN_SIGHT } },
  {
    key: 'finished', label: 'The focused work finished',
    card: { ...base, focus: { ...FOCUS, phase: 'done' }, line: said({ kind: 'done', work: FOCUS, count: 1, at: FOCUS.startedAt + 120_000 }), inSight: IN_SIGHT },
  },
  { key: 'empty', label: 'Nothing else running', card: { ...base, inSight: [] } },
  {
    key: 'large', label: 'Many pieces of work',
    card: { ...base, inSight: Array.from({ length: 14 }, (_, i) => work(`w${i + 10}`, (['chat', 'loop', 'workflow', 'subagent'] as const)[i % 4], `Batch item ${i + 1}`, 'tool', 'Reading a page')) },
  },
  { key: 'reconnecting', label: 'Reconnecting', card: { ...base, list: 'reconnecting' } },
  { key: 'failed', label: 'The list could not be read', card: { ...base, inSight: [], list: 'failed' } },
  {
    key: 'ended', label: 'Interrupted and just finished, about to fold',
    card: {
      ...base, countdown: 10,
      interrupted: [work('w7', 'loop', 'Inbox sweep', 'tool', 'Reading the inbox')],
      finished: [work('w8', 'workflow', 'Photo backup', 'done'), work('w9', 'chat', 'Draft the invite', 'error', 'The site refused')],
    },
  },
]
