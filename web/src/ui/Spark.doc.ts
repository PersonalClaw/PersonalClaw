import type { UiDoc } from './uiDoc'

// Spark.tsx exports two related brand-mark lockups (Spark + Wordmark), so its doc
// default-exports an array. Both wrap ClawMark and track the active scheme gradient;
// the "kept named Spark so call sites get the claw without churn" note was a comment.
const docs: UiDoc[] = [
  {
    name: 'Spark',
    keywords: ['spark', 'brand', 'claw', 'ai', 'motif', 'thinking', 'indicator', 'gradient'],
    description:
      'The PersonalClaw brand mark used as the AI motif throughout the app — the thinking indicator, loop cycle nodes, empty states. Renders the claw logo (via ClawMark) painted with the ACTIVE scheme gradient, NOT the Gemini sparkle. Each instance gets a unique gradient id so multiple Sparks on a page never collide.',
    props: [
      { name: 'animated', description: 'Adds the ambient claw wobble (default true); suppressed under prefers-reduced-motion.' },
      { name: 'size', description: 'Pixel size of the mark (default 24).' },
    ],
    bestPractices: [
      { guidance: true, description: 'Reach for Spark as the AI motif (thinking / loop nodes / empty states) — it is deliberately kept named "Spark" so all call sites get the scheme-tinted claw without renaming churn.' },
      { guidance: false, description: 'Do not expect a Gemini-style sparkle — Spark paints the claw silhouette with the active scheme gradient, not a hardcoded icon.' },
    ],
    anatomy: ['ClawMark (unique per-instance gradient id, scheme-tinted)'],
  },
  {
    name: 'Wordmark',
    keywords: ['wordmark', 'brand', 'logo', 'lockup', 'name', 'claw', 'gradient', 'title'],
    description:
      'The inline wordmark lockup: the claw mark beside the product name, both tracking the active scheme. The name text is gradient-clipped with the scheme --grad tokens so it re-tints with the theme instead of a hardcoded color family.',
    props: [
      { name: 'label', description: "The product name to render (default 'PersonalClaw')." },
    ],
    bestPractices: [
      { guidance: true, description: 'Use Wordmark for the horizontal brand lockup (mark + name); it keeps the mark and gradient-clipped text in sync with the active scheme automatically.' },
      { guidance: false, description: 'Do not restyle the name with a fixed color — the gradient clip reads the scheme --grad tokens so the wordmark tracks any scheme or custom fork.' },
    ],
    anatomy: ['flex row', 'ClawMark', 'gradient-clipped name span (title-l)'],
  },
]

export default docs
