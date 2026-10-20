/** A widget's Tailwind is compiled in this app, by the compiler that builds the app's own
 *  stylesheet, and rides inside the widget's document. These run the real compiler: a stub would
 *  prove the wiring and nothing about what a widget looks like. */
import { describe, it, expect } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { classCandidates, loadWidgetStyles, useWidgetCss, widgetCssNow } from './widgetStyles'

/** Compiled CSS for *source*, once the compiler is in. */
async function cssFor(source: string): Promise<string> {
  expect(await loadWidgetStyles(), 'the compiler did not load').not.toBeNull()
  const css = widgetCssNow(source)
  if (css === null) throw new Error('the compiler loaded but reports none')
  return css
}

const withoutComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '')

describe("a widget's Tailwind is compiled here", () => {
  it('compiles the classes a widget uses — plain, arbitrary-value and variant', async () => {
    const css = await cssFor('<div class="p-4 bg-[var(--card)] hover:underline md:grid-cols-2">x</div>')
    expect(css).toMatch(/\.p-4\s*\{/)
    expect(css).toContain('.bg-\\[var\\(--card\\)\\]')
    expect(css).toContain('.hover\\:underline')
    expect(css).toContain('.md\\:grid-cols-2')
  })

  it('points at nothing outside the document', async () => {
    const css = withoutComments(await cssFor('<p class="p-4 font-sans">x</p>'))
    expect(css).not.toMatch(/@import|url\(|https?:\/\//)
  })

  it('keys dark: off the class on the document body, not the OS colour scheme', async () => {
    // A frame cannot read the parent's theme; the host puts the app's resolved mode on <body>.
    const css = await cssFor('<p class="dark:underline">x</p>')
    expect(css).toContain(':where(.dark, .dark *)')
    expect(css).not.toContain('prefers-color-scheme')
  })

  it('keeps the v3 look for a bare border and for a button', async () => {
    const css = await cssFor('<button class="border">x</button>')
    expect(css).toContain('border-color: var(--color-gray-200, currentColor)')
    expect(css).toMatch(/button:not\(:disabled\), \[role="button"\]:not\(:disabled\) \{\s*cursor: pointer;/)
    // The variable the border rule reads is defined, so the rule is not a no-op.
    expect(css).toMatch(/--color-gray-200:/)
  })

  it('finds a class a script adds as well as one written in markup', () => {
    const found = classCandidates(`<b class="a-1 b-2">x</b><script>el.classList.add('hidden'); el.className = \`flex\`</script>`)
    expect(found).toEqual(expect.arrayContaining(['a-1', 'b-2', 'hidden', 'flex']))
  })
})

describe('a render gets the compiled CSS', () => {
  it('as soon as the compiler is in', async () => {
    const { result } = renderHook(() => useWidgetCss('<p class="p-6">x</p>'))
    await waitFor(() => expect(result.current).toMatch(/\.p-6\s*\{/))
  })

  it('and nothing while a widget is still streaming', () => {
    const { result } = renderHook(() => useWidgetCss(null))
    expect(result.current).toBe('')
  })
})
