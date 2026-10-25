// Type declaration for the visual goldens' coverage check (e2e/visual-coverage.mjs), so its
// vitest tests can import it under tsc --noEmit without an implicit any.

export const GOLDENS: string
export const CAPTURED_PLATFORMS: string[]
export function expectedKeys(): string[]
export function byPlatform(dir?: string): Map<string, Set<string>>
export function missingGoldens(dir?: string): string[]
export function main(options?: { dir?: string; log?: (line: string) => void }): number
