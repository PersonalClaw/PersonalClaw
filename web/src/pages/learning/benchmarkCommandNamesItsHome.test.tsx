import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BenchmarkPanel } from './BenchmarkPanel'

// ── The benchmark's command names the home it must run in ────────────────────────────────────
//
// The runner writes its report under the home it runs in. A gateway running from another home
// showed the bare command, which wrote the report into the default home, where this page never
// reads it. The not-run answer now names such a home, and the command carries it.

describe('the skill-impact benchmark command', () => {
  it('names a home that is not the default one, quoted for a shell', () => {
    render(<BenchmarkPanel view={{ ran: false, home: "/home/user/work home/it's" }} error={null} onRetry={() => {}} />)
    expect(screen.getByText(`PERSONALCLAW_HOME='/home/user/work home/it'\\''s' python scripts/learning_benchmark.py --preflight`))
      .toBeTruthy()
    expect(screen.getByText(/the report is written under the home the command runs in/)).toBeTruthy()
  })

  it('stays the bare command on the default home', () => {
    render(<BenchmarkPanel view={{ ran: false }} error={null} onRetry={() => {}} />)
    expect(screen.getByText('python scripts/learning_benchmark.py --preflight')).toBeTruthy()
    expect(screen.queryByText(/the report is written under the home the command runs in/)).toBeNull()
  })
})
