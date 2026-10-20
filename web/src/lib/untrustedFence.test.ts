import { describe, it, expect } from 'vitest'
import { withoutFence } from './untrustedFence'

// The markers the gateway wraps outside text in before a model reads it
// (`security.fence_untrusted`): an attributed open marker, the text on its own lines, the close.
const fence = (text: string, source = 'web_search') =>
  `<untrusted_content source=${source}>\n${text}\n</untrusted_content>`

describe('withoutFence', () => {
  it('shows the text a fence wraps, and nothing of the fence', () => {
    expect(withoutFence(fence('Changelog — libfoo 2.4 documentation'))).toBe('Changelog — libfoo 2.4 documentation')
  })

  it('takes off every marker of a text that carries several', () => {
    const shown = withoutFence(`${fence('first hit')} and ${fence('second hit', 'https://docs.example.com/libfoo/')}`)
    expect(shown).toBe('first hit and second hit')
  })

  it('keeps a JSON result valid and every field as it was', () => {
    const payload = {
      query: 'what changed in libfoo 2.4.1',
      results: [
        { title: fence('Changelog — libfoo 2.4 documentation'), url: 'https://docs.example.com/libfoo/changelog/', snippet: fence('Fixes a crash on empty feeds.'), score: 0.92 },
        { title: fence('Release notes'), url: 'https://example.org/libfoo/releases/', snippet: fence('Path: C:\\feeds\\') },
      ],
    }
    const shown = JSON.parse(withoutFence(JSON.stringify(payload)))
    expect(shown).toEqual({
      query: 'what changed in libfoo 2.4.1',
      results: [
        { title: 'Changelog — libfoo 2.4 documentation', url: 'https://docs.example.com/libfoo/changelog/', snippet: 'Fixes a crash on empty feeds.', score: 0.92 },
        { title: 'Release notes', url: 'https://example.org/libfoo/releases/', snippet: 'Path: C:\\feeds\\' },
      ],
    })
  })

  it('shows a marker that was part of the wrapped text as the text it was', () => {
    // The fence escapes a marker inside the text it wraps, so that one is content, not a fence.
    const inner = 'The page says &lt;untrusted_content&gt; in its own words.'
    expect(withoutFence(fence(inner))).toBe(inner)
  })

  it('leaves text that was never fenced exactly as it is', () => {
    for (const text of ['', 'Plain output\nacross two lines.', '{"results": []}', 'a <b>bold</b> claim']) {
      expect(withoutFence(text)).toBe(text)
    }
  })
})
