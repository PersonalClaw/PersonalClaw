# `scratch/` — content prepared in-tree for the owner to publish

Nothing here is imported, installed or served by PersonalClaw. It is content that belongs in
**other** repositories, staged in this one so it can be reviewed, tested and version-controlled
before it is pushed. `scratch/` is outside `testpaths` and outside `make lint`'s targets, so it
does not participate in the core build.

ET-2 (`docs/roadmap/plans/ECOSYSTEM-TOOLING.md`, Session 1 T1.4) stages one thing: the template
repo below. Its second item — the apps-guide quickstart — is no longer staged here, because it
has landed in the repository it belongs to (see §2).

## 1. `app-template/` → `github.com/personalclaw/app-template`

The template repo's full content, to be pushed as the repository **root** (not inside a
subdirectory — the CI workflow reads `app.json` at the root).

```
app-template/
├── app.json                    # generated: app new app-template --type tool
├── provider.py                 # generated
├── app_cli.py                  # generated
├── test_provider.py            # generated (7 tests, pass as-is)
├── README.md                   # HAND-WRITTEN: the clone-to-installed walkthrough
├── LICENSE                     # generated (MIT)
├── .gitignore
└── .github/workflows/ci.yml    # HAND-WRITTEN: the apps-repo CI, root-level variant
```

The four generated files are pinned byte-for-byte to a fresh
`personalclaw app new app-template --type tool` run by
`tests/test_app_from_template.py::test_the_staged_template_is_byte_identical_to_a_fresh_scaffold`.
**If you change the scaffold, regenerate this directory in the same commit** — that test is the
only thing keeping the published template from contradicting the generator. To regenerate:

```bash
personalclaw app new app-template --type tool --dir scratch --force \
  --display-name "App Template" \
  --description "The PersonalClaw app template: clone it, rename it, ship it." \
  --author "PersonalClaw contributors"
```

(That overwrites `README.md` and `LICENSE` too, so restore those two from git afterwards.)

Owner steps to publish:

1. Create `personalclaw/app-template` (public, no template-repository flag needed — the repo
   is cloned or fetched as a tarball, not used via GitHub's "Use this template" button).
2. Copy this directory's contents to the repo root, `git commit -s`, push to `main`.
3. Nothing in core needs updating: `app new --from-template` already points at
   `https://codeload.github.com/personalclaw/app-template/tar.gz/refs/heads/main`.

**Until step 2 lands, the live fetch is unproven.** `--from-template` is proven end-to-end
against a local tarball and a loopback HTTP server; the one thing no test can cover is whether
the org repo answers, because it does not exist yet.

## 2. The apps-guide quickstart — LANDED, nothing to publish

The "minutes to first run" quickstart is **in the apps repo**, at the top of
`PersonalClawApps/docs/app-creation-guide.md`. It is not staged here any more: a staged copy of
an already-delivered document has no consumer and only invites drift, so the apps repo is the
single copy. Every command in it was executed verbatim from an empty directory against a
freshly-homed gateway, and the wall-clock numbers in the text are measured, not targets.

The apps repo has no docs test tier, so nothing in **this** repo can pin that text. The same
auth-shape invariant it depends on (`?token=` query parameter, never a Bearer header) is pinned
here on the template README by
`tests/test_app_from_template.py::test_the_staged_readme_uses_the_query_token_not_a_bearer_header`.

## 3. `registry/` → `github.com/personalclaw/registry`

The community-listing data tier (`ET-3`): `app-registry.json` + its generated schema, `validate_registry.py`,
three GitHub workflows, `CONTRIBUTING.md` (listing policy) and `DELISTING.md`, plus fixture apps the tests
build real one-commit git repos from. Publish steps are in an HTML comment at the top of
`registry/README.md`, same as `app-template/`.

**Two things to know before publishing.** The validator only accepts `https://` repo URLs; `file://` needs
`--allow-file-repos`, which the offline tests pass and **no workflow does** — that is pinned by a test, so the
test affordance is not a production hole. And the listing policy in `CONTRIBUTING.md` marks its own
provenance: four rules come from the plan, the rest are proposed and want an owner ruling.
