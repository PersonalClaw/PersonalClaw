import { describe, it, expect, vi, beforeEach } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── One collection, two cache keys, one invalidated ──────────────────────────────────────────────
//
// The last un-swept axis: what a surface claims about work it does not own. `useQuery` is keyed,
// so a collection read under TWO keys has two independent caches — and a mutation that busts only its
// own key leaves the sibling describing a collection that has already changed.
//
// Two measured instances:
//
//   skill proposals   `skill-proposals` (the list) + `skill-proposals-count` (SkillsPage's
//                     "Proposals (N)" badge). Decisions land on THREE surfaces — the skills sub-page,
//                     `#/inbox`, and the dashboard's Action Center — and none of them touched the
//                     badge's key. Accept a proposal anywhere and the number kept counting it.
//   artifacts         the chat composer's attach picker cached the collection under its own key while
//                     deletes happen on `#/artifacts` and inside chat itself (`WidgetFrame`). The
//                     picker kept offering a deleted artifact, and attaching it fails.
//
// 🔑 THE PRIMITIVE ALREADY EXISTED AND NOTHING USED IT. `invalidateKeys(keyOrPrefix, prefix = true)`
// has had prefix mode all along, with zero callers. The proposals keys already shared a prefix, so one
// call keeps every key on that collection in step — including a key added later. The picker's key was
// `chat:artifact-picker`, in the CHAT namespace rather than its collection's, so it could never be
// caught that way; renaming it to `artifacts:chat-picker` is what makes `invalidateKeys('artifacts:',
// true)` cover it. **Name a cache key after the COLLECTION it reads, not the surface that reads it.**
//
// 🪤 The naive census that found this was wrong twice before it was right: a literal
// `invalidateKeys('k')` scan called 92 of 138 keys "never invalidated" because
// `settingsWidgets.mutate(fn, ...keys)` invalidates through a VARIABLE, and a `delete*`-then-reload
// scan flagged detail panels whose PARENT reloads via `onDeleted`. Follow the indirection first.

const proposals = [{ id: 'p1', name: 'summarize-pr', description: 'x', procedure: 'y' }]

function mockApi(over: Record<string, unknown> = {}) {
  const named: Record<string, unknown> = {
    skillProposals: () => Promise.resolve({ proposals, lastReview: null }),
    acceptSkillProposal: () => Promise.resolve({ name: 'summarize-pr' }),
    rejectSkillProposal: () => Promise.resolve({ ok: true }),
    skills: () => Promise.resolve([]),
    ...over,
  }
  const api = new Proxy(named, {
    get(t, prop: string) { return prop in t ? t[prop] : () => Promise.resolve([]) },
  })
  vi.doMock('../lib/api', async (orig) => ({ ...(await orig<Record<string, unknown>>()), api }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('a decision on one surface does not leave a sibling count stale', () => {
  it('busts every key on the proposals collection, not just its own', async () => {
    mockApi()
    const { invalidateKeys, writeQuery, peekQuery } = await import('./data')
    // Both keys warm, as they would be after visiting the Skills page.
    writeQuery('skill-proposals', proposals)
    writeQuery('skill-proposals-count', proposals)
    expect(peekQuery('skill-proposals-count'), 'the badge cache starts warm').toBeTruthy()

    // What the fixed reload does.
    invalidateKeys('skill-proposals', true)

    expect(peekQuery('skill-proposals'), 'the list cache is dropped').toBeUndefined()
    // 🔑 The whole defect: this is the one that used to survive.
    expect(peekQuery('skill-proposals-count'), "the badge's cache is dropped too").toBeUndefined()
  })

  it('prefix mode leaves unrelated collections alone', async () => {
    mockApi()
    const { invalidateKeys, writeQuery, peekQuery } = await import('./data')
    writeQuery('skill-proposals-count', proposals)
    writeQuery('skills', ['a'])
    writeQuery('artifacts:chat-picker', ['b'])
    invalidateKeys('skill-proposals', true)
    expect(peekQuery('skill-proposals-count')).toBeUndefined()
    expect(peekQuery('skills'), 'a neighbouring key must survive').toEqual(['a'])
    expect(peekQuery('artifacts:chat-picker'), 'another collection must survive').toEqual(['b'])
  })

  it('the artifacts namespace covers the chat picker', async () => {
    mockApi()
    const { invalidateKeys, writeQuery, peekQuery } = await import('./data')
    writeQuery('artifacts:chat-picker', ['b'])
    writeQuery('chat:suggestions', ['keep me'])
    invalidateKeys('artifacts:', true)
    expect(peekQuery('artifacts:chat-picker'), 'the picker cache is dropped').toBeUndefined()
    expect(peekQuery('chat:suggestions'), 'the chat namespace is untouched').toEqual(['keep me'])
  })
})

describe('every mutation site busts the collection it changed', () => {
  const SRC = join(process.cwd(), 'src')
  const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('all three proposal-decision sites bust the proposals prefix', () => {
    // Three surfaces decide the same proposal; a fix at one is a fix for one journey only.
    for (const rel of [
      'pages/skills/SkillProposals.tsx',
      'pages/inbox/InboxDetail.tsx',
      'pages/dashboard/widgets/ActionCenter.tsx',
    ]) {
      expect(codeOf(rel), `${rel} must bust the whole collection`)
        .toMatch(/invalidateKeys\('skill-proposals', true\)/)
    }
  })

  it('both artifact mutation sites bust the artifacts namespace', () => {
    for (const rel of ['pages/artifacts/ArtifactViewer.tsx', 'ui/widget/WidgetFrame.tsx']) {
      expect(codeOf(rel), `${rel} must bust the artifacts namespace`)
        .toMatch(/invalidateKeys\('artifacts:', true\)/)
    }
  })

  it('the picker key lives in its COLLECTION namespace, not the chat one', () => {
    // 🪤 The rename is load-bearing: `chat:artifact-picker` could never be caught by an
    // artifacts-prefix bust, so the naming was the defect's other half.
    const chat = codeOf('pages/ChatPage.tsx')
    expect(chat).toMatch(/useQuery\('artifacts:chat-picker'/)
    expect(chat, 'the old surface-namespaced key must be gone').not.toMatch(/chat:artifact-picker/)
  })

  it('prefix mode is actually reachable — it was dead code before this change', () => {
    const cache = codeOf('lib/data/store.ts')
    expect(cache, 'the primitive must still take the flag').toMatch(/invalidateKeys\(keyOrPrefix: string, prefix = false\)/)
    // At least the five call sites this cycle added; a regression to per-key busting drops the count.
    const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
    })
    const users = walk(SRC).filter((f) => /invalidateKeys\([^)]*,\s*true\)/.test(codeOf(f.slice(SRC.length + 1))))
    expect(users.length, 'prefix-mode call sites').toBeGreaterThanOrEqual(5)
  })
})
