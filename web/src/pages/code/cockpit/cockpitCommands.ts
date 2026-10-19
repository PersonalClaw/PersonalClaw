import { type CodeProject } from '../../../lib/api'

// ── The cockpit's build and test commands ──────────────────────────────────────────────────────
//
// A build or test command whose program is not on PATH, said where the cockpit offers to run it,
// and a command shortened for a menu label.

// Truncate a build/test command for an overflow-menu label. A blunt slice(0,48) on
// the WHOLE "Run build (<cmd>)" string cut the command mid-word and dropped the
// closing paren (e.g. "Run build (python -m pytest --cov=src --c") — reads as broken.
// Cap just the command + add an ellipsis so the wrapper "(…)" always stays intact.
export const cmdLabel = (cmd: string, max = 36): string => {
  const c = (cmd || '').trim()
  return c.length > max ? `${c.slice(0, max - 1)}…` : c
}

export interface MissingCommandNotice {
  key: 'verify_command' | 'test_command'
  label: 'build' | 'test'
  command: string
  binary: string
}

/** Missing-binary diagnostics only. A missing project manifest is a stage-timing
 *  state, not a dead cockpit command, so it must not disable the user's run control. */
export function missingCommandNotices(
  checks: CodeProject['command_runnability'],
): MissingCommandNotice[] {
  const out: MissingCommandNotice[] = []
  for (const [key, label] of [
    ['verify_command', 'build'],
    ['test_command', 'test'],
  ] as const) {
    const check = checks?.[key]
    if (check?.runnable === false && check.reason === 'binary_not_on_path' && check.binary) {
      out.push({ key, label, command: check.command, binary: check.binary })
    }
  }
  return out
}
