import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A COMMENT THREAD THAT FAILED TO LOAD IS NOT AN EMPTY THREAD (issue #554) ──────────────────────
//
// `TaskDetail`'s `Comments` loaded with `.catch(() => setComments([]))`, so an unreachable gateway, a
// 500, or a provider error rendered as "this task has no comments" — a thread the user had written
// into read as blank, with nothing on screen saying otherwise and no way to retry. The WRITE path was
// already honest (`reportingWrite`); this is the same honesty for the read.
//
// The same defect family as `fleetReadFailureVisible` (`.catch(() => setAgents([]))`) and
// `guardrailReadHonesty`: a catch that WRITES the collection converts a transient failure into a
// factual claim about the user's data. Asserted over source because the failure is a MISSING branch —
// nothing renders differently, which is exactly what makes it invisible to a screenshot, to axe, and
// to a passing render test.

const SRC = join(__dirname, '..', '..')
const raw = readFileSync(join(SRC, 'pages', 'tasks', 'TaskDetail.tsx'), 'utf8')

/** The `Comments` component body, comments stripped so a quoted defect in prose cannot satisfy or
 *  break an assertion about code. */
function comments(): string {
  const at = raw.indexOf('function Comments(')
  expect(at, 'Comments must still exist in TaskDetail.tsx').toBeGreaterThan(0)
  const end = raw.indexOf('\nfunction ', at + 1)
  return (end > at ? raw.slice(at, end) : raw.slice(at))
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
}

describe('the task-comment read', () => {
  it('reads its subject — a rail over nothing asserts nothing', () => {
    const fn = comments()
    expect(fn.length, 'the Comments body did not read').toBeGreaterThan(800)
    expect(fn, 'it must still load the thread').toMatch(/api\.taskComments\(/)
    expect(fn, 'and still post through the reporting write').toMatch(/reportingWrite\(/)
  })

  it('🔴 a failed read does not become an empty thread', () => {
    const fn = comments()
    // The exact regression: `.catch(() => setComments([]))`.
    expect(fn, 'a failed read must not claim the task has no comments')
      .not.toMatch(/catch\s*\(\s*\)?\s*=>\s*setComments\(\s*\[\s*\]\s*\)/)
    expect(fn, 'and must not clear the list anywhere in the failure path')
      .not.toMatch(/catch[\s\S]{0,80}?setComments\(\s*\[\s*\]\s*\)/)
  })

  it('the failure is its own state, distinguishable from an empty thread', () => {
    const fn = comments()
    expect(fn, 'a failure held separately from the list').toMatch(/setLoadErr\(/)
    // …and it must CLEAR on a good read, or one blip pins the error forever.
    expect(fn, 'a good read clears it').toMatch(/setLoadErr\(null\)/)
  })

  it('says so in words, with a retry', () => {
    const fn = comments()
    expect(fn, 'the failure must render').toMatch(/!!loadErr\s*&&/)
    expect(fn, 'through the inline failure primitive').toMatch(/<InlineError[\s\S]{0,120}onRetry=\{load\}/)
    expect(fn, 'naming what failed').toMatch(/Couldn't load the comments/)
    // The server's own message when it has one — `readableErrText` drops the engine-specific
    // fetch noise and passes a backend sentence through.
    expect(fn, 'and relaying the reason').toMatch(/readableErrText\(loadErr\)/)
  })
})
