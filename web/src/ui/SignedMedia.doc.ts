import type { UiDoc } from './uiDoc'

// Doc objects for SignedMedia.tsx's elements. Authored: keywords, prose, per-prop descriptions,
// Do/Don't, anatomy. Prop type/required are DERIVED from SignedMedia.tsx at build time.
const shared = {
  bestPractices: [
    { guidance: true, description: 'Use the Signed element for any gateway URL the browser loads itself: an image, a player, a PDF, a download or a link that opens in a new tab.' },
    { guidance: false, description: 'Do not put a gateway /api URL straight into src, data or href: on a gateway that pairs its sign-ins the browser sends it without the pair, and the gateway refuses it.' },
    { guidance: true, description: 'Pass a data:, blob: or other site’s URL through it freely: the signer only ever signs this gateway’s own /api routes and leaves the rest as they are.' },
  ],
}

const docs: UiDoc[] = [
  {
    name: 'SignedImg',
    keywords: ['image', 'img', 'signed', 'url', 'artifact', 'thumbnail', 'pair'],
    description: 'An image element whose gateway URL is signed while it is shown (lib/signedUrl.ts). On a gateway that does not pair its sign-ins it is the plain image from the first render; on one that does, it draws nothing until the signature comes back, so the browser never asks for a URL the gateway would refuse.',
    props: [
      { name: 'src', description: 'The image URL. A gateway /api route is signed; any other URL is used as it is.' },
    ],
    ...shared,
    anatomy: ['the image element with the signed src, or nothing while it is signed'],
  },
  {
    name: 'SignedVideo',
    keywords: ['video', 'player', 'media', 'signed', 'url', 'pair'],
    description: 'A <video> whose gateway URL is signed, and signed again before the signature ends. A swap to the fresh signature keeps the playing position, paused or playing, and the speed.',
    props: [{ name: 'src', description: 'The video URL. A gateway /api route is signed; any other URL is used as it is.' }],
    ...shared,
    anatomy: ['<video> with the signed src, or nothing while it is signed'],
  },
  {
    name: 'SignedAudio',
    keywords: ['audio', 'player', 'media', 'signed', 'url', 'pair'],
    description: 'An <audio> whose gateway URL is signed, and signed again before the signature ends, keeping the position, paused or playing, and the speed across the swap.',
    props: [{ name: 'src', description: 'The audio URL. A gateway /api route is signed; any other URL is used as it is.' }],
    ...shared,
    anatomy: ['<audio> with the signed src, or nothing while it is signed'],
  },
  {
    name: 'SignedObject',
    keywords: ['object', 'pdf', 'embed', 'document', 'signed', 'pair'],
    description: 'An <object> (the PDF preview) whose gateway data URL is signed, with its fallback content passed through as children.',
    props: [
      { name: 'data', description: 'The document URL. A gateway /api route is signed; any other URL is used as it is.' },
    ],
    ...shared,
    anatomy: ['<object> with the signed data', 'fallback content (children)'],
  },
  {
    name: 'SignedAnchor',
    keywords: ['link', 'anchor', 'download', 'new tab', 'href', 'signed', 'pair'],
    description: 'An <a> to a gateway URL the browser follows itself: a download or a link that opens in a new tab. Until the URL is signed it is drawn with no href and aria-disabled, so a press can’t open the unsigned URL.',
    props: [
      { name: 'href', description: 'Where it goes. A gateway /api route is signed; any other URL is used as it is.' },
    ],
    ...shared,
    anatomy: ['<a> with the signed href (no href while it is signed)', 'content (children)'],
  },
  {
    name: 'SignedTextLink',
    keywords: ['link', 'text link', 'download', 'signed', 'pair'],
    description: 'A TextLink whose href is a gateway URL the browser follows itself, signed the same way. Every other TextLink prop is passed through as TextLink takes it.',
    props: [
      { name: 'href', description: 'Where it goes. A gateway /api route is signed; any other URL is used as it is.' },
      ...['aria-controls', 'aria-expanded', 'aria-label', 'children', 'className', 'disabled', 'external', 'icon', 'iconPosition', 'iconSize', 'ink', 'onClick', 'size', 'title']
        .map((name) => ({ name, description: `Passed through to TextLink, as TextLink takes ${name}.` })),
    ],
    ...shared,
    anatomy: ['TextLink with the signed href'],
  },
]

export default docs
