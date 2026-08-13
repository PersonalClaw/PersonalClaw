import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The loading state borrows the noun its own failure state already declares ────────────────────
//
// Cycles 143-144 gave every loading placeholder a voice. It said "Loading…" — accurate and anonymous,
// 57 times. The noun was already in the file: the app's canonical load-failure shape names the data,
// and it sits ONE OR TWO LINES from the skeleton it guards.
//
//     if (!info && loadErr) return <LoadError what="update status" error={loadErr} onRetry={refresh} />
//     if (!info) return <FormSkeleton sections={3} />          ← same data, no noun
//
// Measured across the tree: **22 of 57 skeletons sit within 40 lines of a `LoadError what="…"`**, and
// every single match is at −1 or −2 lines — the two branches of one gate. Those 22 now pass the same
// noun, so a screen-reader user hears "Loading update status…" where the failure would have said
// "Couldn't load your update status".
//
// 🔑 THE SOURCE OF TRUTH WAS ALREADY IN THE FILE. No noun in this change was invented; each was copied
// from the sibling branch. That is why it is a rail and not a taste call.
//
// 🔑 THE OTHER 35 ARE LEFT BARE ON PURPOSE. They have no `LoadError` neighbour, so their noun would have
// to be invented — and a wrong noun ("Loading data…") is worse than an honest "Loading…". They are
// recorded in the ledger with proposals for whoever names them, most obviously the route-level ones
// (tasks, triggers, loops, skills, agents, prompts, tags, conflicts, tools, models).

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const SKELETON = /<(?:List|Form|CardGrid)Skeleton\b[^>]*?\/>/
const NOUN = /<(?:LoadError|InlineError)[^>]*?what="([^"]+)"/

/** Every skeleton in the tree, with the nearest LoadError noun within 40 lines (if any). */
function skeletons() {
  const out: { rel: string; line: number; tag: string; noun: string | null; dist: number | null }[] = []
  for (const abs of walk(SRC)) {
    if (abs.endsWith('ListScaffold.tsx')) continue
    const lines = readFileSync(abs, 'utf8').split('\n')
    lines.forEach((text, i) => {
      const tag = SKELETON.exec(text)?.[0]
      if (!tag) return
      let noun: string | null = null
      let dist: number | null = null
      for (let d = 0; d <= 40 && noun === null; d++) {
        for (const j of [i - d, i + d]) {
          const m = lines[j] !== undefined ? NOUN.exec(lines[j]) : null
          if (m) { noun = m[1]; dist = j - i; break }
        }
      }
      out.push({ rel: abs.slice(SRC.length + 1), line: i + 1, tag, noun, dist })
    })
  }
  return out
}

describe('a skeleton next to a LoadError uses the same noun', () => {
  const all = skeletons()
  const paired = all.filter((s) => s.noun)

  it('finds the population — 57 skeletons, 22 of them paired', () => {
    expect(all.length, 'skeleton call sites outside the primitive').toBeGreaterThanOrEqual(57)
    expect(paired.length, 'skeletons with a LoadError noun in reach').toBeGreaterThanOrEqual(22)
  })

  it('every paired skeleton passes its sibling noun', () => {
    const wrong = paired.filter((s) => !s.tag.includes(`what="${s.noun}"`))
      .map((s) => `${s.rel}:${s.line} should say what="${s.noun}" — ${s.tag}`)
    expect(wrong, `these name the failure but not the wait:\n${wrong.join('\n')}`).toEqual([])
  })

  it('the pairs really are the two branches of one gate', () => {
    // Every match measured at −1 or −2 lines. If a future match is 30 lines away it is probably a
    // DIFFERENT fetch, and copying its noun would be a lie — so the rule stays tight.
    const far = paired.filter((s) => Math.abs(s.dist ?? 99) > 3).map((s) => `${s.rel}:${s.line} (${s.dist})`)
    expect(far, `these matched a distant LoadError — check they describe the same data:\n${far.join('\n')}`).toEqual([])
  })

  it('the unpaired ones stay bare rather than guessing', () => {
    // Asserted from the other side: a `what` that matches no sibling noun is an invented word, and the
    // ledger is where proposals belong until someone names them deliberately.
    const invented = all.filter((s) => !s.noun && /what="/.test(s.tag)).map((s) => `${s.rel}:${s.line}`)
    expect(invented, `these invent a noun with no LoadError to source it from:\n${invented.join('\n')}`).toEqual([])
  })

  it('the canonical pattern itself is intact — a failure names the data', () => {
    // If LoadError ever stops taking `what`, this whole rule loses its source of truth.
    expect(readFileSync(join(SRC, 'ui/ListScaffold.tsx'), 'utf8')).toMatch(/what: string/)
  })
})
