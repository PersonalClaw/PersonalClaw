// @module-tag tree-scan
/** The buddy's words (`docs/design/buddy/copy.md`, #3899): one line for every state on every surface
 *  that shows lines, never longer than that surface allows, in one voice, with the approval words
 *  the app's own prompts say.
 *
 *  The table of states and the table of limits are read off copy.md, so the module and the design
 *  cannot part ways; the approval words are compared with what the app's two prompts render for one
 *  registry row; every check reads only the text, never a pose, a colour or an animation. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createElement } from 'react'
import { cleanup, render, screen, within } from '@testing-library/react'
import { join } from 'node:path'
import { readSource } from '../../test/sourceTree'
import type { PendingApproval } from '../api'
import { clipWords } from '../clipWords'
import { invalidateKeys } from '../data'
import { fmtElapsed } from '../duration'
import { clockTime } from '../epoch'
import { ApprovalCard } from '../../pages/chat/ApprovalCard'
import { approvalSegmentOf } from '../../pages/chat/approvalSegment'
import { CompanionPage } from '../../pages/companion/CompanionPage'
import {
  BUDDY_LINES, BUDDY_SURFACES, NAME_FLOOR, SURFACE_LIMITS, approvalAnswers, fitLine, fitParts, lineFor,
  type BuddyState, type BuddySurface, type LinePart,
} from './copy'
import { waitingApproval, waitingList } from '../../test/waiting'

// The phone's prompt is rendered for real, so its page's reads are stubbed as its own test does: the
// waiting list answers with the fixture row as its approval, and the sections under it with nothing.
vi.mock('../useChatSocket', () => ({ useChatSocket: () => {} }))
const approvals = vi.fn()
vi.mock('../api', async (orig) => {
  const real = await orig<typeof import('../api')>()
  return {
    ...real,
    api: {
      ...real.api,
      approvals: () => approvals(),
      waiting: async () => waitingList(...((await approvals()) as Parameters<typeof waitingApproval>[0][]).map((a) => waitingApproval(a))),
      resolveApproval: () => Promise.resolve(),
      uLoops: () => Promise.resolve([]),
      tasks: () => Promise.resolve({ tasks: [], total: 0 }),
      inboxOpen: () => Promise.resolve([]),
      notifications: () => Promise.resolve({ notifications: [], unread: 0 }),
    },
  }
})

const COPY_MD = readSource(join(process.cwd(), '..', 'docs', 'design', 'buddy', 'copy.md'))

/** The rows of the table under copy.md's `## <heading>`, header row left out. */
function docTable(heading: string): string[][] {
  const lines = COPY_MD.split('\n')
  const start = lines.indexOf(`## ${heading}`)
  expect(start, `copy.md has a "${heading}" section`).toBeGreaterThan(-1)
  const rows: string[][] = []
  for (const line of lines.slice(start + 1)) {
    if (line.startsWith('## ')) break
    if (!line.startsWith('|') || /^\|[\s|:-]+\|$/.test(line)) continue
    rows.push(line.slice(1, -1).split('|').map((cell) => cell.trim()))
  }
  return rows.slice(1)
}

/** copy.md's states, by the template that says each. */
const DOC_STATES: Record<string, BuddyState> = {
  'queued': 'queued',
  'thinking (no output yet)': 'thinking',
  'searching': 'searching',
  'tool': 'tool',
  'planning': 'planning',
  'delegating': 'delegating',
  'streaming': 'streaming',
  'listening': 'listening',
  'approval': 'approval',
  'question': 'question',
  'error': 'error',
  'done': 'done',
  'several done': 'severalDone',
  'return summary': 'returnSummary',
  'idle (opened by hand)': 'idle',
  'restarting': 'restarting',
  'offline': 'offline',
  'recovery (connected again after offline)': 'recovery',
  'answered elsewhere': 'answeredElsewhere',
  'approval expired before an answer': 'expired',
  'quiet hours, alert': 'quietAlert',
}

/** copy.md's surfaces, by the name each reads its limit by. */
const DOC_SURFACES: Record<string, BuddySurface> = {
  'Top-edge compact': 'topEdgeCompact',
  'Floating bubble': 'floatingBubble',
  'Expanded card line': 'expandedCard',
  'In-sight row': 'inSightRow',
  'Tray menu row': 'trayMenuRow',
  'Tray tooltip': 'trayTooltip',
  'OS notification body': 'notificationBody',
  'Phone, all-work view': 'phoneAllWork',
}

/** The surfaces with the least room. */
const COMPACT: BuddySurface[] = ['topEdgeCompact', 'floatingBubble', 'inSightRow', 'phoneAllWork']

// ── One registry row, the fixture both the buddy and the app's prompts read ──────────────────────
const DENY_EFFECT = 'Demo CLI offers no way to skip only this step: Deny ends its turn, and PersonalClaw then asks it to carry on without it.'
const ROW: PendingApproval = {
  id: 'ap-1', request_id: 'ap-1', source: 'cron', tool: 'Bash',
  tool_input: 'rm -rf /tmp/scratch', tool_purpose: 'Clear the scratch directory',
  session: 'cron:nightly', ts: Math.round(Date.now() / 1000) - 90, confirms: [],
  session_title: '', agent: '', risk: '', grant_agent: '', source_label: 'trigger “Nightly sweep”',
  deny_effect: DENY_EFFECT,
}

const QUESTION = 'Which branch should the release notes cover, main or the release branch?'
const REASON = 'The model provider refused the request after three retries'
const CHORD = 'CommandOrControl+Shift+Space'
const SINCE = 1_790_000_000

/** A work name exactly *n* characters long. */
function workName(n: number): string {
  let name = ''
  while (name.length < n) name += 'Release notes for the October launch '
  name = name.slice(0, n)
  return name.endsWith(' ') ? `${name.slice(0, -1)}s` : name
}

/** Every state's line for one piece of work, its details as the gateway and the registry give them. */
function lines(work: string): Record<BuddyState, readonly LinePart[]> {
  return {
    queued: BUDDY_LINES.queued(work),
    thinking: BUDDY_LINES.thinking(work),
    searching: BUDDY_LINES.searching(work, 'your knowledge'),
    tool: BUDDY_LINES.tool(work, 'Running the tests'),
    planning: BUDDY_LINES.planning(work),
    delegating: BUDDY_LINES.delegating(work, 3),
    streaming: BUDDY_LINES.streaming(work),
    listening: BUDDY_LINES.listening(CHORD),
    approval: BUDDY_LINES.approval(work, ROW),
    question: BUDDY_LINES.question(work, QUESTION),
    error: BUDDY_LINES.error(work, REASON),
    done: BUDDY_LINES.done(work, 185),
    severalDone: BUDDY_LINES.severalDone(4),
    returnSummary: BUDDY_LINES.returnSummary(12, 3),
    idle: BUDDY_LINES.idle(),
    restarting: BUDDY_LINES.restarting(),
    offline: BUDDY_LINES.offline(SINCE),
    recovery: BUDDY_LINES.recovery(),
    answeredElsewhere: BUDDY_LINES.answeredElsewhere(),
    expired: BUDDY_LINES.expired(work),
    quietAlert: BUDDY_LINES.quietAlert(BUDDY_LINES.approval(work, ROW)),
  }
}

const whole = (parts: readonly LinePart[]): string => fitLine(parts, Infinity)
const texts = (parts: readonly LinePart[], role: LinePart['role']): string[] =>
  parts.filter((p) => p.role === role).map((p) => p.text)

/** The template's own words lengthened by 40%, as a translation might lengthen them: every word
 *  gains four letters in ten, rounded up. The work's name, the details and the values are the
 *  app's data, and stay as they are. */
function pseudoLocalized(parts: readonly LinePart[]): LinePart[] {
  return parts.map((p) => (p.role === 'words'
    ? { ...p, text: p.text.replace(/[\p{L}']+/gu, (w) => w + 'x'.repeat(Math.ceil(w.length * 0.4))) }
    : p))
}

/** What a cut line must still be: within the limit, its state words and values whole, and the
 *  work's name the first thing given up. */
function expectFitted(parts: readonly LinePart[], limit: number, what: string) {
  const out = fitParts(parts, limit)
  const line = fitLine(parts, limit)
  expect(line, what).toBe(out.map((p) => p.text).join(''))
  expect(line.length, `${what}: ${JSON.stringify(line)}`).toBeLessThanOrEqual(limit)
  // The state is never cut: the words and values come out whole, the optional ones whole or not at all.
  for (const role of ['words', 'value'] as const) {
    const kept = parts.filter((p) => p.role === role && !p.optional).map((p) => p.text)
    const all = texts(parts, role)
    expect([all, kept], `${what}: the ${role} stay whole`).toContainEqual(texts(out, role))
  }
  // A cut name is the start of the name with an ellipsis, never nothing at all.
  const name = parts.find((p) => p.role === 'name')
  const shown = out.find((p) => p.role === 'name')
  if (name && name.text && shown && shown.text !== name.text) {
    expect(shown.text.endsWith('…'), `${what}: the name is cut with an ellipsis`).toBe(true)
    expect(name.text.startsWith(shown.text.slice(0, -1)), `${what}: the name is cut from its end`).toBe(true)
  }
  // The name is cut first: a detail is cut, or an optional part dropped, only when the name cut to
  // its floor would not have made the whole line fit.
  const detailCut = texts(out, 'detail').join('|') !== texts(parts, 'detail').join('|')
  const dropped = out.length < parts.length
  if (name && (detailCut || dropped)) {
    const nameAtFloor = parts.map((p) => (p.role === 'name' ? { ...p, text: clipWords(p.text, NAME_FLOOR) } : p))
    expect(whole(nameAtFloor).length, `${what}: the name went first`).toBeGreaterThan(limit)
  }
  return line
}

const NOT_THE_VOICE: ReadonlyArray<[string, RegExp]> = [
  ['"I"', /\bI\b/],
  ['an exclamation mark', /!/],
  ['an emoji', /\p{Extended_Pictographic}/u],
  ['an apology', /\b(sorry|apologi[sz]e)/i],
]

describe('one line for every state in copy.md', () => {
  const rows = docTable('One line per state')

  it('has a template for every state in the table, and none it does not name', () => {
    expect(rows.map(([state]) => state).sort()).toEqual(Object.keys(DOC_STATES).sort())
    expect(Object.values(DOC_STATES).sort()).toEqual(Object.keys(BUDDY_LINES).sort())
  })

  it('says the line the table writes for each state', () => {
    const work = 'Release notes'
    const said = lines(work)
    for (const [state, template] of rows) {
      const key = DOC_STATES[state]
      const written = /`([^`]+)`/.exec(template)
      if (!written) {
        // The quiet-hours alert is the alert's own line, shown only when expanded.
        expect(whole(said[key])).toBe(whole(said.approval))
        continue
      }
      const pattern = written[1]
        .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        .split('<work>').join(work)
        .replace(/<[^>]+>/g, '(.+)')
      expect(whole(said[key]), state).toMatch(new RegExp(`^${pattern}$`))
    }
  })

  it('returns a line for every state on every surface that shows lines', () => {
    for (const surface of BUDDY_SURFACES) {
      for (const [state, parts] of Object.entries(lines('Release notes'))) {
        expect(lineFor(surface, parts), `${state} on ${surface}`).not.toBe('')
      }
    }
  })

  it('fills the details the way the app shows them', () => {
    expect(whole(BUDDY_LINES.done('Release notes', 185))).toBe(`Release notes is done in ${fmtElapsed(185)}`)
    expect(fmtElapsed(185)).toBe('3m 5s')
    expect(whole(BUDDY_LINES.offline(SINCE))).toBe(`Can't reach PersonalClaw since ${clockTime(SINCE)}`)
    expect(whole(BUDDY_LINES.listening(CHORD))).toBe('Listening. Press ⌘⇧Space to stop')
    // As state-machine.md says it, a count of one in the singular.
    expect(whole(BUDDY_LINES.returnSummary(2, 1))).toBe('While you were away: 2 done, 1 needs you')
    expect(whole(BUDDY_LINES.delegating('Release notes', 1))).toBe('Release notes: handing off to 1 helper')
    expect(whole(BUDDY_LINES.approval('Release notes', ROW))).toBe('Release notes needs your OK: Bash')
  })

  it('says what it can when a detail is missing, and never leaves a dangling word', () => {
    // Until the server names the scope, a search says only that it searches (#3899, question 4).
    expect(whole(BUDDY_LINES.searching('Release notes', ''))).toBe('Release notes: searching')
    expect(whole(BUDDY_LINES.done('Release notes', 0))).toBe('Release notes is done')
    expect(whole(BUDDY_LINES.done('Release notes', undefined))).toBe('Release notes is done')
    expect(whole(BUDDY_LINES.offline(undefined))).toBe("Can't reach PersonalClaw")
    expect(whole(BUDDY_LINES.listening(''))).toBe('Listening')
    expect(whole(BUDDY_LINES.error('Release notes', ''))).toBe('Release notes stopped')
    expect(whole(BUDDY_LINES.tool('Release notes', ''))).toBe('Release notes: working')
  })
})

describe('the surface table', () => {
  it('holds copy.md\'s limits, each under its surface\'s name', () => {
    const rows = docTable('Maximum length per surface')
    expect(rows.map(([surface, chars]) => [DOC_SURFACES[surface], Number.parseInt(chars, 10)]))
      .toEqual(BUDDY_SURFACES.map((s) => [s, SURFACE_LIMITS[s]]))
    expect(BUDDY_SURFACES.map((s) => SURFACE_LIMITS[s])).toEqual([40, 48, 72, 48, 56, 80, 120, 48])
  })

  it('gives each surface the line cut to its own limit, read by name', () => {
    const parts = BUDDY_LINES.question(workName(200), QUESTION)
    for (const surface of BUDDY_SURFACES) expect(lineFor(surface, parts)).toBe(fitLine(parts, SURFACE_LIMITS[surface]))
  })
})

describe('fitLine', () => {
  it('keeps a line that fits whole, on one line', () => {
    expect(fitLine(BUDDY_LINES.queued('Release notes'), 40)).toBe('Release notes: waiting to start')
    expect(fitLine(BUDDY_LINES.queued('Release\n  notes'), 40)).toBe('Release notes: waiting to start')
  })

  it('cuts the name at a word with an ellipsis, and keeps the state whole', () => {
    expect(fitLine(BUDDY_LINES.queued('Release notes for the October launch'), 40)).toBe('Release notes for…: waiting to start')
  })

  it('cuts the name to its floor before the detail, and the detail only as far as it must', () => {
    expect(fitLine(BUDDY_LINES.question('Release notes for the October launch', QUESTION), 72))
      .toBe('Release… asks: Which branch should the release notes cover, main or…')
  })

  it.each([1, 40, 200])('fits every line on every surface for a %i-character work name', (n) => {
    const work = workName(n)
    expect(work).toHaveLength(n)
    for (const surface of BUDDY_SURFACES) {
      for (const [state, parts] of Object.entries(lines(work))) {
        const line = expectFitted(parts, SURFACE_LIMITS[surface], `${state} on ${surface}`)
        // Unlengthened, a name is never cut down to its ellipsis alone.
        const shown = fitParts(parts, SURFACE_LIMITS[surface]).find((p) => p.role === 'name')
        if (shown) expect(shown.text, `${state} on ${surface}: ${line}`).not.toBe('…')
      }
    }
  })

  // A lone `%` before a space is printed as it is; vitest's `%%` would take the case's number.
  it.each([1, 40, 200])('still fits every surface with the state whole when the words run 40% longer (%i-character name)', (n) => {
    expect(expect.getState().currentTestName).toContain(`40% longer (${n}-character name)`)
    for (const surface of BUDDY_SURFACES) {
      for (const [state, parts] of Object.entries(lines(workName(n)))) {
        expectFitted(pseudoLocalized(parts), SURFACE_LIMITS[surface], `pseudo-localized ${state} on ${surface}`)
      }
    }
  })

  it('says the same line on every surface with room for it (presence never changes the words)', () => {
    for (const [state, parts] of Object.entries(lines(workName(40)))) {
      const all = whole(parts)
      for (const surface of BUDDY_SURFACES) {
        if (all.length <= SURFACE_LIMITS[surface]) expect(lineFor(surface, parts), `${state} on ${surface}`).toBe(all)
      }
    }
  })

  it.each(COMPACT)('says the offline, expired-approval and recovery lines on %s', (surface) => {
    const limit = SURFACE_LIMITS[surface]
    for (const n of [1, 40, 200]) {
      expect(lineFor(surface, BUDDY_LINES.offline(SINCE))).toMatch(/^Can't reach PersonalClaw( since .+)?$/)
      const expired = expectFitted(BUDDY_LINES.expired(workName(n)), limit, `expired on ${surface}`)
      expect(expired.endsWith(': the request expired')).toBe(true)
      expect(lineFor(surface, BUDDY_LINES.recovery())).toBe('Connected again')
    }
  })
})

describe('one voice', () => {
  it('never says "I", never exclaims, apologises or uses an emoji, in any template on any surface', () => {
    for (const n of [1, 40, 200]) {
      for (const [state, parts] of Object.entries(lines(workName(n)))) {
        for (const surface of BUDDY_SURFACES) {
          const line = lineFor(surface, parts)
          for (const [what, pattern] of NOT_THE_VOICE) expect(line, `${state} on ${surface} has ${what}`).not.toMatch(pattern)
        }
      }
    }
  })
})

describe('what a screen reader announces', () => {
  afterEach(cleanup)

  it('is the visible line, on every surface', () => {
    for (const surface of BUDDY_SURFACES) {
      const shown = Object.values(lines(workName(200))).map((parts) => lineFor(surface, parts))
      render(createElement('ul', null, shown.map((line, i) => createElement('li', { key: i }, createElement('button', { type: 'button' }, line)))))
      for (const line of shown) {
        // Nothing a reader skips, joins or reorders: no zero-width, soft-hyphen or direction marks.
        expect(line).not.toMatch(/\p{Cf}/u)
        const named = screen.getAllByRole('button', { name: line })
        expect(named.map((b) => b.textContent)).toContain(line)
      }
      cleanup()
    }
  })
})

describe('the approval words are the app\'s own', () => {
  beforeEach(() => {
    approvals.mockReset()
    invalidateKeys('companion:', true)
    for (const k of ['loops-companion', 'tasks-companion', 'inbox-companion', 'notifications-companion']) invalidateKeys(k)
    sessionStorage.clear()
  })
  afterEach(cleanup)

  /** Each answer control of a rendered prompt, as the owner sees it and as it is announced. */
  function controls(group: HTMLElement) {
    return within(group).getAllByRole('button').map((b) => ({ label: b.textContent?.trim(), name: b.getAttribute('aria-label') }))
  }
  const asShown = (words: ReturnType<typeof approvalAnswers>) =>
    [words.allow, words.deny].map(({ label, name }) => ({ label, name }))

  it('matches the phone\'s prompt for the same registry row: labels, names and what a Deny does', async () => {
    approvals.mockResolvedValue([ROW])
    render(createElement(CompanionPage, { sub: '', navigate: vi.fn(), navEpoch: 0, query: {}, setQuery: vi.fn() }))
    const group = await screen.findByRole('group', { name: 'Permission needed to run Bash' })
    const words = approvalAnswers(ROW)
    expect(controls(group)).toEqual(asShown(words))
    expect(within(group).getByText(words.denyEffect).tagName).toBe('P')
  })

  it('matches the chat card answering the same row once: labels, names and what a Deny does', () => {
    render(createElement(ApprovalCard, { seg: approvalSegmentOf(ROW), answers: 'once', onAct: () => {} }))
    const group = screen.getByRole('group', { name: 'Permission needed to run Bash' })
    const words = approvalAnswers(ROW)
    expect(controls(group)).toEqual(asShown(words))
    expect(within(group).getByText(words.denyEffect).tagName).toBe('P')
  })

  it('argues for neither answer, as the chat card does: Allow is not the primary, Deny keeps its danger edge', () => {
    render(createElement(ApprovalCard, { seg: approvalSegmentOf(ROW), answers: 'once', onAct: () => {} }))
    const group = screen.getByRole('group', { name: 'Permission needed to run Bash' })
    const words = approvalAnswers(ROW)
    expect([words.allow.tone, words.deny.tone]).toEqual(['neutral', 'danger'])
    const allow = within(group).getByRole('button', { name: words.allow.name }).style.background
    expect(allow).toContain('--color-surface-high')
    expect(allow).not.toContain('--color-primary')
    expect(within(group).getByRole('button', { name: words.deny.name }).style.background).toContain('--color-danger')
  })

  it('reads Allow and Deny, named for the tool, and says the settled expired and recovery lines', () => {
    const words = approvalAnswers({ ...ROW, deny_effect: '' })
    expect([words.allow.label, words.deny.label]).toEqual(['Allow', 'Deny'])
    expect([words.allow.name, words.deny.name]).toEqual(['Allow Bash', 'Deny Bash'])
    expect(words.denyEffect).toBe('')
    expect(approvalAnswers(ROW).deny.name).toBe(`Deny Bash. ${DENY_EFFECT}`)
    expect(whole(BUDDY_LINES.expired('Release notes'))).toBe('Release notes: the request expired')
    expect(whole(BUDDY_LINES.recovery())).toBe('Connected again')
  })
})
