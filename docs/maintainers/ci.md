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

`tests/rails/test_ci_contract.py` holds three workflow rules, each with a planted violation per form:

- No `pull_request_target` or `workflow_run` workflow checks out candidate code: a checkout of
  the pull request's or the run's head, a `refs/pull/` ref, `github.head_ref`, the pull
  request's test-merge commit (`merge_commit_sha`), the head repository, `gh pr checkout`, or a
  `git fetch` of a pull request's ref.
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

### Commit provenance

Every commit that reaches `main` is checked by `scripts/check_commit_provenance.py` (the rules
are in [CONTRIBUTING.md](../../CONTRIBUTING.md#developer-certificate-of-origin-dco)), in two
places:

- **`dco.yml`, on every pull request,** in `pr` mode: rules 1, 3 and 5, over the commits the
  API returns and the title in the event payload. It runs on `pull_request_target`, so it checks
  out only the base branch's script and `MAINTAINERS`, and puts no file or git object of the pull
  request on disk. The commits endpoint returns at most 250 commits, so a list short of the pull
  request's own count fails the check rather than passing half of it. It also answers for the
  pull request as it is when asked, not as the event saw it, so a list whose last commit is not
  the event's head (a push after the event, or a list cut short or reordered) fails it too.
- **`provenance.yml`, on every push to `main`,** in `main` mode: rules 1, 2, 3 and 5, over
  `before..sha`. It is a workflow of its own because each push checks only its own range, so a
  run that never finished would leave its commits unchecked for good. One job with no `needs:`
  cannot be dropped by `tree-check`'s answer or by another job's outcome, and at a few seconds on
  one runner, grouped by commit and never cancelled, it stays so whatever becomes of `ci.yml`'s
  main runs (#3915 plans one run per candidate).

`dco.yml` also runs when a pull request's title is edited, since rule 5 reads the title. Its job
token reaches one step, which only fetches the commits with `gh api`; the check that parses them
runs in the next step with no token. Each violation is also printed as an `::error::` annotation,
so a red run's summary names the commit.

The main push runs the **previous** main commit's copy of the script and of `MAINTAINERS`, read
with `git show "$BEFORE:…"` and run as `python3 -I` from the runner's temporary folder, as
`tree-check` runs the previous copy of its check. A push cannot edit the check, add its own
committer to `MAINTAINERS` or drop a module beside the script to pass itself; a change to either
file judges the pushes after the one that lands it. The one push with no previous copy is the one
that adds the check: `BEFORE`'s history never held the script, so that push runs its own copy and
says so in the summary. A `BEFORE` that once had the script and has lost it is refused, and so
is a `BEFORE` the checkout cannot resolve (all zeros, or an old tip a forced update orphaned):
a git failure is never read as "never had the check".

**What a squash merge must carry.** On `main` nothing can be amended, so the squash commit itself
has to pass. In GitHub's squash dialog, delete the `Co-authored-by` lines GitHub adds for the
other authors when the squash is yours: rule 3 refuses them on a maintainer's commit. Keep the
commit author's email equal to the email in its `Signed-off-by`: rule 1 reads the author GitHub
records, so a contributor whose GitHub commit email differs from their sign-off fails on `main`
(two of the four contributor squashes in history do).

**What it does not cover.** Rule 4, the pushed tree against the tree the merge controller
recorded for its pull request, is not checked: #3908 keeps it open until that record exists, and
where the record lives is settled with #3910. Until then `GitHub <noreply@github.com>` commits a
squash merge, a web-editor commit and a revert made from a pull request's page alike, and rule 2
passes all three. A push to `main` that starts no run at all (one made with a workflow's own
token, say) is never checked either, because the next run starts at its own `before`; rule 5
closes only the skip-marker case.

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

Pull requests into `main` run the suite on `macos-26` with Python 3.13, in four shards of whole
test files planned from macOS's own durations, two xdist workers each and no coverage. With
`test-shard` on Ubuntu and 3.12, every change that merges has run both operating systems and
both Pythons. The four shards take four of the five macOS runners a run can hold, so
concurrent pull requests queue on macOS.

### The suite's shards

Every suite leg runs in shards of whole test files, planned once per run by
`python -m tools.ci shards plan` (`tools/ci/shards.py`): in `ci.yml` by the `shard-plan` job,
and in `breadth.yml` by `freshness`. The plan is longest-processing-time greedy over per-file
seconds, heaviest file first into the lightest shard; a file with no record weighs the median.
`shards check` fails the run when a collectable file is in no shard or in two, so nothing is
dropped or run twice. Each shard downloads the plan, writes its own list with `shards list`
(which fails, writing nothing, for an empty shard: pytest given no selection collects every
test), and runs `pytest tests --shard-list FILE`, which collects from `tests` as a whole-suite
run does but imports only the files the list names (`tests/shard_list.py`). A breadth shard
records its list's digest as one of its inputs, so a result is carried only onto the files it
measured. Modules stay whole, so a module-scoped fixture is set up in one shard.

The per-file seconds come from #3948's telemetry record (see "CI telemetry" below): each file's
median first-attempt seconds over the leg's own reports in the three newest completed `main`
runs whose history the collector wrote, red ones included and cancelled ones skipped, on the
platform and worker count the leg's newest history recorded. It is the only source. Until the
first collected `main` run there is no record, and the plan weighs every file the same and says
so; that measured 1.29x on Linux and 1.33x on macOS on `main`'s run of d75e54a0, and lasts one
plan per leg. Only history the collector's own run uploaded is read: a `ci-telemetry.yml` run,
on `workflow_run`, on `main`, in this repository, whose history's first line names the CI run it
is read for. Any run, a fork's pull request included, can upload an artifact of the same name.
One run is not enough: a file's seconds swing with the runner it lands on (one file took
71-146 s over seven main runs), and replayed over those runs the median of three planned within
1.06x where one run planned up to 1.30x. Red runs count because their reports still time every
file they ran; reading green runs only froze the source for as long as `main` was red.

What no plan can balance is the runner itself. Over the same seven runs, all of one shard's
files together ran 0.66-1.52x their median on Linux and 0.70-1.95x on macOS, the whole
shard at once, as a slower host does. So each shard's log ends with "shard collection and
memory": each worker's collection seconds, its peak RSS at collection's end and over the run,
and the machine's CPUs, which shows a slow host for what it is.
The durations only balance the shards: when neither source can be read, every file weighs the
same and the plan is still whole.

On `main`, `breadth.yml` keeps the plan. A breadth shard's list digest is one of its inputs, so
a plan made afresh each night, from durations that move a few percent, would hand every shard
other files, and no tree-keyed shard would ever carry its result. Measured: two green `main`
runs a few hours apart planned 885 to 949 of each shard's files differently. So `freshness`
passes `--keep-from-main breadth.yml`, and the newest earlier `main` run's plan is reused for
every leg it still plans whole: the same test files, in the same number of shards. Any change
to the set of test files, or no readable earlier plan, plans afresh from the durations. The
plan is kept for a week, so the next night can read it. Off `main` nothing is reused, so no
earlier plan is read. `ci.yml` plans afresh on every run: nothing there is carried.

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
  `tools/ci/breadth_evidence.py` names them. The canary differs from the ubuntu-24.04 leg
  only in image and Python, so a red is the new image's: pytest keeps its own temporary
  folders, since `RUNNER_TEMP` sits under the runner's home, where the suite's home guard
  refuses a test's folders and paths print as `~/...`.
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

`tests/rails/test_ci_contract.py` holds each of these with planted violations:

- `ci.yml` runs on every push to `main` (no negated branch, no path filter) and never cancels a
  `main` run, at the workflow or the job level;
- `test-macos` runs `test-shard`'s split on macOS under exactly the condition that runs it on
  pull requests into `main` and on an untested push: the same pytest words but the worker count
  and the report path, its own shard of its own leg's plan, the same job env and defaults, the same
  step `if:`, `working-directory`, env and shell, no `exclude`, and no `PYTEST_ADDOPTS` in a
  step's env or written by a step;
- every job outside the always-run set waits on `tree-check`;
- `tree-check` runs the previous commit's copy, read-only;
- no `macos-14` leg anywhere (any size or case, in `runs-on` as a string, a list or a
  `{group, labels}` mapping, an expression's quoted literal, or any value of the matrix it
  names), and no coverage on a push or pull request: per command, one that names `--cov` or
  `--cov=…` measures coverage, and nothing else does, because the addopts measure none;
- every pytest step that runs the suite names its worker count with `-n`: a step that names no
  path, or names only `tests`, as every shard does. A step that names its own files runs in one
  process, pytest's default;
- every suite shard runs its share of its run's one plan: one job plans every leg and checks the
  plan, and each shard waits on that job, downloads the plan, lists its own leg's shard of N (its
  matrix size, as the plan names it) and runs pytest over `tests` with that `--shard-list`;
- no step that runs the suite keeps pytest's temporary folders under the runner's home: no
  `--basetemp` and no `TMPDIR` under `RUNNER_TEMP`, the workspace, `$HOME` or `~`;
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

## CI telemetry

Every record of CI history, per run and per test, is written by one component in one versioned
schema: `tools/ci/telemetry.py` (`python -m tools.ci telemetry`), run by `ci-telemetry.yml` from
main's own code. Nothing else writes durations, first results, reruns or quarantines, and
the shard planner reads its durations from it. The weekly report that reads it is #3948's next
change.

- **`collect`** runs when a CI or Breadth run completes. It counts only main's own runs: a CI run
  of a commit landing on `main`, and Breadth's scheduled or dispatched run on `main`, from this
  repository. It decides that from the run itself before it lists any artifact, and the job's
  `if:` holds the same rule, so a pull request's run starts no collector and its artifacts are
  never read. It writes `history-<run>-<attempt>.jsonl`: a first line naming the schema and its
  version, with the facts only the Actions API knows (queue delay, setup, time to the first
  failed job, verdict time, runner-minutes, cancelled work, artifact volume, schedule lag, one
  entry per job), then one line per test case of that attempt's junit reports.
- **A first result is never replaced.** Each attempt is collected on its own: from that
  attempt's own start, end and conclusion, its own jobs, and only the reports uploaded between
  its start and its end. So attempt 1's file keeps the first result even when it is collected
  after a rerun, and a rerun's file sits beside it, never over it. Jobs and artifacts are listed
  page by page against the API's count; a listing that comes up short writes nothing.
- **Where each test ran.** Each pytest process records, on its first test case, its platform
  (OS, machine and Python) and its number of xdist workers (`tests/run_facts.py`), and every test
  line of that report carries them. The planner keeps one platform's and one `-n`'s seconds
  apart, so a leg that moves to another `-n` plans from the first run that recorded it.
- **Artifacts are data.** An archive over 8 MiB is never downloaded, a report is read in memory
  up to 8 MiB, a report that is not UTF-8 or declares a DOCTYPE anywhere is refused, and nothing
  is extracted or run. A report that cannot be read fails the collection and writes nothing. The
  planner reads a history line by line, each line within 1 MiB and the whole within 256 MiB.
- **`slots`** runs daily on its own schedule, because a scheduled run that never starts fires no
  completion event. It writes one line per slot each scheduled workflow was due in the last
  eight days: `ran` (with its lag), `pending` (no run yet, and the next slot has not come), or
  `missed` (the next slot passed with no run for this one). A slot before the workflow existed
  was never due, and a disabled workflow's slots say `disabled`. Scheduled runs here start
  hours late (one workflow's last ten nights: 239-469 minutes), so a slot is called missed only
  once the next one has passed.
- **Kept 90 days.** One main run's history measured 49 MB as JSON lines and 2.7 MB compressed,
  about what its junit reports take. The junit reports stay at 7 days: the history holds every
  figure they give, so 90 days of both would double the storage for nothing. At the 59 main CI
  runs measured in one busy week and one Breadth night a day, 90 days is about 860 histories,
  roughly 2.3 GB compressed.
- **Not yet recorded:** `rerun_result` and `quarantined` are #3947's, `cache_hits` is #3909's,
  and `worker` and `position` are #3949's. No API reports them, so they are null, never guessed.

## Supply chain

The actions, images and tools the workflows and Dockerfiles fetch are named exactly, so a run
tests the same inputs on a day nothing in the repository changed, and every job holds only the
token permissions it uses. The rules live in `tools/ci/supply_chain.py` (with the shell reader
in `tools/ci/shell_installs.py`), the policy they read in `tools/ci/supply-chain.toml`, and
`tests/rails/test_ci_supply_chain_contract.py` holds each rule as a row with planted violations.

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

Dependabot's version updates for Python, npm and actions wait a week after a release before
proposing it (`cooldown: default-days: 7`), so a broken or compromised release has time to be
noticed and pulled first. The wait never delays a security update. The `docker` block keeps
Dependabot's three-day default, because a base-image bump is how the images' OS security fixes
arrive, and images have no separate security-update path.

- **Actions:** Dependabot's `github-actions` ecosystem, weekly. Its PR moves the SHA and the
  comment together.
- **Python dependencies:** Dependabot's `pip` ecosystem, weekly, in one `python-deps` group. A
  PR that moves a `[project]` constraint leaves `uv.lock` stale and fails `uv sync --locked`, so
  re-lock it on the branch (`uv lock`, with the uv CI pins) or close it. Each upper bound
  `pyproject.toml` holds on purpose has an ignore at its own major, and
  `tests/test_dependency_bounds.py` holds the two lists equal. The `uv` ecosystem would rewrite
  the lock with the constraint, but Dependabot's updater runs a uv older than
  `[tool.uv] required-version`, which refuses it.
- **The build backend:** `[build-system] requires` pins setuptools exactly, equal to the
  setuptools `uv.lock` resolves (`tests/test_verify_wheel_contract.py`). Dependabot ignores it,
  since it would move the pin without the lock. Bump both in one commit: edit the pin, then
  `uv lock --upgrade-package setuptools` with the uv CI pins.
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
| `dco.yml` | `dco` | `pull-requests: write` | the commit-provenance remediation comment |
| `pr-feedback.yml` | `lint-help` | `pull-requests: write` | the lint remediation comment |
| `release.yml` | `pypi`, `pypi-client` | `id-token: write` | Trusted Publishing to PyPI |
| `release.yml` | `images` | `packages: write` | pushing the two images to GHCR |
| `release.yml` | `notes` | `contents: write` | creating the GitHub Release |
| `release.yml` | `attest` | `id-token: write`, `attestations: write` | build-provenance attestations |
| `breadth.yml` | `report` | `issues: write` | one issue per red nightly leg |

### The dependency audit

`tools/ci/audit.py` audits the graphs core ships, read from its locks and never from a fresh
resolution, so a lock pinned to an older vulnerable release is reported even when a fresh
resolution would be clean:

- **Python:** `uv export --locked` once per shipped graph, with the extras its artifact
  installs (`PYTHON_GRAPHS`: the wheel, the gateway image's `uv export` in
  `deploy/docker/Dockerfile.backend`, the desktop bundle's `uv sync` in `release.yml`),
  audited by `pip-audit --no-deps --disable-pip`. `tests/test_ci_audit.py` reads each graph's
  extras from the file that installs them.
- **npm:** `npm audit --package-lock-only` on the root `package-lock.json`, every workspace
  and build tool included, since build tools run in CI and shape the artifacts.

It fails on a tool that did not run (no report, or one that cannot be read), on a high or
critical finding no live waiver covers, and on a waiver that is expired, more than 30 days
out, missing a field, or matching no finding. pip-audit reports no severity, so a Python
finding gates as a high one would. Every other finding is listed in the job summary. It runs
as `ci.yml`'s `audit` job, gated on `tree-check` like every job a tested tree skips, and as
breadth's nightly `audit` leg, whose red opens the leg's issue. A new advisory on a dependency
no pull request touched therefore stops merging until it is fixed or waived. The release half,
refusing an unwaived critical, is #3911's.

Waivers are `[[waiver]]` entries in `tools/ci/audit-baseline.toml`, in the form of the
exceptions below. Not audited here: the apps' and the website's locks, which their own
repositories audit, and the images' OS packages, which need a scanner of their own.

### Nothing throws an exit status away unseen

The contract row `nothing-throws-an-exit-status-away-unseen` (`tests/ci_masking.py`) finds
every `|| true`, `|| :` and `continue-on-error` in a workflow, a Dockerfile or a script under
`scripts/`, and requires each to be named in its list with a reason. An entry that matches
nothing fails, and a tolerated pytest step must have its outcome read back by a later step of
its job. It reads the spellings the tree uses; `set +e`, `|| echo` and the like are review's
to catch.

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
