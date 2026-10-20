// Type declaration for the leg merge (e2e/merge-legs.mjs), so the vitest test of it can import
// `withStatefulMetadata` under tsc --noEmit without an implicit any.

export interface MergedReport {
  config?: { metadata?: Record<string, unknown> & { statefulSeed?: string; statefulOrder?: string[] } }
  suites?: unknown[]
  [key: string]: unknown
}

export function withStatefulMetadata<R extends MergedReport>(report: R): R & { config: { metadata: Record<string, unknown> & { statefulSeed?: string; statefulOrder?: string[] } } }
