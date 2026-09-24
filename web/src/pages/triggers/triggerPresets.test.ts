import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { emptyDraft } from '../schedule/ScheduleForm'
import {
  TRIGGER_PRESETS, cadenceCron, cadenceLabel, findTriggerPreset, prefillDraft, type Cadence,
} from './triggerPresets'

// ── A preset must produce a WORKING trigger, not a filled-in-looking form ──────────────
//
// "Pre-filled" is only worth anything if pressing Create next saves something that runs.
// Two ways for a preset to look right and be dead, both held here against oracles rather
// than against a copy of the answer:
//
//   1. THE ACTION. Each preset names a core action provider and a config. Whether that
//      config is complete is decided by the provider's own bundled manifest — the same
//      `settingsSchema` the create form gates its Create button on and the backend
//      validates against. So this test reads those manifests out of the Python tree
//      (`artifactKinds.test.ts` sets the precedent) instead of restating their required
//      lists, which would drift the moment a schema changes.
//
//   2. THE CADENCE. A card that advertises "Every day · 8:00 AM" next to a hand-written
//      cron string is two sources for one fact. The catalog declares a structured
//      `Cadence` once; the label and the saved cron are both derived from it. These
//      assertions pin that derivation in both directions.
//
// The third clause — the expert blank path is unchanged — is `findTriggerPreset`
// returning null for an absent id, which is what makes every field in the create page
// fall back to its original default. That is asserted here and again at the call site in
// `presetSeedsCreateFlow.test.tsx`.

/** The provider's bundled manifest schema — the SAME oracle the form and backend use. */
function providerSchema(provider: string): { required: string[]; properties: Record<string, unknown> } {
  const p = join(__dirname, '../../../../src/personalclaw/apps/native', `${provider}-action`, 'app.json')
  const manifest = JSON.parse(readFileSync(p, 'utf8'))
  const schema = manifest?.provider?.settingsSchema ?? {}
  return { required: schema.required ?? [], properties: schema.properties ?? {} }
}

describe('the Triggers preset catalog', () => {
  it('offers a handful of presets with unique ids', () => {
    // The empty state is an on-ramp, not a catalog page: enough to show the shape of the
    // thing, few enough to read at a glance.
    expect(TRIGGER_PRESETS.length).toBeGreaterThanOrEqual(4)
    expect(new Set(TRIGGER_PRESETS.map((p) => p.id)).size).toBe(TRIGGER_PRESETS.length)
  })

  it('declares each fact once — the card id/title ARE the prefill id/name', () => {
    for (const p of TRIGGER_PRESETS) {
      expect(p.prefill.id, `${p.id}: the prefill carries the id the URL will`).toBe(p.id)
      expect(p.prefill.name, `${p.id}: the saved trigger is named after the card`).toBe(p.title)
      expect(p.summary, `${p.id}: the summary line is DERIVED from the cadence`).toBe(cadenceLabel(p.prefill.cadence))
    }
  })

  it('fills every REQUIRED field of the provider it names', () => {
    for (const p of TRIGGER_PRESETS) {
      const { required } = providerSchema(p.prefill.provider)
      expect(required.length, `${p.prefill.provider} manifest declares required fields`).toBeGreaterThan(0)
      for (const key of required) {
        const v = p.prefill.config[key]
        expect(typeof v, `${p.id}: required "${key}" must be seeded`).toBe('string')
        expect(String(v).trim(), `${p.id}: required "${key}" must not be blank`).not.toBe('')
      }
    }
  })

  it('sets no config key the provider does not declare', () => {
    // An invented key is silently dropped by the backend, so the preset would save and
    // then behave differently from what the card promised.
    for (const p of TRIGGER_PRESETS) {
      const { properties } = providerSchema(p.prefill.provider)
      for (const key of Object.keys(p.prefill.config))
        expect(Object.keys(properties), `${p.id}: "${key}" is not in ${p.prefill.provider}'s schema`).toContain(key)
    }
  })

  it('names only core-native providers (no preset needs an installed app)', () => {
    for (const p of TRIGGER_PRESETS)
      expect(['invoke-agent', 'notify', 'bash', 'run-script', 'send-message', 'create-task', 'run-prompt'])
        .toContain(p.prefill.provider)
  })
})

describe('cadence → the two things derived from it', () => {
  it('builds a five-field cron for every clock cadence', () => {
    for (const p of TRIGGER_PRESETS) {
      const c = p.prefill.cadence
      if (c.kind === 'everyHours') continue
      const cron = cadenceCron(c)
      expect(cron.split(/\s+/), `${p.id}: "${cron}"`).toHaveLength(5)
      expect(cron, `${p.id}`).toMatch(/^\d{1,2} \d{1,2} \* \* (\*|\d|1-5)$/)
    }
  })

  it('puts the cadence, and nothing else, into the schedule draft', () => {
    // The guardrail made literal: a preset SEEDS the existing form. Every other field of
    // the draft — timezone, channel, silent, strict, skip dates — must still be the blank
    // path's default, or a preset is quietly configuring things its card never mentioned.
    const blank = emptyDraft()
    for (const p of TRIGGER_PRESETS) {
      const d = prefillDraft(p.prefill)
      const changed = Object.keys(blank).filter((k) => {
        const a = (d as unknown as Record<string, unknown>)[k]
        const b = (blank as unknown as Record<string, unknown>)[k]
        return JSON.stringify(a) !== JSON.stringify(b)
      })
      for (const k of changed)
        expect(['kind', 'cron', 'intervalValue', 'intervalUnit'], `${p.id} also changed "${k}"`).toContain(k)
    }
  })

  it('routes a clock cadence to cron and an interval cadence to every', () => {
    const daily = prefillDraft({ id: 'x', name: 'x', cadence: { kind: 'daily', hour: 6, minute: 30 }, provider: 'notify', config: {} })
    expect(daily.kind).toBe('cron')
    expect(daily.cron).toBe('30 6 * * *')

    const hourly = prefillDraft({ id: 'x', name: 'x', cadence: { kind: 'everyHours', hours: 4 }, provider: 'notify', config: {} })
    expect(hourly.kind).toBe('every')
    expect(hourly.intervalValue).toBe(4)
    expect(hourly.intervalUnit).toBe('h')
  })

  it('numbers weekdays the way cron does — 1 is Monday', () => {
    expect(cadenceLabel({ kind: 'weekly', weekday: 1, hour: 9, minute: 0 }, 'en-US')).toContain('Monday')
    expect(cadenceLabel({ kind: 'weekly', weekday: 0, hour: 9, minute: 0 }, 'en-US')).toContain('Sunday')
    expect(cadenceCron({ kind: 'weekly', weekday: 1, hour: 9, minute: 0 })).toBe('0 9 * * 1')
  })

  it('formats the clock and the weekday through the LOCALE, not frozen en-US copy', () => {
    // The plan's requirement: derive the cadence from the locale-format seam. A 24-hour
    // locale must not be shown "8:00 AM", and a German user must not be told "Monday".
    const morning: Cadence = { kind: 'daily', hour: 8, minute: 0 }
    expect(cadenceLabel(morning, 'en-US')).toBe('Every day · 8:00 AM')
    // 24-hour locale: same instant, no meridiem. THIS is the drift a frozen string causes.
    expect(cadenceLabel(morning, 'de-DE')).toBe('Every day · 8:00')
    expect(cadenceLabel(morning, 'de-DE')).not.toMatch(/AM|PM/)
    const evening: Cadence = { kind: 'daily', hour: 20, minute: 0 }
    expect(cadenceLabel(evening, 'en-US')).toBe('Every day · 8:00 PM')
    expect(cadenceLabel(evening, 'de-DE')).toBe('Every day · 20:00')

    const monday: Cadence = { kind: 'weekly', weekday: 1, hour: 9, minute: 0 }
    expect(cadenceLabel(monday, 'de-DE')).toContain('Montag')
    expect(cadenceLabel(monday, 'ja-JP')).toContain('月曜日')
  })

  it('formats an afternoon hour without wrapping the day (the UTC weekday trap)', () => {
    // 23:00 in a 12-hour locale is 11 PM, not 11 AM — and the weekday helper builds its
    // date in UTC so a machine west of Greenwich cannot land on the previous day.
    expect(cadenceLabel({ kind: 'daily', hour: 23, minute: 0 }, 'en-US')).toBe('Every day · 11:00 PM')
    for (let wd = 0; wd < 7; wd++) {
      const label = cadenceLabel({ kind: 'weekly', weekday: wd, hour: 0, minute: 0 }, 'en-US')
      expect(label).toContain(['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][wd])
    }
  })

  it('says "Every hour" in the singular and counts hours otherwise', () => {
    expect(cadenceLabel({ kind: 'everyHours', hours: 1 })).toBe('Every hour')
    expect(cadenceLabel({ kind: 'everyHours', hours: 6 })).toBe('Every 6 hours')
    expect(cadenceCron({ kind: 'everyHours', hours: 6 })).toBe('')
  })
})

describe('findTriggerPreset — what keeps the expert path expert', () => {
  it('resolves a known id to its preset', () => {
    expect(findTriggerPreset('morning-briefing')?.title).toBe('Morning briefing')
  })

  it('returns null for an absent id — the blank create path', () => {
    expect(findTriggerPreset('')).toBeNull()
  })

  it('returns null for an unknown id rather than guessing one', () => {
    // A stale bookmark or a hand-typed param opens the ordinary blank form, not the
    // first preset in the list.
    expect(findTriggerPreset('nope')).toBeNull()
    expect(findTriggerPreset('MORNING-BRIEFING')).toBeNull()
  })
})
