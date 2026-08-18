import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── An adapter over useCachedData must not eat the error it was handed ───────────────────────────
//
// `useCachedData` returns `{ data, loading, error, refresh }`. A hook that wraps it and re-shapes the
// result is a fine pattern — but if the new shape omits `error`, every consumer downstream is
// *structurally* unable to tell a failed read from an empty one. The swallow moves out of the
// component, where a reviewer would see it, and into an adapter nobody re-reads.
//
// Census when this rail was written — 4 adapters, 3 of which dropped it:
//
//   lib/rungs.ts            useAutonomyLadder   ✅ returns `{ ladder, error, refresh }`  ← canonical
//   pages/agents/…          useAgentsData       🔴 `data ?? []`, error dropped
//   pages/apps/…            useAppConfig        🔴 error dropped → "Loading…" forever, and a save
//                                                  from the unloaded form REPLACED the app's config
//   app/usePlatform.ts      usePlatform         ⚪️ deliberate — see EXEMPT below
//
// The two 🔴 are fixed in this change; the ⚪️ is named here so the next pass does not "finish the job".

const SRC = join(process.cwd(), 'src')

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })
}

/** Every exported `use*` hook whose body calls `useCachedData`, with that body. */
function adapterHooks(): { rel: string; name: string; body: string }[] {
  const out: { rel: string; name: string; body: string }[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')
    if (!src.includes('useCachedData')) continue
    for (const m of src.matchAll(/export function (use[A-Z]\w*)\s*\([^)]*\)[^{]*\{/g)) {
      let i = m.index! + m[0].length
      let depth = 1
      while (i < src.length && depth > 0) {
        if (src[i] === '{') depth++
        else if (src[i] === '}') depth--
        i++
      }
      const body = src.slice(m.index! + m[0].length, i - 1)
      if (!/useCachedData(?:<[\s\S]*?>)?\(/.test(body)) continue
      out.push({ rel: abs.slice(SRC.length + 1), name: m[1], body })
    }
  }
  return out
}

/** Named exemptions: an adapter that deliberately collapses failure into its value. */
const EXEMPT: Record<string, string> = {
  // Returns a bare platform token and documents `''` as "loading OR unknown", so callers HIDE
  // OS-gated affordances (Finder reveal, screencapture) until it resolves. Fail-closed on purpose:
  // an error channel would make every caller decide what to do about an unknown OS, and hiding the
  // affordance is already the right answer. A distinction, not drift.
  'app/usePlatform.ts:usePlatform': 'fail-closed by design — "" hides OS-gated UI',
}

describe('an adapter hook re-exposes useCachedData\'s error', () => {
  const hooks = adapterHooks()

  it('finds the population — the scan is not vacuous', () => {
    // 🪤 A rail that matches nothing reads exactly like a clean family. Four adapters today.
    expect(hooks.length, 'exported use* hooks that wrap useCachedData').toBeGreaterThanOrEqual(4)
    expect(hooks.map((h) => h.name), 'the canonical one must be in the census').toContain('useAutonomyLadder')
  })

  // 🪤 `/\berror\b/` WAS NOT ENOUGH — a mutation that returned `error: undefined` kept the word and
  // sailed through. The test has to be that the error is READ from the hook and PASSED ON with a real
  // value; the two hard-coded blanks are the shapes that look compliant and are not.
  const carriesError = (body: string) =>
    /const \{[^}]*\berror\b[^}]*\} = useCachedData/.test(body)
    && /return \{[\s\S]*\berror\b/.test(body)
    && !/\berror:\s*(undefined|null)\b/.test(body)

  it('every adapter either returns the error or is named as an exemption', () => {
    const swallowing = hooks
      .filter((h) => !carriesError(h.body))
      .map((h) => `${h.rel}:${h.name}`)
      .filter((k) => !(k in EXEMPT))
      .sort()
    expect(swallowing, `these hide a failed read from every consumer:\n${swallowing.join('\n')}`).toEqual([])
  })

  it('the exemptions still exist and still look like their reason', () => {
    // An exemption pointing at a hook that has changed shape is worse than no exemption.
    for (const key of Object.keys(EXEMPT)) {
      const [rel, name] = key.split(':')
      const hook = hooks.find((h) => h.rel === rel && h.name === name)
      expect(hook, `${key} left the census — prune the exemption`).toBeTruthy()
      expect(carriesError(hook!.body), `${key} now handles the error; drop its exemption`).toBe(false)
    }
  })

  it('the canonical adapter is the shape the others copy', () => {
    const ladder = hooks.find((h) => h.name === 'useAutonomyLadder')!
    expect(ladder.body, 'reads the error from the hook').toMatch(/const \{[^}]*\berror\b[^}]*\} = useCachedData/)
    expect(ladder.body, 'and returns it').toMatch(/return \{[\s\S]*\berror\b/)
  })
})
