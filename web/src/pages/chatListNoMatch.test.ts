import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The chat list's narrowed-to-nothing state names its narrower (AUD-NZ10) ───────────
//
// Source pins, the tasks list's idiom (noMatchBlame.test.ts): ChatPage's shell is too heavy
// to render for four strings. The state is reachable through three controls (search, tag
// filter, origin scope) — and through NO control, when every loaded chat is a worker session
// hidden by the default 'manual' scope. It used to render one sentence for all of them:
// "No matches / Try a different search or tag filter." — blaming the search at a user who
// only switched scope, with no way out and no evidence they still have chats.

const src = readFileSync(join(process.cwd(), 'src', 'pages', 'ChatPage.tsx'), 'utf8')

describe('chat list no-match split', () => {
  it('the blame-everything sentence is gone', () => {
    expect(src).not.toContain('Try a different search or tag filter.')
  })

  it('a search miss names the query and offers Clear search', () => {
    expect(src).toMatch(/No chats match “\$\{q\.trim\(\)\}”/)
    expect(src).toMatch(/label: 'Clear search', onClick: \(\) => setQ\(''\)/)
  })

  it('the view escape resets BOTH remaining narrowers — tags and scope', () => {
    // Resetting only one would be a lying affordance whenever the other did the narrowing;
    // origin 'all' genuinely shows everything, which is why the label can say "all".
    expect(src).toMatch(
      /label: 'View all chats', onClick: \(\) => \{ setTagFilter\(new Set\(\)\); setOrigin\('all'\) \}/,
    )
  })

  it('both variants count the loaded chats, so the state cannot read as "you have none"', () => {
    const counts = src.match(/You have \$\{sessions\.length\} chat/g) ?? []
    expect(counts.length).toBeGreaterThanOrEqual(2)
  })
})
