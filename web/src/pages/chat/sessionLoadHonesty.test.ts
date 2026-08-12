import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Telling an account with 31 sessions that it has none ──────────────────────────────────
//
// Driven, not inferred. `/api/chat/sessions` forced to 500 with a cold sessionStorage, against a
// dev home holding **31** sessions:
//
//                        BEFORE                                          AFTER
//   #/chat/history       "No chats yet" + hint "Start a conversation…"    alert: "Couldn't load
//                        + a **New chat** action · no live region          your chats" + the
//                                                                         server's message + Retry
//   chat-history panel   "No chats yet."          · no live region        the same alert
//   #/dashboard chips    row silently absent, and the fabricated `[]`     nothing cached; row
//                        WRITTEN to sessionStorage (persist:true)          still hidden (see below)
//
// All three read the same resource through `api.chatSessions()`, and all three discarded the
// rejection with `.catch(() => [])`, so `data` became an empty array and every reader took the
// "you have none" branch. This is the worst instance of the family the `LoadError` sweep has
// found so far, because the history page's empty state carries an **action** — the false claim
// arrives with an invitation to act on it.
//
// 🔑 THE RAIL IS SCOPED TO THE RESOURCE, NOT THE FILE. `loadErrorState.test.tsx` bars an adopter
// file from swallowing in ANY fetcher, which is right for a list page whose whole job is one
// resource. `ChatPage.tsx` also reads six unrelated decorations through the same hook
// (`chat:suggestions`, `chat:starters`, `chat:stream-reveal`, `chat:artifact-picker`,
// `chat:folders`, `chat:tags`); a chip row that quietly fails to appear makes no false claim, and
// forcing six unrelated UI decisions into this change would be scope, not rigour. So this rail
// pins the SESSION readers — the ones that speak for whether your chats exist.
//
// 🪤 ONE EXEMPTION, NAMED: `MemoryPanel`'s `consolidate()` handler calls `api.chatSessions()`
// imperatively inside a try block and turns an empty result into its own message ("No active
// sessions to consolidate."). It is not a render path and it does not claim anything about your
// chat list.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

const IMPERATIVE_EXEMPT = new Set(['pages/settings/MemoryPanel.tsx'])

/** Every `api.chatSessions(` call with the text that follows it, so a `.catch` on the same
 *  expression is visible. Bounded rather than to end-of-line: these calls wrap. */
const callSites = walk(SRC).flatMap((abs) => {
  const src = readFileSync(abs, 'utf8')
  const rel = abs.slice(SRC.length + 1)
  return [...src.matchAll(/api\.chatSessions\([^)]*\)[\s\S]{0,60}/g)].map((m) => ({
    file: rel,
    line: src.slice(0, m.index!).split('\n').length,
    frag: m[0].replace(/\s+/g, ' '),
  }))
})

describe('every reader of the chat-session list', () => {
  it('is found by the census (not vacuously green)', () => {
    // Four at the time of writing: two in ChatPage, one on the dashboard, one imperative.
    expect(callSites.length, 'the matcher must find the chatSessions() readers').toBeGreaterThanOrEqual(4)
    expect(callSites.map((c) => c.file)).toContain('pages/ChatPage.tsx')
    expect(callSites.map((c) => c.file)).toContain('pages/dashboard/DashboardPage.tsx')
  })

  it('never discards the rejection', () => {
    const swallowed = callSites
      .filter((c) => /\.catch\(/.test(c.frag))
      .filter((c) => !IMPERATIVE_EXEMPT.has(c.file))
    expect(
      swallowed.map((c) => `${c.file}:${c.line}`),
      'a swallowed rejection makes a 500 indistinguishable from "you have no chats"',
    ).toEqual([])
  })
})

describe('the two chat-history surfaces', () => {
  const src = readFileSync(join(SRC, 'pages/ChatPage.tsx'), 'utf8')

  it('capture the error from the hook', () => {
    // `useCachedData` hands back `{ data, loading, error, refresh }`; the branch is impossible
    // unless the rejection is destructured. Both session readers alias it the same way.
    expect((src.match(/error: sessionsError/g) ?? []).length, 'both readers must capture the error').toBe(2)
  })

  it('render the shared LoadError, with a retry, for both', () => {
    const uses = src.match(/<LoadError what="chats" error=\{sessionsError\} onRetry=\{refreshSessions\} \/>/g) ?? []
    expect(uses.length, 'the page and the side panel each need the branch').toBe(2)
  })

  it('tests the error BEFORE the loading and empty branches', () => {
    // `data === undefined` is true for loading, error AND empty, so an error branch placed after
    // the loading test is unreachable. Assert the guard's shape at both sites rather than trusting
    // the order of the JSX by eye.
    expect(src).toMatch(/data === undefined && sessionsError \?/)          // the side panel
    expect(src).toMatch(/sessions === null && sessionsError \?/)           // the history page
  })

  it('leaves the empty state as a non-alert, so "you have none" does not interrupt', () => {
    // The contrast is the whole point of the fix: the page still ships EmptyState with its New-chat
    // action for a genuinely empty account.
    expect(src).toMatch(/<EmptyState icon=\{MessageSquare\} title="No chats yet"/)
  })
})
