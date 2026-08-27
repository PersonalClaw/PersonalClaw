import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Field as FormsField } from './forms'
import { Toggle } from './Toggle'
import { Combobox } from './Combobox'
import { ShortcutRecorder } from './ShortcutRecorder'
import { Row } from '../pages/settings/settingsUI'

// ── THE HINT BESIDE A CONTROL IS THAT CONTROL'S DESCRIPTION ────────────────────────────────────────
//
// `Row`, `settingsUI`'s `Field` and `ui/forms`' `Field` each render a hint sentence and publish its
// id through `FieldHintProvider`. A control claims it with `aria-describedby`. Six form-family
// primitives already did; `Toggle` — the app's canonical switch — did not, so the sentence beside a
// settings switch existed for sighted users only.
//
// 🔴 Measured live on a demo-seeded home across all 34 `#/settings/*` subpages, before this landed:
//   61 switches rendered · 58 inside a wrapper that publishes a hint id · **0** carrying any
//   `aria-describedby`. After: 53 of the 58 resolve to the hint's exact text and the other 5 are
//   soft-off and keep their reason instead (see below) — 53 descriptions gained, 0 lost.
//
// WHY THIS TEST ASSERTS RESOLVED TEXT, NOT A PROP. `aria-describedby` is a promise about a *document
// relationship*: it names ids that must exist and must contain the sentence. Asserting the attribute
// is present would pass on a dangling id, on a typo'd id, and on an id pointing at the label — all
// three of which are worse than no attribute, because assistive tech announces "described" and then
// reads nothing. So every assertion below resolves the id list against the rendered DOM and compares
// the TEXT.
//
// 🪤 AND WHY SOFT-OFF IS THE INTERESTING CASE. `aria-describedby` outranks `title` in accname, and a
// soft-off control carries its unavailability reason in `title` (the kit's convention, ruled cycle 37
// and re-confirmed on `Button`, which measured an sr-only describedby target getting concatenated into
// the accessible NAME). So a Toggle that claimed the hint unconditionally would have *deleted* the
// reason from what a screen reader announces — a silent regression that looks like a fix. 5 of the 58
// are soft-off; on 3 the hint is a paraphrase of the reason, on 2 it adds real information. The reason
// still wins, and the hint returns by itself once the precondition clears. (7 call sites pass a
// `disabledReason` to a switch; soft-off is a STATE, so how many of them are soft-off at once moves
// with the preconditions. 5 were, in the seeded state that was measured.)

/** The accessible DESCRIPTION as accname computes it for our two carriers: a resolved
 *  `aria-describedby` wins outright, otherwise `title`. Returns '' for "no description". */
function describedText(el: Element | null, scope: HTMLElement): string {
  if (!el) return ''
  const ids = el.getAttribute('aria-describedby')
  if (ids) {
    const parts = ids.split(/\s+/).filter(Boolean).map((id) => {
      const target = scope.querySelector(`[id="${CSS.escape(id)}"]`)
      // A dangling pointer must never read as a description — surface it loudly instead.
      return target ? (target.textContent || '').replace(/\s+/g, ' ').trim() : `DANGLING:${id}`
    })
    const joined = parts.join(' ').trim()
    if (joined) return joined
  }
  return (el.getAttribute('title') || '').replace(/\s+/g, ' ').trim()
}

const HINT = 'Display a time on each message.'
const REASON = 'Set a password first — a sign-in form nobody can pass is worse than none'

describe('Toggle claims the hint published beside it', () => {
  it('a switch in a hinted Row is DESCRIBED by that row’s sentence', () => {
    const { container } = render(
      <Row label="Timestamps" hint={HINT}><Toggle on={false} onChange={() => {}} label="Timestamps" /></Row>,
    )
    const sw = container.querySelector('[role="switch"]')
    expect(sw).not.toBeNull()
    // The relationship, resolved — not the attribute.
    expect(describedText(sw, container)).toBe(HINT)
    // And the name is still the label, not the paragraph: a control that claims the hint as its
    // NAME announces a sentence where a noun belongs.
    expect(sw!.getAttribute('aria-label')).toBe('Timestamps')
  })

  it('a switch in a hinted ui/forms Field is described too (the other publisher)', () => {
    const { container } = render(
      <FormsField label="Timestamps" hint={HINT}><Toggle on onChange={() => {}} label="Timestamps" /></FormsField>,
    )
    expect(describedText(container.querySelector('[role="switch"]'), container)).toBe(HINT)
  })

  it('a read-only display switch is described as well', () => {
    const { container } = render(
      <Row label="Timestamps" hint={HINT}><Toggle on readOnly label="Timestamps" /></Row>,
    )
    const sw = container.querySelector('[role="switch"]')
    expect(sw!.tagName.toLowerCase()).toBe('span')   // the non-interactive path
    expect(describedText(sw, container)).toBe(HINT)
  })

  it('a DECORATIVE switch stays out of the a11y tree entirely', () => {
    const { container } = render(
      <Row label="Timestamps" hint={HINT}><Toggle on readOnly decorative label="Timestamps" /></Row>,
    )
    expect(container.querySelector('[role="switch"]')).toBeNull()
    // aria-hidden: pointing describedby out of a node the tree does not contain would be incoherent.
    expect(container.querySelector('[aria-hidden]')!.getAttribute('aria-describedby')).toBeNull()
  })

  it('NO hint means NO aria-describedby — never a dangling pointer', () => {
    const { container } = render(
      <Row label="Timestamps"><Toggle on={false} onChange={() => {}} label="Timestamps" /></Row>,
    )
    const sw = container.querySelector('[role="switch"]')
    expect(sw!.getAttribute('aria-describedby')).toBeNull()
    expect(describedText(sw, container)).toBe('')
  })

  it('a natively disabled switch (no reason) still gets the hint', () => {
    const { container } = render(
      <Row label="Timestamps" hint={HINT}><Toggle on={false} onChange={() => {}} disabled label="Timestamps" /></Row>,
    )
    const sw = container.querySelector('[role="switch"]')
    expect(sw!.hasAttribute('disabled')).toBe(true)
    expect(describedText(sw, container)).toBe(HINT)
  })
})

describe('soft-off precedence: the REASON survives, because describedby outranks title', () => {
  it('a soft-off switch announces its reason, NOT the row hint', () => {
    const { container } = render(
      <Row label="Offer password sign-in" hint="Set a password first. Turning this on without one would show a form nobody can pass.">
        <Toggle on={false} onChange={() => {}} disabled disabledReason={REASON} label="Offer password sign-in" />
      </Row>,
    )
    const sw = container.querySelector('[role="switch"]')!
    // aria-disabled (reachable-but-unavailable), reason in title, and NO describedby to displace it.
    expect(sw.getAttribute('aria-disabled')).toBe('true')
    expect(sw.getAttribute('aria-describedby')).toBeNull()
    expect(describedText(sw, container)).toBe(REASON)
  })

  it('clearing the precondition hands the hint back on the same render', () => {
    // The loss is transient and self-healing: same row, same props minus the reason.
    const { container } = render(
      <Row label="Offer password sign-in" hint={HINT}>
        <Toggle on={false} onChange={() => {}} label="Offer password sign-in" />
      </Row>,
    )
    expect(describedText(container.querySelector('[role="switch"]'), container)).toBe(HINT)
  })
})

describe('the other two live non-consumers in the family', () => {
  it('a Combobox trigger in a hinted Row is described', () => {
    const { container } = render(
      <Row label="Default agent" hint="Used for every new session.">
        <Combobox options={[{ value: 'a', label: 'Agent A' }]} value="a" onChange={() => {}} />
      </Row>,
    )
    const trigger = container.querySelector('button[aria-haspopup="listbox"]')
    expect(trigger).not.toBeNull()
    expect(describedText(trigger, container)).toBe('Used for every new session.')
  })

  it('a ShortcutRecorder in a hinted Field is described', () => {
    const hint = 'Used by the desktop app for global push-to-talk.'
    const { container } = render(
      <FormsField label="Push-to-talk shortcut" hint={hint}>
        <ShortcutRecorder label="Push-to-talk shortcut" value="Cmd+Shift+Space"
          format={(c) => c} parse={() => ''} onRecord={() => {}} />
      </FormsField>,
    )
    const btn = container.querySelector('button')
    expect(describedText(btn, container)).toBe(hint)
    // Its name is the chord-and-affordance sentence; the hint is the description, not the name.
    expect(btn!.getAttribute('aria-label')).toContain('activate to change')
  })
})

// ── The census half: the family cannot quietly lose a consumer ─────────────────────────────────────
//
// The behavioural tests above cover the three primitives this change touched. This half pins the
// whole consumer set, so deleting `useFieldHintId()` from any of the six that already had it reddens
// here rather than silently un-describing another 100+ call sites.
//
// 🪤 VACUITY FLOOR. A source scan that matches nothing looks exactly like a source scan that passes.
// Both the scanned population and the consumer count are floored, so an import path change, a
// renamed hook or a moved directory fails loudly instead of reading as clean.

// 🪤 ANCHORED TO THIS FILE, NOT `process.cwd()`. The scan root IS this test's own directory, and a
// cwd-derived path silently becomes a wrong path the moment the suite is invoked from anywhere but
// `web/` — which reads as an ENOENT crash, not as a finding. `import.meta.dirname` cannot drift.
const UI_DIR = import.meta.dirname
const tsxFiles = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return tsxFiles(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

/** Every `ui/` primitive that claims a published hint id.
 *
 *  `Slider` is deliberately absent, on measurement: it has exactly two call sites
 *  (`loops/QuestionSlider` and `ui/widget/ArtifactIterationRail`), both inside a plain
 *  `<div className="flex …">`, and neither publishes a hint — so consuming there would ship an inert
 *  control, which is worse than the gap because it reads as done.
 *
 *  `Checkbox` is absent for a design reason, not an oversight: it is a per-row control in a
 *  multi-select list (each row names itself with its item), never the single control of a hinted
 *  Field, so pointing a dozen rows at one sentence would describe every option identically. The one
 *  place a multi-select does sit inside a hinted `Field` is `AgentForm`'s `CheckList`, and that does
 *  not use `Checkbox` at all — it renders `aria-pressed` rows inside a `role="group"`. */
const EXPECTED_CONSUMERS = [
  'ChipInput', 'Combobox', 'DateInput', 'NumberField', 'Select',
  'ShortcutRecorder', 'TextArea', 'TextInput', 'Toggle',
].sort()

/** A module split into its top-level export spans: name → that declaration's own source.
 *
 *  🪤 PER-COMPONENT, NOT PER-FILE. The first draft of this census asked whether a *file* containing
 *  `useFieldHintId` also contained `aria-describedby` anywhere. `forms.tsx` holds SIX consumers, so
 *  deleting `Select`'s attribute left five other occurrences behind and the census passed 12/12 —
 *  a mutation that silently un-described every `<Select>` in the app read as clean. Splitting by
 *  export and asking the question of each body is what kills it.
 *
 *  Spans are cut at the next top-level `export function|const` rather than by brace matching: these
 *  are all top-level and sequential, and a brace scanner trips over braces inside regex literals. */
function exportSpans(rawSrc: string): Map<string, string> {
  // 🪤 A TEXT SCANNER READS COMMENTS. Every consumer below carries a long comment ABOUT
  // `aria-describedby`, so a scan of raw source would go green on a component whose comment
  // explains the attribute it no longer emits. Blank comments (preserving newlines, so the span
  // offsets and the length floor still mean something) before asking the question.
  const src = rawSrc
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/\/\/[^\n]*/g, (m) => ' '.repeat(m.length))
  const re = /^export (?:function|const) ([A-Za-z0-9_]+)/gm
  const hits: { name: string; at: number }[] = []
  for (let m = re.exec(src); m; m = re.exec(src)) hits.push({ name: m[1], at: m.index })
  const out = new Map<string, string>()
  hits.forEach((h, i) => out.set(h.name, src.slice(h.at, i + 1 < hits.length ? hits[i + 1].at : src.length)))
  return out
}

describe('the hint-consumer census', () => {
  it('every expected primitive reads the hint id AND emits aria-describedby IN ITS OWN BODY', () => {
    const files = tsxFiles(UI_DIR)
    // Vacuity floor: the scan must have found a real population of modules.
    expect(files.length).toBeGreaterThanOrEqual(40)

    const spans = new Map<string, { file: string; body: string }>()
    for (const f of files) {
      for (const [name, body] of exportSpans(readFileSync(f, 'utf8'))) {
        if (EXPECTED_CONSUMERS.includes(name)) spans.set(name, { file: f, body })
      }
    }
    // Vacuity floor: all nine located by name, so a rename or a move cannot read as a pass.
    expect([...spans.keys()].sort()).toEqual(EXPECTED_CONSUMERS)

    for (const [name, { file, body }] of spans) {
      expect(body.length, `${name}'s span in ${file} is implausibly short — span-splitting broke`).toBeGreaterThan(120)
      expect(body, `${name} (${file}) does not read the published hint id`).toMatch(/useFieldHintId\(\)/)
      expect(body, `${name} (${file}) reads the hint id but never emits aria-describedby`).toMatch(/aria-describedby=/)
    }
  })

  it('Toggle wires the hint through a softOff-aware binding, not unconditionally', () => {
    const src = readFileSync(join(UI_DIR, 'Toggle.tsx'), 'utf8')
    // The precedence rule is the whole subtlety; pin its shape so a "simplification" cannot drop it.
    expect(src).toMatch(/softOff\s*\?\s*undefined\s*:\s*hintId/)
  })
})
