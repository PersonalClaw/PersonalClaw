// Type declaration for the shared prop extractor (scripts/extractUiProps.mjs),
// so the vitest drift test can import it under tsc --noEmit without an implicit any.

export interface DerivedProp {
  name: string
  type: string
  required: boolean
}

export interface ExtractedUiProps {
  /** componentName → its own authored props (name/type/required). */
  components: Record<string, DerivedProp[]>
  /** relative filename → the component names it exports. */
  files: Record<string, string[]>
}

export function extractUiProps(uiDir: string): ExtractedUiProps
