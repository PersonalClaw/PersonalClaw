import { useEffect, useMemo, useState } from 'react'

// ── Tailwind for a widget document, compiled HERE from the installed `tailwindcss` ─────────────
//
// A widget told "Tailwind CSS is available" used to get it by loading Tailwind's Play CDN
// (cdn.tailwindcss.com) into every document: a request to a third party from an app whose promise
// is that it is local-first and tracks nothing, and every widget unstyled offline. The compiler
// that builds this app's own stylesheet builds the widget's too. What it produces rides INSIDE the
// document as a `<style>`, so a widget needs no network and no CSP allowance, and a downloaded or
// opened-in-a-tab copy carries its styles with it.
//
// ONE compiler for the session, loaded on first use: its `build()` is synchronous, so a widget's
// document can be built in the render that shows it. Tailwind's compiler remembers every class it
// has built, so a document's stylesheet can also hold rules for another widget's classes; those
// match nothing in it.

type Compiler = { build(candidates: string[]): string }

let compiler: Compiler | null = null
let loading: Promise<Compiler | null> | null = null

/** The variant `dark:` keys off: `class="dark"` on the document's `<body>`, set from the app's
 *  resolved theme, because a frame cannot read the parent's colour scheme. */
const DARK_VARIANT = '@custom-variant dark (&:where(.dark, .dark *));'

/** Widgets were written against Tailwind v3, and v4 changed two defaults a reader sees: a bare
 *  `border` took the text colour instead of gray-200, and a button lost the pointer cursor. These
 *  are the compatibility rules Tailwind's own v4 upgrade guide gives for keeping the v3 look. */
const V3_DEFAULTS = `@layer base {
  *, ::after, ::before, ::backdrop, ::file-selector-button { border-color: var(--color-gray-200, currentColor); }
  button:not(:disabled), [role="button"]:not(:disabled) { cursor: pointer; }
}`

/** Start loading the compiler, once. Resolves to it, or to `null` when it cannot be had — a widget
 *  then renders unstyled rather than blank. */
export function loadWidgetStyles(): Promise<Compiler | null> {
  loading ??= Promise.all([import('tailwindcss'), import('tailwindcss/index.css?raw')])
    .then(async ([tailwind, index]) => {
      compiler = await tailwind.compile(`${index.default}\n${DARK_VARIANT}\n${V3_DEFAULTS}\n`)
      return compiler
    })
    .catch((e: unknown) => {
      console.warn('Widget styles could not be compiled; widgets render without Tailwind.', e)
      return null
    })
  return loading
}

// Loaded as soon as anything that renders a widget is, so it is ready before the first widget is:
// one rendered earlier starts unstyled and is rebuilt, once, when the compiler arrives.
if (typeof window !== 'undefined') void loadWidgetStyles()

/** Every class-name candidate in a document's source: its text split where no class name can go
 *  on. Tailwind's own scanner reads source the same way, as text, so a class a widget's script adds
 *  (`classList.add('hidden')`) is found as well as one in a `class` attribute. A token that is not
 *  a class compiles to nothing. */
export function classCandidates(source: string): string[] {
  return [...new Set(source.split(/[\s"'`<>]+/).filter(Boolean))]
}

/** The Tailwind CSS a document built from *source* needs, or `null` when there is no compiler —
 *  still loading, or it could not be loaded. */
export function widgetCssNow(source: string): string | null {
  if (!compiler) return null
  return compiler.build(classCandidates(source))
}

/** For a render: the CSS for *source*, `''` until the compiler has loaded, and the compiled CSS
 *  once it has (this re-renders then). `null` source — a widget still streaming — compiles
 *  nothing. */
export function useWidgetCss(source: string | null): string {
  const [ready, setReady] = useState(compiler !== null)
  useEffect(() => {
    if (ready || source === null) return
    let alive = true
    void loadWidgetStyles().then((c) => { if (alive && c) setReady(true) })
    return () => { alive = false }
  }, [ready, source])
  return useMemo(() => (source !== null && ready ? widgetCssNow(source) ?? '' : ''), [source, ready])
}
