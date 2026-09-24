import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AgentPill, ModelPill, ApprovalPill, ReasoningPill, effortsForAgent } from './controls'
import { ProjectPicker } from '../ProjectPicker'

// ── A value pill that never says what it controls ──────────────────────────────────────
//
// The composer's pill cluster is four value selectors in a row. Each renders an icon, its
// CURRENT VALUE, and a chevron — and nothing else. Measured accessible names on #/chat before
// this change:
//
//   "Agent"      (which agent is bound)
//   "Auto"       (the model)
//   "Default"    (the reasoning effort)
//   "Normal"     (the permission mode — rendered on surfaces that enable it)
//
// Four bare values. A screen-reader user tabbing the composer hears "Agent", "Auto", "Default"
// and has no way to know they are the agent, the model and the reasoning effort — the dimension
// lives only in the icon and the horizontal position, neither of which is announced.
//
// The app already solves this one row up. `HeaderModePill` composes
// `aria-label={`${ariaLabel}: ${label}`}`, so the header's pills announce "Task mode: Agent" and
// "Permission mode: Normal". The composer's pills are the same kind of control and now take the
// same shape; the visible label is untouched, because on screen the dimension is already carried
// by position and icon, which is exactly why the label spends its width on the value.
//
// 🪤 THE AMBIGUITY THAT FOUND THIS. On #/chat the header renders a task-mode pill labelled
// "Agent" with a Bot icon, and the composer renders the agent pill ALSO labelled "Agent" with a
// Bot icon, 380px apart, controlling different things (which tools may run vs. which agent
// answers). The accessible names were the only thing distinguishing them — and only one of the
// two had a useful one. The remaining VISIBLE collision is a copy decision, recorded for the
// owner rather than guessed at here: see the cycle-66 note in POLISH-SESSION.md.

describe('every composer pill announces its dimension', () => {
  it('the agent pill says Agent (not "Agent: Agent") when nothing is bound', () => {
    // Its fallback value IS the dimension word, so the composed name would stutter.
    render(<AgentPill value="" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Agent' })).toBeTruthy()
  })

  it('the agent pill names the dimension AND the bound agent', () => {
    render(<AgentPill value="researcher" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Agent: researcher' })).toBeTruthy()
  })

  it('the model pill says Model', () => {
    render(<ModelPill value="" onSelect={vi.fn()} />)
    // Unset resolves to the "Auto" use-case chain.
    expect(screen.getByRole('button', { name: 'Model: Auto' })).toBeTruthy()
  })

  it('the permission pill matches the HEADER pill it shares an axis with', () => {
    // The header announces "Permission mode: Normal"; the composer must not invent a second
    // name for the same dimension.
    render(<ApprovalPill value="normal" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Permission mode: Normal' })).toBeTruthy()
  })

  it('the reasoning pill says Reasoning effort', () => {
    render(<ReasoningPill value="" efforts={[{ value: 'low', label: 'Low' }]} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Reasoning effort: Default' })).toBeTruthy()
  })

  it('leaves the VISIBLE label alone (this is a naming fix, not a redesign)', () => {
    render(<ReasoningPill value="" efforts={[{ value: 'low', label: 'Low' }]} onSelect={vi.fn()} />)
    // The pill still shows just the value on screen; only the accessible name gained the axis.
    expect(screen.getByRole('button').textContent).toBe('Default')
  })
})

// ── A runtime that declares no reasoning axis (ACP-AGENT-PARITY §2.6, atom AAP-9) ─────
//
// Measured live 2026-08-24: `GET /api/agent-providers/acp:kiro-cli/agents` returns 27 agents
// each with `supported_efforts: []`, and codex the same for its one agent, while claude-code
// declares five. §2.6 asked for the pill to "grey out"; the shipped behaviour HIDES it, and
// the owner ruling (recorded as a DEVIATION on the atom) is that hiding is correct: a greyed
// control still asserts the axis exists for this runtime, while a hidden one plus an API that
// refuses the value (`G21`, tests/test_acp_effort_declaration.py) tells the truth twice. The
// machine-readable truth is already in the payload — `supported_efforts: []`.
//
// Railed because the ruling is only as durable as the test under it: without this, a later
// change could render a dead pill for kiro and nothing would object.
describe('a runtime declaring no efforts offers no pill', () => {
  it('renders nothing rather than a dead control', () => {
    const { container } = render(<ReasoningPill value="" efforts={[]} onSelect={vi.fn()} />)
    expect(container.innerHTML).toBe('')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('effortsForAgent returns the runtime\'s DECLARED set, empty included', () => {
    const data = {
      discovered: {
        'acp:kiro-cli': [{ name: 'atlas', provider_agent: 'atlas', supported_efforts: [] }],
        'acp:claude-code': [{ name: 'Claude Code', provider_agent: '', supported_efforts: [{ value: 'low', label: 'Low' }] }],
      },
    } as never
    expect(effortsForAgent(data, 'atlas')).toEqual([])
    // Vacuity floor: the helper is not simply returning [] for everything.
    expect(effortsForAgent(data, 'Claude Code')).toEqual([{ value: 'low', label: 'Low' }])
  })
})

describe('the THIRD pill family — the project picker — agrees', () => {
  // `ProjectPicker` is the same kind of control (icon + current value + chevron, opening a listbox)
  // in a different file, so neither the primitive above nor its rail reached it. Measured on `#/code`
  // at 1440px, where the loop composer renders it beside the Project-kind Segmented:
  //
  //     ProjectPicker trigger      role=button  name "New project"      x 240..372
  //     Project-kind tab (active)  role=tab     name "New project"      x 384..483
  //
  // Two controls, one row, 8px apart, identical accessible names, different jobs — one chooses WHICH
  // project the work attaches to, the other chooses greenfield vs brownfield. The tablist itself is
  // correctly named "Project kind", so the group context exists on that side; the picker had no
  // dimension anywhere in its name, only in a `title` that a button with text content does not use.
  //
  // 🔁 THE OTHER HALF OF THAT COLLISION IS NOW CLOSED TOO. The pass above fixed the accessible names
  // and signed off "Sighted users see no change" — correct for its scope, but it left the VISIBLE
  // duplication in place, and re-measuring found it at three viewports, not one: 12px apart at
  // 1440px, and 8px apart at 1024px and 640px where the Segmented has collapsed to a pill so the two
  // controls are the same SHAPE as well as the same words. The greenfield tab now reads "Fresh start"
  // (LoopComposer) — chosen over the obvious "New codebase" because that measured 89.4px against the
  // old label's 73px and pushed the Mode dial past its collapse threshold, moving the header's
  // overflow to a width that had been fine. "Fresh start" is 64.9px, NARROWER, so it cannot create an
  // overlap. The rail below is what keeps the two labels apart, since neither file can see the other.
  it('names the dimension and the value', () => {
    render(<ProjectPicker value="" onChange={() => {}} />)
    expect(screen.getByRole('button', { name: 'Project: New project' })).toBeTruthy()
  })

  it('follows the caller\'s empty label rather than inventing one', () => {
    render(<ProjectPicker value="" onChange={() => {}} emptyLabel="No project" />)
    expect(screen.getByRole('button', { name: 'Project: No project' })).toBeTruthy()
  })

  it('says just "Project" when the value would repeat the dimension', () => {
    // Not theoretical: `label` falls back to the bare word "Project" when a bound id is missing from
    // the fetched list, which would otherwise announce "Project: Project".
    render(<ProjectPicker value="missing-id" onChange={() => {}} />)
    expect(screen.getByRole('button', { name: 'Project' })).toBeTruthy()
  })

  it('leaves the VISIBLE label alone (a naming fix, not a redesign)', () => {
    const { container } = render(<ProjectPicker value="" onChange={() => {}} />)
    expect(container.querySelector('button')!.textContent!.trim()).toBe('New project')
  })

  it('the trigger carries no aria-label-shaped title fallback confusion', () => {
    // The `title` stays — it is the sighted user's explanation — but it must not be the only place the
    // dimension lives, which was the defect.
    const src = readFileSync(join(process.cwd(), 'src/ui/ProjectPicker.tsx'), 'utf8')
    expect(src).toMatch(/aria-label=\{label === 'Project' \? 'Project' : `Project: \$\{label\}`\}/)
    expect(src).toMatch(/title="Choose the project this work scopes under"/)
  })
})

describe('the rail', () => {
  const src = readFileSync(join(process.cwd(), 'src/ui/composer/controls.tsx'), 'utf8')

  /** Complete `<PillButton …>` tags, tracking {} depth. A naive `[^>]*>` stops at the `/>` of the
   *  nested `<Bot … />` icon prop and reports every tag as dimension-less — which is how this rail
   *  first "found" three failures against a tree that was already fixed. */
  const sites = (() => {
    const out: string[] = []
    for (const m of src.matchAll(/<PillButton\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < src.length; i++) {
        const ch = src[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) { out.push(src.slice(m.index!, i + 1)); break }
      }
    }
    return out
  })()

  it('finds every pill trigger (not vacuously green)', () => {
    // Four at the time of writing. If a fifth pill appears, `dimension` being a REQUIRED prop
    // means typecheck stops it before this test does — this floor is here so the assertion below
    // cannot pass by matching nothing.
    expect(sites.length, 'the matcher must find the pill triggers').toBeGreaterThanOrEqual(4)
  })

  it('has no pill trigger without a dimension', () => {
    const mute = sites.filter((t) => !/\bdimension=/.test(t))
    expect(mute, `pill trigger(s) announce a bare value:\n  ${mute.join('\n  ')}`).toEqual([])
  })

  it('composes the name the same way HeaderModePill does', () => {
    // If the header's format changes, these two families drift apart again. Assert the shape in
    // both places rather than trusting them to stay in step.
    const header = readFileSync(join(process.cwd(), 'src/ui/HeaderActions.tsx'), 'utf8')
    expect(header, 'the header pill composes "<dimension>: <value>"').toMatch(/aria-label=\{`\$\{ariaLabel[^`]*\}: \$\{label\}`\}/)
    expect(src, 'the composer pill composes the same shape').toMatch(/`\$\{dimension\}: \$\{label\}`/)
  })
})

// ── The cockpit header row must not paint one label twice ──────────────────────────────
//
// The collision above was between two controls in TWO DIFFERENT FILES, which is why nothing caught
// it: `LoopComposer` writes its Segmented's option labels, `ProjectPicker` owns its own idle label,
// and neither module can see the other. A render test would need the whole cockpit (DotGlow, the
// api client, the loop store), so this reads the two label sources directly and compares them —
// source-level ON PURPOSE, and the live proof lives in the PR that added it:
//
//   #/code @1440   picker "New project" x=240..372   ·   greenfield tab "New project" x=384..483
//   #/code @1024   picker "New project" x=240..372   ·   collapsed pill "New project" x=380..499
//   #/code @640    picker "New project" x=44..176    ·   collapsed pill "New project" x=184..303
//
// Only 390px was clean, and only because the picker hides its text below `sm`.
describe('the cockpit header row paints no label twice', () => {
  const composer = readFileSync(join(process.cwd(), 'src/pages/loop/LoopComposer.tsx'), 'utf8')
  const picker = readFileSync(join(process.cwd(), 'src/ui/ProjectPicker.tsx'), 'utf8')

  /** Every `label: '…'` on a Segmented option in the composer's header row, plus the checkbox's own
   *  word. Deliberately NOT scoped to a character window around `headerControls` — a window is not a
   *  scope, and the body's Segmenteds (the kind slider) share this file. Instead: take every option
   *  label in the file, which OVER-collects, because over-collecting can only make this stricter. */
  const composerLabels = [...composer.matchAll(/\blabel:\s*'([^']+)'/g)].map((m) => m[1])
  /** The picker's idle label, i.e. what it paints when no project is bound — the default in
   *  `emptyLabel ?? '…'`. That default is the one every cockpit uses; Chat overrides it. */
  const pickerDefault = picker.match(/emptyLabel \?\? '([^']+)'/)?.[1] ?? null

  it('found both label sources (vacuity floor)', () => {
    // If either matcher stops matching, the comparison below passes by comparing nothing.
    expect(composerLabels.length, 'no Segmented option labels found in LoopComposer').toBeGreaterThanOrEqual(6)
    expect(pickerDefault, "ProjectPicker's default empty label was not found").toBeTruthy()
    // And the specific pair this exists for is present, so a rename cannot silently drop the subject.
    expect(composerLabels).toContain('Existing codebase')
  })

  it("no composer control reuses the project picker's idle label", () => {
    const clash = composerLabels.filter((l) => l === pickerDefault)
    expect(
      clash,
      `LoopComposer paints ${JSON.stringify(clash)}, which is also what ProjectPicker paints when no ` +
        `project is bound — two adjacent controls in the same header row with the same words and ` +
        `different jobs. Rename the composer's label; the picker's is product vocabulary the backend ` +
        `honours ("${pickerDefault} (auto-named)" auto-creates one).`,
    ).toEqual([])
  })

  it('the greenfield/brownfield pair still reads as one choice', () => {
    // Both halves present, and the brownfield half keeps the noun that the body's "Codebase path"
    // input echoes. Pinned by name so a future rewording of either half comes back through this rail
    // and gets its width re-measured — the reason the first attempt here was rejected.
    expect(composerLabels).toContain('Fresh start')
    expect(composerLabels).toContain('Existing codebase')
  })

  it('the group name the collapse rail asserts is unchanged', () => {
    // `composerHeaderNarrow.test.ts` iterates ['Granularity', 'Mode', 'Project kind'] to prove each
    // wide control collapses to a menu. Renaming a visible OPTION must not move the group's name.
    expect(composer).toMatch(/ariaLabel="Project kind"/)
  })

  it('the wire keys are untouched by the rename', () => {
    // `ProjectKind` in lib/api is the persisted vocabulary. A label change must never reach it.
    expect(composer).toMatch(/key: 'greenfield'/)
    expect(composer).toMatch(/key: 'brownfield'/)
  })
})
