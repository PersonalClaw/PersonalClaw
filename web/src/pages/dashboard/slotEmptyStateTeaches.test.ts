import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── The dashboard's slot-empty states must not be bare facts ─────────────────
//
// MEASURED on a fresh install (empty `PERSONALCLAW_HOME`, past onboarding, `#/dashboard` at
// 1440×1000): **seven** slot-empty strips render at once, and six of them either name the
// mechanism that will fill them —
//
//   "No active work. Loops you launch appear here as they run."
//   "No pinned artifacts. Pin one from its page to keep it here."
//   "No models are loaded. One loads on its first use."
//   "No suggestions yet — they build from your activity."
//
// — or are a finished verdict ("All clear — nothing waiting on you."), or carry an on-ramp
// ("No tasks ready to work." + "+ New task"). ONE was a bare fact with neither: Schedule's
// "No recent scheduled runs." This is the app's first screen, so the slot that teaches nothing
// is the most expensive one to leave alone.
//
// This rail is a RATCHET, not a grader. It does not judge prose quality — it asserts a
// mechanical floor that a bare one-clause fact with no action cannot pass, so the NEXT slot
// added to the dashboard cannot quietly reintroduce the shape.
//
// 🔑 Read-error states are held to a DIFFERENT bar and excluded here on purpose. "Couldn't read
// what's loaded on this machine." is not failing to teach a mechanism — there is no mechanism to
// teach, the read failed. Conflating the two would have forced error copy to grow a fake
// on-ramp. They are counted and reported separately so the exclusion cannot silently swallow a
// real empty state.

const HERE = join(process.cwd(), 'src/pages/dashboard')

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) { out.push(...walk(abs)); continue }
    if (!name.endsWith('.tsx') || name.includes('.test.')) continue
    out.push(abs)
  }
  return out
}

type Slot = { file: string; text: string; hasAction: boolean; usesSlotAction: boolean }

/** Where the opening `<SlotEmptyState …>` ends.
 *
 *  🪤 NOT the first `>`, and not the first `>` that isn't `=>`. `action={<SlotAction …>New task
 *  </SlotAction>}` puts several `>` inside the attribute list, and the first draft of this rail
 *  stopped at one of them — so the on-ramp's LABEL leaked into the copy under test and
 *  `"New task } >No tasks ready to work."` read as two clauses. That is a false PASS for exactly
 *  the shape this rail exists to catch. The opening tag ends at the first `>` outside any `{}`. */
function openTagEnd(slice: string): number {
  let depth = 0
  for (let i = '<SlotEmptyState'.length; i < slice.length; i++) {
    const c = slice[i]
    if (c === '{') depth++
    else if (c === '}') depth--
    else if (c === '>' && depth === 0) return i + 1
  }
  return -1
}

/** Every `<SlotEmptyState …>…</SlotEmptyState>` element under `pages/dashboard`, with its rendered
 *  words and whether it passes an `action`. These elements are never nested, so a non-greedy slice
 *  to the closing tag is exact. */
function slots(): Slot[] {
  const found: Slot[] = []
  for (const abs of walk(HERE)) {
    const src = readFileSync(abs, 'utf8')
    const file = abs.slice(abs.lastIndexOf('/') + 1)
    for (const m of src.matchAll(/<SlotEmptyState[\s>][\s\S]*?<\/SlotEmptyState>/g)) {
      const slice = m[0]
      const end = openTagEnd(slice)
      expect(end, `could not find the end of the opening tag in ${file}`).toBeGreaterThan(0)
      const open = slice.slice(0, end)
      const text = slice
        .slice(end)
        .replace(/<\/SlotEmptyState>/, '')
        .replace(/\{[^{}]*\}/g, ' ')                      // any interpolation
        .replace(/<[^>]*>/g, ' ')                         // any nested markup
        .replace(/&rsquo;|&#39;/g, "'").replace(/&mdash;/g, '—').replace(/&amp;/g, '&')
        .replace(/\s+/g, ' ').trim()
      found.push({
        file,
        text,
        hasAction: /\baction=/.test(open),
        usesSlotAction: /<SlotAction[\s>]/.test(open),
      })
    }
  }
  return found
}

/** A read-error state, not an empty one. Its job is to say what failed, not to teach a mechanism. */
const isReadError = (t: string) => /^(Couldn't|Could not)\b/i.test(t) || /read error/i.test(t)

/** The floor: the copy carries a SECOND clause (a mechanism, a consequence, a verdict), or the slot
 *  offers a step. A single "No X." with nothing after it and no action is the shape this rail
 *  exists to keep out. */
const clauses = (t: string) => t.split(/[.—]/).map((s) => s.trim()).filter(Boolean)
const teaches = (s: Slot) => s.hasAction || clauses(s.text).length >= 2

describe('openTagEnd — the parse the tree cannot exercise on its own', () => {
  // Synthetic, because no shipped call site currently has a period inside its action label — and
  // that is precisely the case that would fake a pass. These pin the parse itself.
  const body = (s: string) => s.slice(openTagEnd(s)).replace(/<\/SlotEmptyState>/, '').trim()

  it('stops at the tag-closing > even when the attributes contain > and =>', () => {
    const s = `<SlotEmptyState icon={X} action={<SlotAction icon={Plus} onClick={() => go('a')}>New thing</SlotAction>}>No things.</SlotEmptyState>`
    expect(body(s)).toBe('No things.')
    expect(clauses(body(s))).toHaveLength(1)
  })

  it('a period inside the action label cannot manufacture a second clause', () => {
    const s = `<SlotEmptyState icon={X} action={<SlotAction icon={Plus} onClick={go}>Add. Now</SlotAction>}>No things.</SlotEmptyState>`
    expect(clauses(body(s))).toHaveLength(1)
  })

  it('the plain form is unaffected', () => {
    const s = `<SlotEmptyState icon={X}>No things. They appear here as they run.</SlotEmptyState>`
    expect(clauses(body(s))).toHaveLength(2)
  })
})

describe('dashboard slot-empty states teach a mechanism or offer a step', () => {
  const all = slots()
  const errors = all.filter((s) => isReadError(s.text))
  const empties = all.filter((s) => !isReadError(s.text))

  it('the scan actually found the family (vacuity floor)', () => {
    // 13 element sites measured at the time of writing (7 render at once — the branches are
    // mutually exclusive). A floor rather than an equality so a sibling PR adding a slot does not
    // red this; the ratchet below is what holds the line.
    expect(all.length).toBeGreaterThanOrEqual(13)
    expect(empties.length).toBeGreaterThanOrEqual(10)
    expect(errors.length).toBeGreaterThanOrEqual(3)
    // Every slot must be classifiable, or the split above is hiding one.
    expect(all.every((s) => s.text.length > 0)).toBe(true)
  })

  it('no empty state is a bare fact', () => {
    const failing = empties.filter((s) => !teaches(s))
    // Written as a ceiling of one, for Discover's "Discover tips are off." — a fact with no way to
    // turn the feature back on, being fixed on its own branch at the time. That branch landed
    // (51b9905, "stop the Discover slot claiming a setting it never read") and gave the off-branch
    // an on-ramp, so the exemption is spent and the ceiling closes to zero. Left at one it would be
    // a budget nothing spends: an `every(file === 'Discover.tsx')` over an empty list is vacuous,
    // and a free slot for the next bare fact is exactly what this rail exists to deny.
    expect(
      failing.map((s) => `${s.file}: "${s.text}"`),
      'a dashboard slot-empty state states a bare fact with no mechanism and no on-ramp',
    ).toHaveLength(0)
  })

  it('the Schedule slot names its mechanism AND offers the on-ramp', () => {
    const s = slots().find((x) => x.file === 'ScheduleWidget.tsx' && /No recent scheduled runs/.test(x.text))
    expect(s, 'ScheduleWidget no longer has a "no recent scheduled runs" empty state').toBeTruthy()
    // The mechanism sentence, in the same shape as ActiveWork's, using the product's own verb for a
    // trigger run ("fire" — cf. WeekGridView's "No fires this week").
    expect(s!.text).toMatch(/appear here as they fire/)
    expect(clauses(s!.text).length).toBeGreaterThanOrEqual(2)
    expect(s!.hasAction).toBe(true)
    expect(s!.usesSlotAction).toBe(true)
  })

  it('the slot on-ramp has exactly one definition — no second copy', () => {
    // Two differently-inked on-ramps side by side on the first screen is the drift this prevents.
    // `SlotAction` owns the markup; a call site passing `action` must go through it.
    //
    // 🪤 A SECOND COPY DOES NOT HAVE TO BE HAND-ROLLED. Discover's off-branch reached the third
    // action position with `<Button variant="ghost" size="xs" className="text-on-surface-var">` —
    // a shared primitive, so no lint or reviewer would call it duplication, yet it painted a
    // different ink at a different radius in the same slot position Tasks and Schedule occupy, and
    // all three can render on `#/dashboard` at once. It also stacked two colour utilities on one
    // element (`ghost` already sets `text-on-surface`), the order-dependent ink `ui/Button`'s
    // `ghost-accent` note documents. So the assertion is on the POSITION, not on the markup's
    // origin: whatever sits in a slot's `action` is a slot action and takes the slot's ink.
    const withAction = slots().filter((s) => s.hasAction)
    expect(withAction.length).toBeGreaterThanOrEqual(3)   // Tasks + Schedule + Discover; vacuity floor
    for (const s of withAction) {
      expect(s.usesSlotAction, `${s.file} does not route its slot on-ramp through SlotAction`).toBe(true)
    }
    // And the definition itself lives in the kit, once.
    const kit = readFileSync(join(HERE, 'widgets/kit.tsx'), 'utf8')
    expect(kit.match(/export function SlotAction\b/g) ?? []).toHaveLength(1)
  })
})
