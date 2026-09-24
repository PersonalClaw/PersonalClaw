// What an "Optimize prompt" click ACTUALLY did — the third and fourth answers (#277).
//
// 🔴 A WORKING FEATURE READ AS A BROKEN ONE. Every composer spelled the outcome as
// `if (r.changed && r.optimized) { …rewrite… }` with no else, inside a `try` whose
// `catch` was the bare comment `/* keep the draft on failure */`. So of the four things
// that can happen to a click, only ONE said anything:
//
//     changed:true      the draft is rewritten          ← visible
//     changed:false     the prompt is already good      ← SILENT
//     request threw     the optimizer is unreachable    ← SILENT
//     no model bound    ditto                           ← SILENT
//
// Measured in the report: a 514-char prompt returns `{changed: false, optimized: "<the
// input, verbatim>"}` after ~15 seconds. The button spins, returns to idle, the text is
// byte-identical, the console is clean and nothing is logged server-side. There is no
// signal anywhere that the click was processed — which is indistinguishable from a hung
// request, and reads as "optimize doesn't work". Users then stop clicking it on the short
// vague prompts where it helps most. The backend was right the whole time: the same
// endpoint expands "fix bus routes" into a full plan.
//
// 🔑 ONE OWNER, BECAUSE THREE COMPOSERS ASK. Chat and the Loop composer both call
// `api.optimizePrompt` and both had the same missing branch, so the verdict and its
// wording live here once rather than being re-derived per surface — the composer
// equivalent of the sentence rule the rest of this app follows. A fourth composer gets
// the honest behaviour by calling this instead of re-reading `changed`.
//
// 🪤 `changed:false` AND A FAILURE ARE DIFFERENT NEWS AND MUST NOT SHARE A TONE. "Already
// good" is a normal, successful answer — `info`. An unreachable optimizer is unrequested
// bad news — `error`, which is also the only level the toast host plays a sound cue for.
// Collapsing them (the tempting one-liner) would either cry wolf on every good prompt or
// hide a real outage behind a compliment, and the user's next action differs: nothing vs.
// check the model binding.

/** The API shape — `lib/api.ts`'s `optimizePrompt` return, structurally. */
export interface OptimizeReply { optimized?: string; changed?: boolean }

export type OptimizeOutcome =
  /** The optimizer rewrote the draft. `optimized` is the text to put in the composer. */
  | { kind: 'rewritten'; optimized: string }
  /** The optimizer read the draft and had nothing to add. Not a failure. */
  | { kind: 'unchanged'; message: string; level: 'info' }
  /** The request never landed a verdict. The draft is deliberately left alone. */
  | { kind: 'failed'; message: string; level: 'error' }

/** The two outcomes that have something to SAY — i.e. everything except a rewrite, whose
 *  feedback is the new text in the composer. Derived from `OptimizeOutcome` rather than
 *  re-listed, so a fifth outcome cannot be added without deciding which side it falls on. */
export type OptimizeNotice = Extract<OptimizeOutcome, { message: string }>

/** Classify a settled `optimizePrompt` answer.
 *
 *  `changed:true` with an EMPTY `optimized` is treated as unchanged rather than as a
 *  rewrite: the old condition was `r.changed && r.optimized`, so that combination
 *  already fell through to silence, and blanking a user's draft on a truthy flag with no
 *  text would be the one outcome worse than saying nothing. */
export function optimizeOutcome(r: OptimizeReply): OptimizeOutcome {
  const text = (r.optimized ?? '').trim()
  if (r.changed && text) return { kind: 'rewritten', optimized: r.optimized! }
  return {
    kind: 'unchanged',
    // Second person and past tense, because the user just asked a question and this is
    // the answer to it. "No changes suggested" alone reads as a shrug; naming the reason
    // ("already reads well") is what turns a non-event into a verdict the user can act on.
    message: 'Your prompt already reads well — the optimizer suggested no changes.',
    level: 'info',
  }
}

/** Classify a THROWN `optimizePrompt` call. Keep the reason: "is a model configured?" is
 *  the commonest cause on a fresh home, and the backend's own message names it. */
export function optimizeFailure(e: unknown): OptimizeNotice {
  const raw = (e instanceof Error ? e.message : String(e ?? '')).trim()
  // 🪤 SENTENCE-CASE THE BORROWED CLAUSE. Backend and browser rejection messages are
  // lowercase fragments ("no model is configured", "Failed to fetch"), so appending one
  // verbatim after a full stop renders as *"…your draft is unchanged. no model is
  // configured"* — seen in the browser during validation. This is a user-facing sentence,
  // not a log line, so it gets punctuated like one: capitalise the first letter (never
  // touching the rest, which may legitimately carry an identifier) and end it.
  const detail = raw ? raw[0].toUpperCase() + raw.slice(1) : ''
  const reason = detail ? (/[.!?]$/.test(detail) ? detail : `${detail}.`) : 'Is a model configured?'
  return {
    kind: 'failed',
    // The draft-is-safe half is not padding: the button's whole job is to replace the
    // text the user typed, so "it failed" without it reads as "and your prompt is gone".
    message: `Couldn't optimize this prompt — your draft is unchanged. ${reason}`,
    level: 'error',
  }
}
