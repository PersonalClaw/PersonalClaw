// Type declaration for e2e/on-shard.mjs, so its vitest test imports it under tsc --noEmit.

export type Decision = { run: string[]; skip?: undefined; refuse?: undefined } | { skip: string; run?: undefined; refuse?: undefined } | { refuse: string; run?: undefined; skip?: undefined }

export function decide(argv: string[]): Decision
export function main(
  argv: string[],
  run?: (command: string, args: string[]) => { status: number | null; error?: Error },
): number
