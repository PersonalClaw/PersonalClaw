import { useCallback, useState } from 'react'

// ── The session inspector's sections, and how the owner left them ──────────────────────────────
//
// The Activity rail beside a chat is its session inspector (#3820's Oct 18 amendment): a column of
// sections, each a disclosure with a one-line summary while it is folded. Which are open and the
// order they are in are the owner's, for this browser (`localStorage`), so a reload, another chat
// and another tab show the sections as she left them.

export const SECTIONS = ['environment', 'context', 'connectors', 'outputs', 'files', 'sources', 'background', 'side'] as const
export type SectionId = typeof SECTIONS[number]

export const SECTION_TITLES: Record<SectionId, string> = {
  environment: 'Environment', context: 'Context', connectors: 'Connectors', outputs: 'Outputs',
  files: 'Files', sources: 'Sources', background: 'Background tasks', side: 'Side question',
}

/** Open until she folds them: what the chat made, what it read, and the work still running. */
const OPEN_BY_DEFAULT: SectionId[] = ['outputs', 'sources', 'background']

const KEY = 'pc:chat-inspector'

export interface InspectorLayout { order: SectionId[]; open: SectionId[] }

const isSection = (v: unknown): v is SectionId => typeof v === 'string' && (SECTIONS as readonly string[]).includes(v)

/** The stored layout, with any section it does not name (a new one) at its default place, and
 *  anything it names that is no longer a section dropped. */
export function readLayout(): InspectorLayout {
  let raw: unknown = null
  try { raw = JSON.parse(localStorage.getItem(KEY) ?? 'null') } catch { raw = null }
  const stored = raw && typeof raw === 'object' ? raw as { order?: unknown; open?: unknown } : {}
  const order = Array.isArray(stored.order) ? [...new Set(stored.order.filter(isSection))] : []
  for (const id of SECTIONS) if (!order.includes(id)) order.splice(Math.min(SECTIONS.indexOf(id), order.length), 0, id)
  const open = Array.isArray(stored.open) ? [...new Set(stored.open.filter(isSection))] : [...OPEN_BY_DEFAULT]
  return { order, open }
}

function writeLayout(layout: InspectorLayout): void {
  try { localStorage.setItem(KEY, JSON.stringify(layout)) } catch { /* private mode: it lasts this page */ }
}

/** `id` moved one place up (`-1`) or down (`1`) in `order`; the same order at either end. */
export function moved(order: SectionId[], id: SectionId, by: -1 | 1): SectionId[] {
  const at = order.indexOf(id)
  const to = at + by
  if (at < 0 || to < 0 || to >= order.length) return order
  const next = [...order]
  next.splice(at, 1)
  next.splice(to, 0, id)
  return next
}

/** The layout and its two changes, each kept as it is made. */
export function useInspectorLayout() {
  const [layout, setLayout] = useState<InspectorLayout>(readLayout)
  const change = useCallback((next: (l: InspectorLayout) => InspectorLayout) => {
    setLayout((l) => { const n = next(l); writeLayout(n); return n })
  }, [])
  const toggle = useCallback((id: SectionId) => change((l) => ({
    ...l, open: l.open.includes(id) ? l.open.filter((x) => x !== id) : [...l.open, id],
  })), [change])
  const move = useCallback((id: SectionId, by: -1 | 1) => change((l) => ({ ...l, order: moved(l.order, id, by) })), [change])
  return { layout, toggle, move }
}
