import type { CalibrationBucket, DecisionJournalView, DecisionRow } from '../../lib/api'

// ── The calibration strip (PROACTIVE-ASSISTANT §2.5) ──
//
// This strip is a claim about how good the USER's own judgement is, which makes it the one
// panel in the app where inventing a number is worst. Three states, and they exist because
// each is a different sentence a person needs to hear:
//
//   'calibrated' — enough resolved decisions in some domain to report a rate.
//   'too-few'    — resolved decisions exist, but every domain is under the threshold. A rate
//                  drawn off three points is not a weak claim, it is a false one.
//   'no-data'    — nothing resolved has a calibratable grade, so there is no rate at all.
//
// 🪤 The trap this shape exists to stop: rendering 'too-few' and 'no-data' as an empty chart, a
// 0%, or a flat line. All three READ as "perfectly calibrated" — the strongest possible claim —
// when the truth is "nobody knows yet". That is the failure `optimize.SCORE_UNSCORED` (report
// `unscored`, never `0.0`) and `learningMeta.evidenceLabel` (report `ungraded`, never a
// substituted grade) already have house answers for; this follows them.
export type CalibrationState = 'calibrated' | 'too-few' | 'no-data'

/** Which of the three things the strip is allowed to say.
 *
 *  Reads the backend's own `count_honest`, never n against a locally-spelled threshold: the
 *  server applies `decisions.CALIBRATION_MIN_N` and a second copy here could caveat a bucket
 *  the backend had already called honest. */
export function calibrationState(buckets: Record<string, CalibrationBucket>): CalibrationState {
  const rows = Object.values(buckets || {})
  if (rows.length === 0) return 'no-data'
  return rows.some((b) => b.count_honest) ? 'calibrated' : 'too-few'
}

/** The strip's headline sentence. One per state, and deliberately never a number in the two
 *  states that have no number to report.
 *
 *  In `no-data` the wording separates "you have not logged any decisions" from "you have logged
 *  some and resolved none" — same state, different fact, and telling someone there is nothing to
 *  calibrate when they have eight decisions pending would read as the feature being broken. */
export function calibrationCaption(view: Pick<DecisionJournalView, 'calibration' | 'calibration_min_n' | 'decisions'>): string {
  const state = calibrationState(view.calibration)
  const minN = view.calibration_min_n
  if (state === 'calibrated') {
    const honest = Object.values(view.calibration).filter((b) => b.count_honest).length
    return `Calibration across ${honest} domain${honest === 1 ? '' : 's'} with at least ${minN} resolved decisions.`
  }
  if (state === 'too-few') {
    const n = Object.values(view.calibration).reduce((t, b) => t + b.n, 0)
    return `${n} resolved decision${n === 1 ? '' : 's'} — too few to mean much. No domain has reached ${minN} yet, so no rate is shown.`
  }
  const pending = (view.decisions || []).filter((d) => d.status === 'pending').length
  if (pending > 0) {
    return `${pending} decision${pending === 1 ? '' : 's'} still open and none resolved yet — calibration starts once outcomes come in.`
  }
  return 'No decisions logged yet. Calibration appears once you have logged some and recorded how they turned out.'
}

/** One domain row. A bucket the backend called dishonest reports its COUNT and its distance from
 *  the threshold — never `as_expected_rate`, which is present in the payload precisely so the
 *  view can choose not to draw it. */
export function bucketLabel(b: CalibrationBucket, minN: number): string {
  if (!b.count_honest) {
    return `${b.n} of ${minN} decisions — too few to mean much`
  }
  const rate = Math.round((b.as_expected_rate ?? 0) * 100)
  const conf = b.mean_confidence == null ? 'no stated confidence' : `${Math.round(b.mean_confidence * 100)}% mean confidence`
  return `${b.n} decisions · ${rate}% resolved as expected · ${conf}`
}

/** Is this bucket allowed to draw a proportional bar at all? The one gate the strip's geometry
 *  asks, kept beside the label so a bar can never appear under a "too few" caption. */
export function bucketPlottable(b: CalibrationBucket): boolean {
  return b.count_honest && b.as_expected_rate != null
}

// ── Pending decisions (§5.3: horizon countdown + overdue flag + stale-pending) ──
//
// 'stale' outranks 'overdue' because it carries strictly more: the horizon passed AND the
// deferral cap was spent, so NO reminder is coming. Rendering that as merely overdue would
// promise a review card that will never arrive.
export type PendingState = 'stale' | 'overdue' | 'counting'

export function pendingState(d: DecisionRow): PendingState {
  if (d.stale_pending) return 'stale'
  if (d.overdue) return 'overdue'
  return 'counting'
}

/** The countdown. Whole days, because a decision horizon is a date and rendering hours on it
 *  would imply a precision the user never expressed. An unparseable horizon returns the empty
 *  string rather than a `NaN days` — the same rule as the strip. */
export function horizonLabel(d: DecisionRow, now: Date = new Date()): string {
  const at = Date.parse(d.review_horizon || '')
  if (!Number.isFinite(at)) return ''
  const days = Math.round((at - now.getTime()) / 86400000)
  const state = pendingState(d)
  const ago = `${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'}`
  const times = `deferred ${d.deferrals} time${d.deferrals === 1 ? '' : 's'}`
  if (state === 'stale') {
    // 🔴 CAUGHT BY DRIVING IT: a stale-pending decision's horizon is usually in the FUTURE, not
    // the past — each `too_early` deferral pushes it out by half the original span, so the row
    // goes stale on the deferral COUNT while its date still sits ahead. The first version read
    // the sign off `Math.abs` and announced "Review lapsed 67 days ago" for a horizon 67 days
    // away. What is actually true in both directions is that no reminder is coming, so that is
    // what it says, and the date is only described as lapsed when it has genuinely lapsed.
    return days < 0
      ? `Review lapsed ${ago} ago · ${times}, no reminder left`
      : `${times[0].toUpperCase()}${times.slice(1)} — no reminder left, so nothing will bring this back`
  }
  if (state === 'overdue') return `Review was due ${ago} ago`
  if (days === 0) return 'Review due today'
  return `Review in ${ago}`
}

/** How the decision turned out, in words rather than in the wire enum.
 *
 *  🔴 CAUGHT BY DRIVING IT: the row rendered `d.outcome_grade` verbatim, so the screen read
 *  "as_expected" — a raw vocabulary token in the one place the user is being told what their own
 *  judgement was worth.
 *
 *  An unknown grade — "" from a row filed before the vocabulary settled, or a name this build does
 *  not know — reads `ungraded` and NEVER falls through to a grade. `as_expected` would be the
 *  tempting default and it is the worst one available: it turns "nobody said" into the claim that
 *  the user called it correctly. Same rule as `learningMeta.evidenceLabel` (ES-7) and
 *  `optimize.SCORE_UNSCORED` (ES-11). */
const GRADE_LABEL: Record<string, string> = {
  better: 'better than expected',
  as_expected: 'as expected',
  worse: 'worse than expected',
  mixed: 'mixed',
  too_early: 'too early to tell',
}
export function gradeLabel(grade: string | null): string {
  return GRADE_LABEL[grade || ''] ?? 'ungraded'
}

/** The confidence the user stated, or the honest absence of one. Never `0%`: an unstated
 *  confidence is not a confidence of zero. */
export function confidenceLabel(d: DecisionRow): string {
  return d.confidence == null ? 'no stated confidence' : `${Math.round(d.confidence * 100)}% confident`
}
