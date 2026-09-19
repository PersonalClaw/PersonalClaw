import { test, expect, type Page } from '@playwright/test'
import { gotoRoute, driveScriptedTurns, openHeaderOverflowIfNeeded, settleEntranceAnimations } from './helpers'

// ── SESSION MAP — WIRED INTO THE TRANSCRIPT (SSM-11) + THE COARSE FORM (SSM-10) ──────────────
//
// 🔑 WHY THESE CLAUSES LIVE IN A BROWSER GATE AND NOT IN VITEST. Both atoms assert properties of
// `ChatPage` — "the rail renders in ChatSession", "stays fixed while the transcript scrolls",
// "open/closed persists to a URL query flag", "width to SidePanel storeKey", "a mark jump scrolls
// the transcript", "the rail collapses to one named control". `ChatPage.tsx` is ~4k lines, owns a
// socket and a composer, and is not mountable under jsdom (the reason
// `contextLedgerReach.test.tsx` states in full). Worse, every one of those clauses is about
// LAYOUT or NAVIGATION — scroll offsets, a fixed bounding box, a resize, a media query — and
// jsdom computes no layout at all, so a unit test could only have asserted that the JSX contains
// a string. The unit half that IS honest lives in `sessionMapCoord.test.ts` (the jump
// coordinate) and `SessionMapDrawer.test.tsx` (the drawer's own contract).
//
// The session is REAL: one or more turns driven through the offline scripted provider
// (`helpers.driveScriptedTurns`), so the marks under test are derived from a transcript the
// backend actually produced — not from a fixture whose `visibleIndex` happens to equal its array
// position, which is precisely how the jump coordinate stayed wrong through six confirmed atoms.

const PROMPT = 'Index this turn on the session map, please'

const RAIL = 'nav[aria-label="Session map"]'
const MARK = '[data-session-mark]'
const SCROLLER = '[data-transcript-scroll]'

/** Scroll geometry of the transcript container, read in the page. */
async function scrollBox(page: Page): Promise<{ top: number; scrollable: number }> {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel)
    if (!el) return { top: -1, scrollable: -1 }
    return { top: el.scrollTop, scrollable: el.scrollHeight - el.clientHeight }
  }, SCROLLER)
}

test.describe('Session Map — in the transcript (SSM-11)', () => {
  // A real turn waits on the backend; everything else in this directory navigates and scans.
  test.describe.configure({ timeout: 180_000 })
  // Short on purpose: the scroll-fixity clause needs a transcript that actually OVERFLOWS, and
  // three one-line scripted turns do not overflow 900px. 1280 keeps `useIsMobile` false (its
  // breakpoint is max-WIDTH 768), so this is still the pointer form.
  test.use({ viewport: { width: 1280, height: 420 } })

  test('renders in-session, stays fixed while the transcript scrolls, and a mark jump scrolls it', async ({ page }) => {
    await gotoRoute(page, 'chat')

    // ── the rail is NOT on the empty chat route ──────────────────────────────────────────────
    // The map indexes a session; before one exists there is nothing to index, and the header
    // control that toggles it is `started &&` too. Asserted so "the rail renders" below cannot be
    // satisfied by a rail that was always there.
    await expect(page.locator(RAIL)).toHaveCount(0)

    await driveScriptedTurns(page, PROMPT, 3)

    // ── CLAUSE 1: the rail renders in the session ────────────────────────────────────────────
    const rail = page.locator(RAIL)
    await expect(rail, 'the Session Map rail never mounted in a started session — SSM-11 is the atom that makes the map reachable at all').toBeVisible()
    const markCount = await page.locator(MARK).count()
    expect(markCount, 'the rail mounted with no marks').toBeGreaterThan(1)

    // ── CLAUSE 2: it stays FIXED while the transcript scrolls ────────────────────────────────
    const before = await scrollBox(page)
    // The sentinel, not a position. `scrollBox` returns -1 for BOTH fields when the selector
    // matches nothing, and that is the only thing worth asserting here: a started session has
    // already scrolled itself to the newest turn (`scrollToLatest`), so `scrollTop` is whatever
    // the last turn's height left it at — measured 591 at this viewport. Asserting `=== 0` read
    // like a container check and was really a claim that the transcript starts at the top, which
    // the app deliberately makes false. Nothing downstream needs the initial offset: the fixity
    // block below drives `scrollTop` to 0 and then to the bottom itself.
    expect(before.top, `no element matched ${SCROLLER} — the transcript scroll container is the ref the rail is asserted against`).not.toBe(-1)
    expect(
      before.scrollable,
      'the transcript does not overflow, so scrolling it proves nothing about the rail. This\n' +
        'test is vacuous in that state — send more turns or shorten the viewport rather than\n' +
        'letting it pass.',
    ).toBeGreaterThan(40)

    const railTopBefore = (await rail.boundingBox())!.y
    await page.evaluate((sel) => { document.querySelector(sel)!.scrollTop = 0 }, SCROLLER)
    await page.evaluate((sel) => {
      const el = document.querySelector(sel)!
      el.scrollTop = el.scrollHeight
    }, SCROLLER)
    await page.waitForTimeout(250)
    const after = await scrollBox(page)
    expect(after.top, 'the transcript did not scroll, so the fixity assertion below is vacuous').toBeGreaterThan(40)
    const railTopAfter = (await rail.boundingBox())!.y
    expect(
      railTopAfter,
      `the rail MOVED with the transcript (${railTopBefore} → ${railTopAfter} after scrolling\n` +
        `${after.top}px). It must be a sibling of the scroll container, not inside it.`,
    ).toBeCloseTo(railTopBefore, 0)

    // And the structural reason, asserted directly: a `sticky` rail inside the scroller could
    // pass the box check above and still drift under an ancestor transform.
    const railIsInsideScroller = await page.evaluate(([railSel, scrollSel]) => {
      const r = document.querySelector(railSel)
      const s = document.querySelector(scrollSel)
      return !!(r && s && s.contains(r))
    }, [RAIL, SCROLLER])
    expect(railIsInsideScroller, 'the rail is INSIDE the transcript scroll container — its fixity is then a styling accident, not a structural fact').toBe(false)

    // ── CLAUSE 5: a mark jump scrolls the transcript ─────────────────────────────────────────
    // From the BOTTOM of the transcript, clicking the FIRST mark must travel upward. The first
    // mark is the oldest turn, so this is the longest, least ambiguous move available.
    const first = page.locator(MARK).first()
    await first.click()
    // `scrollIntoView({behavior:'smooth'})` animates; poll rather than sample once.
    await expect
      .poll(async () => (await scrollBox(page)).top, {
        message: 'clicking the oldest mark did not scroll the transcript back up — the jump either ' +
          'resolved no turn node (a coordinate mismatch) or reached no handler',
        timeout: 10_000,
      })
      .toBeLessThan(after.top - 40)
  })

  test('open/closed persists to a URL query flag and survives a reload', async ({ page }) => {
    await gotoRoute(page, 'chat')
    await driveScriptedTurns(page, PROMPT, 1)
    const sessionUrl = page.url()
    expect(sessionUrl, 'the send did not open a session route, so a reload would not restore the transcript').toContain('#/chat/')

    // Default is OPEN (§A.1 — always-available), and the clean URL carries no flag for it.
    await expect(page.locator(RAIL)).toBeVisible()
    expect(new URL(sessionUrl).hash).not.toContain('map=')

    // CLOSE through the one named control, then read the URL.
    expect(await openHeaderOverflowIfNeeded(page, 'Session map'), 'the "Session map" control is reachable nowhere in the header').toBe(true)
    await page.getByRole('button', { name: 'Session map', exact: true }).click()
    await expect(page.locator(RAIL)).toHaveCount(0)
    await expect.poll(() => page.url(), { message: 'closing the map wrote nothing to the URL', timeout: 5_000 }).toContain('map=0')

    // The half that matters: RELOAD. A flag that only lives in React state looks identical up to
    // here and loses the choice on refresh.
    const closedUrl = page.url()
    await page.goto(closedUrl)
    await settleEntranceAnimations(page)
    await expect(
      page.locator(RAIL),
      'the rail came back after a reload of a URL carrying map=0 — the closed state did not persist',
    ).toHaveCount(0, { timeout: 20_000 })

    // …and re-opening restores it, so the flag is two-way rather than a one-time suppression.
    expect(await openHeaderOverflowIfNeeded(page, 'Session map')).toBe(true)
    await page.getByRole('button', { name: 'Session map', exact: true }).click()
    await expect(page.locator(RAIL)).toBeVisible({ timeout: 10_000 })
    await expect.poll(() => page.url(), { timeout: 5_000 }).not.toContain('map=0')
  })
})

test.describe('Session Map — the coarse-pointer form (SSM-10)', () => {
  test.describe.configure({ timeout: 180_000 })
  // A phone viewport: below `useIsMobile`'s 768px breakpoint, which is the signal the shell
  // already uses to switch its own nav rail to a drawer.
  test.use({ viewport: { width: 390, height: 844 } })

  test('the rail collapses to one named control that opens a tappable SidePanel drawer', async ({ page }) => {
    await gotoRoute(page, 'chat')
    await driveScriptedTurns(page, PROMPT, 3)

    // ── the rail is GONE, and the map is NOT ───────────────────────────────────────────────
    // KiroCrew's rail simply vanishes on touch. Since this map is the SOLE in-session index nav,
    // vanishing is the defect §A.8 exists to prevent — so both halves are asserted together.
    await expect(page.locator(RAIL), 'the 4px-tick rail is still mounted at a phone viewport').toHaveCount(0)
    expect(
      await openHeaderOverflowIfNeeded(page, 'Session map'),
      'IN-SESSION NAV IS UNREACHABLE at 390px: the rail is gone and the "Session map" control is\n' +
        'in neither the header row nor its overflow menu. That is exactly the "mobile loses\n' +
        'session navigation" outcome SSM-10 exists to prevent.',
    ).toBe(true)

    // Park the transcript at its newest turn, so "the tap navigated" has somewhere to travel FROM.
    // Asserted, not assumed: with the oldest prompt already on screen the jump below could not be
    // distinguished from doing nothing.
    const oldest = page.getByText(`${PROMPT} (1)`, { exact: false }).first()
    await page.evaluate((sel) => {
      const el = document.querySelector(sel)
      if (el) el.scrollTop = el.scrollHeight
    }, SCROLLER)
    await page.waitForTimeout(250)
    await expect(
      oldest,
      'the oldest turn is still on screen at the newest scroll position — the transcript does not\n' +
        'overflow at this viewport, so the jump assertion below would be vacuous. Drive more turns\n' +
        'rather than letting it pass.',
    ).not.toBeInViewport()

    // ── the drawer ──────────────────────────────────────────────────────────────────────────
    await page.getByRole('button', { name: 'Session map', exact: true }).click()
    const drawer = page.getByRole('region', { name: 'Session map' })
    await expect(drawer, 'the control did not open a SidePanel drawer').toBeVisible({ timeout: 10_000 })
    const rows = drawer.locator('[data-session-map-row]')
    await expect(rows.first()).toBeVisible()
    const rowCount = await rows.count()
    expect(rowCount, 'the drawer opened with no marks').toBeGreaterThan(1)

    // A real 44px measurement at a real viewport — the reason this clause is not in vitest.
    const heights = await rows.evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().height)))
    expect(Math.min(...heights), `a drawer row is under the 44px touch floor (heights: ${heights.join(', ')})`).toBeGreaterThanOrEqual(44)

    // ── CLAUSE: width persists through the SidePanel storeKey (§A.9 / SSM-11) ───────────────
    // The dock's resize handle is the WAI-ARIA window-splitter — keyboard-operable, which is
    // what makes this drivable without a drag. `useResizablePanel` debounces the write by 200ms.
    const splitter = drawer.getByRole('separator', { name: /Resize panel/i })
    await expect(splitter).toBeVisible()
    await splitter.focus()
    for (let i = 0; i < 6; i++) await page.keyboard.press('ArrowLeft')
    await expect
      .poll(() => page.evaluate(() => localStorage.getItem('session-map-w')), {
        message: 'resizing the Session Map drawer persisted nothing at the SidePanel storeKey `session-map-w`',
        timeout: 8_000,
      })
      .not.toBeNull()

    // ── each row is tappable to jump ────────────────────────────────────────────────────────
    // Tap the OLDEST mark and assert WHAT IS ON SCREEN, not a scrollTop delta: closing a 358px
    // dock re-lays-out the 32px-wide transcript column underneath it, so the before/after scroll
    // offsets are not comparable numbers. "Is turn 1 visible?" is both comparable and the
    // property the user cares about.
    await rows.first().click()
    await expect(drawer, 'the drawer stayed open over the transcript it just navigated').toHaveCount(0, { timeout: 10_000 })
    await expect(
      oldest,
      'tapping the oldest drawer row did not bring turn 1 on screen — the tap either resolved no\n' +
        'turn node (a coordinate mismatch) or reached no handler',
    ).toBeInViewport({ timeout: 10_000 })
  })
})

test.describe('Session Map — the Activity Index jumps through the map (SSM-12)', () => {
  test.describe.configure({ timeout: 180_000 })
  // Same geometry as the SSM-11 block: short enough that three scripted turns overflow, wide
  // enough to stay the pointer form (`useIsMobile` is max-width 768).
  test.use({ viewport: { width: 1280, height: 420 } })

  // 🔑 WHY THE BROWSER HALF EXISTS ALONGSIDE `src/pages/chat/sessionMapIndexJump.test.tsx`.
  // The vitest file owns what jsdom can honestly see: one spy receiving both jumps with the same
  // argument, and — read off `ChatPage.tsx`'s source — that the page passes the two surfaces the
  // SAME bare identifier. Neither of those observes the jump ARRIVING, because jsdom computes no
  // layout and `ChatPage` is not mountable there at all. So the claim left over is the user-visible
  // one, and it is the reason the atom exists: driving the rail tick and driving the former Index
  // anchor for the SAME turn must move the real transcript to the SAME place.
  //
  // 🪤 THE ACTIVITY PANEL IS OPENED FIRST, BEFORE EITHER JUMP, AND THAT ORDER IS LOAD-BEARING.
  // It is a docked `SidePanel` flex sibling, so opening it narrows the transcript and re-flows it —
  // a landing offset measured at full width is not comparable with one measured beside an open
  // panel. Both jumps therefore run under one layout, which is also what lets the assertion be an
  // exact offset rather than a direction.
  test('a rail tick and the Index anchor for one turn land the transcript in the same place', async ({ page }) => {
    await gotoRoute(page, 'chat')
    // SIX turns, not the three the SSM-11 block drives, and the count is load-bearing. The target
    // below must be a turn whose centred offset is neither 0 nor the maximum, or a wrong coordinate
    // would clamp to the same place and the comparison would stop discriminating. MEASURED at three
    // turns: the second user turn centres at 674 against a scrollable height of 674 — pinned to the
    // bottom, i.e. exactly the degenerate case. Six turns put it around a quarter of the way down.
    await driveScriptedTurns(page, PROMPT, 6)

    // ── both surfaces, side by side ─────────────────────────────────────────────────────────
    await expect(page.locator(RAIL)).toBeVisible()
    expect(
      await openHeaderOverflowIfNeeded(page, 'Activity'),
      'the "Activity" control is reachable nowhere in the header, so the Index tab cannot be driven',
    ).toBe(true)
    await page.getByRole('button', { name: 'Activity', exact: true }).click()
    // `index` is the panel's default tab — no tab click, so this really is the FORMER behaviour.
    const indexPanel = page.locator('#act-panel-index')
    await expect(indexPanel, 'the Activity panel did not open on its Index tab').toBeVisible({ timeout: 10_000 })

    // ── the pairing: the Nth Index anchor and the Nth `user` mark are the same turn ──────────
    // Asserted as equal counts rather than assumed. `sessionMapCoord.test.ts` proves the two lists
    // carry identical coordinates; this is the same claim in the DOM, where a drift would mean the
    // two clicks below are not about one turn and the offset comparison proves nothing.
    const anchors = indexPanel.getByRole('button')
    const userMarks = page.locator(`${MARK}[data-kind="user"]`)
    const anchorCount = await anchors.count()
    expect(anchorCount, 'the Index tab listed no anchors').toBeGreaterThan(1)
    expect(
      await userMarks.count(),
      'the rail and the Index tab disagree on how many user turns this session has, so "the same\n' +
        'turn" below is not well defined',
    ).toBe(anchorCount)

    // The MIDDLE user turn, never the first: `scrollIntoView({block:'center'})` clamps the oldest
    // turn to offset 0, where a coordinate off by one lands in the same place and the comparison
    // stops discriminating.
    const nth = 1
    const target = page.getByText(`${PROMPT} (${nth + 1})`, { exact: false }).first()

    /** Park at the newest turn and return that offset — the "from" every jump travels out of. */
    const parkAtBottom = async (): Promise<number> => {
      await page.evaluate((sel) => {
        const el = document.querySelector(sel)
        if (el) el.scrollTop = el.scrollHeight
      }, SCROLLER)
      await page.waitForTimeout(250)
      return (await scrollBox(page)).top
    }
    /** The offset a jump settles at, given where it started.
     *
     *  🪤 POLLING FOR QUIET ALONE READS THE OFFSET THE JUMP HAS NOT LEFT YET, and that mode is
     *  invisible: `scrollIntoView({behavior:'smooth'})` animates, so the first two samples after the
     *  click can both be the PARKED value, "settle" on it, and hand back a landing of `from`.
     *  Measured twice at three and at six turns — both runs reported the rail landing exactly at the
     *  bottom while the following `toBeInViewport` (which waits) passed, i.e. the scroll was real and
     *  the reading was early. So movement is awaited FIRST and quiet second. */
    const settledTopAfterMoving = async (from: number, what: string): Promise<number> => {
      await expect
        .poll(async () => (await scrollBox(page)).top, {
          message: `${what} never moved the transcript off ${from} — it either resolved no turn node ` +
            '(a coordinate mismatch) or reached no handler',
          timeout: 10_000,
        })
        .not.toBe(from)
      let last = -1
      await expect
        .poll(async () => {
          const now = (await scrollBox(page)).top
          const quiet = now === last
          last = now
          return quiet
        }, { message: 'the transcript never stopped scrolling', timeout: 10_000 })
        .toBe(true)
      return last
    }

    /** Where the target turn ended up INSIDE the scroller — its top edge relative to the
     *  container's, in px.
     *
     *  🪤 DELIBERATELY NOT `scrollTop`. The two jumps are compared against each other, and the
     *  transcript's CONTENT HEIGHT is not constant between them: measured, the scrollable height grew
     *  1339 → 1379 after path A, because this chat's async follow-up pass appends below the last turn
     *  on its own clock. A 40px growth moves the scrollTop that centres a given turn without the
     *  navigation being wrong at all. The turn's position within the viewport is the property the
     *  reader actually experiences and is immune to that. */
    const targetYInScroller = async (): Promise<number> =>
      target.evaluate((el, sel) => {
        const s = document.querySelector(sel)!
        return Math.round(el.getBoundingClientRect().top - s.getBoundingClientRect().top)
      }, SCROLLER)

    /** Drive one surface's jump and report where it put the target turn. Also returns the raw
     *  offset + its maximum, so the caller can refuse a landing that merely CLAMPED. */
    const jumpAndMeasure = async (click: () => Promise<void>, what: string) => {
      const from = await parkAtBottom()
      expect(
        from,
        'the transcript does not overflow beside the open Activity panel, so no jump can move it\n' +
          'and this whole test is vacuous. Drive more turns or shorten the viewport rather than\n' +
          'letting it pass.',
      ).toBeGreaterThan(40)
      await click()
      const top = await settledTopAfterMoving(from, what)
      await expect(target, `${what} did not bring its turn on screen`).toBeInViewport({ timeout: 10_000 })
      const { scrollable } = await scrollBox(page)
      // Neither end. A landing pinned to 0 or to the maximum is reachable by a WRONG coordinate too
      // — which is the one thing this test exists to tell apart — so it is a vacuity failure, not a
      // pass. (Measured: at three turns the target's centred offset WAS the maximum, which is why
      // the fixture above drives six.)
      expect(top, `${what} landed at ${top}, clamped to the top of the transcript`).toBeGreaterThan(0)
      expect(top, `${what} landed at ${top}, clamped to the bottom (max ${scrollable}) — pick a turn further from the ends`).toBeLessThan(scrollable - 40)
      return { top, y: await targetYInScroller() }
    }

    // ── path A — the RAIL tick ──────────────────────────────────────────────────────────────
    const rail = await jumpAndMeasure(() => userMarks.nth(nth).click(), 'the rail tick')

    // ── path B — the FORMER INDEX BEHAVIOUR, same turn, same layout ─────────────────────────
    const index = await jumpAndMeasure(() => anchors.nth(nth).click(), 'the Index anchor')

    // ── THE CLAUSE ──────────────────────────────────────────────────────────────────────────
    // One handler, one coordinate — so one resting place for the turn. A few pixels of tolerance for
    // the smooth-scroll animation's final frame, and nothing more: a coordinate mismatch of one turn
    // is hundreds of pixels at this viewport.
    expect(
      Math.abs(index.y - rail.y),
      `the Index anchor rested the turn at y=${index.y} inside the transcript and the rail tick at\n` +
        `y=${rail.y} (offsets ${index.top} vs ${rail.top}) for the SAME turn. The two surfaces are not\n` +
        'one navigation — either they reach different handlers or they speak different coordinates (SSM-12).',
    ).toBeLessThanOrEqual(4)
  })
})
