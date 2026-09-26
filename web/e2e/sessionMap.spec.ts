import { test, expect, type Locator, type Page } from '@playwright/test'
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
/** The rail's own live region. `jump()` writes `Jumped to message N of M` into it and nothing else
 *  does, so it is the product's statement of WHICH message an activation resolved — the one reading
 *  a test cannot fake by guessing. */
const LIVE = '[data-session-map-live]'
/** The drawn ink of a mark — the 2px line whose LENGTH is the rail's primary channel. */
const LINE = '[data-session-map-mark-line]'
/** The preview card's own root (`SessionMapCard.tsx`), portaled into `document.body`. */
const CARD = '[data-session-map-card]'

/** Scroll geometry of the transcript container, read in the page. */
async function scrollBox(page: Page): Promise<{ top: number; scrollable: number }> {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel)
    if (!el) return { top: -1, scrollable: -1 }
    return { top: el.scrollTop, scrollable: el.scrollHeight - el.clientHeight }
  }, SCROLLER)
}

/** Poll the transcript's offset until it stops changing, and return where it came to rest.
 *
 *  `jumpToTurn` uses `scrollIntoView({behavior:'smooth'})` (`ChatPage.tsx:2432`), so every
 *  post-activation reading has to be taken AFTER the animation, not after a number of
 *  milliseconds. Quiet is the condition; elapsed time is not one.
 *
 *  🪤 THIS ALSO REPLACES THE FOUR `waitForTimeout(250)` PARKS, and they were the file's other
 *  wall-clock dependence. Assigning `scrollTop = scrollHeight` is synchronous, but the offset that
 *  assignment SETTLES at is not: the transcript is still re-laying-out from the turn that just
 *  finished, so `scrollHeight` can move under it. A fixed 250 ms is therefore a bet on the host —
 *  it is generous on a quiet box and a coin flip at 4-5× CPU oversubscription, which is the one
 *  condition this gate runs under. It is also strictly slower in the common case: two equal samples
 *  ~100 ms apart end the wait as soon as the offset is genuinely at rest. Nothing is loosened —
 *  every baseline derived from a park is now a resting value rather than a timed one. */
async function restingOffset(page: Page, what: string): Promise<number> {
  let last = -1
  await expect
    .poll(async () => {
      const now = (await scrollBox(page)).top
      const quiet = now === last
      last = now
      return quiet
    }, { message: `${what}: the transcript never stopped scrolling`, timeout: 10_000 })
    .toBe(true)
  return last
}

/** THE LANDING PROOF, READ OFF THE MARKER THAT WAS ACTIVATED.
 *
 *  (Written when the rail drew a mark for every event; the map now draws one marker per USER message,
 *  but the lesson is the same and so is the proof.)
 *
 *  🪤 WHY THE TARGET IS NEVER A PROMPT NUMBER, AND THIS IS THE DEFECT THE WHOLE FILE SHARED.
 *  Three sites used to activate a mark and then assert that `${PROMPT} (k)` — a prompt number
 *  derived from a LOOP INDEX — had come on screen. That coupling holds only if two things the test
 *  cannot see are both true: that the Nth send became the Nth turn, and that the Nth mark of a kind
 *  belongs to that turn. Both failed in run 35949502119, in the two different ways they can:
 *   · SSM-13 — `driveScriptedTurns(…, 6)` produced FIVE user turns (one send was swallowed into a
 *     live run), so `userMarks.nth(1)` was prompt `(3)` and `(2)` did not exist at all. The jump
 *     was correct — the rail's live region read `Jumped to turn 3 of 10` — and the test failed
 *     with "the rail tick did not bring its turn on screen", naming the rail.
 *   · SSM-15 — `End` lands the roving cursor on the LAST mark, which for a 6-turn session is mark
 *     17 of 18: the newest turn's ACTIVITY mark, whose coordinate is the assistant turn's node.
 *     Space jumped there correctly (scrollTop 0 → 952 of 1271) and the test then demanded a
 *     DIFFERENT element — the newest USER prompt, which sits above that node and is off the top at
 *     the resting offset. Whether it happens to be visible is a function of the last turn's
 *     rendered height, i.e. of host font metrics: a pass there was as unsound as the failure.
 *
 *  So the subject is read from the element under test instead. Two conditions, both deterministic
 *  and both the PRODUCT's own:
 *   1. the rail's live region names the message this marker names (`aria-label` `Message N of M: …`
 *      → `Jumped to message N of M`), which is what proves the activation reached the handler AND
 *      resolved the right coordinate; and
 *   2. the mark is `data-current`, the rail's own "this turn is on screen" reading — driven by
 *      `useVisibleTurns`' `IntersectionObserver` rooted on the transcript (`sessionMapRegion.ts`),
 *      i.e. exactly the coral region a user sees.
 *
 *  🔑 (2) IS STRUCTURAL, NOT PROBABILISTIC, which is the property the prompt-text assertion never
 *  had. `jumpToTurn` centres the target (`block: 'center'`), and a centred element intersects its
 *  scroll root in every clamping case — clamped to 0 its extent overlaps the first band, clamped to
 *  the maximum it overlaps the last. So a working jump always satisfies this and a broken one never
 *  can, at any viewport, on any runner. Callers pair it with the `not.toHaveAttribute` control
 *  below so "it lit up" is a reading of the jump rather than of where the transcript already was. */
async function expectMarkLanded(page: Page, mark: Locator, what: string): Promise<void> {
  const name = (await mark.getAttribute('aria-label')) ?? ''
  const at = /^Message (\d+) of (\d+)\b/.exec(name)
  expect(
    at,
    `${what}: the activated marker carries no "Message N of M" accessible name (aria-label: ` +
      `${JSON.stringify(name)}). \`sessionMapMarkName\` is the one writer of that name (§A.6) — if its\n` +
      'shape changed, fix this reader; do not drop the assertion, because without the turn coordinate\n' +
      'there is nothing to compare the rail\'s announcement against.',
  ).not.toBeNull()
  await expect(
    page.locator(LIVE),
    `${what} REACHED NO JUMP: the rail's live region never announced "Jumped to message ${at![1]} of ` +
      `${at![2]}".\nThe activation either never reached \`jump()\` or resolved a different mark than the ` +
      'one that had the\ncursor — a screen-reader user would be told nothing happened (WCAG 4.1.3).',
  ).toHaveText(`Jumped to message ${at![1]} of ${at![2]}`, { timeout: 10_000 })
  await expect(
    mark,
    `${what} ANNOUNCED A JUMP THAT DID NOT LAND: the rail says it went to turn ${at![1]} of ${at![2]}, but\n` +
      "that mark is still not `data-current` — the rail's own IntersectionObserver does not see its turn\n" +
      'on screen. `jumpToTurn` centres the node it resolves, and a centred node always intersects the\n' +
      'transcript, so this means the coordinate resolved no node (a `turnNodes` key mismatch) or the\n' +
      'scroll never ran.',
  ).toHaveAttribute('data-current', 'true', { timeout: 10_000 })
}

/** The control that makes `expectMarkLanded` non-vacuous: the mark is NOT already lit, so its
 *  turn is not already on screen and "it became current" can only be the activation's doing. */
async function expectMarkNotCurrent(mark: Locator, what: string): Promise<void> {
  await expect(
    mark,
    `${what}: the mark being activated is ALREADY \`data-current\`, so its turn is already on screen and\n` +
      'the landing assertion below would pass without the jump doing anything. Park the transcript\n' +
      'further away rather than letting it through.',
  ).not.toHaveAttribute('data-current', 'true')
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
    await restingOffset(page, 'parking the transcript at its newest turn')
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
    // TWO messages: the map lists user messages and suppresses itself below two (one message has
    // nothing to navigate between), so a one-turn session has no rail to open or close.
    await driveScriptedTurns(page, PROMPT, 2)
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
    await expect(page.locator(RAIL), 'the pointer rail is still mounted at a phone viewport').toHaveCount(0)
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
    await restingOffset(page, 'parking the transcript at its newest turn')
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
    // The owner's rule holds on the phone too: one row per USER message — three sends, three rows —
    // and none for the replies or the work inside them. Named the way the rail names its markers.
    expect(rowCount, 'the drawer lists something other than the user messages').toBe(3)
    // (The subject is capped at 40 characters, so the prompt's own "(n)" is cut off; the position is
    // what tells the rows apart.)
    const names = await rows.evaluateAll((els) => els.map((el) => el.getAttribute('aria-label') ?? ''))
    names.forEach((name, i) => expect(name).toMatch(new RegExp(`^Message ${i + 1} of 3: ${PROMPT.slice(0, 20)}`)))

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

// ── THE RAIL IS OPERABLE FROM THE KEYBOARD ALONE (atom SSM-15) ────────────────────────────────
//
// 🔑 WHY A BROWSER TIER WHEN `SessionMapRail.keyboard.test.tsx` ALREADY COVERS SSM-7. That file is
// honest and stays — but everything it can assert is a jsdom fact. It reaches the roving cursor by
// `fireEvent.keyDown` on the tick it computed to be the tab stop, so it never asks the question a
// keyboard user asks first: is the rail REACHABLE by Tab at all, in the real focus order, sitting
// where it sits between the transcript and the composer? And it proves a jump by a `vi.fn()` spy
// and a stubbed `scrollIntoView`, because jsdom computes no layout — so "Enter jumped" there means
// "a mock was called", not "the transcript moved". Both gaps are the SAME shape as the one
// `sessionMap.spec.ts`'s header records for the other clauses, and neither can be closed under
// jsdom at any effort.
//
// 🪤 MOUSE-FREE MEANS THE WALKTHROUGH, AND THE BOUNDARY IS EXACT. Getting a session to exist runs
// `driveScriptedTurns`, the shared recipe every spec here uses, and it clicks the composer and the
// send control. From the moment the rail is on screen this test touches NO pointer API: no click,
// no hover, no tap, no `mouse.*`. Every rail interaction below is `page.keyboard.press`, which is
// what makes "a keyboard user can operate the session map" a claim this test actually supports —
// and the boundary is ASSERTED, not just described: the census opened after the rail mounts counts
// the click-shaped events for the rest of the walk and requires zero (see its own 🪤 below).
test.describe('Session Map — operable from the KEYBOARD alone (SSM-15)', () => {
  test.describe.configure({ timeout: 180_000 })
  // The SSM-11 block's geometry, for its reasons: short enough that the scripted turns OVERFLOW
  // (so an activation has somewhere to travel), wide enough to stay the pointer form.
  test.use({ viewport: { width: 1280, height: 420 } })

  /** The tick indices carrying `tabIndex=0`. The rail's contract is that this is always exactly
   *  one (§A.6), so it is read as a LIST and asserted to be a singleton rather than searched for. */
  const tabStops = (page: Page) => page.evaluate((sel) => [...document.querySelectorAll(sel)]
    .map((el, i) => [el.getAttribute('tabindex'), i] as const)
    .filter(([t]) => t === '0')
    .map(([, i]) => i), MARK)

  /** Which tick has focus, or -1 when focus is anywhere else. Doubles as the "are we on the rail"
   *  reading, so reach and cursor position are one measurement and cannot disagree. */
  const focusedMark = (page: Page) => page.evaluate((sel) =>
    [...document.querySelectorAll(sel)].indexOf(document.activeElement as Element), MARK)

  /** The focused marker AND the rail's length, read in ONE page evaluation.
   *
   *  🪤 THE MARKER COUNT MUST NOT BE CACHED — it is the rail's own, read at the instant it is used.
   *  This was a live flake while the rail drew a mark for every event: a turn kept emitting marks
   *  after its stream ended (the `Turn complete: …` stats line and the `Injected N chars of context`
   *  line each became a tick), so a count taken early in a walk was a snapshot of a list still growing.
   *  MEASURED on Darwin at 1-min load 125: `End` put focus on index 18 while a count read earlier said
   *  18 marks, and the failure blamed the cursor for the list moving underneath it. With one marker
   *  per user message those late ticks are gone, but a list that can change under a walk (a send lands
   *  a marker) is still read the way that failure taught: position and length as ONE observation. */
  const cursorAndLength = (page: Page) => page.evaluate((sel) => {
    const marks = [...document.querySelectorAll(sel)]
    return { at: marks.indexOf(document.activeElement as Element), length: marks.length }
  }, MARK)

  /** `End` must put the cursor on the rail's LAST tick (§A.6), asserted against the rail's length
   *  as it is at that instant. Returns the index it landed on, for the caller's locator. */
  const expectEndLandsOnLastMark = async (page: Page, what: string): Promise<number> => {
    const { at, length } = await cursorAndLength(page)
    expect(
      at,
      `${what}: End did not put the cursor on the rail's last tick — focus is on index ${at} of ${length}\n` +
        'marks. Both numbers are read in one evaluation, so this is not the list having grown; the\n' +
        "cursor is genuinely somewhere else (§A.6's End goes to `lastIndex`).",
    ).toBe(length - 1)
    return at
  }

  test('Tab reaches the rail, the arrows rove the cursor, and Enter and Space each move the transcript', async ({ page }) => {
    await gotoRoute(page, 'chat')
    // Six turns for SSM-13's reason: enough marks that a cursor move is a real step and the
    // activations below land away from the transcript's ends.
    await driveScriptedTurns(page, PROMPT, 6)
    await expect(page.locator(RAIL), 'the rail never mounted, so there is nothing to operate').toBeVisible()
    const total = await page.locator(MARK).count()
    expect(total, 'the rail carries too few marks for a cursor walk to prove anything').toBeGreaterThan(3)

    // ── THE CENSUS THAT MAKES THIS BLOCK'S MOUSE-FREE CLAIM AN ASSERTION ──────────────────────
    // The 🪤 above the describe states the boundary in prose — "from the moment the rail is on screen
    // this test touches NO pointer API" — and prose is not a gate. Every step below is a
    // `keyboard.press`, and nothing enforces that it stays one: an author who settles a flake by
    // replacing the Tab walk with `page.locator(MARK).nth(0).click()` leaves EVERY assertion here
    // green, because clicking a tick focuses it and activates it too. The test would keep its name
    // and stop proving the rail is reachable without a mouse (WCAG 2.1.1), which is the only clause
    // it exists to make. So from here the walk is MEASURED, not merely written that way.
    //
    // 🪤 OPENED HERE RATHER THAN IN AN `addInitScript`, AND THE PLACEMENT IS THE DESIGN.
    // `driveScriptedTurns` clicks the composer and the send control BY DESIGN — it is the shared way
    // every spec here starts a session, and the prose above excludes it from the claim on purpose —
    // so a census spanning the page's whole life would count six turns of setup clicks and red on
    // its first run. The census opens exactly where the claim does: after the rail is on screen,
    // before the first Tab. Nothing navigates past this point, so a plain `evaluate` survives.
    //
    // 🪤 THE FOUR TYPES ARE A CLOSED LIST, AND THE THREE OMISSIONS ARE EACH A FALSE POSITIVE.
    //   · `click` — a <button> fires one on Enter AND on Space, so the rail's own keyboard
    //     activation would trip it. The ticks are buttons; both activations below are keys.
    //   · `mousemove` / `pointermove` — Chromium re-dispatches a move at the unchanged cursor
    //     position after a scroll, to re-resolve `:hover` under content that moved. This test's
    //     whole payoff is two smooth-scroll jumps with the cursor parked wherever
    //     `driveScriptedTurns` left it, so a move-sensitive census would be measuring the jump.
    // What is left is the click-shaped set: real pointer input produces it, keyboard activation and
    // scrolling never do. That is precisely the "a `.click()` crept in" signal.
    await page.evaluate(() => {
      const w = window as unknown as { __railPointerEvents: string[] }
      w.__railPointerEvents = []
      for (const type of ['pointerdown', 'pointerup', 'mousedown', 'mouseup']) {
        window.addEventListener(type, (e) => { w.__railPointerEvents.push(e.type) }, true)
      }
    })
    /** The click-shaped events seen since the census opened, as `type` strings so a failure names
     *  WHICH gesture crept in and not only how many. Capture-phase on `window`, which runs before
     *  any handler in the tree — so a `stopPropagation` cannot hide one from the count. */
    const pointerEvents = (p: Page) => p.evaluate(() =>
      [...(window as unknown as { __railPointerEvents: string[] }).__railPointerEvents])
    // CONTROL, because listeners that never attached also report zero — the vacuous pass this whole
    // census would otherwise be. Driven by `dispatchEvent` from the body rather than `page.mouse`:
    // a real gesture is the one thing this test may not make, and a bubbling synthetic event travels
    // the SAME window-capture path a trusted one does, which is the only property being controlled.
    await page.evaluate(() => {
      document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
      document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    })
    expect(
      await pointerEvents(page),
      'THE POINTER CENSUS IS INERT: two dispatched click-shaped events recorded nothing, so the zero\n' +
        'asserted at the end of this test would mean "the listeners never attached" rather than "no\n' +
        'mouse was used" — a vacuous pass on the one clause this test owns.',
    ).toEqual(['pointerdown', 'mousedown'])
    await page.evaluate(() => { (window as unknown as { __railPointerEvents: string[] }).__railPointerEvents = [] })

    // ── REACH: Tab from the top of the document, no pointer ───────────────────────────────────
    // Focus is dropped first so the walk starts where a fresh keyboard user starts. The rail sits
    // AFTER the transcript in the focus order, so the count is not small — it is reported rather
    // than asserted, because the clause is "reachable", not "reachable in N".
    await page.evaluate(() => { (document.activeElement as HTMLElement | null)?.blur() })
    const MAX_TABS = 400
    let tabs = 0
    let at = -1
    while (tabs < MAX_TABS) {
      await page.keyboard.press('Tab')
      tabs++
      at = await focusedMark(page)
      if (at >= 0) break
    }
    expect(
      at,
      `THE RAIL IS UNREACHABLE BY KEYBOARD: ${MAX_TABS} Tab presses from the top of the document\n` +
        'never landed on a session mark. The rail is the session\'s only in-session index on a\n' +
        'pointer device, so a keyboard user would have no way into it (WCAG 2.1.1).',
    ).toBeGreaterThanOrEqual(0)
    // …and it is ONE tab stop, which is what makes a 200-tick rail tabbable at all (§A.6).
    expect(await tabStops(page), `the rail is not a single tab stop (reached after ${tabs} tabs)`).toEqual([at])
    // A 4px cursor parked outside the viewport is not a cursor. Browser-only: jsdom has no layout.
    await expect(page.locator(MARK).nth(at), 'the focused tick is off screen').toBeInViewport()

    // ── ROVE: the cursor keys move BOTH focus and the tab stop ────────────────────────────────
    // `Home` first, deliberately: the slot seeds on the CURRENT region, which at the bottom of a
    // six-turn transcript is near the last mark — so a bare `ArrowDown` would clamp and the
    // assertion would be measuring the clamp instead of the step.
    await page.keyboard.press('Home')
    expect(await focusedMark(page), 'Home did not move the cursor to the first mark').toBe(0)
    await page.keyboard.press('ArrowDown')
    expect(await focusedMark(page), 'ArrowDown moved no focus').toBe(1)
    expect(await tabStops(page), 'the roving tab stop did not travel with focus').toEqual([1])
    await page.keyboard.press('ArrowDown')
    expect(await focusedMark(page)).toBe(2)
    await page.keyboard.press('ArrowUp')
    expect(await focusedMark(page), 'ArrowUp did not step back').toBe(1)
    await page.keyboard.press('End')
    const lastMark = await expectEndLandsOnLastMark(page, 'ROVE')
    expect(await tabStops(page)).toEqual([lastMark])

    /** Park the transcript at its newest turn and return that offset — the "from" a jump leaves. */
    const parkAtBottom = async (): Promise<number> => {
      await page.evaluate((sel) => {
        const el = document.querySelector(sel)
        if (el) el.scrollTop = el.scrollHeight
      }, SCROLLER)
      return restingOffset(page, 'parking the transcript at its newest turn')
    }

    const from = await parkAtBottom()
    expect(
      from,
      'the transcript does not overflow, so no activation can move it and both assertions below\n' +
        'are vacuous. Drive more turns or shorten the viewport rather than letting it pass.',
    ).toBeGreaterThan(40)

    // ── ACTIVATE (1): ENTER, on the OLDEST mark, travels UP ───────────────────────────────────
    // `scrollIntoView({behavior:'smooth'})` animates, so movement is polled rather than sampled —
    // the early-read mode SSM-13's block documents in full.
    await page.keyboard.press('Home')
    expect(await focusedMark(page), 'the cursor left the rail before Enter').toBe(0)
    const oldestMark = page.locator(MARK).first()
    await expectMarkNotCurrent(oldestMark, 'ENTER')
    await page.keyboard.press('Enter')
    await expect
      .poll(async () => (await scrollBox(page)).top, {
        message: `ENTER REACHED NO HANDLER: the transcript never left ${from} after Enter on the oldest ` +
          'mark. Keyboard activation of the session map is broken (WCAG 2.1.1).',
        timeout: 10_000,
      })
      .toBeLessThan(from - 40)
    await expectMarkLanded(page, oldestMark, 'ENTER')
    // …and the ONE place a user-visible form of the same claim is structurally available, so it is
    // kept rather than evened out: mark 0's turn is the transcript's first content and
    // `block:'center'` on it clamps to offset 0, where its own text is on screen by construction.
    // The newest mark has no twin for this — see `expectMarkLanded`'s 🪤 — and asserting one anyway
    // is what made this test host-dependent.
    await expect(
      page.getByText(`${PROMPT} (1)`, { exact: false }).first(),
      'Enter moved the transcript but did not bring the oldest turn on screen',
    ).toBeInViewport({ timeout: 10_000 })

    // ── ACTIVATE (2): SPACE, on the NEWEST mark, travels back DOWN ────────────────────────────
    // The opposite direction from a DIFFERENT key, so neither activation can be satisfied by the
    // other's scroll: Enter's pass requires the offset to fall, Space's requires it to rise.
    //
    // 🪤 `landed` IS READ AT REST, NOT AT THE INSTANT THE VIEWPORT CHECK ABOVE PASSED, and that was
    // the second half of this test's timing dependence. `toBeInViewport` resolves the moment the
    // element ENTERS the viewport, which during a smooth scroll is well before the scroll ends — so
    // Space's `> landed + 40` was measured against a baseline that was still moving, and on a
    // loaded runner the two could be samples of the same animation.
    const landed = await restingOffset(page, 'after ENTER')
    await page.keyboard.press('End')
    const newestIndex = await expectEndLandsOnLastMark(page, 'before SPACE')
    const newestMark = page.locator(MARK).nth(newestIndex)
    await expectMarkNotCurrent(newestMark, 'SPACE')
    await page.keyboard.press(' ')
    await expect
      .poll(async () => (await scrollBox(page)).top, {
        message: `SPACE REACHED NO HANDLER: the transcript never left ${landed} after Space on the ` +
          'newest mark, though Enter had just moved it — so the rail handles Enter and not Space.',
        timeout: 10_000,
      })
      .toBeGreaterThan(landed + 40)
    await expectMarkLanded(page, newestMark, 'SPACE')

    // ── AND NOT ONE POINTER EVENT HAPPENED ────────────────────────────────────────────────────
    // The assertion that makes every step above mean "by keyboard" rather than "somehow". Read as
    // the LIST, not the length, so a failure names the gesture instead of a bare count.
    const seen = await pointerEvents(page)
    expect(
      seen,
      `the page saw ${seen.length} click-shaped event(s) (${[...new Set(seen)].join(', ')}) during a\n` +
        'walk that claims to use no mouse. Either a `.click()`/`.tap()` crept into this test or a\n' +
        'helper it calls now uses one — either way the walk no longer proves the rail is\n' +
        'keyboard-operable, and every other assertion here would still be green (WCAG 2.1.1).',
    ).toEqual([])
  })

  // ── THE GROUND `schemeContrast.test.ts` MEASURES THE MARK TONES AGAINST ────────────────────
  // SSM-15's contrast half asserts both mark tones over `--color-canvas` in all 12 schemes, and
  // that ground is a claim about LAYOUT: it is only the right number while nothing between the
  // rail and the shell paints a surface of its own. A vitest file cannot check that (jsdom
  // computes no backgrounds), so a re-parent of the rail onto `bg-surface` would leave 24 green
  // assertions quietly measuring the wrong backdrop. Read from the real paint here instead, so
  // the drift reds ONCE, loudly, next to the thing that moved.
  test('the rail is painted on the ground the 12-scheme contrast guard measures', async ({ page }) => {
    await gotoRoute(page, 'chat')
    await driveScriptedTurns(page, PROMPT, 3)
    await expect(page.locator(RAIL)).toBeVisible()

    const ground = await page.evaluate((railSel) => {
      const hexToRgb = (hex: string) => {
        const h = hex.trim().replace('#', '')
        const n = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16))
        return `rgb(${n[0]}, ${n[1]}, ${n[2]})`
      }
      // The nearest ancestor that actually PAINTS. `transparent` and a zero-alpha rgba both mean
      // "the layer below shows through", so both are walked past rather than reported.
      const opaque = (c: string) => !!c && c !== 'transparent' && !/^rgba\(.*,\s*0\s*\)$/.test(c)
      let el: Element | null = document.querySelector(railSel)
      const chain: string[] = []
      while (el) {
        const bg = getComputedStyle(el).backgroundColor
        chain.push(`${el.tagName.toLowerCase()}=${bg}`)
        if (opaque(bg)) {
          return {
            painted: bg,
            paintedBy: el.tagName.toLowerCase(),
            canvas: hexToRgb(getComputedStyle(document.documentElement).getPropertyValue('--color-canvas')),
            chain,
          }
        }
        el = el.parentElement
      }
      return { painted: null, paintedBy: null, canvas: null, chain }
    }, RAIL)

    expect(
      ground.painted,
      `nothing between the Session Map rail and the document root paints a background, so the\n` +
        `contrast guard has no ground at all. Chain: ${ground.chain.join(' → ')}`,
    ).not.toBeNull()
    expect(
      ground.painted,
      `THE RAIL'S GROUND MOVED. \`src/design/schemeContrast.test.ts\` asserts both session map mark\n` +
        `tones over --color-canvas (${ground.canvas}) in all 12 schemes, but the rail is now painted\n` +
        `on ${ground.painted} by <${ground.paintedBy}>. Point that guard's RAIL_GROUND at the token\n` +
        `this really is and re-measure — do not delete either assertion.\n` +
        `Chain: ${ground.chain.join(' → ')}`,
    ).toBe(ground.canvas)
  })
})

test.describe('Session Map — it is the session\'s ONLY index (SSM-13)', () => {
  test.describe.configure({ timeout: 180_000 })
  // Same geometry as the SSM-11 block: short enough that the scripted turns overflow, wide enough
  // to stay the pointer form (`useIsMobile` is max-width 768).
  test.use({ viewport: { width: 1280, height: 420 } })

  // 🔑 WHY THIS BLOCK REPLACED SSM-12's. SSM-12 drove the rail tick and the Activity → Index anchor
  // for the same turn and asserted they rested it in the same place — the proof that the two
  // surfaces were one navigation, and therefore that deleting one lost nothing. SSM-13 deleted it,
  // so that comparison has only one side left. What survives is the claim the deletion has to make
  // good on: the panel no longer offers an index, AND the rail still lands a mid-session turn. A
  // vitest file cannot make the second half honestly (jsdom computes no layout and `ChatPage` is not
  // mountable there), so it stays here. `src/pages/chat/indexTabRetired.test.tsx` owns the absences.
  //
  // 🪤 THE ACTIVITY PANEL IS OPENED BEFORE THE JUMP, AND THAT ORDER IS LOAD-BEARING. It is a docked
  // `SidePanel` flex sibling, so opening it narrows the transcript and re-flows it — a landing
  // measured at full width is not comparable with one measured beside an open panel. Opening it
  // first also means the no-Index assertion and the jump are made of one layout.
  test('the Activity panel offers no Index tab, and the rail still lands a mid-session turn', async ({ page }) => {
    await gotoRoute(page, 'chat')
    // SIX turns, and the count is load-bearing. The target below must be a turn whose centred offset
    // is neither 0 nor the maximum, or a wrong coordinate would clamp to the same place and the
    // measurement would stop discriminating. MEASURED at three turns: the second user turn centres
    // at 674 against a scrollable height of 674 — pinned to the bottom, i.e. exactly the degenerate
    // case. Six turns put it around a quarter of the way down.
    await driveScriptedTurns(page, PROMPT, 6)

    await expect(page.locator(RAIL)).toBeVisible()
    expect(
      await openHeaderOverflowIfNeeded(page, 'Activity'),
      'the "Activity" control is reachable nowhere in the header, so the panel cannot be inspected',
    ).toBe(true)
    await page.getByRole('button', { name: 'Activity', exact: true }).click()

    // ── CLAUSE 1: the panel has no Index tab and opens on Files ──────────────────────────────
    // CONTROL FIRST: the panel really opened, on the tab that is now its default. Without it, a
    // panel that never rendered would satisfy every absence below.
    await expect(
      page.locator('#act-panel-files'),
      'the Activity panel did not open on its Files tab',
    ).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('#act-panel-index'), 'the Activity panel still mounts an index tabpanel').toHaveCount(0)
    await expect(page.getByRole('tab', { name: 'Index' }), 'the Activity panel still renders an "Index" tab').toHaveCount(0)
    // The list body, by content: a reinstated outline under any other tab name still fails here.
    // The panel is scoped so the assertion cannot be satisfied by the transcript's own copy of the
    // prompt, which is on screen throughout.
    const panel = page.locator('#act-panel-files')
    await expect(
      panel.getByText(PROMPT, { exact: false }),
      'the Activity panel still lists user turns as jump anchors',
    ).toHaveCount(0)

    // ── CLAUSE 2: the rail is the index now, and its jump works ──────────────────────────────
    // Every marker IS a user message now (one per question), so the rail's markers are the list the
    // Index tab used to show.
    const userMarks = page.locator(MARK)
    const markCount = await userMarks.count()
    expect(markCount, 'the rail carries no markers, so it is not indexing the turns the Index tab listed').toBeGreaterThan(1)

    // The MIDDLE user mark, never the first: `scrollIntoView({block:'center'})` clamps the oldest
    // turn to offset 0, where a wrong coordinate lands in the same place.
    //
    // 🪤 THE SUBJECT IS THE MARK, NOT `${PROMPT} (nth + 1)`. This line used to derive the expected
    // turn from the loop index, which assumes the Nth send became the Nth turn. In run 35949502119
    // it had not — see `expectMarkLanded`'s 🪤 — and the test failed naming the rail for a turn that
    // was never sent. `driveScriptedTurns` now reds on that loss at the send that lost it; this
    // site additionally stops depending on the numbering at all.
    const nth = 1
    const targetMark = userMarks.nth(nth)

    /** Park at the newest turn and return that offset — the "from" the jump travels out of. */
    const from = await (async () => {
      await page.evaluate((sel) => {
        const el = document.querySelector(sel)
        if (el) el.scrollTop = el.scrollHeight
      }, SCROLLER)
      return restingOffset(page, 'parking the transcript at its newest turn')
    })()
    expect(
      from,
      'the transcript does not overflow beside the open Activity panel, so no jump can move it and\n' +
        'this whole test is vacuous. Drive more turns or shorten the viewport rather than letting it pass.',
    ).toBeGreaterThan(40)

    await expectMarkNotCurrent(targetMark, 'the rail tick')
    await targetMark.click()

    // 🪤 POLLING FOR QUIET ALONE READS THE OFFSET THE JUMP HAS NOT LEFT YET, and that mode is
    // invisible: `scrollIntoView({behavior:'smooth'})` animates, so the first two samples after the
    // click can both be the PARKED value, "settle" on it, and report a landing of `from`. Measured
    // twice while SSM-12 was built — both runs reported a landing exactly at the bottom while the
    // following viewport check (which waits) passed, i.e. the scroll was real and the reading was
    // early. So movement is awaited FIRST and quiet second.
    await expect
      .poll(async () => (await scrollBox(page)).top, {
        message: `the rail tick never moved the transcript off ${from} — it either resolved no turn ` +
          'node (a coordinate mismatch) or reached no handler',
        timeout: 10_000,
      })
      .not.toBe(from)
    const last = await restingOffset(page, 'the rail tick')

    await expectMarkLanded(page, targetMark, 'the rail tick')
    const { scrollable } = await scrollBox(page)
    // Neither end. A landing pinned to 0 or to the maximum is reachable by a WRONG coordinate too,
    // so it is a vacuity failure rather than a pass.
    expect(last, `the rail tick landed at ${last}, clamped to the top of the transcript`).toBeGreaterThan(0)
    expect(last, `the rail tick landed at ${last}, clamped to the bottom (max ${scrollable}) — pick a turn further from the ends`).toBeLessThan(scrollable - 40)
  })
})

// ── THE OWNER'S FORM (2026-09-25), MEASURED WHERE LAYOUT EXISTS ───────────────────────────────
//
// 🔴 WHY EVERY CLAUSE BELOW IS A BROWSER CLAUSE AND NOT A VITEST ONE. The rules are about what a
// reader SEES — one uniform length at rest, the on-screen messages told apart by colour, the marker
// under the pointer or the cursor expanding and brightening — and length and colour are rendered
// numbers: `scaleX` is a transform jsdom never applies, and `var(--color-*)` resolves to nothing
// there. The unit files pin the VALUES the rail hands the animation and paint layers
// (`SessionMapRail.test.tsx`, `.target.test.tsx`, `.reducedMotion.test.tsx`, `schemeContrast.test.ts`);
// these read the painted result.

/** Every marker's painted line length, in CSS px, in rail order. Includes the `scaleX`, which is the
 *  entire point: this is the number a reader's eye is given. */
async function lineLengths(page: Page): Promise<number[]> {
  return page.$$eval(`${RAIL} ${LINE}`, (els) =>
    els.map((el) => Math.round(el.getBoundingClientRect().width * 100) / 100))
}

/** Poll the rail's painted lengths until they stop changing, and return where they came to rest.
 *
 *  🪤 A LENGTH READ WHILE A MARKER IS MOVING MEASURES THE SPRING, NOT THE DESIGN — and it is the
 *  regression this file shipped with. The length animates on `physics.snappy`, a spring that takes
 *  about half a second to land and overshoots on the way, and the two tests that assert the SHAPE of
 *  the pointed-at marker awaited only the first sample past a threshold (`> rest`, `> full`) before
 *  asserting the settled shape. A threshold is crossed early in the travel, so the comparison that
 *  followed was made mid-flight. Measured frame by frame on the form this replaced (Darwin, from the
 *  real `mouseenter`): the hovered mark passed the poll's threshold at ~110 ms while its ±1 neighbour
 *  was still at 17.2-18.1px on its way to 18.6, against a ±2 neighbour resting at 18.24; the cursor
 *  test read mark 0 at 12.15px, one frame above its 12.12 rest, on its way to 24. Same numbers at the
 *  commit that added both tests (4 of 6 runs red there) and at every later one — never a regression
 *  of the product, a race the host decided (its CI runner happened to sample late and pass).
 *
 *  Quiet is the condition, as in `restingOffset`: two equal samples at least 100ms apart. Callers
 *  await MOVEMENT first (their threshold poll), because two samples taken before the marker starts
 *  moving are equal too, and would "settle" on the rail it has not left yet.
 *
 *  `settleEntranceAnimations` cannot stand in for this: it watches inline `opacity` and
 *  `document.getAnimations()`, and framer drives `scaleX` from its own frame loop — measured, zero
 *  Web Animations on the marker lines in any frame — which neither sees. */
async function restingLengths(page: Page, what: string): Promise<number[]> {
  let last = ''
  let lengths: number[] = []
  await expect
    .poll(async () => {
      lengths = await lineLengths(page)
      const now = lengths.join(',')
      const quiet = now === last
      last = now
      return quiet
    }, { message: `${what}: the rail's markers never stopped changing length`, timeout: 10_000 })
    .toBe(true)
  return lengths
}

/** Every marker's state as ONE reading: the token the rail chose, what it resolves to on screen,
 *  and whether the marker says it is on screen. */
async function markerState(page: Page): Promise<Array<{ token: string; rgb: string; current: boolean }>> {
  return page.$$eval(`${RAIL} ${MARK}`, (els) => els.map((el) => ({
    token: (el as HTMLElement).style.color,
    rgb: getComputedStyle(el).color,
    current: el.getAttribute('data-current') === 'true',
  })))
}

/** A CSS colour as the page resolves it — `var(--color-canvas)` → `rgb(…)`. */
async function resolved(page: Page, css: string): Promise<string> {
  return page.evaluate((value) => {
    const probe = document.createElement('span')
    probe.style.color = value
    document.body.appendChild(probe)
    const rgb = getComputedStyle(probe).color
    probe.remove()
    return rgb
  }, css)
}

/** WCAG 2.1 contrast between two computed `rgb(…)` colours. */
function contrastOf(a: string, b: string): number {
  const lum = (rgb: string) => {
    const [r, g, bl] = (rgb.match(/[\d.]+/g) ?? []).slice(0, 3).map((v) => Number(v) / 255)
    const ch = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(bl)
  }
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

test.describe('Session Map — the owner\'s form (2026-09-25)', () => {
  test.describe.configure({ timeout: 180_000 })
  // 1280 keeps `useIsMobile` false, so this is the pointer form and not SSM-10's drawer.
  test.use({ viewport: { width: 1280, height: 600 } })

  test('a marker owns its whole row — 32×24, contiguous, on one constant pitch', async ({ page }) => {
    await gotoRoute(page, 'chat')
    await driveScriptedTurns(page, PROMPT, 3)
    await expect(page.locator(RAIL)).toBeVisible()

    const rows = await page.$$eval(MARK, (els) => els.map((el) => {
      const r = el.getBoundingClientRect()
      return { top: r.top, bottom: r.bottom, w: r.width, h: r.height }
    }))
    // One marker per user message: three sends, three markers.
    expect(rows.length, 'the rail drew a marker for something other than a user message').toBe(3)
    for (const [i, r] of rows.entries()) {
      // SC 2.5.8: 24×24 at least — the reference's "precise pointer movement required" friction.
      // Floors, so a density or width change is free to make it BIGGER without reding.
      expect(r.w, `marker ${i} is ${r.w}px wide`).toBeGreaterThanOrEqual(32)
      expect(r.h, `marker ${i} is ${r.h}px tall — under SC 2.5.8's 24px`).toBeGreaterThanOrEqual(24)
    }
    // Contiguous: each row starts where the previous one ended, so every pixel of the rail belongs
    // to a marker — no pointer-dead band between targets.
    for (let i = 1; i < rows.length; i++) {
      expect(Math.abs(rows[i].top - rows[i - 1].bottom), `a gap between marker ${i - 1} and ${i}`).toBeLessThanOrEqual(1)
    }
    const pitches = rows.slice(1).map((r, i) => Math.round(r.top - rows[i].top))
    expect(new Set(pitches).size, `the pitch varies across the rail: ${pitches.join(', ')}`).toBe(1)
  })

  test('🔑 at rest every marker is ONE length, and what is on screen is told by COLOUR — scrolling moves the colour, never the geometry', async ({ page }) => {
    await gotoRoute(page, 'chat')
    await driveScriptedTurns(page, PROMPT, 4)
    await expect(page.locator(RAIL)).toBeVisible()
    await settleEntranceAnimations(page)

    const rest = await restingLengths(page, 'the idle rail')
    expect(rest.length).toBe(4)
    expect(new Set(rest).size, `resting lengths differ: ${rest.join(', ')}`).toBe(1)
    expect(rest[0], 'a resting marker is a visible line').toBeGreaterThanOrEqual(6)
    expect(rest[0], 'and not the full line — that is reserved for the marker being pointed at').toBeLessThan(24)
    // …and every one is painted at FULL strength: none sits inside the list's edge fade, which would
    // composite its tone toward the canvas and spend the contrast a colour-only rail depends on.
    // (Measured before the fix: the first and last lines sat 12px into a 20px fade, at 60% alpha.)
    const fade = await page.evaluate(([rail, line]) => {
      const list = document.querySelector(`${rail} [data-session-map-list]`) as HTMLElement
      const box = list.getBoundingClientRect()
      // The MASK's fade distance, resolved on its own — the rail's EDGE_FADE is `var(--spacing-xl)`
      // (pinned in `SessionMapRail.target.test.tsx`) — so this reads the defect, not the fix.
      const probe = document.createElement('div')
      probe.style.height = 'var(--spacing-xl)'
      document.body.appendChild(probe)
      const distance = probe.getBoundingClientRect().height
      probe.remove()
      const inFade = [...list.querySelectorAll(line)].map((el) => el.getBoundingClientRect())
        .filter((r) => r.top < box.top + distance - 0.5 || r.bottom > box.bottom - distance + 0.5).length
      return { distance, inFade, overflows: list.scrollHeight > list.clientHeight }
    }, [RAIL, LINE] as const)
    expect(fade.overflows, 'four messages overflow the rail, so it cannot be checked at rest here').toBe(false)
    expect(fade.distance, 'the fade distance resolved to nothing').toBeGreaterThan(0)
    expect(fade.inFade, 'a resting marker sits inside the edge fade, dimmed toward the canvas').toBe(0)

    // At the newest message, the newest exchange is on screen and the first is not: BOTH colours
    // are on the rail, which is what makes "the lengths are equal" a claim about colour doing the work.
    const atBottom = await markerState(page)
    const lit = atBottom.filter((m) => m.current)
    const unlit = atBottom.filter((m) => !m.current)
    expect(lit.length, 'nothing is marked on screen').toBeGreaterThan(0)
    expect(unlit.length, 'everything is marked on screen, so colour distinguishes nothing').toBeGreaterThan(0)
    for (const m of lit) expect(m.token).toBe('var(--color-primary)')
    for (const m of unlit) expect(m.token).toBe('var(--color-map-rest)')
    // …and the two colours really differ ON SCREEN, in lightness and not only in hue — the painted
    // measurement of `schemeContrast.test.ts`'s floor for this scheme.
    expect(contrastOf(lit[0].rgb, unlit[0].rgb), `on-screen ${lit[0].rgb} vs ${unlit[0].rgb}`).toBeGreaterThanOrEqual(1.7)
    // Each clears SC 1.4.11 on the canvas the rail sits on.
    const canvas = await resolved(page, 'var(--color-canvas)')
    for (const m of [lit[0], unlit[0]]) expect(contrastOf(m.rgb, canvas), `${m.token} on the canvas`).toBeGreaterThanOrEqual(3)

    // Scroll to the FIRST message. The colour must move with the transcript…
    const before = atBottom.map((m) => m.current)
    await page.evaluate((sel) => { document.querySelector(sel)!.scrollTop = 0 }, SCROLLER)
    await restingOffset(page, 'parking the transcript at its first message')
    await expect
      .poll(async () => (await markerState(page)).map((m) => m.current).join(','), {
        message: 'scrolling the transcript moved nothing on the rail — the colour does not follow the viewport',
        timeout: 10_000,
      })
      .not.toBe(before.join(','))
    const atTop = await markerState(page)
    expect(atTop[0].current, 'the first message is on screen and is not lit').toBe(true)
    // …and the GEOMETRY must not: every marker is still exactly the resting length it was.
    expect(await restingLengths(page, 'the rail after scrolling'), 'scrolling changed a marker\'s size').toEqual(rest)
  })

  test('🔑 only the marker under the pointer expands and brightens — its neighbours stay put — and it lets go', async ({ page }) => {
    await gotoRoute(page, 'chat')
    await driveScriptedTurns(page, PROMPT, 4)
    await expect(page.locator(RAIL)).toBeVisible()
    await settleEntranceAnimations(page)

    const rest = await restingLengths(page, 'the idle rail')
    const restState = await markerState(page)
    const canvas = await resolved(page, 'var(--color-canvas)')
    const marks = page.locator(`${RAIL} ${MARK}`)
    const at = 1
    await marks.nth(at).hover()
    // Movement first, then rest — see `restingLengths` for why the order and the second wait exist.
    await expect
      .poll(async () => (await lineLengths(page))[at], { message: 'the hovered marker never expanded' })
      .toBeGreaterThan(rest[at])
    const hovered = await restingLengths(page, 'the marker under the pointer')

    expect(hovered[at], 'the hovered marker must expand beyond the uniform length').toBeGreaterThan(rest[at])
    expect(hovered[at], 'and be the longest line on the rail').toBe(Math.max(...hovered))
    // 🔑 ONLY IT. Every other marker is exactly where it rested — no ±1/±2/±3 falloff.
    hovered.forEach((len, i) => {
      if (i !== at) expect(len, `marker ${i} moved when marker ${at} was hovered`).toBe(rest[i])
    })
    // It BRIGHTENS: its colour now out-contrasts its resting colour on the canvas.
    const lit = (await markerState(page))[at]
    expect(lit.token, 'the hovered marker kept its resting tone').not.toBe(restState[at].token)
    expect(contrastOf(lit.rgb, canvas), 'the hovered marker dimmed instead of brightening')
      .toBeGreaterThan(contrastOf(restState[at].rgb, canvas))

    // It lets go: moving the pointer off the rail returns it to the uniform length and its tone.
    await page.mouse.move(900, 300)
    await expect
      .poll(async () => (await lineLengths(page))[at], { message: 'the hovered marker never let go' })
      .toBe(rest[at])
    expect(await restingLengths(page, 'the rail after the pointer left')).toEqual(rest)
    expect((await markerState(page))[at].token).toBe(restState[at].token)
  })

  test('the card opens BESIDE the rail, never over the markers below it', async ({ page }) => {
    await gotoRoute(page, 'chat')
    await driveScriptedTurns(page, PROMPT, 4)
    await expect(page.locator(RAIL)).toBeVisible()

    const marks = page.locator(`${RAIL} ${MARK}`)
    await marks.nth(2).hover()
    const card = page.locator(CARD)
    await expect(card, 'hovering a marker must preview it').toBeVisible({ timeout: 10_000 })
    // The owner's card: the request, then a muted excerpt of the reply's opening, and a timestamp.
    await expect(card.locator('[data-session-map-request]')).toContainText(`${PROMPT} (3)`)
    await expect(card.locator('[data-session-map-response]')).toContainText('SCRIPTED-E2E-OK')
    await expect(card.locator('time')).toHaveCount(1)

    const railBox = (await page.locator(RAIL).boundingBox())!
    const cardBox = (await card.boundingBox())!
    // 🔑 THE WHOLE REASON `ui/Popover` GAINED `placement="right"`: a card placed below a marker
    // covers the markers under it, and the sweep-down-the-rail gesture stops working. Asserted as a
    // non-intersection so it holds however the card is sized.
    expect(
      cardBox.x,
      `the card starts at x=${cardBox.x}, inside the rail (${railBox.x}..${railBox.x + railBox.width}) — ` +
        'it is covering the markers it is supposed to be describing',
    ).toBeGreaterThanOrEqual(railBox.x + railBox.width)
    // And it is CENTRED on the marker rather than hanging off it. Tolerance is generous: the card is
    // clamped into the viewport, so an edge marker's card legitimately slides.
    const markBox = (await marks.nth(2).boundingBox())!
    const markMid = markBox.y + markBox.height / 2
    const cardMid = cardBox.y + cardBox.height / 2
    const clamped = cardBox.y <= 8 || cardBox.y + cardBox.height >= 600 - 8
    if (!clamped) {
      expect(Math.abs(cardMid - markMid), `the card's centre is ${Math.abs(cardMid - markMid)}px off the marker's`)
        .toBeLessThanOrEqual(cardBox.height / 2)
    }
  })

  test('under prefers-reduced-motion the pointed-at marker still EXPANDS — it just does not travel there', async ({ page }) => {
    // "Collapses to instant" is pinned where it is falsifiable — `SessionMapRail.reducedMotion.test.tsx`
    // asserts the rail hands framer `physics.snappy` and that under the query that getter IS
    // `instant`. What a browser CAN say, and what matters to a user who has motion off, is that the
    // marker still expands and the card still previews with the animation gone.
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await gotoRoute(page, 'chat')
    await driveScriptedTurns(page, PROMPT, 3)
    await expect(page.locator(RAIL)).toBeVisible()

    const rest = await lineLengths(page)
    const marks = page.locator(`${RAIL} ${MARK}`)
    const at = 1
    await marks.nth(at).hover()
    await expect
      .poll(async () => (await lineLengths(page))[at], { message: 'with motion off the marker never expanded at all' })
      .toBeGreaterThan(rest[at])
    await expect(page.locator(CARD)).toBeVisible({ timeout: 10_000 })
  })

  test('the roving cursor is VISIBLE — its marker expands, brightens and wears the ring; the rest stay uniform', async ({ page }) => {
    // The guarantee `getByRole` cannot give. `SessionMapRail.keyboard.test.tsx` proves the cursor
    // moves and that Enter jumps; neither says the moved-to marker looks any different. The marker
    // under the cursor expands AND brightens AND takes the global `:focus-visible` ring — the ring is
    // the platform's channel and the expansion the design's, and the rail relies on neither alone.
    await gotoRoute(page, 'chat')
    await driveScriptedTurns(page, PROMPT, 4)
    await expect(page.locator(RAIL)).toBeVisible()

    const rest = await restingLengths(page, 'the idle rail')
    expect(new Set(rest).size, 'the rail is not uniform at rest').toBe(1)
    const restState = await markerState(page)
    // Focus the rail's single tab stop directly, then rove with the keys — the same path
    // `SessionMapRail.keyboard.test.tsx` drives, here for its VISIBLE effect.
    await page.locator(`${RAIL} ${MARK}[tabindex="0"]`).first().focus()
    await page.keyboard.press('Home')
    // Movement first, then rest — see `restingLengths` for why the order and the second wait exist.
    await expect
      .poll(async () => (await lineLengths(page))[0], { message: 'the cursor’s own marker never expanded' })
      .toBeGreaterThan(rest[0])
    const lengths = await restingLengths(page, 'the marker under the cursor')
    expect(lengths[0], 'the cursor’s marker must be the longest on the rail').toBe(Math.max(...lengths))
    lengths.forEach((len, i) => {
      if (i !== 0) expect(len, `marker ${i} is not at the uniform length while the cursor is on 0`).toBe(rest[i])
    })
    // Brightened, in the painted colour:
    const canvas = await resolved(page, 'var(--color-canvas)')
    const cursorState = (await markerState(page))[0]
    expect(contrastOf(cursorState.rgb, canvas), 'the cursor’s marker did not brighten')
      .toBeGreaterThan(contrastOf(restState[0].rgb, canvas))
    // And the ring really lands: no `outline-none` anywhere on the marker, so the app-wide
    // `:focus-visible` outline paints. Read from the real computed style, which is why this is here.
    const outline = await page.evaluate((sel) => {
      const el = document.querySelector(`${sel} [data-session-mark]:focus-visible`) as HTMLElement | null
      if (!el) return null
      const s = getComputedStyle(el)
      return { style: s.outlineStyle, width: s.outlineWidth }
    }, RAIL)
    expect(outline, 'no marker holds :focus-visible after Home — the cursor is not really focused').not.toBeNull()
    expect(outline!.style, 'the marker killed its outline; it must inherit the app-wide ring').not.toBe('none')
    expect(parseFloat(outline!.width), 'a 0px ring is no ring').toBeGreaterThan(0)
  })

  test('🔑 reading an answer keeps its question lit — after the question itself has scrolled away', async ({ page }) => {
    // The owner's rule for the on-screen colour: it follows the user message whose EXCHANGE is in
    // view. While a long answer is being read its question has scrolled off the top, and a rail that
    // lit only the markers whose own message is on screen went dark in exactly that state.
    await gotoRoute(page, 'chat')
    await driveScriptedTurns(page, PROMPT, 4)
    await expect(page.locator(RAIL)).toBeVisible()

    // Park the transcript so the SECOND question's bubble sits just above the top edge and its reply
    // is the first thing on screen.
    const parked = await page.evaluate((sel) => {
      const scroller = document.querySelector(sel) as HTMLElement
      const questions = [...scroller.querySelectorAll('[data-transcript-turn="user"]')] as HTMLElement[]
      const question = questions[1]
      const reply = question.nextElementSibling as HTMLElement | null
      scroller.scrollTop += question.getBoundingClientRect().bottom - scroller.getBoundingClientRect().top + 2
      return { questions: questions.length, replyIsAssistant: reply?.getAttribute('data-transcript-turn') === 'assistant' }
    }, SCROLLER)
    expect(parked.questions, 'the transcript does not carry four question turns').toBe(4)
    expect(parked.replyIsAssistant, 'the turn after the second question is not its reply').toBe(true)
    await restingOffset(page, 'parking the second answer at the top of the transcript')

    // The controls that make the assertion below about THIS state: the question is off screen and its
    // answer is on it.
    const geometry = await page.evaluate((sel) => {
      const scroller = document.querySelector(sel) as HTMLElement
      const top = scroller.getBoundingClientRect().top
      const question = scroller.querySelectorAll('[data-transcript-turn="user"]')[1] as HTMLElement
      const reply = question.nextElementSibling as HTMLElement
      return { questionBottom: question.getBoundingClientRect().bottom - top, replyBottom: reply.getBoundingClientRect().bottom - top }
    }, SCROLLER)
    expect(geometry.questionBottom, 'the second question is still on screen, so this proves nothing').toBeLessThanOrEqual(0)
    expect(geometry.replyBottom, 'the second answer is not on screen').toBeGreaterThan(0)

    const second = page.locator(`${RAIL} ${MARK}`).nth(1)
    await expect(second, 'the answer is on screen and its question is not lit').toHaveAttribute('data-current', 'true', { timeout: 10_000 })
    await expect(second).toHaveAttribute('aria-current', 'true')
    // Only the exchanges on screen: the first question, wholly above, is not lit.
    await expect(page.locator(`${RAIL} ${MARK}`).nth(0)).not.toHaveAttribute('data-current', 'true')
  })
})
