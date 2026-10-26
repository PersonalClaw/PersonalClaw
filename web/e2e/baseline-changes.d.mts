// Type declaration for the refresh's changed-baselines list (e2e/baseline-changes.mjs), so its
// vitest test can import it under tsc --noEmit without an implicit any.

export const WRITTEN: string[]

export interface Change {
  kind: 'added' | 'changed' | 'deleted'
  path: string
}

export function parseStatus(text: string): Change[]
export function summary(changes: Change[]): string

export interface Done {
  status: number | null
  stdout: string
  stderr: string
  error?: Error
}

export function main(options?: {
  web?: string
  env?: Record<string, string | undefined>
  log?: (line: string) => void
  run?: (command: string, args: string[], options: { cwd: string; encoding: 'utf8' }) => Done
}): number
