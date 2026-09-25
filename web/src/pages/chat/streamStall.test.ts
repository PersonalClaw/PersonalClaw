import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { STREAM_SETTLED_GRACE_MS, resolveStalledStream } from './streamStall'

const quiet = STREAM_SETTLED_GRACE_MS + 1

describe('resolveStalledStream', () => {
  // The measured defect. CI run 36091495547 (`e2e-a11y`, sessionMap.spec.ts SSM-13, and
  // again on retry1) left a chat claiming the stream for 84 seconds over a turn that had
  // already finished: the reply was fully rendered, the session's ledger row had landed
  // ("unpriced · 51 tokens"), and the page still showed "Assistant is responding…" with the
  // composer's action button reading Stop. `!(isLast && streaming)` therefore suppressed the
  // assistant action row, so Speak / Copy / Regenerate / Fork never existed for that answer
  // and nothing short of a reload could recover it.
  it('ends the streaming claim once the server holds no task for the session', () => {
    expect(resolveStalledStream({
      serverRunning: false,
      serverPendingApproval: false,
      msSinceTranscriptChange: quiet,
    })).toBe('settled')
  })

  // The window the grace exists for, and the reason this is not simply `!running`.
  // `send()` flips `streaming` on BEFORE it posts, so until the handler assigns
  // `session.task` the server truthfully reports no run. Clearing there would advertise
  // "Send message" over a turn that is about to stream — issue #3444's defect with the sign
  // flipped. A live turn repaints the transcript, which restarts this window, so the grace
  // can only elapse over a transcript that has actually stopped changing.
  it('does NOT end it while the dispatch may still be in flight', () => {
    expect(resolveStalledStream({
      serverRunning: false,
      serverPendingApproval: false,
      msSinceTranscriptChange: STREAM_SETTLED_GRACE_MS - 1,
    })).toBe('wait')
  })

  // CONTROL for the test above: the ONLY thing separating it from the `settled` case is the
  // elapsed window, so the boundary is asserted rather than described.
  it('ends it exactly at the boundary, not one tick later', () => {
    expect(resolveStalledStream({
      serverRunning: false,
      serverPendingApproval: false,
      msSinceTranscriptChange: STREAM_SETTLED_GRACE_MS,
    })).toBe('settled')
  })

  // The pre-existing reading this must not break: a turn parked on an approval sends no
  // `chat_done` either, and the server IS still running. Quiet does not mean finished there.
  it('recovers a parked approval instead of ending the stream', () => {
    expect(resolveStalledStream({
      serverRunning: true,
      serverPendingApproval: true,
      msSinceTranscriptChange: quiet,
    })).toBe('recover-approval')
  })

  // A long model think is the case that must stay untouched — the server is running and has
  // nothing parked, so silence is just silence.
  it('leaves a genuinely quiet live turn alone', () => {
    expect(resolveStalledStream({
      serverRunning: true,
      serverPendingApproval: false,
      msSinceTranscriptChange: quiet,
    })).toBe('wait')
  })

  // A stale approval on a session the server is no longer running is still a client that must
  // stop claiming the stream. The caller re-hydrates on `settled` too, so the persisted card
  // still surfaces — the claim being corrected is the stream's, not the card's.
  it('ends the streaming claim even when a stale approval is pending', () => {
    expect(resolveStalledStream({
      serverRunning: false,
      serverPendingApproval: true,
      msSinceTranscriptChange: quiet,
    })).toBe('settled')
  })
})

// ── The rail that keeps this from being a decision layer with no call site ─────────────────────
//
// Source-derived because nothing in `web/` can mount `ChatPage` — the repo's shipped pattern for
// pinning a ChatPage wiring contract (`sessionDelivery.test.ts`) does the same, for the same
// reason. Every assertion below is keyed on an anchor that must exist, so a rename reds the rail
// instead of silently making it vacuous.
const CHAT_PAGE = readFileSync(join(process.cwd(), 'src', 'pages', 'ChatPage.tsx'), 'utf8')
/** The comments here quote the rule at length, so the rail must read CODE only. */
const stripComments = (s: string) =>
  s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
/** The idle stream-reconciler effect — the one silent-while-streaming poll. */
function reconcilerBody(src: string): string {
  const anchor = src.indexOf('const showingApproval =')
  expect(anchor, 'the idle stream-reconciler must exist in ChatPage — the rail is keyed on it').toBeGreaterThan(-1)
  const start = src.lastIndexOf('useEffect(() => {', anchor)
  expect(start, 'the reconciler must live in a useEffect').toBeGreaterThan(-1)
  const end = src.indexOf('}, [streaming, turns])', anchor)
  expect(end, 'the reconciler effect must close on [streaming, turns]').toBeGreaterThan(start)
  return stripComments(src.slice(start, end))
}

describe('ChatPage wires the stall reading', () => {
  it('asks resolveStalledStream instead of re-deriving the rule inline', () => {
    const body = reconcilerBody(CHAT_PAGE)
    expect(body, 'the reconciler must resolve the stall through the shared reading').toMatch(/resolveStalledStream\(\{/)
    // The pre-fix rule, which threw the server's `running` away and returned on anything that was
    // not a pending approval. If it comes back, the stall is undetectable again.
    expect(body, 'a bare `!d.pending_approval` early return is the pre-fix rule — it drops `running`')
      .not.toMatch(/if\s*\(!d\.pending_approval\)\s*return/)
  })

  it('ENDS the streaming claim on a settled verdict', () => {
    const body = reconcilerBody(CHAT_PAGE)
    // Without this the reading is inert: the whole defect is a `streaming` that never clears.
    expect(body, 'a settled stall must clear `streaming` — that is the lie being corrected')
      .toMatch(/stall === 'settled'[\s\S]*markStreaming\(false\)/)
    // The transcript tail is replaced from history first, so a buffered coalescer tail must be
    // DISCARDED, not landed (`endTextRun` would write the old answer into the replaced tail).
    expect(body, 'a settled stall must drop the coalescer run, not seal it')
      .toMatch(/stall === 'settled'[\s\S]*dropTextRun\(\)/)
  })

  it('feeds the reading the SERVER\'s running flag, not a client guess', () => {
    const body = reconcilerBody(CHAT_PAGE)
    expect(body, 'serverRunning must come from session detail').toMatch(/serverRunning:\s*!!d\.running/)
    expect(body, 'serverPendingApproval must come from session detail').toMatch(/serverPendingApproval:\s*!!d\.pending_approval/)
  })
})
