import type { UiDoc } from './uiDoc'

// forms.tsx exports the shared form-field family (Field + the standard controls),
// so its doc default-exports an array — one UiDoc per exported component. The
// Field-label aria contract, the size/surface scale, and NumberField's clamp-on-
// commit behavior were source comments — encoded here as machine-readable data.
// (Segmented is re-exported from forms.tsx but documented in Segmented.doc.ts.)
const docs: UiDoc[] = [
  {
    name: 'Field',
    keywords: ['field', 'label', 'form', 'wrapper', 'hint', 'accessibility', 'labelledby'],
    description:
      'The wrapper for a labeled form control: an uppercase label row (with an optional right slot for a SoonTag) above the control, plus an optional hint below it. It publishes its label id via context so the wrapped control associates with it via aria-labelledby — turning the sighted-only label into a real accessible name with zero call-site changes.',
    props: [
      { name: 'label', description: 'The visible uppercase label; carries a stable id exposed to the wrapped control for aria-labelledby.' },
      { name: 'hint', description: 'Optional muted helper text rendered below the control.' },
      { name: 'right', description: 'Optional slot on the label row (e.g. a SoonTag) aligned to the right.' },
      { name: 'children', description: 'The wrapped control (TextInput / Select / …).' },
    ],
    bestPractices: [
      { guidance: true, description: 'Wrap standard controls in Field rather than hand-rolling a <label> — the control gets its accessible name from the published label id automatically.' },
      { guidance: true, description: "A control claims the Field label only when it passes no ariaLabel of its own — an explicit ariaLabel always WINS, which is how a multi-control Field (two password inputs under one 'Set a password' label) names each member distinctly instead of both announcing the Field label." },
      { guidance: false, description: 'Do not hardcode colors or px — the label tone and spacing route through design tokens (the token-lint ratchet fails the build otherwise).' },
    ],
    anatomy: ['FieldLabelCtx provider', 'label row (uppercase label id + right slot)', 'wrapped control', 'optional hint paragraph'],
  },
  {
    name: 'FieldError',
    keywords: ['error', 'failure', 'validation', 'alert', 'form', 'inline', 'announce'],
    description:
      "The one-line failure message under a control or beside an action. Renders identically to the `<p className=\"text-danger …\">` line that 35 call sites had hand-rolled, and adds the one thing every one of them was missing: role=alert, so a failed save is announced and not merely coloured. Measured on #/settings/design with the theme POST forced to 500 — the failure text appeared on screen and the page held zero live regions.",
    props: [
      { name: 'children', description: 'The message. Keep it one line; use InlineError for a multi-line or dismissible banner.' },
      { name: 'className', description: 'Per-site spacing only (mt-2, mb-m, shrink-0). The danger tone and the 0.8125rem size are fixed so every failure line reads the same.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for FieldError for the terse failure line that belongs to one control or one action, and InlineError for a tinted, dismissible banner above a body.' },
      { guidance: false, description: 'Do not use it for a stored status field (a finished run\'s recorded error, say) — role=alert interrupts, and historical data should not.' },
      { guidance: false, description: 'Do not re-tone or resize it per site; that drift is exactly what the 35 hand-rolled copies were.' },
    ],
    anatomy: ['<p role="alert"> (danger tone, 0.8125rem, optional per-site spacing)'],
  },
  {
    name: 'TextInput',
    keywords: ['input', 'text', 'field', 'form', 'password', 'mono', 'icon', 'controlled'],
    description:
      'The one standard text field. Chrome is fixed; the only axes are `size` (sm/md/lg) and `surface` (container/high/base) — the family the app\'s height/fill spread collapses onto. Codification, not redesign: defaults reproduce the prior fixed h-10 / container / 0.9375rem field byte-for-byte. Controlled via value + onChange; behavioral props (type, mono, leadingIcon) are grown in lockstep with real adopters.',
    props: [
      { name: 'ariaLabel', description: 'Explicit accessible name — used when the control has its own `name` (so it does not claim a Field label) or sits outside any Field. A `name` attribute is not an accessible name, so it never suppresses this.' },
      { name: 'autoFocus', description: 'Focus the input on mount.' },
      { name: 'leadingIcon', description: 'A leading glyph pinned inside the left edge; adds the canonical pl-9 inset that clears the fixed left-3 icon. Pass the raw icon (e.g. `<Search size={14} />`) and it inherits the muted tone.' },
      { name: 'mono', description: 'Monospace — for technical values (commands, endpoints, keys).' },
      { name: 'name', description: 'Stable form name (also the id). When set, the control uses its own name instead of claiming the Field label, so pass ariaLabel too.' },
      { name: 'onChange', description: 'Fires with the new string on each keystroke.' },
      { name: 'onKeyDown', description: 'Key handler on the input (e.g. Enter to submit).' },
      { name: 'placeholder', description: 'Placeholder text shown when empty.' },
      { name: 'required', description: 'Publishes `aria-required` and nothing else — no asterisk, no colour, no layout change. Mark the field a form actually enforces: the app already explains mandatory fields at the submit button (40 `disabledReason` sites read "Enter a … first"), which a screen-reader user tabbing the field never hears. A VISIBLE required marker is a separate design decision; this is the invisible half.' },
      { name: 'size', description: "Height/text tier — 'sm' h-8 (dense in-panel), 'md' h-9 (rows/side panels), 'lg' h-10 (page forms; DEFAULT, the DESIGN.md canonical Input)." },
      { name: 'surface', description: "Fill token — 'container' (default, sits in a panel), 'high' (on a panel/toolbar), 'base' (on the raw surface)." },
      { name: 'type', description: "'text' (default) or 'password' to mask a secret (API keys, tokens)." },
      { name: 'value', description: 'The field text (controlled).' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for TextInput for any single-line text entry rather than a raw <input> — it carries the blessed radius, focus ring, tone, and the Field-label aria wiring.' },
      { guidance: true, description: "Pick a `size`/`surface` from the scale for a non-default shape; use type='password' for secrets and `mono` for technical values." },
      { guidance: true, description: "Inside a Field with no `name`, omit ariaLabel — the control claims the Field's published label automatically. When you set `name`, pass ariaLabel yourself." },
      { guidance: false, description: 'Do not reach for off-ramp heights/text sizes (h-7…h-11, 0.875rem) via className — 0.875rem is 1px off the DESIGN.md ramp; normalize onto sm/md. Everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
    ],
    anatomy: ['<input> (INPUT_BASE + size + surface chrome)', 'optional relative wrapper + left-3 leading-icon span'],
  },
  {
    name: 'TextArea',
    keywords: ['textarea', 'input', 'multiline', 'form', 'field', 'mono', 'notes'],
    description:
      'The one standard multi-line text field. Shares the field family\'s type-size axis (sm/md/lg, height comes from `rows`, not a fixed h-*) and the Field-label aria wiring. Controlled via value + onChange; vertically resizable.',
    props: [
      { name: 'value', description: 'The textarea text (controlled).' },
      { name: 'onChange', description: 'Fires with the new string on each keystroke.' },
      { name: 'placeholder', description: 'Placeholder text shown when empty.' },
      { name: 'rows', description: 'Initial visible line count / height (default 4); the user can drag-resize vertically.' },
      { name: 'mono', description: 'Monospace — for technical/multi-line values.' },
      { name: 'ariaLabel', description: "Explicit accessible name for call-sites that wrap the control in a non-Field section label; a Field's published label (aria-labelledby) wins when present." },
      { name: 'autoFocus', description: 'Focus the textarea on mount.' },
      { name: 'size', description: "Text-size tier — 'sm'/'md' share the dense 0.8125rem, 'lg' (default) the page-form 0.9375rem. Height comes from `rows`, not this axis." },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for TextArea for any multi-line entry rather than a raw <textarea> — it carries the blessed radius, focus ring, tone, and Field-label aria wiring.' },
      { guidance: true, description: 'Set `rows` for the initial height; inside a Field the label is claimed automatically, else pass ariaLabel.' },
      { guidance: false, description: 'Do not hardcode colors or px — surface, tone, and text steps route through design tokens (the token-lint ratchet fails the build otherwise).' },
    ],
    anatomy: ['<textarea> (rounded, focus ring, resize-y, size text step, optional mono)'],
  },
  {
    name: 'NumberField',
    keywords: ['number', 'stepper', 'numeric', 'input', 'clamp', 'form', 'field', 'settings'],
    description:
      'The one canonical numeric stepper — a small right-aligned tabular-nums <input type="number"> with clamp-on-commit: local string state so a half-typed value is not clobbered mid-edit; on blur/Enter it parses → clamps to [min,max] → commits only if changed, reverting an empty/NaN entry to the last good value. Deliberately NOT the TextInput scale — a fixed-width right-aligned stepper is a distinct role. Controlled via value + onChange.',
    props: [
      { name: 'value', description: 'The committed numeric value (controlled). Re-syncs the local edit buffer when it changes externally, but never mid-edit.' },
      { name: 'onChange', description: 'Fires on commit (blur/Enter) with the clamped number, and only when it actually changed.' },
      { name: 'min', description: 'Lower clamp bound applied on commit (default -Infinity).' },
      { name: 'max', description: 'Upper clamp bound applied on commit (default Infinity).' },
      { name: 'step', description: 'Native step increment (default 1).' },
      { name: 'width', description: "Tailwind width class — the one visual axis the steppers vary (default 'w-24'; panels ship w-20/w-24)." },
      { name: 'ariaLabel', description: "Explicit accessible name; a Field's published label (aria-labelledby) is used when no ariaLabel is given." },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for NumberField for any clamped numeric setting rather than hand-rolling <input type="number"> — the local-buffer + clamp-on-commit + revert-on-empty behavior (hand-rolled verbatim three times) comes built in.' },
      { guidance: true, description: 'Pass min/max so an out-of-range entry clamps on commit; vary only `width` for layout (w-20/w-24).' },
      { guidance: false, description: 'Do not force the full-width TextInput scale onto a stepper — it is intentionally fixed-width, right-aligned, and tabular-nums. Keep colors/px on tokens (the token-lint ratchet fails the build otherwise).' },
    ],
    anatomy: ['<input type="number"> (fixed width, right-aligned, tabular-nums, focus ring)'],
  },
  {
    name: 'DateInput',
    keywords: ['date', 'input', 'calendar', 'picker', 'form', 'field'],
    description:
      'The standard native date field — a styled <input type="date"> matching the field-family chrome (rounded, container fill, focus ring). Controlled via value (a date string) + onChange; claims a wrapping Field\'s label for accessibility. The native picker follows the active theme: the scheme is inherited from the root rather than pinned on the element.',
    props: [
      { name: 'value', description: 'The date string value (controlled).' },
      { name: 'onChange', description: 'Fires with the new date string when the native picker changes.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for DateInput for date entry rather than a raw <input type="date"> — it carries the blessed chrome and claims the Field label for accessibility.' },
      { guidance: false, description: 'Do not hardcode colors or px — surface, radius, and focus ring route through design tokens (the token-lint ratchet fails the build otherwise).' },
    ],
    anatomy: ['<input type="date"> (field chrome, theme-inherited color-scheme)'],
  },
  {
    name: 'Select',
    keywords: ['select', 'dropdown', 'native', 'options', 'form', 'field', 'picker'],
    description:
      'The styled native <select> that matches the TextInput chrome (rounded, container fill, focus ring, custom chevron affordance). The right primitive for a short fixed set of options; controlled via value + onChange. For a long/filterable list reach for Combobox instead.',
    props: [
      { name: 'value', description: 'The selected option value (controlled).' },
      { name: 'onChange', description: 'Fires with the newly selected value.' },
      { name: 'options', description: 'The choices as `{ value, label }[]`, rendered as native <option>s.' },
      { name: 'disabled', description: 'Dim + block the select.' },
      { name: 'name', description: 'Stable form name (also the id). When set the control uses its own name instead of claiming the Field label.' },
      { name: 'ariaLabel', description: 'The accessible name for a Select outside any Field (a floating toolbar control, or a second control in a multi-control Field). Wins over the Field label, same precedence as TextInput.' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for Select for a short fixed set of options rather than a raw <select> — it matches the field family chrome and Field-label aria wiring.' },
      { guidance: true, description: 'Outside a Field, pass ariaLabel — a select with no enclosing Field and no ariaLabel has no accessible name.' },
      { guidance: true, description: 'For a long or filterable option set, use Combobox instead — it adds type-to-filter and grouping.' },
      { guidance: false, description: 'Do not hardcode colors or px — surface, radius, chevron, and focus ring route through design tokens (the token-lint ratchet fails the build otherwise).' },
    ],
    anatomy: ['<select> (appearance-none, field chrome, custom chevron pad)', 'native <option>s'],
  },
  {
    name: 'ChipInput',
    keywords: ['chip', 'tag', 'input', 'tokens', 'multi', 'suggestions', 'form', 'field'],
    description:
      'The tag / chip input — type + Enter (or comma) to add a value, × to remove one, Backspace on an empty draft removes the last. Optional suggestions autocomplete via a datalist (surfacing only values not already added, to avoid near-duplicate fragments). Controlled via values (a string array) + onChange.',
    props: [
      { name: 'values', description: 'The current chips as a string array (controlled).' },
      { name: 'onChange', description: 'Fires with the next array on add/remove.' },
      { name: 'placeholder', description: 'Placeholder shown only while there are no chips yet.' },
      { name: 'max', description: 'Optional cap on the number of chips; adding stops once reached.' },
      { name: 'suggestions', description: 'Optional autocomplete pool; only entries not already in `values` are offered (avoids near-duplicate fragments like "Kubernetes" vs "kubernetes").' },
      { name: 'ariaLabel', description: "Explicit accessible name for the draft field, for call-sites whose chips are not tags (aliases, keywords). This ui/forms Field's published label (aria-labelledby) wins when present; with neither, the field falls back to 'Add a tag'. Note settings' own Field (pages/settings/settingsUI) publishes NO label context, so a ChipInput inside one is effectively bare and needs ariaLabel." },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for ChipInput for any free-form multi-value tag entry rather than hand-rolling chips — add/remove keys, the cap, dedupe, and datalist suggestions come built in.' },
      { guidance: true, description: 'Pass `suggestions` to steer users onto existing values and `max` to cap the set; it dedupes and skips already-added entries for you.' },
      { guidance: true, description: "Pass ariaLabel whenever the chips are not tags and no ui/forms Field wraps them — the fallback announces 'Add a tag', which misnames an alias/keyword field to a screen reader. A settings Field looks labelled on screen but publishes no label context, so it does not rescue you." },
      { guidance: false, description: 'Do not hardcode colors or px — the chip pills, surface, and focus-within ring route through design tokens (the token-lint ratchet fails the build otherwise).' },
    ],
    anatomy: ['flex-wrap container (focus-within ring)', 'chip pills (label + × remove)', 'draft <input> (Enter/comma to add, Backspace to pop)', 'optional <datalist> suggestions'],
  },
  {
    name: 'Checkbox',
    keywords: ['checkbox', 'tick', 'select', 'selection', 'multi-select', 'bulk', 'boolean', 'form'],
    description:
      'A single boolean tick, for row selection and inline opt-ins. Distinct from Switch: a Switch applies a SETTING immediately and reads as on/off state, whereas a Checkbox marks a SELECTION the user then acts on (the multi-select bars behind bulk actions want the latter). Click/change propagation is stopped inside the primitive, so a tick inside a clickable list row never also activates the row.',
    props: [
      { name: 'checked', description: 'Current state (controlled).' },
      { name: 'onChange', description: 'Fires with the next boolean. Propagation is already stopped for you.' },
      { name: 'ariaLabel', description: 'Required in practice — a bare tick has no accessible name of its own, so name what it selects (e.g. `Select ${title}`).' },
      { name: 'className', description: 'Extra classes for visibility rules (e.g. reveal-on-hover inside a list row).' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for Checkbox for multi-select in a list rather than a raw <input type="checkbox"> — it carries the accent tone, the focus-visible ring, and the propagation guard that clickable rows need.' },
      { guidance: true, description: 'Use Switch instead when the control applies a setting on the spot; use Checkbox when the user is marking things to act on afterwards.' },
      { guidance: false, description: 'Do not re-add onClick={e => e.stopPropagation()} at the call site — the primitive owns it, and duplicating it hides the contract.' },
    ],
    anatomy: ['native <input type="checkbox"> (accent-primary, focus-visible ring, propagation-guarded)'],
  },
  {
    name: 'FieldLabelProvider',
    keywords: ['field', 'label', 'accessibility', 'labelledby', 'context', 'provider', 'a11y'],
    description:
      "The context provider behind Field's accessible-name contract, exported so a DIFFERENT label+control layout can publish a label id too. A form control claims its surrounding label via aria-labelledby; anything that owns a visible label but publishes nothing leaves its controls unnamed. Settings' own row-style Field had exactly that defect — six inputs on the account page, including both password fields, had no accessible name — so the provider is exported rather than the second layout reimplementing (and re-breaking) the wiring. Usage: `<FieldLabelProvider value={labelId}>` where `labelId` is the id of the element that renders the LABEL text (generate it with useId()); every form control in the subtree then claims it via aria-labelledby. A dangling id reports NO name while the markup still looks correct.",
    // Empty by CONTRACT, not by omission: this is React's own context Provider, so it declares no
    // props of its own and the extractor derives none. Documenting `value`/`children` here would be
    // "documented-but-gone" drift. Their meaning lives in the description instead.
    props: [],
    bestPractices: [
      { guidance: true, description: 'Prefer Field. Reach for the raw provider only when a surface needs its own label layout (a bordered settings row, a compact inline row) and still wants real accessible names.' },
      { guidance: true, description: 'Generate the id with useId() and put it on the element that renders the LABEL — not the hint, or the control announces a paragraph as its name.' },
      { guidance: false, description: 'Do not wrap a subtree whose controls already pass their own ariaLabel expecting the provider to win: an explicit ariaLabel takes precedence, by design.' },
    ],
    anatomy: ['React context provider carrying one label id (no DOM of its own)'],
  },
  {
    name: 'FieldHintProvider',
    props: [],
    anatomy: ['React context provider carrying one hint id (no DOM of its own)'],
    keywords: ['field', 'hint', 'description', 'accessibility', 'describedby', 'context', 'provider', 'a11y'],
    description:
      "The context provider behind Field's accessible-DESCRIPTION contract, and the sibling of FieldLabelProvider: one publishes the id of the label that NAMES a control, this publishes the id of the hint that DESCRIBES it. A Field renders a hint sentence beside its control, and without a published id that sentence is sighted-only, because a control describes itself via aria-describedby and there was nothing to point at. Measured on #/settings/account: all six inputs were correctly NAMED and not one was DESCRIBED, so a constraint (At least 12 characters) and a consequence (Leave it empty to keep records unattributed) never reached a screen reader. 260 hinted publishers render (229 direct call sites — Field 118, settingsUI's Row 77, NumberRow 34 — plus 31 through five local wrappers that forward a hint into one of those three; recounted 2026-08-27, the earlier 196 is stale) and none of them had to change. The claimants are TextInput, TextArea, NumberField, DateInput, Select, ChipInput, plus Toggle, Combobox and ShortcutRecorder. axe cannot see this defect at all — an unassociated paragraph beside an input is valid HTML with no rule to violate.",
    bestPractices: [
      { guidance: true, description: 'Publish the id ONLY when a hint is actually rendered: `value={hint ? hintId : undefined}`. An aria-describedby pointing at a missing element is worse than none, because assistive tech resolves it to nothing while the attribute claims a description exists.' },
      { guidance: true, description: 'Pair it with FieldLabelProvider rather than instead of it. The label is the name and the hint is the description; a control that claims the hint as its NAME announces a paragraph where a noun belongs.' },
      { guidance: false, description: 'Do not reach for this to attach validation errors. It carries the STATIC hint a layout renders; an error message that appears on failure is a different, live announcement (see FieldError).' },
    ],
  },
]

export default docs
