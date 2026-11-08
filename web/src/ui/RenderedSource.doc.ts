import type { UiDoc } from './uiDoc'

// Doc object for RenderedSource: an authored source shown rendered, with its raw source a press away.
const doc: UiDoc = {
  name: 'RenderedSource',
  keywords: ['markdown', 'source', 'raw', 'rendered', 'template', 'prompt', 'toggle'],
  description:
    'An authored source (a prompt, a template, an instruction file) shown rendered by default through `Markdown`, with a Raw ⇄ Rendered switch to the source as written. The switch is the shared Button, and its label and title both flip, so it needs no second channel. Lifted out of the prompt inspector, which fills each variable\'s default in through `transform`.',
  props: [
    { name: 'source', description: 'The source as written. Nothing shows a dash in either view.' },
    { name: 'label', description: 'The section caption (default "Template").' },
    { name: 'transform', description: 'What the rendered view reads instead of the source (the prompt inspector fills in variable defaults). The raw view always shows the source.' },
    { name: 'rawTitle', description: 'The switch title while the rendered view shows (default "Show raw template").' },
    { name: 'renderedTitle', description: 'The switch title while the raw view shows (default "Show rendered").' },
  ],
  bestPractices: [
    { guidance: true, description: 'Use RenderedSource where the owner reads an authored source that also runs as written: rendered to read, raw to check what runs.' },
    { guidance: false, description: 'Do not use it for what was sent or will run verbatim (tool arguments, a resolved prompt, a drafted reply): those stay literal.' },
  ],
  anatomy: ['caption', 'Raw ⇄ Rendered switch (Button, secondary, xs)', 'rendered view (Markdown in a surface)', 'raw view (pre, monospace, wrapping)'],
}

export default doc
