/** "Optimize prompt" said nothing when it worked and nothing when it failed (#277).
 *
 * Both composers spelled the outcome as `if (r.changed && r.optimized) { …rewrite… }`, with no
 * else, inside a `try` whose `catch` was the bare comment `/* keep the draft * /`. The endpoint
 * returns `changed:false` DELIBERATELY for a prompt it has nothing to add to — measured in the
 * report on a 514-char prompt, after a ~15-second spinner, with a clean console and nothing
 * logged server-side. So three of the four things a click can do were rendered as the same
 * nothing, and the one failure mode users actually concluded from that was "optimize is broken".
 *
 * These drive the CLASSIFIER and the TOAST, which are the two things the fix adds:
 *  · the classifier is the missing branch itself, so it is exercised over all four answers;
 *  · the sentence is then pushed through the REAL `notify` into a mounted `Toaster`, because a
 *    message that never reaches a live region is the same silence in a longer code path.
 *
 * The call sites are pinned by source at the bottom, narrowly and with a vacuity floor — the same
 * trade-off `knowledge/intentIsEditable.test.tsx` records for reachability, and for the same
 * reason: driving the real button means a CodeMirror composer inside a whole page. The button was
 * also clicked by hand on `#/loop` and `#/chat` against a live gateway during validation.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { optimizeOutcome, optimizeFailure } from './optimizeOutcome'
import { notify } from '../../app/appSdk'
import { Toaster } from '../Toaster'

const read = (p: string) => readFileSync(join(process.cwd(), 'src', p), 'utf8')

beforeEach(() => cleanup())
afterEach(() => cleanup())

describe('the optimizer verdict names all four answers, not just the rewrite', () => {
  it('a real rewrite is a rewrite, and carries the text', () => {
    const out = optimizeOutcome({ changed: true, optimized: 'Fix the bus routes. Before changing anything: 1. …' })
    expect(out.kind).toBe('rewritten')
    expect(out.kind === 'rewritten' && out.optimized).toMatch(/^Fix the bus routes/)
  })

  it('🔑 changed:false is a SUCCESSFUL answer that says what happened', () => {
    // The exact shape the report measured: the optimizer echoes the input verbatim and flags it.
    const prompt = 'I plan school bus routes for a 9,000-student district…'
    const out = optimizeOutcome({ changed: false, optimized: prompt })
    expect(out.kind, 'echoing the input is NOT a rewrite').toBe('unchanged')
    expect(out.kind !== 'rewritten' && out.level, 'a good prompt is not bad news').toBe('info')
    expect(out.kind !== 'rewritten' && out.message).toMatch(/already reads well/)
  })

  it('🪤 changed:true with no text is unchanged, not a draft-blanking rewrite', () => {
    // The old condition (`r.changed && r.optimized`) already fell through to silence here, so
    // this is not a new case — but resolving it as a rewrite would clear what the user typed,
    // which is the one outcome worse than saying nothing.
    for (const r of [{ changed: true }, { changed: true, optimized: '' }, { changed: true, optimized: '   ' }]) {
      expect(optimizeOutcome(r).kind, JSON.stringify(r)).toBe('unchanged')
    }
  })

  it('an empty answer object does not read as a rewrite either', () => {
    expect(optimizeOutcome({}).kind).toBe('unchanged')
  })

  it('a thrown request is an ERROR, and keeps the reason the backend gave', () => {
    const out = optimizeFailure(new Error('no model is configured'))
    expect(out.kind).toBe('failed')
    expect(out.level, 'an unreachable optimizer IS bad news — and the only level that cues').toBe('error')
    expect(out.message).toMatch(/model is configured/)
    // The draft-is-safe half: the button's job is to REPLACE what the user typed, so "it failed"
    // on its own reads as "and your prompt is gone".
    expect(out.message).toMatch(/draft is unchanged/)
  })

  it('🪤 punctuates the borrowed clause instead of pasting a fragment after a full stop', () => {
    // Seen in the browser during validation: *"…your draft is unchanged. no model is
    // configured for the optimizer"*. Rejection messages are lowercase, unterminated
    // fragments; this is a sentence a user reads, so it is sentence-cased and ended.
    expect(optimizeFailure(new Error('no model is configured')).message)
      .toBe("Couldn't optimize this prompt — your draft is unchanged. No model is configured.")
    // A message that already ends in punctuation is not given a second full stop…
    expect(optimizeFailure(new Error('Load failed.')).message).toMatch(/Load failed\.$/)
    expect(optimizeFailure(new Error('HTTP 502')).message).toMatch(/HTTP 502\.$/)
    // …and only the FIRST letter is touched, so an identifier keeps its own casing.
    expect(optimizeFailure(new Error('model gpt-OSS not bound')).message).toMatch(/gpt-OSS/)
  })

  it('still says something useful when the rejection carries no message', () => {
    for (const e of [undefined, null, new Error('')]) {
      const out = optimizeFailure(e)
      expect(out.message, String(e)).toMatch(/draft is unchanged/)
      expect(out.message, String(e)).toMatch(/model configured/)
    }
  })

  it('never hands the same tone to "already good" and "could not run"', () => {
    const good = optimizeOutcome({ changed: false, optimized: 'x' })
    const bad = optimizeFailure(new Error('boom'))
    expect(good.kind !== 'rewritten' && good.level).not.toBe(bad.level)
  })
})

describe('the verdict actually reaches the user', () => {
  /** Push a notice through the REAL `notify` into a mounted host and hand back every node the
   *  sentence landed in. The host deliberately renders it TWICE — an `aria-hidden` visible card
   *  plus a live-region copy — so a single-node query here would be the wrong assertion. */
  async function shown(message: string, level: 'info' | 'success' | 'error', match: RegExp) {
    render(<Toaster />)
    await act(async () => { notify(message, level) })
    return await waitFor(() => {
      const found = screen.getAllByText(match)
      expect(found.length).toBeGreaterThan(0)
      return found
    })
  }

  it('announces the no-op in a live region rather than leaving the click silent', async () => {
    // The seam a green classifier cannot prove on its own: `notify` dispatches a `ne:toast`
    // CustomEvent, and only a mounted host turns that into something a person sees or hears.
    const out = optimizeOutcome({ changed: false, optimized: 'already good' })
    expect(out.kind).toBe('unchanged')
    if (out.kind === 'rewritten') throw new Error('unreachable')
    const nodes = await shown(out.message, out.level, /already reads well/)
    // Both channels, because the old behaviour was silent in both: something on screen…
    expect(nodes.some((n) => n.getAttribute('aria-hidden') === 'true'), 'a visible card').toBe(true)
    // …and something announced, or a screen-reader user gets the old nothing.
    expect(nodes.some((n) => n.closest('[aria-live]')), 'and an announcement').toBe(true)
  })

  it('announces a failure too', async () => {
    const f = optimizeFailure(new Error('optimizer unavailable'))
    // Sentence-cased by `optimizeFailure` — the borrowed clause is punctuated, not pasted.
    expect(f.message).toContain('Optimizer unavailable.')
    const nodes = await shown(f.message, f.level, /Optimizer unavailable/)
    expect(nodes.some((n) => n.closest('[aria-live]'))).toBe(true)
  })
})

// ── the call sites, pinned narrowly ─────────────────────────────────────────────────────────
describe('both optimize handlers route through the shared verdict', () => {
  const chat = read('pages/ChatPage.tsx')
  const loop = read('pages/loop/LoopComposer.tsx')

  it('neither handler still reads `changed` by hand', () => {
    // 🔑 THE DEFECT, as a literal: `if (r.changed && r.optimized)` with nothing after it. One
    // owner of that condition is what stops the next composer re-deriving it wrong.
    for (const [name, src] of [['ChatPage', chat], ['LoopComposer', loop]] as const) {
      expect(src, `${name} must not re-derive the verdict`).not.toMatch(/r\.changed\s*&&\s*r\.optimized/)
      expect(src, `${name} must import the shared classifier`).toMatch(/optimizeOutcome/)
    }
  })

  it('neither `catch` is empty any more', () => {
    for (const [name, src] of [['ChatPage', chat], ['LoopComposer', loop]] as const) {
      expect(src, `${name} swallowed the failure in a comment`).not.toMatch(/catch \{ \/\* keep the draft/)
      expect(src, `${name} must report it`).toMatch(/optimizeFailure/)
    }
  })

  it('the vacuity floor: the handlers this scans really are in these files', () => {
    // Without this, renaming `optimize()` or moving it would make every assertion above pass by
    // matching nothing — the failure mode an enumerated source rail is most prone to.
    for (const [name, src] of [['ChatPage', chat], ['LoopComposer', loop]] as const) {
      expect(src, `${name} still has an optimize handler to check`).toMatch(/async function optimize\(\)/)
      expect(src, `${name} still calls the endpoint`).toMatch(/api\.optimizePrompt\(/)
      expect(src, `${name} must notify on the non-rewrite path`).toMatch(/notify\(/)
    }
  })

  it('the shared module is the only place the verdict is spelled', () => {
    const mod = read('ui/composer/optimizeOutcome.ts')
    expect(mod).toMatch(/kind: 'unchanged'/)
    expect(mod).toMatch(/kind: 'failed'/)
    // The copy lives with the verdict, not at the call sites, so the two composers cannot drift
    // into two sentences for one outcome.
    for (const src of [chat, loop]) {
      expect(src, 'a call site must not hand-write the sentence').not.toMatch(/already reads well/)
    }
  })
})
