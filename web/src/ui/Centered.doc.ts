import type { UiDoc } from './uiDoc'

// Doc object for Centered — the full-height centering wrapper. Its "single source
// for the flex h-full items-center justify-center box" intent (and the drifted
// fourth copy it consolidated) was a source comment, encoded here.
const doc: UiDoc = {
  name: 'Centered',
  keywords: ['center', 'centered', 'wrapper', 'spinner', 'empty', 'placeholder', 'loading', 'full-height'],
  description:
    'The full-height centering wrapper — the `flex h-full items-center justify-center` box that holds a pane\'s loading spinner, load-failure placeholder, or empty hint centered in the available height. Pure chrome, no props beyond children.',
  props: [
    { name: 'children', description: 'The content to center in the available height (spinner, empty hint, or failure placeholder).' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for Centered whenever a pane needs a spinner / empty / failure state centered in its height — three surfaces defined this inline and a fourth drifted to a different layout; this is the single source.' },
    { guidance: false, description: 'Do not hand-roll `flex h-full items-center justify-center` again — that duplication is exactly what let one copy drift.' },
  ],
  anatomy: ['div (flex h-full, items-center, justify-center)', 'children'],
}

export default doc
