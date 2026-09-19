import { describe, it, expect, vi } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { render } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatActivityPanel } from './ChatActivityPanel'
import { SessionMapRail } from './SessionMapRail'
import { hydrateTurns, deriveActivity, type HistMsg } from './chatTypes'
import { sessionMapMarks } from './sessionMap'

// ── SSM-13 — THE SUPERSEDED ACTIVITY → INDEX TAB IS GONE, AND STAYS GONE ─────────────────────
//
// The atom's clause: "the 'Index' tab and its list body are deleted from `ChatActivityPanel.tsx`;
// a test asserts no 'Index' tab is rendered and that `deriveActivity().index` has no remaining
// consumer other than SSM-1's model (grep-style assertion), leaving no dual path."
//
// 🔑 WHAT A DELETION ATOM'S PROOF HAS TO BE. There is no new behaviour to observe, so the only
// honest evidence is an ABSENCE — and an absence is exactly the claim that passes for the wrong
// reason. Every block below therefore carries a CONTROL: a positive assertion, made with the same
// query or the same scanner, that would fail if the query/scan were broken, mis-pathed or pointed
// at an empty fixture. A block without a control could not tell "the Index tab is gone" from "the
// panel did not render" or "the walker read no files".
//
// 🔴 AND THE DELETION IS ONLY SAFE BECAUSE A SURVIVOR EXISTS. The Index tab was the session's
// in-session index. Removing it would be a REGRESSION rather than a clean break if the Session Map
// were not there to be it — which is the whole reason this atom is gated behind SSM-12 (one jump
// handler, one coordinate, both surfaces). So the last two blocks assert the survivor positively:
// the rail still reaches `ChatPage`'s jump handler with a mark's coordinate, and `ChatPage` still
// hands it that handler. Deleting the tab without those two passing would leave the reader with no
// way to navigate a long session at all.
//
// WHAT THIS FILE DOES NOT OWN. The jump's SCROLL — jsdom computes no layout and `ChatPage` is not
// mountable there. `e2e/sessionMap.spec.ts`'s SSM-13 block drives the real browser: it asserts the
// panel has no Index tab and that the rail lands a mid-session turn off both clamp ends.

const WEB = process.cwd()
const SRC = join(WEB, 'src')
const E2E = join(WEB, 'e2e')
/** This file's own path, excluded from the tree scan below — it necessarily spells out every
 *  pattern it searches for. The exclusion is asserted to have removed exactly this one file. */
const SELF = join('src', 'pages', 'chat', 'indexTabRetired.test.tsx')

/** A real transcript that WOULD have produced Index rows: two user turns (the outline's only
 *  source), plus assistant prose carrying a path and a URL so the surviving tabs are non-empty.
 *  Built through `hydrateTurns`, not by hand, so the turn shapes are the ones the page renders. */
const MESSAGES: HistMsg[] = [
  { role: 'user', content: 'run the build' },
  { role: 'assistant', content: 'starting' },
  { role: 'assistant', content: 'done — touched src/pages/chat/sessionMap.ts, see https://example.com/build' },
  { role: 'user', content: 'now the tests' },
  { role: 'assistant', content: 'all green' },
]

const TURNS = hydrateTurns(MESSAGES)
const ACTIVITY = deriveActivity(TURNS)
const MARKS = sessionMapMarks(TURNS)

const USER_TEXTS = ['run the build', 'now the tests']
const A_FILE = 'src/pages/chat/sessionMap.ts'

describe('SSM-13 — the Activity panel renders no Index tab', () => {
  it('🔴 the fixture HAS the user turns an Index tab would have listed', () => {
    // The non-vacuity floor. "No Index rows rendered" is trivially true of a transcript with no
    // user turns, and a hand-built `ChatActivity` with an empty list would satisfy every assertion
    // in this describe without the tab having been deleted at all.
    expect(TURNS.length).toBeGreaterThan(1)
    expect(TURNS.filter((t) => t.role === 'user')).toHaveLength(USER_TEXTS.length)
    // …and the map DOES index them, so the outline's content is still reachable — just elsewhere.
    expect(MARKS.filter((m) => m.kind === 'user')).toHaveLength(USER_TEXTS.length)
    // The surviving tabs are non-empty, so the render below is a populated panel.
    expect(ACTIVITY.files.length, 'the fixture surfaced no file, so the Files control below is vacuous').toBeGreaterThan(0)
    expect(ACTIVITY.links.length, 'the fixture surfaced no link').toBeGreaterThan(0)
  })

  it('🔑 no tab named "Index" exists, and Files/Links still do (the control)', () => {
    const panel = render(<ChatActivityPanel activity={ACTIVITY} onOpenFile={() => {}} />)
    // CONTROL FIRST: the same query finds the tabs that survive. Without this, a panel that failed
    // to render — or a renamed role — would report "no Index tab" and look like a pass.
    expect(panel.getByRole('tab', { name: 'Files' })).toBeTruthy()
    expect(panel.getByRole('tab', { name: 'Links' })).toBeTruthy()
    // THE CLAUSE.
    expect(
      panel.queryByRole('tab', { name: 'Index' }),
      'the Activity panel still renders an "Index" tab — SSM-13 is the atom that deletes it',
    ).toBeNull()
    // The whole tablist, so a tab reachable under some other accessible name is caught too.
    const names = panel.getAllByRole('tab').map((t) => t.getAttribute('aria-label'))
    expect(names).toEqual(['Files', 'Links'])
    panel.unmount()
  })

  it('🔑 the panel OPENS on Files — no index panel is mounted, by default or at all', () => {
    const panel = render(<ChatActivityPanel activity={ACTIVITY} onOpenFile={() => {}} />)
    // The default tab was `index`; the reader's first sight of this panel was the outline. That is
    // the user-visible half of the removal, and it is separate from "no Index tab renders": a tab
    // strip could lose its Index button while the body still defaulted to the index panel.
    expect(panel.container.querySelector('#act-panel-files'), 'the Activity panel does not open on Files').toBeTruthy()
    expect(
      panel.container.querySelector('#act-panel-index'),
      'an index tabpanel is still mounted in the Activity panel',
    ).toBeNull()
    expect(panel.container.querySelector('#act-tab-index')).toBeNull()
    panel.unmount()
  })

  it('🔑 no jump anchor for any user turn is rendered anywhere in the panel', () => {
    // The tab strip is chrome; the LIST BODY is the thing that navigated. Asserted by content, so a
    // reinstated outline under any other tab name, id or role still fails here.
    const panel = render(<ChatActivityPanel activity={ACTIVITY} onOpenFile={() => {}} />)
    // CONTROL: rows ARE findable by their `title` — that is how the Files row is found.
    expect(panel.getByTitle(A_FILE), 'no Files row carries its path as a title, so the absences below prove nothing').toBeTruthy()
    for (const text of USER_TEXTS) {
      expect(panel.queryByTitle(text), `an Index-style jump anchor for the user turn "${text}" is still rendered`).toBeNull()
      expect(panel.queryByText(text), `the user turn "${text}" is still listed in the Activity panel`).toBeNull()
    }
    panel.unmount()
  })
})

describe('SSM-13 — the panel owns no navigation at all', () => {
  const src = readFileSync(join(SRC, 'pages/chat/ChatActivityPanel.tsx'), 'utf8')

  it('🔑 ChatActivityPanel takes no jump prop and implements no scroll', () => {
    // CONTROL: the right file was read.
    expect(src.length, 'ChatActivityPanel.tsx read empty').toBeGreaterThan(1000)
    expect(src, 'the file read is not the Activity panel').toMatch(/export function ChatActivityPanel/)
    // SSM-12 could only assert that the tab's jump was the MAP's jump (the prop, reused). With the
    // tab deleted the stronger claim holds: the panel has no jump surface, so there is no second
    // navigation left to drift — which is what "leaving no dual path" means for this atom.
    expect(src, 'ChatActivityPanel still accepts an onJumpTo prop — the deleted tab\'s seam is still open').not.toMatch(/onJumpTo/)
    expect(src, 'ChatActivityPanel grew its own scroll implementation').not.toMatch(/scrollIntoView/)
  })

  it('🔑 the tab union and the tab descriptors carry no index member', () => {
    const union = src.match(/type Tab = ([^\n]+)/)
    expect(union, 'no Tab union found in ChatActivityPanel.tsx — widen this reader').not.toBeNull()
    expect(union![1], `the Tab union still admits an index tab: ${union![1]}`).not.toMatch(/'index'/)
    // CONTROL: the union is the real one, still naming the surviving tabs.
    expect(union![1]).toMatch(/'files'/)
    expect(src).not.toMatch(/label: 'Index'/)
    expect(src, 'the control pattern found no tab descriptor at all').toMatch(/label: 'Files'/)
  })

  it('🔑 ChatPage no longer hands the panel a jump handler', () => {
    const page = readFileSync(join(SRC, 'pages/ChatPage.tsx'), 'utf8')
    expect(page.length, 'ChatPage.tsx read empty').toBeGreaterThan(1000)
    const open = page.indexOf('<ChatActivityPanel ')
    expect(open, 'no <ChatActivityPanel> call site in ChatPage.tsx — this rail would be vacuous').toBeGreaterThan(-1)
    const close = page.indexOf('/>', open)
    expect(close, '<ChatActivityPanel> is not self-closing in ChatPage.tsx — widen this reader').toBeGreaterThan(open)
    const tag = page.slice(open, close)
    // CONTROL: the tag really is the call site, still passing the props that survive.
    expect(tag).toMatch(/activity=\{/)
    expect(tag).toMatch(/onOpenFile=\{/)
    expect(tag, `<ChatActivityPanel> is still handed a jump handler: ${tag.match(/onJumpTo=\{[^}]*\}/)?.[0]}`).not.toMatch(/onJumpTo/)
  })
})

// ── The grep-style half: `deriveActivity().index` has no consumer left ──────────────────────────
//
// 🪤 THE SCAN WALKS THE TREE RATHER THAN READING A FILE LIST, because an enumerated rail cannot see
// its own blind spot: a reinstated index in a file nobody thought to list would pass. It also runs
// the SAME walker over the SURVIVING siblings (`activity.files`, `activity.links`), so a walker that
// read nothing — wrong cwd, wrong extension filter, a `readdirSync` that threw and was swallowed —
// fails loudly instead of reporting a clean tree.

/** Every `.ts`/`.tsx` file under `dir`, recursively, as paths relative to `web/`. */
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) return sources(p)
    return /\.tsx?$/.test(name) ? [relative(WEB, p)] : []
  })
}

/** Relative paths whose text matches `re`. */
function hits(files: string[], re: RegExp): string[] {
  return files.filter((rel) => re.test(readFileSync(join(WEB, rel), 'utf8')))
}

describe('SSM-13 — nothing in the tree consumes an activity index', () => {
  const all = [...sources(SRC), ...sources(E2E)]
  const scanned = all.filter((rel) => rel !== SELF)
  /** The files that can SHIP a surface, i.e. everything that is not a test.
   *
   *  🪤 THE TAB'S DOM IDS ARE SCANNED HERE AND NOT OVER THE WHOLE TREE, because a text scan cannot
   *  tell a renderer from an assertion ABOUT a renderer — and the first run of this rail proved it,
   *  failing on `e2e/sessionMap.spec.ts`'s own `expect('#act-panel-index').toHaveCount(0)`, i.e. on
   *  the test that exists to prove the id is gone. Scoping to production is not a softening: an
   *  `act-panel-index` can only reach a browser from a file that renders it, so a spec naming the id
   *  cannot reinstate the tab, and a spec DRIVING a reinstated tab would need the production hit
   *  this set catches. The model patterns below stay tree-wide, where no such ambiguity exists. */
  const shipped = scanned.filter((rel) => !/\.(?:test|spec)\.tsx?$/.test(rel) && !rel.startsWith(`e2e${sep}`))

  it('🔴 the walker actually read the tree, and excluded exactly this file', () => {
    // Without this, every absence below is satisfied by an empty file list.
    expect(all.length, 'the source walk found almost nothing — check the cwd (run vitest from web/, not --root web)').toBeGreaterThan(200)
    expect(all, 'this test file is not where the exclusion says it is').toContain(SELF)
    expect(all.length - scanned.length, 'the self-exclusion removed more than this one file').toBe(1)
    // The two files that would carry a reinstated index are in the set, named explicitly.
    expect(scanned).toContain(join('src', 'pages', 'ChatPage.tsx'))
    expect(scanned).toContain(join('src', 'pages', 'chat', 'ChatActivityPanel.tsx'))
    expect(scanned).toContain(join('src', 'pages', 'chat', 'chatTypes.ts'))
    expect(scanned.some((rel) => rel.startsWith(`e2e${sep}`)), 'the e2e directory was not scanned').toBe(true)
    // The production subset is real and holds the file a reinstated tab would live in.
    expect(shipped.length, 'the production subset came out empty').toBeGreaterThan(100)
    expect(shipped).toContain(join('src', 'pages', 'chat', 'ChatActivityPanel.tsx'))
    expect(shipped.filter((rel) => /\.(?:test|spec)\.tsx?$/.test(rel)), 'a test file leaked into the production subset').toHaveLength(0)
  })

  it('🔴 the SAME scanner finds the surviving siblings (the control)', () => {
    // `activity.files` / `activity.links` are read by exactly the same kind of expression the
    // deleted `activity.index` was. If the scanner cannot find them it cannot find an index either,
    // and every assertion in the next test is a false negative.
    expect(hits(scanned, /activity\.files/), 'the scanner found no activity.files consumer — it is not reading real source').not.toHaveLength(0)
    expect(hits(scanned, /activity\.links/)).not.toHaveLength(0)
    expect(hits(scanned, /deriveActivity\(/), 'the scanner found no deriveActivity call at all').not.toHaveLength(0)
    // …and the id-family pattern DOES hit the panel that would carry a reinstated index id, so the
    // production-scoped absence below is a real absence and not an unreachable file set.
    expect(hits(shipped, /act-(?:tab|panel)-/), 'the tab-id pattern matches nothing in production source')
      .toContain(join('src', 'pages', 'chat', 'ChatActivityPanel.tsx'))
  })

  it('🔑 zero consumers of an activity index remain', () => {
    // Deliberately NOT a bare `\.index\b`: `RegExpMatchArray.index` is all over the parsers here and
    // would make this rail unpassable for the wrong reason. Each pattern names the activity model.
    const model: [string, RegExp][] = [
      ['activity.index — the field read', /activity\.index/],
      ['IndexEntry — the row type', /\bIndexEntry\b/],
      ['an index member on ChatActivity', /ChatActivity\s*\{[^}]*\bindex\b/],
      ['deriveActivity(...).index', /deriveActivity\([^)]*\)\s*\.index/],
      ['destructured index off deriveActivity', /\{\s*index[,\s}][^=]*=\s*deriveActivity\(/],
    ]
    for (const [what, re] of model) {
      expect(hits(scanned, re), `${what} is still consumed in: ${hits(scanned, re).join(', ')}`).toHaveLength(0)
    }
    // The rendered surface, production only (see `shipped`).
    const surface: [string, RegExp][] = [
      ['the index tab/panel DOM ids', /act-(?:tab|panel)-index/],
      ['an Index tab descriptor', /label: 'Index'/],
      ["an 'index' member of the Tab union", /type Tab = [^\n]*'index'/],
    ]
    for (const [what, re] of surface) {
      expect(hits(shipped, re), `${what} is still shipped from: ${hits(shipped, re).join(', ')}`).toHaveLength(0)
    }
  })

  it('🔑 SSM-1\'s model is the session\'s only index, and it is still consumed', () => {
    // The clause is "no consumer OTHER THAN SSM-1's model" — a one-sided absence would also be
    // satisfied by deleting the map. So the survivor is asserted positively: `sessionMapMarks` has
    // production importers, and the rail is mounted in the page.
    const marksUsers = hits(scanned, /sessionMapMarks\(/).filter((rel) => !/\.test\.tsx?$/.test(rel))
    expect(marksUsers, 'SSM-1\'s model has no non-test consumer — the session would have NO index').not.toHaveLength(0)
    expect(marksUsers).toContain(join('src', 'pages', 'ChatPage.tsx'))
    const railUsers = hits(scanned, /<SessionMapRail\b/)
    expect(railUsers, 'the Session Map rail is mounted nowhere — deleting the Index tab would leave no in-session index').toContain(join('src', 'pages', 'ChatPage.tsx'))
  })
})

describe('SSM-13 — the survivor still navigates', () => {
  it('🔑 a rail tick reaches the jump handler with the mark\'s coordinate', async () => {
    // The behavioural half of "the deletion removed nothing the reader needed". Kept from SSM-12's
    // proof, which asserted this of BOTH surfaces; only one is left to assert it of.
    const jumpToTurn = vi.fn()
    const rail = render(
      <SessionMapRail marks={MARKS} turnNodes={new Map<number, Element>()} scrollRef={{ current: null }} onJumpTo={jumpToTurn} />,
    )
    const ticks = rail.container.querySelectorAll('[data-session-mark]')
    expect(ticks, 'the rail rendered no marks').toHaveLength(MARKS.length)
    const nth = MARKS.findIndex((m) => m.kind === 'user')
    expect(nth, 'the rail carries no user mark, so it is not indexing the turns the Index tab listed').toBeGreaterThan(-1)
    await userEvent.click(ticks[nth])
    expect(jumpToTurn, 'a rail tick reached no jump handler').toHaveBeenCalledTimes(1)
    expect(jumpToTurn.mock.calls[0][0]).toBe(MARKS[nth].visibleIndex)
    rail.unmount()
  })

  it('🔑 ChatPage still hands the rail its one jump handler, un-wrapped', () => {
    const page = readFileSync(join(SRC, 'pages/ChatPage.tsx'), 'utf8')
    const open = page.indexOf('<SessionMapRail ')
    expect(open, 'no <SessionMapRail> call site in ChatPage.tsx — the map is not mounted').toBeGreaterThan(-1)
    const tag = page.slice(open, page.indexOf('/>', open))
    const expr = tag.match(/onJumpTo=\{([^}]*)\}/)
    expect(expr, '<SessionMapRail> passes no onJumpTo prop').not.toBeNull()
    // A bare identifier: an inline arrow would be a SECOND function that could re-map the
    // coordinate on the way in, which is the dual path SSM-12 closed and this atom must not reopen.
    expect(expr![1].trim(), `<SessionMapRail> passes an expression, not the shared handler: ${expr![1]}`).toMatch(/^[A-Za-z_$][\w$]*$/)
    const name = expr![1].trim()
    const defs = page.match(new RegExp(`(?:function|const)\\s+${name}\\b`, 'g')) ?? []
    expect(defs, `expected one definition of \`${name}\` in ChatPage.tsx, found ${defs.length}`).toHaveLength(1)
  })
})
