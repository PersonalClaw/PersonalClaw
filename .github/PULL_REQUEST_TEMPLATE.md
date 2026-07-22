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
Until LIFECYCLE-DOCTRINE (plan 31) lands, class-B/S changes ship as clean breaks
under the pre-1.0 banner — say so here.
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
