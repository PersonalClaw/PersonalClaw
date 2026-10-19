import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { missingCommandNotices } from './CodeCockpitPage'

describe('code command runnability', () => {
  it('turns a missing binary into a command-specific cockpit notice', () => {
    expect(missingCommandNotices({
      verify_command: {
        command: 'python -m py_compile bell_times.py',
        runnable: false,
        binary: 'python',
        reason: 'binary_not_on_path',
      },
      test_command: {
        command: 'python -m doctest bell_times.py -v',
        runnable: false,
        binary: 'python',
        reason: 'binary_not_on_path',
      },
    })).toEqual([
      {
        key: 'verify_command',
        label: 'build',
        command: 'python -m py_compile bell_times.py',
        binary: 'python',
      },
      {
        key: 'test_command',
        label: 'test',
        command: 'python -m doctest bell_times.py -v',
        binary: 'python',
      },
    ])
  })

  it('does not call a missing project manifest a missing binary', () => {
    expect(missingCommandNotices({
      verify_command: {
        command: 'npm run build',
        runnable: false,
        binary: 'npm',
        reason: 'project_manifest_missing',
      },
    })).toEqual([])
  })

  it('annotates and disables dead run controls instead of opening a terminal', () => {
    // The run controls live in the cockpit's header and the notice in its notices, each moved out of
    // `CodeCockpitPage` into its own module.
    const read = (rel: string) => readFileSync(join(process.cwd(), rel), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    const header = read('src/pages/code/cockpit/header/CockpitHeader.tsx')
    const controls = header.slice(
      header.indexOf('p.verify_command && <HeaderControl'),
      header.indexOf('onNewTarget && !active'),
    )
    expect(controls).toMatch(/missingBuild[\s\S]*?not on PATH[\s\S]*?disabled=\{!!missingBuild\}/)
    expect(controls).toMatch(/missingTest[\s\S]*?not on PATH[\s\S]*?disabled=\{!!missingTest\}/)

    const source = read('src/pages/code/cockpit/header/CockpitNotices.tsx')
    const notice = source.slice(
      source.indexOf('missingCommands.length > 0'),
      source.indexOf("p.status === 'ready' && p.project_kind"),
    )
    expect(notice).toMatch(/role="status"/)
    expect(notice).toContain('notice.command')
    expect(notice).toContain('notice.binary')
  })
})
