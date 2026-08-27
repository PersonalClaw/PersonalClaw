import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { notify } from '../../app/appSdk'
import { useQuery } from '../../lib/data'
import { PanelHeader, Section, ToggleRow, NumberRow } from './settingsUI'
import { FormSkeleton, LoadError } from '../../ui/ListScaffold'
import { TextLink } from '../../ui/TextLink'

/** The five runtime-editable `evals.*` fields — the FIFTH point of the config
 *  round-trip contract, which was the only one missing.
 *
 *  Measured before this: `evals.*` had a dataclass + `_meta`, a `load()`, a `to_dict()` and five
 *  entries in `_EDITABLE_CONFIG` (`dashboard/handlers/core.py`), and **zero** frontend controls —
 *  `git grep -in evals -- web/src/pages/settings` returned nothing across 33 subpages. Meanwhile
 *  `#/learning` told four panels' worth of users to turn the substrate on, and the only path that
 *  existed was `personalclaw config set`. A backend allowlist with no control is a feature only its
 *  author can reach.
 *
 *  🔑 EVERY LABEL AND HINT BELOW IS THE FIELD'S OWN `_meta`, VERBATIM. `EvalsConfig` in
 *  `config/loader.py` already carries the sentence the owner wrote for each knob, so inventing a
 *  second wording here would mean two answers to "what does this do" — and the one the CLI prints
 *  (`personalclaw config get --describe`) would not be the one the UI shows. `evalsCopy.test.ts`
 *  reads `loader.py` and fails if any string here drifts from it. The one addition is the cost
 *  sentence on the switch, marked below, because `_meta` describes the FIELD and this row has to
 *  describe the *decision* — see `COST_OF_TURNING_IT_ON`.
 *
 *  🪤 `evals.bakeoff_capture_enabled` IS NOT HERE, and its absence is the point. It is a
 *  privacy-sensitive input-capture flag deliberately kept OUT of `_EDITABLE_CONFIG` (the
 *  `inbound.mcp.allow_remote` precedent), so a control for it would PATCH a path the backend
 *  refuses — a switch that flips, 400s, and rolls back. Surfacing exactly what the allowlist
 *  permits is what makes this panel honest; `evalsRoundTrip.test.ts` pins both directions.
 *
 *  Ranges are the allowlist's own `min`/`max`. A stepper that let you pick a value the PATCH
 *  refuses is the same defect as a control for a non-allowlisted key, one layer down. */
type EvalsCfg = Record<string, unknown>

/** The sentence `_meta` cannot carry, because `_meta` describes a field and this describes a
 *  decision: the switch itself spends nothing, and everything it unlocks spends model calls.
 *
 *  It says "judge" specifically because that is the cost a reader will not predict. A study's own
 *  runs are obviously runs; the position-swapped LLM judge behind every pair is the half people
 *  discover from a bill. `judge_agreement_floor` below is the knob that exists BECAUSE the judge is
 *  a model and can be wrong — so the two rows explain each other, and this is the row that has to
 *  say it first.
 *
 *  Kept to ONE sentence on purpose: with the `_meta` help above it this row is already the tallest
 *  in settings, and at 390px `Row` centres the switch against the hint — so every clause costs
 *  vertical distance between the control and its own label. Measured: the first draft ran to 456
 *  characters and put the switch 200px below "Evals enabled" on a 390px viewport. */
const COST_OF_TURNING_IT_ON =
  'Enabling it spends nothing; the studies it unlocks spend real model calls — the judge that ' +
  'scores them is a model too, so set a budget below first.'

export function EvalsPanel() {
  const [cfg, setCfg] = useState<EvalsCfg | null>(null)

  // Stale-while-revalidate + persist, same as every other config panel: paint instantly on
  // revisit, revalidate behind it. The bare read is deliberate — see the LoadError branch.
  const { data, error: loadErr, refresh } = useQuery('settings:evals', () =>
    api.personalclawConfig().then((c) => (c.evals ?? {}) as EvalsCfg),
    { persist: true },
  )

  useEffect(() => { if (data) setCfg(data) }, [data])

  // Error BEFORE the skeleton: `data` is undefined for loading and for failure alike, so a
  // fallback here would render all five controls at their defaults — indistinguishable from
  // "this is what you saved", on rows that PATCH the moment you touch them.
  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={3} what="settings" />

  // Optimistic single-field PATCH; a rejection rolls the row back and NAMES the control the user
  // was looking at (not its config key — nobody has seen `study_default_k` on screen).
  const patch = (key: string, value: unknown, onSaved: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`evals.${key}`, value).then(onSaved).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Evaluations"
        hint="Measure whether a change actually helped — paired A/B studies over prompt templates, retrieval and judge benchmarks, and monthly ablations that ask whether a component earns its keep. Nothing runs on a schedule you did not set; every result is a file you can read." />

      {/* 🪤 `underline`, not just the accent ink. A coral link inside grey body copy is
          distinguishable ONLY by hue: measured `1.35:1` against the surrounding text, and axe's
          `link-in-text-block` (WCAG 1.4.1, serious) fires on exactly that — "The link has no
          styling (such as underline) to distinguish it from the surrounding text". `TextLink`'s
          hover-only underline is right for a standalone link and wrong mid-sentence, which is why
          `JudgeBenchPanel`/`RetrievalBenchPanel` already ship `className="underline"` on their
          in-prose `Settings → Models` links. Same idiom, one primitive. */}
      <Section title="The substrate"
        hint={<>Off by default. Turn it on and the results appear on{' '}
          <TextLink href="#/learning" ink="emphasis" className="underline">Learning</TextLink>,
          which is where the four eval panels live.</>}>
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <ToggleRow label="Evals enabled" cfg={cfg} field="enabled" patch={patch}
            hint={'Turn on the offline eval substrate — pre-registered studies, ablation reports, '
              + 'the retrieval/judge benchmarks. Off by default; nothing runs until you invoke a '
              + 'study or benchmark. Results are files under ~/.personalclaw/evals/, never a '
              + `background service. ${COST_OF_TURNING_IT_ON}`} />
        </div>
      </Section>

      <Section title="Study defaults" hint="What a study assumes when it does not say otherwise. Each study still declares its own k and budget at registration; these are the values it inherits.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <NumberRow label="Study runs per arm (k)" cfg={cfg} field="study_default_k" patch={patch}
            min={1} max={50}
            hint="How many paired runs per arm a template A/B study takes by default. k≈5 is the smallest paired design that survives judge noise; higher k buys confidence at a linear cost in runs and judge calls." />
          {/* step 0.05, not 1: this field is a RATE in 0…1, and the shared NumberRow stepped by 1,
              which would have offered exactly two reachable values — 0 and 1 — for a floor whose
              default is 0.6. A stepper that cannot express the saved value is not a control. */}
          <NumberRow label="Judge agreement floor" cfg={cfg} field="judge_agreement_floor" patch={patch}
            min={0} max={1} step={0.05}
            hint="Below this position-swap agreement rate a study's verdict is 'judge_unreliable' — it files a judge-calibration item instead of a template verdict, so a noisy judge never produces a fake win." />
          <NumberRow label="Default eval budget (USD)" cfg={cfg} field="default_budget_usd" patch={patch}
            min={0} max={1000} step={0.5}
            hint="The default hard spend cap a matrix/study run refuses to exceed. 0 means no default cap — each study still declares its own budget at registration." />
        </div>
      </Section>

      <Section title="Ablation" hint="Turning one component off — or down to a declared cheaper form — and replaying the benchmark, so its delta says whether it earns its keep.">
        <div className="rounded-lg bg-surface-container px-4 py-1">
          <NumberRow label="Ablation cadence (days)" cfg={cfg} field="ablation_cadence_days" patch={patch}
            min={1} max={365}
            hint="How often the harness-ablation runner picks one component to measure keep/remove/lighten. Monthly by default — component payoff drifts on the timescale of model upgrades, not days." />
        </div>
      </Section>
    </div>
  )
}
