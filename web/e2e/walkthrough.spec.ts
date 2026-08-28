import { test, expect } from '@playwright/test'
import { ROUTES, SETTINGS_ROUTES, VIEW_ROUTES, NON_NAV_ROUTES, THEMES, type Theme } from './routes'
import { seedTheme, gotoRoute, assertMounted, OPENERS } from './helpers'

// ── V3: the full-app keyboard-only / reduced-motion / phone-viewport walkthrough ─────────────
//
// `DESIGN-SYSTEM-CONSISTENCY` `DSC-11`'s second clause. Its first clause — `e2e/a11y.spec.ts`
// mounted as a blocking CI gate against a seeded authenticated session — is met by the `e2e-a11y`
// job in `ci.yml`. This file is the other half, and it exists because axe cannot express any of
// the three properties below.
//
//   * **Keyboard-only.** axe checks that a control HAS an accessible name and role; it cannot ask
//     "once focus lands here, is the user told?". `design/tokens.css` provides one global ring
//     (`:focus-visible { outline: 2px solid var(--color-primary) }`) and `.outline-none` defeats it
//     — `@layer utilities` beats `@layer base`. `design/focusRingSurvival.test.ts` and
//     `design/focusRingPerElement.test.ts` guard that from SOURCE, and both say the same thing in
//     their headers: a source scan cannot resolve whether an ANCESTOR draws the ring, because that
//     needs a JSX parse rather than a regex. A browser can just ask. This is the Tab-walk those two
//     rails defer to.
//   * **Reduced motion.** A static scan can count files declaring a `prefers-reduced-motion` block
//     (`docs/design/consistency-audit.json` does: 12 of 167 animated files). That number says
//     nothing about what a user with the preference set actually gets, which is what matters.
//   * **Phone viewport.** Horizontal overflow is a layout fact, not a markup fact.
//
// ── Measured before it was written, on `origin/main` ──
//
//   keyboard      dashboard/chat: 1 non-indicating focus stop each, settings: 0 (see CARET CREDIT)
//   reduced motion elements with a >50ms transition: settings 102 → 0, chat 74 → 0, dashboard 71 → 0,
//                 workflows 55 → 0 once the preference actually applies
//   phone         390px viewport: no horizontal overflow, nothing offscreen-right, on all probes
//
// So every leg below gates at ZERO because zero is the MEASURED population, not an aspiration. The
// repo's baseline doctrine cuts both ways: shipping a gate at zero over an unmeasured surface is an
// outage, and padding a baseline over a surface measured clean is a hole.

/** How many Tab presses per route. Not a silent cap: the walk REPORTS when it stops here rather
 *  than at the natural end of the tab ring, so "covered everything" is never implied by a number
 *  that was actually truncated. 60 covers every route measured (the deepest, `settings`, reaches
 *  its focus ring's end well inside it). */
const TAB_CAP = 60

/** The SAME surfaces `a11y.spec.ts` scans: 18 nav routes + 32 settings panels + the view route.
 *
 *  The first version of this file walked the 18 nav routes only and still called itself a "full-app"
 *  walkthrough — 18 of 51 surfaces. That is the exact hole `a11y.spec.ts`'s own header documents on
 *  the route axis: each settings panel is a plain `#/settings/<id>` route that mounts only when
 *  visited, so scanning `settings` covered 1 of 33, and 3 of one cycle's 5 hand-found defects lived
 *  there. Keyboard focus is at least as panel-local as axe's findings were. */
const SURFACES = [...ROUTES, ...SETTINGS_ROUTES, ...VIEW_ROUTES, ...NON_NAV_ROUTES]

/** Focus stops allowed to have no visible indicator.
 *
 *  SELF-CLEARING, deliberately: the leg asserts each entry still MATCHES a real unannounced stop, so
 *  the moment the fix lands this file goes red telling you to delete the entry. That is the opposite
 *  of a count baseline, which would quietly absorb the fix and leave permanent slack. It has already
 *  paid for itself once — it turned the merge of the focus-ring work red and named the five entries
 *  to delete, which a count baseline would have silently absorbed. */
const KNOWN_UNANNOUNCED: { route?: string; opener?: string; match: string; why: string }[] = [
  // ── The four pending-fix groups that used to live here are GONE, because the focus-ring PR they
  //    were waiting on landed in the same batch as this file: PathBar's container (`files` / "Go to
  //    path"), PromptsPanel's select (`settings/prompts` / "Prompt for"), SecurityPanel's denylist
  //    and host inputs (`settings/security`, two entries from ONE ringed `HostList` rendered twice)
  //    and AlwaysOnConventions' select (`settings/legibility`). The self-clearing assertion below is
  //    what forced the deletion — each of those stops now announces its focus, so keeping the
  //    entries would have left the gate wider than the code needs. Nothing pending remains; the one
  //    survivor is a written taste call, not a fix in flight.
  {
    // NOT a pending fix — a deliberate, written taste call, and the reason lives in the code this
    // points at. `ui/SearchField` puts `outline-none` in INPUT_CHROME and `focus:ring-2
    // focus:ring-inset` in OVERLAY_FOCUS, then documents why the INLINE variant keeps the former
    // without the latter: the palette row's focus is carried by the modal context, and an inset
    // rectangle inside a round row would redesign a hero surface. Kept as an allowance rather than
    // a credit rule so the decision stays VISIBLE — a credit for "inputs inside modals" would
    // silently excuse the next real one.
    opener: 'command palette',
    match: 'Search pages and actions',
    why: "ui/SearchField's inline variant, deliberately unringed — see SearchField.tsx around the OVERLAY_FOCUS constant.",
  },
]

/** Emulation that must be (re)applied AFTER `seedTheme`.
 *
 *  🪤 `seedTheme` ends with `page.emulateMedia({ colorScheme })`, and that call CLEARS the
 *  `reducedMotion` emulation that `test.use({ reducedMotion: 'reduce' })` set on the context. The
 *  first version of the reduced-motion leg below measured `matchMedia('(prefers-reduced-motion:
 *  reduce)').matches === false` on every route — it was measuring a user with no preference while
 *  reporting on one who has it. Only the vacuity floor caught it; the leg was otherwise "passing".
 *  Any future spec that combines a theme with a media preference has to re-assert the preference
 *  after seeding the theme. */
async function seedThemeAndMedia(page: import('@playwright/test').Page, theme: Theme, reduce: boolean) {
  await seedTheme(page, theme)
  await page.emulateMedia({
    colorScheme: theme,
    reducedMotion: reduce ? 'reduce' : 'no-preference',
  })
}

/** Install the focus-signature probes into the page. Shared by both tiers of leg A — the route walk
 *  and the opened-surface walk — so the credit rules cannot drift between them. */
async function installProbes(page: import('@playwright/test').Page) {
  await page.evaluate(() => {
    const w = window as unknown as Record<string, unknown>
    // Signature spans the element AND its three nearest ancestors: this design system draws
    // the ring on the WRAPPER for a transparent control (`focus-within:`) and on the PARENT
    // for a `-z-10` hit target (`has-[>button:focus-visible]`). Reading the element alone
    // would report both correct patterns as defects — the exact false-positive class that
    // took the source rail's population from 35 to 10.
    //
    // 🪤 The signature must be the EFFECTIVE indicator, not the raw computed values. Reading
    // `outlineWidth` verbatim made this leg blind to the exact bug it exists to catch:
    // Tailwind's `outline-none` sets only `outline-style: none`, while `tokens.css`'s
    // `:focus-visible { outline: 2px solid … }` still sets the WIDTH. So a control with
    // `outline-none` reports `outlineWidth: 2px` focused and `medium` at rest — a difference,
    // for a ring that is never painted. Falsification proved it: stripping the ring from every
    // NavRail row (which has no other focus treatment) left this leg at 37/37 green, and the
    // mutated class string was confirmed present in the served bundle, so the run was honest
    // and the detector was wrong. An outline with `style: none` or zero width now contributes
    // NOTHING.
    const effective = (cs: CSSStyleDeclaration) => {
      const w = parseFloat(cs.outlineWidth) || 0
      const outline = cs.outlineStyle !== 'none' && w > 0
        ? `o:${w}:${cs.outlineStyle}:${cs.outlineColor}` : ''
      const shadow = cs.boxShadow && cs.boxShadow !== 'none' ? `s:${cs.boxShadow}` : ''
      return [outline, shadow, `bd:${cs.borderColor}`, `bg:${cs.backgroundColor}`,
              `fg:${cs.color}`, `op:${cs.opacity}`].join('~')
    }
    // The indicator can live in any of THREE places, and all three are legitimate patterns in
    // this codebase, so all three are read:
    //   * the element itself             — `focus:ring-2` (45 files)
    //   * an ANCESTOR                    — `focus-within:` on a box-drawing wrapper, or
    //                                      `has-[>button:focus-visible]:ring-2` on a parent
    //   * a DESCENDANT                   — `group` on the focusable + `group-focus-visible:` on
    //                                      a child that paints the seam
    // The descendant case was found by falsification too: `ui/SidePanel.tsx:196`'s window
    // splitter is `outline-none group`, and its visible seam is a child `<motion.span>` with
    // `group-focus-visible:bg-primary`. Reading only the element and its ancestors reported
    // that correct control as a defect on `#/files` in both themes — a gate that flags correct
    // code teaches people to "fix" it. Descendants are bounded so a large container cannot
    // make the signature enormous.
    w.__sig = (el: Element | null) => {
      if (!el) return 'none'
      const parts: string[] = []
      let cur: Element | null = el
      for (let i = 0; i < 4 && cur; i++) {
        parts.push(effective(getComputedStyle(cur)))
        cur = cur.parentElement
      }
      const kids = Array.from(el.querySelectorAll('*')).slice(0, 25)
      for (const k of kids) parts.push(effective(getComputedStyle(k)))
      return parts.join('||')
    }
    w.__desc = (el: Element | null) => {
      if (!el) return 'none'
      const e = el as HTMLElement
      const cls = typeof e.className === 'string' ? e.className.split(/\s+/).slice(0, 3).join('.') : ''
      const name = (e.getAttribute('aria-label') || e.textContent || '').trim().slice(0, 32)
      return `<${el.tagName.toLowerCase()}${cls ? ' .' + cls : ''}>${name ? ` "${name}"` : ''}`
    }
    // CARET CREDIT — deliberately NARROW.
    //
    // A rich text editor (CodeMirror in the composer, Monaco in Files/gists) is a
    // `contenteditable` div whose native focus indicator is the blinking caret, which no
    // computed-style signature can see. Crediting it is correct; crediting every text-entry
    // surface would NOT be, because `<input>`/`<textarea>` in this design system are
    // explicitly ringed — seventeen of them had lost that ring, and a gate that excused
    // "anything with a caret" would have been blind to every one. So the credit is scoped to
    // `contenteditable` only, and the falsification for this file strips the ring from a real
    // `<input>` and requires a red.
    w.__caretCredited = (el: Element | null) => {
      if (!el) return false
      const e = el as HTMLElement
      if (e.isContentEditable !== true) return false
      const cs = getComputedStyle(e)
      return cs.caretColor !== 'transparent'
    }
  })

}

/** Tab through the focus ring, reporting every stop that changes nothing visible.
 *
 *  Stops at the first repeat (the ring wrapped) or at TAB_CAP, and REPORTS which — a cap that is
 *  silent would imply coverage it did not have. */
async function tabWalk(page: import('@playwright/test').Page) {
  const seen = new Set<string>()
  const unannounced: string[] = []
  let presses = 0
  let cappedOut = false

  for (; presses < TAB_CAP; presses++) {
    await page.keyboard.press('Tab')
    const r = await page.evaluate(async () => {
      const el = document.activeElement
      if (!el || el === document.body) return null
      const w = window as unknown as Record<string, (el: Element | null) => unknown>
      const desc = w.__desc(el) as string
      if (w.__caretCredited(el)) return { desc, ok: true, credited: true }
      const sig = () => w.__sig(el) as string
      const settle = () => new Promise((r) => setTimeout(r, 260))

      const focused = sig()
      ;(el as HTMLElement).blur()
      const resting = sig()
      if (focused !== resting) {
        ;(el as HTMLElement).focus()
        return { desc, ok: true, credited: false }
      }
      // 🪤 A SETTLED re-check before flagging. Focus treatments in this codebase are routinely
      // painted through `transition-colors`, and `getComputedStyle` returns the CURRENT value
      // of an in-flight transition — which at t≈0 is the value it is transitioning AWAY from.
      // So an immediate read makes a working indicator look identical: `ui/SidePanel.tsx`'s
      // window splitter (`group` + a child `group-focus-visible:bg-primary transition-colors`)
      // was reported as a defect on `#/files` in both themes, and a direct probe showed the
      // child's background byte-identical focused vs resting while `:focus-visible` genuinely
      // matched. Only the suspected misses pay the settle, so the fast path stays fast.
      ;(el as HTMLElement).focus()
      await settle()
      const focusedSettled = sig()
      ;(el as HTMLElement).blur()
      await settle()
      const restingSettled = sig()
      ;(el as HTMLElement).focus()
      return { desc, ok: focusedSettled !== restingSettled, credited: false }
    })
    if (!r) break
    if (seen.has(r.desc)) break // wrapped around the tab ring
    seen.add(r.desc)
    if (!r.ok) unannounced.push(r.desc)
  }
  cappedOut = presses >= TAB_CAP
  return { stops: seen.size, presses, capped: cappedOut, unannounced }
}

// ── Leg A: keyboard-only — every focus stop is visible ───────────────────────────────────────

for (const theme of THEMES) {
  test.describe(`V3 keyboard-only: ${theme} theme`, () => {
    for (const { route, label } of SURFACES) {
      test(`${label} (#/${route}) — every focus stop announces itself`, async ({ page }, testInfo) => {
        await seedThemeAndMedia(page, theme, false)
        await gotoRoute(page, route)

        await installProbes(page)
        const walk = await tabWalk(page)
        const { unannounced } = walk
        const seenSize = walk.stops
        const presses = walk.presses
        const cappedOut = walk.capped

        await testInfo.attach(`kbd-${route}-${theme}.txt`, {
          body: `stops=${seenSize} presses=${presses} cappedAtTabCap=${cappedOut}\n` +
            `unannounced=${unannounced.length}\n${unannounced.map((u) => '  ' + u).join('\n')}`,
          contentType: 'text/plain',
        })

        // Vacuity floor. A route whose Tab-walk reaches nothing measures nothing, and an empty
        // `unannounced` list would read exactly like a clean surface.
        expect(
          seenSize,
          `the Tab-walk reached NO focusable element on #/${route} — the walk broke, it did not ` +
            `find a clean route. (Every route in routes.ts is shell-bearing, and the shell alone ` +
            `carries the nav rail's stops.)`,
        ).toBeGreaterThan(0)

        const allowed = KNOWN_UNANNOUNCED.filter((k) => k.route === route)
        const unexpected = unannounced.filter((u) => !allowed.some((k) => u.includes(k.match)))

        expect(
          unexpected,
          `${unexpected.length} keyboard focus stop(s) on #/${route} (${theme}) change NOTHING ` +
            `visible when focused — not on the element, not on its three nearest ancestors, not on ` +
            `a descendant. The user cannot see where they are. Add a focus treatment (focus:ring-2 ` +
            `on the control, focus-within: on the box-drawing container, or group-focus-visible: on ` +
            `the child that paints the seam), or drop the outline-none and let tokens.css's global ` +
            `ring paint:\n${unexpected.map((u) => '  ' + u).join('\n')}`,
        ).toEqual([])

        // …and every allowance must still be EARNED. A fixed site turns this red rather than
        // silently widening the gate — see KNOWN_UNANNOUNCED.
        for (const k of allowed) {
          expect(
            unannounced.some((u) => u.includes(k.match)),
            `KNOWN_UNANNOUNCED names "${k.match}" on #/${route}, but that stop now announces its ` +
              `focus. The gate is wider than the code needs: DELETE the entry.\n  ${k.why}`,
          ).toBe(true)
        }
      })
    }
    // ── Keyboard on surfaces that only exist AFTER an interaction ─────────────────────────────
    //
    // The third tier `a11y.spec.ts` added for the same reason: modals, docks and menus are where
    // defects hide, because nothing on the route axis ever opens them. Its header records 10
    // blocking violations found by hand behind a click. A focus indicator is at least as likely to
    // be missing on a surface nobody scans — and a modal is precisely where keyboard-only users
    // have no alternative.
    for (const opener of OPENERS) {
      test(`${opener.label} [opened] — every focus stop announces itself`, async ({ page }, testInfo) => {
        await seedThemeAndMedia(page, theme, false)
        await gotoRoute(page, opener.route)

        const before = await page.evaluate(() => document.querySelectorAll('*').length)
        const opened = await opener.open(page)
        // A recipe whose target is absent must NOT report a clean surface — indistinguishable from
        // "no defects", and exactly how the axe gate hid violations for months. The recipe supplies
        // its own reason so the report says which it was.
        test.skip(opened !== true, opened === true ? '' : `${opener.label}: ${opener.skip}`)
        await page.waitForTimeout(700)
        await assertMounted(page, before, opener.label)
        await installProbes(page)

        const walk = await tabWalk(page)
        await testInfo.attach(`kbd-opened-${opener.label.replace(/\W+/g, '-')}-${theme}.txt`, {
          body: `stops=${walk.stops} presses=${walk.presses} cappedAtTabCap=${walk.capped}\n` +
            `unannounced=${walk.unannounced.length}\n${walk.unannounced.map((u) => '  ' + u).join('\n')}`,
          contentType: 'text/plain',
        })

        expect(
          walk.stops,
          `the Tab-walk reached NO focusable element on ${opener.label} — the walk broke, it did ` +
            `not find a clean surface.`,
        ).toBeGreaterThan(0)

        const allowedHere = KNOWN_UNANNOUNCED.filter((k) => k.opener === opener.label)
        const unexpectedHere = walk.unannounced.filter(
          (u) => !allowedHere.some((k) => u.includes(k.match)),
        )

        expect(
          unexpectedHere,
          `${unexpectedHere.length} keyboard focus stop(s) on ${opener.label} (${theme}) change ` +
            `NOTHING visible when focused — a surface the route-level walk never reaches:\n` +
            unexpectedHere.map((u) => '  ' + u).join('\n'),
        ).toEqual([])

        for (const k of allowedHere) {
          expect(
            walk.unannounced.some((u) => u.includes(k.match)),
            `KNOWN_UNANNOUNCED names "${k.match}" on ${opener.label}, but that stop now announces ` +
              `its focus. DELETE the entry.\n  ${k.why}`,
          ).toBe(true)
        }
      })
    }
  })
}

// ── Leg B: reduced motion — the preference is actually honored ───────────────────────────────

for (const theme of THEMES) {
  test.describe(`V3 reduced-motion: ${theme} theme`, () => {
    test.use({ reducedMotion: 'reduce' })
    for (const { route, label } of SURFACES) {
      test(`${label} (#/${route}) — motion is suppressed`, async ({ page }, testInfo) => {
        await seedThemeAndMedia(page, theme, true)
        await gotoRoute(page, route)
        // Entrance motion is allowed to have STARTED; what must not happen is it still running or
        // still declared long after mount.
        await page.waitForTimeout(1_000)

        const m = await page.evaluate(() => {
          const long: string[] = []
          let total = 0
          for (const el of document.querySelectorAll('*')) {
            total++
            const cs = getComputedStyle(el)
            const td = Math.max(...cs.transitionDuration.split(',').map((s) => parseFloat(s) || 0))
            const ad = Math.max(...cs.animationDuration.split(',').map((s) => parseFloat(s) || 0))
            const animated = ad > 0.05 && cs.animationName !== 'none'
            if (td > 0.05 || animated) {
              const e = el as HTMLElement
              const cls = typeof e.className === 'string' ? e.className.split(/\s+/).slice(0, 3).join('.') : ''
              long.push(`<${el.tagName.toLowerCase()}${cls ? ' .' + cls : ''}> ` +
                        `transition=${td}s animation=${animated ? cs.animationName + ' ' + ad + 's' : 'none'}`)
            }
          }
          return {
            mq: matchMedia('(prefers-reduced-motion: reduce)').matches,
            long: long.slice(0, 12),
            longCount: long.length,
            total,
          }
        })

        await testInfo.attach(`reduce-${route}-${theme}.txt`, {
          body: `mqReduce=${m.mq} elementsWithLongMotion=${m.longCount} of ${m.total}\n` +
            m.long.map((l) => '  ' + l).join('\n'),
          contentType: 'text/plain',
        })

        // Vacuity floor FIRST — see the trap documented on `seedThemeAndMedia`. Without this the
        // whole leg passed while measuring a user who never set the preference.
        expect(
          m.mq,
          `prefers-reduced-motion is NOT emulated on this page, so this leg is measuring a user ` +
            `with no preference. seedTheme's emulateMedia({colorScheme}) clears it — re-apply it.`,
        ).toBe(true)

        expect(
          m.longCount,
          `${m.longCount} element(s) on #/${route} (${theme}) still declare motion over 50ms with ` +
            `prefers-reduced-motion: reduce set. Measured on main: 55-102 per route WITHOUT the ` +
            `preference and 0 WITH it, so the global suppression works and a non-zero count here ` +
            `means a new rule escaped it:\n${m.long.map((l) => '  ' + l).join('\n')}`,
        ).toBe(0)
      })
    }
  })
}

// ── Leg C: phone viewport — nothing runs off the side ────────────────────────────────────────

for (const theme of THEMES) {
  test.describe(`V3 phone viewport: ${theme} theme`, () => {
    // iPhone 14 logical resolution — the narrowest device the responsive contract targets.
    test.use({ viewport: { width: 390, height: 844 } })
    for (const { route, label } of SURFACES) {
      test(`${label} (#/${route}) — no horizontal overflow at 390px`, async ({ page }, testInfo) => {
        await seedThemeAndMedia(page, theme, false)
        await gotoRoute(page, route)

        const m = await page.evaluate(() => {
          const vw = window.innerWidth
          const offscreen: string[] = []
          let inScroller = 0
          /** Whether some ancestor scrolls horizontally, making this element REACHABLE.
           *
           *  Calibration, measured not assumed: the first version of this leg flagged 25 elements on
           *  `#/artifacts` (and tasks/files, both themes) while `scrollWidth === clientWidth === 390`
           *  — the document did not pan at all. They were the contents of a deliberately
           *  horizontally-scrollable control row (an `inline-flex` reaching `right=1168px`). An
           *  element inside a scroller is a design, not an overflow; the defect this leg is about is
           *  the PAGE forcing a sideways pan, which the document-level assertion below owns. */
          const inHorizontalScroller = (el: Element): boolean => {
            let cur: Element | null = el.parentElement
            while (cur && cur !== document.documentElement) {
              const ox = getComputedStyle(cur).overflowX
              if (ox === 'auto' || ox === 'scroll') return true
              cur = cur.parentElement
            }
            return false
          }
          for (const el of document.querySelectorAll('*')) {
            const r = el.getBoundingClientRect()
            if (r.width === 0 || r.height === 0) continue
            // An element the user cannot SEE cannot overflow FOR the user. `#/tasks` (both themes)
            // reported 8 offscreen nodes rooted at a `pointer-events-none invisible absolute` ghost
            // — a hidden measuring copy at right=476px — while `scrollWidth === clientWidth === 390`,
            // i.e. the page did not pan at all. `visibility` inherits, so this one read also covers
            // every child of a hidden subtree.
            if (getComputedStyle(el).visibility !== 'visible') continue
            // 1px of tolerance for subpixel rounding; a real overflow is far larger.
            if (r.right > vw + 1) {
              if (inHorizontalScroller(el)) {
                inScroller++
                continue
              }
              const e = el as HTMLElement
              const cls = typeof e.className === 'string' ? e.className.split(/\s+/).slice(0, 3).join('.') : ''
              offscreen.push(`<${el.tagName.toLowerCase()}${cls ? ' .' + cls : ''}> right=${Math.round(r.right)}px`)
            }
          }
          return {
            vw,
            scrollW: document.documentElement.scrollWidth,
            clientW: document.documentElement.clientWidth,
            offscreen: offscreen.slice(0, 10),
            offscreenCount: offscreen.length,
            inScroller,
          }
        })

        await testInfo.attach(`phone-${route}-${theme}.txt`, {
          body: `vw=${m.vw} scrollW=${m.scrollW} clientW=${m.clientW} offscreenRight=${m.offscreenCount} ` +
            `(creditedInsideAHorizontalScroller=${m.inScroller})\n` +
            m.offscreen.map((o) => '  ' + o).join('\n'),
          contentType: 'text/plain',
        })

        // Vacuity floor: a leg that silently ran at the default 1280px would find no overflow and
        // report a phone contract it never tested.
        expect(m.vw, 'the phone viewport did not apply — this ran at desktop width').toBe(390)

        expect(
          m.scrollW,
          `#/${route} (${theme}) scrolls horizontally at 390px (scrollWidth ${m.scrollW} > ` +
            `clientWidth ${m.clientW}). A phone user has to pan sideways to read the page.`,
        ).toBeLessThanOrEqual(m.clientW + 1)

        expect(
          m.offscreen,
          `${m.offscreenCount} element(s) extend past the right edge at 390px on #/${route} ` +
            `(${theme}). Nothing overflowed on any route measured on main, so this is new:\n` +
            m.offscreen.map((o) => '  ' + o).join('\n'),
        ).toEqual([])
      })
    }
  })
}
