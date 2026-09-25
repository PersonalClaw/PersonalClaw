import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { loopStatusLabel } from '../../lib/loopStatus'
import { WORK_STATE_LABEL } from '../projects/ProjectsSection'

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
// 🔑 THE RULING, AND WHY IT IS THIS WAY ROUND. Widening the card's set was the other option and
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

const SRC = join(process.cwd(), 'src')

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) { out.push(...walk(abs)); continue }
    if (!/\.tsx?$/.test(name) || name.includes('.test.')) continue
    out.push(abs)
  }
  return out
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
 *  stripper `ui/loadErrorState.test.tsx` arrived at after the same mistake, made there four times. */
const codeOf = (abs: string) =>
  readFileSync(abs, 'utf8')
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

const actionCenter = () => codeOf(join(SRC, 'pages/dashboard/widgets/ActionCenter.tsx'))
const dashboardPage = () => codeOf(join(SRC, 'pages/dashboard/DashboardPage.tsx'))

describe('§1 the triage card does not assert the absence of a set it cannot see', () => {
  it('its empty state makes no unscoped "nothing waiting on you" claim', () => {
    expect(
      actionCenter(),
      'a card reading three lanes cannot report on everything that waits on the user',
    ).not.toMatch(/nothing waiting on you/i)
  })

  it('and scopes the negative to the three lanes it actually read', () => {
    // Not merely "shorter copy": the sentence has to name the lanes, or the reader is back to
    // guessing how wide the claim is.
    const src = actionCenter()
    expect(src).toMatch(/Nothing to triage/)
    for (const lane of ['approvals', 'messages', 'skill proposals']) {
      expect(src, `the empty state does not name the ${lane} lane`).toContain(lane)
    }
  })

  it('and the set it reads is still exactly those three (the vacuity floor)', () => {
    // §1 goes green the day the card starts reading a fourth lane without widening its
    // sentence — which is the same defect with a different set.
    const src = actionCenter()
    expect(src).toMatch(/approvals\.map\(/)
    expect(src).toMatch(/liveInbox\.map\(/)
    expect(src).toMatch(/proposals\.map\(/)
  })
})

describe('§2 the dashboard section is named for what it holds', () => {
  it('the triage card is not labelled with the wider phrase', () => {
    expect(
      dashboardPage(),
      'the ActionCenter section still claims the phrase that names the needs_input state',
    ).not.toMatch(/label="Needs you"/)
  })

  it('it is labelled "To triage", agreeing with its own empty state', () => {
    expect(dashboardPage()).toMatch(/label="To triage"/)
    expect(actionCenter()).toMatch(/Nothing to triage/)
  })

  it('and the phrase still exists somewhere, owned by the state that means it', () => {
    // Without this the whole file passes if "Needs you" is simply deleted from the app — a
    // green built on the phrase meaning nothing rather than one thing.
    const sites = phraseSites('Needs you')
    expect(sites, 'the phrase vanished; it is supposed to have ONE owner, not none').not.toEqual([])
    expect(sites).toContain('lib/loopStatus.ts')
    expect(sites, 'the dashboard page took the phrase back').not.toContain('pages/dashboard/DashboardPage.tsx')
  })
})

describe('§3 one wire value, one word', () => {
  it('the Work board reads the registry rather than hand-typing a second label', () => {
    expect(WORK_STATE_LABEL.needs_input).toBe(loopStatusLabel('needs_input'))
  })

  it('and that word is "Needs you", per the registry ruling', () => {
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

describe('§4 the tour describes the card it points at', () => {
  it('names the new label and the set the card actually holds', () => {
    const tour = codeOf(join(SRC, 'app/onboarding/ProductTour.tsx'))
    const step = tour.slice(tour.indexOf("id: 'approvals'"))
    expect(step.slice(0, 600), 'the tour still quotes the old section label').not.toContain('"Needs you"')
    expect(step.slice(0, 600)).toContain('"To triage"')
  })
})
