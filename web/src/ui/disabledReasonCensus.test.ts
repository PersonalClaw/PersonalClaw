import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── Every disabled control is explained, in flight, or listed here with its reason ────────────────
//
// #1645 pinned the two carriers and said plainly that it was NOT a census, because three instruments had
// over-reported (a proximity window; an opening-tag scan that missed reasons in the children; matches
// located in comment-stripped source, which shifted every line number). This is the instrument those
// attempts were missing, and the honest number it produces:
//
//   337 `disabled=` props → 191 in-flight (natively disabled, per `unavailable.ts`'s own rule)
//                        →  91 carrying an explicit reason
//                        →  11 remaining, ALL classified below
//
// So the "55 other" reported in #1645 was instrument noise, not a backlog. Reading the element WITH ITS
// CHILDREN is what collapsed it.
//
// 🔑 The ratchet: a NEW conditionally-disabled control that neither explains itself nor appears below fails
// this test. The exemptions are not a bypass — each names why, and the second test asserts each still has
// the shape it is excused for.

const SRC = join(process.cwd(), 'src')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

/** In-flight names: `unavailable.ts` sends these natively disabled on purpose. */
const BUSY =
  /busy|saving|loading|pending|submitting|acting|probing|searching|testing|running|reloading|installing|deleting|syncing|creating|sending|launching|retrying|regenning|bundling|starting|stopping|pulling|applying|resolving|refreshing|rebuilding|generating|uploading|downloading|reconnecting|scanning|verifying|repairing|restoring|pausing|resuming|cancelling|dismissing|promoting|consolidating|rechecking|exporting|importing|merging|pinPending|flash/i
/** An explanation a user or AT can reach: the carriers, or an explanation-bearing attribute.
 *
 *  🪤 `label=` AND `placeholder=` ARE DELIBERATELY ABSENT. The first draft counted them and the census went
 *  toothless: a label is the control's NAME, not a reason it is off, and nearly every control has one — a
 *  mutation that added `disabled={!q}` to a labelled `HeaderControl` passed cleanly. */
const REASON = /disabledReason|unavailableWhen|aria-disabled|title=|hint=/i

/** The element containing `i`, INCLUDING its children — the region the last instrument got wrong.
 *
 *  🪤 An opening-tag scan is as wrong as a character window, in the other direction: the knowledge
 *  outcome row disables its link and puts the reason in the button's own text. */
function elementWithChildren(src: string, i: number): string {
  const a = src.lastIndexOf('<', i)
  if (a < 0) return ''
  const tag = /^<([A-Za-z][\w.]*)/.exec(src.slice(a))?.[1]
  if (!tag) return src.slice(a, a + 400)
  let depth = 0
  let j = a
  for (; j < src.length; j++) {
    const c = src[j]
    if (c === '{') depth++
    else if (c === '}') depth--
    else if (c === '>' && depth === 0) break
  }
  if (src[j - 1] === '/') return src.slice(a, j + 1)   // self-closing: no children
  const close = `</${tag}>`
  const openRe = new RegExp(`<${tag}[\\s/>]`, 'g')
  let k = j + 1
  let level = 1
  while (k < src.length && level > 0) {
    const nextClose = src.indexOf(close, k)
    openRe.lastIndex = k
    const nextOpen = openRe.exec(src)
    if (nextClose < 0) break
    if (nextOpen && nextOpen.index < nextClose) { level++; k = nextOpen.index + nextOpen[0].length }
    else { level--; k = nextClose + close.length }
  }
  return src.slice(a, k)
}

/** A site's identity: its FILE plus its own `disabled=` expression — never its line number.
 *
 *  🔑 THIS IS THE FOURTH RE-KEY AND THE LAST ONE. The comments below record three renumbers in which
 *  nothing about any control changed; #2234 then added one import to `TaskDetail.tsx` and shifted all
 *  four of its sites by +1, reding this rail from a PR that never touched a disabled control. A key is
 *  supposed to say WHICH site is excused; a line number says WHERE IT SAT, and those stop being the
 *  same thing the moment anyone edits above it. The expression is what the exemption is actually about.
 *
 *  Line numbers are still reported in the failure message — losing them would trade a false-alarm
 *  problem for an unnavigable one — they are simply not what identity is computed from. */
type Site = { key: string; rel: string; line: number }

const siteKey = (rel: string, expr: string) => `${rel}  disabled={${expr}}`

/** Every conditionally-disabled control with no reason in its element. */
function unexplained(): Site[] {
  const out: Site[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')          // ORIGINAL text: line numbers match the file
    for (const m of src.matchAll(/disabled=\{([^}]{1,90})\}/g)) {
      const expr = m[1].trim()
      const ids = (expr.match(/[A-Za-z_$][\w$]*/g) ?? []).filter((x) => !['true', 'false', 'null', 'undefined', 'length'].includes(x))
      if (!ids.length || BUSY.test(expr)) continue
      if (REASON.test(elementWithChildren(src, m.index!))) continue
      const rel = abs.replace(SRC + '/', '')
      out.push({ key: siteKey(rel, expr), rel, line: src.slice(0, m.index!).split('\n').length })
    }
  }
  return out.sort((a, b) => a.key.localeCompare(b.key) || a.line - b.line)
}

/** The remainder, each with why it is not a defect. Thirteen sites, twelve keys.
 *
 *  Keyed by the control's own `disabled=` expression (see `siteKey`), with `n` where one file has more
 *  than one site sharing an expression. The count is part of the exemption: a THIRD `disabled={readOnly}`
 *  in `TaskDetail.tsx` is a new unexplained control and must red, not inherit an excuse. */
const CLASSIFIED: Record<string, { n?: number; why: string }> = {
  // The carrier itself: these two lines ARE the soft-off implementation `disabledReason` drives.
  'ui/Button.tsx  disabled={softOff || undefined}': { why: 'the Button carrier implementing soft-off' },
  'ui/Button.tsx  disabled={softOff ? undefined : off}': { why: 'the Button carrier implementing soft-off' },
  // LOADING, not missing input: `=== null` is "not fetched yet", which `unavailable.ts` sends natively
  // disabled on purpose. (Two copies because the inbox settings panel exists twice — an open taste call.)
  'pages/inbox/InboxSettingsPanel.tsx  disabled={sourcesOn === null}': { why: 'loading (sourcesOn === null)' },
  'pages/inbox/InboxSettingsPanel.tsx  disabled={engagementOn === null}': { why: 'loading (engagementOn === null)' },
  'pages/settings/InboxSettingsPanel.tsx  disabled={sourcesOn === null}': { why: 'loading (sourcesOn === null)' },
  'pages/settings/InboxSettingsPanel.tsx  disabled={engagementOn === null}': { why: 'loading (engagementOn === null)' },
  // The reason is the button's own visible text — "(removed — insight kept)" — not an attribute.
  'pages/knowledge/KnowledgeListPage.tsx  disabled={!o.item_id}': { why: "the reason is the button's own label text" },
  // Section-level explanation: the panel renders "Managed by project — read-only" with a lock icon, so
  // the surface states it once rather than repeating it on every checkbox.
  'pages/tasks/TaskDetail.tsx  disabled={readOnly}': { n: 2, why: 'read-only task; the panel states it once with a lock' },
  // Display-only rows: with no navigation handler the row is not a button, and `disabled:cursor-default`
  // says exactly that rather than "blocked".
  'pages/tasks/TaskDetail.tsx  disabled={!onOpenTask}': { why: 'no navigation handler → informational row (cursor-default)' },
  'pages/tasks/TaskDetail.tsx  disabled={!dep || !onOpenTask}': { why: 'no navigation handler → informational row (cursor-default)' },
  // A sequence dependency whose cause is the field directly above it.
  'pages/tasks/TaskForm.tsx  disabled={!projectId}': { why: 'depends on the Project field rendered immediately above' },
  // The reason is the control's own NAME, which flips with the state: "Pin to dashboard" when it can
  // be pressed, "Pinned to dashboard" when it cannot — plus `aria-pressed` saying the same thing.
  //
  // 🔍 THIS SITE IS NOT NEW; IT WAS HIDDEN. It read `disabled={pinPending || pinned}`, and `pinPending`
  // is in the BUSY list above, so the whole compound expression was skipped as in-flight. Giving the icon
  // tiers a `loading` prop split it into `disabled={pinned}` + `loading={pinPending}`, and the
  // pre-existing unexplained half surfaced. A compound gate with one in-flight term is a blind spot for
  // any in-flight-keyed scan, this one included.
  'ui/widget/WidgetFrame.tsx  disabled={pinned}': { why: "the reason is the button's own name, which flips to \"Pinned to dashboard\"" },
}

/** 📓 WHY THE KEYS ARE NOT LINE NUMBERS — the history, kept because it is the evidence for the change.
 *
 *  This map was keyed `path:line` and was renumbered FOUR times without a single control changing:
 *
 *    · `pages/settings/InboxSettingsPanel.tsx`  91 → 129 / 95 → 133   (PA-5 inserted a section above)
 *    · `pages/knowledge/KnowledgeListPage.tsx`  918 → 941 → 953       (KL-8 added the Home lens above)
 *    · `pages/tasks/TaskDetail.tsx`  197/208/226/254 → 210/221/239/267 (exit-criteria bar adopted
 *      `ui/Meter`) → 226/247/267/295 (two tick targets raised to 24px) → 227/248/268/296 (#2234 added
 *      ONE import line)
 *
 *  The last one is the clearest: a PR whose whole subject was reporting failed writes shifted four
 *  exemptions by +1 and reded this rail. Each time the fix was to read the new line, confirm it was the
 *  same control, and renumber — bookkeeping that taught nobody anything and that the next insertion
 *  undoes. The previous note here concluded "RE-KEY IT ON THE CONTROL'S OWN TEXT … the fix is overdue
 *  and belongs in its own change". This is that change.
 *
 *  🪤 What is DELIBERATELY preserved: the old keying's real strength was being exact about which site it
 *  excused, so this does not loosen to a per-file allowance. `file + expression + count` is exact about
 *  the shape AND the number of sites, so a new unexplained control reds even when it shares an
 *  expression with an excused one — verified by mutation. */

/** Pass-through primitives: `disabled` arrives as a prop and the reason belongs to the CALLER. Counted
 *  separately because "explain yourself" is not a rule a primitive can satisfy. */
// 🪤 `Button` is NOT in here. Its two sites are the CARRIER implementing soft-off, not a component
// forwarding a caller's prop — lumping them in would have hidden the difference behind a convenient regex,
// and the difference is the whole point of the classification.
const PASSTHROUGH = /^(ui\/(Slider|Segmented|HeaderActions|forms)\.tsx|pages\/(loops\/DesignCockpitPage|settings\/(DurabilityPanel|ProjectionRulesPanel|SecurityPanel))\.tsx):/

describe('the disabled-reason census', () => {
  it('finds the population — the scan is not vacuous', () => {
    const all = walk(SRC).flatMap((abs) => [...readFileSync(abs, 'utf8').matchAll(/disabled=\{/g)])
    expect(all.length, 'conditional disabled props').toBeGreaterThanOrEqual(300)
  })

  it('every unexplained control is classified or a pass-through', () => {
    const found = unexplained().filter((s) => !PASSTHROUGH.test(`${s.rel}:`))
    const where = (k: string) =>
      found.filter((s) => s.key === k).map((s) => `${s.rel}:${s.line}`).join(', ')

    const surprises = [...new Set(found.map((s) => s.key))].filter((k) => !(k in CLASSIFIED))
    expect(
      surprises.map((k) => `${k}   at ${where(k)}`),
      'a control disabled for a reason nobody states',
    ).toEqual([])

    // 🪤 EXACT, not "at most", and now exact on the COUNT too. A `<=` passes when a site gains a reason
    // and its entry above becomes dead weight — an exemption list nobody prunes is how the next reader
    // inherits stale excuses. Counting also keeps the old line-keying's precision: two excused
    // `disabled={readOnly}` sites do not silently excuse a third.
    const actual: Record<string, number> = {}
    for (const s of found) actual[s.key] = (actual[s.key] ?? 0) + 1
    const expected = Object.fromEntries(Object.entries(CLASSIFIED).map(([k, v]) => [k, v.n ?? 1]))
    expect(actual, 'the classification must match the remainder exactly, count included').toEqual(expected)
  })

  it('each classified site still has the shape it is excused for', () => {
    // 🪤 The vacuity half. The KEY already proves the `disabled=` expression is present — that is what it
    // is computed from — so what this adds is the CORROBORATION each reason claims: the label, the cursor,
    // the neighbouring field, the paired prop. None of it reads a line number, so an insertion above any
    // of these sites cannot red it.
    const code = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

    expect(code('ui/Button.tsx'), 'the carrier really implements soft-off').toMatch(/softOff/)

    expect(code('pages/knowledge/KnowledgeListPage.tsx'),
      'and its label really does explain the state').toMatch(/\(removed — insight kept\)/)

    expect(code('pages/tasks/TaskDetail.tsx'),
      'the cursor says "not a button", not "blocked"').toMatch(/disabled:cursor-default/)
    expect(code('pages/tasks/TaskDetail.tsx'),
      'and the read-only state is stated once for the section').toMatch(/read-only|readOnly/)

    // The compound is the point here: `pinned` gates the press while `pinPending` is the in-flight half.
    // Splitting them is what surfaced this site, so assert they are still split rather than recombined.
    expect(code('ui/widget/WidgetFrame.tsx'),
      'pinned gates the press; pinPending stays the in-flight prop').toMatch(/disabled=\{pinned\}\s+loading=\{pinPending\}/)
    expect(code('ui/widget/WidgetFrame.tsx'),
      'and the name really does state the gate').toMatch(/pinned \? 'Pinned to dashboard'/)

    expect(code('pages/tasks/TaskForm.tsx'),
      'and the Project field it depends on is right above').toMatch(/<Field label="Project">/)

    for (const rel of ['pages/settings/ProjectionRulesPanel.tsx', 'pages/settings/DurabilityPanel.tsx']) {
      expect(code(rel), `${rel} takes disabled from a caller`)
        .toMatch(/disabled\??:\s*boolean|disabled\s*\}/)
    }
  })

  it('the keys carry no line numbers — the property this change exists to establish', () => {
    // The regression guard for the change itself. Four renumbers happened because identity was
    // positional; if a later edit reintroduces a `path:123` key, that clock starts again.
    const positional = Object.keys(CLASSIFIED).filter((k) => /:\d+/.test(k))
    expect(positional, 'a classified key must identify a control, not a position').toEqual([])
    expect(Object.keys(CLASSIFIED).every((k) => k.includes('disabled={')),
      'every key names the expression it excuses').toBe(true)
  })
})
