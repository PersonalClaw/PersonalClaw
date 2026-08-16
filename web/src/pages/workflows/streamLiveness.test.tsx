import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A dead feed and a quiet run look identical, and only one of them is fine ────────────────────────
//
// `useWorkflowStream` swallowed `onerror` with "transient — EventSource retries automatically". True,
// and that is why it is dangerous: the browser retries forever, so there is no give-up branch and no
// error state — the run view simply stops updating. A user watching a live run cannot tell
// "nothing is happening right now" from "we lost the feed", and the run's own status chip keeps
// cheerfully saying **Running** either way.
//
// 🔑 CONVERGENCE, NOT INVENTION. `settings/DiagnosticsPanel` already ships a liveness indicator for its
// own SSE feed — a `size-1.5` dot plus the WORD "Streaming"/"Connecting…". This reuses that form and
// vocabulary verbatim: same dot, same two words, `--color-ok` when up and `--color-on-surface-low` when
// not. The colour only confirms the word, which is what keeps it clear of 1.4.1 — a dot alone would
// carry the state in hue only.
//
// 🪤 THE INDICATOR IS THE FEED'S, NOT THE RUN'S. It renders only while `live` (`!!run &&
// !isTerminal(run.status)`): a finished run has no stream to be connected to, so a "Connecting…" on a
// completed run would be a lie. It also sits BESIDE the run-status chip rather than replacing it —
// two different facts, and conflating them is what made the defect invisible.
//
// 🪤 `chat/WorkflowProgressCard` deliberately does NOT show it. It calls the same hook and simply
// ignores the new return value, because it is a compact card inside a chat turn where a second status
// row would crowd the message; the run view is where someone watches for progress. Pinned below so the
// omission reads as a decision rather than an oversight.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the workflow run view says whether its feed is alive', () => {
  it('the hook reports liveness from the transport, not from a guess', () => {
    const code = read('pages/workflows/useWorkflowStream.ts')
    expect(code).toMatch(/es\.onopen = \(\) => setConnected\(true\)/)
    expect(code).toMatch(/es\.onerror = \(\) => setConnected\(false\)/)
    expect(code, 'and hands it to the caller').toMatch(/return \{ connected \}/)
  })

  it('it resets when the target run changes, so state cannot leak between runs', () => {
    // Without this, opening run B shows run A's last-known liveness until B's first event.
    const code = read('pages/workflows/useWorkflowStream.ts')
    expect(code).toMatch(/useEffect\(\(\) => \{\s*\n\s*setConnected\(false\)/)
    expect(code, 'and on teardown').toMatch(/es\?\.close\(\); setConnected\(false\)/)
  })

  it('it still never closes or resubscribes on error — the browser owns the retry', () => {
    const code = read('pages/workflows/useWorkflowStream.ts')
    // The whole point is to REPORT the gap, not to take over reconnection.
    expect(code).not.toMatch(/onerror = \(\) => \{[^}]*es\.close\(\)/)
    expect(code).not.toMatch(/onerror[\s\S]{0,80}new EventSource/)
  })

  it('the run view renders the dot AND the word', () => {
    const code = read('pages/workflows/WorkflowRunDetail.tsx')
    expect(code).toMatch(/const \{ connected \} = useWorkflowStream/)
    expect(code, 'the word carries the state').toMatch(/\{connected \? 'Streaming' : 'Connecting…'\}/)
    expect(code, 'the colour only confirms it')
      .toMatch(/background: connected \? 'var\(--color-ok\)' : 'var\(--color-on-surface-low\)'/)
  })

  it('it shows only while the run is live', () => {
    // A terminal run has no stream; "Connecting…" on a finished run would be a lie.
    expect(read('pages/workflows/WorkflowRunDetail.tsx')).toMatch(/\{live && \(\s*\n\s*<span className="inline-flex shrink-0 items-center gap-1 text-on-surface-low/)
  })

  it('the form it converges on is still the one DiagnosticsPanel ships', () => {
    // Vacuity floor: if that panel changes its vocabulary, this should be re-argued, not left to drift.
    const diag = read('pages/settings/DiagnosticsPanel.tsx')
    expect(diag).toMatch(/connected \? 'Streaming' : 'Connecting…'/)
    expect(diag).toMatch(/size-1\.5 rounded-pill/)
  })

  it('the chat card deliberately opts out', () => {
    const card = read('pages/chat/WorkflowProgressCard.tsx')
    expect(card, 'it calls the hook').toMatch(/useWorkflowStream\(/)
    expect(card, 'and ignores the liveness return by design — a chat turn is not a watch surface')
      .not.toMatch(/\{ connected \} = useWorkflowStream/)
  })
})
