<!--
Thanks for the PR. Fill in the four sections below — they mirror the project's
definition of done (see CONTRIBUTING.md / AGENTS.md). A reviewer checks a PR at a
glance against these.

Every commit must be signed off (DCO): `git commit -s`. CI enforces it.
-->

## What changed

<!-- One paragraph. What behavior/code changed and why. -->

## Change class

<!--
R / B / S per the lifecycle doctrine:
- R (reversible): no persisted-state or stable-surface change.
- B (behavioral): changes a stable surface (API/CLI/config) or persisted state.
- S (structural/schema): changes a stored schema or a Tier-S contract.

LIFECYCLE-DOCTRINE (plan 31) is deliberately deferred to late in the roadmap, so
there is no gate/migration machinery to use yet.
- Maintainer, on a roadmap task: class-B/S ships as a clean break under the
  pre-1.0 banner — say so here, add a CHANGELOG entry, advise `personalclaw
  snapshot` in release notes.
- Contributor: aim for class R. If your change is B/S, describe the break here
  rather than building compatibility shims or migration helpers — the maintainer
  decides whether to take it, reshape it additively, or schedule it. See
  CONTRIBUTING.md#breaking-changes.
-->

Class: <!-- R | B | S -->

## What you validated as a user

<!--
Not just what you wrote — what you DROVE. Which flows in the UI/CLI, what you
checked in logs and persisted state. "Ran the endpoint" is not validation.
-->

## Docs touched

<!--
Config fields, routes, CLI flags, or user-visible behavior → docs move in the
same PR (docs/reference/, guides, the owning plan). Class-B/S → CHANGELOG entry.
Write "none" only if genuinely none apply.
-->
