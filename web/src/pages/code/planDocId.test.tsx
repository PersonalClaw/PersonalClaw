import { describe, expect, it, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { commentStore } from '../files/comments/commentStore'

// ── A planning comment docId must identify ONE document ────────────────────────
//
// `CommentLayer`'s `docId` is the comment store's DOCUMENT IDENTITY, and that store is a single
// global localStorage key (`doc-comments-v1`) shared by every surface that can be commented on:
// files (`entry.path`), artifacts (`art.slug`), and the two planning walkthroughs.
//
// So the id has to be unique per document, and one of the two planning views was not:
//
//     LoopPlanningView   `plan-${loopId}-${kind}`     scoped per loop      ✓
//     CodePlanningView   `code-plan-${kind}`          NO project scope     ✗
//
// Every Code project's "requirements" step was therefore the SAME document. Comments left while
// planning project A came back on project B — and not merely listed: `CommentLayer` passes `docId`
// straight through as `activeDocId`, and `CommentDeck` renders `muted={c.docId !== activeDocId}`,
// so another project's comments render as ACTIVE ON THIS DOCUMENT. They also ride along into the
// planner: the deck's submit collects comments and posts them to `api.uLoopPlanComment` for the
// loop currently open.
//
// The fix converges on the form that was already correct rather than inventing a third: the config
// becomes a factory closing over `projectId` (exactly LoopPlanningView's `makeCfg`), which is what
// a module-level `const CFG` could not do — the reason the scope went missing in the first place.
//
// The census that found it (cycle 32) also ruled the two `ArtifactView` renderers a DISTINCTION:
// they read disjoint artifact schemas (stories/decisions/entities vs sub_goals/roster/criteria) and
// even their `phases` agree only on the NAME (title/stage/objective/tasks vs role/min_cycles/
// target/phase_exit). This docId was the real divergence hiding underneath.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the comment store keys documents by docId alone', () => {
  beforeEach(() => { commentStore.clear() })

  it('two documents with the SAME id are one document — the defect, reproduced', () => {
    // This is what `code-plan-requirements` did across two projects.
    commentStore.add({ docId: 'code-plan-requirements', docLabel: 'requirements plan', quote: 'q', comment: 'from project A' })
    const forProjectB = commentStore.all().filter((c) => c.docId === 'code-plan-requirements')
    expect(forProjectB).toHaveLength(1)
    expect(forProjectB[0].comment).toBe('from project A')
  })

  it('project-scoped ids keep two projects apart', () => {
    commentStore.add({ docId: 'code-plan-projA-requirements', docLabel: 'requirements plan', quote: 'q', comment: 'from A' })
    commentStore.add({ docId: 'code-plan-projB-requirements', docLabel: 'requirements plan', quote: 'q', comment: 'from B' })
    const a = commentStore.all().filter((c) => c.docId === 'code-plan-projA-requirements')
    const b = commentStore.all().filter((c) => c.docId === 'code-plan-projB-requirements')
    expect(a).toHaveLength(1)
    expect(b).toHaveLength(1)
    expect(a[0].comment).toBe('from A')
    expect(b[0].comment).toBe('from B')
  })
})

describe('both planning views scope their artifact docId to their own run', () => {
  it('CodePlanningView includes the projectId', () => {
    // Reverting to `code-plan-${kind}` reds this.
    expect(read('pages/code/CodePlanningView.tsx')).toMatch(/docId=\{`code-plan-\$\{projectId\}-\$\{kind\}`\}/)
  })

  it('LoopPlanningView includes the loopId (unchanged, pinned)', () => {
    expect(read('pages/loops/LoopPlanningView.tsx')).toMatch(/docId=\{`plan-\$\{loopId\}-\$\{kind\}`\}/)
  })

  it('the Code config is a factory, which is what lets the renderer reach the id', () => {
    const src = read('pages/code/CodePlanningView.tsx')
    expect(src).toMatch(/function makeCfg\(projectId: string\): WalkthroughConfig/)
    // A module-level constant cannot close over a prop — the shape that caused the bug.
    expect(/^const CFG: WalkthroughConfig/m.test(src), 'CFG should no longer be a module constant').toBe(false)
    expect(src).toMatch(/cfg=\{makeCfg\(projectId\)\}/)
  })

  it('no planning docId is built from the step kind alone', () => {
    // The general rule, so a third walkthrough cannot reintroduce it. Comments are stripped first:
    // the migration note above NAMES the old unscoped id, and a bare text search would count the
    // explanation as the defect.
    for (const rel of ['pages/code/CodePlanningView.tsx', 'pages/loops/LoopPlanningView.tsx']) {
      const code = read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      const ids = [...code.matchAll(/docId=\{`([^`]+)`\}/g)].map((m) => m[1])
      expect(ids.length, `${rel} should build at least one docId`).toBeGreaterThan(0)
      for (const id of ids) {
        expect(
          /\$\{(projectId|loopId)\}/.test(id),
          `${rel}: docId \`${id}\` has no run scope — two runs would share one comment thread`,
        ).toBe(true)
      }
    }
  })
})
