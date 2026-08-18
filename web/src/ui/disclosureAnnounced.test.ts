import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Eight disclosures that hid that they disclose — and a census that was wrong by 4× ────────
//
// Cycle 127 found `#/settings/design`'s colour-editor button missing `aria-expanded` and logged it rather
// than smuggling it into a target-size diff. This is that cycle, and the census WAS the work: **every
// button whose click flips a boolean** — `setX((v) => !v)` / `setX(!x)` — anywhere in `pages/`.
//
// 🪤 MY FIRST CENSUS SAID 12. THE TRUE NUMBER IS 48. It matched `<button …</button>` within 400
// characters, so every toggle whose button markup runs longer than that was invisible — and the miss was
// silent, because a scan that finds *some* results looks like it worked. The tell arrived by accident:
// after adding the attribute to 8 sites the count DROPPED from 12 to 10, because the new attribute pushed
// two buttons past the window. **A census that shrinks when you fix things was never measuring the
// population.** Re-anchored on the TOGGLE itself (which is what defines membership) rather than on tag
// boundaries: **48 toggles, 34 still silent.**
//
// This cycle therefore ships what it actually classified and drove — 14 of the 48 — and records the rest
// as a measured backlog rather than pretending to have closed the family:
//
//   8   disclosures, fixed here (each verified to gate adjacent content on the same flag)
//   2   already announced — the canonical form this converges on
//   2   `DiagnosticsPanel` MODE toggles, deliberately silent (they reveal nothing)
//   34  unclassified, queued — some will be disclosures, some modes, some neither
//
// 🔑 THE TWO THAT ALREADY SHIPPED IT ARE THE PRECEDENT, not an invention:
//   `chat/ChatActivityPanel.tsx:211`   `aria-expanded={open}`  → `{open && <Markdown/>}`
//   `code/CodeCockpitPage.tsx:3127`    `aria-expanded={showEvidence}` (+ a state-named `aria-label`)
// Neither uses `aria-controls`, so these eight don't either — inventing ids for a pattern the repo has
// never needed would be speculative API, not convergence.
//
// 🔑 AND TWO OF THE TEN ARE A REAL DISTINCTION, deliberately left alone. `DiagnosticsPanel`'s autoscroll
// and pause buttons flip a MODE, not the visibility of adjacent content — nothing is revealed, so
// `aria-expanded` would be a false promise. (Autoscroll is a separate finding: its `title` names the
// STATE — "Autoscroll on" — and its only other cue is a coral tint, so it wants `aria-pressed`. Logged for
// its own cycle; `aria-pressed` and `aria-expanded` answer different questions and one PR should not
// blur them.) **A census's value is the classification, not the count** — a rail asserting "every
// boolean-toggling button announces expansion" would have been wrong twice.
//
// Driven on the two routes whose disclosures need no setup, parent worktree vs this one
// (`grep -c aria-expanded DesignPanel.tsx` = 0 there, 1 here):
//
//                        collapsed → expanded → collapsed        DOM nodes with it open
//   before   #/settings/design   null → null → null      🔴      845
//            #/tools             null → null → null      🔴      2316
//   after    #/settings/design   "false" → "true" → "false"  ✅  845   ← identical: attribute-only
//            #/tools             "false" → "true" → "false"  ✅  2316

const PAGES = join(process.cwd(), 'src', 'pages')
const read = (rel: string) => readFileSync(join(PAGES, rel), 'utf8')

/** Each disclosure: [file, the state it is gated on, a fragment of its own button]. */
const DISCLOSURES: [string, string, string][] = [
  ['agents/AgentDetail.tsx', 'open', 'setOpen((v) => !v)'],
  ['apps/AppsSection.tsx', 'advancedOpen', 'setAdvancedOpen((o) => !o)'],
  ['files/browse/FilePreviews.tsx', 'open', 'setOpen(!open)'],
  ['loops/LoopCockpitPage.tsx', 'open', 'setOpen((v) => !v)'],
  ['schedule/ScheduleForm.tsx', 'open', 'setOpen((v) => !v)'],
  ['settings/DesignPanel.tsx', 'editingColors', 'setEditingColors((v) => !v)'],
  ['tools/ToolInspector.tsx', 'open', 'setOpen((v) => !v)'],
  ['tools/ToolsPage.tsx', 'open', 'setOpen((v) => !v)'],
]

describe('a button that reveals content says that it does', () => {
  for (const [rel, state, toggle] of DISCLOSURES) {
    it(`${rel} announces its expanded state`, () => {
      const src = read(rel)
      // 🪤 Scan to the CLOSING `>` of the tag is unsafe — `onClick={() => …}` contains one (four false
      // negatives in this session). Anchor on the toggle and read a fixed window after it.
      const at = src.indexOf(toggle)
      expect(at, `${rel} must still contain ${toggle}`).toBeGreaterThan(-1)
      const tag = src.slice(at, at + 260)
      expect(tag, 'the attribute must be on the button itself').toMatch(/aria-expanded=\{/)
      expect(tag, `and bound to \`${state}\` — the same flag its content is gated on`)
        .toContain(`aria-expanded={${state}}`)
    })

    it(`${rel} still gates its content on that same flag`, () => {
      // Guards the pairing from the other side: if the render moves to a different flag, the attribute
      // becomes a lie rather than merely stale.
      expect(read(rel)).toContain(`{${state} && `)
    })
  }

  it('the two canonical adopters still carry it', () => {
    expect(read('chat/ChatActivityPanel.tsx')).toMatch(/aria-expanded=\{open\}/)
    expect(read('code/CodeCockpitPage.tsx')).toMatch(/aria-expanded=\{showEvidence\}/)
  })

  it('a MODE toggle is not given a disclosure promise', () => {
    // The distinction the census turned up: these two reveal nothing, so `aria-expanded` would be false.
    const diag = read('settings/DiagnosticsPanel.tsx')
    const autoscroll = diag.slice(diag.indexOf('setAutoscroll((v) => !v)'), diag.indexOf('setAutoscroll((v) => !v)') + 240)
    expect(autoscroll, 'autoscroll flips a mode, it does not disclose').not.toMatch(/aria-expanded/)
    const paused = diag.slice(diag.indexOf('setPaused((v) => !v)'), diag.indexOf('setPaused((v) => !v)') + 240)
    expect(paused, 'pause/resume flips a mode too').not.toMatch(/aria-expanded/)
  })

  it('the census is reproducible — every boolean-toggling button is accounted for', () => {
    // Not vacuous, and the number is the point: if a new toggle appears it must be classified (disclosure
    // → `aria-expanded`, mode → `aria-pressed` or a state-naming label), not left silent by default.
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    // 🪤 FIFTH WINDOW FAILURE OF THE SESSION, and the funniest: the first version matched
    // `<button …</button>` within 400 chars, so ADDING `aria-expanded` pushed two buttons past the
    // window and the census fell from 12 to 10 — the fix shrank its own population. Anchor on the
    // TOGGLE (which is what defines the population) and look for the attribute around it.
    // 🔴 THIS REGEX'S KEY EXCLUDED A WHOLE SHAPE, and the exclusion was silent — the same failure mode
    // as the 400-character window above, one level up. It matches a BOOLEAN FLIP
    // (`setX(!x)` / `setX(v => !v)`), so every ACCORDION — `setX(open ? null : id)`, one panel open at a
    // time — sat outside the 48 entirely. Four exist; three announced nothing until they were fixed, and
    // `NotificationRulesMatrix` had been doing it correctly all along. A second, independent sweep (the
    // exclusive-choice one in pages/settings) missed them too, for its own different reason.
    // **When a census defines membership by a WRITE pattern, enumerate the other ways the same
    // interaction is written.**
    const TOGGLE = /onClick=\{\(\) => set\w+\(\(?\w*\)? ?=> ?!\w+\)|onClick=\{\(\) => set\w+\(!\w+\)/g
    // One-open-at-a-time (`setX(open ? null : id)`) stores an id, so there is no `!` here to match on;
    // that population is swept in the last describe of this file.
    const toggles = walk(PAGES).flatMap((abs) => {
      const src = readFileSync(abs, 'utf8')
      return [...src.matchAll(TOGGLE)].map((m) => src.slice(Math.max(0, m.index! - 200), m.index! + 260))
    })
    expect(toggles.length, 'the scan must still find the population it was written for').toBeGreaterThanOrEqual(48)
    const silent = toggles.filter((w) => !/aria-expanded|aria-pressed/.test(w))
    // A CEILING on the backlog, not a claim that it is empty: 34 measured today, and it may only fall.
    // A new toggle that announces nothing pushes this over and has to be classified before it lands.
    expect(silent.length, 'measured backlog — classify a new toggle, do not add to this number')
      .toBeLessThanOrEqual(34)
  })
})

describe('the accordions the boolean-flip census could not see', () => {
  // Membership here is the OTHER way this interaction gets written: `setX(open ? null : id)`, one panel
  // open at a time. Four in the tree; three were silent. This is not a new family — it is the same
  // family under a write pattern the original matcher's key excluded, which is why it lives in this file
  // rather than growing a second ledger somewhere else.
  const ACCORDION = /onClick=\{\(\) => set\w+\(\s*\w+ \? null : [\w.]+\s*\)/g

  /** The file's other `walk` is scoped inside its own test, so this describe carries one. */
  const walkPages = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walkPages(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  function accordions() {
    const out: { rel: string; announced: boolean }[] = []
    for (const abs of walkPages(PAGES)) {
      const src = readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      for (const m of src.matchAll(ACCORDION)) {
        // Anchored on the toggle, not on tag boundaries — the lesson this file already carries.
        const around = src.slice(Math.max(0, m.index! - 300), m.index! + 320)
        out.push({ rel: abs.slice(PAGES.length + 1), announced: /aria-expanded/.test(around) })
      }
    }
    return out
  }

  it('finds the population (not vacuously green)', () => {
    const found = accordions()
    expect(found.length, 'the accordion scan must find its population').toBeGreaterThanOrEqual(4)
    // And it must still see the one that was correct before this cycle — the precedent, not an invention.
    expect(found.map((a) => a.rel)).toContain('settings/NotificationRulesMatrix.tsx')
  })

  it('every one of them announces that it discloses', () => {
    const silent = accordions().filter((a) => !a.announced).map((a) => a.rel)
    expect(silent, `an accordion that reveals a panel and says nothing:\n${silent.join('\n')}`).toEqual([])
  })

  it('each fixed one still gates content on the same flag it announces', () => {
    // The classification criterion this file insists on: `aria-expanded` is a promise that something is
    // revealed. A mode toggle that reveals nothing must NOT take it (see the DiagnosticsPanel note).
    // 🪤 Deliberately NOT an adjacency window (`aria-expanded=...[\s\S]{0,500}...{flag && (`): the
    // distance between a toggle and the content it gates is arbitrary, and a window that happens to span
    // it today silently stops asserting when a child is added. Two independent facts about the SAME flag
    // name is the check that cannot rot.
    const gated: [string, string][] = [
      ['ChatPage.tsx', 'open'],
      ['prompts/SyntaxReference.tsx', 'open'],
      ['schedule/ScheduleDetail.tsx', 'expanded'],
    ]
    for (const [rel, flag] of gated) {
      const src = read(rel)
      expect(src, `${rel}: the toggle must announce ${flag}`).toMatch(new RegExp(`aria-expanded=\\{${flag}\\}`))
      // `{flag && (` and `{flag && <X/>}` are both used in this tree — assert the gate, not its bracket.
      expect(src, `${rel}: and ${flag} must be what reveals the content`).toMatch(new RegExp(`\\{${flag} && [(<]`))
    }
  })
})
