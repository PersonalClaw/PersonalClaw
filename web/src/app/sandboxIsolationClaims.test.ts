import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── #492, second clause: no frame may CLAIM isolation it does not have ────────────────
//
// `ChatEmbed` described its iframe as "a sandboxed iframe — full isolation, the app never
// touches the chat internals" directly above
// `sandbox: 'allow-scripts allow-same-origin allow-forms'`, with `src` built from
// `location.origin`. `allow-scripts` + `allow-same-origin` together is the documented
// no-op pair: the framed document can reach its parent and clear its own sandbox
// attribute. So the one frame in the tree that claimed full isolation was the one frame
// that had none — and the reader most likely to rely on that comment is the next person
// deciding whether app UI needs isolating.
//
// The attribute itself is CORRECT and stays: the chat route needs the host session, so
// `allow-same-origin` is required, and the deny-by-default residue (no top-level
// navigation, no popups, no downloads) is worth keeping. What was wrong was the sentence.
//
// This rails the PROPERTY rather than the sentence: a frame that opts out of origin
// isolation must not describe itself as isolated. Counted, because the count is the fact
// that makes the rule cheap — every other sandboxed frame in `web/src` is
// `sandbox="allow-scripts"` off a blob (null) origin, which IS origin-isolated. Exactly
// one frame is same-origin, and a second appearing silently is the regression to catch.

const ROOT = join(process.cwd(), 'src')

function sources(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) { out.push(...sources(p)); continue }
    if (!/\.tsx?$/.test(name) || /\.test\.tsx?$/.test(name)) continue
    out.push(p)
  }
  return out
}

/** Files declaring an iframe sandbox that keeps the frame in the PARENT's origin. */
function sameOriginFrames(): { file: string; text: string }[] {
  const hits: { file: string; text: string }[] = []
  for (const file of sources(ROOT)) {
    const text = readFileSync(file, 'utf8')
    for (const m of text.matchAll(/sandbox[:=]\s*['"]([^'"]*)['"]/g)) {
      const tokens = m[1].split(/\s+/)
      if (tokens.includes('allow-scripts') && tokens.includes('allow-same-origin')) {
        hits.push({ file: file.slice(ROOT.length + 1), text })
      }
    }
  }
  return hits
}

describe('a same-origin sandbox never claims isolation (#492)', () => {
  it('exactly one frame in web/src opts out of origin isolation', () => {
    const hits = sameOriginFrames()
    expect(hits.map((h) => h.file)).toEqual(['app/appSdk.tsx'])
  })

  it('that frame does not describe itself as isolated', () => {
    const [hit] = sameOriginFrames()
    // Scoped to the component, not the file: `appSdk.tsx` legitimately discusses the
    // widget frames elsewhere, which ARE isolated.
    const start = hit.text.indexOf('export function ChatEmbed')
    const doc = hit.text.slice(hit.text.lastIndexOf('/**', start), hit.text.indexOf('}', start))
    expect(doc).not.toMatch(/full isolation/i)
    // Deliberately NOT a blanket ban on the word: the first draft of this rail forbade
    // /isolat(ed|ion)/ outside a negation and went red on "NOT an isolation boundary" —
    // the marker matching its own negation. The positive assertions below are the real
    // rail, because they cannot be satisfied by a comment that overclaims.
    // And it says the true thing, so the next reader does not have to re-derive it.
    expect(doc).toMatch(/same-origin by construction/)
    expect(doc).toMatch(/NOT an isolation boundary/)
  })

  it('the isolated frames are still allowed to say so', () => {
    // The rail must not become "never write the word isolation". `WidgetFrame` runs
    // `sandbox="allow-scripts"` off a blob (null) origin — genuinely origin-isolated —
    // and its comment is accurate. If this ever fails, the rail above got too greedy.
    const widget = readFileSync(join(ROOT, 'ui/widget/WidgetFrame.tsx'), 'utf8')
    expect(widget).toMatch(/sandbox="allow-scripts"/)
    expect(widget).not.toMatch(/allow-same-origin/)
  })
})
