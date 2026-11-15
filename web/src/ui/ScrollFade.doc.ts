import type { UiDoc } from './uiDoc'

// Doc object for ScrollFade, the app's one cue that a pane runs past its edge.
const doc: UiDoc = {
  name: 'ScrollFade',
  keywords: ['scroll', 'fade', 'overflow', 'more below', 'cut edge', 'pane', 'scrollbar'],
  description:
    "A scrolling pane with the app's one cue that its content runs past its edge. Scrollbars stay hidden app-wide (the tenet of 2026-07-07), so while content runs past the bottom edge a 24 px fade from the pane's own ground to transparent lies over it, and once the content has moved past the top edge the same fade lies at the top. It is measured on scroll, whenever the pane or anything in it changes size, and once the faces have loaded. It takes no pointer, says nothing and does not move; under forced colours it keeps itself and runs from the user's own Canvas.",
  props: [
    { name: 'ground', description: "The pane's own ground, which the fade starts from: 'canvas' under a page's list, 'surface' in a detail pane." },
    { name: 'className', description: "Classes for the scrolling element itself (its padding). Tokens only." },
    { name: 'style', description: 'Inline style for the scrolling element, merged over its 24 px scroll-padding.' },
    { name: 'scrollRef', description: 'A ref to the scrolling element, for a caller that scrolls or measures it.' },
    { name: 'children', description: 'What scrolls.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Wrap every pane that can run past its edge in ScrollFade, with the ground it sits on, so the cue is the same everywhere.' },
    { guidance: true, description: "Keep the scroll-padding it sets: it holds a card reached with the keys, a focused control and a reveal clear of the fade." },
    { guidance: false, description: 'Do not show a scrollbar, a "More below" line or a shadow for the same purpose: the fade is the one cue.' },
    { guidance: false, description: 'Do not animate the fade in or out: it shows and goes at once.' },
  ],
  anatomy: [
    'wrapper (relative, takes the space its parent gives)',
    'scroller (overflow-y-auto, scroll-padding 24 px top and bottom)',
    'top fade (h-2xl, from the ground to transparent, only once content has moved past the top)',
    'bottom fade (h-2xl, from the ground to transparent, only while content runs past the bottom)',
  ],
}

export default doc
