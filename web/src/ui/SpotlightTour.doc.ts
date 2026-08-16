import type { UiDoc } from './uiDoc'

// Doc object for SpotlightTour. The modality rationale (why a dimmed page owes a focus
// trap), the click-shield decision, the bounded anchor poll and the reduced-motion
// ABSENCE were all source comments — encoded here as machine-readable data.
const doc: UiDoc = {
  name: 'SpotlightTour',
  keywords: ['tour', 'spotlight', 'coach mark', 'walkthrough', 'onboarding', 'overlay', 'scrim', 'guide', 'product tour'],
  description:
    'A spotlight ("coach mark") tour over the real, mounted UI: the page dims except for a ring around one element, and a card beside it says what that element is. Each stop names an anchor, which it finds as `[data-tour="<anchor>"]` on the live DOM and re-measures on scroll/resize. It is a modal dialog because the page underneath is dimmed — focus is trapped in the card and re-taken on every stop, Escape exits from any stop while the tour holds focus (a layer opened above it, like Cmd+K\u2019s palette, closes first), and a click outside the card ends the tour rather than reaching a control the dim layer was covering. It never navigates and never persists anything: the host owns the step index, the routing and any record that the tour was taken.',
  props: [
    { name: 'index', description: 'Which stop is showing (0-based). Owned by the host so Back/Next and any deep-link stay one source of truth.' },
    { name: 'label', description: 'The tour\'s name, e.g. "PersonalClaw tour" — it opens the dialog\'s accessible name ("<label> — step 2 of 5: <title>").' },
    { name: 'onExit', description: 'Called on every way out: Escape, the X, a click outside the card, and Done on the last stop. Unmount the tour here.' },
    { name: 'onIndex', description: 'Called with the next stop index by Back/Next. Do any navigation for the new stop in response to this, not inside the tour.' },
    { name: 'steps', description: 'The ordered stops: `{ id, anchor, icon, title, body }`. `anchor` is matched against `data-tour` on the element to spotlight.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Put a `data-tour="<anchor>"` attribute on the element each stop points at — a stable wrapper, not a leaf that re-renders away.' },
    { guidance: true, description: 'Drive routing from onIndex in the host when a stop lives on another surface; the tour polls for the anchor, so the element may mount a few frames after the step changes.' },
    { guidance: true, description: 'Keep the host\'s step state in memory only if the tour is meant to be replayable rather than resumable — a stop index in storage resurrects a half-finished tour on the next load.' },
    { guidance: false, description: 'Do not rely on the spotlight to gate anything: every stop is skippable, Escape works from all of them, anything opened on top of it still takes the first Escape, and a stop whose anchor is missing degrades to a centred card with no ring.' },
    { guidance: false, description: 'Do not add a second prefers-reduced-motion query — the component already drops the pulsing halo, the card transition and the smooth scroll under it.' },
  ],
  anatomy: [
    'createPortal to <body>',
    'fixed full-screen container',
    'transparent click shield (click-away exit)',
    'dim: four bands around the ring, or one full sheet when unanchored',
    'static outline ring on the anchor box',
    'pulsing halo (motion only — absent under reduced motion)',
    'motion.div step card: role=dialog + aria-modal + useFocusTrap (icon • title • body • end-the-tour X • step counter • Back/Next/Done)',
  ],
}

export default doc
