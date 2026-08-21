import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── A loop action that silently did not happen ─────────────────────────────────────────────────────
//
// Third adopter of the data-driven write contract (`tools/toggleFailureReported` named it,
// `knowledge/knowledgeWriteReported` extracted `app/reportingWrite`). Every loop action was
// swallowed, and every one of them re-renders from a refetch rather than a local flip:
//
//     await api.uLoopAction(id, action).catch(() => {})     // swallowed
//     invalidateKeys('loops'); refresh()                   // ran REGARDLESS
//
// So a failure left NOTHING: the loop did not pause, no message appeared, and the refetch re-rendered
// the same status — the click read as "nothing happened, twice". A silent `stop` is the shape whose
// failure a user ACTS on, because their next assumption is that the loop is no longer running.
//
// 🔑 TWO SITES DID MORE THAN SWALLOW — they performed the step that depended on the write:
//
//   `DesignCockpitPage.sendNudge`   cleared `nudgeText` and closed the panel, so a failure DESTROYED
//                                   the message the user typed and did not deliver it. `nudgeText`
//                                   lives only in that component, so nothing was left to retry with.
//   `LoopSection.routeCreated`      navigated to the cockpit whether or not the ready→planning kick
//                                   landed. Its own comment says the kick must happen "BEFORE
//                                   navigating, or the by-id resume shows the cockpit's Start button
//                                   and silently skips the walkthrough the rigor earned" — which is
//                                   precisely what a swallowed failure produced.
//
// 🪤 The nudge KEEPS its text on failure; navigation deliberately still HAPPENS. Those look
// inconsistent and are not: the loop in the second case was really created (that is what `onCreated`
// means) and only the status kick failed, so stranding the user on the composer with a loop they
// cannot reach is worse than landing them on it with an explanation. Reporting is what removes the
// silence the comment feared. Both decisions are pinned below so a later pass does not "even them up".

const F = (rel: string) => readFileSync(join(process.cwd(), 'src', 'pages', rel), 'utf8')
const strip = (s: string) =>
  s.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const COCKPIT = F('loops/DesignCockpitPage.tsx')
const LIST = F('loops/LoopsListPage.tsx')
const SECTION = F('loop/LoopSection.tsx')

/** file → the loop writes it owns. */
const WRITES: Array<[string, string, string[]]> = [
  ['loops/DesignCockpitPage.tsx', COCKPIT, ['uLoopAction', 'uLoopNudge', 'updateULoop']],
  ['loops/LoopsListPage.tsx', LIST, ['uLoopAction']],
  ['loop/LoopSection.tsx', SECTION, ['uLoopAction', 'uLoopPlanStart']],
]

describe('a loop action that fails tells the user', () => {
  it('all three files use the SHARED reporter and keep no local copy', () => {
    for (const [name, src] of WRITES.map(([n, s]) => [n, strip(s)] as const)) {
      expect(src, `${name} must import the shared contract`).toMatch(
        /import \{ reportingWrite \} from '\.\.\/\.\.\/app\/reportingWrite'/,
      )
      const local = [...src.matchAll(/(function|const)\s+reportingWrite\b\s*[=(]/g)]
      expect(local.length, `${name}: a page-local copy would shadow the shared one`).toBe(0)
    }
  })

  it('no loop write swallows its rejection — the ratchet', () => {
    const offenders: string[] = []
    for (const [name, raw, calls] of WRITES) {
      const scan = strip(raw).replace(/=>/g, '⇒')
      for (const call of calls) {
        for (const m of scan.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
          if (/\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/.test(scan.slice(m.index!, m.index! + 200))) {
            offenders.push(`${name}:${call}`)
          }
        }
      }
    }
    expect(offenders).toEqual([])
  })

  it('the ratchet is not vacuous — it finds every write it guards', () => {
    for (const [name, raw, calls] of WRITES) {
      for (const call of calls) {
        expect(strip(raw), `${name} should still perform api.${call}`).toContain(`api.${call}(`)
      }
    }
    // Seven call sites across the three files: 4 cockpit, 1 list, 2 section.
    const total = WRITES.reduce((n, [, raw]) => n + [...strip(raw).matchAll(/reportingWrite\(/g)].length, 0)
    expect(total, 'every write routed through the reporter').toBe(7)
  })

  it('EVERY write in the two refetching files is gated — not just the ones that already are', () => {
    // 🪤 The first version of this test iterated `/if \(!\(await reportingWrite\(/g` and asked whether
    // each MATCH had a guard. A mutation that DROPPED the guard from one site passed it, because a
    // site without the pattern is simply never visited — the sweep only inspected the sites that
    // already complied. Count instead: in these two files every reporter call must be the guarded
    // form, so an ungated one shows up as a mismatch rather than as an absence.
    for (const [name, raw] of [['cockpit', COCKPIT], ['list', LIST]] as const) {
      const src = strip(raw)
      const all = [...src.matchAll(/await reportingWrite\(/g)].length
      const gated = [...src.matchAll(/if \(!\(await reportingWrite\(/g)].length
      expect(all, `${name}: the sweep must find its writes`).toBeGreaterThan(0)
      expect(gated, `${name}: every write must gate what follows it`).toBe(all)
    }
    // And the guard really does precede the refetch at each one.
    for (const [name, raw] of [['cockpit', COCKPIT], ['list', LIST]] as const) {
      const src = strip(raw)
      for (const m of src.matchAll(/if \(!\(await reportingWrite\(/g)) {
        const after = src.slice(m.index!, m.index! + 460)
        expect(after, `${name}: the guard must return`).toMatch(/\)\)\) return/)
        const guard = after.indexOf(')) return')
        const refetch = after.search(/loadLoop\(|loadTokens\(|invalidateKeys\(|refresh\(/)
        if (refetch > -1) expect(guard, `${name}: guard must precede the refetch`).toBeLessThan(refetch)
      }
    }
  })

  it('a failed nudge KEEPS the message and the panel open', () => {
    // The decision, pinned: clearing on failure destroyed the only copy of what the user typed.
    const body = strip(COCKPIT)
    const at = body.indexOf('async function sendNudge()')
    expect(at).toBeGreaterThan(-1)
    const fn = body.slice(at, body.indexOf('\n  }', at))
    const guard = fn.indexOf(')) return')
    const clear = fn.indexOf("setNudgeText('')")
    expect(guard, 'the nudge is reported').toBeGreaterThan(-1)
    expect(clear, 'and still cleared on success').toBeGreaterThan(-1)
    expect(guard, 'but only AFTER the write landed').toBeLessThan(clear)
    expect(fn, 'the panel closes on success too').toContain('setNudgeOpen(false)')
  })

  it('the created-loop route still NAVIGATES, deliberately', () => {
    // The counterpart decision. Stranding the user on the composer with a created loop they cannot
    // reach would be worse than landing them on it with the failure explained. If a later pass adds
    // a guard here, this test is the place to argue it — not a silent change.
    const body = strip(SECTION)
    const kick = body.lastIndexOf('reportingWrite(')
    const nav = body.indexOf('navigate(kind === ', kick)
    expect(kick, 'the kick is reported').toBeGreaterThan(-1)
    expect(nav, 'and navigation follows it').toBeGreaterThan(kick)
    const between = body.slice(kick, nav)
    expect(between, 'no early return may gate the navigation').not.toMatch(/\breturn\b/)
  })

  it('every message names the action or the subject', () => {
    expect(COCKPIT).toContain('`${a} this loop`')
    expect(LIST).toContain('`${action} this loop`')
    expect(COCKPIT).toContain("'send that nudge'")
    expect(COCKPIT).toContain('`apply the ${scale} colour`')
    expect(COCKPIT).toContain('`set ${path}`')
    expect(SECTION).toContain("'start planning for this loop'")
  })

  it('the writes are still data-driven — the premise', () => {
    // If a later pass added an optimistic flip, the failure shape changes to "a control showing a
    // value the server refused" and the remedy changes with it.
    const src = strip(COCKPIT)
    for (const call of ['uLoopAction', 'updateULoop']) {
      const at = src.indexOf(`api.${call}(`)
      const before = src.slice(Math.max(0, at - 200), at)
      expect(before, `${call} gained an optimistic flip`).not.toMatch(/setStatus\(|setLoop\(/)
    }
  })
})
