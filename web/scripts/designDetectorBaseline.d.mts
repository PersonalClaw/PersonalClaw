// Type declaration for the design detector's baseline comparator (scripts/designDetectorBaseline.mjs),
// so the vitest suite can import it under tsc --noEmit without an implicit any.

/** One finding, its file relative to web/. The line is carried but not compared. */
export interface Finding {
  file: string
  rule: string
  line?: number
}

/** One baseline line: how many of a rule's findings a file has. */
export interface BaselineCount {
  file: string
  rule: string
  count: number
}

export const BASELINE_FILE: string

export function baselineLine(count: BaselineCount): string
export function parseBaseline(text: string): { counts: Map<string, BaselineCount>; problems: string[] }
export function compareFindings(findings: readonly Finding[], baselineText: string): string[]
