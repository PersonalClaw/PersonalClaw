// Type declaration for the pinned-image runner (e2e/run-pinned.mjs), so its vitest test can
// import it under tsc --noEmit without an implicit any.

export const PIN_FILE: string
export const MANIFEST: string
export const COMPARED: string[]
export const PIN_KEYS: string[]
export const PLATFORM: string
export const LOOPBACK: RegExp
export const UPDATE_SNAPSHOTS: string

export class Refusal extends Error {
  status: number
  constructor(message: string, status?: number)
}

export interface Pin {
  image: string
  platform: string
  playwright: string
  tag: string
  index: string
}

export interface RunResult {
  status: number | null
  signal?: string | null
  stdout?: string
  stderr?: string
  error?: (Error & { code?: string }) | undefined
}

export function lockVersions(lock: unknown): { test?: string; playwright?: string; core?: string }
export function checkPin(pin: unknown, lock: unknown): string[]
export function compareEnvironment(manifest: unknown, current: unknown, pin: Pin): string[]
export interface Server {
  endpoint: string
  stop: () => Promise<void>
}

export function hostMounts(options: { root: string; execPath: string; exists?: (file: string) => boolean }): string[]
export function dockerArgs(options: {
  root: string
  mounts: string[]
  pin: Pin
  uid: number
  gid: number
  execPath: string
  network: 'none' | 'host'
  name?: string
  command: string[]
}): string[]
export function playwrightCli(web: string): string
export function startServer(command: string, args: string[], options: { log: (line: string) => void }): Promise<Server>
export function playwrightCoreCli(web: string): string
export function main(
  argv?: string[],
  options?: {
    web?: string
    platform?: string
    arch?: string
    execPath?: string
    uid?: number
    gid?: number
    env?: Record<string, string | undefined>
    log?: (line: string) => void
    mounts?: () => string[]
    cli?: () => string
    coreCli?: () => string
    run?: (
      command: string,
      args: string[],
      options?: { capture?: boolean; env?: Record<string, string | undefined>; cwd?: string },
    ) => Promise<RunResult>
    serve?: (command: string, args: string[]) => Promise<Server>
  },
): Promise<number>
