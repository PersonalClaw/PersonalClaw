# CI

The rest of this page, the rulesets on `main` and how they are applied and read back, comes with
the change that replaces classic branch protection with rulesets.

## Trust boundary

A candidate (a pull request, or a push to a branch other than `main`) must not be able to change
the code that decides whether that same candidate may merge or publish. The line between what
decides and what is decided is drawn here once, and the workflows, `tools/ci/` and the CI
contract table follow it.

### Trusted

- **The workflows GitHub runs from the default branch:** `pull_request_target`, `workflow_run`,
  `schedule`, and pushes or dispatches on `main`. GitHub takes these from `main`, whatever the
  candidate's copy says.
- **The default branch's copy of `tools/ci/`,** checked out by one of those workflows.
- **The push of a `v*` tag,** once only the release controller can create one, on a verified
  `main` commit. Until the tag rules are in place, a tag push is trusted only as far as the
  person who pushed it.

### Untrusted

Everything a candidate brings:

- its workflows, its `.github/actions/`, and its catalogue, planner and verdict code;
- every artifact and every job output of its run.

Candidate code means any job of a `pull_request` workflow, of a workflow a push to a branch
other than `main` runs, or of a workflow either of those calls, and any step that checks out a
pull request's ref. GitHub runs a fork's pull request with a read-only token and no secrets; a
same-repository branch gets whatever its own workflow asks for, which is why the rows below hold
every candidate job to a read-only token whoever opened it.

### How a trusted job reads a candidate

A trusted job reads candidate output only as data:

- It takes the pull request, the head commit and the tree from the event or the API, never from
  an artifact. `pr-feedback.yml` finds the one open pull request whose head is the run's head
  commit; the artifact's `pr.txt` is only cross-checked against it, and a mismatch posts
  nothing.
- It never checks out or runs candidate code. It checks out the default branch, and puts any
  artifact it downloads outside that checkout.
- It shows candidate text as text. `scripts/ci_pr_feedback.mjs` fences the lint output itself,
  with a fence longer than any backtick run inside it, so text that closes a block or adds a
  link stays inside the fence.

### Definitions: which files judge

The definitions are the files that decide which jobs run, what each one runs and how the
verdict is computed: `.github/workflows/**`, `.github/actions/**`, `tools/ci/**` (the catalogue
included), the CI contract table, and every file a command names as its entry point, such as
the `Makefile` for a `make` target or a script under `scripts/`. Until the catalogue exists, a
workflow step is a command. Files an entry point loads are listed by hand in
`REVIEWED_HELPERS` in `tools/ci/trust.py`; add a helper there when an entry point starts
loading one.

`tools/ci/trust.py` compares the full set of those paths, each with its git mode and object id,
at the candidate's head with the default branch's. So an added, deleted or renamed file counts
as a change, and so does one whose content is unchanged but whose mode is: an executable bit, or
a file that becomes a symlink or a gitlink.

- **Equal:** the candidate's run used the trusted definitions, and its per-job results count.
- **Different:** the candidate's run is advisory. The verdict comes from a run of the default
  branch's definitions on the candidate's tree, which holds a read-only token, references no
  secret and saves no cache that `main` or a release restores. A changed controller judges only
  after it merges.

### Receipts

A GitHub receipt (the merge verdict's output, the push's reuse receipt and the release
manifest) names its judge beside what it judged: `controller_revision` (the commit of the
`tools/ci` copy the trusted job ran), `head_sha`, `tree_oid`, `external_revisions`, `toolchain`
and `evidence_digests`. `trust.check_receipt` refuses a receipt that lacks one, whose controller
revision is not a commit on `main`, or whose tree is not its head's tree. A local receipt names
no controller, because a local result never stands in for GitHub's.

### Reachability

`trust.require_on_main` refuses a commit that is not an ancestor of `main`. Only a full commit
id is accepted, and a shallow clone refuses, because it cannot show ancestry.

### The rows that hold it

`tests/test_ci_contract.py` holds three workflow rules, each with a planted violation per form:

- No `pull_request_target` or `workflow_run` workflow checks out candidate code: a checkout of
  the pull request's or the run's head, a `refs/pull/` ref, `github.head_ref`, the head
  repository, `gh pr checkout`, or a `git fetch` of a pull request's ref.
- No job that runs candidate code holds a write permission or a secret: a write grant,
  `write-all`, no `permissions` at all (the repository default is unknown to the file), a
  `secrets.` reference anywhere in the job, or `secrets: inherit`.
- Only `release.yml` holds a publishing permission (`contents`, `packages`, `id-token`,
  `attestations` or `deployments` write, or `write-all`) or any deployment environment. A new
  environment has to argue its way into `release.yml`.

Until the catalogue lands, these rows run in the candidate's own suite, so a candidate could
delete one. Its validator then runs the table from the default branch.

## Supply chain

Everything CI fetches is named exactly, so a run tests the same inputs on a day nothing in the
repository changed, and every job holds only the token permissions it uses. The rules live in
`tools/ci/supply_chain.py`, the policy they read in `tools/ci/supply-chain.toml`, and
`tests/test_ci_contract.py` holds each rule as a row with planted violations.

### How to pin

- **An action:** a full commit SHA with its exact release as a comment,
  `uses: owner/repo@<40-character sha> # vX.Y.Z`. A bare `# v7` fails. A `docker://` action is
  an image and is pinned as one.
- **A workflow's container or image:** a tag with the digest of its linux/amd64 manifest, and a
  comment above it naming that platform and the index the manifest belongs to:
  `# ubuntu:26.04, the linux/amd64 manifest of index sha256:<index>`. Pinning the index digest
  itself fails, because an index is not any one platform's image.
- **A Dockerfile base:** a tag with its index digest, and each platform's manifest digest in the
  comment above. A base named through an `ARG` is judged by the `ARG`'s default, which must
  exist. Dockerfiles are read wherever they are in the tree.
- **An install a `run:` or `RUN` performs:** an exact version (`tool==X.Y.Z`, `pkg@X.Y.Z`), a
  lock (`npm ci`, `uv sync --locked`, `pip install --require-hashes -r`), or this project
  itself. `npm install` in a workflow, `npx` of a package that is not in the lock, `uvx` and
  `uv run --with` without a version, and a download piped into a shell all fail. The public
  installer's own `curl … | sh` in `full.yml`'s install-smoke is what that job tests, so it is
  allowed.
- **A setup action:** an exact version or the file that names one, never `check-latest`.
  setup-qemu names its binfmt image by digest; setup-buildx names an exact buildx release, and
  its BuildKit image by digest in `driver-opts`.
- **Node:** every Node that CI or an image runs is on a line `[node_end_of_life]` dates, before
  that date. The dates come from the Node.js release schedule. Add a line when the repository
  moves to it, and leave past lines in place.

OS packages (`apt-get`, `apk`, `brew`) are outside the install rule. They come with the image
the job runs in, which is pinned by digest, or with the runner image, which every job logs in
its Runner identity step.

### How a refresh arrives

- **Actions:** Dependabot's `github-actions` ecosystem, weekly. Its PR moves the SHA and the
  comment together.
- **Dockerfile bases:** Dependabot's `docker` ecosystem for `deploy/docker/`, weekly. It moves
  the `FROM` or `ARG` line. Refresh the platform digests in the comment above it by hand in the
  same PR, since Dependabot does not read comments.
- **Workflow containers, QEMU, BuildKit and buildx:** a reviewed edit, by hand. The release's
  QEMU and buildx steps are also run by `release-image-tooling.yml`'s `pin-smoke` job on every
  pull request that changes `release.yml`: a two-platform build of a two-line image, with no
  permissions and nothing pushed. The contract holds its pins equal to `release.yml`'s.
- **Tools named by hand** (`pip-audit` in the audit job, `wheel` in the gateway image,
  `pyinstaller` in the desktop builds): nothing refreshes them yet. Moving one is a reviewed
  edit of its line.
- **The weekly check:** `action-pins.yml` asks the GitHub API, once per release, which commit
  each pinned tag names, following an annotated tag to its commit. It fails on a SHA that is
  not its tag's commit, a tag that is gone, a pin with no release comment, and any answer the
  API could not give. It holds `contents: read`, and uses its token for reads only.

### Least privilege

- No `write-all`, and every job declares its permissions or inherits declared ones.
- A write is granted on the job that uses it, never at the top of a workflow, where it would
  reach every job added later.
- Every write a job holds, and every checkout that keeps its credentials, is an
  `[[audited_write]]` in the policy: the workflow, the job, the permission, the steps that use
  it and why. A write that is not listed fails, and so does an entry that matches nothing.
  Every checkout sets `persist-credentials: false` today, so none is listed.
- No workflow-level or job-level `env:` hands `github.token` or a secret to every step; set it
  on the step that calls the API.

The audited writes today:

| Workflow | Job | Permission | Used by |
|---|---|---|---|
| `dco.yml` | `dco` | `pull-requests: write` | the sign-off remediation comment |
| `pr-feedback.yml` | `lint-help` | `pull-requests: write` | the lint remediation comment |
| `release.yml` | `pypi`, `pypi-client` | `id-token: write` | Trusted Publishing to PyPI |
| `release.yml` | `images` | `packages: write` | pushing the two images to GHCR |
| `release.yml` | `notes` | `contents: write` | creating the GitHub Release |
| `release.yml` | `attest` | `id-token: write`, `attestations: write` | build-provenance attestations |

### Exceptions

An `[[exception]]` waives one rule for one reference in one file, while a pin cannot hold yet.
It names `file`, `reference` and `scope` exactly as the row prints the violation, with an
`owner`, a `reason` that says what ends it, and an `expires` date at most 30 days after the day
it is written. Only the pinning rules may be waived (`action-sha`, `image-digest`,
`run-install`, `setup-floating`, `node-eol`); a write is audited, never waived.

An expired exception, one more than 30 days out, one that matches nothing, and one missing a
field all fail. To renew one, set a new expiry and say in the reason what has changed. To end
one, fix the reference and delete the entry: an exception left behind after its violation is
gone fails as matching nothing.
