import { describe, expect, it } from 'vitest'
import type { WaitingItem } from './api'
import { ALERT_TITLE, alertBanner, alertOf, itemOfNote } from './alertBanner'
import { SURFACE_LIMITS } from './buddy/copy'
import { waitingApproval, waitingItem, waitingList } from '../test/waiting'

// ── A buddy alert's banner says what the buddy says (#3897) ───────────────────────────────────
//
// One line from the buddy's own templates, at most 120 characters with the work's name cut first
// and the state's words never cut, so a banner shown without its icon still says what changed and
// for which work. The line names what waits: the waiting item the note is a view of.

const APPROVAL = waitingApproval({
  id: 'ap-1', request_id: 'ap-1', source: '', tool: 'shell.run', session: 'c1', ts: 0,
  session_title: 'Release notes', agent: '', risk: 'caution', grant_agent: '', source_label: 'chat “Release notes”',
} as never, { route: '#/chat/c1' })

const LOOP_ASK: WaitingItem = waitingItem({
  kind: 'loop_question', id: 'loop_question:l1', work: 'loop:l1', brief: { name: 'Sort the inbox', question: 'May I archive last year’s mail?' },
  route: '#/loops/l1',
})

describe('which alert a note is', () => {
  it('reads the alert the gateway named, and only one of the three', () => {
    expect(alertOf({ alert: 'approval' })).toBe('approval')
    expect(alertOf({ alert: 'question' })).toBe('question')
    expect(alertOf({ alert: 'error' })).toBe('error')
    for (const alert of [undefined, '', 'progress', 7, 'APPROVAL']) expect(alertOf({ alert })).toBe('')
  })
})

describe('the item an alert is about', () => {
  it('an approval by the registry id its row names, and nothing else', () => {
    const list = waitingList(LOOP_ASK, APPROVAL)
    expect(itemOfNote({ approval: 'ap-1' }, list)).toBe(APPROVAL)
    expect(itemOfNote({ approval: 'ap-2' }, list)).toBeNull()
  })

  it('a question by the work it names, else the chat it is shown in', () => {
    const chatAsk = waitingItem({ kind: 'question', id: 'question:q1', session: 'c9', brief: { questions: [{ question: 'Which version?' }] } })
    const list = waitingList(LOOP_ASK, chatAsk)
    expect(itemOfNote({ loop_id: 'l1' }, list)).toBe(LOOP_ASK)
    expect(itemOfNote({ session: 'c9' }, list)).toBe(chatAsk)
    expect(itemOfNote({ session: 'elsewhere' }, list)).toBeNull()
    expect(itemOfNote({ loop_id: 'l1' }, null)).toBeNull()
  })
})

describe('the banner', () => {
  it('an approval: “<work> needs your OK: <tool>”, opening the chat it asks in', () => {
    expect(alertBanner({ approval: 'ap-1' }, 'approval', APPROVAL)).toEqual({
      title: ALERT_TITLE, body: 'Chat “Release notes” needs your OK: shell.run', route: 'chat/c1',
    })
  })

  it('a question: “<work> asks: <the question>”', () => {
    expect(alertBanner({}, 'question', LOOP_ASK)).toEqual({
      title: ALERT_TITLE, body: 'Loop “Sort the inbox” asks: May I archive last year’s mail?', route: 'loops/l1',
    })
  })

  it('an error: “<work> stopped: <the reason>”, its work named by its title', () => {
    expect(alertBanner({ title: 'Loop failed', body: 'The model did not answer\nRetry later' }, 'error', null)).toEqual({
      title: ALERT_TITLE, body: 'Loop stopped: The model did not answer', route: '',
    })
  })

  it('an item the list no longer holds is said in the note’s own words', () => {
    expect(alertBanner({ title: 'Approval needed: shell.run', source_label: 'loop “Docs”' }, 'approval', null).body)
      .toBe('Loop “Docs” needs your OK: shell.run')
  })

  it('a 200-character work name still yields at most 120 characters, the state’s words whole', () => {
    const long = 'A'.repeat(200)
    for (const [alert, words] of [['approval', ' needs your OK: '], ['question', ' asks: '], ['error', ' stopped: ']] as const) {
      const { body } = alertBanner({ source_label: long, title: 'Approval needed: shell.run', body: 'why' }, alert, null)
      expect(body.length, alert).toBeLessThanOrEqual(SURFACE_LIMITS.notificationBody)
      expect(body, alert).toContain(words)
    }
  })

  it('a route the shell would refuse is left to the note’s source', () => {
    const odd = { ...LOOP_ASK, route: '#/automations?open=t1' }
    expect(alertBanner({}, 'question', odd).route).toBe('')
  })
})
