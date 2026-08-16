import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The loop cockpit's feed, same defect as the workflow run view ───────────────────────────────────
//
// `useRunStream` swallowed `onerror` with "transient — EventSource retries automatically". The browser
// does retry forever, which is why there is no give-up branch to notice: the cockpit just stops
// updating, and its status line keeps saying **Working** whether the loop is thinking or the feed is
// gone. Ported from `workflows/useWorkflowStream` (the reference implementation) rather than re-derived.
//
// 🪤 THIS HOOK HAS FOUR CONSUMERS AND ONLY ONE SHOWS THE SIGNAL. That is stated as an INCOMPLETENESS,
// not dressed up as a decision: `loops/LoopCockpitPage` is the primary loop run view and gets it now.
// `loops/DesignCockpitPage` and `code/CodeCockpitPage` are also full-page watch surfaces and **should
// follow** — they were left only because each has its own bespoke header and no shared component to edit
// once. `loops/LoopPlanReview` is a review panel rather than a live watch view, so it is the one genuine
// opt-out. Returning the flag is backwards-compatible: all four compile unchanged.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the loop cockpit says whether its feed is alive', () => {
  it('the hook reports liveness from the transport', () => {
    const code = read('pages/loops/useRunStream.ts')
    expect(code).toMatch(/es\.onopen = \(\) => setConnected\(true\)/)
    expect(code).toMatch(/es\.onerror = \(\) => setConnected\(false\)/)
    expect(code).toMatch(/return \{ connected \}/)
  })

  it('it resets per target and on teardown, so state cannot leak between loops', () => {
    const code = read('pages/loops/useRunStream.ts')
    expect(code).toMatch(/setConnected\(false\)\n    if \(!enabled \|\| !id\) return/)
    expect(code).toMatch(/es\?\.close\(\); setConnected\(false\)/)
  })

  it('it still never closes or resubscribes on error — the browser owns the retry', () => {
    const code = read('pages/loops/useRunStream.ts')
    expect(code).not.toMatch(/onerror = \(\) => \{[^}]*es\.close\(\)/)
    expect(code).not.toMatch(/onerror[\s\S]{0,80}new EventSource/)
  })

  it('the cockpit renders the dot AND the word, only while running', () => {
    const code = read('pages/loops/LoopCockpitPage.tsx')
    expect(code).toMatch(/const \{ connected \} = useRunStream/)
    expect(code).toMatch(/\{connected \? 'Streaming' : 'Connecting…'\}/)
    expect(code).toMatch(/background: connected \? 'var\(--color-ok\)' : 'var\(--color-on-surface-low\)'/)
    expect(code, 'a finished loop has no stream').toMatch(/\{running && \(/)
  })

  it('it reuses the vocabulary the workflow run view already adopted', () => {
    // Vacuity floor across the family: if the reference implementation changes its words, this should be
    // re-argued rather than left to drift into a second dialect.
    expect(read('pages/workflows/WorkflowRunDetail.tsx')).toMatch(/\{connected \? 'Streaming' : 'Connecting…'\}/)
    expect(read('pages/settings/DiagnosticsPanel.tsx')).toMatch(/connected \? 'Streaming' : 'Connecting…'/)
  })

  it('the remaining consumers still compile against the new return', () => {
    // Backwards-compatible by construction: they ignore the value.
    for (const rel of ['pages/loops/DesignCockpitPage.tsx', 'pages/code/CodeCockpitPage.tsx', 'pages/loops/LoopPlanReview.tsx']) {
      expect(read(rel), `${rel} calls the hook`).toMatch(/useRunStream\(/)
    }
  })
})
