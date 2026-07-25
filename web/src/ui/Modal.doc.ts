import type { UiDoc } from './uiDoc'

// Doc object for Modal. The portal-to-<body> centering rationale, the single-close
// header contract, and the layoutId shared-element morph were all source comments —
// encoded here as machine-readable data.
const doc: UiDoc = {
  name: 'Modal',
  keywords: ['modal', 'dialog', 'sheet', 'overlay', 'scrim', 'popup', 'portal', 'centered'],
  description:
    'The reusable centered modal with a scrim. A pinned header carries the title + a single close (X) button; Escape and a scrim click also dismiss it, focus is trapped inside, and only the body scrolls. Portaled to <body> so position:fixed centers against the VIEWPORT (an animated/transformed ancestor like the composer or glow would otherwise capture the fixed positioning and push it off-center). Width tracks the content column, a touch wider for reading.',
  props: [
    { name: 'children', description: 'The scrolling modal body.' },
    { name: 'icon', description: 'Optional leading node beside the title in the header.' },
    { name: 'layoutId', description: 'Shared-element id: when the opening trigger renders a motion.* with the same layoutId, the sheet morphs OUT of that element instead of scaling from center.' },
    { name: 'onClose', description: 'Called on any dismiss — the X button, Escape, or a scrim click. Required.' },
    { name: 'title', description: 'The modal heading, shown in the pinned header (also the aria-label when a string).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Modal for any centered dialog rather than hand-rolling a fixed overlay — the scrim, Escape/scrim-click dismiss, focus trap, viewport centering, and enter/exit motion come built in.' },
    { guidance: true, description: 'Wire onClose to your open-state so Escape and scrim clicks close the modal (it does not manage its own open state).' },
    { guidance: true, description: 'Pass a matching `layoutId` on both the trigger (a motion.*) and the Modal to get the "grow from the trigger" shared-element morph.' },
    { guidance: false, description: 'Do not wrap Modal in a transformed/animated ancestor expecting it to center there — it portals to <body> on purpose; center offset comes from the viewport.' },
    { guidance: false, description: 'Do not hardcode colors or px in className — everything routes through design tokens (the token-lint ratchet fails the build otherwise).' },
  ],
  anatomy: ['createPortal to <body>', 'fixed full-screen flex-center container', 'blurred scrim (click-to-close)', 'motion.div sheet (squircle, expressiveness-scaled overshoot; layoutId morph)', 'pinned header (icon • title • close X)', 'scrolling body'],
}

export default doc
