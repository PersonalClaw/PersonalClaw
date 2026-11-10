import type { UiDoc } from './uiDoc'

// Doc object for ChoiceList: the vertical pick-one list with a line under each choice. Authored:
// keywords, prose, per-prop descriptions, Do/Don't, anatomy. Prop type/required are DERIVED from
// ChoiceList.tsx at build time.
const doc: UiDoc = {
  name: 'ChoiceList',
  keywords: ['choice', 'radio', 'radiogroup', 'single-select', 'list', 'options', 'pick', 'preference'],
  description:
    'A vertical pick-one list where each choice needs a line saying what it does: a role="radiogroup" named by its heading, each row a role="radio" named by its label and described by its hint. One tab stop for the group, on the checked row, even one that cannot be picked now, or on the first row that can be picked when none is; the arrows move AND select, with Home and End, as Segmented’s do. A disabled row stays in the list, is skipped by the arrows (from one, they go to the nearest row that can be picked in their direction) and says why in its hint, at full contrast while its radio and label dim. Rows are at least 40 px tall, and a long label or hint wraps anywhere. First used by Chat preferences.',
  props: [
    { name: 'labelledBy', description: 'The id of the heading that names the group (`aria-labelledby`).' },
    { name: 'options', description: 'The choices ({ key, label, hint?, disabled? }). `hint` is the row’s description; a `disabled` row says why in it.' },
    { name: 'value', description: 'The checked choice’s key (controlled). A key no row has checks none.' },
    { name: 'onChange', description: 'Fires with the picked key, on a click and on every arrow, Home or End move.' },
    { name: 'checkedRef', description: 'Reaches the row that holds the tab stop, so a window can open with focus on it.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for ChoiceList when each choice needs a sentence of its own (a preference with consequences); a short strip of words is Segmented.' },
    { guidance: true, description: 'Name the group with a visible heading and pass its id as `labelledBy`.' },
    { guidance: true, description: 'Treat each change as the pick: the arrows select as they move, so a host that writes on change keeps only the latest answer.' },
    { guidance: false, description: 'Do not drop a choice that cannot be picked now: keep it, `disabled`, with a hint that says why.' },
  ],
  anatomy: ['role="radiogroup" column', 'per-choice button (role="radio", aria-checked, aria-describedby its hint, a roving tab stop)', 'check mark (aria-hidden)', 'label and hint'],
}

export default doc
