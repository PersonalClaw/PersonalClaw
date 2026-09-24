// The typed doc-object shape for `ui/` primitives (Platform-Legibility §5).
//
// Each `web/src/ui/<Name>.tsx` primitive gains a co-located `<Name>.doc.ts` that
// default-exports one or more `UiDoc`s — the Astryx `.doc.mjs` pattern: the
// conventions currently living as comments (the HeaderActions ordering tenet, the
// SidePanel `urlKey` contract, the token-lint ratchet) become machine-readable
// data an app-building agent can `ui_search`/`ui_get`.
//
// AUTHORED here: name, keywords, description, per-prop descriptions, bestPractices,
// anatomy. DERIVED at build time (never authored — see scripts/extractUiProps.mjs):
// each prop's `type` and `required`, straight from the TypeScript source. The drift
// test asserts the authored prop set equals the compiler-derived set, so a prop can
// never be added to a component without being documented, and the type half can
// never rot. This is the §1 "describe from the source; drift is a test failure"
// thesis applied to the component kit.

/** One prop's authored documentation. `type`/`required` are filled at build time. */
export interface UiDocProp {
  /** The prop name — MUST match a real prop of the component (drift-tested). */
  name: string
  /** One line: what it does / when to pass it. */
  description: string
  /** Derived from the TS type at build time; leave unset when authoring. */
  type?: string
  /** Derived from the TS type at build time; leave unset when authoring. */
  required?: boolean
}

/** A machine-readable Do (`guidance: true`) or Don't (`guidance: false`). */
export interface UiDocPractice {
  /** true = "do this", false = "don't do this". */
  guidance: boolean
  description: string
}

/** The doc object for one exported `ui/` component. */
export interface UiDoc {
  /** The exported component name — MUST match an export of the .tsx (drift-tested). */
  name: string
  /** Search terms for the `ui_search` inverted index (lowercased at index time). */
  keywords: string[]
  /** One-to-three sentences: what the component is and when to reach for it. */
  description: string
  /** Authored per-prop docs; type/required are merged in from the source at build. */
  props: UiDocProp[]
  /** Machine-readable Do/Don't rules (the conventions that were once comments). */
  bestPractices: UiDocPractice[]
  /** The parts that make up the component, top-level to leaf (structural map). */
  anatomy: string[]
}
