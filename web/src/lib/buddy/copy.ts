import type { PendingApproval } from '../api'
import { clipWords } from '../clipWords'
import { fmtElapsed } from '../duration'
import { clockTime } from '../epoch'
import { formatChord } from '../pushToTalk'
import { phaseLabel } from '../workPhase'

// ── The buddy's words (`docs/design/buddy/copy.md`, #3899) ─────────────────────────────────────
//
// One line for every state, on every surface that shows words, never longer than that surface
// allows, in one voice: the work's own name first, then what it is doing, in plain present tense;
// never "I", never an apology, no exclamation mark, no emoji. Personality belongs to the orb, and
// no line leans on a pose, a colour or an animation to be understood.
//
// A template gives its line as parts, so a surface can cut it without ever cutting the state:
//
//   · `name`   — the work's name (a chat's title, a loop's goal, a run's workflow name). Cut first,
//                at a word with an ellipsis (`clipWords`), down to `NAME_FLOOR` characters.
//   · `detail` — text the line carries from elsewhere: an approval's brief, a question, a reason, a
//                tool's own label. Cut next, the same way, down to `DETAIL_FLOOR`.
//   · `words`  — the template's own words: the state. Never cut.
//   · `value`  — a count, a time or a chord, as the app shows it. Never cut.
//
// A part marked `optional` (the time in "since <time>", the chord's hint, the return summary's
// lead) goes whole, with the others marked so, when the line cannot fit with the name and the
// details at their floors. Only then do the name and the details give up the rest of their room.
//
// A surface only cuts: the same truth gives the same line on every surface with room for it.

/** Every surface that shows a line, by the name it reads its limit by. */
export const BUDDY_SURFACES = [
  'topEdgeCompact', 'floatingBubble', 'expandedCard', 'inSightRow',
  'trayMenuRow', 'trayTooltip', 'notificationBody', 'phoneAllWork',
] as const

export type BuddySurface = (typeof BUDDY_SURFACES)[number]

/** The most characters each surface shows (copy.md, "Maximum length per surface"). The expanded
 *  card's 72 is its one line; the brief below it has its own layout. */
export const SURFACE_LIMITS: Readonly<Record<BuddySurface, number>> = {
  topEdgeCompact: 40,
  floatingBubble: 48,
  expandedCard: 72,
  inSightRow: 48,
  trayMenuRow: 56,
  trayTooltip: 80,
  notificationBody: 120,
  phoneAllWork: 48,
}

/** How short the work's name is cut before a detail gives up any of its room. */
export const NAME_FLOOR = 12
/** How short a detail is cut before the line's optional parts go. */
export const DETAIL_FLOOR = 8

export type PartRole = 'name' | 'detail' | 'words' | 'value'

export interface LinePart {
  role: PartRole
  text: string
  /** Dropped whole, with the line's other optional parts, when the line cannot fit with it. */
  optional?: true
}

export type LineParts = readonly LinePart[]

const name = (text: string): LinePart => ({ role: 'name', text })
const detail = (text: string): LinePart => ({ role: 'detail', text })
const words = (text: string): LinePart => ({ role: 'words', text })
const value = (text: string): LinePart => ({ role: 'value', text })
const optional = (part: LinePart): LinePart => ({ ...part, optional: true })

const given = (text: string | undefined): string => String(text ?? '').trim()
const count = (n: number, one: string, many: string): LinePart[] => [value(String(n)), words(n === 1 ? one : many)]

/** One template per state in copy.md's table. `work` is the work's name; the rest is what the
 *  gateway and the registry say of it. A detail that is missing leaves its words out, so a line
 *  never ends on a dangling colon or preposition. */
export const BUDDY_LINES = {
  queued: (work: string): LineParts => [name(work), words(': waiting to start')],
  thinking: (work: string): LineParts => [name(work), words(': thinking')],
  /** *where* is the scope the server's search line names ("your knowledge", "the web", "the
   *  files"); until it names one, the line says only that the work is searching. */
  searching: (work: string, where?: string): LineParts => (given(where)
    ? [name(work), words(': searching'), optional(words(' ')), optional(value(given(where)))]
    : [name(work), words(': searching')]),
  /** *label* is the tool's own label, the server's line for the call. */
  tool: (work: string, label: string): LineParts =>
    [name(work), words(': '), detail(given(label) || phaseLabel('tool').toLowerCase())],
  planning: (work: string): LineParts => [name(work), words(': planning')],
  delegating: (work: string, helpers: number): LineParts =>
    [name(work), words(': handing off to '), ...count(helpers, ' helper', ' helpers')],
  streaming: (work: string): LineParts => [name(work), words(': writing')],
  /** *chord* is the owner's bound push-to-talk chord, as stored; it is shown as `formatChord`
   *  shows it. With none bound there is nothing to press, so the line says only that it listens. */
  listening: (chord: string): LineParts => {
    const keys = formatChord(chord ?? '')
    return keys
      ? [words('Listening'), optional(words('. Press ')), optional(value(keys)), optional(words(' to stop'))]
      : [words('Listening')]
  },
  /** The brief's first line is what will run: the tool, as the app's prompt names it. */
  approval: (work: string, row: Pick<PendingApproval, 'tool'>): LineParts => (given(row.tool)
    ? [name(work), words(' needs your OK: '), detail(given(row.tool))]
    : [name(work), words(' needs your OK')]),
  question: (work: string, question: string): LineParts => (given(question)
    ? [name(work), words(' asks: '), detail(given(question))]
    : [name(work), words(' asks')]),
  error: (work: string, reason: string): LineParts => (given(reason)
    ? [name(work), words(' stopped: '), detail(given(reason))]
    : [name(work), words(' stopped')]),
  /** *tookSecs* is how long the work took, shown as `fmtElapsed` shows it; nothing measured leaves
   *  the time out. */
  done: (work: string, tookSecs: number | undefined): LineParts => {
    const took = fmtElapsed(tookSecs)
    return took
      ? [name(work), words(' is done'), optional(words(' in ')), optional(value(took))]
      : [name(work), words(' is done')]
  },
  severalDone: (finished: number): LineParts => [value(String(finished)), words(' finished')],
  returnSummary: (done: number, needYou: number): LineParts => [
    optional(words('While you were away: ')), value(String(done)), words(' done, '),
    ...count(needYou, ' needs you', ' need you'),
  ],
  idle: (): LineParts => [words('Nothing running')],
  restarting: (): LineParts => [words('PersonalClaw is restarting')],
  /** *since* is when the connection was lost, shown as `clockTime` shows it. */
  offline: (since: number | string | null | undefined): LineParts => {
    const at = clockTime(since)
    return at
      ? [words("Can't reach PersonalClaw"), optional(words(' since ')), optional(value(at))]
      : [words("Can't reach PersonalClaw")]
  },
  recovery: (): LineParts => [words('Connected again')],
  answeredElsewhere: (): LineParts => [words('Answered in PersonalClaw')],
  expired: (work: string): LineParts => [name(work), words(': the request expired')],
  /** In quiet hours an alert keeps its own line, shown only once the buddy is expanded. */
  quietAlert: (alert: LineParts): LineParts => alert,
} as const

export type BuddyState = keyof typeof BUDDY_LINES

const width = (parts: LineParts): number => parts.reduce((n, p) => n + p.text.length, 0)
const render = (parts: LineParts): string => parts.map((p) => p.text).join('')
/** Text that is not the template's own, on one line. */
const oneLine = (text: string): string => String(text ?? '').split(/\s+/).filter(Boolean).join(' ')

/** *parts* with each part of *role*, last first, cut toward *floor* until the line fits *limit*.
 *  Each cut is made from the part's whole text, so a part is never cut twice over. */
function shrink(parts: LineParts, limit: number, steps: ReadonlyArray<readonly [PartRole, number]>): LinePart[] {
  const out = parts.slice()
  for (const [role, floor] of steps) {
    for (let i = out.length - 1; i >= 0; i--) {
      const over = width(out) - limit
      if (over <= 0) return out
      if (out[i].role !== role) continue
      const target = Math.max(Math.min(floor, parts[i].text.length), out[i].text.length - over)
      if (target < out[i].text.length) out[i] = { ...out[i], text: clipWords(parts[i].text, target) }
    }
  }
  return out
}

/** *parts* as they fit *limit* characters: the name cut first, then the details, then the optional
 *  parts dropped, and the state's own words never cut. */
export function fitParts(parts: LineParts, limit: number): LinePart[] {
  const line = parts.map((p) => (p.role === 'words' ? p : { ...p, text: oneLine(p.text) }))
  if (width(line) <= limit) return line
  const floors = [['name', NAME_FLOOR], ['detail', DETAIL_FLOOR]] as const
  const withAll = shrink(line, limit, floors)
  if (width(withAll) <= limit) return withAll
  const cut = shrink(line.filter((p) => !p.optional), limit, [...floors, ['name', 1], ['detail', 1]])
  if (width(cut) <= limit) return cut
  // The state's own words alone are longer than the surface. Never longer than it allows, so the
  // line is cut as one; `copy.test.ts` holds every template to every surface, so no surface meets this.
  return [words(clipWords(render(cut), limit))]
}

/** The line *parts* say, cut to *limit* characters. */
export function fitLine(parts: LineParts, limit: number): string {
  return render(fitParts(parts, limit))
}

/** The line *parts* say on *surface*, within that surface's limit. A screen reader is given this
 *  same text: the line is cut here, never by the surface's styles, so what is announced is what
 *  is shown. */
export function lineFor(surface: BuddySurface, parts: LineParts): string {
  return fitLine(parts, SURFACE_LIMITS[surface])
}

/** One answer control on an approval alert: the visible verb, the accessible name, and its style. */
export interface BuddyAnswer {
  /** The approvals queue's action it sends (`POST /api/approvals/{id}/{action}`). */
  key: 'approve' | 'reject'
  label: string
  name: string
  /** As the chat's approval card styles them: Allow `neutral` and Deny its `danger` edge. Neither
   *  is the primary, so the buddy never argues for an answer. */
  tone: 'neutral' | 'danger'
}

/** An approval alert's controls and what its Deny does, in the app's own words: the phone's prompt
 *  and the chat card answering a call once say these strings for the same registry row, and
 *  `copy.test.ts` renders both from one row to hold them equal. Security copy is never reworded, so
 *  the buddy writes no approval words of its own. */
export function approvalAnswers(row: Pick<PendingApproval, 'tool' | 'deny_effect'>): {
  allow: BuddyAnswer
  deny: BuddyAnswer
  /** What a Deny does when it does more than decline the call, said before either verb; `''`
   *  when a Deny only declines it. */
  denyEffect: string
} {
  const denyEffect = row.deny_effect || ''
  return {
    allow: { key: 'approve', label: 'Allow', name: `Allow ${row.tool}`, tone: 'neutral' },
    deny: { key: 'reject', label: 'Deny', name: `Deny ${row.tool}${denyEffect ? `. ${denyEffect}` : ''}`, tone: 'danger' },
    denyEffect,
  }
}
