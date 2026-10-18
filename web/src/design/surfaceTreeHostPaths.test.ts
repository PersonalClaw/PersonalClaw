import { afterEach, describe, expect, it, vi } from 'vitest'
import { homedir, tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { normalizeTree } from '../../e2e/surfaceChecks'
import { userHomeFor } from '../../e2e/runPlaces'

// ── The surface matrix's tree baseline names no path of the machine that wrote it ────────────────
//
// Settings pages print folders: the Doctor names the venv the agent config starts, Security names
// the outside places (the other agent tools' setups, the shared skills folder, the shared model
// folder) under the gateway's home folder. Read raw, a committed baseline held the writer's own
// checkout and home, which is published text, and which no other machine or CI runner reads back.
// The tree folds the checkout to `<repo>`, the gateway's home folder (a run's `user-home-for-…`
// beside its home) to `~`, and the runner's own home folder to `~`, all read from the run itself.
// A home the run was handed (`PW_E2E_HOME`) is read from the run too: its name need not carry the
// prefix a home the harness makes does, and it may sit inside the checkout.

const REPO = dirname(process.cwd()) // vitest runs from web/
const RUN_HOME = join(tmpdir(), 'personalclaw-e2e-Ab3dEf')
const GATEWAY_HOME = join(tmpdir(), 'user-home-for-personalclaw-e2e-Ab3dEf')

describe("the matrix's accessibility tree folds the host's paths", () => {
  afterEach(() => { vi.unstubAllEnvs() })

  it('folds the checkout to <repo>', () => {
    const line = `  - group: PersonalClaw's server starts ${REPO}/.venv/bin/personalclaw mcp-core`
    expect(normalizeTree(line)).toBe("  - group: PersonalClaw's server starts <repo>/.venv/bin/personalclaw mcp-core")
  })

  it("folds the gateway's home folder to ~, and keeps the run's home apart from it", () => {
    const line = `  - text: Your setup ${GATEWAY_HOME}/.config, ${GATEWAY_HOME}/.agents/skills · home ${RUN_HOME}/snapshots`
    expect(normalizeTree(line)).toBe('  - text: Your setup ~/.config, ~/.agents/skills · home <e2e home>/snapshots')
  })

  it("folds the runner's own home folder to ~", () => {
    const line = `  - text: Skills other AI tools share ${homedir()}/.agents/skills`
    expect(normalizeTree(line)).toBe('  - text: Skills other AI tools share ~/.agents/skills')
  })

  it("folds a home the run was handed, inside the checkout, and the gateway's home beside it", () => {
    const named = join(REPO, '.local', 'scratch', 'e2e-named-home')
    vi.stubEnv('PW_E2E_HOME', named)
    const line = `  - text: Skills other AI tools share ${userHomeFor(named)}/.agents/skills · home ${named}/snapshots`
    expect(normalizeTree(line)).toBe('  - text: Skills other AI tools share ~/.agents/skills · home <e2e home>/snapshots')
  })

  it('folds a home the run was handed outside the checkout and the home folder', () => {
    const named = join(tmpdir(), 'runs', 'named-home')
    vi.stubEnv('PW_E2E_HOME', named)
    const line = `  - text: A setup ${userHomeFor(named)}/.config, ${userHomeFor(named)}/.agents/skills · ${named}/memory-vault`
    expect(normalizeTree(line)).toBe('  - text: A setup ~/.config, ~/.agents/skills · <e2e home>/memory-vault')
  })

  it('leaves a path of no one in particular alone', () => {
    // Control: only the run's own places fold, not every absolute path.
    expect(normalizeTree('  - code: /etc/hosts')).toBe('  - code: /etc/hosts')
  })
})
