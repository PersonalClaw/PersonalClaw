import type { UiDoc } from './uiDoc'

// Doc object for Tooltip. Authored: keywords, prose, per-prop descriptions, Do/Don't, anatomy.
// Prop type/required are DERIVED from Tooltip.tsx at build time — never restate them here.
const doc: UiDoc = {
  name: 'Tooltip',
  keywords: ['tooltip', 'tip', 'hint', 'hover', 'focus', 'describe', 'description', 'explain'],
  description:
    'A real tooltip for a control: it says what the control does before it is used. It shows after 400 ms of hover and at once on keyboard focus, stays while the pointer is over it, and hides on leave, blur and Escape. Its text is always the control’s description, through aria-describedby on a hidden copy, so a screen reader hears it without hovering; the bubble is that copy’s visual twin, portaled at --z-menu. It never opens on touch, nor on the focus a mouse press gives. IconButton renders one for its `tip`.',
  props: [
    { name: 'text', description: 'What the control does, in one or two short sentences; it may open with the control’s name, as the composer’s tips do. It is the control’s description too.' },
    { name: 'children', description: 'A render function that draws the control from the trigger props (aria-describedby, the pointer, focus and key handlers, and the ref). Spread them onto the control itself.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Say what the control does, and what happens on a press, not only its name: "Voice input: dictate into the box. It never sends."' },
    { guidance: true, description: 'Prefer IconButton’s `tip` for a round icon button; reach for Tooltip directly only for a control IconButton does not draw.' },
    { guidance: false, description: 'Do not put a reason or a passing state in the control’s name to explain it; the name stays the one the control is found by, and the tooltip carries the explanation.' },
    { guidance: false, description: 'Do not handle Escape for it on the document: it handles Escape on the control, and only while it shows, so a running turn’s Stop shortcut still gets the key otherwise.' },
    { guidance: false, description: 'Do not rely on it alone on a phone: a touch never opens it, so a state that matters while a control is on needs words on the page too, as hands-free voice’s line under the orb does.' },
  ],
  anatomy: ['the control (the render function’s output, with the trigger props)', 'hidden copy (role=tooltip, the aria-describedby target)', 'bubble (portaled, aria-hidden, glass, caption type)'],
}

export default doc
