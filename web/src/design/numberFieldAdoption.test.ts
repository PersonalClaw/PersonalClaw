import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── A clamped numeric SETTING goes through NumberField ────────────────────────
//
// `ui/forms.tsx` declares `NumberField` "the canonical numeric stepper" and "the one home for
// that role", and its comment records that the settings panels had hand-rolled it verbatim
// three times. Two hand-rolled copies were still live — both named `NumInput`, both with
// byte-identical chrome (`h-9 w-28 rounded-md bg-surface-container …`), in
// `settings/MemoryPanel.tsx` and `inbox/InboxSettingsPanel.tsx`.
//
// This was not cosmetic. Neither copy had the canonical clamp-on-commit behaviour, and the
// difference is measurable — driven on a live seeded gateway at #/settings/memory?tab=settings,
// on the `history_idle_hours` field (min 0.5):
//
//                          BEFORE (NumInput)                AFTER (NumberField)
//   type "125"             3 saves: 13, 123, 1253           0 while typing, 1 on blur
//   enter 0 (min 0.5)      committed 0                      clamped to 0.5
//   clear the field        committed 0 (Number('') === 0)   reverts, 0 saves
//
// So every keystroke persisted a garbage intermediate. Mid-edit re-renders also clobbered the
// buffer badly enough that a plain retype produced "301253" — the field was effectively
// unusable, not merely imprecise.
//
// SCOPE — this rail is narrow on purpose, because the other `type="number"` sites are a
// different ROLE and forcing them onto NumberField would break them:
//
//  · SCHEMA-DRIVEN form fields (`tools/schema.tsx`, `settings/ProviderConfigForm.tsx`,
//    `chat/PromptPalette.tsx`, `prompts/PromptDetail.tsx`) accept `''` as a legitimate empty
//    value and live inside generic JSON-schema renderers. `NumberField` REVERTS an empty entry
//    by design, so it cannot express "no value".
//  · DRAFT-form inputs on non-settings surfaces (`ChatPage`, `schedule/ScheduleForm`,
//    `code/CodePlanReview`, `loops/LoopPlanReview`) carry their own inline `Math.max` clamping
//    and different chrome. Converging them is a wider migration, logged as a candidate.
//
// The rail therefore keys on the CHROME the two copies shared — the settings-stepper look —
// rather than on `type="number"` alone. A rail that flagged every numeric input would cry wolf
// on nine legitimate call sites, and a rail that cries wolf gets ignored.

const SRC = join(process.cwd(), 'src')

/** Every `.tsx` under src/, excluding tests and the primitive's own home. */
function sourceFiles(): string[] {
  const out: string[] = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (!/\.tsx$/.test(e.name) || /\.test\.tsx$/.test(e.name)) continue
      if (p.endsWith(join('ui', 'forms.tsx'))) continue   // the canonical implementation
      out.push(p)
    }
  }
  walk(SRC)
  return out
}

// The settings-stepper chrome the two hand-rolled copies shared verbatim. Keyed on the
// distinctive pairing (fixed height + fixed width + the container fill), not on any one class,
// so a reformat or class reorder cannot slip a copy past.
const STEPPER_CHROME = /h-9[^"']*w-28[^"']*bg-surface-container|w-28[^"']*h-9[^"']*bg-surface-container/

describe('the canonical numeric stepper', () => {
  const files = sourceFiles()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(files.length).toBeGreaterThan(50)
    expect(files.some((f) => f.endsWith(join('settings', 'MemoryPanel.tsx')))).toBe(true)
  })

  it('has no hand-rolled twin wearing the settings-stepper chrome', () => {
    const offenders: string[] = []
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      // Only the combination matters: a number input dressed as the settings stepper.
      if (!/type="number"/.test(src)) continue
      for (const [i, line] of src.split('\n').entries()) {
        if (/type="number"/.test(line) || STEPPER_CHROME.test(line)) {
          // Look at the element as a whole — the chrome often sits on the next line.
          const window = src.split('\n').slice(Math.max(0, i - 2), i + 3).join(' ')
          if (/type="number"/.test(window) && STEPPER_CHROME.test(window)) {
            offenders.push(`${f.slice(SRC.length + 1)}:${i + 1}`)
            break
          }
        }
      }
    }
    expect(
      offenders,
      'A clamped numeric setting must use NumberField from ui/forms — hand-rolling it loses ' +
        'clamp-on-commit, so every keystroke persists a garbage intermediate and an empty ' +
        'field commits 0:\n  ' + offenders.join('\n  '),
    ).toEqual([])
  })

  it('the two migrated panels reach for the primitive', () => {
    // Named explicitly: these are the call sites the defect was measured on, so a future edit
    // that reverts either one fails here by name rather than by a generic sweep.
    for (const rel of [join('settings', 'MemoryPanel.tsx'), join('inbox', 'InboxSettingsPanel.tsx')]) {
      const src = readFileSync(join(SRC, 'pages', rel), 'utf8')
      expect(src, `${rel} should render NumberField`).toMatch(/<NumberField\b/)
      expect(src, `${rel} still defines a local NumInput`).not.toMatch(/function NumInput\b/)
    }
  })
})
