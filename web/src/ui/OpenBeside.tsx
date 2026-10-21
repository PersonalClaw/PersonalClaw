import { createContext, useContext, useLayoutEffect, useMemo, useRef, useSyncExternalStore, type ReactNode } from 'react'

// ── A place beside the reading, for a block of rendered text ──────────────────────────────────
//
// A surface with a place beside its reading (#3820's split) provides one, and `Markdown` asked to
// (`beside`) offers each code block, diff and table an Open beside. A block is named by what it is,
// not where it sits: its address is its kind and a fingerprint of its parsed content (a fence's
// language and text, a table's cells). So the same reply names the same block live and after a
// reload, whatever the turn's position or the segments around it, and a block nested in a list or
// a quote is named, and shown, as the block it parsed to. Each block on screen registers itself
// under its address while it is mounted, and the surface shows the registered block, rendered by
// the same components, so nothing is cut out of the text again or fetched to show it.

/** A block that can open beside the reading: its address, what it is, and how to draw it there. */
export type BesideBlock = {
  address: string
  kind: 'code' | 'diff' | 'table'
  /** What the block is, which names its pane: "python code", "Diff", "Table". */
  title: string
  /** The pane's controls' noun: "code" gives "Close code". */
  label: string
  render: () => ReactNode
}

/** The blocks on screen that can open beside the reading, by address, and who is watching them. */
export class BesideRegistry {
  private byAddress = new Map<string, BesideBlock[]>()
  private watchers = new Set<() => void>()

  /** Registers `block` until the returned call takes it back. Two blocks with one content share
   *  an address; the first one mounted is the one shown. */
  add(block: BesideBlock): () => void {
    this.byAddress.set(block.address, [...(this.byAddress.get(block.address) ?? []), block])
    this.changed()
    return () => {
      const rest = (this.byAddress.get(block.address) ?? []).filter((b) => b !== block)
      if (rest.length) this.byAddress.set(block.address, rest)
      else this.byAddress.delete(block.address)
      this.changed()
    }
  }

  /** The block `address` names, or null when none on screen has it. */
  get(address: string | null): BesideBlock | null {
    return (address && this.byAddress.get(address)?.[0]) || null
  }

  subscribe = (watch: () => void): (() => void) => {
    this.watchers.add(watch)
    return () => { this.watchers.delete(watch) }
  }

  private changed(): void {
    for (const watch of this.watchers) watch()
  }
}

type Place = { open: (address: string) => void; registry: BesideRegistry }

const PlaceContext = createContext<Place | null>(null)

/** Gives the text under it a place beside the reading. `open` receives a block's address; keep it
 *  stable, since every block that offers Open beside reads it. */
export function OpenBesideProvider({ open, registry, children }: {
  /** Opens the block the address names beside the reading. */
  open: (address: string) => void
  /** Where the blocks on screen register (`new BesideRegistry()`, kept for the surface's life). */
  registry: BesideRegistry
  children: ReactNode
}) {
  const place = useMemo(() => ({ open, registry }), [open, registry])
  return <PlaceContext.Provider value={place}>{children}</PlaceContext.Provider>
}

/** The place beside the reading, or null where nothing provides one. */
export function useOpenBeside(): Place | null {
  return useContext(PlaceContext)
}

/** Keeps `block` registered while it is mounted (and, with a new address, registers it again), and
 *  its drawing current. A null block, or no place, registers nothing. */
export function useBesideRegistration(place: Place | null, block: BesideBlock | null): void {
  const entry = useRef<BesideBlock | null>(null)
  // The drawing changes with each render (a table's rows); the registered entry keeps one identity,
  // so the surface reading it re-renders only when what is registered changes.
  useLayoutEffect(() => {
    if (entry.current && block && entry.current.address === block.address) entry.current.render = block.render
  })
  const address = block?.address ?? null
  useLayoutEffect(() => {
    if (!place || !block) return
    const registered = { ...block }
    entry.current = registered
    const takeBack = place.registry.add(registered)
    return () => { entry.current = null; takeBack() }
  }, [place, address]) // eslint-disable-line react-hooks/exhaustive-deps
}

/** The registered block `address` names, kept current as blocks mount and unmount. */
export function useBesideBlock(registry: BesideRegistry, address: string | null): BesideBlock | null {
  return useSyncExternalStore(registry.subscribe, () => registry.get(address), () => null)
}

/** A short, stable fingerprint of `text` (cyrb53, base 36). Not a secret, only a name. */
export function fingerprint(text: string): string {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57
  for (let i = 0; i < text.length; i++) {
    const ch = text.charCodeAt(i)
    h1 = Math.imul(h1 ^ ch, 2654435761)
    h2 = Math.imul(h2 ^ ch, 1597334677)
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909)
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909)
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36)
}
