import type { UiDoc } from './uiDoc'

// Doc object for RemoteImage. Prop type/required are DERIVED from RemoteImage.tsx at build time.
const doc: UiDoc = {
  name: 'RemoteImage',
  keywords: ['image', 'remote', 'placeholder', 'load', 'host', 'untrusted', 'privacy'],
  description:
    'An image from another site, in text the dashboard did not write: a placeholder that shows an image icon, the image’s words and the host it would come from, and fetches nothing. Its Load image button asks the gateway to fetch the image through the owner’s network settings, then shows it from a blob: address that is let go when the image leaves the page. A refusal shows the gateway’s sentence in its place; one a later press could clear (the gateway was busy, the site was down) keeps the button beside it. A Load still in flight is let go when the image leaves the page or its address changes. The page’s own policy loads no image from another host, so this is the one way such an image is shown.',
  props: [
    { name: 'src', description: 'The image’s https address, as the text named it. Shown only as the button’s tooltip and description; the visible text names its host.' },
    { name: 'alt', description: 'The image’s words from the text, shown in the placeholder and kept as the alt of the loaded image.' },
  ],
  bestPractices: [
    { guidance: true, description: 'Reach for it for any image address a renderer did not make itself: a model reply, a tool result, a message, a knowledge body, an image file whose content is an address.' },
    { guidance: false, description: 'Do not draw such an address with an image element, or prefetch, preconnect or show a favicon for its host: each of those is the request this exists to hold back.' },
  ],
  anatomy: ['image icon', 'the image’s words', 'its host', 'Load image button (name includes the host; tooltip is the full address)', 'the refusal sentence, in the button’s place (beside it when a later press could clear it)', 'the loaded image'],
}

export default doc
