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

/** file:line for every conditionally-disabled control with no reason in its element. */
function unexplained(): string[] {
  const out: string[] = []
  for (const abs of walk(SRC)) {
    const src = readFileSync(abs, 'utf8')          // ORIGINAL text: line numbers match the file
    for (const m of src.matchAll(/disabled=\{([^}]{1,90})\}/g)) {
      const expr = m[1].trim()
      const ids = (expr.match(/[A-Za-z_$][\w$]*/g) ?? []).filter((x) => !['true', 'false', 'null', 'undefined', 'length'].includes(x))
      if (!ids.length || BUSY.test(expr)) continue
      if (REASON.test(elementWithChildren(src, m.index!))) continue
      out.push(`${abs.replace(SRC + '/', '')}:${src.slice(0, m.index!).split('\n').length}`)
    }
  }
  return out.sort()
}

/** The remainder, each with why it is not a defect. Thirteen today. */
const CLASSIFIED: Record<string, string> = {
  // The carrier itself: these two lines ARE the soft-off implementation `disabledReason` drives.
  'ui/Button.tsx:161': 'the Button carrier implementing soft-off',
  'ui/Button.tsx:163': 'the Button carrier implementing soft-off',
  // LOADING, not missing input: `=== null` is "not fetched yet", which `unavailable.ts` sends natively
  // disabled on purpose. (Two copies because the inbox settings panel exists twice — an open taste call.)
  'pages/inbox/InboxSettingsPanel.tsx:67': 'loading (sourcesOn === null)',
  'pages/inbox/InboxSettingsPanel.tsx:73': 'loading (engagementOn === null)',
  // 91 → 129 / 95 → 133 (PA-5): the proactive-triage section was inserted ABOVE these two, so the
  // SITES are unchanged and only their line numbers moved. This map keys on file:line, so an edit
  // above a classified site rots the key; the shape assertion below re-proves each one.
  'pages/settings/InboxSettingsPanel.tsx:129': 'loading (sourcesOn === null)',
  'pages/settings/InboxSettingsPanel.tsx:133': 'loading (engagementOn === null)',
  // The reason is the button's own visible text — "(removed — insight kept)" — not an attribute.
  // Line moved 918 → 941 when `KL-8` added the Home lens above it; the SITE is unchanged and the
  // shape assertion below re-proves it. This map keys on file:line, so any edit above a classified
  // site is a mechanical re-point — not a re-classification.
  'pages/knowledge/KnowledgeListPage.tsx:941': "the reason is the button's own label text",
  // Section-level explanation: the panel renders "Managed by project — read-only" with a lock icon, so
  // the surface states it once rather than repeating it on every checkbox.
  // 🪤 THESE FOUR ARE PINNED BY LINE NUMBER, so ANY edit above them in TaskDetail.tsx moves all four
  // and reds this rail. They shifted 197/208/226/254 → 210/221/239/267 when the exit-criteria bar
  // adopted `ui/Meter` (the same control at each, verified by reading the line). Nothing about the
  // controls changed. Noted rather than silently renumbered: a census keyed on `path:line` is exact
  // about WHICH site it excuses, which is its strength, but it makes an unrelated insertion look like
  // a new offender. Worth re-keying on the control's own text if it bites a third time.
  'pages/tasks/TaskDetail.tsx:210': 'read-only task; the panel states it once with a lock',
  'pages/tasks/TaskDetail.tsx:221': 'read-only task; the panel states it once with a lock',
  // Display-only rows: with no navigation handler the row is not a button, and `disabled:cursor-default`
  // says exactly that rather than "blocked".
  'pages/tasks/TaskDetail.tsx:239': 'no navigation handler → informational row (cursor-default)',
  'pages/tasks/TaskDetail.tsx:267': 'no navigation handler → informational row (cursor-default)',
  // A sequence dependency whose cause is the field directly above it.
  'pages/tasks/TaskForm.tsx:181': 'depends on the Project field rendered immediately above',
  // The reason is the control's own NAME, which flips with the state: "Pin to dashboard" when it can
  // be pressed, "Pinned to dashboard" when it cannot — plus `aria-pressed` saying the same thing.
  //
  // 🔍 THIS SITE IS NOT NEW; IT WAS HIDDEN. It read `disabled={pinPending || pinned}`, and
  // `pinPending` is in the BUSY list above, so the whole compound expression was skipped as
  // in-flight. Giving the icon tiers a `loading` prop split it into `disabled={pinned}` +
  // `loading={pinPending}`, and the pre-existing unexplained half surfaced. A compound gate with one
  // in-flight term is a blind spot for any in-flight-keyed scan, this one included.
  'ui/widget/WidgetFrame.tsx:250': "the reason is the button's own name, which flips to \"Pinned to dashboard\"",
}

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
    const found = unexplained().filter((k) => !PASSTHROUGH.test(k))
    const surprises = found.filter((k) => !(k in CLASSIFIED))
    expect(surprises, 'a control disabled for a reason nobody states').toEqual([])
    // 🪤 EXACT, not "at most". A `<=` passes when a site gains a reason and its entry above becomes dead
    // weight — an exemption list nobody prunes is how the next reader inherits stale excuses.
    expect(found.sort(), 'the classification must match the remainder exactly')
      .toEqual(Object.keys(CLASSIFIED).sort())
  })

  it('each classified site still has the shape it is excused for', () => {
    // 🪤 The vacuity half. A line number rots the moment a file is edited above it, so this asserts the
    // CODE at each site rather than trusting the key — an exemption pointing at the wrong line is worse
    // than none, because it silently excuses whatever moved into place.
    const at = (rel: string, line: number) => readFileSync(join(SRC, rel), 'utf8').split('\n')[line - 1] ?? ''
    expect(at('ui/Button.tsx', 161)).toMatch(/softOff/)
    expect(at('pages/knowledge/KnowledgeListPage.tsx', 941)).toMatch(/disabled=\{!o\.item_id\}/)
    expect(readFileSync(join(SRC, 'pages/knowledge/KnowledgeListPage.tsx'), 'utf8'),
      'and its label really does explain the state').toMatch(/\(removed — insight kept\)/)
    expect(at('pages/tasks/TaskDetail.tsx', 267)).toMatch(/disabled=\{!onOpenTask\}/)
    expect(readFileSync(join(SRC, 'pages/tasks/TaskDetail.tsx'), 'utf8'),
      'the cursor says "not a button", not "blocked"').toMatch(/disabled:cursor-default/)
    expect(at('ui/widget/WidgetFrame.tsx', 250)).toMatch(/disabled=\{pinned\} loading=\{pinPending\}/)
    expect(at('ui/widget/WidgetFrame.tsx', 250),
      'and the name really does state the gate').toMatch(/pinned \? 'Pinned to dashboard'/)
    expect(at('pages/tasks/TaskForm.tsx', 181)).toMatch(/disabled=\{!projectId\}/)
    expect(readFileSync(join(SRC, 'pages/tasks/TaskForm.tsx'), 'utf8'),
      'and the Project field it depends on is right above').toMatch(/<Field label="Project">/)
    for (const rel of ['pages/settings/ProjectionRulesPanel.tsx', 'pages/settings/DurabilityPanel.tsx']) {
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel} takes disabled from a caller`)
        .toMatch(/disabled\??:\s*boolean|disabled\s*\}/)
    }
  })
})
