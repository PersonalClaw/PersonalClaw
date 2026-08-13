import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The loading state borrows a noun the surface has already declared ─────────────────────────────
//
// Cycles 143-144 gave every loading placeholder a voice. It said "Loading…" — accurate and anonymous,
// 57 times. The noun was already in the file: the app's canonical load-failure shape names the data,
// and it sits ONE OR TWO LINES from the skeleton it guards.
//
//     if (!info && loadErr) return <LoadError what="update status" error={loadErr} onRetry={refresh} />
//     if (!info) return <FormSkeleton sections={3} />          ← same data, no noun
//
// Measured across the tree: **22 of 57 skeletons sit within 40 lines of a `LoadError what="…"`**, and
// every single match is at −1 or −2 lines — the two branches of one gate. Those 22 pass the same noun,
// so a screen-reader user hears "Loading update status…" where the failure would have said "Couldn't
// load your update status".
//
// 🔑 THE SOURCE OF TRUTH WAS ALREADY IN THE FILE. No noun here is invented; each is copied from a
// sibling declaration. That is what makes this a rail and not a taste call.
//
// ── Cycle 148: THERE IS A SECOND DECLARATION, AND IT IS SPOKEN ALOUD TOO ──────────────────────────
//
// `ui/ListControls` takes `results={{ count, noun, active }}` and `ResultAnnouncement` says
// "39 triggers" / "No matching triggers" out of a live region. So a list page that declares its rows
// are "triggers" when it COUNTS them has already told us what to call them while they LOAD — same
// word, same user, same live region. Six list pages declare a results noun and gate a skeleton on the
// very state that noun counts; measured cold (fresh context, every `/api/**` held, 150ms poll for the
// first frame with a busy region):
//
//   surface        before        after
//   #/inbox        "Loading…"    "Loading items…"
//   #/triggers     "Loading…"    "Loading triggers…"
//   #/knowledge    "Loading…"    "Loading items…"
//   #/skills       "Loading…"    "Loading skills…"
//   #/prompts      "Loading…"    "Loading prompts…"
//   #/loops        no loading frame observed — rule-consistent, unverifiable (see below)
//
// 🪤 THE SCAN THAT DECLARED 35 SITES "UNNAMEABLE" COULD NOT SEE A DYNAMIC NOUN. `#/prompts` serves
// prompts and snippets from one page and declares `what={isSnips ? 'snippets' : 'prompts'}` on the
// LoadError ONE LINE above its skeleton — a textbook cycle-144 pair. It was invisible because the
// noun regex only matched `what="…"`. It matches `what={…}` now, and the site is named.
//
// 🪤 SAME NAME, DIFFERENT COMPONENT, DIFFERENT DATA. `KnowledgeListPage` counts `items` (the library)
// and `EntityDetail`, further down the same file, has its own `items` (what mentions this entity).
// A file-wide match would name both "items" and say the same word about different rows, so the rule is
// scoped to the component that declares the noun AND to a skeleton gated on the state it counts. That
// scoping is also what excludes the item-PEEK skeleton 37 lines up, which gates `peekItem`.
//
// 🪤 ONE SITE CANNOT BE OBSERVED, AND IT IS THE SHELL'S DOING. `#/loops` never shows a loading frame:
// `/api/loops` is requested at 428ms as a shell prefetch and resolves at 2933ms — the same frame the
// route content first appears (traced: rows=31 at 3177ms, busy=0 throughout). So `loops` is never
// `undefined` while `LoopsListPage` is mounted. The noun is applied because the rule holds, and
// recorded as unverified rather than claimed.
//
// 🔑 THE REMAINING UNNAMED SKELETONS STAY BARE. No `LoadError` neighbour and no results noun means the
// word would have to be invented, and a wrong noun ("Loading data…") is worse than an honest
// "Loading…". The settings panels are the next coherent subset — they declare `PanelHeader title=`,
// a third source — and belong to their own pass, not to this one.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const SKELETON = /<(?:List|Form|CardGrid)Skeleton\b[^>]*?\/>/
/** `what="literal"` or `what={expression}` — a dynamic noun is still a declared noun. */
const ERR_NOUN = /<(?:LoadError|InlineError)[^>]*?what=(?:"([^"]+)"|\{([^}]+?)\})/
const RESULTS = /results=\{\{([^}]*)\}\}/
/** An identifier used as a gate: `x === null`, `!x`, `x ?`, `x &&`. */
const GATE = /!?([A-Za-z_$][\w$]*)\s*(?:===|!==|&&|\?)/g

/** Top-level `function X` / `const X = …=>` blocks, so a match can be scoped to one component. */
function blocks(lines: string[]): { name: string; start: number; end: number }[] {
  const starts: { name: string; start: number }[] = []
  lines.forEach((l, i) => {
    const m = /^(?:export\s+)?(?:default\s+)?function\s+(\w+)/.exec(l)
      || /^(?:export\s+)?const\s+(\w+)\s*[:=].*=>/.exec(l)
    if (m) starts.push({ name: m[1], start: i })
  })
  return starts.map((s, k) => ({ ...s, end: k + 1 < starts.length ? starts[k + 1].start : lines.length }))
}
const ownerOf = (bs: ReturnType<typeof blocks>, i: number) => bs.find((b) => i >= b.start && i < b.end)?.name ?? '?'

/** The identifiers this skeleton is gated on: the ternary/guard conditions nearest above it. */
function gateIdents(lines: string[], i: number, before: string): string[] {
  const out = new Set<string>()
  const add = (text: string) => { for (const m of text.matchAll(GATE)) out.add(m[1]) }
  add(before)
  for (let d = 1; d <= 4 && out.size === 0; d++) {
    const l = lines[i - d]
    if (l !== undefined && /[?]|if \(/.test(l)) add(l)
  }
  return [...out]
}

type Site = {
  rel: string; line: number; tag: string; owner: string
  errNoun: string | null; errDist: number | null
  resultsNoun: string | null
}

/** Every skeleton in the tree, with whichever noun its own surface has already declared. */
function skeletons(): Site[] {
  const out: Site[] = []
  for (const abs of walk(SRC)) {
    if (abs.endsWith('ListScaffold.tsx')) continue
    const lines = readFileSync(abs, 'utf8').split('\n')
    const bs = blocks(lines)
    // Every `results={{ count: …, noun: … }}` in the file, with the component that declares it.
    const declared = lines.flatMap((l, i) => {
      const inner = RESULTS.exec(l)?.[1]
      if (!inner) return []
      const noun = /noun:\s*([^,]+?)\s*(?:,|$)/.exec(inner)?.[1]
      const count = /count:\s*(.+?),\s*noun:/.exec(inner)?.[1]
      return noun && count ? [{ owner: ownerOf(bs, i), noun: noun.trim(), count }] : []
    })
    lines.forEach((text, i) => {
      const tag = SKELETON.exec(text)?.[0]
      if (!tag) return
      const owner = ownerOf(bs, i)
      let errNoun: string | null = null
      let errDist: number | null = null
      for (let d = 0; d <= 40 && errNoun === null; d++) {
        for (const j of [i - d, i + d]) {
          const m = lines[j] !== undefined ? ERR_NOUN.exec(lines[j]) : null
          if (m) { errNoun = (m[1] ?? m[2]).trim(); errDist = j - i; break }
        }
      }
      const idents = gateIdents(lines, i, text.slice(0, text.indexOf(tag)))
      const hit = declared.find((d) => d.owner === owner && idents.some((id) => new RegExp(`\\b${id}\\b`).test(d.count)))
      out.push({ rel: abs.slice(SRC.length + 1), line: i + 1, tag, owner, errNoun, errDist, resultsNoun: hit?.noun ?? null })
    })
  }
  return out
}

/**
 * How the noun appears inside the skeleton tag. A declaration reaches us either already unquoted
 * (`what="items"` on a LoadError) or as JS source (`noun: 'items'`), so unquote before comparing; an
 * expression noun has to be passed as an expression.
 */
const carries = (tag: string, noun: string) => {
  const lit = /^(['"])(.*)\1$/.exec(noun)
  const forms = lit ? [`what="${lit[2]}"`] : [`what="${noun}"`, `what={${noun}}`]
  return forms.some((f) => tag.includes(f))
}

describe('a skeleton borrows a noun its own surface already declares', () => {
  const all = skeletons()
  const errPaired = all.filter((s) => s.errNoun)
  const resPaired = all.filter((s) => !s.errNoun && s.resultsNoun)

  it('finds the population — 57 skeletons, 22 beside a LoadError, 5+ beside a results noun', () => {
    expect(all.length, 'skeleton call sites outside the primitive').toBeGreaterThanOrEqual(57)
    expect(errPaired.length, 'skeletons with a LoadError noun in reach').toBeGreaterThanOrEqual(22)
    expect(resPaired.length, 'skeletons gated on the state a results noun counts').toBeGreaterThanOrEqual(5)
  })

  it('every skeleton beside a LoadError passes that sibling noun', () => {
    const wrong = errPaired.filter((s) => !carries(s.tag, s.errNoun!))
      .map((s) => `${s.rel}:${s.line} should say what for ${s.errNoun} — ${s.tag}`)
    expect(wrong, `these name the failure but not the wait:\n${wrong.join('\n')}`).toEqual([])
  })

  it('every skeleton gated on counted rows passes the results noun', () => {
    const wrong = resPaired.filter((s) => !carries(s.tag, s.resultsNoun!))
      .map((s) => `${s.rel}:${s.line} should say what for ${s.resultsNoun} — ${s.tag}`)
    expect(wrong, `these count the rows by name but load them anonymously:\n${wrong.join('\n')}`).toEqual([])
  })

  it('the dynamic-noun form is visible to the scan — the blind spot that hid #/prompts', () => {
    // Before cycle 148 the noun regex only matched `what="…"`, so a page whose rows change name with
    // its tab looked unnameable. If this stops matching, 1 site silently goes bare again.
    const dyn = all.find((s) => s.rel === 'pages/prompts/PromptsListPage.tsx')
    expect(dyn?.errNoun, 'the LoadError one line above declares the noun as an expression')
      .toBe("isSnips ? 'snippets' : 'prompts'")
    expect(dyn && carries(dyn.tag, dyn.errNoun!), 'and the skeleton passes the same expression').toBe(true)
  })

  it('the pairs really are the two branches of one gate', () => {
    // Every LoadError match measured at −1 or −2 lines. If a future match is 30 lines away it is
    // probably a DIFFERENT fetch, and copying its noun would be a lie — so the rule stays tight.
    const far = errPaired.filter((s) => Math.abs(s.errDist ?? 99) > 3).map((s) => `${s.rel}:${s.line} (${s.errDist})`)
    expect(far, `these matched a distant LoadError — check they describe the same data:\n${far.join('\n')}`).toEqual([])
  })

  it('a results noun only reaches a skeleton in its own component', () => {
    // 🪤 `KnowledgeListPage` counts `items`; `EntityDetail` in the same file has a different `items`.
    // Naming that one "items" would say the same word about different rows.
    const leaked = all.filter((s) => s.resultsNoun && s.rel === 'pages/knowledge/KnowledgeListPage.tsx'
      && s.owner !== 'KnowledgeListPage')
    expect(leaked.map((s) => `${s.line} in ${s.owner}`), 'a sub-view borrowed the list noun').toEqual([])
    // And the item-peek skeleton in the SAME component stays bare — it gates `peekItem`, not `items`.
    const peek = all.find((s) => s.rel === 'pages/knowledge/KnowledgeListPage.tsx' && s.owner === 'KnowledgeListPage'
      && !/what=/.test(s.tag))
    expect(peek, 'the peek-panel skeleton is gated on other state and stays bare').toBeTruthy()
  })

  it('the unnamed ones stay bare rather than guessing', () => {
    // Asserted from the other side: a `what` that matches neither declaration is an invented word, and
    // the ledger is where proposals belong until someone names them deliberately.
    const invented = all.filter((s) => !s.errNoun && !s.resultsNoun && /what=/.test(s.tag))
      .map((s) => `${s.rel}:${s.line} — ${s.tag}`)
    expect(invented, `these invent a noun with no declaration to source it from:\n${invented.join('\n')}`).toEqual([])
  })

  it('both canonical patterns are intact — a failure and a count each name the data', () => {
    // If either stops taking a noun, this whole rule loses a source of truth.
    expect(readFileSync(join(SRC, 'ui/ListScaffold.tsx'), 'utf8'), 'LoadError').toMatch(/what: string/)
    const controls = readFileSync(join(SRC, 'ui/ListControls.tsx'), 'utf8')
    expect(controls, 'ListControls results contract').toMatch(/results\?: \{ count: number; noun: string/)
    expect(controls, 'and it is spoken aloud').toMatch(/ResultAnnouncement/)
  })
})
