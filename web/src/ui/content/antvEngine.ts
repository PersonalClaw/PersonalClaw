/** Lazy AntV Infographic engine loader, shared by the preview (InfographicView)
 *  and the export path (exporters). AntV is ~8MB → loaded on demand, cached.
 *
 *  CRITICAL: AntV ships 5 built-in fonts whose configs point at a remote CDN
 *  (assets.antv.antgroup.com). On every render it injects a <link> stylesheet for
 *  EACH registered font — remote egress PClaw forbids (and our CSP blocks, spamming
 *  the console). We re-register each family with NO url (local-only) so the loader
 *  finds nothing to fetch, and default to a system stack. Local-first + silent. */

export interface InfographicInstance {
  /** Render the DSL syntax string (parsed via AntV's fault-tolerant parser). */
  render: (syntax: string) => void
  destroy: () => void
}
export type InfographicCtor = new (
  // `theme` ('light'|'dark'|…) is set at construct time — AntV's render(string)
  // parses DSL, so theme can't ride along there; we re-create on app-theme change.
  opts: { container: Element; width?: string | number; height?: string | number; theme?: string },
) => InfographicInstance

const LOCAL_STACK = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
const REMOTE_FONT_FAMILIES = ['Alibaba PuHuiTi', 'Source Han Sans', 'Source Han Serif', 'LXGW WenKai', '851tegakizatsu']

let enginePromise: Promise<InfographicCtor> | null = null

export function loadInfographicEngine(): Promise<InfographicCtor> {
  if (!enginePromise) {
    enginePromise = import('@antv/infographic').then((m) => {
      try {
        const reg = m as unknown as {
          registerFont?: (f: { fontFamily: string; baseUrl?: string; fontWeight: Record<string, string> }) => void
          setDefaultFont?: (f: string) => void
        }
        for (const fontFamily of REMOTE_FONT_FAMILIES) {
          reg.registerFont?.({ fontFamily, baseUrl: '', fontWeight: {} })
        }
        reg.setDefaultFont?.(LOCAL_STACK)
      } catch { /* engine internals changed — CSP still blocks any egress */ }
      return m.Infographic as unknown as InfographicCtor
    })
  }
  return enginePromise
}
