import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── One primary per header, and it is the create action ───────────────────────────────────────────
//
// Every list destination in the app writes its create action the same way:
//
//     <HeaderControl icon={Plus} label="New task" variant="primary" priority="primary" … />
//
// `New chat`, `New task`, `New trigger`, `New project` (×2), `New agent`, `New report`, `New intent`,
// `Add knowledge`, `Install from URL`, `Start from template` — ten sites, one spelling. `#/skills` was
// the sole outlier and it broke the pattern in the more confusing direction: it SPLIT the two
// attributes across two different controls, declaring `priority="primary"` on `New skill` while
// rendering it `variant="secondary"`, and handing the coral `variant="primary"` to a `Browse` button.
//
// 🪤 AND THAT `Browse` BUTTON WAS THE MODE TAB RENDERED TWICE — same label, same `Store` icon, same
// `onBrowse` handler as the `ModeToggle` segment in the header's `left` slot, ~870px away in the same
// 40px strip. One action, two affordances, and the duplicate wore the primary colour while the real
// primary did not. That is the same defect class as the two `New project` controls 12px apart, except
// here both controls fired the identical function.
//
// This rail holds two things a reviewer cannot see from a diff: that the two attributes stay on ONE
// control, and that no header renders the same label twice.

const PAGES = join(import.meta.dirname, '..')
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

/** Every `<HeaderControl …/>` in `src/pages`, with the file it came from. */
function headerControls(): Array<{ rel: string; tag: string }> {
  const out: Array<{ rel: string; tag: string }> = []
  for (const abs of walk(PAGES)) {
    const src = strip(readFileSync(abs, 'utf8'))
    for (const m of src.matchAll(/<HeaderControl\b[\s\S]{0,400}?\/>/g)) {
      out.push({ rel: abs.slice(abs.indexOf('/pages/') + 7), tag: m[0] })
    }
  }
  return out
}


/** Every `<TopBar …/>` element in one source string, sliced to its own closing `/>` at brace depth 0.
 *  🪤 Depth-tracked because a TopBar's `left`/`right` slots are full of JSX with `{}` and `>` in them —
 *  stopping at the first `/>` catches an inner element and the slot contents are lost. */
function topBars(src: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(/<TopBar\b/g)) {
    let depth = 0
    for (let i = m.index! + m[0].length; i < src.length - 1; i++) {
      const c = src[i]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '/' && src[i + 1] === '>' && depth === 0) { out.push(src.slice(m.index!, i + 2)); break }
    }
  }
  return out
}

const labelOf = (tag: string) => /label=(?:"([^"]+)"|\{`?([^`{}]*?)`?\})/.exec(tag)?.[1] ?? null

describe('a header control that renders primary also declares primary priority', () => {
  const controls = headerControls()

  it('the sweep found the population (vacuity floor)', () => {
    expect(controls.length, 'no HeaderControl parsed out of src/pages — the walk is wrong').toBeGreaterThan(25)
    expect(
      controls.filter((c) => /variant="primary"/.test(c.tag)).length,
      'no primary HeaderControl found, so the check below passes over nothing',
    ).toBeGreaterThan(8)
  })

  it('the TopBar slices actually contain the controls (vacuity floor)', () => {
    // 🪤 THE FLOOR THIS FILE WAS MISSING. Mutation-testing showed that breaking `topBars` to stop at
    // the first `/>` — losing every slot's contents — left the duplicate check GREEN, because empty
    // slices contain no labels and a list with no labels has no duplicates. A scope-narrowing bug in a
    // per-item rail does not fail; it reports clean.
    //
    // So: nearly every HeaderControl in the tree lives inside a TopBar, and this ties the in-header
    // count to the file-wide one. A truncating `topBars` collapses the ratio immediately.
    let inBars = 0
    for (const abs of walk(PAGES)) {
      const src = strip(readFileSync(abs, 'utf8'))
      for (const bar of topBars(src)) {
        inBars += [...bar.matchAll(/<HeaderControl\b/g)].length
      }
    }
    expect(
      inBars,
      `only ${inBars} of ${controls.length} HeaderControls were found inside a TopBar slice. The ` +
        `slicer is truncating, so every per-header check above is passing over nothing.`,
    ).toBeGreaterThan(controls.length * 0.7)
  })

  it('the two attributes never disagree', () => {
    // The confusing split: one control claims the colour, another claims the overflow priority. A
    // reader then cannot tell which action the page considers primary, and `HeaderActions` orders its
    // overflow by `priority` — so the control that LOOKS primary is not the one that survives a
    // narrow header.
    const split = controls
      .filter((c) => /variant="primary"/.test(c.tag) && !/priority="primary"/.test(c.tag))
      .map((c) => `${c.rel}: ${labelOf(c.tag) ?? '(unlabelled)'}`)
    expect(
      split,
      'these render as the primary action but do not declare primary priority, so a narrow header ' +
        'may fold away the coral control and keep a secondary one:\n  ' + split.join('\n  '),
    ).toEqual([])
  })

  it('no single header renders the same label twice', () => {
    // 🪤 THIS IS THE HALF THAT CAUGHT THE REAL BUG, AND THE UNIT MATTERS. `Browse` existed as a
    // `ModeToggle` segment in the header's `left` slot AND as a `HeaderControl` in its `right` slot,
    // both calling `onBrowse`.
    //
    // Scoped to ONE `<TopBar …/>`, not one file. A per-file check flagged `ChatPage`'s two identical
    // `New chat` controls and `CodeCockpitPage`'s two `All projects` — which are the same control in
    // two different render branches (a list view and a session view), not two controls in one header.
    // A file can render several headers; only what appears together is a duplicate.
    const dupes: string[] = []
    for (const abs of walk(PAGES)) {
      const src = strip(readFileSync(abs, 'utf8'))
      const rel = abs.slice(abs.indexOf('/pages/') + 7)
      for (const [i, bar] of topBars(src).entries()) {
        const labels = [...bar.matchAll(/<HeaderControl\b[\s\S]{0,400}?\/>/g)]
          .map((m) => labelOf(m[0]))
          .filter((l): l is string => !!l)
        // A `Segmented` option must carry a `key`; an `EmptyState` action or a menu row must not — so
        // anchoring on `key:` immediately before `label:` is what separates them. An earlier draft
        // matched any `label: '…'` and produced ELEVEN false positives.
        const segLabels = [...bar.matchAll(/\bkey:\s*'[^']+',\s*label:\s*'([^']+)'/g)].map((m) => m[1])
        const seen = new Set<string>()
        for (const l of labels) {
          if (seen.has(l)) dupes.push(`${rel} TopBar#${i + 1}: "${l}" twice as a HeaderControl`)
          seen.add(l)
          if (segLabels.includes(l)) {
            dupes.push(`${rel} TopBar#${i + 1}: "${l}" is both a HeaderControl and a Segmented option`)
          }
        }
      }
    }
    expect(
      dupes,
      'one label, two controls in the SAME header — a user cannot predict which does what:\n  ' +
        dupes.join('\n  '),
    ).toEqual([])
  })

  it("skills' create action now matches the ten siblings", () => {
    const src = readFileSync(join(PAGES, 'skills/SkillsPage.tsx'), 'utf8')
    expect(src, 'New skill must carry both attributes on one control').toMatch(
      /label="New skill" variant="primary" priority="primary"/,
    )
    expect(
      strip(src),
      'the duplicate Browse HeaderControl must stay deleted — ModeToggle owns that action and also ' +
        'shows which mode you are in, which a button cannot',
    ).not.toMatch(/<HeaderControl[^>]*label="Browse"/)
  })
})
