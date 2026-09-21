// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { AppCatalogEntry, AppCatalog } from '../../lib/api'

// ── A Store card renders its author's prose, and admits what it cut (issue 2515) ──────────────────
//
// Two defects, one card, both measured on a real catalog entry:
//
// 🔴 THE DESCRIPTION WAS THE AUTHOR'S MARKDOWN PRINTED AS PUNCTUATION. `app.json`'s `description` is
// markdown by contract — the Store, the detail panel and the config hints all show the same string,
// and every published app writes `**bold**` and `` `code` `` in it. The card interpolated it as a
// text node, so a reader saw the asterisks and backticks. This app has ONE renderer (`ui/Markdown`);
// the assertion is that the card reaches it, not that some string transformation happened.
//
// 🔴 A FOURTH TAG JUST VANISHED. `.slice(0, 3)` with nothing beside it: a three-tag app and a
// nine-tag app rendered identically, which is the undisclosed-cap defect `ui/MoreRow` exists to
// close. Same sentence as the other capped lists, not a card-local `+N` pill.
//
// 🔑 A DRIVEN RENDER, and the markdown leg asserts on the ELEMENT (`<strong>`, `<code>`) rather than
// on absent asterisks — "the punctuation is gone" is also true of a bad `String.replace`. The clamp
// leg is here for the same reason: `line-clamp` is `-webkit-box`-based, so a BLOCK child escapes the
// clamp entirely and the card grows without bound. jsdom cannot measure that (every box is 0), but
// it can prove the structural precondition — the flow stays inline inside the clamped element.

vi.mock('../../lib/api', () => ({
  api: {
    installApp: () => Promise.resolve({ ok: true }),
    removeAppSource: () => Promise.resolve({}),
    addAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    addLocalAppSource: () => Promise.resolve({ ok: true, sources: [] }),
    removeLocalAppSource: () => Promise.resolve({}),
  },
}))
vi.mock('../../lib/useGuardedInstall', () => ({
  useGuardedInstall: () => ({
    install: () => Promise.resolve({ ok: true }),
    confirmInstall: () => Promise.resolve({ ok: true }),
    reset: () => {}, blocked: null, busy: false, error: null, fixPrompt: null,
  }),
  guardedFromApp: (r: unknown) => r,
  isBlockingResult: () => false,
  terminalRefusalReason: () => '',
}))
vi.mock('../../app/appSdk', () => ({ notify: vi.fn(), launchChat: vi.fn() }))

import { StoreView } from './AppsSection'

function entry(over: Partial<AppCatalogEntry> = {}): AppCatalogEntry {
  return {
    name: 'deep-research', displayName: 'Deep Research', description: 'Ask one question and walk away…',
    version: '0.1.0', icon: '', author: '',
    source: '/srv/apps/deep-research', sourceKind: 'local',
    isProvider: false, providerType: '', tags: [],
    permissions: { network: false }, crons: [],
    ...over,
  }
}

const EMPTY: AppCatalog = {
  bundled: [], gitSources: [], defaultGitSources: [], builtinGitSources: [],
  localSources: ['/srv/apps'], firstPartySources: [], localApps: [], remoteApps: [], gitApps: [],
}

function grid(e: AppCatalogEntry) {
  return render(
    <StoreView catalog={{ ...EMPTY, localApps: [e] }}
      result={[{ ...e, installed: false, enabled: false, hasUI: false }]}
      totalKnown={1} installedCount={0} onInstalled={() => {}} reloadCatalog={() => {}}
      onClearFilters={() => {}} filtersActive={false} onOpen={() => {}}
      onAction={(() => {}) as never} onOpenSources={() => {}} />,
  )
}

describe('a Store card renders its description as markdown', () => {
  it('emphasis and code spans reach the DOM as elements, not as literal punctuation', () => {
    const { container } = grid(entry({ description: 'Sync your **vault** via `rsync`' }))
    const strong = container.querySelector('strong')
    const code = container.querySelector('code')
    expect(strong?.textContent, 'the author wrote **vault** — it must be emphasis').toBe('vault')
    expect(code?.textContent, 'the author wrote `rsync` — it must be a code span').toBe('rsync')
    expect(container.textContent, 'no raw markdown punctuation left on the card').not.toContain('**')
    expect(container.textContent).not.toContain('`')
  })

  it('the clamped element still CARRIES the text flow — no block child escapes line-clamp', () => {
    // The whole reason the card uses `inline` mode. A `<p>` from the block renderer inside this
    // element would be a new flow root: `-webkit-line-clamp` would stop applying and a long
    // description would push the card taller than its grid row, on every card at once.
    const { container } = grid(entry({ description: 'Sync your **vault**\n\nand a second paragraph' }))
    const clamped = container.querySelector('.line-clamp-2')
    expect(clamped, 'the description sink is the clamped element').toBeTruthy()
    expect(clamped!.querySelector('p, div, ul, ol, pre, blockquote, h1, h2, h3, table'),
      'a block child would escape the clamp').toBeNull()
    // …and the second paragraph's words are still THERE (flattened, not dropped).
    expect(clamped!.textContent).toContain('second paragraph')
  })

  it('a description that is plain prose renders unchanged', () => {
    // The common case must not acquire stray markup or lose characters.
    const { container } = grid(entry({ description: 'Ask one question and walk away…' }))
    const clamped = container.querySelector('.line-clamp-2')
    expect(clamped!.textContent).toContain('Ask one question and walk away…')
  })
})

describe('a Store card discloses the tags it did not show', () => {
  it('five tags render three chips and say how many are hidden', () => {
    grid(entry({ tags: ['research', 'agent', 'web', 'search', 'reports'] }))
    expect(screen.getByText('research')).toBeTruthy()
    expect(screen.getByText('agent')).toBeTruthy()
    expect(screen.getByText('web')).toBeTruthy()
    // the 4th and 5th are NOT rendered as chips…
    expect(screen.queryByText('search')).toBeNull()
    expect(screen.queryByText('reports')).toBeNull()
    // …and the card says so, in the house sentence.
    expect(screen.getByText(/…\s*2 more tags/)).toBeTruthy()
  })

  it('three tags or fewer say nothing — a complete list needs no caveat', () => {
    grid(entry({ tags: ['research', 'agent', 'web'] }))
    expect(screen.queryByText(/more tags/)).toBeNull()
  })
})
