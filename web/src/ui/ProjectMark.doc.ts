import type { UiDoc } from './uiDoc'

// Doc object for ProjectMark — a project's initials on a neutral tile, beside its name.
const doc: UiDoc = {
  name: 'ProjectMark',
  keywords: ['project', 'mark', 'initials', 'avatar', 'tile', 'monogram'],
  description:
    "A project's mark: a rounded tile with its initials (one word gives its first letter, two or more the first letters of the first two words, upper case: Garden G, Trip planning TP, Taxes 2026 T2), on a neutral surface step with secondary ink. It is aria-hidden, because the project's name is always drawn beside it and carries the meaning. It has no hue: a project is not a state, and semantic colours never decorate. `projectInitials(name)` is exported for tests and for any surface that names a project by its initials.",
  props: [
    { name: 'name', description: "The project's name, as the owner wrote it. The mark draws its initials." },
    { name: 'size', description: '`md` (default), 28 px, for list rows and a project page title; `sm`, 18 px, for chips, picker rows and the library\'s rows.' },
  ],
  bestPractices: [
    { guidance: true, description: "Draw the project's name beside it, always: the mark is aria-hidden and two projects can share initials." },
    { guidance: true, description: 'Use `sm` inside a 24 px chip or a picker row, and `md` on a list row or a page title.' },
    { guidance: false, description: 'Do not colour it by project. A colour per project says nothing the name does not, and would read as a state.' },
    { guidance: false, description: 'Do not use it as a control. It is a picture of the name; the row or chip it sits in is what opens or filters.' },
  ],
  anatomy: [
    'an aria-hidden inline-flex tile, 28 px (md) or 18 px (sm), corners in proportion, following the roundness setting',
    'bg-surface-highest with on-surface-var ink, no hue: a step above the chip and the row it sits on',
    "the name's initials, upper case, at the label-s (md) or caption (sm) type role",
  ],
}

export default doc
