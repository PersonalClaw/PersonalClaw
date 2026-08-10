> [!IMPORTANT]
> **Every commit must be signed off** (DCO) or CI fails. Commit with `git commit -s`.
> Forgot on commits you already pushed? Fix and re-push:
> ```bash
> git rebase --signoff main && git push --force-with-lease
> ```
> The sign-off name/email must match your commit author. See [CONTRIBUTING.md](../CONTRIBUTING.md#developer-certificate-of-origin-dco).

<!--
Thanks for the PR. Fill in the four sections below — they mirror the project's
definition of done (see CONTRIBUTING.md / AGENTS.md). A reviewer checks a PR at a
glance against these.
-->

## What changed

<!-- One paragraph. What behavior/code changed and why. -->

## Change class

<!--
R / B / S per the lifecycle mental model (see CONTRIBUTING.md#breaking-changes):
- R (reversible): no persisted-state or stable-surface change.
- B (behavioral): changes a stable surface (API/CLI/config) or persisted state.
- S (structural/schema): changes a stored schema or a stable contract.

The migration-backed gate/migration regime is deliberately deferred until the
architecture stops moving, so there is no gate/migration machinery to use yet.
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
