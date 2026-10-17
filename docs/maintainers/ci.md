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

### `test-macos`

Pull requests into `main` run the suite on `macos-26` with Python 3.13, four shards split as
`test-shard` splits, two xdist workers each and no coverage. With `test-shard` on Ubuntu and
3.12, every change that merges has run both operating systems and both Pythons. The four
shards take four of the five macOS runners a run can hold, so concurrent pull requests queue
on macOS.

### `breadth.yml`

The rest of the platform breadth runs nightly (09:37 UTC) and on dispatch:

- **`freshness`** runs first. It reads the newest `breadth-evidence-<sha256>` record from an
  earlier run, refuses it unless its bytes hash to that digest, and follows a carried entry
  once, to the run that executed it. On `main` it reads only `main`'s runs. On another ref (a
  release gate's tag) it reads that ref's own runs first, so a red there is carried rather than
  run again on the same inputs. When there are no earlier results, every shard runs.
- **Tree-keyed legs:** Ubuntu 24.04 on 3.13, macOS 26 on 3.12 (six shards, two at a time),
  Ubuntu 24.04 on arm64 on 3.13, and coverage. Each shard reads its own runner's image,
  architecture and toolchain. It skips pytest and carries the earlier result, red or green,
  only when every input matches.
- **Legs that run every night:** the security corpus, a canary on the next Ubuntu image
  (advisory), the installer smoke, and the dependency audit. `NON_REUSABLE` in
  `tools/ci/breadth_evidence.py` names them.
- **`report`** runs last, whatever the legs concluded. It writes the record (canonical JSON)
  as the artifact `breadth-evidence-<sha256>`, kept 90 days, and puts the digest in the
  summary. It keeps one issue per red leg, titled `breadth: <leg> is red`, and reads only what
  the workflow's own bot wrote on it. A green on changed inputs closes it. A green on the same
  inputs is added to the issue and does not close it, and the record keeps the leg red. A rerun
  produces one. So does a later night of a leg that runs every night, while none of its
  recorded inputs has changed. The installer smoke and the audit record the night as an input,
  because their outside state moves daily, so for them a later night is a changed input. It
  holds `issues: write` and writes issues only on `main`.
- **A shard that writes no entry is red,** never absent. Its runner was lost, or it failed
  before it recorded anything. Its leg's issue opens or stays open, and it runs the next night.
  A leg whose issue cannot be read is recorded red, and the record is still written.

A record satisfies a release alone only when it holds every shard, none of them carried, and
all of them green.

### The rows that hold it

`tests/test_ci_contract.py` holds each of these with planted violations:

- `ci.yml` runs on every push to `main` (no negated branch, no path filter) and never cancels a
  `main` run, at the workflow or the job level;
- `test-macos` runs `test-shard`'s split, the same command and environment, on macOS, on pull
  requests into `main` and on an untested push;
- every job outside the always-run set waits on `tree-check`;
- `tree-check` runs the previous commit's copy, read-only;
- no `macos-14` leg anywhere, and no coverage on a push or pull request (a root pytest without
  `--no-cov` measures coverage, because the addopts turn it on);
- `freshness` first and `report` last;
- `NON_REUSABLE` matches the jobs;
- the evidence is named by its digest and kept 90 days;
- `report` writes only issues, and only on `main`;
- every breadth shard records what it ran: its shards are `SHARDS`, a managed Python is
  installed before `uv python find`, a crashed decision runs the shard, no `--tree` falls back
  to a constant, the served installer is hashed from a checked download, and a failed coverage
  download is a failure;
- `docker-single-container.yml` checks the tree before it builds on `main`.
