import { describe, it, expect, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { setOnboardingExit, peekOnboardingExit, clearOnboardingExit } from './exitTo'

// ── OU-3: the Settings deep-link out of onboarding actually leaves onboarding ────────────────────
//
// `App.tsx`'s guard pulls every route back to `#/onboarding` while `onboarded` is false. That makes
// the obvious implementation of a "deep-link into Settings" inert: set the hash and the guard
// bounces you before paint. The less obvious one is worse — commit the name first and THEN
// navigate, and you are racing the same guard, which fires on the `onboarded` flip and sends the
// user to the dashboard. Whichever navigation wins that race is a coin toss, and it fails on the
// one surface where a stuck user needs the exit to work. So the destination is handed to the guard.
//
// ── The re-entrancy rail below exists because the first version SHIPPED BROKEN and green. ───────
//
// v1 cleared the destination on read. Every unit test passed. Driven against a real gateway, the
// deep-link landed on `#/dashboard` instead of `#/settings/providers`, every single time.
//
// Cause: the guard effect is RE-ENTRANT. `navigate` sets `location.hash`; `route` only updates when
// the browser's async `hashchange` fires. Between those two moments any other App re-render runs
// the effect again with `onboarded === true` and a stale `route === 'onboarding'` — so the exit
// branch fired twice. Run one read the destination and navigated correctly; run two read `''`,
// took the `|| 'dashboard'` default, and overwrote the hash. The guard silently undid itself.
//
// The tests could not see it because they asserted the read-and-clear contract, which was the
// WRONG contract. What a caller of a re-entrant guard needs is idempotence, so that is what is
// pinned now: N reads resolve to the same destination, and clearing is a separate, explicit act
// the guard performs only after the route has provably left onboarding.

beforeEach(() => { clearOnboardingExit() })

describe('the pending onboarding exit destination', () => {
  it('is empty by default, so the guard keeps its dashboard behaviour', () => {
    expect(peekOnboardingExit()).toBe('')
  })

  it('round-trips a hash path', () => {
    setOnboardingExit('settings/providers')
    expect(peekOnboardingExit()).toBe('settings/providers')
  })

  it('READING IS IDEMPOTENT — the guard may run any number of times before `route` catches up', () => {
    // The regression rail. With a consuming read this is the assertion that goes red, and it is
    // the only one that distinguishes "navigated correctly once" from "navigated correctly and
    // then overwrote it with the default".
    setOnboardingExit('settings/providers')
    for (let i = 0; i < 5; i++) {
      expect(peekOnboardingExit(), `guard run ${i + 1} must resolve the SAME destination`).toBe('settings/providers')
      // What the guard actually computes on each run — never the default while one is pending.
      expect(peekOnboardingExit() || 'dashboard').toBe('settings/providers')
    }
  })

  it('clearing is explicit, and only then does the default come back', () => {
    setOnboardingExit('settings/doctor')
    clearOnboardingExit()
    expect(peekOnboardingExit()).toBe('')
    expect(peekOnboardingExit() || 'dashboard').toBe('dashboard')
  })

  it('normalises a leading `#/` or `/` so callers can pass either spelling', () => {
    setOnboardingExit('#/knowledge/item/abc')
    expect(peekOnboardingExit()).toBe('knowledge/item/abc')
    setOnboardingExit('/loops/lp-1')
    expect(peekOnboardingExit()).toBe('loops/lp-1')
  })
})

describe('the guard consumes it', () => {
  const app = () => readFileSync(join(process.cwd(), 'src/app/App.tsx'), 'utf8')

  it('the exit branch PEEKS, defaulting to the dashboard', () => {
    // Asserted on the guard's source because the branch only runs inside the full App tree with a
    // live identity provider; a mechanism with no reader is the other defect this pins.
    //
    // 🔴 It reaches the branch having already RETURNED on `wantsSetup`, which is why no onboarding
    // predicate appears in the condition at all. The flat `else if` chain this replaced was one of
    // two effects, and the other one — the unknown-hash corrector — overwrote this navigation on the
    // handoff commit (#3506). That is the second time this destination was measured landing on
    // `#/dashboard`; see `routeCasesCoverRoutable.test.ts` for the ordering rail.
    //
    // `wantsSetup`, not `onboarded`, is what the branch above returns on: a DELIBERATE re-run
    // (Settings → Account) is an onboarded user who should stay in the flow — see `onboarding/rerun.ts`.
    expect(app()).toMatch(
      /if \(route === 'onboarding'\) \{ navigate\(peekOnboardingExit\(\) \|\| 'dashboard'\); return \}/)
  })

  it('the exit branch must NOT consume — a clearing read reintroduces the measured bug', () => {
    expect(app()).not.toMatch(/navigate\(takeOnboardingExit\(\)/)
  })

  it('clearing happens on a LATER branch, once the route has left onboarding', () => {
    // Last statement of the effect, reachable only once every route decision above declined — i.e.
    // setup is not wanted, the route is not `#/onboarding`, and the shell can render it. Pinned with
    // the dependency list so a new guard input cannot be added without this rail being re-read.
    expect(app()).toMatch(
      /\n {4}clearOnboardingExit\(\)\n {2}\}, \[identityStatus, onboarded, setupRerun, route, sub, navigate\]\)/)
  })

  it('the redirect INTO onboarding still holds the gate', () => {
    // `wantsSetup`, not `!onboarded`: a DELIBERATE re-run (Settings → Account) is an onboarded user
    // who belongs in the flow, so the request is a second input to this guard rather than a
    // navigation racing it — see `onboarding/rerun.ts`.
    expect(app()).toMatch(/const wantsSetup = !onboarded \|\| setupRerun/)
    expect(app()).toMatch(/if \(wantsSetup\) \{/)
    expect(app()).toMatch(/if \(route !== 'onboarding'\) \{/)
    expect(app()).toMatch(/navigate\('onboarding', \{ replace: !onboarded \}\)/)
  })

  it('🔴 the redirect REPLACES for a first run, or the browser Back button is a bounce', () => {
    // Measured on a fresh home with a push: Back left `#/onboarding`, this effect fired on the new
    // route and pushed `#/onboarding` straight back — `history.length` pinned at 3 and the user
    // could neither leave nor go back. `replace: !onboarded` is a push only for a deliberate re-run,
    // where the user came from a real page Back should return them to.
    expect(app(), 'a bare push would recreate the bounce').not.toMatch(/navigate\('onboarding'\)\s*$/m)
  })

  it('the redirect DEFERS the route the user asked for, so it is not a silent hijack', () => {
    // A fresh home pulls every route here. Recording the requested route turns the redirect into a
    // promise the flow names on screen and `finish()` keeps by landing there.
    expect(app()).toMatch(/setOnboardingExit\(\[route, sub\]\.filter\(Boolean\)\.join\('\/'\)\)/)
  })

  it('🔴 a pending destination survives a run that happens while setup is still wanted (#3506)', () => {
    // `exitTo` sets the destination and THEN commits the name, so the effect can run in between with
    // `wantsSetup` still true and `route === 'onboarding'`. That run must do nothing at all: clearing
    // there would drop the destination before the branch that reads it ever ran. The `wantsSetup`
    // branch RETURNS, and that return is what makes `clearOnboardingExit()` unreachable in that
    // state — a structural guarantee rather than a predicate two effects have to agree on, which is
    // the shape that failed twice (see `routeCasesCoverRoutable.test.ts`).
    const src = app()
    const effect = src.slice(
      src.indexOf("if (identityStatus !== 'ready') return"),
      src.indexOf('[identityStatus, onboarded, setupRerun, route, sub, navigate])'))
    const wantsSetup = effect.indexOf('if (wantsSetup) {')
    // 🪤 The STATEMENT, anchored at its own indentation — `indexOf('clearOnboardingExit()')` finds
    // the prose mention in the branch's own comment first and truncates the slice before the return.
    const clearing = effect.search(/\n {4}clearOnboardingExit\(\)/)
    expect(wantsSetup, 'the wantsSetup branch must exist').toBeGreaterThan(-1)
    expect(clearing, 'clearing must come after it').toBeGreaterThan(wantsSetup)
    expect(effect.slice(wantsSetup, clearing), 'the branch must return, or clearing is reachable')
      .toMatch(/\n {6}return\n {4}\}/)
  })

  it('the flow hands the destination over and then finishes, in that order', () => {
    // Reversed, the name commit would fire the guard before the destination was set and the
    // user would land on the dashboard — the silent version of a broken deep-link.
    const src = readFileSync(join(process.cwd(), 'src/app/Onboarding.tsx'), 'utf8')
    const body = src.match(/function exitTo\(path: string\) \{[\s\S]*?\n  \}/)?.[0] ?? ''
    expect(body, 'exitTo must exist').toContain('setOnboardingExit(path)')
    expect(body.indexOf('setOnboardingExit')).toBeLessThan(body.indexOf('finish()'))
  })
})
