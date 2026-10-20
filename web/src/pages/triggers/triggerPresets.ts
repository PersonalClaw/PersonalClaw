import { Sunrise, Newspaper, Moon, BellRing, type LucideIcon } from 'lucide-react'
import type { PresetDef } from '../../ui/PresetEmptyState'
import { emptyDraft, type ScheduleDraft } from '../schedule/ScheduleForm'

/** A preset's recurrence, declared STRUCTURALLY rather than as a cron string plus a
 *  hand-written label.
 *
 *  One datum feeds both halves — {@link cadenceCron} builds what gets SAVED and
 *  {@link cadenceLabel} builds what the card SAYS — so the card can never advertise
 *  a cadence the saved trigger does not have. (Declaring `cron: '0 8 * * *'` next to
 *  `summary: 'Every day · 8:00am'` is two sources of one fact, and the second one
 *  rots silently.)
 *
 *  `weekday` is cron numbering: 0 = Sunday … 6 = Saturday. */
export type Cadence =
  | { kind: 'daily'; hour: number; minute: number }
  | { kind: 'weekly'; weekday: number; hour: number; minute: number }
  | { kind: 'weekdays'; hour: number; minute: number }
  | { kind: 'everyHours'; hours: number }

/** The cron expression (or interval) a cadence saves as. */
export function cadenceCron(c: Cadence): string {
  switch (c.kind) {
    case 'daily': return `${c.minute} ${c.hour} * * *`
    case 'weekly': return `${c.minute} ${c.hour} * * ${c.weekday}`
    case 'weekdays': return `${c.minute} ${c.hour} * * 1-5`
    case 'everyHours': return ''  // an interval schedule, not a cron one
  }
}

/** The clock time, through the platform's locale formatter — NOT a frozen string.
 *
 *  A hardcoded "6:00am" is wrong for most of the world: whether the clock is 12- or
 *  24-hour, where the meridiem sits, and which separator is used are all locale
 *  facts. `Intl` (via `toLocaleTimeString` with no explicit locale) is the seam the
 *  rest of the app already formats times through (`scheduleMeta.relPast`,
 *  `weekGrid`), so the cadence line follows the user's environment for free. */
function timeLabel(hour: number, minute: number, locale?: string): string {
  // A fixed calendar day — only the time-of-day is being formatted.
  return new Date(2024, 0, 2, hour, minute).toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' })
}

/** The weekday NAME, from the same locale seam. 2024-01-07 is a Sunday, so adding
 *  the cron weekday number lands on that day. UTC throughout so a machine west of
 *  Greenwich cannot shift the date into the previous day. */
function weekdayLabel(weekday: number, locale?: string): string {
  return new Date(Date.UTC(2024, 0, 7 + weekday)).toLocaleDateString(locale, { weekday: 'long', timeZone: 'UTC' })
}

/** The human cadence line a preset card shows ("Every day · 8:00 AM"). */
export function cadenceLabel(c: Cadence, locale?: string): string {
  switch (c.kind) {
    case 'daily': return `Every day · ${timeLabel(c.hour, c.minute, locale)}`
    case 'weekly': return `Every ${weekdayLabel(c.weekday, locale)} · ${timeLabel(c.hour, c.minute, locale)}`
    case 'weekdays': return `Every weekday · ${timeLabel(c.hour, c.minute, locale)}`
    case 'everyHours': return c.hours === 1 ? 'Every hour' : `Every ${c.hours} hours`
  }
}

/** What a picked preset seeds the create flow with: the schedule mechanism (as a
 *  cadence) and the action (a registered provider name + its config). Everything
 *  else in the form keeps its blank-path default. */
export interface TriggerPrefill {
  /** The preset's id — this is what the surface puts in `?preset=`, so the seeded
   *  create flow is deep-linkable and survives a reload like every other view state. */
  id: string
  name: string
  cadence: Cadence
  /** A core action-provider name — `invoke-agent`, `notify`, … */
  provider: string
  /** Config for that provider, merged OVER its schema defaults at seed time so the
   *  required fields are filled and the optional ones keep their declared default. */
  config: Record<string, unknown>
}

/** The cadence half of the prefill as a {@link ScheduleDraft} — i.e. exactly what the
 *  user would have typed into `ScheduleForm` by hand. Interval cadences take the
 *  `every` path, clock cadences the `cron` path, so a preset can seed either mode. */
export function prefillDraft(p: TriggerPrefill): ScheduleDraft {
  const base = emptyDraft()
  if (p.cadence.kind === 'everyHours')
    return { ...base, kind: 'every', intervalValue: p.cadence.hours, intervalUnit: 'h' }
  return { ...base, kind: 'cron', cron: cadenceCron(p.cadence) }
}

/** Build one catalog entry from a single declaration of each fact.
 *
 *  The id, the title and the cadence are each written ONCE and used twice — the id
 *  as both the catalog key and the `?preset=` payload, the title as both the card
 *  heading and the trigger's name, the cadence as both the card's summary line and
 *  the saved cron. Nothing here can disagree with itself. */
function preset(p: {
  id: string
  icon: LucideIcon
  title: string
  description: string
  cadence: Cadence
  provider: string
  config: Record<string, unknown>
}): PresetDef<TriggerPrefill> {
  return {
    id: p.id,
    icon: p.icon,
    title: p.title,
    summary: cadenceLabel(p.cadence),
    description: p.description,
    prefill: { id: p.id, name: p.title, cadence: p.cadence, provider: p.provider, config: p.config },
  }
}

/** The Triggers/Schedule preset catalog.
 *
 *  Every entry saves a REAL schedule trigger: the cadence produces a valid cron (or
 *  interval) and the action config fills its provider's required schema fields, so
 *  picking a card and pressing Create yields a trigger with a next run — not a form
 *  full of placeholder prose the user has to finish. `triggerPresets.test.ts` holds
 *  that: it checks each preset against the two providers' required-field lists.
 *
 *  Both providers are core-native (registered unconditionally by
 *  `action_providers.registry._ensure_default_providers_registered`), so no preset
 *  depends on an installed app. */
export const TRIGGER_PRESETS: PresetDef<TriggerPrefill>[] = [
  preset({
    id: 'morning-briefing',
    icon: Sunrise,
    title: 'Morning briefing',
    description: 'An agent writes you a short start-of-day briefing.',
    cadence: { kind: 'daily', hour: 8, minute: 0 },
    provider: 'invoke-agent',
    config: {
      task_template:
        'Write a short morning briefing: what is waiting in my inbox, what is scheduled today, '
        + 'and the three things most worth my attention. Keep it under 200 words.',
    },
  }),
  preset({
    id: 'weekly-digest',
    icon: Newspaper,
    title: 'Weekly digest',
    description: 'A once-a-week summary of what moved and what stalled.',
    cadence: { kind: 'weekly', weekday: 1, hour: 9, minute: 0 },
    provider: 'invoke-agent',
    config: {
      task_template:
        'Summarize the past week: what got finished, what is still open, and what slipped. '
        + 'Group it by project and end with what to pick up first this week.',
    },
  }),
  preset({
    id: 'nightly-check',
    icon: Moon,
    title: 'Nightly check',
    description: 'An agent looks for anything left broken before you stop for the day.',
    cadence: { kind: 'daily', hour: 23, minute: 0 },
    provider: 'invoke-agent',
    config: {
      task_template:
        'Check the projects I touched today for anything left in a broken state — failing tests, '
        + 'uncommitted work, an unfinished edit — and list what needs picking up tomorrow.',
    },
  }),
  preset({
    id: 'standup-reminder',
    icon: BellRing,
    title: 'Standup reminder',
    description: 'A plain desktop notification on weekdays — no agent run.',
    cadence: { kind: 'weekdays', hour: 9, minute: 45 },
    provider: 'notify',
    config: {
      title_template: 'Standup in 15 minutes',
      body_template: 'Jot down what you finished yesterday and what you are picking up today.',
    },
  }),
]

/** The preset named by a `?preset=<id>` URL param, or `null` for an absent/unknown id.
 *
 *  `null` is what keeps the expert path untouched: the create page seeds from
 *  `findTriggerPreset(...)?.prefill` and falls back to its own blank defaults, so a
 *  blank `#/triggers/new` — and a stale or hand-typed preset id — both open the
 *  form exactly as they did before presets existed. */
export function findTriggerPreset(id: string): PresetDef<TriggerPrefill> | null {
  if (!id) return null
  return TRIGGER_PRESETS.find((p) => p.id === id) ?? null
}
