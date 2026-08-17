import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A rejected save must name the control, not its config key ──────────────────────────────────────
//
// `settingsUI`'s `ToggleRow` and `NumberRow` hold BOTH the control's visible name and its config key,
// and handed the panel's patch only the key. So a rejected save said
//
//     Couldn't save soft_stop_budget_secs: value must be ≤ 600
//
// about a control the UI calls **"Subagent timeout"**. The user has never seen that string anywhere on
// screen — the panel renders `label`, and `field` exists only in the code. Measured across the family:
// **eleven identical toasts in eight panels**, over **36 shared-row usages**.
//
// 🔑 THE LABEL WAS ALREADY IN HAND, one line away — both rows pass it to `<Toggle label={label}>` and
// `ariaLabel={label}` for accessibility, then dropped it on the way to the failure path. Same shape as
// the organize proposal that cited a title it never showed (`session_organize`): the fix is not new
// data, it is not throwing away what the component already has.
//
// 🪤 THE FOURTH ARGUMENT IS OPTIONAL, DELIBERATELY, and the toast keeps `?? key`. A panel that has not
// adopted it still type-checks and still shows the key — which is worse copy but not a crash, whereas
// a bare `${label}` would render the literal "undefined" the moment any caller forgot. The fallback is
// asserted below so nobody "tidies" it away.

const SETTINGS = join(process.cwd(), 'src', 'pages', 'settings')
const UI = readFileSync(join(SETTINGS, 'settingsUI.tsx'), 'utf8')
const panels = () =>
  readdirSync(SETTINGS).filter((f) => /Panel\.tsx$/.test(f) && !f.includes('.test.'))

describe('a rejected settings save names the control', () => {
  it('both shared rows hand the label to the patch they fire', () => {
    expect(UI, 'the toggle must pass its label').toContain('patch(field, v as never, flash, label)')
    expect(UI, 'the number field must too').toContain('patch(field, n as never, flash, label)')
    // The contract has to admit it, or a panel cannot receive it.
    const sigs = [...UI.matchAll(/patch: \(k: string, v: never, cb: \(\) => void, label\?: string\) => void/g)]
    expect(sigs.length, 'both row prop types carry the 4th argument').toBe(2)
  })

  it('NO save failure names a config key or path — the ratchet', () => {
    // 🪤 Keyed on the population (every "Couldn't save" toast in every panel), never on the fixed
    // form: a sweep that looked for the CORRECTED string could only ever visit sites already fixed.
    //
    // 🪤 And scoped to what was MEASURED. The first version demanded `label ?? key` of every save
    // toast and reported 13 offenders — but most of those name the thing in words already ("your
    // username", "mid-turn policy", "this chat setting", "scan mode", "your inbox settings"). Those
    // are the goal, not violations. The rule is narrower and is the actual finding: **a save toast may
    // not interpolate a bare config identifier.** A human phrase, or a label with the identifier as a
    // fallback, both pass.
    const IDENTIFIER_VARS = ['key', 'path', 'field']
    const offenders: string[] = []
    let seen = 0
    for (const f of panels()) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      for (const m of src.matchAll(/notify\(`Couldn't save ([^`]*)`/g)) {
        seen++
        const phrase = m[1]
        for (const v of IDENTIFIER_VARS) {
          // A bare `${key}` is the defect; `${label ?? key}` is the fix.
          if (new RegExp(`\\$\\{\\s*${v}\\s*\\}`).test(phrase)) {
            offenders.push(`${f}: \${${v}} — ${phrase.slice(0, 44)}`)
          }
        }
      }
    }
    expect(seen, 'the sweep must find the family it guards').toBeGreaterThanOrEqual(16)
    expect(offenders, 'a config identifier is not a name the user has seen').toEqual([])
  })

  it('the human-phrased toasts are left alone — they were already the goal', () => {
    // Pinned so a later pass does not "normalise" them into the label form and lose better copy.
    const chat = readFileSync(join(SETTINGS, 'ChatPanel.tsx'), 'utf8')
    expect(chat).toContain("Couldn't save mid-turn policy:")
    expect(chat).toContain("Couldn't save this chat setting:")
    const account = readFileSync(join(SETTINGS, 'AccountPanel.tsx'), 'utf8')
    expect(account).toContain("Couldn't save your username:")
  })

  it('every panel that receives the label declares it', () => {
    // A toast using `label` whose enclosing helper never took it would not compile, but a helper that
    // declares it while the row never passes it would silently keep showing the key. Both ends,
    // counted: the number of panels with the toast must equal the number declaring the parameter.
    const withToast = panels().filter((f) =>
      readFileSync(join(SETTINGS, f), 'utf8').includes('${label ?? key}'),
    )
    const withParam = panels().filter((f) =>
      /label\?: string\) => \{/.test(readFileSync(join(SETTINGS, f), 'utf8')),
    )
    expect(withToast.length, 'the family spans the panels measured').toBeGreaterThanOrEqual(8)
    expect(withToast.sort()).toEqual(withParam.sort())
  })

  it('a label is actually SUPPLIED in every panel, not merely declared', () => {
    // 🪤 THE GAP THIS TEST EXISTS FOR. The first version of this rail asserted the toast form and the
    // parameter declaration — both true in `ChatPanel` and `DurabilityPanel` while **nothing passed a
    // label**, because those two panels use their OWN row components (`onCommit`/`saved`) rather than
    // `settingsUI`'s. So `label` was always `undefined` there and the toast fell through to `?? key`:
    // four of the eleven toasts still read "Couldn't save autocompact_pct" at runtime.
    //
    // A declared-but-unsupplied parameter is invisible to a form check. Assert the SUPPLY: every panel
    // whose helper declares `label?` must contain at least one caller that passes a fourth argument or
    // forwards one. See [[defaulted-field-is-an-unsupplied-input]] for the general shape.
    const missing: string[] = []
    for (const f of panels()) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      if (!/label\?: string\) => \{/.test(src) && !/label\?: string\)/.test(src)) continue
      const supplies =
        // a shared row hands it over for us…
        /from '\.\/settingsUI'/.test(src) && /<(ToggleRow|NumberRow)/.test(src)
        // …or the panel's own rows forward it explicitly
        || /undefined, l\)/.test(src)
        || /onCommit\(\w+, (label|'[^']+')\)/.test(src)
        || /patchNum\('[^']+', v, '[^']+'\)/.test(src)
      if (!supplies) missing.push(f)
    }
    expect(missing, 'these panels accept a label but nothing gives them one').toEqual([])
  })

  it('the two panels with their OWN rows forward the label through every commit', () => {
    // Counted, because "some sites forward it" is how four toasts stayed broken.
    for (const [f, expected] of [['ChatPanel.tsx', 9], ['DurabilityPanel.tsx', 3]] as const) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      const commits = [...src.matchAll(/onCommit=\{\(/g)].length
      const forwarding = [...src.matchAll(/onCommit=\{\(\w+, l\) => patch\([^)]*undefined, l\)\}/g)].length
      expect(commits, `${f}: the row usages must be discoverable`).toBeGreaterThanOrEqual(expected)
      expect(forwarding, `${f}: every commit must forward the label`).toBe(expected)
    }
    // …and the rows themselves must PASS one when they commit, or the forwarding receives undefined.
    const chat = readFileSync(join(SETTINGS, 'ChatPanel.tsx'), 'utf8')
    expect(chat, 'the labelled row passes its own label').toContain('onCommit(n, label)')
    expect(chat, 'the row without a label prop supplies a literal').toContain(
      "onCommit(n, 'Auto-archive after (days)')",
    )
    const dur = readFileSync(join(SETTINGS, 'DurabilityPanel.tsx'), 'utf8')
    expect(dur).toContain('onChange={(n) => onCommit(n, label)}')
  })

  it('the fallback stays — a bare label would print "undefined"', () => {
    for (const f of panels()) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      for (const m of src.matchAll(/notify\(`Couldn't save \$\{([^}]*)\}/g)) {
        expect(m[1], `${f}: an interpolated save toast must fall back to the identifier`).toMatch(
          /\?\?\s*(key|path)/,
        )
      }
    }
  })

  it('GuardrailsPanel is included, though it uses a different row contract', () => {
    // Its rows fire `onSave`, not `settingsUI`'s `patch`, so the label had to be threaded separately.
    // Leaving it out would have made the family "fixed except one", which is the split this session's
    // coherence rules forbid.
    const g = readFileSync(join(SETTINGS, 'GuardrailsPanel.tsx'), 'utf8')
    expect(g).toContain('const patchNum = (path: string, value: number, label?: string)')
    const labelled = [...g.matchAll(/patchNum\('[^']+', v, '[^']+'\)/g)]
    expect(labelled.length, 'every patchNum call names its control').toBe(5)
  })

  it('the label is still used for accessibility, not moved off the control', () => {
    // The fix must not have "reused" the accessibility label by removing it from where it was.
    expect(UI).toContain('label={label}')
    expect(UI).toContain('ariaLabel={label}')
  })
})
