import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── "`aria-busy` already announces it" — an EXEMPTION USED BY THREE RAILS, AND IT IS FALSE ────────
//
// Three rails exempt an entire census class from having to explain itself, on the stated grounds
// that the state is already announced:
//
//   `ui/disabledReasonTriage.test.ts`   "…whose state `aria-busy` already announces. So a rail that
//                                        demanded a reason everywhere would have broken 87 correct
//                                        sites."
//   `ui/rawSoftOffContract.test.ts`     "busy — an in-flight action must not be re-clickable, and
//                                        `aria-busy` already says so."
//   `ui/unavailable.test.tsx`           "its spinner + `aria-busy` already carry that state."
//
// **None of the three asserts it, and it is not true.** `Button` publishes `aria-busy` from ONE
// prop:
//
//     aria-busy={loading || undefined}          // Button.tsx
//
// `loading`, never `disabled`. So `<Button disabled={busy}>` — the exact shape being exempted —
// emits no `aria-busy` at all. The claim is not a small overstatement. Measured:
//
//   235  <Button disabled={…}>  in the tree
//   185  of them busy-gated  ← the class all three rails exempt on announcement grounds
//     6  actually announce it (they pass `loading=`)   ← the claim is true HERE, and only here
//   179  announce nothing at all                       ← 28 + 24 + 17 + 110, the classes below
//
// **CLASS A (28) IS CLOSED IN THIS CHANGE, taking the population to 151.** Those were the sites
// whose disabled gate WAS their hand-rolled spinner's own condition, so `loading={…}` is a true
// drop-in. Converting them deleted 28 re-implementations of a prop the kit already ships, removed
// six now-dead `Loader2` imports outright, and — because `loading` preserves the button's width —
// removed a layout jump the hand-rolled version caused every time one of them fired.
//
// 🔑 AND IT SURFACED A GAP IN THE PRIMITIVE, which is why `Button` gained `loadingLabel` here.
// Class A was not homogeneous: **8 of the 28 named the operation in their busy arm** — "Dreaming…",
// "Consolidating…", "Rendering…", "Syncing…", "Building…", "Linking…", "Running…", "Loading" — and
// bare `loading` FADES THE WHOLE LABEL, so a mechanical conversion would have deleted those words.
// Seven of the eight name a multi-second operation sitting in a row where several could run, so the
// verb is the only thing separating "still working" from "stuck". The workaround they had written
// cost them `aria-busy` (published from `loading`, which they never set), so a screen-reader user
// got neither the announcement nor the word. `loadingLabel` gives both.
//
// 🪤 EVERY VERB IS NO LONGER THAN THE ACTION LABEL IT REPLACES — checked pair by pair, because the
// loading overlay is `absolute inset-0` and therefore sized by the ORIGINAL label. "Consolidate now"
// (15) → "Consolidating…" (14), "Load older events" (17) → "Loading" (7), "Build / refresh" (15) →
// "Building…" (9). Had one been longer it would have truncated instead of widening the pill, which
// is why the overlay carries `min-w-0 truncate` as a floor rather than as the plan.
//
// 🔑 THE REPO ALREADY RETRACTED THIS ONCE AND DID NOT PROPAGATE IT. `gatedIconButtonSaysWhy` says
// of its own census row: *"⚠️ THE LAST ROW OF THAT CENSUS WAS WRONG… those gates did NOT 'already
// read as busy'… no `aria-busy`."* That correction stopped at the icon tiers. The three rails above
// kept using the claim as an exemption criterion, which is how a retracted fact goes on certifying
// ~180 sites as correct.
//
// ── WHY THIS IS A MEASURED CEILING AND NOT A SWEEP ───────────────────────────────────────────────
//
// The obvious remedy — copy each `disabled=` gate into an `aria-busy=` — is WRONG, and the reasons
// are worth writing down because they are what makes this a per-site change rather than a codemod:
//
//   🪤 THE GATE IS A DISJUNCTION. `disabled={!dirty || busy}` is unavailable for two unrelated
//      reasons and only one of them is "working". Copying the gate announces busy whenever a form
//      is merely invalid — a new lie in place of a missing truth.
//
//   🪤 MOST OF THE POPULATION IS A BYSTANDER. `busy` is very often a STRING naming WHICH action is
//      in flight, with every sibling button gated on `!!busy`. `InboxDetail` has four such rows: at
//      most ONE is working and the other three are blocked BY it. Marking them all `aria-busy`
//      announces four concurrent operations.
//
//      🔴 CORRECTION TO THAT PARAGRAPH, from actually doing the work: **a bystander gate is not
//      automatically unfixable.** It is unfixable only when nothing at the site distinguishes the
//      working button from the blocked ones. Where the children ALREADY hand-roll a spinner on a
//      NARROWER condition — `disabled={!!busy}` with `{busy === 'accept' ? <Loader2/> : …}` — the site
//      has made the distinction and merely had no way to publish it. Those take
//      `loading={busy === 'accept'} disabled={!!busy}` as TWO props: `off = !!disabled || loading`
//      keeps every sibling disabled, while `aria-busy` becomes true for exactly the one working.
//      **24 sites closed that way, including the `InboxDetail` rows cited just above as the
//      counterexample.** What remains in Class D is the genuinely undecidable case: a shared flag
//      with no spinner, where inventing an identity would be guessing. For those the honest fix is
//      nothing, or a reason ("wait for the current action") — the `disabledReason` family.
//
//   🪤 AND THE SPINNER CONDITION IS NOT ALWAYS THE GATE. `PortabilityPanel` pairs
//      `disabled={busy !== null}` with a `busy === spec.key ? <Loader2/> : <Icon/>` spinner — the
//      dimming is shared, the spinner is per-row. `loading={busy !== null}` there would spin every
//      row at once, which is a worse bug than the missing attribute and invisible to a grep that
//      only asks "is there a spinner?".
//
// So this rail does what `inertUtilities.allowlist.json` does for its own family — *"They are BUGS,
// not exemptions"*: it measures the population, records the classes, and RATCHETS DOWNWARD. A new
// busy-gated button that announces nothing makes the number go up and reds this. Fixing one makes
// it go down, and the ceiling is lowered in that PR.
//
// 🔑 CLASS A IS THE DROP-IN, AND IT IS NOT AN A11Y-ONLY CHANGE. Where the gate IS the spinner's own
// condition, `loading={…}` replaces a hand-rolled `cond ? <Loader2 className="animate-spin"/> :
// <Icon/>` ternary with the primitive's own in-flight state — same disabled behaviour (`off =
// disabled || loading`), plus `aria-busy`, plus the centered spinner and preserved width. That is
// the abstraction this kit already owns, re-implemented by hand ~28 times.
//
// 🔑 AND CLASS C IS EASIER THAN IT LOOKS, because `loading` and `disabled` are INDEPENDENT props.
// `<Button loading={busy === 'send'} disabled={busy === 'send' || !draft.trim()}>` is correct and
// needs no new API: `loading` carries the narrow in-flight truth, `disabled` keeps the wider gate,
// and `softOff = off && reason && !loading` still resolves to native-disabled while working.

const SRC = join(process.cwd(), 'src')

/** 🪤 Comments BLANKED IN PLACE, never deleted — the first draft of this census stripped them and
 *  every reported line number was off. This file's own prose also quotes the shapes it scans for. */
const code = (abs: string): string =>
  readFileSync(abs, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^(\s*)\/\/.*$/gm, '$1')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

/** In-flight vocabulary, unioned from the two rails that already discovered it by measurement. */
const BUSY = /\b(busy|saving|sending|loading|installing|retrying|pending|working|submitting|launching|testing|promoting|consolidating|regen\w*|bulkBusy|levelBusy|deleting|creating|running|uploading|importing|exporting|refreshing|syncing|starting|stopping|genning|repairing|reloading|applying|generating|fetching|polling|checking)\b/i

/** A bare shared flag — the bystander tell. */
const BARE = /^\s*!*\s*(busy|bulkBusy|levelBusy|pending|loading|saving|working)\s*$/

/** Complete `<Button …>` opening tag plus its children, by brace depth. A `[^>]*>` matcher stops
 *  at the `>` inside `onClick={() => f()}` and reports every tag as prop-less. */
function elements(src: string): Array<{ tag: string; body: string; line: number }> {
  const out: Array<{ tag: string; body: string; line: number }> = []
  for (const m of src.matchAll(/<Button\b/g)) {
    let depth = 0
    let tagEnd = -1
    let selfClose = false
    for (let i = m.index! + m[0].length; i < src.length; i++) {
      const ch = src[i]
      if (ch === '{') depth++
      else if (ch === '}') depth--
      else if (ch === '>' && depth === 0) { tagEnd = i; selfClose = src[i - 1] === '/'; break }
    }
    if (tagEnd < 0) continue
    const close = selfClose ? -1 : src.indexOf('</Button>', tagEnd)
    out.push({
      tag: src.slice(m.index!, tagEnd + 1),
      body: close < 0 ? '' : src.slice(tagEnd + 1, close),
      line: src.slice(0, m.index!).split('\n').length,
    })
  }
  return out
}

/** The balanced contents of `disabled={…}` — and NOT of `aria-disabled={…}`: `\bdisabled=` matches
 *  inside `aria-disabled=` because the word boundary sits after the hyphen. `rawSoftOffContract`
 *  hit this exact trap and counted every button it had just converted as an offender. */
function gateOf(tag: string): string | null {
  const i = tag.search(/(?<!aria-)\bdisabled=\{/)
  if (i < 0) return null
  const start = tag.indexOf('{', i)
  let d = 0
  for (let j = start; j < tag.length; j++) {
    if (tag[j] === '{') d++
    else if (tag[j] === '}') { d--; if (d === 0) return tag.slice(start + 1, j) }
  }
  return null
}

/** Split on TOP-LEVEL `||` only — `disabled={a || (b || c)}` is two disjuncts, not three. */
function disjuncts(g: string): string[] {
  const out: string[] = []
  let d = 0
  let last = 0
  for (let i = 0; i < g.length; i++) {
    const ch = g[i]
    if ('([{'.includes(ch)) d++
    else if (')]}'.includes(ch)) d--
    else if (d === 0 && ch === '|' && g[i + 1] === '|') { out.push(g.slice(last, i)); i++; last = i + 1 }
  }
  out.push(g.slice(last))
  return out.map((s) => s.trim()).filter(Boolean)
}

/** The condition guarding a hand-rolled spinner in the children: `COND ? <Loader2…/> : …`. */
function spinnerCond(body: string): string | null {
  const i = body.search(/Loader2/)
  if (i < 0) return null
  const before = body.slice(0, i)
  const q = before.lastIndexOf('?')
  if (q < 0) return null
  let d = 0
  let start = 0
  for (let j = q - 1; j >= 0; j--) {
    const ch = before[j]
    if (')]}'.includes(ch)) d++
    else if ('([{'.includes(ch)) { if (d === 0) { start = j + 1; break } d-- }
  }
  return before.slice(start, q).trim()
}

const norm = (s: string) => s.replace(/\s+/g, ' ').trim()

type Site = { at: string; gate: string; spinner: string | null }
const census = () => {
  const announced: Site[] = []
  const A: Site[] = []
  const B: Site[] = []
  const C: Site[] = []
  const D: Site[] = []
  let population = 0
  for (const abs of walk(SRC)) {
    const rel = abs.slice(SRC.length + 1)
    const src = code(abs)
    for (const { tag, body, line } of elements(src)) {
      const gate = gateOf(tag)
      if (gate === null) continue
      population++
      if (!BUSY.test(gate)) continue
      const site: Site = { at: `${rel}:${line}`, gate: norm(gate), spinner: spinnerCond(body) }
      // `loading=` IS the announcement — it is the prop `aria-busy` is published from.
      if (/aria-busy|\bloading=/.test(tag)) { announced.push(site); continue }
      const ds = disjuncts(gate)
      const busyDs = ds.filter((d) => BUSY.test(d))
      // 🪤 ORDER MATTERS, AND GATE-EQUALS-SPINNER OUTRANKS BARENESS. A first draft tested `BARE`
      // first and put `disabled={busy}` in the bystander class even where the children render
      // `busy ? <Loader2/> : <Icon/>` — which measured Class A at 15 instead of 28. A bare flag is
      // only *ambiguous*: `busy` may be a boolean owned by this button or a string any sibling
      // sets, and the tag alone cannot say which. The site's own spinner settles it. An author who
      // renders a spinner on exactly the gate condition is asserting that this gate means "THIS
      // button is working" — so trust that over the shape of the identifier.
      if (ds.length === 1 && site.spinner && norm(site.spinner) === norm(gate)) A.push(site)
      else if (ds.length > 1 && busyDs.length === 1 && BARE.test(busyDs[0])) D.push(site)
      else if (ds.length > 1) C.push(site)
      else if (BARE.test(gate)) D.push(site)
      else B.push(site)
    }
  }
  return { population, announced, A, B, C, D }
}

describe('the `aria-busy` exemption is measured, not asserted by comment', () => {
  it('🔑 THE PREMISE: Button publishes aria-busy from `loading`, NOT from `disabled`', () => {
    // Everything in this file rests on this one line. If a future change makes `Button` derive
    // `aria-busy` from its disabled gate, the whole family closes at once and this rail is what
    // should red to say so.
    const btn = code(join(SRC, 'ui', 'Button.tsx'))
    expect(btn, 'aria-busy comes from loading').toMatch(/aria-busy=\{loading \|\| undefined\}/)
    expect(btn, 'and NOT from the disabled gate').not.toMatch(/aria-busy=\{[^}]*\bdisabled\b/)
    // The independence that makes Class C fixable with no new API.
    expect(btn, '`loading` and `disabled` are independent inputs to one off-state')
      .toMatch(/const off = !!disabled \|\| loading/)
  })

  it('🔴 the two rails no longer claim the exemption they never checked', () => {
    // The retraction, propagated. Neither may state that a busy-gated `disabled` is already
    // announced, because it is not — and a comment is what a later reader trusts.
    const triage = readFileSync(join(SRC, 'ui', 'disabledReasonTriage.test.ts'), 'utf8')
    expect(triage, 'the false exemption criterion is gone').not.toMatch(/aria-busy` already announces/)
    expect(triage, 'and it says what is actually true instead').toMatch(/announces nothing|no `aria-busy`|NOT announced/)
    const raw = readFileSync(join(SRC, 'ui', 'rawSoftOffContract.test.ts'), 'utf8')
    expect(raw, 'the false exemption criterion is gone').not.toMatch(/`aria-busy` already says so/)
    expect(raw, 'and it says what is actually true instead').toMatch(/announces nothing|no `aria-busy`|NOT announced/)
  })

  it('finds the population it is filtering (not vacuously green)', () => {
    const { population, announced, A, B, C, D } = census()
    // 🪤 AN ANTI-VACUITY FLOOR, NOT A CENSUS — and it must be generous, because THIS FAMILY'S FIXES
    // SHRINK IT. A converted site loses its `disabled=` entirely (it becomes `loading=`), so every
    // fix removes a member: 235 at first measurement, 196 after this change. A floor set near the
    // measured value would red on the next correct fix, which is the opposite of what it is for.
    expect(population, 'the matcher must find the disabled Buttons').toBeGreaterThanOrEqual(150)
    const busyGated = announced.length + A.length + B.length + C.length + D.length
    // Same reasoning as the floor above: the busy-gated subset shrinks with every fix (185 → 145),
    // so this is a "the scan still resolves the subset" floor and nothing more.
    expect(busyGated, 'and the busy-gated subset the exemption covers').toBeGreaterThanOrEqual(100)
  })

  it('🔴 THE RATCHET: the number of busy-gated Buttons announcing nothing may only go DOWN', () => {
    // 179 as first measured; **114 after this change closes Class A, the self-spun bystanders, and the
    // identity-gated half of Class B** — the 28 converted sites drop
    // out of the population entirely, because `disabled={busy}` is GONE from them rather than
    // supplemented. A CEILING, not a floor: each future fix lowers it (lower it in that PR), and a
    // NEW `<Button disabled={busy}>` with no `loading=` raises it and reds this.
    //
    // 🪤 A `>=` FLOOR HERE WOULD BE EXACTLY BACKWARDS, and this repo has the scar: `railFloors`
    // records that a floor detects a REMOVAL but never an ADDITION, and addition is the direction
    // that actually happens. The defect grows by someone writing one more ordinary button.
    const { A, B, C, D } = census()
    const unannounced = [...A, ...B, ...C, ...D]
    expect(
      unannounced.length,
      'a busy-gated Button that announces nothing to assistive tech:\n  ' +
        unannounced.slice(0, 12).map((s) => `${s.at}  disabled={${s.gate}}`).join('\n  ') +
        `\n  …and ${Math.max(0, unannounced.length - 12)} more`,
    ).toBeLessThanOrEqual(114)
  })

  it('records the classes, because they want OPPOSITE fixes', () => {
    // The value of the split is that a single sweep would be wrong three different ways. Each
    // class is a floor on its own kind, so a reclassification that quietly empties one shows up.
    const { A, B, C, D } = census()
    // 🔑 CLASS A IS NOW EMPTY, AND THAT IS AN ASSERTION RATHER THAN A FLOOR — every site whose gate
    // was its own spinner's condition has adopted `loading=`. A NEW one is a regression toward the
    // hand-rolled pattern, so this is `toBe(0)`, not `>=`.
    expect(A, 'a hand-rolled spinner whose condition IS the disabled gate — use `loading=` instead')
      .toEqual([])
    expect(C.length, 'Class C — mixed gate; `loading` takes the busy disjunct, `disabled` keeps the gate')
      .toBeGreaterThanOrEqual(5)
    expect(D.length, 'Class D — bystander shared flag with NO spinner to derive identity from')
      .toBeGreaterThanOrEqual(95)
    // Class D is the majority, which is the single most important thing this census establishes:
    // the family is NOT ~180 missing attributes, it is ~50 real ones plus a large class whose
    // honest answer is something else entirely.
    expect(D.length, 'the bystanders outnumber the genuine cases — a sweep would have lied at most sites')
      .toBeGreaterThan(A.length + B.length + C.length)
  })

  it('🪤 the spinner condition is NOT always the gate — the trap a codemod would fall into', () => {
    // Named sites, so the trap survives as an example rather than as prose. Each pairs a SHARED
    // dimming gate with a PER-ROW spinner; `loading={gate}` would spin every row at once.
    const { A, B, C, D } = census()
    // 🔻 WAS A FLOOR OF 15, NOW AN EXACT RECORD, because the class was 24 and is now 1. The 24 all
    // took the same two-prop fix — `loading={<spinner condition>} disabled={<original gate>}` — and
    // the spinner condition is precisely the identity the bystander gate lacked. What is left is the
    // INVERTED shape (`ScheduleDetail`, spinner in the non-busy arm), which the converter refused on
    // purpose rather than guessing at. A floor here would now certify nothing; an exact record reds
    // when a NEW mismatched site appears, which is the direction that happens.
    const mismatched = [...A, ...B, ...C, ...D]
      .filter((s) => s.spinner && norm(s.spinner) !== norm(s.gate))
      .map((s) => s.at.split(':')[0])
    expect([...new Set(mismatched)], 'a new shared-gate/narrow-spinner site needs the two-prop fix')
      .toEqual(['pages/schedule/ScheduleDetail.tsx'])
  })

  it('the sites that DO announce it keep doing so', () => {
    // The counterpart ratchet. These are the proof the fix works at all, so a regression that
    // dropped `loading=` back to `disabled=` must red here rather than silently rejoining Class A.
    const { announced } = census()
    expect(announced.length, 'buttons that publish their in-flight state').toBeGreaterThanOrEqual(6)
  })
})

// ── `loadingLabel`: the mode the primitive was missing ────────────────────────────────────────────
describe('a slow action can name what it is doing AND announce it', () => {
  /** The 8 sites that had hand-rolled a labelled busy state, with the word each must keep. */
  const VERBS: Array<[string, string]> = [
    ['pages/settings/MemoryPanel.tsx', 'Dreaming…'],
    ['pages/settings/MemoryPanel.tsx', 'Linking…'],
    ['pages/settings/MemoryPanel.tsx', 'Rendering…'],
    ['pages/settings/MemoryPanel.tsx', 'Consolidating…'],
    ['pages/settings/MemoryPanel.tsx', 'Building…'],
    ['pages/settings/MemoryPanel.tsx', 'Syncing…'],
    ['pages/settings/AuditPanel.tsx', 'Loading'],
    ['pages/tools/ToolInspector.tsx', 'Running…'],
  ]

  it('Button accepts a loading label and renders it beside the spinner', () => {
    const btn = code(join(SRC, 'ui', 'Button.tsx'))
    expect(btn, 'the prop exists').toMatch(/loadingLabel\?: string/)
    expect(btn, 'and is destructured, not just declared').toMatch(/loading = false, loadingLabel,/)
    expect(btn, 'rendered only when given, so every existing caller is unaffected')
      .toMatch(/\{loadingLabel \? \(/)
    expect(btn, 'a long verb truncates rather than overflowing the pill it is positioned over')
      .toMatch(/min-w-0 items-center gap-s/)
  })

  it('🔴 a labelled busy state is NOT dimmed to 40% — the word has to be readable', () => {
    // Caught by looking at the rendered component, not by any assertion: `loading` disables the
    // button, so `disabled:opacity-40` fired and "Consolidating…" rendered at 40% opacity. The suite
    // was fully green while the prop was half-defeated. Measured after the fix, on the real
    // component in both themes: labelled loading = opacity 1, bare loading = 0.4 (unchanged).
    //
    // 🪤 The dim is OMITTED for that case, never overridden — two opacity utilities on one element
    // are resolved by Tailwind's stylesheet order, not source order, which is the collision this
    // component's own `ghost-accent` comment records.
    const btn = code(join(SRC, 'ui', 'Button.tsx'))
    expect(btn, 'the labelled case drops the dim and keeps the click refusal')
      .toMatch(/loading && loadingLabel \? 'disabled:pointer-events-none'/)
    expect(btn, 'and the bare case still dims exactly as before')
      .toMatch(/: 'disabled:opacity-40 disabled:pointer-events-none'/)
    expect(btn, 'no competing opacity utility was added instead').not.toMatch(/disabled:opacity-100/)
  })

  it('🔴 no Button renders a JS expression as literal TEXT — tsc cannot see this', () => {
    // Introduced twice by my own conversion and invisible to the compiler, because bare JSX text is
    // valid TSX. Converting `{cond ? <Loader2/> : REST}` to `loading={cond}` has to keep REST in
    // braces unless it is a string literal or a JSX element:
    //
    //     {busy ? <Loader2/> : null} Save        ->  >null Save          renders the WORD "null"
    //     {busy === c.id ? … : CHOICE_LABELS.x}  ->  >CHOICE_LABELS.x    renders the SOURCE
    //
    // Three sites shipped the first shape and one the second before this rail existed. A green tsc
    // and a green suite both said nothing; only reading the rendered text does.
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      const src = code(abs)
      for (const m of src.matchAll(/<Button\b/g)) {
        let d = 0
        let te = -1
        for (let i = m.index! + m[0].length; i < src.length; i++) {
          const ch = src[i]
          if (ch === '{') d++
          else if (ch === '}') d--
          else if (ch === '>' && d === 0) { te = i + 1; break }
        }
        if (te < 0 || src[te - 2] === '/') continue
        const close = src.indexOf('</Button>', te)
        if (close < 0) continue
        // Strip braced expressions and nested elements; whatever remains is rendered verbatim.
        const text = src.slice(te, close)
          .replace(/\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}/g, ' ')
          .replace(/<[^>]*>/g, ' ')
        const at = `${abs.slice(SRC.length + 1)}:${src.slice(0, te).split('\n').length}`
        // 🪤 A FILENAME IS NOT A MEMBER EXPRESSION. The first draft flagged `Edit SKILL.md` — prose
        // that happens to contain a dot — so the extension set is excluded by name. Narrow and
        // explicit beats clever here: the alternative (resolving the base identifier against the
        // file's declarations) is a type-checker's job, and this rail only has to catch the shape a
        // ternary-unwrapping conversion produces.
        const prose = text.replace(/\b[\w$]+\.(md|json|ts|tsx|js|jsx|py|txt|ya?ml|sh|css|html|svg|png|toml|lock)\b/gi, ' ')
        if (/\b(null|undefined|NaN)\b/.test(prose)) offenders.push(`${at}  literal keyword as text`)
        else if (/[A-Za-z_$][\w$]*\.[A-Za-z_$][\w$]*/.test(prose)) offenders.push(`${at}  member expression as text`)
      }
    }
    expect(offenders, 'a Button label showing source instead of its value:\n  ' + offenders.join('\n  '))
      .toEqual([])
  })

  it('🪤 the overlay stays aria-hidden — the accessible name must remain the ACTION', () => {
    // The trap `loadingLabel` walks into: making it part of the accessible name would rename the
    // control mid-flight, so "Rebuild links" stops being findable by that name exactly when the
    // user is looking for it. Same regression that killed the first `disabledReason` draft, where an
    // sr-only span concatenated into the name. `aria-busy` is the announcement channel; this is the
    // sighted one.
    const btn = code(join(SRC, 'ui', 'Button.tsx'))
    const overlay = btn.slice(btn.indexOf('{loading && ('), btn.indexOf('</AnimatePresence>'))
    expect(overlay, 'the loading overlay must be decorative').toMatch(/aria-hidden/)
    expect(overlay, 'and must not become a label').not.toMatch(/aria-label|sr-only/)
  })

  it('🔴 every progress verb survived the conversion', () => {
    // The words are the point. A conversion that gained `aria-busy` by deleting "Consolidating…"
    // would have traded one signal for another, which is not a fix.
    for (const [rel, verb] of VERBS) {
      const src = code(join(SRC, rel))
      expect(src, `${rel} must still say "${verb}"`).toContain(`loadingLabel="${verb}"`)
    }
  })

  it('and none of them kept the hand-rolled fragment that cost them the announcement', () => {
    for (const rel of [...new Set(VERBS.map(([r]) => r))]) {
      const src = code(join(SRC, rel))
      expect(src, `${rel} must not re-grow a manual spinner+verb`).not.toMatch(
        /\?\s*<>\s*<Loader2[^>]*\/>\s*\w/,
      )
    }
  })
})
