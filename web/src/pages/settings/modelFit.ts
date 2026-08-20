// Will this model run on THIS machine? (LMMV-8)
//
// The verdict itself is the backend's (`local_models/fit.py`) — this module only holds the two
// decisions a renderer must not re-derive per call site: which semantic token a verdict wears,
// and WHEN the browse filter is allowed to remove a row.
//
// 🔑 THE LOAD-BEARING RULE IS THE SECOND ONE. `hide_unrunnable` is a user preference about a
// MEASURED budget. On a host we could not measure, honouring it would delete the catalog for a
// reason we invented — the worst failure this feature can have, and strictly worse than showing
// a model that turns out not to fit. So `budgetKnown` gates every hide, and the gate lives here
// (one function, one test) rather than as an `&&` inside a JSX expression.
import type { AvailableModel, HostModelFit, ModelFitVerdict } from '../../lib/api'

/** The tone token per verdict, in the vocabulary this panel already speaks: `--color-success` is
 *  the downloaded check, `--color-danger` is the per-row error line. 'unknown' is deliberately
 *  NEUTRAL — `residency.pressureTone` makes the same call, giving an unreadable host the neutral
 *  tone rather than "a warning colour on numbers nobody measured". */
export const FIT_TONE: Record<ModelFitVerdict, string> = {
  green: 'var(--color-success)',
  yellow: 'var(--color-warning)',
  red: 'var(--color-danger)',
  unknown: 'var(--color-outline-variant)',
}

/** The chip's visible text. Short because it sits in a 260px row beside the model name; the full
 *  sentence is `fit_reason`, which becomes the chip's accessible name and title. */
export const FIT_LABEL: Record<ModelFitVerdict, string> = {
  green: 'Fits',
  yellow: 'Tight',
  red: "Won't fit",
  unknown: 'Fit unknown',
}

/** The chip's accessible name: the VERDICT first, then the backend's `fit_reason`.
 *
 *  Both halves are required. `role="img"` replaces the chip's visible text with this string, so
 *  dropping the verdict would leave a screen reader with a bare measurement ("needs 9.5 GB, budget
 *  is 6.0 GB") and no statement of what that means. Dropping the reason would leave the chip named
 *  by its label alone, which is barely better than being named by its colour. A verdict with no
 *  reason falls back to `fit_need_mb`, then to the label — never to nothing. */
export function fitDescription(m: AvailableModel): string {
  const verdict = m.fit
  if (!verdict) return ''
  const reason = m.fit_reason
    || (m.fit_need_mb ? `needs about ${m.fit_need_mb} MB on this device` : '')
  return reason ? `${FIT_LABEL[verdict]} — ${reason}` : FIT_LABEL[verdict]
}

/** Did we actually measure this machine?
 *
 *  Three ways to fail, all one answer: no `fit` object at all (a gateway predating the probe),
 *  `measured: false`, or a null `budget_mb`.
 *
 *  A budget of **0 is measured, not unknown** — it is the backend's real answer for a machine
 *  smaller than the reserve ("nothing fits"), which `usable_memory_bytes` returns distinctly from
 *  `None`. Treating it as unknown collapsed a legitimate value into the absent marker and silently
 *  disabled the filter on exactly the machines that need it most: driving a 48 GB host with the
 *  reserve at 64 GB chipped all six models "Won't fit" and then hid none of them. `measured` and
 *  the `typeof` check already separate "we could not tell" from "we could"; the magnitude must not
 *  be asked to carry that meaning too. */
export function budgetKnown(host?: HostModelFit | null): boolean {
  if (!host || !host.measured) return false
  return typeof host.budget_mb === 'number' && host.budget_mb >= 0
}

/** The host budget as the rows carry it (denormalized by `api.modelsAvailable`).
 *
 *  Reads the FIRST row that has one instead of requiring a prop: the top-level fact is identical
 *  on every row of the response, and this component is handed only rows by its parent card. */
export function hostFitOf(rows: AvailableModel[]): HostModelFit | undefined {
  return rows.find((r) => r.host_fit)?.host_fit
}

/** Rows the device cannot run, and therefore what the filter is even able to hide.
 *  Empty whenever the budget is unknown — see the module note. */
export function unrunnable(rows: AvailableModel[], host?: HostModelFit | null): AvailableModel[] {
  if (!budgetKnown(host)) return []
  return rows.filter((m) => m.fit === 'red')
}

/** Apply the browse filter. Returns `rows` UNCHANGED — same array identity — whenever hiding is
 *  off or the budget is unknown, so the "hides nothing" case is not merely equal-length. */
export function filterByFit(
  rows: AvailableModel[], host: HostModelFit | null | undefined, hide: boolean,
): AvailableModel[] {
  if (!hide || !budgetKnown(host)) return rows
  return rows.filter((m) => m.fit !== 'red')
}

/** The size a row states, plus the family median when that is a genuinely different fact.
 *
 *  🪤 COHERENCE CALL, and it is decided by WHERE THE VERDICT COMES FROM. A row can hold two sizes:
 *  its own `size_mb`, and `quoted_size_mb` — the MEDIAN across the family's variants. The backend
 *  judges the verdict on the row's OWN size (falling back to the quote only for a row that
 *  publishes none), because judging every variant by the median made a 16 GB model read yellow on
 *  an 8 GB machine — the promise-a-fit-that-OOMs this feature exists to prevent.
 *
 *  So THE ROW STATES ITS OWN SIZE: that is the number its chip was computed from, and a row reading
 *  "6000 MB · Won't fit" beside a verdict judged on 16000 MB would be self-contradictory — the one
 *  outcome worse than showing no chip at all. The quote is still worth showing, but only ever
 *  LABELLED as the family's, never as this tag's bytes. A row with no size of its own has just one
 *  number, which is also the one the verdict used, so it is simply stated.
 *
 *  Rounded because the wire sends floats (`quoted_size_mb: 6000.0`). */
export function statedSizeMb(m: AvailableModel): { mb: number; familyMedianMb: number | null } {
  const own = Math.round(m.size_mb ?? (m.size ? m.size / 1024 / 1024 : 0))
  const quoted = m.quoted_size_mb ? Math.round(m.quoted_size_mb) : 0
  if (!own) return { mb: quoted, familyMedianMb: null }
  return { mb: own, familyMedianMb: quoted && quoted !== own ? quoted : null }
}
