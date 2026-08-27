import type { UiDoc } from './uiDoc'

// Doc object for IconButton (Platform-Legibility §5). Authored: keywords, prose,
// per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// IconButton.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'IconButton',
  keywords: ['icon', 'button', 'round', 'pill', 'toolbar', 'action', 'toggle', 'halo', 'bloom', 'loading', 'busy', 'spinner'],
  description:
    'The round, icon-only button — a pill hit area (40px default) wrapping a single rounded outline glyph, for toolbar/chrome affordances that a text label would only clutter. Expressiveness-scaled press/hover springs, a soft hover halo at bold intensity, an optional icon-morph on iconKey change, and a one-shot success bloom. Yields to reduced-motion.',
  props: [
    { name: 'icon', description: 'The Lucide icon component to render (rendered at strokeWidth 2, absoluteStrokeWidth).' },
    { name: 'title', description: "Tooltip override — defaults to `label`, matching ui/SquareIconButton. Use it when the accessible NAME must carry a row's subject (e.g. `Delete: <title>` on a list of 83 notifications) while the hover hint stays the bare verb." },
      { name: 'label', description: 'Accessible name (aria-label) and native tooltip — required, since there is no visible text.' },
    { name: 'onClick', description: 'Click handler; receives the mouse event. Suppressed while disabled.' },
    { name: 'active', description: 'Renders the currently-on (toggled) state — a surface-high fill instead of the bare idle glyph.' },
    { name: 'filled', description: 'Solid primary treatment (bg-primary / on-primary) for an emphasized action; carries its own emphasis so the hover halo is suppressed.' },
    { name: 'size', description: 'Hit-area width/height in px (default 40). Shrink for dense inline chrome.' },
    { name: 'iconSize', description: 'Glyph size within the hit area (default 20); dense toolbars use 12–16 so those sites adopt the primitive instead of hand-rolling a <button>.' },
    { name: 'className', description: 'Extra classes (tokens only — no raw hex/px).' },
    { name: 'disabled', description: 'Dim (40% opacity) + block interaction (not-allowed cursor); onClick is suppressed regardless of what is passed, so a gated button reads as inert instead of a silent dead-click.' },
    { name: 'disabledReason', description: 'WHY it is unavailable, when disabled is true; appended to the tooltip after an em dash. This button keeps its tab stop (disabled maps to aria-disabled, never the native attribute), so a keyboard user lands on it and would otherwise hear only the label — and being icon-only, it has no visible text to carry the reason either. Omit it when the gate is self-evident or transient; pass it only for the branch it describes when the gate is compound.' },
    { name: 'loading', description: 'The action is IN FLIGHT — the opposite claim from disabled, which says "unavailable". Sets aria-busy, cross-fades the glyph out under a centered spinner (Button’s treatment, carried to the tier that has no label to swap), refuses the click through the same off = disabled || loading guard, and stands the press/hover springs and the hover halo down. It deliberately does NOT dim and does NOT set aria-disabled: measured, 16 async icon controls passed disabled={busy} mid-flight, so a working button announced itself unavailable and dimmed to 40%. Reach for it for any async click; keep disabled for a gate the user must satisfy, and split a compound gate (disabled={!text.trim()} loading={busy}).' },
    { name: 'iconKey', description: 'When set, the icon cross-fades/scales/rotates in whenever this key changes (a shape morph, e.g. send arrow → success check); without it the icon swaps instantly.' },
    { name: 'bloom', description: 'A one-shot success bloom — the button pops with a playful overshoot when it mounts in this state (e.g. the send→check confirmation), scaled by the bounce tier.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for IconButton for any round icon-only affordance rather than hand-rolling a <button> — press/hover springs, the hover halo, and reduced-motion handling come built in.' },
    { guidance: true, description: 'Always pass a meaningful label — it is the only accessible name and the tooltip; an icon alone has no text for screen readers.' },
    { guidance: true, description: 'Pair iconKey with a morphing glyph (send→check) so the swap animates; use bloom for the one-shot success confirmation.' },
    { guidance: true, description: 'Pass loading for an async click, never disabled — "unavailable" and "working" are opposite claims, and an icon-only button has no label to carry the difference. Split a compound gate: disabled={!text.trim()} loading={busy}.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise); size/iconSize are the sanctioned numeric knobs.' },
    { guidance: false, description: 'Do not hand-roll the in-flight spinner (icon={busy ? Loader2 : Send} plus a [&_svg]:animate-spin class) — loading owns the glyph cross-fade, and a caller-side spinner inside a dimmed button is the shape this prop replaced.' },
    { guidance: false, description: 'Do not use IconButton for a labelled action (use Button) or for a dense squared toggle in list rows (use SquareIconButton).' },
  ],
  anatomy: ['motion.button (press/hover spring, rounded-pill)', 'hover halo span (radial, bold intensity only)', 'icon glyph (AnimatePresence morph when iconKey set)', 'loading spinner overlay (absolute, aria-hidden, cross-fades over the glyph)'],
}

export default doc
