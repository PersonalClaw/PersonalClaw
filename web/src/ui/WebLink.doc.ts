import type { UiDoc } from './uiDoc'

// Doc object for WebLink — the one link out of the app that rendered text and its rows use.
const doc: UiDoc = {
  name: 'WebLink',
  keywords: ['link', 'external', 'href', 'url', 'scheme', 'javascript', 'new tab', 'system browser', 'source'],
  description:
    "A link out of the app: the path `Markdown`'s links take, and the one every row drawn from rendered text (a turn's sources) takes too. Only a web or email address opens (`webHref`: http, https, mailto, read by the browser's own URL parser, so `java<TAB>script:` is refused where a regex would pass it); it opens in a new tab with `rel=\"noopener noreferrer\"`, which the desktop app hands to the system browser. Any other address keeps its words, styled as the link, and goes nowhere.",
  props: [
    { name: 'href', description: 'The address as it was written or recorded. Refused unless it parses as an http, https or mailto address.' },
    { name: 'children', description: 'The link text, and the text shown in its place when the address is refused.' },
    { name: 'className', description: 'The link\'s look, on the anchor and on the refused span alike.' },
    { name: 'title', description: 'The tooltip of an opened link (a refused one says why it does not open).' },
    { name: 'aria-label', description: 'The accessible name, when the visible text alone does not say where the link goes.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Use it for any link whose address came from a model, a tool result or a stored body, so it is held to the same schemes as the text it came from.' },
    { guidance: false, description: 'Do not build an `<a href>` of your own for such an address: a second path is a second allowlist to keep in step.' },
  ],
  anatomy: ['an <a target="_blank" rel="noopener noreferrer"> for a web or email address', 'a <span> with the same look for any other'],
}

export default doc
