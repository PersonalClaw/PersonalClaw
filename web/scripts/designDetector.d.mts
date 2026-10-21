// Type declaration for the design detector's runner (scripts/designDetector.mjs), so the vitest
// suite can import it under tsc --noEmit without an implicit any.

import type { Finding } from './designDetectorBaseline.mjs'

/** What one run of the detector left: its exit status and both streams. */
export interface DetectorRun {
  status: number | null
  stdout: string
  stderr: string
}

/** The locked binary and how it is run, or why it cannot be. */
export type DetectorCommand =
  | { bin: string; args: string[]; cwd: string; env: Record<string, string>; problem?: undefined }
  | { problem: string; bin?: undefined }

/** The parts of a CommonJS require the runner reads the installed tree through. */
export interface Resolver {
  (id: string): { version?: string; optionalDependencies?: Record<string, string> }
  resolve(id: string): string
}

export function platformPackage(platform?: string, arch?: string): string
export function detectorCommand(options?: {
  require?: Resolver
  exists?: (path: string) => boolean
  platform?: string
  arch?: string
}): DetectorCommand
export function configProblems(read?: (rel: string) => string | undefined): string[]
export function readRun(run: DetectorRun, webRoot?: string): { findings: Finding[]; problems: string[] }
export function baselineText(findings: readonly Finding[]): string
