import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ListRow } from './ListScaffold'

// ── The row's hit target is a SIBLING of its content, never an ancestor ──────────────
//
// `ListRow` used to put `role="button" tabIndex={0}` on the wrapper. A row that carries its
// own controls then becomes `nested-interactive` (axe, serious): AT is told "one button" and
// finds a checkbox and three tag filters inside it. Measured: 60 nodes — knowledge 26,
// workflows 34. After this change both are 0, with no new violations.
//
// 🔑 WHY AN EMPTY OVERLAY AND NOT `pointer-events` ON THE CHILDREN. The obvious fix keeps the
// wrapper interactive and re-exposes its descendants with
// `[&_button]:pointer-events-auto`-style selectors. That was built in cycle 46 and REVERTED,
// because it has to ENUMERATE every control type and silently misses the conditional ones:
//
//   · workflows' delete button only exists in its `armed` state (`armed === r.id ? … : …`)
//   · workflows' second delete is gated on `d.source !== 'bundled'`
//   · knowledge's tag filters are gated on `tags?.length` AND `hidden md:flex`
//
// A fix that passes on every row you can see and breaks the one you cannot. The overlay owns
// NO descendants, so there is nothing to enumerate and nothing to miss — verified live,
// including arming the workflows delete.
//
// The click still fires through the WRAPPER's onClick: every nested control already calls
// stopPropagation for itself (`ui/forms.tsx`'s Checkbox does it on both onClick and onChange;
// the tag/run/delete buttons do it inline), so bubbling was already the contract.

// ── Cycle 159: THE TASKS LIST HAND-ROLLS ITS ROWS, AND OPENING ONE WAS POINTER-ONLY ──────────────
//
// `TasksListPage` does not use `ListRow`; it builds its own `motion.div` with `onClick={onOpen}`. So the
// idiom above never reached it, and the surface's PRIMARY action had no keyboard equivalent — WCAG 2.1.1.
// Measured with the keyboard alone across 30 rows, at 1440×900:
//
//   the only tab stop inside a row     its 24px "Select: <title>" checkbox
//   Enter / Space there                toggles SELECTION, never opens
//   Shift+F10                          opens nothing (the ContextMenu is right-click only)
//   Tab onward + Enter                 reaches the PROJECT CHIP → navigates to the project, not the task
//
// 🔑 FOUR CLEAN axe PASSES MISSED IT, and that is the lesson: a `div` with an `onclick` and no `role` is
// invisible to every rule. `#/tasks` had just measured 0 blocking findings at both themes × both
// viewports, and its four switcher views were clean too. **"Can this be done with the keyboard?" is not
// a question a scanner asks** — it has to be driven.
//
// The fix is this file's own idiom, copied verbatim into the list row and the card: an EMPTY
// `absolute inset-0 -z-10` sibling button carrying `aria-label={t.title}`, `tabIndex={-1}` on the
// wrapper, no role on the wrapper, and the ring drawn on the row via `:has(> button:focus-visible)`.
//
// After, measured the same way:
//
//   hash                     `#/tasks` → `#/tasks?open=t-8cf48814`, focus on a control named "triage"
//   tab stops per LIST row   exactly 2 — the overlay, then the select checkbox (no Framer double stop)
//   tab stops per CARD       exactly 1
//   axe serious/critical     0 in both views × both themes (no `nested-interactive`)
//   focus ring               the focused row gains `oklab(…/0.5) 0 0 0 2px inset`; an unfocused
//                            sibling has NO visible layer
//   captures                 4/4 desktop and 2/2 phone identical — the overlay is invisible at rest
//
// 🪤 The ring check had to read the FULL computed shadow: Tailwind's ring is a multi-layer box-shadow
// whose first layer is `rgba(0,0,0,0)`, so a truncated read looks like no ring at all.

describe('ListRow hit target', () => {
  it('is a real <button>, not a role=button div', () => {
    const { container } = render(
      <ListRow onClick={() => {}} label="Deploy pipeline"><span>body</span></ListRow>,
    )
    const btn = screen.getByRole('button', { name: 'Deploy pipeline' })
    expect(btn.tagName).toBe('BUTTON')
    // And the wrapper must no longer claim the role.
    expect(container.firstElementChild?.getAttribute('role')).toBeNull()
  })

  it('does not CONTAIN the row content (the nested-interactive shape)', () => {
    render(
      <ListRow onClick={() => {}} label="Row name">
        <button type="button">nested action</button>
      </ListRow>,
    )
    const hit = screen.getByRole('button', { name: 'Row name' })
    const nested = screen.getByRole('button', { name: 'nested action' })
    expect(hit.contains(nested), 'the hit target must not be an ancestor of the row content').toBe(false)
    // Symmetrically, the row's controls must not be inside ANY interactive ancestor.
    for (let el = nested.parentElement; el; el = el.parentElement) {
      expect(
        el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button',
        `a nested control must have no interactive ancestor (found <${el.tagName}>)`,
      ).toBe(false)
    }
  })

  it('owns exactly ONE tab stop per row', () => {
    // `whileTap` makes Framer Motion set tabindex="0" on the wrapper itself, so simply
    // dropping the attribute left TWO tab stops per row (measured live: Tab landed on a bare
    // div, then on the overlay). The wrapper is pinned to -1 to keep the single stop.
    const { container } = render(<ListRow onClick={() => {}} label="Row"><span>body</span></ListRow>)
    const wrapper = container.firstElementChild!
    expect(wrapper.getAttribute('tabindex')).toBe('-1')
    expect(container.querySelectorAll('[tabindex="0"]').length).toBe(0)
    // The <button> is focusable natively without an explicit tabindex.
    expect(screen.getByRole('button', { name: 'Row' }).getAttribute('tabindex')).toBeNull()
  })

  it('fires onClick from the row body (bubbling), so the whole row stays clickable', () => {
    const onClick = vi.fn()
    render(<ListRow onClick={onClick} label="Row"><span>the body text</span></ListRow>)
    fireEvent.click(screen.getByText('the body text'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('lets a nested control stop the row from firing', () => {
    const onRow = vi.fn()
    const onNested = vi.fn()
    render(
      <ListRow onClick={onRow} label="Row">
        <button type="button" onClick={(e) => { e.stopPropagation(); onNested() }}>action</button>
      </ListRow>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'action' }))
    expect(onNested).toHaveBeenCalledTimes(1)
    expect(onRow, 'a control that stops propagation must not also trigger the row').not.toHaveBeenCalled()
  })

  it('adds no hit target to a NON-interactive row', () => {
    const { container } = render(<ListRow label="unused"><span>static</span></ListRow>)
    expect(container.querySelector('button')).toBeNull()
    expect(container.firstElementChild?.getAttribute('tabindex')).toBeNull()
  })
})

describe('the tasks list row and card carry the same overlay', () => {
  const src = readFileSync(join(process.cwd(), 'src/pages/tasks/TasksListPage.tsx'), 'utf8')

  it('both wrappers hand their tab stop to the shared hit-target primitive', () => {
    // 🪤 The first version of this change hand-rolled the overlay, and the primitive-adoption ratchet
    // went red at 273/272 — correctly: `ui/RowHitTarget` already owns this idiom. The ratchet is what
    // found the primitive.
    // Matched on the primitive rather than one exact label expression: the row's name now appends its
    // status (see `taskStatusAnnounced.test.tsx`), and pinning `label={t.title}` literally would have
    // read that deliberate improvement as a regression. What this rail is FOR — both wrappers delegate
    // their tab stop instead of hand-rolling one — is unchanged, and the count still enforces two.
    const overlays = [...src.matchAll(/<RowHitTarget label=\{/g)]
    expect(overlays.length, 'one for the list row, one for the card').toBeGreaterThanOrEqual(2)
    expect(src, 'through the primitive, not a bespoke element').toMatch(/import \{ RowHitTarget \}/)
    // And each one still names the row from the task, not from a constant — a hit target labelled
    // "Open" ×30 is the failure this primitive exists to prevent.
    for (const m of overlays) {
      const tail = src.slice(m.index, m.index + 90)
      expect(tail, 'the name is derived from the task').toMatch(/t\.title/)
    }
  })

  it('neither wrapper claims a role, and both keep tabIndex -1', () => {
    // A role="button" wrapper containing the select checkbox is nested-interactive; and without
    // `tabIndex={-1}` Framer's `whileTap` puts a second tab stop on the wrapper itself.
    //
    // 🪤 Strip comments first. The first version of this assertion failed on CORRECT source, because the
    // explanatory comment at the call site quotes the very markup the scan forbids — the recorded
    // "a ratchet counts markup in comments" trap, third occurrence in this session.
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const wrappers = [...code.matchAll(/<motion\.div[\s\S]{0,900}?onClick=\{onOpen\}([\s\S]{0,1400}?)>/g)]
    expect(wrappers.length, 'the list row and the card').toBeGreaterThanOrEqual(2)
    for (const w of wrappers) {
      expect(w[1], 'no role on the wrapper').not.toMatch(/role="button"/)
      expect(w[1]).toMatch(/tabIndex=\{-1\}/)
    }
  })

  it('the ring is drawn on the row, keyed off the overlay', () => {
    const rings = [...src.matchAll(/has-\[>button:focus-visible\]:ring-2 has-\[>button:focus-visible\]:ring-inset has-\[>button:focus-visible\]:ring-primary\/50/g)]
    expect(rings.length, 'both wrappers').toBeGreaterThanOrEqual(2)
  })

  it('the select checkbox still names itself per row, so the two stops are distinguishable', () => {
    // Two tab stops per row only helps if they announce differently: "triage" opens, "Select: triage"
    // selects.
    expect(src).toMatch(/aria-label=\{`\$\{selected \? 'Deselect' : 'Select'\}: \$\{t\.title\}`\}/)
  })
})

// ── Cycle 164: THE WHOLE FAMILY, COUNTED — because fixing rows one surface at a time never ends ──
//
// Cycles 159 and 161 each fixed one list's row. This censuses every clickable NON-INTERACTIVE element
// in `pages/` + `ui/` instead, so the next mouse-only row is a red test rather than another cycle.
// Measured: **15 such elements**, of which 9 already carried `role` + `tabIndex` and 6 did not.
//
// The two the ledger sent me to, driven at 1440×900 before the fix — same defect, opposite symptoms:
//
//   `#/notifications`    83 rows, `tabindex` NULL on every one. The row is never a tab stop, so `Open`
//                        was reachable only via Shift+F10 on a hover action that is `opacity-0` until
//                        hovered. WCAG 2.1.1.
//   `#/loops/history`    the row IS focusable — `whileHover`/`whileTap` make Motion add `tabindex="0"` —
//                        with NO role and NO key handler. Focus landed on it (2px outline) and Enter
//                        and Space were both dead: body text frozen at 533 chars for each, while a
//                        mouse click opened the peek panel (864). A focus stop that does nothing.
//                        WCAG 2.1.1 + 4.1.2.
//
// 🪤 `#/loops` IS NOT THAT LIST. The loops list lives at `#/loops/history`; `#/loops` renders the
// composer (`LoopsSection` routes `seg === 'history'` to `LoopsListPage`). The capture inventory only
// had `#/loops`, so this surface had never been in a cycle's evidence set at all — `loops-history` was
// added to `surfaces.json` in the same pass.
//
// The ones still unfixed are listed below WITH their reason, because they are the same defect and not
// distinctions — a deferral that reads as a judgment is how a family gets half-converged forever.
//
// 🔑 AND THE STRENGTHENED CHECK IMMEDIATELY EARNED ITS KEEP: it failed on `TerminalPage` the moment
// it was written, because **cycle 167 fixed that strip and left its deferral entry behind**. Under the
// old "still matches the scan" rule that entry could have sat here indefinitely describing a defect
// that no longer existed. Both terminal entries are gone; what remains is one element that genuinely
// does not want this file's shape.
//
// ── Cycle 170 CLOSED THE LAST ONE, and it did NOT use this file's primitive ───────────────────────
//
// `pages/tasks/TaskBoard.tsx`'s card: **30** of them on `#/tasks?view=board`, `role`/`tabindex`/
// `aria-label` all null, **0 of 70** Tab presses landing on one, axe 0 blocking. It was deferred
// because it is `draggable` — and the answer turned out to be that `RowHitTarget` is the WRONG fix
// here. That primitive exists for a row carrying its own controls; this card has **zero** interactive
// descendants (measured), so the wrapper can simply BE the button, and stretching an overlay across a
// drag source would put a control between the pointer and the gesture. `role="button" tabIndex={0}` +
// Enter/Space, the shape `pages/code/CodeSection.tsx` already ships. Verified after: Enter opens the
// task, the coral focus outline comes free from the global `:focus-visible` rule, and native
// `dragstart`/`dragend` still fire on a mouse drag.
//
// ── Cycle 168 CLOSED the largest deferral: the dashboard widget row ───────────────────────────────
//
// `pages/dashboard/widgets/kit.tsx`'s `WidgetRow` is shared by four widgets (Action Center, Tasks,
// Schedule, Pinned Artifacts). Measured on `#/dashboard` at 1440×1000 — the app's FIRST screen:
//
//   clickable widget rows   **20**        `tabindex` **null** on all 20 · `role` **null** on all 20
//   Tab presses on a row    **0 of 80**   while each row's ACTION pills ARE reachable
//   axe                     **0 blocking** — a div with an onclick and no role is invisible to it
//
// So a keyboard user could reply to or dismiss an Action Center item but never OPEN one. Fixed in the
// primitive, once, for all four widgets; each call site passes the subject its own actions already
// announce, so "Reply: X" and the row that opens X agree. `WidgetRow`'s props are now a discriminated
// union — `onClick` REQUIRES `label` — so the next clickable widget row cannot ship nameless.

describe('every clickable non-interactive element has a keyboard route', () => {
  const SRC = join(process.cwd(), 'src')
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  /** 🪤 Brace-aware: the `>` inside `onClick={() => …}` does NOT end the tag, and a matcher that
   *  scans to the first `>` truncates it — five false positives earlier in this session. */
  function tags(src: string, name: string) {
    const out: string[] = []
    const re = new RegExp(`<${name}(?=[\\s>])`, 'g')
    let m: RegExpExecArray | null
    while ((m = re.exec(src))) {
      let i = m.index + 1 + name.length, depth = 0, quote: string | null = null
      for (; i < src.length; i++) {
        const c = src[i]
        if (quote) { if (c === quote) quote = null; continue }
        if (c === '"' || c === "'" || c === '`') { quote = c; continue }
        if (c === '{') depth++
        else if (c === '}') depth--
        else if (c === '>' && depth === 0) break
      }
      out.push(src.slice(m.index, i + 1))
    }
    return out
  }

  /** Same defect as the two fixed here, deliberately NOT fixed in this change, and why. */
  const DEFERRED: Record<string, string> = {
    'pages/knowledge/KnowledgeCreatePage.tsx':
      // Cycle 169 gave this its keyboard route, and it is deliberately NOT the shape this file
      // polices: the drop area stays a plain div, and the route is the real `<input type="file">`
      // inside it, kept focusable (`sr-only`, not `hidden`) so Space/Enter opens the native picker.
      // `design/filePickerReachable.test.ts` owns that contract and drives it. The entry stays here
      // because the element still has an onClick with no role — correctly, since the input is the
      // control — so this scan must keep skipping it for a stated reason rather than by accident.
      'a file DROPZONE, not a row: the keyboard route is the focusable `sr-only` input inside it ' +
      '(cycle 169), policed by design/filePickerReachable.test.ts, so the div needs no role of its own',
  }

  const hits = () => {
    const found: { file: string; tag: string }[] = []
    for (const abs of walk(SRC)) {
      const src = readFileSync(abs, 'utf8')
      for (const name of ['div', 'li', 'article', 'motion\\.div']) {
        for (const tag of tags(src, name)) {
          if (!/onClick=/.test(tag) || !/cursor-pointer/.test(tag)) continue
          if (/stopPropagation/.test(tag)) continue          // a propagation shim, not a target
          found.push({ file: abs.slice(SRC.length + 1), tag })
        }
      }
    }
    return found
  }

  it('finds the population it is meant to police', () => {
    // 🪤 Vacuity guard: a scan that matches nothing passes silently and reads as "all clean".
    expect(hits().length, 'the clickable-row census must not go empty').toBeGreaterThanOrEqual(12)
  })

  it('each one is either a real control, or a documented deferral', () => {
    const bad: string[] = []
    for (const { file, tag } of hits()) {
      // 🪤 Not a literal `tabIndex={0}`: `ui/BoardCollapse` writes `tabIndex={onExpand ? 0 : undefined}`
      // — a control that is only interactive in one state, which is correct and must not be flagged.
      const declaresRole = /\brole=/.test(tag) && /tabIndex=\{[^}]*\b0\b/.test(tag)
      // 🪤 Not a literal `tabIndex={-1}`: a SHARED row primitive pins it only when the row is
      // clickable (`tabIndex={onClick ? -1 : undefined}` in the dashboard widget kit), because a
      // non-interactive row must not claim a tab index at all.
      const usesPrimitive = /tabIndex=\{[^}]*-1/.test(tag) && readFileSync(join(SRC, file), 'utf8').includes('<RowHitTarget')
      if (declaresRole || usesPrimitive || file in DEFERRED || file === 'ui/RowHitTarget.tsx') continue
      bad.push(file)
    }
    expect(bad, `mouse-only click targets: add <RowHitTarget label> + tabIndex={-1}, or record the reason in DEFERRED\n${bad.join('\n')}`)
      .toEqual([])
  })

  it('the two rows this cycle converged go through the primitive', () => {
    for (const rel of ['pages/notifications/NotificationsPage.tsx', 'pages/loops/LoopsListPage.tsx', 'ui/NotificationBell.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, `${rel}: the hit target`).toMatch(/<RowHitTarget label=/)
      expect(src, `${rel}: the ring, keyed off the overlay`).toMatch(/has-\[>button:focus-visible\]:ring-2/)
    }
  })

  it('the deferrals stay honest — a listed file must still FAIL the criteria', () => {
    // 🪤 THE FIRST VERSION OF THIS ONLY CHECKED THAT THE FILE STILL MATCHED THE SCAN, and cycle 170
    // proved that is not the same thing: `TaskBoard`'s card was fixed (`role="button" tabIndex={0}`)
    // and still matched, because the scan keys on `onClick` + `cursor-pointer` — which a FIXED
    // clickable row also has. So a stale entry could sit here forever while its file was clean, which
    // is exactly the rot this test exists to prevent. It now asserts the file still fails the
    // accessibility criteria, which is what "deferred" is supposed to mean.
    for (const rel of Object.keys(DEFERRED)) {
      const tags = hits().filter((h) => h.file === rel)
      expect(tags.length, `${rel} no longer matches the scan at all — drop it from DEFERRED`).toBeGreaterThan(0)
      const fixed = tags.some((h) => {
        const declaresRole = /\brole=/.test(h.tag) && /tabIndex=\{[^}]*\b0\b/.test(h.tag)
        const usesPrimitive = /tabIndex=\{[^}]*-1/.test(h.tag) && readFileSync(join(SRC, rel), 'utf8').includes('<RowHitTarget')
        return declaresRole || usesPrimitive
      })
      expect(fixed, `${rel} now satisfies the criteria — drop it from DEFERRED`).toBe(false)
    }
  })
})
