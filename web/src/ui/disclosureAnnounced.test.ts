// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'

// ── Eight disclosures that hid that they disclose — and a census that was wrong by 4× ────────
//
// An earlier pass found `#/settings/design`'s colour-editor button missing `aria-expanded` and logged it rather
// than smuggling it into a target-size diff. This is that follow-up, and the census WAS the work: **every
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
// This change therefore ships what it actually classified and drove — 14 of the 48 — and records the rest
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
// its own change; `aria-pressed` and `aria-expanded` answer different questions and one PR should not
// blur them.) **A census's value is the classification, not the count** — a rail asserting "every
// boolean-toggling button announces expansion" would have been wrong twice.
//
// Driven on the two routes whose disclosures need no setup, before vs after the change
// (`grep -c aria-expanded DesignPanel.tsx` = 0 before, 1 after):
//
//                        collapsed → expanded → collapsed        DOM nodes with it open
//   before   #/settings/design   null → null → null      🔴      845
//            #/tools             null → null → null      🔴      2316
//   after    #/settings/design   "false" → "true" → "false"  ✅  845   ← identical: attribute-only
//            #/tools             "false" → "true" → "false"  ✅  2316

const PAGES = join(process.cwd(), 'src', 'pages')
const read = (rel: string) => readSource(join(PAGES, rel))

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
      // negatives in earlier rails). Anchor on the toggle and read a fixed window after it.
      const at = src.indexOf(toggle)
      expect(at, `${rel} must still contain ${toggle}`).toBeGreaterThan(-1)
      const tag = src.slice(at, at + 260)
      // A raw button carries the attribute; a primitive (`QuietButton`, `Button`) takes it as
      // `ariaExpanded` and renders the same attribute.
      expect(tag, 'the attribute must be on the button itself').toMatch(/aria-expanded=\{|ariaExpanded=\{/)
      expect(tag, `and bound to \`${state}\` — the same flag its content is gated on`)
        .toMatch(new RegExp(`(?:aria-expanded|ariaExpanded)=\\{${state}\\}`))
    })

    it(`${rel} still gates its content on that same flag`, () => {
      // Guards the pairing from the other side: if the render moves to a different flag, the attribute
      // becomes a lie rather than merely stale.
      expect(read(rel)).toContain(`{${state} && `)
    })
  }

  it('the two canonical adopters still carry it', () => {
    expect(read('chat/ChatActivityPanel.tsx')).toMatch(/aria-expanded=\{open\}/)
    // The cockpit's finding card, which moved out of `CodeCockpitPage` into its own module.
    expect(read('code/cockpit/tasks/FindingCard.tsx')).toMatch(/aria-expanded=\{showEvidence\}/)
  })

  it("the cockpit's Terminal button is a disclosure, not a setting", () => {
    // It shows and hides the terminal drawer. `active` keeps its look, and `ariaExpanded` is what a
    // screen reader hears: `HeaderControl` drops `aria-pressed` once it is passed. This file's
    // prop-toggle census reads a file as announced only while it holds some `aria-expanded`, and on
    // the page that once held this toggle it was the finding card's, a different control. The
    // toggle now lives in the cockpit's header.
    expect(read('code/cockpit/header/CockpitHeader.tsx'))
      .toMatch(/label=\{showTerm \? 'Hide terminal' : 'Terminal'\} onClick=\{\(\) => setShowTerm\(!showTerm\)\} active=\{showTerm\} ariaExpanded=\{showTerm\}/)
  })

  it("the loop cockpit's Details button is a disclosure, not a setting", () => {
    // The same case as the Terminal button: it shows and hides the Details rail, which renders only
    // while `railOpen` holds. Pinned on its own tag because the census below could not tell: its window
    // reached back into the Nudge button beside it, whose `ariaExpanded={nudgeOpen}` read as this
    // toggle's announcement before it had one.
    const src = read('loops/LoopCockpitPage.tsx')
    expect(src).toMatch(/label="Details" active=\{railOpen\} ariaExpanded=\{railOpen\} onClick=\{\(\) => \{ setRailOpen\(!railOpen\); setSelected\(null\) \}\}/)
    expect(src, 'and the rail is gated on that same flag').toMatch(/\{railOpen && \(\s*<SidePanel/)
  })

  it("the chat's Activity button is a disclosure, not a setting", () => {
    // The same case again: it shows and hides the Activity pane, which renders only while
    // `activityOpen` holds. The census read it as announced while it had only `active`, because its
    // window reached back into the Session map button's `ariaExpanded={mapOpen}`. The census now
    // counts only a toggle's own attribute, and this pins the tag itself.
    expect(read('chat/header/ChatHeader.tsx'))
      .toMatch(/label="Activity" active=\{activityOpen\} ariaExpanded=\{activityOpen\} onClick=\{\(\) => setActivityOpen\(!activityOpen\)\}/)
    expect(read('chat/panes/ChatPanes.tsx'), 'and the pane is gated on that same flag')
      .toMatch(/\{activityOpen && started && \(\s*<SidePanel title="Activity"/)
  })

  it('a MODE toggle is not given a disclosure promise', () => {
    // The distinction the census turned up: these two reveal nothing, so `aria-expanded` would be false.
    const diag = read('settings/DiagnosticsPanel.tsx')
    const autoscroll = diag.slice(diag.indexOf('setAutoscroll((v) => !v)'), diag.indexOf('setAutoscroll((v) => !v)') + 240)
    expect(autoscroll, 'autoscroll flips a mode, it does not disclose').not.toMatch(/aria-expanded/)
    const paused = diag.slice(diag.indexOf('setPaused((v) => !v)'), diag.indexOf('setPaused((v) => !v)') + 240)
    expect(paused, 'pause/resume flips a mode too').not.toMatch(/aria-expanded/)
  })

  // 🔴 THIS REGEX'S KEY EXCLUDED A WHOLE SHAPE, and the exclusion was silent — the same failure mode
  // as the 400-character window in the census below, one level up. It matches a BOOLEAN FLIP
  // (`setX(!x)` / `setX(v => !v)`), so every ACCORDION — `setX(open ? null : id)`, one panel open at a
  // time — sat outside the 48 entirely. Four exist; three announced nothing until they were fixed, and
  // `NotificationRulesMatrix` had been doing it correctly all along. A second, independent sweep (the
  // exclusive-choice one in pages/settings) missed them too, for its own different reason.
  // **When a census defines membership by a WRITE pattern, enumerate the other ways the same
  // interaction is written.**
  // One-open-at-a-time (`setX(open ? null : id)`) stores an id, so there is no `!` here to match on;
  // that population is swept in the last describe of this file.
  //
  // 🔴 AND A GUARD IN FRONT OF THE SETTER HID ONE MORE, the lesson above for the fourth time. The
  // matcher wanted the setter straight after the arrow, so a flip written behind a condition —
  // `onClick={() => items.length > 1 && setExpanded((e) => !e)}`, the chat composer's queue header,
  // which is a toggle only while two or more messages wait — was never in the population. It shipped
  // `aria-expanded` without this census ever counting it. The optional `cond && ` prefix takes that
  // shape in; its class stops at `{`, `}`, `&` and a line break, so the guard cannot run past the
  // handler's own closing brace into the next attribute.
  //
  // 🔴 AND A BLOCK BODY HID ANOTHER, the fifth time. The setter still had to follow the arrow, so a
  // flip written as a statement — `onClick={() => { setRailOpen(!railOpen); setSelected(null) }}`, the
  // loop cockpit's Details button, which shows and hides its rail — was never counted, and it announced
  // a pressed toggle where a rail appeared. The optional `{` opener takes that shape in, with any
  // `;`-ended statements before the flip; their class stops at `{`, `}`, `;` and a line break, so it
  // cannot leave the block either.
  const TOGGLE = /onClick=\{\(\) => (?:\{\s*(?:[^{}\n;]+;\s*)*)?(?:[^&{}\n]+&& )?set\w+\(\(?\w*\)? ?=> ?!\w+\)|onClick=\{\(\) => (?:\{\s*(?:[^{}\n;]+;\s*)*)?(?:[^&{}\n]+&& )?set\w+\(!\w+\)/g

  it('the old matcher is BLIND to a guarded flip — the gap is structural', () => {
    // Synthetic and exact: the first line is the one that sat outside the census since it was written.
    const OLD = /onClick=\{\(\) => set\w+\(\(?\w*\)? ?=> ?!\w+\)|onClick=\{\(\) => set\w+\(!\w+\)/
    const sees = (shape: string) => { TOGGLE.lastIndex = 0; return TOGGLE.test(shape) }
    for (const shape of [
      'onClick={() => items.length > 1 && setExpanded((e) => !e)}',
      'onClick={() => !busy && setOpen(!open)}',
    ]) {
      expect(OLD.test(shape), `the old matcher cannot see ${shape}`).toBe(false)
      expect(sees(shape), `and this one does: ${shape}`).toBe(true)
    }
    // Widened, not changed: the unguarded flips it was written for still match.
    expect(sees('onClick={() => setOpen((v) => !v)}')).toBe(true)
    expect(sees('onClick={() => setOpen(!open)}')).toBe(true)
    // And the guard stays inside its own handler — a `&&` in a later attribute is not a guard.
    expect(sees('onClick={() => run()} data-x={ready && setOpen(!open)}')).toBe(false)
    TOGGLE.lastIndex = 0
  })

  it('the guarded matcher is BLIND to a block-bodied flip — the gap is structural', () => {
    // Synthetic and exact: the first line is the one that sat outside the census since it was written.
    const GUARDED = /onClick=\{\(\) => (?:[^&{}\n]+&& )?set\w+\(\(?\w*\)? ?=> ?!\w+\)|onClick=\{\(\) => (?:[^&{}\n]+&& )?set\w+\(!\w+\)/
    const sees = (shape: string) => { TOGGLE.lastIndex = 0; return TOGGLE.test(shape) }
    for (const shape of [
      'onClick={() => { setRailOpen(!railOpen); setSelected(null) }}',
      'onClick={() => { setOpen((v) => !v) }}',
      'onClick={() => {\n  setOpen(!open)\n}}',
      'onClick={() => { setSelected(null); setRailOpen(!railOpen) }}',
      'onClick={() => { !busy && setOpen(!open) }}',
    ]) {
      expect(GUARDED.test(shape), `the guarded matcher cannot see ${shape}`).toBe(false)
      expect(sees(shape), `and this one does: ${shape}`).toBe(true)
    }
    // Widened, not changed: the unguarded and guarded flips still match.
    expect(sees('onClick={() => setOpen((v) => !v)}')).toBe(true)
    expect(sees('onClick={() => setOpen(!open)}')).toBe(true)
    expect(sees('onClick={() => items.length > 1 && setExpanded((e) => !e)}')).toBe(true)
    expect(sees('onClick={() => !busy && setOpen(!open)}')).toBe(true)
    // A block that sets without flipping is not a toggle, and the block stays inside its own handler.
    expect(sees('onClick={() => { setOpen(true); setSelected(null) }}')).toBe(false)
    expect(sees('onClick={() => { run() }} onBlur={() => { reset(); setOpen(!open) }}')).toBe(false)
    TOGGLE.lastIndex = 0
  })

  // 🔴 AND THE SILENT CHECK READ A NEIGHBOUR'S ATTRIBUTE. It took a window around each toggle and
  // called the toggle announced if ANY `aria-expanded`/`aria-pressed` sat in it, so a control beside
  // one that announces counted as announcing too. Two did: the loop cockpit's Details button, by the
  // Nudge button's `ariaExpanded={nudgeOpen}`, and the chat's Activity button, by the Session map
  // button's `ariaExpanded={mapOpen}`. Both showed and hid a pane and said only "pressed". The check
  // is now bound: the attribute must read the state the setter flips (`setRailOpen` → `railOpen`).
  //
  // A state can reach the attribute through a name derived from it. The chat composer's queue header
  // flips `expanded` and announces `aria-expanded={items.length > 1 ? !stacked : undefined}`, where
  // `const stacked = !reduce && items.length > 1 && !expanded`. `stacked` is the right state to read:
  // under reduced motion the list is always flat, and `expanded` would claim a collapse that is not
  // there. So a `const` written from the flipped state counts as that state, and a check bound to the
  // name alone would wrongly call the queue header silent.

  /** Each toggle in `src`: the flip it matched, and the markup around it, anchored on the toggle. */
  const togglesIn = (src: string) => [...src.matchAll(TOGGLE)].map((m) => ({
    flip: m[0], around: src.slice(Math.max(0, m.index! - 200), m.index! + 260),
  }))

  /** The check this replaced: any announcing attribute in the window. */
  const ANY_ATTRIBUTE = /aria-expanded|aria-pressed|ariaExpanded|ariaPressed/

  /** Does the toggle carry an attribute that reads the state its flip writes? The state is the
   *  setter's own (`setRailOpen` → `railOpen`), plus the name a `setX(!y)` negates, plus any `const`
   *  in `src` written from one of those (`const stacked = … && !expanded`). A primitive takes the
   *  state as `ariaExpanded`/`ariaPressed` and renders the same attribute. */
  function announcesOwnState({ flip, around }: { flip: string; around: string }, src: string): boolean {
    const [, setter, negated] = /set(\w+)\((?:\(?\w*\)? ?=> ?!\w+|!(\w+))\)$/.exec(flip)!
    const states = new Set([setter[0].toLowerCase() + setter.slice(1), ...(negated ? [negated] : [])])
    const reads = (expr: string) => [...states].some((s) => new RegExp(`\\b${s}\\b`).test(expr))
    for (const [, name, expr] of src.matchAll(/\bconst (\w+) = ([^\n]+)/g)) if (reads(expr)) states.add(name)
    return [...around.matchAll(/(?:aria-expanded|aria-pressed|ariaExpanded|ariaPressed)=\{([^{}]*)\}/g)]
      .some(([, expr]) => reads(expr))
  }

  it("the window check credits a neighbour's attribute — the bound check does not", () => {
    // Synthetic and exact: the Session map and Activity buttons as the chat header had them.
    const before = [
      '<HeaderControl icon={ListTree} label="Session map" ariaExpanded={mapOpen} onClick={() => setMapOpen(!mapOpen)} />',
      '<HeaderControl icon={PanelRight} label="Activity" active={activityOpen} onClick={() => setActivityOpen(!activityOpen)} />',
    ].join('\n')
    const [map, activity] = togglesIn(before)
    expect(ANY_ATTRIBUTE.test(activity.around), "the window check reads Map's attribute as Activity's").toBe(true)
    expect(announcesOwnState(activity, before), 'the bound check does not').toBe(false)
    expect(announcesOwnState(map, before), 'and still credits Map with its own').toBe(true)
    // Its own attribute counts, in either spelling and either flip.
    const after = before.replace('active={activityOpen}', 'active={activityOpen} ariaExpanded={activityOpen}')
    expect(announcesOwnState(togglesIn(after)[1], after)).toBe(true)
    const arrow = '<button onClick={() => setRailOpen((v) => !v)} aria-pressed={railOpen}>'
    expect(announcesOwnState(togglesIn(arrow)[0], arrow)).toBe(true)

    // A derived state counts, as the queue header writes it.
    const queue = [
      'const stacked = !reduce && items.length > 1 && !expanded',
      '<button type="button" onClick={() => items.length > 1 && setExpanded((e) => !e)}',
      '  aria-expanded={items.length > 1 ? !stacked : undefined}>',
    ].join('\n')
    expect(/\bexpanded\b/.test('items.length > 1 ? !stacked : undefined'),
      'bound to the name alone, the queue header would read as silent').toBe(false)
    expect(announcesOwnState(togglesIn(queue)[0], queue), 'bound through the derivation, it announces').toBe(true)
    // But a name derived from a neighbour's state is still the neighbour's.
    const shown = `const mapShown = started && mapOpen\n${before.replace('ariaExpanded={mapOpen}', 'ariaExpanded={mapShown}')}`
    expect(announcesOwnState(togglesIn(shown)[1], shown)).toBe(false)
  })

  it('the census is reproducible — every boolean-toggling button is accounted for', () => {
    // Not vacuous, and the number is the point: if a new toggle appears it must be classified (disclosure
    // → `aria-expanded`, mode → `aria-pressed` or a state-naming label), not left silent by default.
    const walk = (d: string): string[] => filesUnder(d, (n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))
    // 🪤 FIFTH WINDOW FAILURE OF THE SESSION, and the funniest: the first version matched
    // `<button …</button>` within 400 chars, so ADDING `aria-expanded` pushed two buttons past the
    // window and the census fell from 12 to 10 — the fix shrank its own population. Anchor on the
    // TOGGLE (which is what defines the population) and look for the attribute around it.
    const toggles = walk(PAGES).flatMap((abs) => {
      const src = readSource(abs)
      return togglesIn(src).map((t) => ({ ...t, announced: announcesOwnState(t, src) }))
    })
    // 58 measured when the guard went in: 57 unguarded flips (the tree grew past the 48 this floor once
    // held, and the floor never followed) plus the queue header. 59 when the block body went in: the
    // Details button is the only block-bodied flip in `pages/`. A measured population, so it sits at
    // the measurement.
    expect(toggles.length, 'the scan must still find the population it was written for').toBeGreaterThanOrEqual(59)
    const queue = toggles.find((t) => t.around.includes('items.length > 1 && setExpanded((e) => !e)'))
    expect(queue, 'including the guarded flip that widened the matcher').toBeDefined()
    expect(queue!.announced, 'which announces through the state derived from its flip').toBe(true)
    expect(toggles.some((t) => t.around.includes('{ setRailOpen(!railOpen); setSelected(null) }')),
      'and the block-bodied flip that widened it again').toBe(true)
    const silent = toggles.filter((t) => !t.announced)
    // A CEILING on the backlog, not a claim that it is empty: 34 measured when it was written, and it may
    // only fall. A new toggle that announces nothing pushes this over and has to be classified before
    // it lands. 9 once the primitives' `ariaExpanded`/`ariaPressed` counted: 24 of the 33 "silent"
    // toggles were primitives that announce, which the raw-attribute regex could not see. 8 when the
    // guard went in, and the slack of one was closed with it: the ceiling had sat at 9 against a
    // measured 8, and the guarded flip it newly counts already announces, so widening added nothing here.
    // Still 8 when the block body went in, and that hid a silent toggle rather than proving none: this
    // window reads 200 characters back, which reaches a neighbouring control's attribute. The Details
    // button counted as announced because of the Nudge button's `ariaExpanded={nudgeOpen}` beside it,
    // so it was classified on its own tag (pinned above) and now announces its own rail.
    // 9 when the check was bound to the toggle's own state, which closed that gap: the Activity button
    // had counted as announced by the Session map button's `ariaExpanded={mapOpen}`. 8 again once
    // Activity, a disclosure, announced its own pane (pinned above).
    expect(silent.length, `measured backlog — classify a new toggle, do not add to this number:\n${
      silent.map((t) => t.flip).join('\n')}`).toBeLessThanOrEqual(8)
  })
})

describe('the accordions the boolean-flip census could not see', () => {
  // Membership here is the OTHER way this interaction gets written: `setX(open ? null : id)`, one panel
  // open at a time. Four in the tree; three were silent. This is not a new family — it is the same
  // family under a write pattern the original matcher's key excluded, which is why it lives in this file
  // rather than growing a second ledger somewhere else.
  const ACCORDION = /onClick=\{\(\) => set\w+\(\s*\w+ \? null : [\w.]+\s*\)/g

  /** The file's other `walk` is scoped inside its own test, so this describe carries one. */
  const walkPages = (d: string): string[] => filesUnder(d, (n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))

  /** Does the markup around a toggle actually publish an expanded state?
   *
   *  🪤 TWO SPELLINGS, AND ONLY ONE IS VALID PER ELEMENT TYPE. A raw `<button>` takes the DOM
   *  attribute `aria-expanded`. `ui/Button` declares camelCase props (`ariaExpanded`) and spreads no
   *  rest, so on IT the hyphenated form is silently DROPPED — TypeScript does not excess-property-check
   *  a JSX attribute whose name contains a dash, so it compiles and reaches nothing. Measured on
   *  `#/settings/notifications`: 27 triggers with no `aria-expanded` in the DOM at all, every one of
   *  them written `aria-expanded={isOpen}` in the source. `ui/ariaPropForwarding.test.tsx` now forbids
   *  that spelling on our components.
   *
   *  Accepting `ariaExpanded` ONLY beside a `<Button` keeps this strict rather than merely permissive:
   *  on a raw `<button>` that spelling vanishes just as quietly, and must still count as silent. That
   *  distinction is asserted directly below, because nothing in the tree exercises it today — a guard
   *  whose strictness is untested is a guard that is only claimed. */
  function announcesDisclosure(around: string): boolean {
    return /aria-expanded/.test(around)
      || (/ariaExpanded=/.test(around) && /<Button\b/.test(around))
  }

  it('the two spellings are accepted on the right elements, and only there', () => {
    // Synthetic, because the tree contains no counter-example — which is exactly why this is here.
    expect(announcesDisclosure('<Button onClick={() => setOpen(open ? null : id)} ariaExpanded={isOpen}>'),
      'camelCase on ui/Button reaches the DOM').toBe(true)
    expect(announcesDisclosure('<button onClick={() => setOpen(open ? null : id)} aria-expanded={isOpen}>'),
      'the DOM attribute on a raw button').toBe(true)
    expect(announcesDisclosure('<button onClick={() => setOpen(open ? null : id)} ariaExpanded={isOpen}>'),
      'camelCase on a RAW button is dropped by React — still silent').toBe(false)
    expect(announcesDisclosure('<Button onClick={() => setOpen(open ? null : id)}>detail</Button>'),
      'and a Button that says nothing is still silent').toBe(false)
  })

  function accordions() {
    const out: { rel: string; announced: boolean }[] = []
    for (const abs of walkPages(PAGES)) {
      const src = readSource(abs).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      for (const m of src.matchAll(ACCORDION)) {
        // Anchored on the toggle, not on tag boundaries — the lesson this file already carries.
        const around = src.slice(Math.max(0, m.index! - 300), m.index! + 320)
        out.push({ rel: abs.slice(PAGES.length + 1), announced: announcesDisclosure(around) })
      }
    }
    return out
  }

  it('finds the population (not vacuously green)', () => {
    const found = accordions()
    expect(found.length, 'the accordion scan must find its population').toBeGreaterThanOrEqual(4)
    // And it must still see the one that was correct before this change — the precedent, not an invention.
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
      // The tool-call group's accordion, which moved with the chat's transcript region.
      ['chat/transcript/AssistantSegments.tsx', 'open'],
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

// ── 2026-08-19: the THIRD write pattern — a toggle handed DOWN as a prop ───────────────────────────
//
// This file already carries the lesson twice: **when a census defines membership by a WRITE pattern,
// enumerate the other ways the same interaction is written.** Here is the next instance, found by
// reviewing `#/settings/archive` rather than by re-reading the matcher.
//
// `ArchivePanel` opens a transcript with
//
//   onToggle={() => setOpen(open === a.name ? null : a.name)}     ← the parent
//   <button type="button" onClick={onToggle}>                     ← the child, in the same file
//
// Both earlier matchers key on `onClick={() => setX(…)}`, so BOTH are structurally blind to it:
//   · the boolean-flip census wants `!x` — this is an accordion, so there is no `!`;
//   · the accordion census wants `onClick=` and a bare `\w+ ? null :` — this is `onToggle=` with an
//     `open === a.name` condition.
// Measured on the live surface with three archives seeded: `aria-expanded` read **null collapsed and
// null expanded**, while `AuditPanel`'s structurally identical event row — a card whose button reveals
// detail beneath it, in the same settings area — has declared `aria-expanded={open}` all along.
//
// 🔑 THE GAP IS PINNED, NOT JUST CLOSED. The test below asserts the two older regexes do NOT match this
// shape, so the hole is documented as structural rather than accidental — and nobody can "simplify" the
// three matchers into one that quietly re-opens it.
//
// 🔑 THE POPULATION IS 23, AND MOST OF IT IS NOT THIS FAMILY. Several prop-passed toggles are FILTER
// selections (`KnowledgeListPage`'s four curation filters write `setCurationFilter(x === 'read' ? '' :
// 'read')` — the same syntax, a different act) and they must NOT take `aria-expanded`: nothing is
// revealed, so the promise would be false. That is the DiagnosticsPanel distinction again, and it is why
// this describe carries a classified table plus a ceiling rather than a blanket assertion.

describe('the disclosures whose toggle arrives as a PROP', () => {
  /** [file, the flag the child announces, the parent's prop-passed toggle]. */
  const PROP_DISCLOSURES: [string, string, string][] = [
    ['settings/ArchivePanel.tsx', 'open', 'onToggle={() => setOpen(open === a.name ? null : a.name)}'],
  ]

  for (const [rel, flag, toggle] of PROP_DISCLOSURES) {
    it(`${rel} announces expansion on the child that owns the button`, () => {
      const src = read(rel)
      expect(src, 'the parent must still hand the toggle down').toContain(toggle)
      // 🪤 NOT a window around the toggle: the button lives in a SIBLING component further down the
      // file, so any adjacency window either misses it or spans unrelated markup. Two independent facts
      // about the same flag is the check that cannot rot — the form this file already settled on.
      expect(src, `the child's button must announce ${flag}`).toMatch(new RegExp(`aria-expanded=\\{${flag}\\}`))
      expect(src, `and ${flag} must be what reveals the content`).toMatch(new RegExp(`\\{${flag} && [(<]`))
    })
  }

  /** A toggle handed to a child: `onAnything={() => setX(<a flip, an accordion, or a clear>)}`. */
  const PROP_TOGGLE = /\bon[A-Z]\w*=\{\(\) => \{?\s*set(\w+)\(([^)]*)\)/g

  it('the two older matchers are BLIND to this shape — the gap is structural', () => {
    // Synthetic and exact: this is the line that shipped silent for as long as both censuses existed.
    const shape = "onToggle={() => setOpen(open === a.name ? null : a.name)}"
    expect(/onClick=\{\(\) => set\w+\(\(?\w*\)? ?=> ?!\w+\)|onClick=\{\(\) => set\w+\(!\w+\)/.test(shape),
      'the boolean-flip census cannot see it (no `!`, and not onClick=)').toBe(false)
    expect(/onClick=\{\(\) => set\w+\(\s*\w+ \? null : [\w.]+\s*\)/.test(shape),
      'the accordion census cannot see it either (onToggle=, and a `===` condition)').toBe(false)
    expect(PROP_TOGGLE.test(shape), 'and this one does').toBe(true)
    PROP_TOGGLE.lastIndex = 0
  })

  const walkPages = (d: string): string[] => filesUnder(d, (n) => /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n))

  function propToggles() {
    const out: { rel: string; state: string; announced: boolean }[] = []
    for (const abs of walkPages(PAGES)) {
      const src = readSource(abs).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      for (const m of src.matchAll(PROP_TOGGLE)) {
        const arg = m[2]
        if (!/!\w+/.test(arg) && !/\?\s*null\s*:/.test(arg) && !/\?\s*''\s*:/.test(arg)) continue
        // File-level, and deliberately so: the receiving button is a sibling component (sometimes in
        // another file), so there is no window that can hold both ends. Coarse on purpose — it is a
        // CEILING, not a per-site verdict, and the classified table above carries the per-site proof.
        out.push({ rel: abs.slice(PAGES.length + 1), state: m[1], announced: /aria-expanded|ariaExpanded=/.test(src) })
      }
    }
    return out
  }

  it('finds the population (not vacuously green)', () => {
    const found = propToggles()
    expect(found.length, 'the prop-toggle scan must find its population').toBeGreaterThanOrEqual(23)
    expect(found.map((t) => t.rel), 'including the one this change drove').toContain('settings/ArchivePanel.tsx')
  })

  it('the silent remainder is a measured ceiling, and may only fall', () => {
    // **9 today** — the number is measured, and this assertion is how I learned my own count of it was
    // wrong (I wrote 8 from reading a scan by eye; the rail said 9 and named the ninth). Five of the nine
    // are `KnowledgeListPage`'s curation filters, which are selections and must NOT take
    // `aria-expanded`; the rest need classifying — disclosure → `aria-expanded`, filter/selection →
    // `aria-pressed` or a state-naming label — before any of them can be called done. A NEW silent prop
    // toggle pushes this over, which is the point: the default for an unclassified one is "not yet
    // reviewed", not "fine".
    const silent = propToggles().filter((t) => !t.announced)
    expect(silent.length, `classify a new prop toggle, do not add to this number:\n${
      silent.map((s) => `${s.rel} set${s.state}`).join('\n')}`).toBeLessThanOrEqual(9)
  })
})
