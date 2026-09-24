import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  BLAST_RADIUS_FACET_ORDER, blastRadiusLine, deriveBlastRadius, establishedFacets,
  RISK_ESTABLISHES_READ_ONLY,
} from './approvalMeta'
import type { ApprovalRisk } from './approvalMeta'

// ── OU-7: blast-radius derivation is a DESCRIPTION, not a decision ──────────────────────────
//
// Contract C2 (ONBOARDING-UX) adds `blastRadius?` to the existing ApprovalSegment, derived
// frontend-side from tool name + the existing `risk` + the command-screening classification.
// The three inputs are NOT equally available, and that asymmetry is what these tests pin:
//
//   chat path       `approval` WS event carries `risk`      → ChatPage.tsx:911
//   companion path  GET /api/approvals → PendingApproval    → NO `risk` field at all
//   screening       is_read_only_bash() per approval        → written to perm_meta, read by NOBODY
//
// So the rail is honesty under missing inputs: a `false` must mean "not established", never
// "verified absent", and four falses must never be rendered as a confident all-clear.

describe('deriveBlastRadius — representative tools (C2 done_when)', () => {
  // The chat path: risk present. `bash` is declared DESTRUCTIVE backend-side but
  // resolve_effective_risk downgrades a screened read-only invocation to 'safe', so both
  // rows below are real wire states for the same tool.
  it('bash — shell, and read-only only when the backend already said so', () => {
    expect(deriveBlastRadius({ tool: 'bash', risk: 'destructive' }))
      .toEqual({ writes: false, network: false, shell: true, readOnly: false })
    expect(deriveBlastRadius({ tool: 'bash', risk: 'safe' }))
      .toEqual({ writes: false, network: false, shell: true, readOnly: true })
  })

  it('web_fetch — network, and NOT read-only (fetch is not a read verb here)', () => {
    expect(deriveBlastRadius({ tool: 'web_fetch', risk: 'caution' }))
      .toEqual({ writes: false, network: true, shell: false, readOnly: false })
  })

  it('memory_remember (the memory write) — writes, never read-only', () => {
    expect(deriveBlastRadius({ tool: 'memory_remember', risk: 'caution' }))
      .toEqual({ writes: true, network: false, shell: false, readOnly: false })
  })

  it('memory_forget — a destructive verb still reads as a write', () => {
    expect(deriveBlastRadius({ tool: 'memory_forget', risk: 'destructive' }))
      .toEqual({ writes: true, network: false, shell: false, readOnly: false })
  })

  it('read_file (the read-only tool) — read-only and nothing else', () => {
    expect(deriveBlastRadius({ tool: 'read_file', risk: 'safe' }))
      .toEqual({ writes: false, network: false, shell: false, readOnly: true })
  })
})

describe('deriveBlastRadius — the companion path has NO risk', () => {
  // PendingApproval (api.ts:1661) carries {id, source, tool, tool_input?, tool_purpose?,
  // session, ts}. Every one of these is what the phone route can actually derive.
  it('still establishes name-evidenced facets without risk', () => {
    expect(deriveBlastRadius({ tool: 'bash' }))
      .toEqual({ writes: false, network: false, shell: true, readOnly: false })
    expect(deriveBlastRadius({ tool: 'web_fetch' }))
      .toEqual({ writes: false, network: true, shell: false, readOnly: false })
    expect(deriveBlastRadius({ tool: 'memory_remember' }))
      .toEqual({ writes: true, network: false, shell: false, readOnly: false })
  })

  it('does NOT invent read-only when risk is the only thing that could have said so', () => {
    // Same tool, risk present vs absent. Dropping `risk` may only ever REMOVE a claim.
    const withRisk = deriveBlastRadius({ tool: 'bash', risk: 'safe' })!
    const withoutRisk = deriveBlastRadius({ tool: 'bash' })!
    expect(withRisk.readOnly).toBe(true)
    expect(withoutRisk.readOnly).toBe(false)
    expect(withoutRisk.shell).toBe(true)
  })

  it('a read-verb name is its own positive evidence, so reads survive the missing risk', () => {
    // Mirrors infer_risk_from_name's _READ_VERB_HINTS short-circuit.
    for (const tool of ['read_file', 'list_dir', 'knowledge_search', 'task_get', 'project_run_status']) {
      expect(deriveBlastRadius({ tool })).toEqual(
        { writes: false, network: false, shell: false, readOnly: true },
      )
    }
  })

  it('a verbless read tool is UNKNOWN without risk, not guessed', () => {
    // grep/glob/repo_map are declared-SAFE native reads, but their names carry no verb the
    // backend's own _READ_VERB_HINTS would match either. Name evidence alone therefore says
    // nothing, and saying nothing is the correct answer — this is precisely the case that
    // wants the `risk` pass-through OU-9 adds to the queue payload, not a cleverer guess.
    for (const tool of ['grep', 'glob', 'repo_map']) {
      expect(deriveBlastRadius({ tool })).toBeUndefined()
      expect(deriveBlastRadius({ tool, risk: 'safe' })).toEqual(
        { writes: false, network: false, shell: false, readOnly: true },
      )
    }
  })

  it('returns undefined rather than four false negatives when nothing is established', () => {
    // The worst possible output would be an all-false object: the renderer would show
    // "no writes / no network / no shell / not read-only" from zero evidence. Absence is
    // C2's own unknown channel, so an unknown external MCP tool must produce nothing.
    expect(deriveBlastRadius({ tool: 'acme_frobnicate' })).toBeUndefined()
    expect(deriveBlastRadius({ tool: 'mcp/acme/frobnicate' })).toBeUndefined()
    // 'caution' and 'destructive' say a call HAS side effects but not which facet, so
    // they must not conjure one either.
    expect(deriveBlastRadius({ tool: 'acme_frobnicate', risk: 'caution' })).toBeUndefined()
    expect(deriveBlastRadius({ tool: 'acme_frobnicate', risk: 'destructive' })).toBeUndefined()
  })
})

describe('deriveBlastRadius — the command-screening input (no caller yet)', () => {
  // is_read_only_bash() runs per approval at chat_runner.py:2593 and lands in
  // perm_meta["is_read_only"], but it is not on the WS payload and nothing reads it. The
  // parameter exists with that verdict's exact shape so OU-8/OU-9 can pass it through.
  it('an explicit read-only verdict is the strongest signal', () => {
    expect(deriveBlastRadius({ tool: 'bash', readOnlyCommand: true })!.readOnly).toBe(true)
  })

  it('an explicit not-read-only verdict rules the claim out, even over a safe risk', () => {
    expect(deriveBlastRadius({ tool: 'bash', risk: 'safe', readOnlyCommand: false })!.readOnly)
      .toBe(false)
  })

  it('omitting it changes nothing — it is genuinely optional', () => {
    expect(deriveBlastRadius({ tool: 'bash', risk: 'safe' }))
      .toEqual(deriveBlastRadius({ tool: 'bash', risk: 'safe', readOnlyCommand: undefined }))
  })
})

describe('RISK_ESTABLISHES_READ_ONLY — a closed enum with no default branch', () => {
  // A `default:` that swallows an unmapped value is the defect class this repo audits for.
  // The Record type makes typecheck fail when a member joins the union; this assertion then
  // trips too, so the new level cannot be added without a conscious read-only ruling.
  it('maps exactly the risk vocabulary, no more and no less', () => {
    expect(Object.keys(RISK_ESTABLISHES_READ_ONLY).sort())
      .toEqual(['caution', 'destructive', 'safe'])
  })

  it('the vocabulary is the same one ToolItem.risk_level and the backend RiskLevel use', () => {
    // ApprovalSegment['risk'] and ToolItem.risk_level are byte-identical unions
    // ('safe' | 'caution' | 'destructive'); ApprovalRisk is aliased from the former so the
    // two cannot drift. This asserts the alias still accepts every member at runtime.
    const all: ApprovalRisk[] = ['safe', 'caution', 'destructive']
    for (const risk of all) {
      const derived = deriveBlastRadius({ tool: 'bash', risk })!
      expect(derived.shell).toBe(true)
      expect(derived.readOnly).toBe(RISK_ESTABLISHES_READ_ONLY[risk])
    }
  })

  it('a risk value this build has never heard of establishes nothing', () => {
    // ChatPage.tsx:911 casts String(d.risk) straight into the union without validating, so a
    // session persisted by another build can carry a foreign level. It must read as no
    // evidence — the same defence RiskChip already makes with `if (!m) return null`.
    const foreign = 'catastrophic' as ApprovalRisk
    expect(deriveBlastRadius({ tool: 'bash', risk: foreign })!.readOnly).toBe(false)
    expect(deriveBlastRadius({ tool: 'acme_frobnicate', risk: foreign })).toBeUndefined()
  })
})

describe('deriveBlastRadius — never under-claims danger', () => {
  it('an established write never also claims read-only, whatever the risk says', () => {
    // The one place the FE's `remember` token diverges from the backend's
    // _MUTATING_NAME_HINTS: infer_risk_from_name classifies memory_remember as 'safe'
    // because it has no `remember` token, so this exact combination is reachable on the
    // wire. The write must win.
    const d = deriveBlastRadius({ tool: 'memory_remember', risk: 'safe' })!
    expect(d.writes).toBe(true)
    expect(d.readOnly).toBe(false)
  })

  it('is total — garbage in produces no claim, not a throw', () => {
    for (const tool of ['', '   ', '///', ' ', 'A'.repeat(4000)]) {
      expect(() => deriveBlastRadius({ tool })).not.toThrow()
    }
    expect(deriveBlastRadius({ tool: '' })).toBeUndefined()
  })

  it('is a pure function — same inputs, same output, no accumulated state', () => {
    const once = deriveBlastRadius({ tool: 'bash', risk: 'safe' })
    for (let i = 0; i < 5; i++) {
      expect(deriveBlastRadius({ tool: 'bash', risk: 'safe' })).toEqual(once)
    }
  })
})

describe('approvalMeta.ts must stay a pure leaf that nothing can gate on', () => {
  // OU-7 ships the derivation half; OU-8 renders it. The durable invariant is not "it has no
  // call site" (OU-8 adds one) but that it CANNOT become a control: no runtime imports, so it
  // can neither reach the API client nor be reached by an approval decision path.
  const source = readFileSync(join(process.cwd(), 'src/pages/chat/approvalMeta.ts'), 'utf8')

  it('the rail is not vacuous — it is reading the real module', () => {
    expect(source.length).toBeGreaterThan(1000)
    expect(source).toContain('export function deriveBlastRadius')
  })

  it('has no runtime imports at all (type-only)', () => {
    const runtimeImports = source
      .split('\n')
      .filter((l) => /^\s*import\s/.test(l) && !/^\s*import\s+type\s/.test(l))
    expect(runtimeImports).toEqual([])
  })

  it('performs no I/O and reads no ambient state', () => {
    for (const forbidden of ['fetch(', 'localStorage', 'sessionStorage', 'Date.now', 'Math.random', 'window.']) {
      expect(source).not.toContain(forbidden)
    }
  })

  it('never re-implements the command screening it consumes', () => {
    // Deciding whether a command is read-only is security logic with an owner
    // (is_read_only_bash, task_modes.py:88). This module takes the verdict; it must never
    // parse a command string itself.
    for (const forbidden of ['_READ_ONLY_BASH', 'rm -rf', 'sudo', 'split(\'|\')']) {
      expect(source).not.toContain(forbidden)
    }
  })
})

// ── OU-8: the facet vocabulary the surfaces share ───────────────────────────────────────────
// Three surfaces render this radius (the chat card's chips, the out-of-context toast's line,
// and OU-9's channel brief). The words live in this module so they cannot become three
// vocabularies for one claim — and the positives-only rule lives here too, so a surface
// cannot re-derive it and get it wrong.

describe('establishedFacets / blastRadiusLine', () => {
  it('the order list covers every facet of the radius — a fifth cannot go unrendered', () => {
    // FACET_COPY is a total Record (typecheck catches an unlabelled facet); this catches a
    // facet that is labelled but left out of the render order, which no type can see.
    const all = deriveBlastRadius({ tool: 'bash_web_write', risk: 'caution' })
    expect(all).toBeDefined()
    expect(BLAST_RADIUS_FACET_ORDER.length).toBe(Object.keys(all as object).length)
    expect([...BLAST_RADIUS_FACET_ORDER].sort()).toEqual(Object.keys(all as object).sort())
  })

  it('returns ONLY established facets, in the declared order', () => {
    const facets = establishedFacets({ writes: true, shell: true, network: false, readOnly: false })
    expect(facets.map((f) => f.key)).toEqual(['writes', 'shell'])
    expect(facets.map((f) => f.label)).toEqual(['Writes files', 'Runs a command'])
  })

  it('yields nothing at all for an undefined radius — the unknown channel stays silent', () => {
    expect(establishedFacets(undefined)).toEqual([])
    expect(blastRadiusLine(undefined)).toBe('')
  })

  it('never renders a false facet as a negative claim', () => {
    // The failure this exists to prevent: an all-false radius presented as "no writes, no
    // network, no shell, not read-only" — four confident negatives from zero evidence.
    const none = establishedFacets({ writes: false, shell: false, network: false, readOnly: false })
    expect(none).toEqual([])
    expect(blastRadiusLine({ writes: false, shell: false, network: false, readOnly: false })).toBe('')
  })

  it('every facet has a label and a spelled-out detail, and none of them is a verdict', () => {
    const facets = establishedFacets({ writes: true, shell: true, network: true, readOnly: true })
    expect(facets).toHaveLength(4)
    for (const f of facets) {
      expect(f.label.length, f.key).toBeGreaterThan(3)
      expect(f.detail.length, f.key).toBeGreaterThan(10)
      // A facet states a capability; it must not editorialise about the decision.
      for (const advocacy of [/safe to/i, /recommend/i, /harmless/i, /no risk/i, /probably/i]) {
        expect(`${f.label} ${f.detail}`, f.key).not.toMatch(advocacy)
      }
    }
  })

  it('renders the compact line from the same words as the chips', () => {
    const radius = { writes: true, shell: true, network: false, readOnly: false }
    const line = blastRadiusLine(radius)
    expect(line).toBe('writes files, runs a command')
    // Same vocabulary, one lowercasing apart — the toast and the card cannot drift.
    for (const f of establishedFacets(radius)) expect(line).toContain(f.label.toLowerCase())
  })
})
