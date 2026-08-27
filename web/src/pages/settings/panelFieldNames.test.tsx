import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

// ── Settings controls that announced nothing, or announced the same thing twice ─
//
// Probed all 30 settings panels for controls with no resolvable accessible name (aria-labelledby →
// aria-label → label[for] → wrapping <label> → title). Eight were unnamed across four panels, and
// one pair was worse than unnamed — it was AMBIGUOUS:
//
//     settings/security      "e.g. nas.local" ×2   ← the SAME shared component, twice
//                            "e.g. my-secret-tool .*"
//     settings/tool-output   "new rule name", the strategy <select>, "match regex …"
//     settings/agent         "Add path…"
//     settings/voice         "Add a term …"
//
// A placeholder is NOT an accessible name (it is not exposed as one and disappears on input), so
// every one of these was a bare box to a screen reader.
//
// 🔑 The instructive case is `SecurityPanel`'s `HostList`: ONE component rendered twice, as "Allowed
// hosts" and "Denied hosts". A constant aria-label would have made both announce IDENTICALLY —
// non-null and still useless, and confusing the allow box for the deny box is security-relevant. So
// every name here DERIVES from what distinguishes that instance (`label`, `rule.name`), never a
// constant on a component that renders more than once. Same reasoning for `ProjectionRulesPanel`'s
// `StrategyPicker` (existing rule vs the new-rule row) and its per-row Remove button, which
// announced "Remove rule" N times.
//
// Verified on the running build, all 30 panels: 109 controls, 0 unnamed, 0 duplicate-name groups.
// With a rule configured, the two rows read "Strategy for probe-rule" vs "Strategy for the new rule"
// and the button reads "Remove rule probe-rule".

const SETTINGS = join(process.cwd(), 'src/pages/settings')
const read = (f: string) => readFileSync(join(SETTINGS, f), 'utf8')

/** Source with comments stripped — the notes above name the very attributes under test, and a bare
 *  text search would count an explanation as compliance. */
const code = (f: string) =>
  read(f).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the four panels that had unnamed controls', () => {
  it('SecurityPanel host inputs name themselves from the list they add to', () => {
    // Reverting to a constant (or dropping it) reds this — and a constant would ALSO be wrong,
    // because this component renders twice.
    expect(code('SecurityPanel.tsx')).toMatch(/aria-label=\{`Add a host to \$\{label\.toLowerCase\(\)\}`\}/)
  })

  it('SecurityPanel denylist input says what typing there DOES', () => {
    expect(code('SecurityPanel.tsx')).toMatch(/aria-label="Add a shell denylist pattern \(regex\)"/)
  })

  it('the shared StrListField input names itself from its Field label', () => {
    // A RAW input inside settingsUI's Field cannot claim the published label — only the form-family
    // components read FieldLabelCtx — so it must self-name. Derived from `label`, so it stays right
    // at every call site.
    //
    // 🔁 SCANNED IN `settingsUI.tsx`, NOT `AgentDefaultsPanel.tsx`: StrListField gained its second
    // call site (External Access' capture upstream allow-list) and moved into the shared module
    // rather than being copied. The prediction in this test's old comment is what happened; the
    // scan follows the code. `placeholder` is now per-call-site ("Add path…" / "Add host…") — which
    // is precisely why the NAME must not be, since a placeholder is not an accessible name.
    expect(code('settingsUI.tsx')).toMatch(/aria-label=\{`Add to \$\{label\.toLowerCase\(\)\}`\}/)
    // …and the panel that used to declare it privately must not have kept a copy.
    expect(code('AgentDefaultsPanel.tsx')).not.toMatch(/function StrListField/)
  })

  it('VoicePanel vocabulary input is named', () => {
    expect(code('VoicePanel.tsx')).toMatch(/aria-label="Add a vocabulary term"/)
  })

  it('ProjectionRulesPanel names all six controls, scoped per row', () => {
    const src = code('ProjectionRulesPanel.tsx')
    expect(src).toMatch(/aria-label="Rule name"/)
    expect(src).toMatch(/aria-label="New rule name"/)
    expect(src).toMatch(/aria-label=\{rule\.name \? `Match regex for \$\{rule\.name\}` : 'Match regex'\}/)
    expect(src).toMatch(/aria-label="Match regex for the new rule"/)
    // The shared picker and the per-row button must SAY WHICH ROW.
    expect(src).toMatch(/aria-label=\{forRule \? `Strategy for \$\{forRule\}` : 'Strategy for the new rule'\}/)
    expect(src).toMatch(/aria-label=\{rule\.name \? `Remove rule \$\{rule\.name\}` : 'Remove rule'\}/)
  })
})

describe('a component that renders more than once must not carry a constant name', () => {
  it('StrategyPicker takes forRule, and both call sites distinguish themselves', () => {
    const src = code('ProjectionRulesPanel.tsx')
    expect(src).toMatch(/forRule\?: string/)
    // The existing-rule row passes the rule's name; the new-rule row deliberately passes nothing and
    // gets the 'new rule' branch. If the existing row stopped passing it, both would read the same.
    expect(src).toMatch(/<StrategyPicker[\s\S]*?forRule=\{rule\.name\}/)
  })

  it('HostList renders twice with different labels — which is why the name is derived', () => {
    const src = code('SecurityPanel.tsx')
    const uses = [...src.matchAll(/<HostList\s+label="([^"]+)"/g)].map((m) => m[1])
    expect(uses).toEqual(['Allowed hosts', 'Denied hosts'])
    // Belt and braces: no constant aria-label on the input inside a twice-rendered component.
    expect(/aria-label="Add a host"/.test(src), 'a constant here would announce both identically').toBe(false)
  })
})

describe('what a SOURCE rail can and cannot decide here', () => {
  // A source-only rail CANNOT judge whether a control is named: a checkbox wrapped in a <label>
  // (SecurityPanel's "Allow all private networks") and a <select> inside a Field (VoicePanel's
  // speech voice) are both correctly named in the DOM with nothing on the element itself. A rail that
  // flags them reds on correct code, and a gate nobody can satisfy gets turned off.
  //
  // Measured, to be concrete: a naive "every control carries aria-label/id" rail reported EIGHT
  // offenders in settings/ while the live DOM reported ZERO unnamed. All eight resolved a name from
  // an ancestor. So the rail below asserts only the part source can decide — that the controls this
  // cycle fixed keep their names — and the DOM probe stays the detector.
  //
  // Two rail bugs found while building it, worth not repeating:
  //   · `search(/\/>|>/)` to find a tag's end STOPS at the `>` in an inline `(e) => …` handler, so
  //     the attribute slice was truncated before a later aria-label. Track brace depth instead.
  //   · Computing a line number against COMMENT-STRIPPED source and reporting it as a file line
  //     points at the wrong code — off by the number of comment lines above it.

  it('no fixed control silently loses its name (the panels driven this cycle)', () => {
    const MUST_KEEP: Array<[string, RegExp]> = [
      ['SecurityPanel.tsx', /aria-label=\{`Add a host to \$\{label\.toLowerCase\(\)\}`\}/],
      ['SecurityPanel.tsx', /aria-label="Add a shell denylist pattern \(regex\)"/],
      // Scanned in the SHARED module: `StrListField` moved there when External Access became its
      // second call site. Same control, same derived name, one declaration.
      ['settingsUI.tsx', /aria-label=\{`Add to \$\{label\.toLowerCase\(\)\}`\}/],
      ['VoicePanel.tsx', /aria-label="Add a vocabulary term"/],
      ['MemoryPanel.tsx', /aria-label="Lesson rule"/],
      ['MemoryPanel.tsx', /aria-label="Fact key"/],
      ['MemoryPanel.tsx', /aria-label="Fact value"/],
      ['ProjectionRulesPanel.tsx', /aria-label="Rule name"/],
      ['ProjectionRulesPanel.tsx', /aria-label="New rule name"/],
      ['ProjectionRulesPanel.tsx', /aria-label="Match regex for the new rule"/],
    ]
    const missing = MUST_KEEP.filter(([f, re]) => !re.test(code(f))).map(([f, re]) => `${f} ${re}`)
    expect(missing, `these names were measured on the live DOM and must not regress:\n  ${missing.join('\n  ')}`).toEqual([])
  })

  it('the check is not vacuous — every named file exists and is scanned', () => {
    for (const f of ['SecurityPanel.tsx', 'AgentDefaultsPanel.tsx', 'settingsUI.tsx', 'VoicePanel.tsx', 'MemoryPanel.tsx', 'ProjectionRulesPanel.tsx']) {
      expect(readdirSync(SETTINGS), `${f} must exist`).toContain(f)
      expect(code(f).length).toBeGreaterThan(200)
    }
  })
})
