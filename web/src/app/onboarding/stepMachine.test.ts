import { describe, it, expect } from 'vitest'
import {
  ORDER, TITLES, SLUGS, STORED,
  furthestOf, isUnlocked, nextOf, pathOf, previousOf, resolveStep, stepFromSlug, stepFromStored,
  stepIndex, type StepId,
} from './steps'

// ── The first-run flow's navigation, proved over the machine rather than the screen ───────────────
//
// The owner's word for this surface was "illnavigable", and the three defects behind it were all
// properties of the step machine rather than of any one component, all measured on a fresh home:
//
//  1. **Going back destroyed going forward.** Row state was derived from `ORDER.indexOf(step)`, so
//     returning to step 1 from step 3 re-derived steps 2-5 as `upcoming` — which removed every row
//     header button, leaving the page with only `Continue` and `Skip setup`. The user's only route
//     forward was to walk all four steps again.
//  2. **A jumped-over step rendered as complete.** Same derivation: a resumed run marked every row
//     before the current one `done`, putting a green check on "Bring your setup over" for a user
//     who had never seen that screen.
//  3. **Nothing survived a refresh.** Steps had no URL and only three of the five had a persisted
//     resume point, so a reload on the import step restarted at the beginning and a reload on the
//     recap walked the user BACK a step.
//
// These tests are over the pure machine on purpose: a rendering test can show that ONE path works,
// and what was wrong here was a property of ALL of them. The no-dead-end claim in particular is a
// statement about every state, which is only checkable by enumerating them.

const STATES: StepId[] = [...ORDER]

describe('the machine is a total, well-formed sequence', () => {
  it('declares five steps, with no duplicates', () => {
    expect(ORDER).toEqual(['name', 'import', 'essentials', 'try', 'ready'])
    expect(new Set(ORDER).size).toBe(ORDER.length)
  })

  it('every step has a title, a URL slug and a persisted spelling', () => {
    // A step missing any of the three is a step that cannot be announced, addressed or resumed.
    for (const id of STATES) {
      expect(TITLES[id], `${id} needs a title`).toBeTruthy()
      expect(SLUGS[id], `${id} needs a slug`).toBeTruthy()
      expect(STORED[id], `${id} needs a stored spelling`).toBeTruthy()
    }
  })

  it('slugs and stored spellings are injective, so neither mapping can collapse two steps', () => {
    expect(new Set(STATES.map((id) => SLUGS[id])).size).toBe(STATES.length)
    expect(new Set(STATES.map((id) => STORED[id])).size).toBe(STATES.length)
  })

  it('round-trips every step through its URL slug', () => {
    for (const id of STATES) expect(stepFromSlug(SLUGS[id])).toBe(id)
  })

  it('round-trips every step through its persisted spelling', () => {
    // 🔑 This is the rail against `STEPS` in `onboarding.py`. Before this change the stored
    // vocabulary named only three of the five steps, so `import` and `ready` had nowhere to be
    // recorded — which is why a reload on either of them lost the user's position.
    for (const id of STATES) expect(stepFromStored(STORED[id])).toBe(id)
  })

  it('ignores a slug that is not a step, rather than rendering nothing', () => {
    expect(stepFromSlug('nonsense')).toBeNull()
    expect(stepFromSlug('')).toBeNull()
    expect(stepFromSlug(undefined)).toBeNull()
    // A nested sub-path resolves on its FIRST segment, so a stale deep link still finds its step.
    expect(stepFromSlug('essentials/anything')).toBe('essentials')
  })

  it('treats a finished run as a fresh one rather than dropping it on the recap', () => {
    // `done` means a previous run completed. Someone re-entering asked to REDO setup, so resuming
    // at the recap would skip the very steps they came back for.
    expect(stepFromStored('done')).toBeNull()
    expect(stepFromStored(undefined)).toBeNull()
  })

  it('pathOf produces a hash path the router accepts', () => {
    for (const id of STATES) expect(pathOf(id)).toBe(`onboarding/${SLUGS[id]}`)
  })
})

describe('🔴 no dead ends: every state can be left, in at least one direction', () => {
  it('every step except the last has a next', () => {
    for (const id of STATES.slice(0, -1)) expect(nextOf(id), `${id} must advance`).not.toBeNull()
    expect(nextOf('ready'), 'the recap advances by LEAVING the flow, not to a step').toBeNull()
  })

  it('every step except the first has a previous', () => {
    for (const id of STATES.slice(1)) expect(previousOf(id), `${id} must go back`).not.toBeNull()
    expect(previousOf('name')).toBeNull()
  })

  it('🔑 every state has at least one edge out — forward, back, or both', () => {
    // The dead-end claim, stated over the whole state set. A state with neither a next nor a
    // previous would be one a user could reach and never leave.
    for (const id of STATES) {
      const edges = [nextOf(id), previousOf(id)].filter(Boolean)
      expect(edges.length, `${id} is a dead end`).toBeGreaterThan(0)
    }
  })

  it('every state is reachable from the first by walking forward', () => {
    const walked: StepId[] = ['name']
    let cur: StepId | null = 'name'
    while ((cur = nextOf(cur))) walked.push(cur)
    expect(walked).toEqual(STATES)
  })

  it('every state is reachable from the last by walking back', () => {
    const walked: StepId[] = ['ready']
    let cur: StepId | null = 'ready'
    while ((cur = previousOf(cur))) walked.unshift(cur)
    expect(walked).toEqual(STATES)
  })

  it('next and previous are exact inverses', () => {
    for (const id of STATES.slice(0, -1)) expect(previousOf(nextOf(id) as StepId)).toBe(id)
  })
})

describe('the high-water mark only ever rises', () => {
  it('furthestOf picks the later step, whichever way round it is asked', () => {
    expect(furthestOf('name', 'try')).toBe('try')
    expect(furthestOf('try', 'name')).toBe('try')
    expect(furthestOf('essentials', 'essentials')).toBe('essentials')
  })

  it('🔴 going back cannot lower it', () => {
    // The mark is what a reload resumes from. If walking back lowered it, a user who reviewed step 1
    // and then reloaded would be sent to step 1 — the resume would decay one step per visit.
    let mark: StepId = 'name'
    for (const id of STATES) mark = furthestOf(mark, id)
    expect(mark).toBe('ready')
    for (const id of [...STATES].reverse()) {
      mark = furthestOf(mark, id)
      expect(mark, `revisiting ${id} must not lower the mark`).toBe('ready')
    }
  })
})

describe('🔴 the name step is the one hard gate', () => {
  it('nothing past it is unlocked until it is passed', () => {
    for (const id of STATES.slice(1)) {
      expect(isUnlocked(id, 'ready', false), `${id} must be locked with no name`).toBe(false)
    }
  })

  it('the name step itself is always unlocked, so a user can always go fix it', () => {
    for (const reached of STATES) {
      expect(isUnlocked('name', reached, false)).toBe(true)
      expect(isUnlocked('name', reached, true)).toBe(true)
    }
  })

  it('a deep link past the gate resolves to the name step, not to the step it names', () => {
    // `finish()` commits the fallback name for a run that never asked, so admitting a deep link
    // here would rename someone who had not declined to give a name.
    for (const id of STATES) expect(resolveStep(id, 'ready', false)).toBe('name')
  })
})

describe('🔑 unlocked means "at or behind the mark" — not "already finished"', () => {
  it('every step at or behind the mark is unlocked', () => {
    // THE FIX FOR DEFECT 1. The old predicate was "this step is DONE", so a run standing on step 1
    // after reaching step 5 had no clickable row ahead of it and had to redo everything. It now
    // covers both a step already finished AND one a resume jumped over.
    for (const reached of STATES) {
      for (const id of STATES) {
        expect(
          isUnlocked(id, reached, true),
          `${id} with mark ${reached}`,
        ).toBe(stepIndex(id) <= stepIndex(reached))
      }
    }
  })

  it('a step ahead of the mark stays locked, so the flow cannot be skipped forward', () => {
    expect(isUnlocked('ready', 'import', true)).toBe(false)
    expect(resolveStep('ready', 'import', true)).toBe('import')
  })
})

describe('resolveStep is total — there is no input that renders nothing', () => {
  const marks = STATES
  const requests: (StepId | null)[] = [null, ...STATES]

  it('always answers with a real step', () => {
    for (const namePassed of [false, true]) {
      for (const reached of marks) {
        for (const requested of requests) {
          const got = resolveStep(requested, reached, namePassed)
          expect(STATES, `resolveStep(${requested}, ${reached}, ${namePassed})`).toContain(got)
        }
      }
    }
  })

  it('never answers with a step it would refuse to unlock', () => {
    // The self-consistency that makes the URL correction terminate: if the answer were itself
    // locked, the flow would correct the URL to a step the next render also refused.
    for (const namePassed of [false, true]) {
      for (const reached of marks) {
        for (const requested of requests) {
          const got = resolveStep(requested, reached, namePassed)
          expect(isUnlocked(got, reached, namePassed), `${got} from (${requested}, ${reached})`).toBe(true)
        }
      }
    }
  })

  it('honours the URL when it names an unlocked step — that is what refresh and Back rely on', () => {
    expect(resolveStep('import', 'try', true)).toBe('import')
    expect(resolveStep('try', 'try', true)).toBe('try')
    expect(resolveStep('name', 'ready', true)).toBe('name')
  })

  it('resumes at the mark when the URL names no step', () => {
    // `#/onboarding` with no sub-path is what the route guard's redirect produces, and what a user
    // typing the bare route produces. Both mean "wherever this run belongs".
    for (const reached of marks) expect(resolveStep(null, reached, true)).toBe(reached)
  })

  it('is idempotent — resolving its own answer changes nothing', () => {
    for (const reached of marks) {
      for (const requested of requests) {
        const once = resolveStep(requested, reached, true)
        expect(resolveStep(once, reached, true)).toBe(once)
      }
    }
  })
})
