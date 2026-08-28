import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── The two consumers of /api/suggestions must not show different amounts of one list ────────────
//
// `#/dashboard`'s `Suggestions` widget rendered `items.slice(0, 5)` while `#/chat`'s
// `SuggestionChips` rendered `slice(0, 6)` — the same endpoint, two surfaces, different answers to
// "how many suggestions do I have". The backend settles it: `suggestions.py`'s parser caps the
// model's reply at `[:6]` and `_FALLBACK_SUGGESTIONS` is exactly six, so on a brand-new install
// (where the fallback is what you always get) the dashboard dropped the sixth on every render, with
// no affordance to reach it. Each one is generated per user from their own memory.
//
// 🪤 THIS ASSERTS THE AGREEMENT, NOT THE NUMBER. Pinning `6` in a test is how you end up with a
// third place to update when the producer changes — the same failure that left two stale hint counts
// in `ui/forms.tsx`. What is asserted is that the two consumer caps are EQUAL and that neither
// exceeds the Python parser's cap. Move the producer to eight and this rail asks for eight.
//
// 🪤 AND IT IS NOT ABOUT DISCLOSURE. `ui/cappedListDisclosed.test.tsx` already censused 29 rendered
// caps and deliberately left the dashboard widget previews alone, because they state no total and
// adding one is a per-surface copy decision. That ruling stands and this rail does not touch it: a
// silent cap is fine, a silent cap BELOW the producer's is discarded work.

// This file sits at `web/src/pages/dashboard/`, so the repo root is four levels up. Anchored on
// `import.meta.dirname` rather than `process.cwd()`, which differs between a root-level
// `npm run test:web` and a `cd web && vitest`.
const REPO = join(import.meta.dirname, '..', '..', '..', '..')

const read = (p: string) => readFileSync(join(REPO, p), 'utf8')

/** The `slice(0, N)` applied to the suggestions array in one file. */
function capIn(source: string, marker: string): number | null {
  const at = source.indexOf(marker)
  if (at < 0) return null
  const m = /\.slice\(0,\s*(\d+)\)/.exec(source.slice(at, at + 400))
  return m ? Number(m[1]) : null
}

describe('both suggestion surfaces show the same amount of the same list', () => {
  const widget = read('web/src/pages/dashboard/widgets/Suggestions.tsx')
  const chat = read('web/src/pages/ChatPage.tsx')
  const py = read('src/personalclaw/suggestions.py')

  const dashCap = capIn(widget, 'items.slice')
  const chatCap = capIn(chat, 'api.suggestions()')
  const parserCap = (() => {
    // 🪤 ANCHOR ON THE FUNCTION, NOT ON `][:N]`. My first draft scanned the whole file for
    // `\]\[:(\d+)\]` and matched `m["content"][:150]` inside `_build_context` — an unrelated
    // truncation 60 lines earlier — so the rail asserted the consumers were below 150 and reported a
    // defect that did not exist. An under-anchored regex does not fail loudly; it answers a
    // different question confidently.
    const at = py.indexOf('def _parse_suggestions')
    if (at < 0) return null
    const body = py.slice(at, py.indexOf('\ndef ', at + 1))
    const m = /\]\[:(\d+)\]/.exec(body)
    return m ? Number(m[1]) : null
  })()

  it('the three numbers were all found (vacuity floor)', () => {
    // Each of these is a regex against someone else's file. If any stops matching, every
    // comparison below passes on `null === null` and the rail reports clean while measuring
    // nothing — which is the failure mode this whole file exists to prevent elsewhere.
    expect(dashCap, "the dashboard widget's slice(0, N) was not found").not.toBeNull()
    expect(chatCap, "ChatPage's SuggestionChips slice(0, N) was not found").not.toBeNull()
    expect(parserCap, "_parse_suggestions' [:N] cap was not found in suggestions.py").not.toBeNull()
  })

  it('the dashboard and the chat hero agree', () => {
    expect(
      dashCap,
      `#/dashboard shows ${dashCap} suggestions and #/chat shows ${chatCap} from the same ` +
        `endpoint. One of the two surfaces is lying about how many you have.`,
    ).toBe(chatCap)
  })

  it('neither consumer caps below the producer, which would discard generated work', () => {
    for (const [name, cap] of [
      ['#/dashboard', dashCap],
      ['#/chat', chatCap],
    ] as const) {
      expect(
        cap,
        `${name} shows ${cap} of the up-to-${parserCap} suggestions the backend produces. ` +
          `Every one is generated per user from their own memory, so a consumer cap below the ` +
          `producer's throws that away with no way to reach it. Raise the consumer, or lower the ` +
          `producer in suggestions.py so nothing is generated that cannot be seen.`,
      ).toBeGreaterThanOrEqual(parserCap!)
    }
  })

  it("the shipped fallback list fits — it is what a brand-new install always sees", () => {
    // `_FALLBACK_SUGGESTIONS` is returned whenever there is no context to personalise from, so its
    // length is the one count guaranteed to occur in the wild.
    const block = py.slice(py.indexOf('_FALLBACK_SUGGESTIONS = ['))
    const list = block.slice(0, block.indexOf(']'))
    const n = (list.match(/^\s*"/gm) || []).length
    expect(n, 'the fallback list was not parsed — this assertion is vacuous').toBeGreaterThan(0)
    expect(
      dashCap,
      `the fallback list ships ${n} suggestions and #/dashboard renders ${dashCap}, so on every ` +
        `fresh install the last ${n - (dashCap ?? 0)} would be unreachable.`,
    ).toBeGreaterThanOrEqual(n)
  })
})
