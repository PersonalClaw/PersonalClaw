import {
  AppWindow, Braces, FileChartColumn, FileSpreadsheet, FileText, Gavel, Image, Link2, ListChecks, Music,
  PanelsTopLeft, Presentation, ScrollText, StickyNote, Table, Video,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { LibraryItem } from '../../lib/api'
import { epochSeconds } from '../../lib/epoch'

// ── What a library row says ──────────────────────────────────────────────────
//
// One vocabulary over the two stores (the Kind-Is-A-Word Rule, `web/DESIGN.md`): a row's kind is one
// neutral icon and its word, never a colour of its own. Pure data and functions, so the row, the card,
// the preview and the tests read one table.

/** The library's kinds, as the Filter's Kind section names them (`GET /api/library`'s `kind`). */
export const KINDS: readonly { key: string; label: string }[] = [
  { key: 'note', label: 'Notes' },
  { key: 'document', label: 'Documents' },
  { key: 'link', label: 'Links' },
  { key: 'media', label: 'Images and media' },
  { key: 'page', label: 'Pages and apps' },
  { key: 'report', label: 'Reports' },
]

/** Who made a thing, as the Filter's Made by section names it. "Not recorded" is offered only while
 *  older notes have no maker. */
export const MAKERS: readonly { key: string; label: string }[] = [
  { key: 'you', label: 'You' },
  { key: 'chats', label: 'Chats' },
  { key: 'automations', label: 'Automations' },
  { key: 'sources', label: 'Sources' },
  { key: 'not_recorded', label: 'Not recorded' },
]

/** The exact format a row names, and its icon: the stores' own types, in one table. */
const FORMATS: Record<string, { word: string; icon: LucideIcon }> = {
  // knowledge items
  note: { word: 'Note', icon: StickyNote },
  fleeting: { word: 'Quick note', icon: StickyNote },
  journal: { word: 'Journal', icon: ScrollText },
  gist: { word: 'Code snippet', icon: Braces },
  decision: { word: 'Decision', icon: Gavel },
  bookmark: { word: 'Link', icon: Link2 },
  image: { word: 'Image', icon: Image },
  audio: { word: 'Audio', icon: Music },
  video: { word: 'Video', icon: Video },
  pdf: { word: 'PDF', icon: FileText },
  document: { word: 'Document', icon: FileText },
  sheet: { word: 'Spreadsheet', icon: FileSpreadsheet },
  slides: { word: 'Slides', icon: Presentation },
  // artifacts
  html: { word: 'Page', icon: PanelsTopLeft },
  widget: { word: 'Widget', icon: AppWindow },
  react: { word: 'App', icon: AppWindow },
  infographic: { word: 'Infographic', icon: PanelsTopLeft },
  markdown: { word: 'Document', icon: FileText },
  svg: { word: 'Image', icon: Image },
  json: { word: 'JSON', icon: Braces },
  text: { word: 'Text', icon: FileText },
  csv: { word: 'CSV', icon: Table },
  docx: { word: 'Word', icon: FileText },
  xlsx: { word: 'Spreadsheet', icon: FileSpreadsheet },
  pptx: { word: 'Slides', icon: Presentation },
  // what automations write
  report: { word: 'Report', icon: FileChartColumn },
  findings: { word: '', icon: ListChecks },
}

/** The word a row names its format by ("PDF", "Page", "Journal"); "" for a findings log, whose title
 *  says what it is, and for a format no table knows (its kind's word is said by the Filter). */
export function formatWord(item: Pick<LibraryItem, 'format'>): string {
  return FORMATS[item.format]?.word ?? ''
}

/** The neutral icon a row's tile draws. */
export function formatIcon(item: Pick<LibraryItem, 'format' | 'kind'>): LucideIcon {
  if (FORMATS[item.format]) return FORMATS[item.format].icon
  return item.kind === 'page' ? PanelsTopLeft: item.kind === 'media' ? Image: item.kind === 'link' ? Link2: FileText
}

/** What the page knows beside the rows: each intent's findings and whether it is paused, each
 *  report's schedule and last run (the set-up's reads), and the project the list is filtered to. */
export interface LibraryContext {
  intents?: ReadonlyMap<string, { count: number; enabled: boolean; proposesSkill?: boolean }>
  reports?: ReadonlyMap<string, { schedule: string; lastRunTs: number | null; lastStatus: string }>
  /** The project the list is filtered to, whose rows carry no chip. */
  project?: string
  /** How far into a knowledge item its reader got, 0 to 100, as the reader keeps it on this device
   *  (`readingPosition.ts`); null when it was never opened. */
  readingPct?: (id: string) => number | null
}

/** The id a ref names (`report:r1` is `r1`). */
export const refId = (ref: string) => ref.slice(ref.indexOf(':') + 1)
export const refKind = (ref: string) => ref.slice(0, ref.indexOf(':'))

/** A row's state mark: its words and its short form for a phone. Never cut. */
export interface StateMark { words: string; short: string; tone: 'progress' | 'warn' | 'neutral' }

/** The row's state mark, if it has one, in the brief's order of precedence: a report's run, a reading
 *  that failed or waits, a conflict, a deleted tag, a paused intent, archived, reading. */
export function stateMarkOf(item: LibraryItem, ctx: LibraryContext = {}): StateMark | null {
  if (item.running) return { words: 'Writing now', short: 'Writing now', tone: 'progress' }
  if (refKind(item.ref) === 'report' && ctx.reports?.get(refId(item.ref))?.lastStatus === 'error') {
    return { words: 'Last run failed', short: 'Last run failed', tone: 'warn' }
  }
  if (item.missing_tags.length) {
    const reads = item.missing_tags.length > 1 || refKind(item.ref) !== 'report'
    return reads
      ? { words: 'Reads nothing', short: 'Reads nothing', tone: 'warn' }
      : { words: `No longer reads “${item.missing_tags[0]}”`, short: `No longer reads “${item.missing_tags[0]}”`, tone: 'warn' }
  }
  if (item.state === 'unreadable') return { words: "Couldn't read it", short: "Couldn't read it", tone: 'warn' }
  if (item.state === 'being_read') return { words: 'Still being read', short: 'Being read', tone: 'progress' }
  if (item.conflicts.length) return { words: 'Disagrees with an item', short: 'Disagrees', tone: 'warn' }
  if (refKind(item.ref) === 'findings' && ctx.intents?.get(refId(item.ref))?.enabled === false) {
    return { words: 'Paused', short: 'Paused', tone: 'neutral' }
  }
  if (item.archived) return { words: 'Archived', short: 'Archived', tone: 'neutral' }
  if (item.read === 'reading') {
    const pct = refKind(item.ref) === 'knowledge' ? ctx.readingPct?.(refId(item.ref)) : null
    return pct != null && pct > 0
      ? { words: `Reading · ${Math.round(pct)}%`, short: `${Math.round(pct)}%`, tone: 'progress' }
      : { words: 'Reading', short: 'Reading', tone: 'progress' }
  }
  return null
}

// ── time words ───────────────────────────────────────────────────────────────────────────────────

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()

/** "Nov 8", or "Nov 8, 2025" in another year. */
export function dayWords(ms: number, now: number = Date.now()): string {
  const d = new Date(ms)
  const year = d.getFullYear() === new Date(now).getFullYear() ? '' : `, ${d.getFullYear()}`
  return `${MONTHS[d.getMonth()]} ${d.getDate()}${year}`
}

/** "8:02", in the browser's clock, as the approved rows read a report's last run. */
export function clockWords(ms: number): string {
  const d = new Date(ms)
  return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** A row's age: "just now", "12 min ago", "2 h ago" within the day, then its day ("Nov 8"). A stamp
 *  ahead of now reads as its day, never "just now"; one that is no stamp reads "". */
export function ageWords(stamp: string | number | null | undefined, now: number = Date.now()): string {
  const secs = epochSeconds(stamp ?? undefined)
  if (secs === undefined) return ''
  const ms = secs * 1000
  const s = Math.floor((now - ms) / 1000)
  if (s < 0) return dayWords(ms, now)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (ms >= startOfDay(new Date(now))) return `${Math.floor(s / 3600)} h ago`
  return dayWords(ms, now)
}

/** The day-grain age of a stamp, as a project's Results tiles read it: "Today", "Yesterday", then
 *  its day ("Nov 8"). "" for no stamp. */
export function dayAge(stamp: string | number | null | undefined, now: number = Date.now()): string {
  const secs = epochSeconds(stamp ?? undefined)
  if (secs === undefined) return ''
  const ms = secs * 1000
  const today = startOfDay(new Date(now))
  if (ms >= today && ms - now < 86_400_000) return 'Today'
  if (ms >= today - 86_400_000 && ms < today) return 'Yesterday'
  return dayWords(ms, now)
}

/** The size word a document's row shows ("38 pages" is the store's; the read gives bytes). */
export function sizeWords(bytes: number | null): string {
  if (bytes == null || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// ── groups ────────────────────────────────────────────────────────────────────────────────

export const PINNED_SHOWN = 5
export const READING_SHOWN = 3

/** The time group a stamp falls in: Today, Yesterday, Earlier this week, Earlier this month, then the
 *  month's name in this year and the year before that. */
export function timeGroup(stamp: string, now: number = Date.now()): string {
  const secs = epochSeconds(stamp)
  if (secs === undefined) return 'Earlier'
  const ms = secs * 1000
  const today = startOfDay(new Date(now))
  if (ms >= today) return 'Today'
  if (ms >= today - 86_400_000) return 'Yesterday'
  const n = new Date(now)
  const weekStart = today - ((n.getDay() + 6) % 7) * 86_400_000
  if (ms >= weekStart) return 'Earlier this week'
  const d = new Date(ms)
  if (d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth()) return 'Earlier this month'
  if (d.getFullYear() === n.getFullYear()) return MONTH_NAMES[d.getMonth()]
  return String(d.getFullYear())
}

export interface LibraryGroup {
  /** The heading ("Pinned", "Today", "October"); "" for a list with no groups (a search, by name). */
  title: string
  rows: LibraryItem[]
  /** Rows the group holds beyond those shown, with its Show all words. */
  more?: { count: number; words: string }
}

/** The list's groups, as approved: Pinned (five, then "Show all 12 pinned"), Continue reading
 *  (three, then "Show all 7 you're reading"), then the time groups by activity, or by when added
 *  for Sort by Date added. A search ranks and does not group, nor does Sort by Name. A pinned or
 *  reading row is listed in its own group alone. `open` names the groups shown whole. */
export function groupRows(rows: readonly LibraryItem[], sort: string, opts: { searching?: boolean; open?: ReadonlySet<string>; now?: number } = {}): LibraryGroup[] {
  if (opts.searching || sort === 'name' || sort === 'rank') return rows.length ? [{ title: '', rows: [...rows] }] : []
  const now = opts.now ?? Date.now()
  const pinned = rows.filter((r) => r.pinned)
  const reading = rows.filter((r) => !r.pinned && r.read === 'reading')
  const rest = rows.filter((r) => !r.pinned && r.read !== 'reading')
  const out: LibraryGroup[] = []
  const shelf = (title: string, list: LibraryItem[], shown: number, words: (n: number) => string) => {
    if (!list.length) return
    const whole = opts.open?.has(title) || list.length <= shown
    out.push({ title, rows: whole ? list: list.slice(0, shown), ...(whole ? {} : { more: { count: list.length, words: words(list.length) } }) })
  }
  shelf('Pinned', pinned, PINNED_SHOWN, (n) => `Show all ${n} pinned`)
  shelf('Continue reading', reading, READING_SHOWN, (n) => `Show all ${n} you're reading`)
  const by = sort === 'added' ? 'created_at' : 'activity_at'
  for (const r of rest) {
    const title = timeGroup(r[by] || r.created_at, now)
    const last = out[out.length - 1]
    if (last && last.title === title && last.title !== 'Pinned' && last.title !== 'Continue reading') last.rows.push(r)
    else out.push({ title, rows: [r] })
  }
  return out
}

// ── the meta line ─────────────────────────────────────────────────────────────────────────

/** One part of a row's meta line. `project` is drawn as the chip (the text on a phone); `state` is
 *  the mark, never cut; `size` gives way first, then `age`. */
export interface MetaPart { part: 'word' | 'maker' | 'project' | 'age' | 'size' | 'fact' | 'state'; text: string }

/** Who made it, in the desktop row's words: "You", "Chat · Lisbon weekend", "Feed · Simon's blog",
 *  "From your intent", "From Garden" (a shared item on another project's list), "Added by Sam". */
export function makerWords(item: LibraryItem, ctx: LibraryContext = {}): string[] {
  if (item.contributor) return [`Added by ${item.contributor}`]
  if (ctx.project && item.project && item.project.id !== ctx.project) return [`From ${item.project.name}`]
  const from = item.from?.title ?? ''
  const fromKind = item.from ? refKind(item.from.ref) : ''
  if (refKind(item.ref) === 'findings') return ['From your intent']
  if (refKind(item.ref) === 'report') return []
  if (item.made === 'you') return ['You']
  if (item.made === 'chats') return from ? ['Chat', from] : ['Chat']
  if (item.made === 'sources') return from ? ['Feed', from] : ['Feed']
  if (item.made === 'automations') return fromKind === 'loop' && from ? ['Loop', from] : from ? ['Automation', from] : ['Automation']
  return []
}

/** The desktop row's meta parts, in the order he saw: the format word, the maker, the project chip,
 *  then a findings log's count or a report's schedule and last run, the age, the size, and the
 *  state mark. */
export function metaParts(item: LibraryItem, ctx: LibraryContext = {}, now: number = Date.now()): MetaPart[] {
  const parts: MetaPart[] = []
  const word = formatWord(item)
  if (word) parts.push({ part: 'word', text: word })
  for (const m of makerWords(item, ctx)) parts.push({ part: 'maker', text: m })
  // The chip, on the whole library only: a list filtered to a project draws none, and a shared item
  // from another project says so as its maker ("From Garden").
  if (item.project && !ctx.project) parts.push({ part: 'project', text: item.project.name })
  const kind = refKind(item.ref)
  if (kind === 'findings') {
    const n = ctx.intents?.get(refId(item.ref))?.count
    if (n) parts.push({ part: 'fact', text: `${n.toLocaleString()} so far` })
    const age = ageWords(item.activity_at, now)
    if (age) parts.push({ part: 'age', text: age })
  } else if (kind === 'report') {
    const rep = ctx.reports?.get(refId(item.ref))
    const last = rep?.lastRunTs ? rep.lastRunTs * 1000: null
    if (last) parts.push({ part: 'age', text: `${timeGroup(new Date(last).toISOString(), now) === 'Today' ? 'Today' : dayWords(last, now)} ${clockWords(last)}` })
    else if (rep?.schedule) parts.push({ part: 'fact', text: rep.schedule })
  } else {
    const age = ageWords(item.activity_at || item.created_at, now)
    if (age) parts.push({ part: 'age', text: age })
    const size = sizeWords(item.size)
    if (size && item.kind === 'document') parts.push({ part: 'size', text: size })
  }
  const mark = stateMarkOf(item, ctx)
  if (mark) parts.push({ part: 'state', text: mark.words })
  return parts
}

/** The phone row's short meta (768 px and below): the format word, the maker by its name alone, the
 *  project as text, then the state mark in its short form, or else one fact (a findings log's count,
 *  a report's last run time, or an age its group does not say already, within the week). A row with
 *  a mark leaves "You" out. */
export function phoneMetaParts(item: LibraryItem, ctx: LibraryContext = {}, group = '', now: number = Date.now()): MetaPart[] {
  const parts: MetaPart[] = []
  const word = formatWord(item)
  if (word) parts.push({ part: 'word', text: word })
  const mark = stateMarkOf(item, ctx)
  const maker = makerWords(item, ctx)
  const kind = refKind(item.ref)
  const name = kind === 'findings' ? 'Your intent' : maker.length > 1 ? maker[maker.length - 1] : maker[0] ?? ''
  if (name && !(mark && name === 'You')) parts.push({ part: 'maker', text: name })
  if (item.project && item.project.id !== ctx.project) parts.push({ part: 'project', text: item.project.name })
  if (mark) {
    parts.push({ part: 'state', text: mark.short })
    return parts
  }
  if (kind === 'findings') {
    const n = ctx.intents?.get(refId(item.ref))?.count
    if (n) parts.push({ part: 'fact', text: `${n.toLocaleString()} so far` })
    return parts
  }
  if (kind === 'report') {
    const rep = ctx.reports?.get(refId(item.ref))
    if (rep?.lastRunTs) parts.push({ part: 'fact', text: group === 'Today' ? clockWords(rep.lastRunTs * 1000) : dayWords(rep.lastRunTs * 1000, now) })
    return parts
  }
  const secs = epochSeconds(item.activity_at || item.created_at)
  if (secs !== undefined && now - secs * 1000 < 7 * 86_400_000 && group !== 'Yesterday') {
    const age = ageWords(item.activity_at || item.created_at, now)
    if (age && !(group === 'Today' && !/ago$|just now/.test(age))) parts.push({ part: 'age', text: age })
  }
  return parts
}

/** A row's name: its visible title first, then its meta, joined with commas, as a screen reader
 *  hears it ("Findings: Anything that could improve my homelab, From your intent, 14 so far, 3 h ago"). */
export function rowName(item: LibraryItem, parts: readonly MetaPart[]): string {
  return [titleOf(item), ...parts.map((p) => p.text)].filter(Boolean).join(', ')
}

/** The title a row shows: a findings log reads "Findings: <goal>"; anything with no title its
 *  format's word. */
export function titleOf(item: Pick<LibraryItem, 'ref' | 'title' | 'format'>): string {
  if (refKind(item.ref) === 'findings') return `Findings: ${item.title}`
  return item.title || formatWord(item) || 'Untitled'
}
