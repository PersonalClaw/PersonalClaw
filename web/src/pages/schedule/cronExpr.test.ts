/** `cronExprInvalidReason` against croniter's own answers (#687).
 *
 *  The validator it guards is deliberately SOUND, not complete (see `cronExpr.ts`): it may pass an
 *  expression the server then refuses, but it must never refuse one the server would accept — a
 *  false refusal on a disabled Save button is a dead end the user cannot argue with.
 *
 *  So the load-bearing assertion is one-directional over `cronExprCorpus.json`, whose `valid`
 *  column is croniter's MEASURED verdict rather than a hand-written expectation.
 *  `tests/test_cron_expr_corpus.py` keeps that column honest against `schedule.validate_cron_expr`,
 *  which is the same function the create/update refusal calls — so a croniter upgrade that changed
 *  an answer reds there rather than quietly making these two disagree.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { cronExprInvalidReason } from './cronExpr'

type Case = { expr: string; valid: boolean; group: string }
const CASES: Case[] = JSON.parse(
  readFileSync(join(__dirname, 'cronExprCorpus.json'), 'utf8'),
).cases

describe('cronExprInvalidReason is SOUND against croniter', () => {
  it('never refuses an expression croniter accepts', () => {
    const wrongly = CASES.filter((c) => c.valid).flatMap((c) => {
      const reason = cronExprInvalidReason(c.expr)
      return reason ? [`${JSON.stringify(c.expr)} (${c.group}) → ${reason}`] : []
    })
    expect(wrongly, 'a false refusal disables Save on a working cron').toEqual([])
  })

  it('is not vacuously sound — it refuses most of what croniter refuses', () => {
    // The vacuity partner for the assertion above: a validator that returned `null` for everything
    // would satisfy soundness perfectly and gate nothing. Not `all`, because soundness buys the
    // right to pass an expression it cannot model (`5L`), and pinning 100% would forbid that.
    const invalid = CASES.filter((c) => !c.valid)
    const caught = invalid.filter((c) => cronExprInvalidReason(c.expr) !== null)
    expect(invalid.length).toBeGreaterThan(20)
    expect(caught.length / invalid.length).toBeGreaterThan(0.9)
  })

  it('covers both directions the old token count got wrong', () => {
    // 🔴 THE defect. `value.trim().split(/\s+/).length === 5` cleared a broken expression and
    // flagged a working one, in one line.
    expect('99 99 * * *'.trim().split(/\s+/).length, 'the old check saw five tokens').toBe(5)
    expect(cronExprInvalidReason('99 99 * * *')).toMatch(/minute/)
    expect('@daily'.trim().split(/\s+/).length, 'the old check saw one token').toBe(1)
    expect(cronExprInvalidReason('@daily')).toBeNull()
  })

  it('passes every cron preset the form offers', () => {
    // A preset button that paints the field red is the form contradicting itself.
    const presets = readFileSync(join(__dirname, 'scheduleMeta.ts'), 'utf8')
    const exprs = [...presets.matchAll(/expr:\s*'([^']+)'/g)].map((m) => m[1])
    expect(exprs.length).toBeGreaterThan(3)
    for (const expr of exprs) expect(cronExprInvalidReason(expr), expr).toBeNull()
  })

  it('names the field it rejected, so the message points somewhere', () => {
    expect(cronExprInvalidReason('* 24 * * *')).toMatch(/hour \(0-23\)/)
    expect(cronExprInvalidReason('* * 32 * *')).toMatch(/day-of-month \(1-31\)/)
    expect(cronExprInvalidReason('* * * 13 *')).toMatch(/month \(1-12\)/)
    expect(cronExprInvalidReason('* * * * 8')).toMatch(/day-of-week \(0-7\)/)
  })

  it('asks for a value rather than reporting an error on an empty field', () => {
    expect(cronExprInvalidReason('')).toMatch(/Enter a cron expression/)
    expect(cronExprInvalidReason('   ')).toMatch(/Enter a cron expression/)
  })

  it('lists the macros it accepts when one is misspelled', () => {
    const reason = cronExprInvalidReason('@fortnightly')
    expect(reason).toContain('@daily')
    expect(reason).toContain('@hourly')
  })
})
