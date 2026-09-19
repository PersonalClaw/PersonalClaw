/** Cron-expression validation for the schedule forms (#687).
 *
 *  🔴 WHAT THIS REPLACES. `CronField` decided validity by counting whitespace-separated tokens
 *  (`value.trim().split(/\s+/).length === 5`), which is wrong in BOTH directions: `'99 99 * * *'`
 *  has five tokens and croniter rejects it, `'@daily'` has one and croniter accepts it. The count
 *  therefore flagged a working expression and cleared a broken one, and nothing gated submit on it
 *  anyway (`TriggerCreatePage`'s `canSave` had no cron term and posted `body.cron` regardless).
 *
 *  **The contract, and it is deliberately one-sided.** The server is the authority — as of #687
 *  `POST`/`PUT /api/triggers` refuse an expression `schedule.validate_cron_expr` rejects. This
 *  module exists to say the same thing before the round trip, and it is SOUND rather than complete:
 *
 *      it reports a reason ⟹ croniter rejects the expression
 *
 *  It never claims the converse. Re-implementing croniter in TypeScript is how a validator drifts
 *  into refusing a legitimate expression, which on a disabled Save button is a dead end the user
 *  cannot argue with; an expression this module passes and the server refuses is a 400 that names
 *  the expression, which is recoverable. So anything it is not confident about it lets through, and
 *  `cronExpr.test.ts` pins the soundness direction over a corpus measured against croniter itself.
 */

/** Field bounds, in order, for the 5-field form (minute hour day-of-month month day-of-week). */
const FIELDS: Array<{ name: string; min: number; max: number; names?: readonly string[] }> = [
  { name: 'minute', min: 0, max: 59 },
  { name: 'hour', min: 0, max: 23 },
  { name: 'day-of-month', min: 1, max: 31 },
  {
    name: 'month',
    min: 1,
    max: 12,
    names: ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'],
  },
  // 0 AND 7 both mean Sunday — croniter accepts each, so a `max` of 6 would refuse `0 9 * * 7`.
  { name: 'day-of-week', min: 0, max: 7, names: ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'] },
]

/** Macros croniter's `is_valid` accepts, case-insensitively. `@reboot` is NOT one of them. */
const MACROS = ['@yearly', '@annually', '@monthly', '@weekly', '@daily', '@midnight', '@hourly']

/** One `a`, `a-b`, `*`, or a name — the part of a term before any `/step`. */
function rangeIsInvalid(part: string, field: (typeof FIELDS)[number]): boolean {
  if (part === '*') return false
  // `L`, `W` and `5#2` are croniter extensions for the day fields. Not modelled — passed through,
  // per this module's soundness contract, because guessing at them is how a valid expression gets
  // refused. The server has the real parser.
  if (/[LW#]/i.test(part)) return false
  const bounds = part.split('-')
  if (bounds.length > 2) return true
  const values: number[] = []
  for (const bound of bounds) {
    const lowered = bound.toLowerCase()
    const named = field.names ? field.names.indexOf(lowered) : -1
    if (named >= 0) {
      values.push(named + (field.name === 'month' ? 1 : 0))
      continue
    }
    if (!/^\d+$/.test(bound)) return true
    values.push(Number(bound))
  }
  for (const value of values) {
    if (value < field.min || value > field.max) return true
  }
  // `5-1` is a descending range croniter rejects. Only checked for plain numerics: a named range
  // spanning the week boundary (`fri-mon`) is croniter's business, not this module's.
  return values.length === 2 && bounds.every((b) => /^\d+$/.test(b)) && values[0] > values[1]
}

/** One comma-separated term — a range with an optional `/step` suffix, e.g. `1-5`, `MON`. */
function termIsInvalid(term: string, field: (typeof FIELDS)[number]): boolean {
  if (!term) return true
  const [range, step, ...rest] = term.split('/')
  if (rest.length) return true
  if (step !== undefined && (!/^\d+$/.test(step) || Number(step) === 0)) return true
  return rangeIsInvalid(range, field)
}

/** Why `expr` is definitely not a cron expression, or `null` when it may well be.
 *
 *  `null` is NOT a claim of validity — see the module docstring. The sentence is written for the
 *  form field, so it says what to type rather than which token failed a regex.
 */
export function cronExprInvalidReason(expr: string): string | null {
  const value = (expr || '').trim()
  if (!value) return 'Enter a cron expression, e.g. 0 9 * * * — or a macro like @daily.'
  if (value.startsWith('@')) {
    return MACROS.includes(value.toLowerCase())
      ? null
      : `${value} is not a cron macro. Use one of ${MACROS.join(', ')}, or five fields.`
  }
  const parts = value.split(/\s+/)
  // Six fields is croniter's seconds form and it accepts it, so the count check has to allow it —
  // the old five-only count is half of why `@daily` and `0 9 * * * *` both read as broken.
  if (parts.length !== 5 && parts.length !== 6) {
    return 'Cron needs five fields: minute hour day-of-month month day-of-week.'
  }
  // 🔴 MEASURED against croniter, not assumed: the sixth field is TRAILING seconds, not a leading
  // one and not a year. `'0 9 * * * 30'` is valid (30s) and `'0 9 * * * 2026'` is not, so a
  // leading-seconds model would have refused a legitimate expression — the one direction this
  // module promises never to be wrong in.
  const fields = parts.length === 6 ? [...FIELDS, { name: 'second', min: 0, max: 59 }] : FIELDS
  for (let i = 0; i < parts.length; i++) {
    const field = fields[i]
    if (parts[i].split(',').some((term) => termIsInvalid(term, field))) {
      return `${parts[i]} is not a valid ${field.name} (${field.min}-${field.max}).`
    }
  }
  return null
}
