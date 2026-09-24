import type { UiDoc } from './uiDoc'

// Doc object for SearchField — the one compound-search field. Authored: keywords,
// prose, per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED
// from SearchField.tsx at build time.
const doc: UiDoc = {
  name: 'SearchField',
  keywords: ['search', 'filter', 'input', 'query', 'clear', 'magnifier', 'palette', 'command', 'overlay', 'inline'],
  description:
    'The one compound-search field — a leading magnifier plus a trailing affordance (a spring-pop clear-X, a keyboard hint, or a spinner), the chrome a plain TextInput lacks. Two structural variants: overlay (default) is the dominant list/page search — a solid box owning type="search", Escape-to-clear, and the size→height/radius scale; inline is the ⌘K/⌘P palette shell — a transparent flex child whose surrounding row the caller styles. Codification, not redesign: every value is a shape the app already ships. Controlled via value + onChange.',
  props: [
    { name: 'ariaHasPopup', description: "Set to 'listbox' when this field DRIVES a list of options that lives elsewhere (the command palette, the composer menus). Forwarded to the input as aria-haspopup." },
    { name: 'ariaControls', description: 'Id of the listbox this field drives — forwarded as aria-controls, so assistive tech can tie the field to the results it filters.' },
    { name: 'ariaActiveDescendant', description: "Id of the currently ACTIVE option while the field keeps focus — forwarded as aria-activedescendant. Without it, arrowing through results moves a purely visual highlight and announces nothing; measured exactly that in the command palette (22 options, activedescendant null). Follows MarkdownInput's documented pattern: haspopup + controls + activedescendant, deliberately not role='combobox'." },
    { name: 'ariaExpanded', description: "Whether the controlled listbox is currently showing — forwarded as aria-expanded. Pass it when the popup APPEARS AND DISAPPEARS: the command palette's list exists for as long as the palette is mounted so it has no use for this, but the code quick-open's popover opens on typing and closes on Escape, and without it nothing announces that a list arrived. Same deliberate omission of role='combobox' as the three above." },
    { name: 'value', description: 'The current query (controlled).' },
    { name: 'onChange', description: 'Fires with the new query string on every keystroke (and with `\'\'` on clear).' },
    { name: 'placeholder', description: 'Placeholder text; also the accessible-name fallback when ariaLabel is omitted.' },
    { name: 'ariaLabel', description: 'Accessible name. Falls back to the placeholder (then "Search") — a field outside a labeled Field must still name itself.' },
    { name: 'autoFocus', description: 'Focus the input on mount (e.g. when a palette opens).' },
    { name: 'name', description: 'Stable form name (also the id). Defaults to a generated one so the browser does not autofill a transient filter and each field is uniquely targetable.' },
    { name: 'onKeyDown', description: 'Extra key handling layered on top of the built-in Escape-to-clear; runs FIRST, and calling preventDefault skips the built-in Escape (e.g. Enter picks the first match, arrows navigate results).' },
    { name: 'trailingSlot', description: 'Variant-specific chrome shown AFTER the clear-X (a `<kbd>esc</kbd>`, a spinner).' },
    { name: 'clearable', description: 'Render the built-in spring-pop clear-X (default true). The ⌘K/⌘P palettes clear by convention (Esc/backspace) and opt OUT so the primitive adds no chrome those hero surfaces never had.' },
    { name: 'variant', description: "'overlay' (default): absolute-pinned icon + clear over a solid field. 'inline': transparent input as a flex child of a caller-styled row." },
    { name: 'size', description: "Field scale (overlay: height/text/radius — sm h-8, md h-9, lg h-10 pill DEFAULT; inline: text step only). Off-ramp sizes normalize onto these blessed steps." },
    { name: 'surface', description: "Overlay fill token — 'high' (default), 'container', or 'base'. Overlay-only (the inline row's background is the caller's)." },
    { name: 'inlineIconSize', description: 'Leading magnifier size for the inline variant (palettes ship 13–17px to match row density; default 14). Ignored by overlay (fixed 14).' },
    { name: 'clearOnEscape', description: 'Enable Escape-to-clear on the inline variant (overlay always clears on Escape). Opt in only where clearing beats the palette owning its own Escape (close the modal).' },
    { name: 'inputRef', description: 'Ref to the underlying <input> — palettes focus/select it on a shortcut.' },
    { name: 'spellCheck', description: 'Passed through to the input (palettes disable it).' },
    { name: 'autoCapitalize', description: 'Passed through to the input (palettes disable it).' },
    { name: 'autoCorrect', description: 'Passed through to the input (palettes disable it).' },
    { name: 'onFocus', description: 'Called on input focus (e.g. CodeCockpit reopens its result list).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for SearchField for any search/filter input rather than hand-rolling a magnifier + clear-X over an <input> — a dozen hand-rolls with four clear-button styles and three radii are exactly what this primitive retires.' },
    { guidance: true, description: "Use variant='overlay' for list/page search bars and variant='inline' inside a ⌘K/⌘P palette row (where the field IS the row and the caller styles the surrounding shell)." },
    { guidance: true, description: 'Layer custom keys via onKeyDown and call preventDefault to keep your own Escape; otherwise the built-in Escape-to-clear runs after it.' },
    { guidance: false, description: 'Do not add a native search-cancel glyph or a second clear button — the field owns its clear affordance (the webkit cancel glyph is suppressed); toggle `clearable` instead.' },
    { guidance: false, description: 'Do not reach for off-ramp heights/text sizes via className — pick a `size`/`surface`; everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['relative box (overlay) / bare fragment (inline)', 'leading magnifier (absolute left-3 overlay / flex child inline)', 'search input (pl-9 pr-9 overlay / transparent inline)', 'trailing cluster: spring-pop ClearButton + trailingSlot'],
}

export default doc
