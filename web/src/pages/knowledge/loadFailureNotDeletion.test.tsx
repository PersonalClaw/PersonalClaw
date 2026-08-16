import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── "It no longer exists" is a claim about the USER'S DATA, so it needs a 404 ──────────────────────
//
// Two detail surfaces asserted a deletion on ANY failed read:
//
//   knowledge-detail   `getKnowledge` is `try { … } catch { return null }`, so a 500, a dropped
//                      connection and a real 404 arrive identically as `null`. The page set
//                      `missing` and rendered "This knowledge item no longer exists." — and titled
//                      the header "Not found".
//   projects-detail    `useCachedData(...)` returns `data: undefined` on error, and this call site
//                      destructured only `{ data, loading, refresh }`. A 500 fell through to
//                      `!project` → "This project no longer exists."
//
// Both are worse than a generic error: they tell the user something was destroyed when the server
// merely failed to answer, and they offer no retry — so the honest recovery (reload) looks pointless.
//
// 🔑 THE LINE ALREADY EXISTS IN THIS REPO, which is why this is convergence rather than invention.
// `ProjectsSection`'s `dirErrorMessage` switches on `ApiError.status`: 404 → "no longer exists on
// disk", 400/403 → outside the browsable area, else → "couldn't read this directory". And
// `CodeCockpitPage` carries the same rule in a comment: *"Only flag 'no longer exists' when the dir is
// genuinely GONE."* These two surfaces were simply never brought in line.
//
// 🪤 WHY THE STORE WAS NOT THE FIX. The obvious change is to stop `getKnowledge` swallowing the error,
// but it has five other callers and TWO of them (`KnowledgeDetail.tsx:43` and `:76`) have no `.catch`
// — making it throw would turn a false message into an unhandled rejection. The page therefore calls
// `api.knowledgeItem` directly, where `ApiError.status` is still intact, and the store's
// `null`-means-not-found contract is left exactly as its other callers expect.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('a failed read is not a deletion', () => {
  it('knowledge-detail claims deletion ONLY on a 404', () => {
    const code = read('pages/knowledge/KnowledgeDetailPage.tsx')
    expect(code, 'the status has to survive, so the API is called directly')
      .toMatch(/api\.knowledgeItem\(id\)/)
    expect(code).toMatch(/if \(e instanceof ApiError && e\.status === 404\) setMissing\(true\)/)
    expect(code, 'anything else is a load failure').toMatch(/else setLoadErr\(e\)/)
  })

  it('knowledge-detail offers a retry instead of asserting a deletion', () => {
    const code = read('pages/knowledge/KnowledgeDetailPage.tsx')
    expect(code).toMatch(/loadErr \? \(/)
    expect(code).toMatch(/<LoadError what="knowledge item" error=\{loadErr\} onRetry=/)
    // The deletion copy must still exist — for the case that IS a deletion.
    expect(code).toMatch(/This knowledge item no longer exists\./)
  })

  it("knowledge-detail's header does not say Not found while a read is merely failing", () => {
    // The title is the other place the claim leaked: "Not found" beside a 500 is the same lie.
    expect(read('pages/knowledge/KnowledgeDetailPage.tsx'))
      .toMatch(/missing \? 'Not found' : loadErr \? "Couldn't load" : 'Loading…'/)
  })

  it('projects-detail stops discarding the load error', () => {
    const code = read('pages/projects/ProjectsSection.tsx')
    expect(code, 'the error must be destructured to be checkable')
      .toMatch(/const \{ data: project, loading, error: detailErr, refresh \} = useCachedData/)
    expect(code, 'and a failed read renders the retry, before the deletion branch')
      .toMatch(/if \(!project && detailErr\) \{[\s\S]{0,220}?<LoadError what="project" error=\{detailErr\} onRetry=\{refresh\} \/>/)
  })

  it('projects-detail keeps the deletion copy for a genuine absence', () => {
    const code = read('pages/projects/ProjectsSection.tsx')
    expect(code).toMatch(/This project no longer exists\./)
    // Order matters: the error branch must come FIRST, or it is unreachable.
    expect(code.indexOf('if (!project && detailErr)'))
      .toBeLessThan(code.indexOf('This project no longer exists.'))
  })

  it('the retry copy does not double the article ("your this project")', () => {
    // Caught by DRIVING it, not by reading the diff: `LoadError` renders "Couldn't load your <what>",
    // so a `what` beginning with "this" produced "Couldn't load your this knowledge item". The same
    // shape once shipped as "Couldn't load your your tasks".
    for (const rel of ['pages/knowledge/KnowledgeDetailPage.tsx', 'pages/projects/ProjectsSection.tsx']) {
      expect(read(rel), `${rel} must not pass a demonstrative to LoadError`)
        .not.toMatch(/<LoadError what="(this|your|the) /)
    }
  })

  it('the 404-vs-other rule this converges on is still the repo’s rule', () => {
    // Vacuity floor: if `dirErrorMessage` stops distinguishing 404, the precedent cited above is gone
    // and these two surfaces should be re-argued rather than silently kept.
    expect(read('pages/projects/ProjectsSection.tsx'))
      .toMatch(/if \(status === 404\) return "This folder no longer exists on disk\."/)
  })

  it('the store contract its other callers rely on is untouched', () => {
    // Two of those callers have no `.catch`; making the store throw would trade a false message for
    // an unhandled rejection. Pinned so a later "cleanup" does not do exactly that.
    expect(read('pages/knowledge/knowledgeStore.ts'))
      .toMatch(/try \{ return await api\.knowledgeItem\(id\) \} catch \{ return null \}/)
  })
})
