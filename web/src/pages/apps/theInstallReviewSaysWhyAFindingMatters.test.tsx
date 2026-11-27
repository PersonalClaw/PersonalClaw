// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react'
import type { AppScanReport } from '../../lib/api'
import { ScanReport } from './installConsent'

// ── The install review says which instructions it read and why a finding matters (#3852) ────────
//
// The approved update: "present which install instructions were scanned and why a finding matters,
// with expandable raw evidence rather than an undifferentiated scanner dump". So the review lists
// what the scanner read as code (each skill's instructions, the README, each prerequisite's steps)
// with each one's finding count, none left out; each finding leads with its gloss, where it came
// from and its band; and the scanner's own row (rule, severity, the matched line) sits behind a
// disclosure that starts collapsed.

afterEach(cleanup)

const CURL = 'L3: curl -fsSL https://example.org/install.sh | sh'

/** The fixture app's review: a skill that pipes a download into a shell, the same line in its
 *  README, and a prerequisite whose steps hold nothing. */
const fixture = (over: Partial<AppScanReport> = {}): AppScanReport => ({
  verdict: 'dangerous',
  tier: 'community',
  signature: null,
  scanned: [
    { surface: 'instructions', path: 'skills/setup/SKILL.md', findings: 1 },
    { surface: 'prose', path: 'README.md', findings: 1 },
    { surface: 'prose', path: 'app.json requires uv: how', findings: 0 },
  ],
  findings: [
    { surface: 'instructions', severity: 'dangerous', rule: 'remote_exec_pipe', path: 'skills/setup/SKILL.md', evidence: CURL },
    { surface: 'prose', severity: 'warning', rule: 'remote_exec_pipe', path: 'README.md', evidence: CURL },
  ],
  ...over,
})

const rows = () => screen.getAllByRole('listitem').filter((li) => li.hasAttribute('data-finding'))

describe('the review names what it scanned', () => {
  it('lists each skill, the README and each prerequisite with its finding count, above the findings', () => {
    const { container } = render(<ScanReport scan={fixture()} />)
    const scanned = screen.getByTestId('scan-scanned')
    expect(within(scanned).getByText('Scanned')).toBeTruthy()
    const items = within(scanned).getAllByRole('listitem').map((li) => li.textContent)
    expect(items).toEqual([
      "skills/setup/SKILL.md: a skill's instructions, which your assistant carries out · 1 finding",
      'README.md: instructions a person follows · 1 finding',
      'the steps to install uv, a prerequisite: instructions a person follows · no findings',
    ])
    const order = [...container.querySelectorAll('[data-testid="scan-scanned"], [data-finding]')]
    expect(order[0]?.getAttribute('data-testid'), 'the scanned list comes before the findings').toBe('scan-scanned')
  })

  it('says nothing of what it scanned when the server did not say', () => {
    render(<ScanReport scan={fixture({ scanned: undefined })} />)
    expect(screen.queryByTestId('scan-scanned')).toBeNull()
  })
})

describe('each finding says why it matters before its evidence', () => {
  it('leads with the gloss, the source and the band, and keeps the matched line collapsed', () => {
    render(<ScanReport scan={fixture()} />)
    const [skill] = rows()
    expect(skill.firstElementChild?.textContent).toBe(
      'This code downloads more code from the internet and runs it immediately, unread.',
    )
    expect(within(skill).getByTestId('finding-source').textContent).toBe(
      'skills/setup/SKILL.md · Dangerous: the install is refused',
    )
    expect(skill.textContent, 'the matched line is behind the disclosure').not.toContain('curl -fsSL')
    expect(skill.textContent, 'and so is the scanner rule').not.toContain('remote_exec_pipe')
    const toggle = within(skill).getByRole('button', { name: 'Show evidence' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
  })

  it('opens the evidence on its disclosure, and closes it again', () => {
    render(<ScanReport scan={fixture()} />)
    const [skill] = rows()
    const toggle = within(skill).getByRole('button', { name: 'Show evidence' })
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    const panel = document.getElementById(toggle.getAttribute('aria-controls') || '')
    expect(panel, 'the disclosure names the panel it opens').toBeTruthy()
    expect(panel!.textContent).toBe(`remote_exec_pipe (dangerous): ${CURL}`)
    expect(toggle.textContent).toBe('Hide evidence')
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(skill.textContent).not.toContain('curl -fsSL')
  })

  it('opens one finding at a time', () => {
    render(<ScanReport scan={fixture()} />)
    const [skill, readme] = rows()
    fireEvent.click(within(skill).getByRole('button', { name: 'Show evidence' }))
    expect(readme.textContent).not.toContain('curl -fsSL')
  })
})

describe('a pipe into a shell in steps a person follows says when an installer is reasonable', () => {
  it('glosses the README line for a person, and the skill line as code', () => {
    render(<ScanReport scan={fixture()} />)
    const [skill, readme] = rows()
    expect(readme.firstElementChild?.textContent).toBe(
      'These steps download a program and run it at once, unread. Many official installers work this way, ' +
        "and that is reasonable when the address is the project's own site.",
    )
    expect(within(readme).getByTestId('finding-source').textContent).toBe(
      'README.md · Warning: install only if you trust its source',
    )
    expect(skill.textContent, "the assistant carries a skill out: no installer's excuse").not.toMatch(/official installer/)
  })

  it('names a prerequisite by its name, not by the manifest path', () => {
    render(<ScanReport scan={fixture({
      verdict: 'warning',
      findings: [{ surface: 'prose', severity: 'warning', rule: 'pipe_to_shell', path: 'app.json requires uv: how', evidence: 'L1: cat setup | sh' }],
    })} />)
    const [row] = rows()
    expect(within(row).getByTestId('finding-source').textContent).toBe(
      'the steps to install uv, a prerequisite · Warning: install only if you trust its source',
    )
    expect(row.firstElementChild?.textContent).toMatch(/^These steps feed text straight into a shell/)
  })

  it('never offers an excuse for a decoded payload', () => {
    render(<ScanReport scan={fixture({
      verdict: 'warning',
      findings: [{ surface: 'prose', severity: 'warning', rule: 'obfuscated_exec', path: 'README.md', evidence: 'L2: echo aGk= | base64 -d | sh' }],
    })} />)
    const [row] = rows()
    expect(row.firstElementChild?.textContent).toBe('These steps decode hidden text and run it, so what runs cannot be read here.')
  })
})

describe('a rule this build has no sentence for', () => {
  it('leads with the rule name rather than an empty line', () => {
    render(<ScanReport scan={fixture({
      verdict: 'warning',
      findings: [{ surface: 'prose', severity: 'low', rule: 'zzz_future_rule', path: 'README.md', evidence: 'L9: x' }],
    })} />)
    const [row] = rows()
    expect(row.firstElementChild?.textContent).toBe('zzz_future_rule')
    expect(within(row).getByTestId('finding-source').textContent).toBe('README.md · Low: noted, nothing to decide')
  })
})
