/** A react artifact's library card previews the component the way its full view does: compiled in
 *  this app, React inside the document, nothing fetched — and a component that does not compile
 *  says so rather than drawing an empty frame. */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import type { Artifact } from '../../lib/api'
import { ArtifactCard } from './ArtifactCard'
import { registerBuiltinContentTypes } from '../../ui/content/registerBuiltins'

registerBuiltinContentTypes()

const BODIES: Record<string, string> = {
  'a-counter': 'function App() { return <p>hi</p> }',
  'a-broken': 'function App( { return <p>',
}

vi.mock('../../lib/api', async (importActual) => {
  const actual = await importActual<typeof import('../../lib/api')>()
  return {
    ...actual,
    api: { ...actual.api, artifact: async (slug: string) => ({ slug, content: BODIES[slug] }) as unknown as Artifact },
  }
})

// The compile runs in the code editor's language worker, which jsdom cannot host. Marking what it
// returns shows the card built its document from the COMPILED component.
vi.mock('../../ui/widget/reactJsx', () => ({
  transformJsx: async (jsx: string) => (jsx.includes('App( {') ? { error: 'Line 1: bad' } : { code: `/*compiled*/${jsx}` }),
}))

const art = (slug: string): Artifact => ({
  slug, name: slug, kind: 'react', source: 'chat', version: 1,
  created_at: '2026-08-16T00:00:00Z', updated_at: '2026-08-16T00:00:00Z',
} as unknown as Artifact)

describe("a react artifact's card", () => {
  it('previews the compiled component, with React inside the document', async () => {
    const view = render(<ArtifactCard art={art('a-counter')} onOpen={() => {}} />)
    const frame = await waitFor(() => {
      const f = view.container.querySelector('iframe')
      if (!f) throw new Error('the card drew no preview frame')
      return f
    })
    const doc = frame.getAttribute('srcdoc') ?? ''
    expect(doc).toContain('/*compiled*/function App()')
    expect(doc).toContain("'react-dom/client'")
    expect(doc).not.toMatch(/<script[^>]*\ssrc=/i)
  })

  it('says a component that does not compile does not compile', async () => {
    const view = render(<ArtifactCard art={art('a-broken')} onOpen={() => {}} />)
    expect(await screen.findByText('Does not compile')).toBeTruthy()
    expect(view.container.querySelector('iframe')).toBeNull()
  })
})
