import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// ── Cancelling running work is not a best-effort write ───────────────────────────────────────
//
// `SystemWidget`'s background-agent monitor is shell-level: it is one click from every surface in
// the app, and the fleet it lists is spawned by crons, goal loops, Slack and `subagent_run`. Its two
// controls both acted on that fleet and both swallowed the failure with `catch { /* */ }` — an empty
// comment, which is the one shape in this family that states no reason at all. Twenty-six of the
// twenty-eight empty catches around a write in `web/src` say WHY they are empty ("keep the draft on
// failure", "best-effort; preview still loads", "surfaced by reload", "never break the host
// surface"); these two said nothing.
//
// 🔑 WHY THE SILENCE IS TOTAL HERE, and not merely terse. This is the family's *data-driven* shape:
// the row's state comes from the refetch, not from a local flip. So a failed cancel left NOTHING to
// look at — `setBusy(null)` stopped the row's spinner-disable, `load()` ran unconditionally, and the
// same agent came back still spinning. That is pixel-identical to a click that never registered,
// which invites a second click on work the gateway may already be tearing down.
//
// Two decisions asserted below, because a later pass could plausibly normalise either away:
//
//   · the message NAMES the agent. The fleet is a list; "couldn't cancel that agent" identifies
//     nothing when three are running, so the task's first line is threaded from the row where it is
//     already in scope. Same call, for the same reason, as the task-comment delete.
//   · `load()` is GATED on success. Refetching after a failed cancel re-renders the identical
//     running row, which reads as "nothing happened, twice" — the rule this family recorded for
//     data-driven controls. The 4s poll is still running while the card is open, so nothing can go
//     stale either way.
//
// 🪤 SCOPE: this rail is deliberately about the two sites it measured, not a tree-wide sweep. The
// derived form — "an empty catch around an `api.*` write must state a reason" — has a population of
// 28 and would currently also flag four `/* ignore */` sites that a separate open change resolves.
// Writing it before those land would either red on arrival or need an exemption set that the other
// change cannot remove. Census recorded in `.validation/ux/PRODUCT-POLISH.md` §4 so it can be
// written once, cleanly, rather than half-written twice.

const SRC = join(process.cwd(), 'src')
const widget = readFileSync(join(SRC, 'ui/SystemWidget.tsx'), 'utf8')
/** 🪤 Comments stripped before asserting on code: this file's own header quotes the empty
 *  `catch { /* *\/ }` it removed, and a rail that reads its own explanation as a program is the
 *  most common way one of these goes false-green in this tree. */
const code = widget.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the background-agent monitor reports a failed cancel or clear', () => {
  it('parses its subject (a rail that reads nothing asserts nothing)', () => {
    expect(widget.length, 'ui/SystemWidget.tsx did not read').toBeGreaterThan(2_000)
    expect(code, 'the RunningAgents monitor is still here').toMatch(/function RunningAgents\(/)
    expect(code, 'and it still calls both writes').toMatch(/api\.cancelSpawnedAgent\(/)
    expect(code).toMatch(/api\.clearSpawnedAgents\(/)
  })

  it('neither write is swallowed', () => {
    // The precise defect: an empty catch. Asserted on comment-stripped source so restoring the old
    // line with a nicer comment cannot pass.
    const empty = [...code.matchAll(/catch\s*(?:\([^)]*\))?\s*\{\s*\}/g)]
    expect(
      empty.length,
      'an empty catch around a write in this file tells the user nothing — the row simply comes ' +
        'back still running, which is what a click that never landed looks like',
    ).toBe(0)
    expect(code).toMatch(/reportingWrite\([\s\S]{0,80}api\.cancelSpawnedAgent/)
    expect(code).toMatch(/reportingWrite\([\s\S]{0,80}api\.clearSpawnedAgents/)
  })

  it('the cancel message names WHICH agent, from the row that already has it', () => {
    // A fleet is a list. Without this the message is ambiguous across every running row.
    expect(code, 'the row threads its own task line into the handler').toMatch(
      /cancel\(a\.id,\s*firstLine\(a\.task\)\)/,
    )
    expect(code, 'and the handler spends it on the sentence').toMatch(
      /const cancel = async \(id: string, task: string\)/,
    )
    expect(code).toMatch(/reportingWrite\(`cancel [^`]*\$\{task\}/)
  })

  it('the refetch is gated on the write landing', () => {
    // Both handlers, and NOT a bare `load()` after the await — that is the shape being fixed.
    const gated = [...code.matchAll(/const ok = await reportingWrite\([\s\S]{0,140}?if \(ok\) load\(\)/g)]
    expect(
      gated.length,
      'both cancel and clear must skip the refetch on failure — re-rendering the same running row ' +
        'reads as "nothing happened, twice"',
    ).toBe(2)
  })

  it('the poll that keeps the list fresh is untouched', () => {
    // The reason gating the refetch is safe. If this ever goes, the gate above needs revisiting.
    expect(code, 'still polls every 4s while the card is open').toMatch(
      /window\.setInterval\(load, 4000\)/,
    )
  })
})
