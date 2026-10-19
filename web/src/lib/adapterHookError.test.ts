// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import { filesUnder, readSource } from '../test/sourceTree'

// ── An adapter over useQuery must not eat the error it was handed ───────────────────────────
//
// `useQuery` returns `{ data, loading, error, refresh }`. A hook that wraps it and re-shapes the
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
  return filesUnder(dir, (n) => /\.tsx?$/.test(n) && !/\.(test|doc)\./.test(n))
}

/** Every exported `use*` hook whose body calls `useQuery`, with that body. */
function adapterHooks(): { rel: string; name: string; body: string }[] {
  const out: { rel: string; name: string; body: string }[] = []
  for (const abs of walk(SRC)) {
    const src = readSource(abs)
    if (!src.includes('useQuery')) continue
    for (const m of src.matchAll(/export function (use[A-Z]\w*)\s*\([^)]*\)[^{]*\{/g)) {
      let i = m.index! + m[0].length
      let depth = 1
      while (i < src.length && depth > 0) {
        if (src[i] === '{') depth++
        else if (src[i] === '}') depth--
        i++
      }
      const body = src.slice(m.index! + m[0].length, i - 1)
      if (!/useQuery(?:<[\s\S]*?>)?\(/.test(body)) continue
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
  // Not an adapter over its reads: the chat's stream (moved out of `ChatPage`) reads two settings
  // that tune how it folds frames (the reveal cadence, thinking shown inline) and returns neither.
  // An unanswered read is the shipped behaviour at the use site, the smooth reveal and thinking
  // hidden; `loadErrorState.test.tsx` budgets the pair as its own debt.
  'pages/chat/useChatStream.ts:useChatStream': 'reads stream settings it never returns — unanswered is the shipped default',
  // Moved out of `ChatPage` with the chat's header: the channels this chat can continue on. A failed
  // read offers no channel, on purpose and in its own comment, so the header's "Continue on …"
  // controls are simply absent; there is nowhere in the cluster that would say a list failed.
  'pages/chat/header/useChatHeader.ts:useChatHeader': 'a failed channel read offers no channel — the handoff controls are absent, by design',
}

describe('an adapter hook re-exposes useQuery\'s error', () => {
  const hooks = adapterHooks()

  it('finds the population — the scan is not vacuous', () => {
    // 🪤 A rail that matches nothing reads exactly like a clean family. Four adapters today.
    expect(hooks.length, 'exported use* hooks that wrap useQuery').toBeGreaterThanOrEqual(4)
    expect(hooks.map((h) => h.name), 'the canonical one must be in the census').toContain('useAutonomyLadder')
  })

  // 🪤 `/\berror\b/` WAS NOT ENOUGH — a mutation that returned `error: undefined` kept the word and
  // sailed through. The test has to be that the error is READ from the hook and PASSED ON with a real
  // value; the two hard-coded blanks are the shapes that look compliant and are not.
  //
  // A hook over several reads names each one's error (`error: sessionsError`), and carries it when it
  // returns every one of those names; one it reads and drops still fails here.
  const carriesError = (body: string) => {
    if (/\berror:\s*(undefined|null)\b/.test(body)) return false
    const ret = body.match(/return \{[\s\S]*/)?.[0] ?? ''
    const reads = [...body.matchAll(/const \{([^}]*)\} = useQuery/g)].map((m) => m[1])
    const errs = reads.map((d) => d.match(/\berror(?:\s*:\s*(\w+))?/)).filter((m) => m !== null)
    if (!errs.length) return false
    return errs.every((m) => new RegExp(`\\b${m![1] ?? 'error'}\\b`).test(ret))
  }

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
    expect(ladder.body, 'reads the error from the hook').toMatch(/const \{[^}]*\berror\b[^}]*\} = useQuery/)
    expect(ladder.body, 'and returns it').toMatch(/return \{[\s\S]*\berror\b/)
  })
})
