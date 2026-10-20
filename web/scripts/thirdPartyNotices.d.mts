// Type declaration for the third-party notices generator (scripts/thirdPartyNotices.mjs), so the
// vitest suite can import it under tsc --noEmit without an implicit any.

export const NOTICES_TXT: string
export const NOTICES_JSON: string
export const RECORDS_FILE: string

export function normalizeText(text: string): string
export function declaredLicense(pkg: Record<string, unknown>): string | null
export function licenseFilesIn(dir: string): string[]
export function sourceUrl(pkg: Record<string, unknown>): string | null
export function noticeComments(source: string): string[]

/** One output of a Rolldown bundle, as far as the collector reads it. */
export type BundleOutput =
  | {
      type: 'chunk'
      fileName: string
      modules: Record<string, { renderedLength: number }>
      viteMetadata?: { importedCss: Set<string> }
    }
  | { type: 'asset'; fileName: string }

export interface NoticesBuildPlugin {
  name: string
  apply: 'build'
  configResolved(config: { root: string; build: { outDir: string } }): void
  buildStart(): void
  generateBundle(options: unknown, bundle: Record<string, BundleOutput>): void
  closeBundle: { order: 'post'; handler(this: { info?: (message: string) => void }, error?: Error): void }
}

export interface NoticesWorkerPlugin {
  name: string
  apply: 'build'
  generateBundle(options: unknown, bundle: Record<string, BundleOutput>): void
}

export interface CensusEntry {
  path: string
  name: string
  version: string | null
  license: string
  license_files: string[]
  from: string | null
  source: string | null
  record?: string
  license_from?: string
  copied_into?: string
}

export interface Census {
  schema_version: 1
  about: string
  notices: string
  packages: CensusEntry[]
  files: Record<string, string[]>
}

export function thirdPartyNotices(
  webDir: string,
  options?: { repoRoot?: string },
): {
  plugin(): NoticesBuildPlugin
  workerPlugin(): NoticesWorkerPlugin
  recordOutput(file: string, inputs: string[]): void
}

export function buildCensus(input: {
  outputs: Map<string, Set<string>>
  webDir: string
  repoRoot: string
}): { text: string; json: Census }
