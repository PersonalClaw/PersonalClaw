import { useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'
import type { PreviewProps } from './contentTypes'
import { loadInfographicEngine } from './antvEngine'

/** Render an `infographic` artifact (AntV declarative DSL) to SVG. The DSL is
 *  highly fault-tolerant, so a partial body during streaming renders progressively
 *  (the registry marks this type `streaming: true`) — re-render on every content
 *  change rather than holding for completion. Malformed syntax falls back to the
 *  raw source so the content is never lost. */
export function InfographicView({ content, mode }: PreviewProps) {
  const hostRef = useRef<HTMLDivElement | null>(null)
  // The live engine instance — kept across renders so streaming calls .render(buffer)
  // on the SAME instance (AntV diffs internally) instead of re-instantiating.
  const engineRef = useRef<{ render: (s: string) => void; destroy: () => void } | null>(null)
  const [failed, setFailed] = useState(false)

  // Instantiate the engine once the module + container are ready; tear down on unmount.
  // Re-instantiate when the app THEME flips (theme is a construct-time option — AntV's
  // render(string) parses DSL, so it can't carry theme) so the infographic follows
  // light/dark like every other renderer.
  useEffect(() => {
    let alive = true
    loadInfographicEngine().then((Infographic) => {
      if (!alive || !hostRef.current) return
      try {
        engineRef.current = new Infographic({ container: hostRef.current, width: '100%', height: '100%', theme: mode })
        engineRef.current.render(content)
        setFailed(false)
      } catch { setFailed(true) }
    }).catch(() => { if (alive) setFailed(true) })
    return () => {
      alive = false
      try { engineRef.current?.destroy() } catch { /* engine already torn down */ }
      engineRef.current = null
    }
  // Re-init on theme change; content updates go through the render effect below.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode])

  // Re-render on content change (incl. streaming partials) once the engine exists.
  useEffect(() => {
    if (!engineRef.current) return
    try { engineRef.current.render(content); setFailed(false) }
    catch { setFailed(true) }
  }, [content])

  if (failed) {
    return (
      <pre className="m-l overflow-auto rounded-lg bg-surface-low px-m py-3 font-mono text-on-surface-low text-[0.8125rem] leading-relaxed whitespace-pre-wrap">{content}</pre>
    )
  }
  return (
    <div className="relative h-full w-full">
      <div ref={hostRef} className="flex h-full w-full items-center justify-center p-l [&_svg]:max-h-full [&_svg]:max-w-full" />
      {!engineRef.current && <div className="pointer-events-none absolute inset-0 flex items-center justify-center"><Loader2 size={20} className="animate-spin text-on-surface-low" /></div>}
    </div>
  )
}
