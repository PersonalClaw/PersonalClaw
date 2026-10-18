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

## A push to main, and the nightly breadth

### The tested tree

`ci.yml` runs on every push to `main`. Its first job, `tree-check`, asks whether a green
pull-request run already tested the tree the push lands. The answer is yes only when all of
these hold:

- exactly one pull request into `main` was merged as the pushed commit;
- that pull request's head has the same tree as the pushed commit;
- the head contains the previous `main` commit. A `pull_request` run tests
  `refs/pull/N/merge`, and that tree equals the head's only when the head contains its base;
- the first attempt of every `pull_request` run on that head concluded `success`. A green
  rerun of a red first attempt is not enough;
- those runs were this pull request's into `main`: the first was made after it was opened, and
  its base was never changed. A run names neither its pull request nor its base, and a base
  change starts no run, so a run made for another pull request on the same head, or before a
  retarget to `main`, may have tested another base and skipped `test-macos`.

Anything else, and any error, is no. Every job outside the always-run set (`tree-check`,
`changes`, `security-corpus`, and `apps-contract` when the SDK changed) waits on the answer and
runs unless it is exactly `tested=true`. So a push behind its base, a merge queue's commit or a
direct push runs the whole gate, `test-macos` included.

The check is `tools/ci/tree_tested.py`, and the copy that runs is the previous `main` commit's,
read from the API at `github.event.before`. It is standard-library Python, run on the image's
own `python3` with nothing checked out or installed. A pull request that changes the check is
judged by the copy it replaces, and the merge that adds the check runs the whole gate.
`docker-single-container.yml` asks the same question about its own runs before it builds.

Runs on `main` are grouped by commit and never cancelled, so no merge goes unchecked because
a later one replaced it. Everywhere else a newer push still cancels the older run.

### A closed pull request's runs

A concurrency group cancels a run only when a newer run joins it, and closing a pull request
starts no run in the workflows that test it. `cancel-closed-pr-runs.yml` runs on
`pull_request` `closed`, merged or not. Each of its jobs joins one per-pull-request workflow's
group with `cancel-in-progress` and runs nothing but its Runner identity step: no checkout,
`permissions: {}`, and no `pull_request_target`. A job-level group and a workflow-level group are
one namespace across the repository's workflows (GitHub's "Control the concurrency of workflows
and jobs": group names "must be unique across workflows to avoid canceling in-progress jobs or
runs from other workflows"), so the job cancels that workflow's run.

Its keys are written for the close, not copied: when a pull request closes because it merged,
`github.ref` is the branch it merged into and `github.sha` its merge commit (GitHub's "Events
that trigger workflows", `pull_request`), so `ci.yml`'s own key would name the merge's `main`
group and its `cancel-in-progress` would read false. A workflow added later that runs on
`pull_request` needs a job here; the contract row below fails until it has one.

### `test-macos`

Pull requests into `main` run the suite on `macos-26` with Python 3.13, four shards split as
`test-shard` splits, two xdist workers each and no coverage. With `test-shard` on Ubuntu and
3.12, every change that merges has run both operating systems and both Pythons. The four
shards take four of the five macOS runners a run can hold, so concurrent pull requests queue
on macOS.

### `breadth.yml`

The rest of the platform breadth runs nightly (09:37 UTC) and on dispatch:

- **`freshness`** runs first. On `main` it reads the newest `breadth-evidence-<sha256>` record
  of an earlier `main` run, refuses it unless its bytes hash to that digest, and follows a
  carried entry once, to the run that executed it. Every record a run holds is read (a rerun
  of failed jobs adds one per attempt, listed across every page and ordered by artifact id),
  and a measured red beats a later attempt's green on the same inputs. A key whose newest
  entry measured nothing (a shard that wrote no entry, a green whose issue could not be read)
  is looked for in older records, so a red behind it is still carried. A run with no record
  (cancelled, or its report wrote none) is skipped. A key whose carried origin cannot be read
  is left out, and its shard runs. When there are no earlier results, every shard runs. It
  also publishes the run's night: the date of the 09:37 UTC slot at or before the run's
  `created_at`, which every attempt of a run keeps.
- **Tree-keyed legs:** Ubuntu 24.04 on 3.13, macOS 26 on 3.12 (six shards, two at a time),
  Ubuntu 24.04 on arm64 on 3.13, and coverage. Each shard reads its own runner's image,
  architecture and toolchain. On `main` it skips pytest and carries the earlier result, red
  or green, only when every input matches.
- **Legs that run every night:** the security corpus, a canary on the next Ubuntu image
  (advisory), the installer smoke, and the dependency audit. `NON_REUSABLE` in
  `tools/ci/breadth_evidence.py` names them.
- **`report`** runs last, whatever the legs concluded. It writes the record (canonical JSON)
  as the artifact `breadth-evidence-<sha256>`, kept 90 days, and puts the digest in the
  summary. It keeps one issue per red leg, titled `breadth: <leg> is red`, and reads only what
  the workflow's own bot wrote on it (its search names the bot as author, and a full or
  incomplete page is unreadable). Each red adds its inputs to the issue. A green on inputs that
  differ from every red's closes it. A green on any red's inputs is a flake: it is added to the
  issue, does not close it, and the record keeps the leg red. A rerun of the run produces one,
  whatever the clock says, because it keeps the run's night. So does a later night of the
  security corpus or the canary while none of their recorded inputs has changed: they stay red
  until a change of input, or until the issue is closed by hand. The installer smoke and the
  audit record the night as an input, because their outside state moves daily, so for them the
  next scheduled night is a changed input. It holds `issues: write` and writes issues only on
  `main`.
- **A shard that writes no entry is red,** never absent. Its runner was lost, or it failed
  before it recorded anything. It ran on no inputs, so its red never hides the inputs an
  earlier red ran on, and no later run carries it: the next run looks past it to the shard's
  last measured result. A leg whose issue cannot be read has its greens recorded red, marked
  `unsettled`, which no later run carries; a red it measured stays a red and opens the issue.
  The record is still written. The issues step tries every leg, and fails at the end naming
  each one it could not write.

A record satisfies a release alone only when it holds every shard, none of them carried, and
all of them green.

### Off `main`, nothing is reused

On a tag or a release branch, no earlier result is read or carried, and no leg issue is read.
`previous` writes an empty map and reads no earlier run (the `freshness` job still asks the API
for its own run's `created_at`, for the night), every `decide` says run whatever map it is
handed, the tree-keyed shards download the map only on `main`, and every leg executes on every
dispatch. `report` reads none of `main`'s leg issues off `main`, so an open issue there can
neither turn a tag's green into a flake nor, by failing to load, pin it red. The record a
release reads is what that dispatch measured.

"Main" is the full ref, `refs/heads/main`: every command is handed `$GITHUB_REF`, never the
short `$GITHUB_REF_NAME`, which is `main` for a tag called `main` too.

Why: the reuse on `main` rests on the leg issues, which hold every red's inputs and turn a
green on them into a flake. Off `main` no issue is written, so reuse there had to rebuild that
memory from earlier records. Each round of that (blind keys, a window over a tag's own runs
and `main`'s, a check of the never-reused legs against every red in it) opened a new way for a
red to come back green, or for a key to stay blind across dispatches. A release is rare and
the full breadth is affordable for it, so off `main` it is measured, not reconstructed.

Every entry and every record names the run attempt that wrote it (`attempt`, from
`GITHUB_RUN_ATTEMPT`). Three rules hold within one run:

- **A key is held to its job's latest attempt.** On any rerun, on `main` too, `report` reads the
  run's jobs as their latest attempts left them (`jobs?filter=latest`; each leg's job is named
  `breadth-leg <key>`). A rerun's entries are the newest of each name across attempts, so a
  shard that wrote none on its latest attempt would be stood in for by an older one. A key is
  red, marked `shard_job`, when its job did not succeed in its latest attempt, or ran in a later
  attempt than its entry names. When the jobs cannot be read, every key is red.
- **A red stays red on a rerun.** Off `main`, on attempt N > 1, `report` reads the run's earlier
  attempts' records. A key red on any of them is red on this one, whatever its inputs, marked
  `red_on_an_earlier_attempt`. Without a record of each of attempts 1 to N-1, by the attempt
  each names, what a missing attempt measured is not known, so every key is red, marked
  `earlier_attempt_unread`.
- **A release refuses a gap or any red.** `satisfies_release` takes every record of the run and
  the run's newest attempt, read from the run itself. It accepts the run only when there is a
  record of every attempt from 1 to the newest, the newest attempt's record satisfies a release
  alone, and no attempt recorded a red. An attempt that measured a red and wrote no record
  (cancelled, a failed upload, still running) is a gap, so the run is refused rather than
  accepted on an older attempt's green.

A re-dispatch is a new run, and so a new measurement: dispatch 1 red, then dispatch 2 green,
gives dispatch 2 an all-green record. Whether a release may proceed while an earlier dispatch
of the same commit was red is the release gate's question (#3911), not this workflow's.

### The rows that hold it

`tests/test_ci_contract.py` holds each of these with planted violations:

- `ci.yml` runs on every push to `main` (no negated branch, no path filter) and never cancels a
  `main` run, at the workflow or the job level;
- `test-macos` runs `test-shard`'s split on macOS under exactly the condition that runs it on
  pull requests into `main` and on an untested push: the same pytest words but the worker count
  and the report path, `--group ${{ matrix.shard }}`, the same job env and defaults, the same
  step `if:`, `working-directory`, env and shell, no `exclude`, and no `PYTEST_ADDOPTS` in a
  step's env or written by a step;
- every job outside the always-run set waits on `tree-check`;
- `tree-check` runs the previous commit's copy, read-only;
- no `macos-14` leg anywhere (any size or case, in `runs-on` as a string, a list or a
  `{group, labels}` mapping, an expression's quoted literal, or any value of the matrix it
  names), and no coverage on a push or pull request: per command, one that names `--cov` or
  `--cov=…` measures coverage, and nothing else does, because the addopts measure none;
- every pytest step that runs the suite names its worker count with `-n`: a step that takes a
  split (`--splits` or `--group`), names no path, or names only `tests`. A step that names its
  own files runs in one process, pytest's default;
- no workflow command passes `--no-cov`, which would only undo a default that is not there;
- `freshness` first and `report` last;
- `NON_REUSABLE` matches the jobs;
- the evidence is named by its digest and kept 90 days;
- `report` writes only issues, and only on `main`;
- every breadth shard records what it ran: every job but `freshness`, `coverage` and `report`
  is a leg writing entries under one key no other job writes, its `decide --key` is that key,
  and the keys with their matrices' shard counts (every dimension, no `exclude`) are `SHARDS`;
  a managed Python is installed before `uv python find`; a crashed decision runs the shard;
  every `--tree` is the checkout's or freshness's with no fallback; the served installer is
  never hashed from a `curl … | sha256sum` pipe (continued lines joined), and an unreadable one
  exits red after its inputs are written; the installer smoke and the audit record freshness's
  night, read from the run's `created_at` and tied to the cron slot; and a failed coverage
  download is a failure;
- off `main` nothing is reused: `freshness`, every `decide` and `report` pass the full ref
  (`$GITHUB_REF`); every `decide`, `ran` and `report` pass the run attempt; every leg's job is
  named `breadth-leg <key>`; no never-reused leg downloads `freshness`'s map, and a tree-keyed
  shard downloads it only on `refs/heads/main`;
- `docker-single-container.yml` checks the tree before it builds on `main`;
- every workflow that runs on `pull_request` has its run cancelled when the pull request closes:
  the row evaluates each workflow's group on a pull request's run and the canceller's on its
  close, merged and unmerged, from this repository and from a fork, and holds them equal with
  `cancel-in-progress`. It also fails a workflow with no group to join, a canceller job whose
  group a merge's push to `main` would share, and a canceller that runs anything, holds a
  permission or runs on any other event.

The rows check the edits a maintainer plausibly makes: a respelled flag, a new leg, a moved
condition, a fallback added for convenience. They do not evaluate expressions or shell, so a
deliberately adversarial rewrite (a coverage flag assembled from variables, a hash computed in
a helper script) can pass them; review is what holds those.

## Supply chain

The actions, images and tools the workflows and Dockerfiles fetch are named exactly, so a run
tests the same inputs on a day nothing in the repository changed, and every job holds only the
token permissions it uses. The rules live in `tools/ci/supply_chain.py` (with the shell reader
in `tools/ci/shell_installs.py`), the policy they read in `tools/ci/supply-chain.toml`, and
`tests/test_ci_supply_chain_contract.py` holds each rule as a row with planted violations.

### What the check reads, and what it does not

It reads every workflow (`.yml` and `.yaml`), every local action's `action.yml` (its steps
are judged as if they ran in the job that calls it), every Dockerfile or Containerfile in the
tree whatever its spelling, `.github/dependabot.yml`, `.nvmrc`, the root `package.json` and
`package-lock.json`, and the policy. A workflow or action file that does not parse fails
(`workflow-yaml`), and that rule cannot be waived.

It does not read, and review holds instead:

- **compose files.** The only images they name are the project's own GHCR images, which
  `clean-machine-walkthroughs.yml`'s `v3-container` pulls at `latest` on purpose: the
  published image is what that job tests.
- **`[build-system] requires`** in a `pyproject.toml`. The root pins its build backend exactly,
  and `tests/test_verify_wheel_contract.py` holds it equal to `uv.lock`;
  `packages/personalclaw-client-py` does not yet.
- **The pre-commit hook and Nix.** No workflow runs the hook. `flake.lock` locks nixpkgs by rev
  and hash, and `nix/pins.toml` pins each wheel by sha256.
- **What an exact pin brings with it.** An exact version names the tool, not its dependencies:
  without a lock or hashes the dependency tree resolves on the day. That is true of
  `pip-audit`'s tree, PyInstaller's hooks, and installs of this project itself (editable, as a
  uv tool, or the client package). Exact top-level pins are the boundary this check
  holds; transitive pins come from a lock (`uv sync --locked`, `npm ci`,
  `pip install --require-hashes -r`).
- **Whether a comment's digests are the image's.** The contract runs offline: it requires the
  comment above an image to name the tag the line pins and each platform's own digest, but
  does not ask a registry whether those digests are that tag's.
- **A download run in a later step.** A download piped into a shell or an interpreter, given
  as `<(curl …)` or as `sh -c "$(curl …)"`, fails; a file downloaded in one command and run in
  another (`curl -o x.sh … && sh x.sh`) is not traced.

### How to pin

- **An action:** a full commit SHA with its exact release as a comment,
  `uses: owner/repo@<40-character sha> # vX.Y.Z`. A bare `# v7` fails. A `docker://` action is
  an image and is pinned as one.
- **A workflow's container or image:** a tag with the digest of its linux/amd64 manifest, and a
  comment above it naming that platform and the index the manifest belongs to:
  `# ubuntu:26.04, the linux/amd64 manifest of index sha256:<index>`. Pinning the index digest
  itself fails, because an index is not any one platform's image.
  The line must hold the whole value: an image behind a trailing comment, a `${{ }}`
  expression or a flow mapping fails, and so does a `services:` image. setup-buildx's BuildKit
  image (`driver-opts: image=`) is judged the same way.
- **A Dockerfile base:** a tag with its index digest on the `FROM` line, and a comment above it
  that names the tag and gives each platform's own manifest digest
  (`#   linux/amd64 sha256:<digest>`), none of them the index. Write the base on the `FROM`
  line: a base named through an `ARG` fails, because Dependabot cannot move it, and a build
  that overrides a base `ARG` with `--build-arg` or `build-args` fails too. `COPY --from=` and
  `RUN --mount=from=` an image are judged as bases. Dockerfiles are read wherever they are in
  the tree, under any spelling (`Dockerfile-dev`, `app.dockerfile`).
- **An image a `run:` step fetches:** `docker run`, `pull` and `create`, and the `FROM` of a
  build fed on stdin, are judged as workflow images. The project's own GHCR images, and an
  image an earlier step of the job builds with `--tag`, are what those jobs test, so they pass.
- **An install a `run:` or `RUN` performs:** an exact version (`tool==X.Y.Z`, `pkg@X.Y.Z`), a
  lock (`npm ci`, `uv sync --locked`, `pip install --require-hashes -r`), or this project
  itself (a path in the checkout, a built wheel, or `personalclaw`). A URL, a VCS reference or
  a `$VAR` is never this project. A version through a variable counts only when the step's,
  job's or workflow's `env:`, or the Dockerfile's `ARG` default, gives it a literal exact value.
  `npm install` in a workflow, `npx` of a package neither pinned nor installed by an `npm ci`
  earlier in the job (or stage), `uvx`, `pipx run` and `uv run --with` without a version,
  `uv pip sync` without hashes, and installers of other ecosystems (`go install`, `cargo`,
  `gem`, `pnpm`, `yarn`, `bun`) all fail. The command is found wherever the shell would run it:
  after `if`/`then`/`do`, inside `( )`, `{ }` and `$( )`, behind `sudo`, `env`, `timeout`,
  `xargs` and the like, in `sh -c "…"`, behind a Dockerfile `RUN`'s flags, in its exec form and
  in its heredocs. A `#` comment after a pin is not part of it.
- **A download a shell or an interpreter runs** fails: piped into `sh`, `bash`, `python` and the
  like (through `tee` or `sudo -E` too), or given as `<(curl …)` or `"$(curl …)"`. The one
  allowed is the public installer, `https://personalclaw.dev/install`, piped into `sh` in
  `breadth.yml`'s install-smoke step that tests it, and nowhere else.
- **A setup action:** a literal exact version or the file that names one, never `check-latest`
  (anything but a literal `false` fails) and never an expression. setup-qemu names its binfmt
  image by digest; setup-buildx names an exact buildx release, and its BuildKit image by digest
  in `driver-opts`. A setup or install action the check does not know fails until its version
  inputs are added to `SETUP_INPUTS` (or `INSTALLS_BUNDLED`, for one whose pinned release
  bundles its tool) in `tools/ci/supply_chain.py`.
- **Node:** every Node that CI or an image runs is on a line `[node_end_of_life]` dates, before
  that date: `.nvmrc`, each setup-node version, and every `node:<tag>` image a workflow, an
  action or a Dockerfile names. A setup-node `node-version-file` other than `.nvmrc` fails, and
  so does an OS package's `nodejs`, whose line is the distribution's. The dates come from the
  Node.js release schedule. Add a line when the repository moves to it, and leave past lines
  in place.

Other OS packages (`apt-get`, `apk`, `brew`) are outside the install rule. They come with the
image the job runs in, which is pinned by digest, or with the runner image, which every job
logs in its Runner identity step.

### How a refresh arrives

- **Actions:** Dependabot's `github-actions` ecosystem, weekly. Its PR moves the SHA and the
  comment together.
- **Dockerfile bases:** Dependabot's `docker` ecosystem for `deploy/docker/`, weekly. It moves
  the `FROM` line. In the same PR, refresh the tag and the platform digests in the comment
  above it by hand, since Dependabot does not read comments, and the contract fails a comment
  that no longer names the tag. Node has a group of its own and no major moves: a Node line
  change also moves `.nvmrc`, `release-image-tooling.yml`'s base (held equal to
  `Dockerfile.web`'s) and `[node_end_of_life]`, so it is a reviewed edit. The contract's plants
  read today's pins from the tree, so a refresh needs no test edit.
- **Workflow containers, QEMU, BuildKit and buildx:** a reviewed edit, by hand. The release's
  QEMU and buildx steps are also run by `release-image-tooling.yml`'s `pin-smoke` job on every
  pull request that changes `release.yml`: a two-platform build of a two-line image on the
  images' own node base, with no permissions, no other action and nothing pushed. The contract
  holds its pins and base equal to `release.yml`'s and `Dockerfile.web`'s, and requires it to
  build every platform the release builds.
- **Tools named by hand** (`pip-audit` in the audit job, `wheel` in the gateway image,
  `pyinstaller` in the desktop builds): nothing refreshes them yet. Moving one is a reviewed
  edit of its line. `packaging` in `release.yml`'s notes step is named at the version
  `uv.lock` holds, and a test holds the two equal: a Dependabot python-deps PR that moves
  packaging in `uv.lock` is red until that line follows.
- **The pin check:** `action-pins.yml` asks the GitHub API, once per release, which commit
  each pinned tag names, following an annotated tag to its commit. It reads the workflows and
  the local actions. It fails on a SHA that is not its tag's commit, a tag that is gone, and a
  pin with no release comment. It runs weekly, for a tag that moves upstream, where an answer
  the API could not give fails too; and on every pull request that changes a workflow, a local
  action or the check, where such an answer is a warning, so an API blip cannot turn the pull
  request red. It holds `contents: read`, and uses its token for reads only.

### Least privilege

- No `write-all`, and every job declares its permissions or inherits declared ones. A job that
  calls another repository's workflow declares them too; a call to a local workflow is exempt,
  because that workflow's own jobs are read here.
- A write is granted on the job that uses it, never at the top of a workflow, where it would
  reach every job added later.
- Every write a job holds, and every checkout that keeps its credentials, is an
  `[[audited_write]]` in the policy: the workflow, the job, the permission, the steps that use
  it and why. A write that is not listed fails, and so does an entry that matches nothing. A
  credential entry names each checkout that keeps its credentials by a `name:` no other checkout
  in the job shares. A local action's checkout may never keep them, since it runs in every job
  that calls it. Every checkout sets `persist-credentials: false` today, so none is listed.
- No workflow-level, job-level or job-container `env:` hands the job token or a secret to every
  step, in any spelling (`secrets.X`, `secrets['X']`, `toJSON(secrets)`, any case); set it on
  the step that calls the API. An `env:` that is an expression fails, since its variables cannot
  be read.

The audited writes today:

| Workflow | Job | Permission | Used by |
|---|---|---|---|
| `dco.yml` | `dco` | `pull-requests: write` | the sign-off remediation comment |
| `pr-feedback.yml` | `lint-help` | `pull-requests: write` | the lint remediation comment |
| `release.yml` | `pypi`, `pypi-client` | `id-token: write` | Trusted Publishing to PyPI |
| `release.yml` | `images` | `packages: write` | pushing the two images to GHCR |
| `release.yml` | `notes` | `contents: write` | creating the GitHub Release |
| `release.yml` | `attest` | `id-token: write`, `attestations: write` | build-provenance attestations |
| `breadth.yml` | `report` | `issues: write` | one issue per red nightly leg |

### Exceptions

An `[[exception]]` waives exactly one violation of one rule in one file, while a pin cannot hold
yet. It names `file`, `reference` and `scope` exactly as the row prints the violation (for a
workflow step, `<job> <step>: <problem>`; a repeat of the same problem in a file gets ` (2)`),
with an `owner`, a `reason` that says what ends it, and an `expires` date at most 30 days after
the day it is written. Every field but `expires` is a non-empty string. Only the pinning rules
may be waived (`action-sha`, `image-digest`, `run-install`, `setup-floating`, `node-eol`); a
write is audited, never waived, and a file that does not parse is never waived.

An expired exception, one more than 30 days out, one that matches nothing, one that matches
more than one violation, and one missing a field all fail. To renew one, set a new expiry and
say in the reason what has changed. To end one, fix the reference and delete the entry: an
exception left behind after its violation is gone fails as matching nothing.

The day an exception expires, or a Node line reaches its end of life, CI goes red with no
change to the repository. That is the policy: a dated exception is a deadline, and a Node past
its end of life gets no security fixes. Renew or fix before the date.

The exceptions today:

| File | Rule | What it waives | Ends when |
|---|---|---|---|
| `deploy/docker/Dockerfile.backend` | `run-install` | the four agent CLIs installed at their newest | a lock under `deploy/docker/` names them |
