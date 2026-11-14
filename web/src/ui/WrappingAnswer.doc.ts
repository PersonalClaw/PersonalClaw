import type { UiDoc } from './uiDoc'

// Doc object for WrappingAnswer, a card's answer whose words a server wrote.
const doc: UiDoc = {
  name: 'WrappingAnswer',
  keywords: ['answer', 'choice', 'card', 'wrap', 'pill', 'button', 'needs you'],
  description:
    "A card's answer whose words a server wrote (a workflow run's choice), drawn in the kit's secondary pill made to wrap (InlineConfirm's `wrapping`): `Button` never wraps, and a long choice must never run past a card at 320 px. Its height is a floor, it takes a 44 px box below 1024 px, and it has a transparent edge forced colours draw. It is a real `<button>` and takes every button attribute but its class and type.",
  props: [
    { name: 'children', description: "The answer, in the words its server wrote. Name it with `aria-label` (\"Answer <ask> — <choice>\") so each choice in a list is told apart." },
  ],
  bestPractices: [
    { guidance: true, description: 'Use it for answers whose words come from a server; an answer the app names keeps `Button`.' },
    { guidance: false, description: 'Do not truncate a choice to fit a row: it wraps, so the whole choice is read before it is pressed.' },
  ],
  anatomy: ['pill (secondary look, wraps, min-h-7, 44 px below 1024 px, transparent edge)'],
}

export default doc
