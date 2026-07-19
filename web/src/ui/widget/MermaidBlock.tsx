import { useEffect, useRef, useState } from 'react'
import { useMode } from '../../app/theme'

// Mermaid is ~2MB — import on demand (only when a mermaid block actually
// renders) so it stays out of the initial bundle. Cached after first import.
let mermaidPromise: Promise<typeof import('mermaid').default> | null = null
function loadMermaid(dark: boolean) {
  if (!mermaidPromise) {
    mermaidPromise = import('mermaid').then(({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, theme: dark ? 'dark' : 'default', securityLevel: 'strict' })
      return mermaid
    })
  }
  return mermaidPromise
}

let seq = 0

/** Render a ```mermaid fenced block as an SVG diagram. Errors fall back to the
 *  raw source so a malformed diagram is still readable. */
export function MermaidBlock({ code }: { code: string }) {
  const { mode } = useMode()
  const ref = useRef<HTMLDivElement>(null)
  const [err, setErr] = useState(false)
  const rendered = useRef('')

  useEffect(() => {
    if (!ref.current || rendered.current === `${code}|${mode}`) return
    rendered.current = `${code}|${mode}`
    let alive = true
    const id = `mermaid-${seq++}`
    loadMermaid(mode === 'dark')
      .then((mermaid) => mermaid.render(id, code))
      .then(({ svg }) => {
        if (!alive || !ref.current) return
        ref.current.innerHTML = ''
        ref.current.appendChild(document.createRange().createContextualFragment(svg))
        setErr(false)
      })
      .catch(() => { if (alive) setErr(true) })
    return () => { alive = false }
  }, [code, mode])

  if (err) {
    return <pre className="my-3 overflow-x-auto rounded-lg bg-surface-low px-m py-2 text-[0.8125rem]"><code className="font-mono text-on-surface-low">{code}</code></pre>
  }
  return <div ref={ref} className="my-3 flex justify-center overflow-x-auto rounded-lg bg-surface-low px-m py-3" />
}
