// @module-tag tree-scan
import { describe, it, expect } from 'vitest'
import { join } from 'node:path'
import { loopStatusLabel } from '../../lib/loopStatus'
import { WORK_STATE_LABEL } from '../projects/ProjectsSection'
import { filesUnder, readSource } from '../../test/sourceTree'

// ── #3471 "Needs you" names ONE set ───────────────────────────────────────────────────────
//
// MEASURED, with one `blocked` task in a project and nothing else:
//
//   the project's Work board   →  "NEEDS INPUT · 1", the task labelled NEEDS INPUT
//   the dashboard, same moment →  "All clear — nothing waiting on you."
//
// Neither surface was lying. They compute genuinely different sets:
//
//   the dashboard card   approvals ∪ inbox ∪ skill proposals   (`widgets/ActionCenter`)
//   the Work board       BoardState.NEEDS_INPUT, which a `blocked` task projects onto
//                        (`tasks/hierarchy_handlers._TASK_STATE`) alongside `stagnant` and
//                        `needs_input` runs
//   `lib/loopStatus`     needs_input → "Needs you", a third use of the phrase
//
// THE PHRASE WAS LYING. "All clear — nothing waiting on you" is a confident negative about a
// set the reader has no way to know is narrower than the words, and it is the sentence that
// turns a coherence collision into a defect.
//
// 🔑 THE DECISION, AND WHY IT IS THIS WAY ROUND. Widening the card's set was the other option and
// it is unbounded: "everything waiting on you" would have to absorb blocked tasks, stalled runs,
// suspended and review rows — which is the Work board, a surface that already exists — and none
// of those resolve inline, which is this card's entire contract. So the NARROWER set gives up the
// BROADER phrase. `needs_input` keeps "Needs you"; the triage queue is named for what it holds.
//
// 🪤 AND THE PHRASE HAD ALREADY DRIFTED A FOURTH TIME. `lib/loopStatus`'s own header lists
// `"Needs you" vs "Needs input"` among the drifts it retired when it became the one registry —
// while `ProjectsSection`'s `WORK_STATE_LABEL` hand-typed `'Needs input'` for the same wire
// value. A fifth per-page map its rail could not see, so the drift it declared fixed was live.
// §3 compares the two objects directly, which is why it cannot drift again silently.
//
// 🔑 AND THEN THE WIDER SET ARRIVED (#3888, #3813). The triage card is gone: Home's Needs You reads
// `GET /api/waiting`, the one list of everything the owner must answer from every source, and the
// approved Home names its group "Needs You". The rule this file held is unchanged, only its
// subject moved: a confident "nothing waiting on you" is said only by the surface that read the
// whole set, and only when no source of it went unread. So §1 and §2 now hold Home to that, §3
// still holds the work state's one word, and §4 holds the tour to the group it points at.

const SRC = join(process.cwd(), 'src')

function walk(dir: string): string[] {
  return filesUnder(dir, (name) => /\.tsx?$/.test(name) && !name.includes('.test.'))
}

/** Source with comments blanked — a rail measures the PROGRAM, not the explanation of it.
 *
 *  🪤 MEASURED ON THE FIRST RUN OF THIS FILE, which is why it is here rather than trusted: three
 *  of its five assertions red against the very commit that fixes them. Every hit was prose. The
 *  note above the new empty state quotes the sentence it replaced ("nothing waiting on you"); the
 *  note above the dashboard section quotes the label it gave up ("Needs you"); and `lib/loopStatus`'s
 *  header lists `"Needs you" vs "Needs input"` among the drifts it retired. All three are exactly
 *  the writing this rail wants future readers to find, so a scanner that reds them is a scanner
 *  that punishes documentation. Newlines are preserved so nothing shifts line for line — the same
 *  stripper `ui/loadErrorCensus.test.ts` arrived at after the same mistake, made there four times. */
const codeOf = (abs: string) =>
  readSource(abs)
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^[ \t]*\/\/.*$/gm, '')

/** Every non-test source file whose CODE mentions the phrase, repo-relative. */
const phraseSites = (phrase: string | RegExp) =>
  walk(SRC)
    .filter((abs) => (typeof phrase === 'string'
      ? codeOf(abs).includes(phrase)
      : phrase.test(codeOf(abs))))
    .map((abs) => abs.slice(SRC.length + 1))
    .sort()

const home = () => codeOf(join(SRC, 'pages/dashboard/Home.tsx'))
const needsRead = () => codeOf(join(SRC, 'lib/useNeedsYou.ts'))
const overview = () => codeOf(join(SRC, 'pages/dashboard/DashboardPage.tsx'))

describe('§1 "nothing waiting on you" is said only by the surface that read the whole set', () => {
  it('Needs You is the waiting list, read whole, with the Inbox’s own asks after it', () => {
    expect(needsRead(), 'Needs You reads the one list of everything waiting').toMatch(/api\.waiting\(\)/)
    expect(home(), 'Home draws Needs You from that read').toMatch(/useNeedsYou\(\)/)
  })

  it('and its all-clear sentence is the only one, said only when no source went unread', () => {
    const src = home()
    expect(src).toMatch(/'Nothing is waiting on you\.'/)
    // The sentence for a list some source of which could not be read is a narrower one.
    expect(src).toMatch(/needs\.unreadable\.length > 0 \? 'Nothing else is waiting on you\.'/)
    expect(
      phraseSites(/nothing (is )?waiting on you/i).filter((rel) => rel.startsWith('pages/dashboard/')),
      'a dashboard surface that did not read the whole set claims nothing waits',
    ).toEqual(['pages/dashboard/Home.tsx'])
  })
})

describe('§2 the group is named for what it holds', () => {
  it('Home names the whole waiting list "Needs You"', () => {
    expect(home()).toMatch(/'needs-you': 'Needs You'/)
  })

  it('and the work state keeps its own word, owned by the registry', () => {
    // Without this the whole file passes if "Needs you" is simply deleted from the app — a
    // green built on the phrase meaning nothing rather than one thing.
    const sites = phraseSites('Needs you')
    expect(sites, 'the phrase vanished; it is supposed to have ONE owner, not none').not.toEqual([])
    expect(sites).toContain('lib/loopStatus.ts')
    expect(overview(), 'the Overview took the phrase').not.toMatch(/Needs [yY]ou/)
  })
})

describe('§3 one wire value, one word', () => {
  it('the Work board reads the registry rather than hand-typing a second label', () => {
    expect(WORK_STATE_LABEL.needs_input).toBe(loopStatusLabel('needs_input'))
  })

  it('and that word is "Needs you", per the registry', () => {
    // Pinned as a literal as well as an identity, because the identity above is also satisfied
    // by both sides drifting together to a third word.
    expect(loopStatusLabel('needs_input')).toBe('Needs you')
    expect(WORK_STATE_LABEL.needs_input).toBe('Needs you')
  })

  it('and no source file still hand-types the retired variant', () => {
    expect(
      phraseSites(/'Needs input'|"Needs input"/),
      'the registry lists "Needs you" vs "Needs input" as drift it retired',
    ).toEqual([])
  })

  it('the board still groups by the state this is a label for (the vacuity floor)', () => {
    // A label map is only a coherence surface while something renders from it.
    const src = codeOf(join(SRC, 'pages/projects/ProjectsSection.tsx'))
    expect(src).toMatch(/WORK_STATE_LABEL\[/)
    expect(Object.keys(WORK_STATE_LABEL).sort())
      .toEqual(['done', 'needs_input', 'queued', 'review', 'suspended', 'working'])
  })
})

describe('§4 the tour describes the group it points at', () => {
  it('names the group by its label, and no label Home no longer has', () => {
    const tour = codeOf(join(SRC, 'app/onboarding/ProductTour.tsx'))
    const step = tour.slice(tour.indexOf("id: 'approvals'"))
    expect(step.slice(0, 600)).toContain('"Needs You"')
    expect(step.slice(0, 600), 'the tour still quotes the retired card').not.toContain('"To triage"')
  })
})
