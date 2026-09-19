import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

// ── One list of twelve answered "nothing matches" with its onboarding paragraph ─────────────
//
// "You have none" and "none match" are different sentences, and the second one must never carry the
// first one's advice. Censused every list surface by filtering it to nothing in a real browser and
// reading the VISIBLE empty state (sr-only live regions stripped, so cycle 120's announcement could
// not be mistaken for on-screen copy):
//
//   #/artifacts   "No matching artifacts · Try a different search, kind, or collection."   ✅
//   #/knowledge   "No matching items · Try a different search or filter."                  ✅
//   #/prompts · #/skills · #/agents · #/tools · #/triggers · #/workflows   "Try a different …"  ✅
//   #/tasks       "No tasks match this filter."                                            ✅
//   #/apps        "No installed app matches the current search and filters."               ✅
//   #/inbox       "Nothing here · Inbox collects messages, questions, and notifications
//                  from your agents and connected sources (filesystem and Slack; email
//                  coming). Enable a source to begin."                                     🔴
//
// 🔑 THE TITLE WAS ALREADY RIGHT AND THE HINT WAS NOT, which is why this survived: `title` tested the
// narrowed expression ('Nothing here' vs 'Inbox zero') while `hint` tested `disabled` FIRST. So a user
// with items, searching for something that does not match, was told to enable a source — advice for a
// different problem — beneath a title saying their filter found nothing. Two halves of one component
// disagreeing about which state the list is in.
//
// Driven before → after on `#/inbox?q=zzqqxnomatch` (same build, same tree, only the edit differs):
//
//   before  "Nothing here  Inbox collects messages, questions, and notifications from your agents and
//            connected sources (filesystem and Slack; email coming). Enable a source to begin."
//   after   "Nothing here  Try a different search or filter."
//
// 🔑 ONE DEFINITION OF NARROWED, SHARED. `narrowed` is now derived once and used by the empty state's
// title, its hint, AND the results announcement — so the three cannot drift apart. It compares the
// status filter against this surface's OWN default (`open`, not `all`), the trap the announcement rail
// already records.
//
// 🪤 HOISTING IT BROKE THE ANNOUNCEMENT RAIL, WHICH WAS MEASURING THE SPELLING — it read the `active:`
// expression off one line, so a named const looked like a hardcoded value. Widened to resolve an
// identifier back to its `const` (the fourth widening of that family, same lesson each time): the
// property is "derived from the query or a filter compared to this surface's own default", not where
// the expression is written.

const SRC = join(process.cwd(), 'src')

/** Comments stripped BEFORE any match — the standing rule in this file, and load-bearing for the
 *  census below: several subjects carry comments quoting the copy they replaced. */
function strip(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')
}

/** The sentences that mean "your filter or search emptied this list", as distinct from "you have
 *  none". Deliberately phrase-based rather than component-based: the whole point of the census is to
 *  find the sites that did NOT reach for the component. */
const NARROWED_COPY =
  /No matching [a-z]+|No [a-z]+ match|Nothing matches|No results|Try a different|match(?:es)? (?:the|your) (?:current )?(?:search|filter)/i

/** Re-measured 2026-09-19 against this tree: 43 narrowed states through `EmptyState`, 19 hand-rolled
 *  (44 / 20 before this change converted `notifications/NotificationsPage`). The ceiling may only
 *  FALL. The remaining 19 are NOT all defects — a popover, a command palette and an inline composer
 *  toolbar cannot host a centred block with a 48px icon badge and `py-2xl`, and some are result
 *  summaries rather than empty states at all. They stay a shrink-only count rather than nineteen
 *  named exemptions, because a weak claim that cannot be got wrong beats nineteen verdicts that can. */
const HAND_ROLLED_CEILING = 19

function tsxFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) tsxFiles(p, acc)
    else if (entry.endsWith('.tsx') && !entry.includes('.test.')) acc.push(p)
  }
  return acc
}

function censusNarrowed(): { primitive: string[]; handRolled: string[] } {
  const primitive: string[] = []
  const handRolled: string[] = []
  for (const abs of [...tsxFiles(join(SRC, 'pages')), ...tsxFiles(join(SRC, 'ui'))]) {
    const rel = abs.slice(SRC.length + 1)
    const code = strip(readFileSync(abs, 'utf8'))
    for (const m of code.matchAll(new RegExp(NARROWED_COPY.source, 'gi'))) {
      // Which JSX construct encloses it: the nearest opening tag looking backwards.
      const back = code.slice(Math.max(0, m.index! - 400), m.index!)
      const tags = [...back.matchAll(/<([A-Za-z][A-Za-z0-9]*)/g)].map((t) => t[1])
      const wrapper = tags.length ? tags[tags.length - 1] : '?'
      ;(wrapper === 'EmptyState' ? primitive : handRolled).push(`${rel} <${wrapper}>`)
    }
  }
  return { primitive, handRolled }
}

const inbox = readFileSync(join(SRC, 'pages/inbox/InboxPage.tsx'), 'utf8')
/** 🪤 Comments stripped, because this rail's first version flagged its own subject's PROSE: the file
 *  documents the historical `filter !== 'all'` trap in a comment, and the assertion below counted that
 *  sentence as code. Fifth time in this session a rail has measured an explanation instead of a
 *  program — strip first, always. */
const inboxCode = inbox.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the inbox distinguishes "nothing matches" from "you have nothing"', () => {
  it('derives `narrowed` once, against its own default filter', () => {
    // 🪤 WIDENED for TSE2-3's owner chip, and deliberately not weakened. The property this
    // rail owns is "ONE derivation, comparing the status filter to THIS surface's default" —
    // not the exact list of narrowing dimensions, which grows whenever the page gains a
    // filter (`kind` was itself such a growth). So the trailing terms are open-ended while
    // the two load-bearing halves stay pinned: the single `const narrowed = !!(…)` shape,
    // and `filter !== 'open'` rather than `'all'`. A second derivation elsewhere would still
    // be caught by the `hint=`/`title=` assertions below, which read the identifier.
    expect(inbox).toMatch(/const narrowed = !!\(q\.trim\(\) \|\| filter !== 'open' \|\| kind[^)]*\)/)
    expect(inboxCode.match(/const narrowed\b/g) ?? [], 'derived exactly once').toHaveLength(1)
    expect(inboxCode, "'all' is not this surface's default — 'open' is").not.toMatch(/filter !== 'all'/)
  })

  it('the narrowed hint tells the user about their filter, not about onboarding', () => {
    const tag = inbox.match(/<EmptyState icon=\{InboxIcon\}[\s\S]{0,900}?\/>/)?.[0] ?? ''
    expect(tag, 'the empty state must still exist').toContain('InboxIcon')
    expect(tag, 'narrowed must be tested BEFORE disabled').toMatch(
      /hint=\{narrowed[\s\S]*?:\s*disabled/,
    )
    expect(tag).toMatch(/Try a different search or filter\./)
  })

  it('keeps the blank-slate copy for the state it was written for', () => {
    // The onboarding paragraph is right when the inbox genuinely has nothing AND no source is on —
    // deleting it would trade one wrong answer for another.
    const tag = inbox.match(/<EmptyState icon=\{InboxIcon\}[\s\S]{0,900}?\/>/)?.[0] ?? ''
    expect(tag).toMatch(/Enable a source to begin\./)
    expect(tag, 'and the caught-up line for a genuinely empty, enabled inbox').toMatch(/all caught up/)
  })

  it('keeps the kind-specific narrowed line, and makes it say why', () => {
    // A kind chip is also narrowing, so its line moved under `narrowed` too — and now names the cause
    // ("matches the current search or filter") instead of the ambiguous "right now".
    const tag = inbox.match(/<EmptyState icon=\{InboxIcon\}[\s\S]{0,900}?\/>/)?.[0] ?? ''
    expect(tag).toMatch(/matches the current search or filter\./)
  })

  it('the title and the hint read the SAME flags, in the same order, so they cannot disagree', () => {
    // 🔁 TIGHTENED 2026-09-07 — and this is a strengthening, not a loosening, which matters because
    // editing a guard to permit what it was written to check is exactly the wrong move.
    //
    // This asserted the title's exact two-branch spelling: `title={narrowed ? 'Nothing here' :
    // 'Inbox zero'}`. That spelling WAS the defect on the other axis. The hint branches `narrowed`
    // then `disabled`; the title branched on `narrowed` only — so on a fresh install the headline read
    // "Inbox zero" above a hint reading "Enable a source to begin". The two could still disagree about
    // which state the list is in; they just could not disagree about `narrowed`.
    //
    // This test's PURPOSE (its own name) is that the two props cannot disagree. Once a third state was
    // named, a pinned two-branch title could no longer express that purpose — so the assertion now
    // requires BOTH flags in BOTH props, in the same order. Strictly more than it demanded before.
    // Behaviour is covered by `inboxZeroNotConnected.test.tsx`.
    const tag = inbox.match(/<EmptyState icon=\{InboxIcon\}[\s\S]{0,900}?\/>/)?.[0] ?? ''
    expect(tag, 'the tag must be found before it can be measured').not.toBe('')
    expect(tag, 'the title must branch narrowed → disabled → caught-up')
      .toMatch(/title=\{narrowed \? 'Nothing here' : disabled \? '[^']+' : 'Inbox zero'\}/)
    expect(tag, 'and the hint must test the same two flags in the same order')
      .toMatch(/hint=\{narrowed[\s\S]*?: disabled/)
  })

  it('the announcement shares that one definition too', () => {
    expect(inbox).toMatch(/results=\{\{[^}]*active: narrowed[^}]*\}\}/)
  })

  it('the seven pinned surfaces keep their narrowed copy', () => {
    // 🔴 RENAMED, because the name was the bug. It said "the eleven surfaces" and the list held
    // SEVEN — and the name was the only surviving record of the four it had lost. That is the defect
    // class this repo keeps re-finding: an enumerated rail cannot see its own blind spot. The count
    // now matches the list, and the census test below is what actually guards the POPULATION; this
    // one is a copy pin on seven surfaces whose exact wording has been deliberately chosen.
    // Not vacuous, and a guard against a future copy sweep flattening these into one sentence: each
    // names what the user should change. Asserted per file, since that is where a regression lands.
    const CANONICAL: [string, RegExp][] = [
      ['pages/artifacts/ArtifactGrid.tsx', /Try a different search, kind, or collection\./],
      // Knowledge graduated from the single narrowed sentence to the emptyStateNoMatch split
      // (AUD-NZ10) — the pin follows the copy to its query-named form; the narrowed-vs-blank
      // distinction this rail guards is still there (the blank slate stays "Knowledge base is
      // empty" and is pinned by knowledgeNoMatch.test.ts alongside the split).
      ['pages/knowledge/KnowledgeListPage.tsx', /No items match “\$\{submitted\}”/],
      ['pages/prompts/PromptsListPage.tsx', /Try a different term\./],
      ['pages/skills/SkillsPage.tsx', /Try a different term\./],
      ['pages/triggers/TriggersListPage.tsx', /Try a different filter\./],
      ['pages/workflows/WorkflowsListPage.tsx', /Try a different search\./],
      ['pages/tasks/TasksListPage.tsx', /No tasks match this (filter|scope)\./],
    ]
    for (const [rel, copy] of CANONICAL) {
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel} must keep its narrowed copy`).toMatch(copy)
    }
  })

  it('every narrowed empty state goes through the PRIMITIVE, and the list is DERIVED', () => {
    // The census the enumerated pin above cannot be: measured from source, so a surface added
    // tomorrow is covered without anyone remembering to register it. 62 narrowed states across
    // `pages` + `ui` — 43 through `EmptyState`, 19 hand-rolled.
    const { primitive, handRolled } = censusNarrowed()

    // A rail over nothing asserts nothing: the scan must find the population it guards.
    expect(primitive.length, 'the census found almost no primitive-based narrowed states — the scan is broken')
      .toBeGreaterThan(30)

    // Shrink-only in the direction that matters: hand-rolled may only go DOWN. A new surface that
    // centres a bare <div> instead of reaching for the primitive reds here, by name.
    expect(
      handRolled.length,
      'a narrowed empty state must use `EmptyState` unless its container is too small for it ' +
        '(a popover, a palette, an inline toolbar). Hand-rolled sites, which may only decrease:\n  ' +
        handRolled.join('\n  '),
    ).toBeLessThanOrEqual(HAND_ROLLED_CEILING)

    // And the two sharp ones must not come back. Each reached for `EmptyState` on its blank-slate
    // branch and hand-rolled the sibling branch IN THE SAME COMPONENT, so there was never a
    // container argument for them. `CodeSection` was converged on main; `NotificationsPage` here.
    for (const rel of ['pages/code/CodeSection.tsx', 'pages/notifications/NotificationsPage.tsx']) {
      const code = strip(readFileSync(join(SRC, rel), 'utf8'))
      expect(code, `${rel} must render its narrowed state through the primitive`)
        .toMatch(/<EmptyState[\s\S]{0,400}?just none/)
      expect(code, `${rel} must not reintroduce a centred div for it`)
        .not.toMatch(/text-center[^>]{0,80}>\s*\{?\s*(needle \?|`No projects|Nothing matches this filter)/)
    }
  })

  it('a narrowed state names what the user can CHANGE, and offers the un-narrowing', () => {
    // The family's own criterion. `Nothing matches this filter.` named the narrowing and not the way
    // out, so a user who filtered a fresh install could not tell an empty filter from an empty app.
    // Shape shared with `loops/LoopsListPage` and `code/CodeSection`, the most complete members: how
    // many exist, that they are merely elsewhere, and the un-narrowing as an action.
    for (const [rel, action] of [
      ['pages/code/CodeSection.tsx', /(Clear search|View all projects)/],
      ['pages/notifications/NotificationsPage.tsx', /Show all/],
      ['pages/loops/LoopsListPage.tsx', /View all loops/],
    ] as [string, RegExp][]) {
      const code = strip(readFileSync(join(SRC, rel), 'utf8'))
      expect(code, `${rel} must offer the un-narrowing as an action`).toMatch(action)
      expect(code, `${rel} must say how many exist, so an empty filter reads differently from an empty app`)
        .toMatch(/just none in this view/)
    }
  })
})
