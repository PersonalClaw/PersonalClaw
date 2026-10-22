// @module-tag tree-scan
import { describe, it, expect, vi } from 'vitest'
import { join, relative, sep } from 'node:path'
import { fireEvent, render, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatActivityPanel } from './ChatActivityPanel'
import { SessionMapRail } from './SessionMapRail'
import { hydrateTurns, deriveActivity, type HistMsg } from './chatTypes'
import { sessionMapEntries } from './sessionMap'
import { filesUnder, readSource } from '../../test/sourceTree'

// ── THE SUPERSEDED ACTIVITY → INDEX TAB IS GONE, AND STAYS GONE ──────────────────────────────
//
// The change's contract: "the 'Index' tab and its list body are deleted from `ChatActivityPanel.tsx`;
// a test asserts no 'Index' tab is rendered and that `deriveActivity().index` has no remaining
// consumer other than the model (grep-style assertion), leaving no dual path."
//
// 🔑 WHAT A DELETION CHANGE'S PROOF HAS TO BE. There is no new behaviour to observe, so the only
// honest evidence is an ABSENCE — and an absence is exactly the claim that passes for the wrong
// reason. Every block below therefore carries a CONTROL: a positive assertion, made with the same
// query or the same scanner, that would fail if the query/scan were broken, mis-pathed or pointed
// at an empty fixture. A block without a control could not tell "the Index tab is gone" from "the
// panel did not render" or "the walker read no files".
//
// 🔴 AND THE DELETION IS ONLY SAFE BECAUSE A SURVIVOR EXISTS. The Index tab was the session's
// in-session index. Removing it would be a REGRESSION rather than a clean break if the Session Map
// were not there to be it — which is the whole reason this change came after the shared jump (one jump
// handler, one coordinate, both surfaces). So the last two blocks assert the survivor positively:
// the rail still reaches `ChatPage`'s jump handler with a mark's coordinate, and `ChatPage` still
// hands it that handler. Deleting the tab without those two passing would leave the reader with no
// way to navigate a long session at all.
//
// WHAT THIS FILE DOES NOT OWN. The jump's SCROLL — jsdom computes no layout and `ChatPage` is not
// mountable there. `e2e/sessionMap.spec.ts`'s no-Index-tab block drives the real browser: it asserts the
// panel has no Index tab and that the rail lands a mid-session turn off both clamp ends.

const WEB = process.cwd()
const SRC = join(WEB, 'src')
const E2E = join(WEB, 'e2e')
/** This file's own path, excluded from the tree scan below — it necessarily spells out every
 *  pattern it searches for. The exclusion is asserted to have removed exactly this one file. */
const SELF = join('src', 'pages', 'chat', 'indexTabRetired.test.tsx')

/** A real transcript that WOULD have produced Index rows: two user turns (the outline's only
 *  source), plus assistant prose carrying a path and citing a page its turn read, so the surviving
 *  tabs are non-empty.
 *  Built through `hydrateTurns`, not by hand, so the turn shapes are the ones the page renders. */
const MESSAGES: HistMsg[] = [
  { role: 'user', content: 'run the build' },
  { role: 'assistant', content: 'starting' },
  { role: 'tool', content: 'web_fetch', meta: { tool_call_id: 'tc-1', input: '{"url":"https://example.com/build"}', output: '# Build', done: true } },
  { role: 'assistant', content: 'done — touched src/pages/chat/sessionMap.ts, see https://example.com/build' },
  { role: 'user', content: 'now the tests' },
  { role: 'assistant', content: 'all green' },
]

const TURNS = hydrateTurns(MESSAGES)
const ACTIVITY = deriveActivity(TURNS)
const ENTRIES = sessionMapEntries(TURNS)

const USER_TEXTS = ['run the build', 'now the tests']
const A_FILE = 'src/pages/chat/sessionMap.ts'

describe('The Activity panel renders no Index tab', () => {
  it('🔴 the fixture HAS the user turns an Index tab would have listed', () => {
    // The non-vacuity floor. "No Index rows rendered" is trivially true of a transcript with no
    // user turns, and a hand-built `ChatActivity` with an empty list would satisfy every assertion
    // in this describe without the tab having been deleted at all.
    expect(TURNS.length).toBeGreaterThan(1)
    expect(TURNS.filter((t) => t.role === 'user')).toHaveLength(USER_TEXTS.length)
    // …and the map DOES index them, so the outline's content is still reachable — just elsewhere.
    expect(ENTRIES.map((e) => e.preview)).toEqual(USER_TEXTS)
    // The surviving tabs are non-empty, so the render below is a populated panel.
    expect(ACTIVITY.files.length, 'the fixture surfaced no file, so the Files control below is vacuous').toBeGreaterThan(0)
    expect(ACTIVITY.sources.length, 'the fixture surfaced no source').toBeGreaterThan(0)
  })

  it('🔑 no section named "Index" exists, and Files/Sources still do (the control)', () => {
    const panel = render(<ChatActivityPanel activity={ACTIVITY} onOpenFile={() => {}} />)
    // CONTROL FIRST: the same query finds the sections that survive. Without this, a panel that
    // failed to render — or a renamed role — would report "no Index section" and look like a pass.
    // (The panel was a tab strip; it is the session inspector's column of sections now.)
    expect(panel.getByRole('region', { name: 'Files' })).toBeTruthy()
    expect(panel.getByRole('region', { name: 'Sources' })).toBeTruthy()
    // THE CONTRACT.
    expect(
      panel.queryByRole('region', { name: 'Index' }),
      'the Activity panel still renders an "Index" section — this change deletes it',
    ).toBeNull()
    // The whole column, so a section reachable under some other accessible name is caught too.
    const names = panel.getAllByRole('region').map((t) => document.getElementById(t.getAttribute('aria-labelledby') ?? '')?.textContent)
    expect(names).toEqual(['Environment', 'Context', 'Connectors', 'Files', 'Sources', 'Background tasks'])
    panel.unmount()
  })

  it('🔑 the panel opens with no index list mounted, by default or at all', () => {
    const panel = render(<ChatActivityPanel activity={ACTIVITY} onOpenFile={() => {}} />)
    // The default tab was `index`; the reader's first sight of this panel was the outline. That is
    // the user-visible half of the removal, and it is separate from "no Index section renders": a
    // column could lose its Index header while the body still showed the outline.
    expect(panel.getByRole('region', { name: 'Sources' }).querySelector('a'), 'the open Sources section shows its links').toBeTruthy()
    expect(panel.container.querySelector('#act-panel-index'), 'an index panel is still mounted in the Activity panel').toBeNull()
    expect(panel.container.querySelector('#act-tab-index')).toBeNull()
    panel.unmount()
  })

  it('🔑 no jump anchor for any user turn is rendered anywhere in the panel', () => {
    // The tab strip is chrome; the LIST BODY is the thing that navigated. Asserted by content, so a
    // reinstated outline under any other tab name, id or role still fails here.
    const panel = render(<ChatActivityPanel activity={ACTIVITY} onOpenFile={() => {}} />)
    // Every section open, so nothing the panel holds is folded out of the search.
    for (const section of panel.getAllByRole('region')) {
      const header = within(section).getAllByRole('button')[0]
      if (header.getAttribute('aria-expanded') === 'false') fireEvent.click(header)
    }
    // CONTROL: rows ARE findable by their `title` — that is how the Files row is found.
    expect(panel.getByTitle(A_FILE), 'no Files row carries its path as a title, so the absences below prove nothing').toBeTruthy()
    for (const text of USER_TEXTS) {
      expect(panel.queryByTitle(text), `an Index-style jump anchor for the user turn "${text}" is still rendered`).toBeNull()
      expect(panel.queryByText(text), `the user turn "${text}" is still listed in the Activity panel`).toBeNull()
    }
    panel.unmount()
  })
})

describe('The panel owns no navigation at all', () => {
  const src = readSource(join(SRC, 'pages/chat/ChatActivityPanel.tsx'))

  it('🔑 ChatActivityPanel takes no jump prop and implements no scroll', () => {
    // CONTROL: the right file was read.
    expect(src.length, 'ChatActivityPanel.tsx read empty').toBeGreaterThan(1000)
    expect(src, 'the file read is not the Activity panel').toMatch(/export function ChatActivityPanel/)
    // Before the deletion a test could only assert that the tab's jump was the MAP's jump (the
    // prop, reused). With the tab deleted the stronger claim holds: the panel has no jump surface,
    // so there is no second navigation left to drift — which is what "leaving no dual path" means
    // for this change.
    expect(src, 'ChatActivityPanel still accepts an onJumpTo prop — the deleted tab\'s seam is still open').not.toMatch(/onJumpTo/)
    expect(src, 'ChatActivityPanel grew its own scroll implementation').not.toMatch(/scrollIntoView/)
  })

  it('🔑 the section list and its titles carry no index member', () => {
    const layout = readSource(join(SRC, 'pages/chat/inspector/inspectorLayout.ts'))
    const list = layout.match(/export const SECTIONS = \[([^\]]+)\]/)
    expect(list, 'no SECTIONS list found in inspector/inspectorLayout.ts — widen this reader').not.toBeNull()
    expect(list![1], `the section list still admits an index section: ${list![1]}`).not.toMatch(/'index'/)
    // CONTROL: the list is the real one, still naming the surviving sections.
    expect(list![1]).toMatch(/'files'/)
    expect(layout).not.toMatch(/index: 'Index'/)
    expect(layout, 'the control pattern found no section title at all').toMatch(/files: 'Files'/)
  })

  it('🔑 ChatPage no longer hands the panel a jump handler', () => {
    // The panel is rendered by the chat's docked panes (`chat/panes/ChatPanes.tsx`), which moved out
    // of ChatPage.
    const page = readSource(join(SRC, 'pages/chat/panes/ChatPanes.tsx'))
    expect(page.length, 'ChatPanes.tsx read empty').toBeGreaterThan(1000)
    const open = page.indexOf('<ChatActivityPanel ')
    expect(open, 'no <ChatActivityPanel> call site in ChatPanes.tsx — this rail would be vacuous').toBeGreaterThan(-1)
    const close = page.indexOf('/>', open)
    expect(close, '<ChatActivityPanel> is not self-closing in ChatPanes.tsx — widen this reader').toBeGreaterThan(open)
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
// the SAME walker over the SURVIVING siblings (`activity.files`, `activity.sources`), so a walker that
// read nothing — wrong cwd, wrong extension filter, a `readdirSync` that threw and was swallowed —
// fails loudly instead of reporting a clean tree.

/** Every `.ts`/`.tsx` file under `dir`, recursively, as paths relative to `web/`. */
function sources(dir: string): string[] {
  return filesUnder(dir, (name) => /\.tsx?$/.test(name)).map((p) => relative(WEB, p))
}

/** Relative paths whose text matches `re`. */
function hits(files: string[], re: RegExp): string[] {
  return files.filter((rel) => re.test(readSource(join(WEB, rel))))
}

describe('Nothing in the tree consumes an activity index', () => {
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
    // `activity.files` / `activity.sources` are read by exactly the same kind of expression the
    // deleted `activity.index` was. If the scanner cannot find them it cannot find an index either,
    // and every assertion in the next test is a false negative.
    expect(hits(scanned, /activity\.files/), 'the scanner found no activity.files consumer — it is not reading real source').not.toHaveLength(0)
    expect(hits(scanned, /activity\.sources/)).not.toHaveLength(0)
    expect(hits(scanned, /deriveActivity\(/), 'the scanner found no deriveActivity call at all').not.toHaveLength(0)
    // …and the section-title pattern DOES hit the file that would carry a reinstated index section, so
    // the production-scoped absence below is a real absence and not an unreachable file set.
    expect(hits(shipped, /SECTION_TITLES/), 'the section pattern matches nothing in production source')
      .toContain(join('src', 'pages', 'chat', 'inspector', 'inspectorLayout.ts'))
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
      ['an Index section title', /index: 'Index'/],
    ]
    for (const [what, re] of surface) {
      expect(hits(shipped, re), `${what} is still shipped from: ${hits(shipped, re).join(', ')}`).toHaveLength(0)
    }
  })

  it('🔑 the map\'s model is the session\'s only index, and it is still consumed', () => {
    // The contract is "no consumer OTHER THAN the model" — a one-sided absence would also be
    // satisfied by deleting the map. So the survivor is asserted positively: the map's entries —
    // The model grouped one per user message — have a production consumer, and the rail is
    // mounted in the page.
    // The chat's transcript region holds both since it left ChatPage.tsx: the entries in its
    // controller, the rail in its view.
    const entryUsers = hits(scanned, /sessionMapEntries\(/).filter((rel) => !/\.test\.tsx?$/.test(rel))
    expect(entryUsers, 'the map\'s model has no non-test consumer — the session would have NO index').not.toHaveLength(0)
    expect(entryUsers).toContain(join('src', 'pages', 'chat', 'transcript', 'useTranscript.ts'))
    const railUsers = hits(scanned, /<SessionMapRail\b/)
    expect(railUsers, 'the Session Map rail is mounted nowhere — deleting the Index tab would leave no in-session index').toContain(join('src', 'pages', 'chat', 'transcript', 'Transcript.tsx'))
  })
})

describe('The survivor still navigates', () => {
  it('🔑 a rail tick reaches the jump handler with the mark\'s coordinate', async () => {
    // The behavioural half of "the deletion removed nothing the reader needed". Kept from the earlier
    // proof, which asserted this of BOTH surfaces; only one is left to assert it of.
    const jumpToTurn = vi.fn()
    const rail = render(
      <SessionMapRail entries={ENTRIES} turnNodes={new Map<number, Element>()} scrollRef={{ current: null }} onJumpTo={jumpToTurn} />,
    )
    const ticks = rail.container.querySelectorAll('[data-session-mark]')
    expect(ticks, 'the rail rendered no marks').toHaveLength(ENTRIES.length)
    const nth = ENTRIES.findIndex((e) => e.preview === USER_TEXTS[1])
    expect(nth, 'the rail carries no user mark, so it is not indexing the turns the Index tab listed').toBeGreaterThan(-1)
    await userEvent.click(ticks[nth])
    expect(jumpToTurn, 'a rail tick reached no jump handler').toHaveBeenCalledTimes(1)
    expect(jumpToTurn.mock.calls[0][0]).toBe(ENTRIES[nth].visibleIndex)
    rail.unmount()
  })

  it('🔑 the transcript still hands the rail its one jump handler, un-wrapped', () => {
    // The rail is mounted by the chat's transcript view, and the handler is its controller's.
    const page = readSource(join(SRC, 'pages/chat/transcript/Transcript.tsx'))
    const controller = readSource(join(SRC, 'pages/chat/transcript/useTranscript.ts'))
    const open = page.indexOf('<SessionMapRail ')
    expect(open, 'no <SessionMapRail> call site in Transcript.tsx — the map is not mounted').toBeGreaterThan(-1)
    const tag = page.slice(open, page.indexOf('/>', open))
    const expr = tag.match(/onJumpTo=\{([^}]*)\}/)
    expect(expr, '<SessionMapRail> passes no onJumpTo prop').not.toBeNull()
    // A bare identifier: an inline arrow would be a SECOND function that could re-map the
    // coordinate on the way in, which is the dual path the shared jump closed and this change must not reopen.
    expect(expr![1].trim(), `<SessionMapRail> passes an expression, not the shared handler: ${expr![1]}`).toMatch(/^[A-Za-z_$][\w$]*$/)
    const name = expr![1].trim()
    const defs = controller.match(new RegExp(`(?:function|const)\\s+${name}\\b`, 'g')) ?? []
    expect(defs, `expected one definition of \`${name}\` in useTranscript.ts, found ${defs.length}`).toHaveLength(1)
    // …and the view takes that one from its controller rather than defining a second.
    expect(page.match(new RegExp(`(?:function|const)\\s+${name}\\b`, 'g')), `Transcript.tsx defines its own \`${name}\``).toBeNull()
    expect(page).toMatch(new RegExp(`const \\{[^}]*\\b${name}\\b[^}]*\\} = transcript`))
  })
})
