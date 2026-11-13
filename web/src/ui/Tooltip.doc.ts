import type { UiDoc } from './uiDoc'

// Doc object for Tooltip. Authored: keywords, prose, per-prop descriptions, Do/Don't, anatomy.
// Prop type/required are DERIVED from Tooltip.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'Tooltip',
  keywords: ['tooltip', 'tip', 'hint', 'hover', 'focus', 'describe', 'description', 'explain'],
  description:
    'A real tooltip for a control: it says what the control does before it is used. It shows after 400 ms of hover and at once on keyboard focus, stays while the pointer is over it, and hides on leave, blur and Escape. Its description, through aria-describedby on a hidden copy, is its `description` when one is given, else its text unless describes is false, so a screen reader hears it without hovering; the bubble shows the text, portaled at --z-menu. It never opens on touch, nor on the focus a mouse press gives. IconButton renders one for its `tip`.',
  props: [
    { name: 'text', description: 'What the control does, in one or two short sentences; it may open with the control’s name, as the composer’s tips do. It is the control’s description too, unless describes is false or a description is given.' },
    { name: 'describes', description: 'Whether the text is the control\'s description too (the default). Pass false for a control whose name already says it, such as a list row whose tooltip is its whole title, so a screen reader says it once.' },
    { name: 'description', description: 'What the bubble says that the control\'s name does not, such as an app\'s permissions beside a row\'s name. Only it is the control\'s description; with describes={false} and none, the control has no description.' },
    { name: 'side', description: 'Where the bubble sits: above the control (the default), or "end": beside the row, flipping to its start when its end has no room, or on its own row where neither side has room, taking no pointer, so it covers no other row of its list. Use "end" for rows stacked in a list, where a bubble above would cover the row above and take its pointer.' },
    { name: 'shows', description: 'Asked with the control as the tooltip would open; false keeps it shut. A row whose tooltip is its whole title passes whether the title is cut short, so a title that shows whole has no tip that only repeats it.' },
    { name: 'children', description: 'A render function that draws the control from the trigger props (aria-describedby, the pointer, focus and key handlers, and the ref). Spread them onto the control itself.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Say what the control does, and what happens on a press, not only its name: "Voice input: dictate into the box. It never sends."' },
    { guidance: true, description: 'Prefer IconButton’s `tip` for a round icon button; reach for Tooltip directly only for a control IconButton does not draw.' },
    { guidance: false, description: 'Do not put a reason or a passing state in the control’s name to explain it; the name stays the one the control is found by, and the tooltip carries the explanation.' },
    { guidance: false, description: 'Do not handle Escape for it on the document: it handles Escape on the control, and only while it shows, so a running turn’s Stop shortcut still gets the key otherwise.' },
    { guidance: false, description: 'Do not rely on it alone on a phone: a touch never opens it, so a state that matters while a control is on needs words on the page too, as hands-free voice’s line under the orb does.' },
  ],
  anatomy: ['the control (the render function’s output, with the trigger props)', 'hidden copy (role=tooltip, the aria-describedby target, unless describes is false with no description)', 'bubble (portaled, aria-hidden, glass, caption type)'],
}

export default doc
