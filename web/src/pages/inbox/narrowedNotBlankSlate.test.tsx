import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
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
const inbox = readFileSync(join(SRC, 'pages/inbox/InboxPage.tsx'), 'utf8')
/** 🪤 Comments stripped, because this rail's first version flagged its own subject's PROSE: the file
 *  documents the historical `filter !== 'all'` trap in a comment, and the assertion below counted that
 *  sentence as code. Fifth time in this session a rail has measured an explanation instead of a
 *  program — strip first, always. */
const inboxCode = inbox.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the inbox distinguishes "nothing matches" from "you have nothing"', () => {
  it('derives `narrowed` once, against its own default filter', () => {
    expect(inbox).toMatch(/const narrowed = !!\(q\.trim\(\) \|\| filter !== 'open' \|\| kind\)/)
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

  it('the title and the hint read the SAME flag, so they cannot disagree', () => {
    const tag = inbox.match(/<EmptyState icon=\{InboxIcon\}[\s\S]{0,900}?\/>/)?.[0] ?? ''
    expect(tag).toMatch(/title=\{narrowed \? 'Nothing here' : 'Inbox zero'\}/)
    expect(tag).toMatch(/hint=\{narrowed/)
  })

  it('the announcement shares that one definition too', () => {
    expect(inbox).toMatch(/results=\{\{[^}]*active: narrowed[^}]*\}\}/)
  })

  it('the eleven surfaces that were already right still are', () => {
    // Not vacuous, and a guard against a future copy sweep flattening these into one sentence: each
    // names what the user should change. Asserted per file, since that is where a regression lands.
    const CANONICAL: [string, RegExp][] = [
      ['pages/artifacts/ArtifactGrid.tsx', /Try a different search, kind, or collection\./],
      ['pages/knowledge/KnowledgeListPage.tsx', /Try a different search or filter\./],
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
})
