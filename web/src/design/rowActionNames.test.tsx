import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { RowAction } from '../pages/dashboard/widgets/kit'

// ── A control that acts on ONE row has to name that row ────────────────────────────────────────
//
// Censused every strictly-visible control on 17 routes and grouped them by accessible name, keeping
// only groups whose members act on DIFFERENT rows. What came back:
//
//   #/dashboard   8× "Reply" · 8× "Dismiss" · 6× "Mark complete"
//   #/tasks      30× "Select task"
//   #/projects    3× "Delete project"
//
// Each of those acts on a different item, so a screen-reader user listing the controls hears the same
// two or three words repeated with nothing to choose between them (WCAG 4.1.2). The visible label is
// right as it is — on screen the subject is the row you are looking at — so the fix is the accessible
// NAME only, composed the way the kit already composes it elsewhere: `ui/Toaster`
// (`Dismiss: ${message}`), `ui/forms` (`Remove ${value}`), `ui/WidthPill` (`Content width: ${label}`),
// FileTree / AppsSection (`Actions for ${name}`). Drift, not a taste call — the convention exists.
//
// 🪤 A `title` IS NOT THE NAME WHEN THE BUTTON HAS TEXT. `RowAction`'s Reply button already carried
// `title="Open to reply"`, and it still announced "Reply": the text content wins. Two of these needed
// `aria-label` precisely because the verb was already visible; only the icon-only ones (`Dismiss`,
// `Reject`, `Unpin`, `Mark complete`) were named by their title at all.
//
// 🔑 WHAT IS DELIBERATELY LEFT ALONE, so a later pass does not "finish" it:
//   • SINGLETON actions — `SystemHealth`'s doctor/update buttons, `ActiveWork`'s Send inside the open
//     composer. One per widget, no sibling to be confused with.
//   • REPEATED CHIPS WITH ONE TARGET — `#/knowledge`'s 11× "draid" tag links, `#/tasks`' 8× project
//     chips, `#/inbox`'s 35× "Proposals" deep links. Same name AND same destination, so the repetition
//     carries no ambiguity; naming them per-row would add noise, not information.
//
// 🪤 THE CENSUS UNDERCOUNTS, AND THAT IS WHY THIS RAIL IS SOURCE-LEVEL TOO. Its "different rows" test
// keys on the nearest `li,tr,[rounded-*]` ancestor's first 40 characters, which collapsed `#/projects`'
// three "Delete project" buttons into one group (their rows share a prefix) — the finding came from a
// separate per-route dump. A DOM census is a lead generator, not a gate.

const SRC = join(process.cwd(), 'src')
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

describe('RowAction carries an explicit accessible name', () => {
  it('renders the composed name, and the visible verb stays short', () => {
    render(<RowAction onClick={() => {}} title="Open to reply" ariaLabel="Reply: Skill: refine-a-skill">Reply</RowAction>)
    const b = screen.getByRole('button', { name: 'Reply: Skill: refine-a-skill' })
    expect(b.textContent, 'the label a sighted user reads is unchanged').toBe('Reply')
    expect(b.getAttribute('title'), 'the tooltip stays the short hint').toBe('Open to reply')
  })

  it('without it, the button falls back to its text — the defect being fixed', () => {
    render(<RowAction onClick={() => {}} title="Open to reply">Reply</RowAction>)
    // Proof that `title` does not win over text content: this is why aria-label was needed.
    expect(screen.getByRole('button', { name: 'Reply' })).toBeTruthy()
  })
})

describe('every row-scoped RowAction names its row', () => {
  /** [file, how many call sites must pass ariaLabel, how many singletons may not] */
  const WIDGETS: [string, number, number][] = [
    ['pages/dashboard/widgets/ActionCenter.tsx', 4, 0],
    ['pages/dashboard/widgets/TasksWidget.tsx', 1, 0],
    ['pages/dashboard/widgets/PinnedArtifacts.tsx', 1, 0],
    ['pages/dashboard/widgets/ActiveWork.tsx', 2, 1],   // the composer's Send is a singleton
    // doctor + update + health-unknown: one each, no rows. The third is ux-673's "Health unknown"
    // row — a singleton like its siblings (the strip has no rows to name), so `named` stays 0.
    ['pages/dashboard/widgets/SystemHealth.tsx', 0, 3],
    // One Unload per resident model (LMMV-5) — five rows of a bare "Unload" is this rail's
    // exact defect, so the name carries the model.
    ['pages/dashboard/widgets/OnThisMachine.tsx', 1, 0],
  ]

  for (const [rel, named, singletons] of WIDGETS) {
    it(`${rel.split('/').pop()} names ${named} and leaves ${singletons} singleton(s)`, () => {
      const code = read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      const sites = [...code.matchAll(/<RowAction\b/g)]
      expect(sites.length, `${rel} call sites`).toBe(named + singletons)
      const withName = [...code.matchAll(/ariaLabel=\{`/g)]
      expect(withName.length, `${rel} must compose ${named} name(s) from the row`).toBe(named)
    })
  }

  it('every composed name interpolates a subject, never a bare verb', () => {
    // `ariaLabel="Dismiss"` would satisfy a "passes ariaLabel" check and fix nothing.
    for (const [rel] of WIDGETS) {
      for (const m of read(rel).matchAll(/ariaLabel=\{`([^`]*)`\}/g)) {
        // "verb: subject", where the SUBJECT is the interpolation at the end. The verb may itself be
        // computed — ActionCenter's is `${kind === 'approval' ? 'Approve' : 'Accept'}` — so anchoring
        // on a literal prefix would fail a correct name (it did, first run).
        expect(m[1], `${rel}: ${m[1]} must end in the row's subject`).toMatch(/: \$\{[^}]+\}$/)
      }
    }
  })

  it('no OTHER RowAction call site appears without a name — the census is closed', () => {
    const files = walk(SRC).filter((abs) => readFileSync(abs, 'utf8').includes('<RowAction'))
    expect(files.length, 'widgets using RowAction').toBe(WIDGETS.length)
  })
})

describe('the two list surfaces name their row controls too', () => {
  it("#/tasks' selection checkbox says WHICH task", () => {
    const code = read('pages/tasks/TasksListPage.tsx')
    expect(code).toMatch(/aria-label=\{`\$\{selected \? 'Deselect' : 'Select'\}: \$\{t\.title\}`\}/)
    expect(code, 'the old shared name must be gone').not.toMatch(/'Deselect task' : 'Select task'/)
  })

  it("#/projects' delete button says WHICH project, and keeps a short tooltip", () => {
    const code = read('pages/projects/ProjectsSection.tsx')
    expect(code).toMatch(/label=\{`Delete project: \$\{p\.name\}`\} title="Delete project"/)
  })

  it('the repeated-chip families are left alone on purpose', () => {
    // Pinned so "finish the sweep" cannot turn 11 identical tag links into 11 different names.
    const kn = read('pages/knowledge/KnowledgeListPage.tsx')
    expect(kn, 'tag chips share a name because they share a destination').not.toMatch(/aria-label=\{`Tag: /)
  })
})

// ── 2026-08-19: the census was closed over a PRIMITIVE, so hand-rolled row actions were invisible ──
//
// The sweep above ends with *"no OTHER RowAction call site appears without a name — the census is
// closed"*. Closed over `<RowAction>`. `#/settings/durability` hand-rolls its row controls out of
// `ui/Button`, so it sat outside the population entirely. Measured on the live panel:
//
//   4 shared names across 9 rendered buttons
//     2× "Preview restore" · 2× "Merge-restore"   (one pair per snapshot ARCHIVE)
//     3× "See undoing just this" · 2× "See going back to here"   (one pair per HISTORY entry)
//   and 3 more per CONFLICT row — three near-identical choices where a wrong pick overwrites an edit
//
// After: **17 buttons in the panel, 0 duplicate accessible names**, visible text untouched.
//
// 🔑 THE PANEL ALREADY KNEW THE SUBJECT. Its confirm body says "…version of ${c.entity_id} will be
// written into ${c.entry_id}" and its toast says "Resolved ${c.entity_id}: …". Only the button that
// STARTS the act dropped it — which is what makes this drift rather than a taste call.
//
// 🪤 NAMING BY THE ROW'S OWN SUBJECT DID NOT WORK FOR THE HISTORY LIST, and shipping it would have been
// a fix that fixes nothing. Measured from `/api/durability/history/config/timeline`: all three entries
// read `subject = "Configuration: 1 file changed"`, and their ages (3.87h / 3.87h / 3.88h) all render
// "4 hours ago" through the panel's deliberately coarse formatter. So subject collides, time collides,
// and subject+time collides. The first version of this fix was verified by re-reading the live DOM,
// which still showed 2 duplicate groups — that measurement is the only reason it did not ship.
//   · POSITION is what actually separates one row from the next in a chronological list, and it is
//     human where `entry.sha` would be a machine code read out loud (`change 2 of 3 — …`).
//   · The deeper problem — three rows a SIGHTED user cannot tell apart either — is the backend's commit
//     subject, and it is logged as an owner call rather than papered over here.

describe('the hand-rolled row actions a primitive-shaped census could not see', () => {
  /** [the verb as written, the expression that must carry the row]. */
  const DURABILITY: [string, string][] = [
    ['Preview restore', '${a.name}'],
    ['Merge-restore', '${a.name}'],
    ['CHOICE_LABELS.keep_local', '${c.entity_id}'],
    ['CHOICE_LABELS.take_remote', '${c.entity_id}'],
    ['CHOICE_LABELS.accept_proposal', '${c.entity_id}'],
  ]
  const panel = () => read('pages/settings/DurabilityPanel.tsx')

  it.each(DURABILITY)('%s names its row with %s', (verb, subject) => {
    const code = panel()
    const label = verb.startsWith('CHOICE_LABELS')
      ? `ariaLabel={\`\${${verb}}: ${subject}\`}`
      : `ariaLabel={\`${verb}: ${subject}\`}`
    expect(code, `${verb} must name the row it acts on`).toContain(label)
  })

  it('the history pair is named by POSITION, because subject and time both collide', () => {
    // Pinned deliberately: "simplifying" this back to `${entry.subject}` re-creates the defect while
    // still looking like a named button. The measurement is in this describe's header.
    const code = panel()
    for (const verb of ['See going back to here', 'See undoing just this']) {
      expect(code, `${verb} needs the row's position`).toContain(
        `ariaLabel={\`${verb}: change \${i + 1} of \${timeline.data!.entries.length} — \${entry.subject}\`}`)
    }
  })

  it('the visible verbs are unchanged — this is a NAME fix, not a relabel', () => {
    const code = panel()
    // Each verb asserted as a BUTTON BODY. (A first draft looped over the verbs asserting
    // `toContain('>')`, which every file satisfies — a vacuous rail is worse than none, because it
    // reports as coverage.)
    expect(code).toMatch(/>\s*See going back to here\s*<\/Button>/)
    expect(code).toMatch(/>\s*See undoing just this\s*<\/Button>/)
    expect(code).toMatch(/>\s*Merge-restore\s*<\/Button>/)
    expect(code, 'the archive verb still renders through its busy branch').toMatch(/: 'Preview restore'}/)
    expect(code, 'the conflict choices still render their shared constants')
      .toMatch(/\{CHOICE_LABELS\.take_remote\}/)
  })

  it('camelCase, because ui/Button spreads no rest', () => {
    // 🪤 `aria-label` on our own components compiles and reaches NOTHING — TS does not
    // excess-property-check a dashed JSX attribute. `ui/ariaPropForwarding` forbids it globally; this
    // asserts the panel this cycle touched did not reintroduce it.
    expect(panel(), 'the dashed spelling silently vanishes on ui/Button').not.toMatch(/<Button[^>]*aria-label=/)
  })

  // ── 2026-08-19 (ux-716): the same defect at ELEVEN instances, one card component ────────────────
  //
  // `#/settings/providers` renders one `ProviderCard` per provider. Measured live: **11 buttons, and
  // every one of them was named "Configure"** — one duplicate group of 11, each opening a DIFFERENT
  // provider's config form. The card's other controls have the same shape (Sign in · Check availability ·
  // and the channel strip's Test / Connect / Disconnect), conditional on runtime state, so they are
  // fixed together rather than left as known members.
  //
  // 🔑 THE FILE ALREADY NAMED ONE OF ITS OWN CONTROLS: the enable `Toggle` passes
  // `label={`Toggle ${ext.name}`}`. So the convention was in the file, applied once.
  //
  // The subject is `who = ext.displayName || ext.name` — the card's own visible title, computed once so
  // six controls cannot drift apart. `title` stays the short verb on the two `SquareIconButton`s, which
  // is the same split `#/projects` uses (`label={`Delete project: ${p.name}`} title="Delete project"`).
  //
  // After: **11 buttons, 0 duplicate name groups.**
  const PROVIDER_CONTROLS: [string, string][] = [
    ['Sign in', 'aria-label={`Sign in: ${who}`}'],
    ['Check availability', 'label={`Check availability: ${who}`} title="Check availability"'],
    ['Configure', 'label={`Configure: ${who}`} title="Configure"'],
    ['Test', 'aria-label={`Test: ${channel.name}`}'],
    ['Disconnect', 'aria-label={`Disconnect: ${channel.name}`}'],
    ['Connect', 'aria-label={`Connect: ${channel.name}`}'],
  ]

  it.each(PROVIDER_CONTROLS)('ProviderCard names its %s', (_verb, expected) => {
    expect(read('pages/settings/ProviderCard.tsx')).toContain(expected)
  })

  it('the provider subject is the card\'s own visible title, computed once', () => {
    const code = read('pages/settings/ProviderCard.tsx')
    expect(code, 'derived from what the card displays, not re-picked per control')
      .toMatch(/const who = ext\.displayName \|\| ext\.name/)
    expect(code, 'and the title the card renders is the same expression')
      .toMatch(/\{ext\.displayName \|\| ext\.name\}/)
    // The bare names are the defect; none may come back.
    expect(code).not.toMatch(/label="Configure"/)
    expect(code).not.toMatch(/label="Check availability"/)
  })

  // ── the derived census: a row action is one whose HANDLER references the mapped item ─────────────
  //
  // Membership by shape, not by primitive and not by verb: inside `.map((item) => …)`, a `<Button>`
  // whose own `onClick` mentions `item` acts on that row. Buttons whose visible text already
  // interpolates the subject (`Add to {c.name}`) are excluded — they are distinguishable as rendered —
  // and so are the per-row editors' `Cancel`/`Done`, whose handlers touch no item.
  const walkTsx = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walkTsx(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  /** Brace-aware `<Button …>` openings: `onClick={() => f(a)}` contains a `>`, so `[^>]*` ends the tag
   *  in the wrong place and every site reads as attribute-less. Four regex traps in this repo's rails
   *  came from exactly that. */
  function buttonTags(src: string, from: number, to: number) {
    const out: { tag: string; end: number }[] = []
    for (const m of src.matchAll(/<Button\b/g)) {
      if (m.index! < from || m.index! > to) continue
      let depth = 0
      for (let i = m.index! + m[0].length; i < src.length; i++) {
        const c = src[i]
        if (c === '{') depth++
        else if (c === '}') depth--
        else if (c === '>' && depth === 0) { out.push({ tag: src.slice(m.index!, i + 1), end: i }); break }
      }
    }
    return out
  }

  function rowActions() {
    const out: { rel: string; text: string; named: boolean }[] = []
    for (const abs of walkTsx(SRC)) {
      const raw = readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      for (const m of raw.matchAll(/\.map\(\((\w+)(?:,\s*\w+)?\)\s*=>/g)) {
        const item = m[1]
        for (const t of buttonTags(raw, m.index!, m.index! + 3000)) {
          const close = raw.indexOf('</Button>', t.end)
          if (close < 0) continue
          const text = raw.slice(t.end + 1, close).trim()
          if (/\{/.test(text)) continue                        // subject already visible
          if (!/^[A-Za-z][\w' ,.:—–-]{1,58}$/.test(text)) continue
          const oc = t.tag.indexOf('onClick={')
          if (oc < 0) continue
          let d = 1, j = t.tag.indexOf('{', oc) + 1
          for (; j < t.tag.length && d > 0; j++) { if (t.tag[j] === '{') d++; else if (t.tag[j] === '}') d-- }
          if (!new RegExp(`\\b${item}\\b`).test(t.tag.slice(oc, j))) continue   // not row-scoped
          out.push({ rel: abs.slice(SRC.length + 1), text, named: /ariaLabel=/.test(t.tag) })
        }
      }
    }
    return out
  }

  it('finds the population, and the five this cycle named are in it', () => {
    const all = rowActions()
    expect(all.length, 'the row-action scan must resolve its population').toBeGreaterThanOrEqual(10)
    const named = all.filter((r) => r.named)
    expect(named.length, 'the named ones').toBeGreaterThanOrEqual(5)
    expect(named.map((r) => r.rel)).toContain('pages/settings/DurabilityPanel.tsx')
  })

  it('the panel this cycle fixed has no unnamed row action left', () => {
    const mute = rowActions().filter((r) => !r.named && r.rel === 'pages/settings/DurabilityPanel.tsx')
    expect(mute, `still sharing one name across rows:\n${mute.map((m) => m.text).join('\n')}`).toEqual([])
  })

  it('the remainder is a measured ceiling of 5 — classify, do not add', () => {
    // `ChatPage` "Open", `GuardrailsPanel` "Hand back"/"Undo", `PacksPanel` "Install" ×2. Each needs its
    // own look: a row action gets the subject, a singleton stays as it is. The default for a NEW one is
    // "not yet reviewed", which is why this is a ceiling rather than a to-do list.
    const mute = rowActions().filter((r) => !r.named)
    expect(mute.length, `unnamed row-scoped actions:\n${mute.map((m) => `${m.rel} "${m.text}"`).join('\n')}`)
      .toBeLessThanOrEqual(5)
    expect(mute.length, 'and the census must still see the family it bounds').toBeGreaterThan(0)
  })
})
