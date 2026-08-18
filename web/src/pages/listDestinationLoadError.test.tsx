import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A list destination's failed fetch must not wear its empty state ─────────────────────────────
//
// One family, one canonical form. `LoadError` exists so that "the read failed" and "you have none"
// are different sentences; a `.catch(() => [])` inside the fetcher destroys the difference before
// the hook can see it, because a rejection becomes the SAME empty array a fresh install produces.
//
// Measured, not argued (dev gateway + a proxy 500-ing only the list endpoint, native skills on disk):
//
//   #/inbox   healthy-and-empty vs 500  →  PIXEL-IDENTICAL, 0.0000% different. Both said
//             "Inbox zero — you're all caught up", the most reassuring sentence in the app,
//             composed by a failed request.
//   #/skills  500 with skills installed →  "No skills installed" under a coral "Browse skills"
//             CTA: a user whose library is intact, told to go fix a problem they do not have.
//   #/knowledge  the other half of the same bug — the search branch never caught, so its failure
//             fell through `items === null` with `itemsLoading` false and rendered a BLANK region.
//             Silence instead of a lie, equally unactionable.
//
// The four destinations that already converged (tasks, prompts, loops, notifications) each carry a
// comment saying why the catch went; these three are the remainder that fetch a primary collection
// through `useCachedData`. After this cycle every one of them agrees.
//
// 🪤 `persist: true` (skills) means a warm cache seeds `data` and the fetch can fail AFTER a paint.
// That must keep showing the cached rows, so the gate is `data === undefined && error` — never
// `error` alone.

const nav = () => {}
const q = {}
const setQuery = () => {}
const boom = () => Promise.reject(new Error('gateway down'))

/** Everything these pages touch on mount. Overridden per test with the one endpoint under test. */
function mockApi(over: Record<string, unknown>) {
  vi.doMock('../lib/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      // inbox
      inbox: () => Promise.resolve([]),
      inboxStatus: () => Promise.resolve({ providers: [], enabled: false }),
      inboxKinds: () => Promise.resolve([]),
      inboxProviders: () => Promise.resolve([]),
      // skills
      skills: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve([]),
      // knowledge
      knowledgeCollections: () => Promise.resolve([]),
      knowledgeItems: () => Promise.resolve({ items: [] }),
      knowledgeStats: () => Promise.resolve({ items: 0, entities: 0, relations: 0 }),
      autonomyLadder: boom,
      ...over,
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('#/inbox distinguishes a failed read from an empty queue', () => {
  it('shows a retryable LoadError when the items read rejects', async () => {
    mockApi({ inbox: boom })
    const { InboxPage } = await import('./inbox/InboxPage')
    render(<InboxPage query={q} setQuery={setQuery} navigate={nav} />)
    // role=alert is LoadError's signature — the empty state has no live region, which is precisely
    // why the old behavior was unreadable to a screen reader as well as to an eye.
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/inbox/i)
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Inbox zero' }), 'not the caught-up claim').toBeNull()
  })

  it('still shows "Inbox zero" when the queue really is empty', async () => {
    mockApi({})
    const { InboxPage } = await import('./inbox/InboxPage')
    render(<InboxPage query={q} setQuery={setQuery} navigate={nav} />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Inbox zero' })).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty queue is not an error').toBeNull()
  })
})

describe('#/skills distinguishes a failed read from an empty library', () => {
  it('shows a retryable LoadError when the installed read rejects', async () => {
    mockApi({ skills: boom })
    const { SkillsPage } = await import('./skills/SkillsPage')
    render(<SkillsPage query={q} setQuery={setQuery} />)
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/skills/i)
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No skills installed' }), 'not the newcomer claim').toBeNull()
  })

  it('still shows "No skills installed" when nothing is installed', async () => {
    mockApi({})
    const { SkillsPage } = await import('./skills/SkillsPage')
    render(<SkillsPage query={q} setQuery={setQuery} />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No skills installed' })).toBeInTheDocument())
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('#/skills → Proposals distinguishes a failed read from no proposals', () => {
  it('shows a retryable LoadError when the proposals read rejects', async () => {
    mockApi({ skillProposals: boom })
    const { SkillProposals } = await import('./skills/SkillProposals')
    render(<SkillProposals />)
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/proposals/i)
    expect(screen.queryByRole('heading', { name: 'No skill proposals' }), 'not a claim about the synthesizer').toBeNull()
  })

  it('still shows "No skill proposals" when there are none', async () => {
    mockApi({})
    const { SkillProposals } = await import('./skills/SkillProposals')
    render(<SkillProposals />)
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No skill proposals' })).toBeInTheDocument())
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('#/knowledge distinguishes a failed read from an empty library', () => {
  /** The library reads its items through `knowledgeStore`, not `api` directly. */
  function mockStore(over: Record<string, unknown>) {
    vi.doMock('./knowledge/knowledgeStore', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      listKnowledge: () => Promise.resolve([]),
      knowledgeStats: () => Promise.resolve({ items: 0, entities: 0, relations: 0, embedded: 0 }),
      ...over,
    }))
  }
  const mountKnowledge = async () => {
    const { KnowledgeListPage } = await import('./knowledge/KnowledgeListPage')
    render(<KnowledgeListPage onCreate={() => {}} onOpenItem={() => {}} onOpenSources={() => {}}
      query={q} setQuery={setQuery} />)
  }

  it('shows a retryable LoadError instead of a blank region when the items read rejects', async () => {
    mockApi({}); mockStore({ listKnowledge: boom })
    await mountKnowledge()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/knowledge items/i)
    expect(screen.getByRole('button', { name: /Retry/ })).toBeInTheDocument()
  })

  it('still shows the newcomer empty state when the library really is empty', async () => {
    mockApi({}); mockStore({})
    await mountKnowledge()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Knowledge base is empty' })).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty library is not an error').toBeNull()
  })
})

describe('the sources no longer swallow their own error', () => {
  const read = (p: string) => readFileSync(join(process.cwd(), 'src', p), 'utf8')

  // 🪤 Anchored to the `useCachedData(<key>, …)` REGISTRATION, not a character window off the api
  // call: last cycle's window landed on a COMMENT naming the same method, so restoring a real catch
  // passed. The region ends at the option object / closing paren that follows the fetcher.
  const registration = (src: string, key: string) => {
    const m = new RegExp(`useCachedData(?:<[^>]*>)?\\(${key}[\\s\\S]*?\\)\\)?(?:,\\s*\\{[^}]*\\})?\\)`).exec(src)
    expect(m, `${key} fetcher must be found`).not.toBeNull()
    return m![0]
  }

  it('the inbox items read lets its rejection through', () => {
    const src = read('pages/inbox/InboxPage.tsx')
    expect(registration(src, "'inbox:items'")).not.toMatch(/\.catch\(/)
    expect(src, 'and the error gates the LoadError').toMatch(/items === undefined && itemsErr/)
  })

  it('the installed-skills read lets its rejection through', () => {
    const src = read('pages/skills/SkillsPage.tsx')
    expect(registration(src, "'skills'")).not.toMatch(/\.catch\(/)
    expect(src, 'and the error gates the LoadError').toMatch(/items === undefined && itemsErr/)
  })

  it('the knowledge items read lets BOTH branches through', () => {
    const src = read('pages/knowledge/KnowledgeListPage.tsx')
    const reg = registration(src, 'itemsKey')
    expect(reg, 'the shelf branch must not swallow').not.toMatch(/\.catch\(\(\)\s*=>\s*\[\]/)
    expect(reg, 'the search branch never did').toContain('listKnowledge')
    expect(src, 'and the error is rendered, not dropped to a blank region')
      .toMatch(/itemsData === undefined && itemsErr/)
  })
})

// ── The sentence the primitive composes ─────────────────────────────────────────────────────────
//
// 🪤 CAUGHT BY DRIVING IT, NOT BY THE TEST. The first version passed `what="the inbox"`, every
// assertion above went green (they match /inbox/i), and the screen read **"Couldn't load your the
// inbox"**. `LoadError` interpolates twice — `Couldn't load your {what}` and `Your {what} are safe`
// — so `what` has one contract, stated in its own doc comment: a lowercase, PLURAL, bare noun.
// Scoped to the four sites this cycle adds: a tree-wide version fails today on three pre-existing
// values ("the Store catalog", "the pack catalog", "the autonomy ladder") plus every singular one
// ("Your project are safe"), which is the next cycle's family and not this concern.
describe("the what= values compose LoadError's sentence", () => {
  const SITES = [
    'pages/inbox/InboxPage.tsx',
    'pages/skills/SkillsPage.tsx',
    'pages/skills/SkillProposals.tsx',
    'pages/knowledge/KnowledgeListPage.tsx',
  ]
  it('reads as a sentence in both of the primitive\'s templates', () => {
    let checked = 0
    for (const f of SITES) {
      const src = readFileSync(join(process.cwd(), 'src', f), 'utf8')
      for (const m of src.matchAll(/<LoadError what=(?:"([^"]+)"|\{[^}]*?((?:'[^']+')(?:\s*:\s*'[^']+')?)[^}]*\})/g)) {
        const values = (m[1] ? [m[1]] : (m[2] ?? '').split(/\s*:\s*/)).map((v) => v.replace(/'/g, '').trim()).filter(Boolean)
        for (const v of values) {
          checked++
          expect(v, `"Couldn't load your ${v}" — an article makes it ungrammatical`).not.toMatch(/^(the|this|a|an) /)
          expect(v, `"Your ${v} are safe" — needs a plural noun`).toMatch(/s$/)
          expect(v, 'lowercase, per the prop doc').toEqual(v.toLowerCase())
        }
      }
    }
    // Vacuity floor: five values across the four files (knowledge contributes two).
    expect(checked, 'the rail must actually find the values').toBe(5)
  })
})

// ── The convergence census ──────────────────────────────────────────────────────────────────────
//
// A family is only converged if the NEXT surface inherits the form. Every top-level list
// destination that fetches its primary collection through `useCachedData` must import `LoadError`;
// the exceptions are listed here BY NAME with the reason, and compared for exact equality so
// neither a regression nor a fix can slip past silently.
describe('every useCachedData list destination adopts LoadError', () => {
  it('has no un-named holdouts', () => {
    const dir = join(process.cwd(), 'src/pages')
    const files: string[] = []
    const walk = (d: string) => {
      for (const e of require('node:fs').readdirSync(d, { withFileTypes: true })) {
        const p = join(d, e.name)
        if (e.isDirectory()) walk(p)
        else if (e.name.endsWith('.tsx') && !e.name.includes('.test.')) files.push(p)
      }
    }
    walk(dir)

    // 🪤 `includes('useCachedData(')` MISSED THE GENERIC FORM and the census came back empty —
    // a rail matching nothing reads exactly like a converged family. `useCachedData<T>(…)` is the
    // majority spelling on these pages, so the hook has to be matched as a regex, and the
    // vacuity floor below is what would have caught it on the first run.
    const HOOK = /useCachedData(?:<[\s\S]*?>)?\(/
    const usesHook = files.filter((f) => HOOK.test(readFileSync(f, 'utf8')))
    expect(usesHook.length, 'vacuity floor: the hook must be found across the pages tree')
      .toBeGreaterThan(20)

    const holdouts = files.filter((f) => {
      const src = readFileSync(f, 'utf8')
      // A list destination: renders the shared EmptyState AND reads through the shared cache hook.
      return src.includes('<EmptyState') && HOOK.test(src) && !src.includes('LoadError')
    }).map((f) => f.slice(dir.length + 1).replace(/\\/g, '/')).sort()

    // Each holdout swallows through a DIFFERENT mechanism, so each needs its own judgement — not a
    // bulk find-and-replace. Named here so the list is a decision record rather than a blind spot.
    expect(holdouts).toEqual([
      // Promise.all of five reads with per-read fallbacks; partial tolerance is the design here
      // (`load_failures` is a first-class concept on this surface), so "did it fail" is not one bit.
      'tools/ToolsPage.tsx',
      // Renders the week grid from a prop, and its own reads go through useState + try/catch rather
      // than the hook's `error` — a different mechanism, so a different fix. Deliberately not bulked in.
      'triggers/WeekGridView.tsx',
    ])
  })
})
