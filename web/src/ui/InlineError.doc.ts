import type { UiDoc } from './uiDoc'

// Doc object for InlineError (Platform-Legibility §5). Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// InlineError.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'InlineError',
  keywords: ['error', 'alert', 'banner', 'inline', 'danger', 'failure', 'dismiss', 'strip', 'notice'],
  description:
    'The inline, danger-tinted error band shown when an action fails — above list/detail bodies, inline in the chat transcript, or as a transient banner. A rounded danger strip holding the message, an optional leading AlertTriangle, and an optional corner "×". The single shape (role="alert") behind the several per-page {err && <div role="alert">} banners that had drifted apart.',
  props: [
    { name: 'children', description: 'The error message content.' },
    { name: 'onDismiss', description: 'Show a corner "×" that calls this; omit for a non-dismissible strip (e.g. chat turn errors).' },
    { name: 'icon', description: 'Lead with an AlertTriangle glyph (matches the Code section banner). Default false.' },
    { name: 'multiline', description: 'Top-align and wrap a multi-line message (whitespace-pre-wrap break-words) instead of the single-line default.' },
    { name: 'animated', description: 'Render with a slide-in entrance (opacity + y) for transient banners, e.g. a rejected board drag.' },
    { name: 'className', description: 'Per-site outer spacing, e.g. mx-l mt-2 (tokens only — no raw hex/px).' },
    { name: 'onRetry', description: 'Offer a "Retry" beside the message. For a FAILED READ inside a form field, where the field\'s own control is unusable until the read succeeds — LoadError\'s centred empty-state treatment is wrong at that scale, but the retry it offers is still what the user needs.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for InlineError for any failure banner rather than hand-rolling a {err && <div role="alert">} — it is the single canonical danger-tinted chrome across the app.' },
    { guidance: true, description: 'The four modes (onDismiss, multiline, animated, onRetry) are orthogonal: omit onDismiss for a non-dismissible strip, set multiline for a wrapping message, set animated for a transient slide-in, set onRetry when the failure is a READ the user can re-attempt.' },
    { guidance: false, description: 'Do not pass onRetry for a failed WRITE — re-running a mutation from an error band is not the same offer as re-reading, and the surface that owns the write should decide (roll back, reconcile, or report).' },
    { guidance: false, description: 'Do not re-tone the band to warn or adjust its tint/padding per site — the per-site 14%-tint / tighter-padding / warn-tone variants were deliberately collapsed onto this one danger chrome.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — reserve it for outer spacing; the danger tint routes through the color tokens.' },
  ],
  anatomy: ['div/motion.div role="alert" (rounded danger strip)', 'optional leading AlertTriangle', 'message span (wraps when multiline)', 'optional "Retry" text button', 'optional corner "×" dismiss button'],
}

export default doc
