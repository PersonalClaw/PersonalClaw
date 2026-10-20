import type { UiDoc } from './uiDoc'

// Doc objects for StaleWriteNotice and HeldChange — the one recovery for a whole-document save the
// gateway refused as stale (409 stale_write), shared by every surface that saves a whole list or
// document, and the lock that keeps the refused change the one the recovery re-applies.
const doc: UiDoc = {
  name: 'StaleWriteNotice',
  keywords: ['stale', 'conflict', 'concurrent', 'tab', 'overwrite', 'lost update', 'reload', 'reapply', 'review', 'difference', 'save', '409'],
  description:
    "The alert a surface shows when its save was refused because the document changed since the page read it — in another tab, on another device, or by PersonalClaw itself. Nothing was written, and the user's change is kept: Reload and reapply puts it back on top of what is stored now and saves that; Review the difference opens a dialog with what changed elsewhere and what their save would change; Discard my change keeps what is stored. When both sides changed the same part there is no Reapply, and the review offers to save theirs over it only after showing what that replaces. It scrolls itself into view when it appears, so a refusal below the fold of a long form is still seen. Driven entirely by `lib/useStaleWriteGuard`; renders nothing until the guard holds a conflict.",
  props: [
    { name: 'guard', type: 'StaleWriteGuard<T>', required: true, description: "The guard the surface's save runs through (`useStaleWriteGuard`). The notice renders only while it holds a conflict." },
    { name: 'what', type: 'string', required: true, description: 'The document, as it starts a sentence — "Your projection rules", "This skill". The notice reads "<what> changed elsewhere…".' },
    { name: 'present', type: '(doc: T) => unknown', required: false, description: 'The document as the review dialog shows it, when the saved form would mislead — e.g. a stored secret the editor holds blank ("keep it") shown as saved rather than as an empty value. What is saved is unchanged.' },
    { name: 'className', type: 'string', required: false, description: 'Outer spacing at the call site, e.g. `mt-s`.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Render it beside the control that saved, so the refusal lands where the user is looking — the same place the save failure line goes.' },
    { guidance: true, description: "Express a list edit as an operation (`guard.apply(base, op)`), so Reload and reapply can put it back on top of another tab's edit rather than replacing it." },
    { guidance: true, description: 'Wrap the controls the change was made with in `HeldChange`, so an edit typed after the refusal cannot be silently left out of the change Reload and reapply puts back.' },
    { guidance: false, description: 'Do not clear the draft on a refused save: `guard.save` resolves false and the notice promises the change is kept.' },
    { guidance: false, description: 'Do not resend the stale copy with a fresher revision to make the refusal go away — that is the overwrite the refusal exists to stop.' },
  ],
  anatomy: [
    'role="alert" band in the failure paint, with an AlertTriangle',
    'the sentence naming the document, and — when the change cannot be re-applied — why',
    'Reload and reapply (primary), Review the difference (secondary), Discard my change (ghost)',
    'the review dialog: "Changed elsewhere" and "Your change" as UnifiedDiff patches, with the matching action',
  ],
}

const held: UiDoc = {
  name: 'HeldChange',
  keywords: ['stale', 'conflict', 'held', 'lock', 'freeze', 'fieldset', 'disabled', 'reapply'],
  description:
    "The editor a refused change was made in, frozen while the StaleWriteNotice holds that change. Reload and reapply puts back exactly the change the refused save was built from, so an edit typed afterwards would be silently left out; while the change is held, every control inside is off (a disabled fieldset) with the one held-change reason. Renders its children unchanged when nothing is held.",
  props: [
    { name: 'guard', type: 'StaleWriteGuard<T>', required: true, description: 'The same guard the StaleWriteNotice beside it reads; the controls are off while it holds a conflict.' },
    { name: 'children', type: 'ReactNode', required: true, description: "The controls the change is made with. Keep the notice and the page's Save/Cancel outside it." },
  ],
  bestPractices: [
    { guidance: true, description: 'Wrap only the inputs — the notice and the actions that settle the change must stay usable.' },
    { guidance: false, description: 'Do not use it for an editor whose own control has a lock prop (a code editor `locked`): a fieldset cannot turn off a contenteditable surface.' },
  ],
  anatomy: [
    'a `display: contents` fieldset — no box of its own, so the layout is unchanged',
    'disabled with the held-change reason as its title while a conflict is held',
  ],
}

export default [doc, held]
