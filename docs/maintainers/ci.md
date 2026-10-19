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
the `Makefile` for a `make` target or a script under `scripts/`. Until the workflows run the
catalogue's commands, a workflow step is a command. Files an entry point loads are listed by
hand in `REVIEWED_HELPERS` in `tools/ci/trust.py`; add a helper there when an entry point starts
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
no controller, because a local result never stands in for GitHub's. `trust.receipt_from` is the
format half of that check, with no git: the merge verdict's verifier uses it, and checks the
head's tree from the API.

### The merge verdict's record

The merge controller posts its verdict as a `merge-verdict` check run on the pull request's head,
with its GitHub App's token, and that check run is the record (#3910). Its output has a title and
a summary for people, and the receipt as canonical JSON in one fenced block
(`tools/ci/merge_verdict.py`). Only the App that posted a check run can change it, and it stays
with the commit. Neither an artifact (it expires, and any job can upload one under the same name)
nor a commit status (no room for the receipt) holds it. The main commit a squash lands is not in
it: the push job will record that in its own record, beside this one, when #3908 checks rule 4.

`python -m tools.ci verify-check-run <sha> --repo OWNER/NAME [--external NAME=SHA ...]` says
whether the verdict on a head counts: exit 0 accepted (it prints the receipt), 1 refused, 2 an
answer it could not read. `--help` exits 0 and a usage error 2, as argparse's do, so acceptance
is exit 0 with the `accepted:` line. Every rule fails closed:

- **Which App is believed:** the one `main`'s required-status-check rule names for
  `merge-verdict`. No such rule, a rule naming no App, two Apps, or GitHub Actions' own App
  refuses every verdict, so a candidate's own job named `merge-verdict` satisfies nothing. Until
  that ruleset exists, every verdict is refused.
- **Newest wins:** every `merge-verdict` check run on the head is read, page by page against the
  API's count, which must hold from page to page. The newest of that App's runs (the highest id,
  whatever order the API lists them in) decides, so a later pending or red run takes back an
  older green. Only a `completed` run concluded `success` is accepted; another App's runs
  neither count nor hide the controller's.
- **The identity:** exactly one receipt, with no key given twice, naming this head; the head's
  tree as the API reads it (a squash-merged head is not on `main`, so local git cannot); a
  controller revision on `main`; and every external revision the caller expects.

Nothing posts the record yet: the controller workflow and its App come later in #3910.
`tests/test_ci_merge_verdict.py` holds each rule on planted check runs.

### The merge controller's decision

`tools/ci/merge_decision.py` decides what the controller will post: green, red or pending, with
a reason, and for a green the receipt, written in the record's one format. It is a library only;
no workflow calls it yet.

- **Which run counts:** `trust.compare` of the default branch's commit, `main`'s tip, and the
  head. A comparison whose base is not `main`'s tip is red: a base off `main` never picks which
  run counts, and nor does an older commit on `main`, against which a candidate that puts back a
  definition `main` has changed since shows no change. When no definition changed, the
  candidate's own run counts. When any did, that run is advisory and only a run of the default
  branch's definitions on the candidate's tree counts. So a job kept by name but changed to always
  pass, an edited `tools/ci/verdict.py` or an emptied `Makefile` target is judged by the default
  branch's copy.
- **The planned set** is an input. Until the planner lands, the caller reads it with
  `planned_jobs`: the merge tier of `tools/ci/catalogue.toml` at `main`'s tip, read from git,
  never from an older commit on `main`, the candidate's files or a worktree.
- **Red:** a planned job that is missing, cancelled, skipped or red, even when the candidate's own
  workflow dropped it (results are read with `verdict.from_needs` and judged with
  `verdict.decide`); results or a plan that cannot be read; a run naming the head with another
  tree, whichever run counted; a green whose receipt would lack its toolchain or evidence.
- **Pending:** no counted run yet, or one of an older head, of other definitions, or against
  other partner heads than the current ones, so a partner head that moves takes a green back.
  A red or pending output carries no receipt, so `verify-check-run` never accepts it.
- **A pull request's reading:** it judges with `verdict.decide`, never `decide_main_push`, so a
  cancelled planned job stays red as cancelled and is never read as superseded. A red on an
  earlier attempt is not in `needs`; reading it from the API is the controller workflow's job.

### Reachability

`trust.require_on_main` refuses a commit that is not an ancestor of `main`. Only a full commit
id is accepted, and a shallow clone refuses, because it cannot show ancestry.

### The rows that hold it

`tests/rails/test_ci_contract.py` holds five workflow rules, each with a planted violation per form:

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
- No workflow that runs candidate code posts `merge-verdict`, the one check the default branch's
  controller posts: no job whose id or name is `merge-verdict` (in any case, a matrix-built name
  included), and no step, action input, key or workflow-level value that names it. A job name the
  row cannot read (a matrix that is an expression, a matrix value that is one, any other
  expression) is refused, since it could be `merge-verdict`. A trusted workflow, or a push to
  `main`, may post it; that only the controller does is #3910's to hold once its workflow exists.
- Every concurrency group a candidate's run joins, the workflow's and each job's (once per literal
  matrix leg), keys on its pull request or its head. The row evaluates each group against the
  runs of several unrelated candidates, each event as GitHub fills it in: two pull requests, two
  forks with the same branch name, pushes to two feature branches, and the same pull requests
  under `pull_request_target`, whose `github.ref` and `github.sha` are main's for every pull
  request. It refuses a group two candidates share, in one workflow or across two, and one that
  two runs for one head do not share: a second run, or a re-run (the same run id and commit, a
  later `run_attempt`). A called workflow is held for its callers, and a `workflow_run` listener
  of a candidate workflow is held to the first rule against each candidate's run. A context the
  row does not model (`vars`, `inputs`, `needs`, an unreadable matrix) is refused, never read as
  empty. A `github` property the row leaves out (`github.workflow`, `github.actor`, ...) is the
  exception: it reads as empty, as GitHub reads a missing property, so the row does not hold a
  group keyed on one. A `workflows:` entry of a listener is read as a glob, and a listener of a
  `pull_request_target` workflow is held like one of a `pull_request` workflow. A push to a feature branch counts as the pull request whose head it is. A push to `main`
  and a scheduled run are not a candidate's, so `main`'s own grouping is not this row's.

Until a validator on the default branch runs the catalogue and this table, these rows run in the
candidate's own suite, so a candidate could delete one.

## The catalogue

`tools/ci/catalogue.toml` lists CI's validation jobs, one `[[job]]` entry each, and
`tools/ci/catalogue.py` reads it into typed jobs. `python -m tools.ci run-job` and `run` run its
jobs on a maintainer's machine (see "Local runs"); `ci.yml` still runs its own `run:` lines. The
contract table's row "every catalogue command is the workflow's" holds each entry to the `ci.yml`
jobs it maps to, in both directions:

- Each step is one of those jobs' `run:` lines, token for token, from the same working directory
  and with the same step `env`. An entry's steps come in `ci.yml`'s order, and none comes twice.
- Every `run:` line of a mapped job is one entry's step. The only lines left out are a closed
  list in the row, each named for what it does: the runner's identity, the installs the
  toolchain names, the shell around a step (its options, and the branch on which base there is,
  with the scan's arm that has none), the fetch of that base, a value set through `GITHUB_ENV`,
  and the lint job's wrapper and feedback relay. A listed line that no mapped job runs any more
  fails the row too, so the list holds only lines that exist. No line is a step of two entries.
- An install is toolchain, never a step: `uv sync`, `npm ci` and the Playwright browser install
  are refused as steps, and each install an entry's toolchain names (`uv-sync`, `npm-ci`,
  `chromium`) is one its `ci.yml` jobs run. Each entry names every install it needs, so one that
  shares a `ci.yml` job with another (`browse-live` with `e2e`) does not lean on the other's.
- The cap, the runner, the Python (the one `setup-uv` sets before the entry's steps, inside the
  setup action when the job calls it there), the environment, the JUnit reports and the shard
  count are the jobs'.
- An entry whose pytest steps name a security-marked module, by its file or by a folder that
  holds one, is `security` posture. The census in `tests/security_census.py` says which modules
  are marked.
- Each entry's tiers, `security`, `live` and `reuse` are the ones the row's table gives it, so
  changing one takes an edit in both places.
- Every `ci.yml` job is an entry's or orchestration (`tree-check`, `changes`, `shard-plan`,
  `test`, `test-macos-gate`).

The map from entry to job lives in that row.

### The setup action

`ci.yml`'s jobs set up their toolchain with one composite action, `.github/actions/setup`. It
runs setup-uv with the one uv release and the exact Python the job names (`python-version`),
and setup-node from `.nvmrc` when the job asks for it (`node: "true"`, with `node-cache`, `npm`
by default). Each job keeps the rest itself:

- the checkout, before the action, because a local action loads only from a checkout;
- the Runner identity step, first, so a failed checkout still logs the image. The runner
  identity row reads it from the job's own steps and fails one inside a local action;
- its installs (`uv sync`, `npm ci`), which the catalogue row holds to the toolchain each entry
  names;
- its JUnit upload, after its tests: a composite action has no post step.

The toolchain rows (Python, uv, the uv Python preference, Node, npm, the runner identity), the
catalogue row and the uv-cache rail read a job's steps with each local action it calls read in
place (`tests/rails/local_actions.py`), as the runner reads them for that call: the call's
inputs fill the action's `${{ inputs.* }}`, its `env:` sits beneath each step's own, and a step
whose `if:` compares an input to a literal, bare or as the whole of one `${{ }}`, is read only
when that call runs it, the comparison ignoring case as GitHub's does. Text outside the
`${{ }}`, such as a block scalar's newline, makes the condition a string GitHub fills in, which
is always true, so that step is held. So is a step behind any other condition the reader cannot
evaluate, and one whose input is itself an expression or a YAML boolean (the rails parse YAML
1.1, which reads `yes` and `on` as `true`, where GitHub hands on the word): a step it cannot
rule out is still held. A local action read from `action.yaml`, or called from inside another,
is read the same way, with the outer call's inputs and `env:`, and a call of one the reader
cannot read fails the row. A setup step inside the action counts at the index of the call. Each
of those rows has a plant inside the action.

A change under `.github/actions/` runs the path-gated jobs, `e2e` and `apps-contract`, as a
change to `ci.yml` does: both set up their toolchain through the action, so it defines them too
(`scripts/ci_touches_frontend.py`, `scripts/ci_touches_sdk.py`). The planner plans the whole
tier for it (`tools/ci/areas.toml`). The rails check that every local action `e2e` or
`apps-contract` calls, and each one that action calls, runs that job's gate, and that every one
a `ci.yml` job calls plans the whole tier.

`python -m tools.ci list` prints every job. `python -m tools.ci explain <tier>` says what a tier
proves and what it does not, and lists each of its jobs' commands. Both read only the catalogue
and import only the standard library, as everything `python -m tools.ci` imports must.

### Fields

Every entry declares:

- `id`;
- `tiers`, from `change`, `merge`, `breadth`, `weekly` and `release`;
- `inputs`, as git pathspecs;
- `external_inputs`: what the job reads that the tree does not pin, such as the apps checkout,
  the advisory feed, the package index or the shard history;
- `toolchain` and `cache`;
- `security`: `security` when its result is security evidence, else `ordinary`. A `security`
  job is never selected out; the job's result is never rerun or reused, and a security-marked
  test is never rerun; an ordinary failed test inside it gets one diagnostic rerun, recorded
  only (see "The diagnostic rerun" under "The quarantine");
- `timeout_min`, 2 or more, since a local run stops a job a minute before its cap.

An entry may also set `platform`, `python`, `live`, `reuse`, `env`, `workers`, `workers_env`,
`mem_gb`, `junit`, `shards`, `needs_node`, `needs_browser`, `full_history`, `after` (the jobs
whose output it uses) and `install_exemptions`, each exemption with its reason. A key the reader
does not know is refused, so a misspelt field fails rather than being ignored.

`workers` is the job's worker count, a whole number or `auto`. Every pytest step's `-n` must equal
it, and a step that passes `-n` needs it. `workers_env` names the variable through which another
tool reads it: `web-unit`'s `VITEST_MAX_WORKERS`, which `ci.yml` sets to the runner's core count.
A job that sets `workers` passes it through a pytest `-n` or a `workers_env`. `workers_env` may
not name a variable the runner owns.

A job has either a `command` or a `script` with its `container`, never both. A command is a list
of steps. Each step is an argv run from the repository root, or from `workdir` under it, with its
own `env` set on top of the job's: the workflow step's own `env:`, such as a flag that turns a
skip into a failure. A value only the runner knows is written as a placeholder: `{runner_temp}`,
`{shard}` or `{base}` (the commit a diff is taken against). An argument or an `env` value
containing `$`, or a placeholder the catalogue does not have, is refused, so no step reads the
environment around the catalogue. An `env` name is a variable name (`[A-Z_][A-Z0-9_]*`), and not
one the runner owns (`tools.ci.run.runner_owns`: `PATH`, `HOME`, `TMPDIR`, the locale, git's
variables and `PERSONALCLAW_HOME`), so no entry the loader reads is refused when it runs. A
`workdir` holds no `$` and no placeholder. A sharded job's steps name `{shard}`; an unsharded
job's do not.

`needs_node` and `needs_browser` agree with the toolchain: `needs_node` exactly when it installs
`node`, `needs_browser` exactly when it installs `chromium`. A pathspec's magic is read as git
reads it, in the long form (`:(exclude)`) or the short one (`:!`, `:^`, `:/`), and a pathspec
that leaves the repository is refused in either form.

### Reuse

`reuse` says when a job's result may stand for a later run with the same inputs:

- `never`, the default when `reuse` is absent;
- `tree`: any run of the same tree and external inputs;
- `ttl:<hours>`: within that many hours, the hours a positive whole number.

A `security` job or a `live` job is never reused, by TTL or by tree: its evidence is about the
run, not the tree. Each external input has a kind (`catalogue.EXTERNAL_INPUT_KINDS`), and the
reader holds a job's reuse to it:

- `revision` (`apps-partner`): its commit is part of the result's identity, so tree reuse is
  allowed.
- `plan-only` (`shard-history`): it decides which shard runs a file, never what is tested, so
  tree reuse is allowed.
- `unpinned` (`package-index`, `browser-download`): what it fetches can change while the tree
  does not, so a job that reads one is never reused by tree. A TTL is allowed.
- `live` (`advisory-feed`): it changes with the time of the run and not with the tree, so the
  reader refuses a job that reads it without `live = true`. `catalogue.parse_reuse` is the one parser of these values; any other reader
imports it.

A local run's reuse of a recorded pass is under [Verdict reuse](#verdict-reuse).

### The tiers

- **merge:** what a pull request into `main` must pass. Its security evidence is the suite's own
  run of the security-marked modules, `python-suite` and `python-suite-macos`. The catalogue is
  refused if this tier lacks the `rails` job, lacks either suite, holds either as anything but
  `security`, or runs either off its own platform: `python-suite` runs on an `ubuntu-` runner
  and `python-suite-macos` on a `macos-` one. So no other security job stands in for the suite's
  run, and neither platform's run is lost. `[tier.merge] jobs` lists the tier's jobs, checked
  both ways: every job whose tiers claim `merge` is in the list, and the list names only such
  jobs, so dropping a job from the merge tier takes two edits. A catalogue without the list is
  refused.
- **change:** the fast jobs, run before a commit.
- **breadth:** the checks run on `main` that are too slow or too wide for every pull request,
  among them the adversarial security corpus.
- **weekly** and **release:** reserved for the long checks and for what a release is cut from.
  No job is in them yet.

`explain` prints, for each tier, what it proves and what it does not.

### Adding a job

1. Add a `[[job]]` entry with every required field, and its commands copied from the
   workflow's `run:` lines.
2. Add its id, its `ci.yml` jobs, its tiers and its posture to the table in the row "every
   catalogue command is the workflow's". Any `run:` line of its `ci.yml` steps that validates
   nothing goes in that row's list of such lines, under what it does.
3. Run that row and `python -m tools.ci explain <tier>`.

A workflow command changed without its entry, or an entry changed without its workflow, fails
the row.

### Planning a run

`tools/ci/plan.py` (`python -m tools.ci plan <tier>`, changed paths on stdin or `--paths`, `-z`
for NUL-separated) turns a diff into the jobs to run and prints them as JSON: `tier`, `full`,
`jobs`, `reasons` (why each job is in), `full_reasons` and `rails_floor`. The area map is
generated from the catalogue's declared inputs; `tools/ci/areas.toml` holds only the explicit
overrides (`--areas`, beside the catalogue by default):

- **A declared input schedules its job.** A changed path a job's `inputs` reach schedules that
  job. So does a path one of its steps runs: a script or folder a step names, and a sibling
  script that script imports.
- **Only an input names a path.** A path is known when an input other than the whole tree
  names it: an include selects it, or an exclude leaves it out by name. A step or an override
  schedules a job but makes no path known.
- **An `[[override]]` only adds.** Each names its `paths`, the catalogue `jobs` it adds (or
  `whole_tier = true`) and a `reason`. The loader refuses an override with no reason or one
  naming a job the catalogue lacks. Since it makes no path known, an override never takes a
  job out of a plan: a path no input names still plans the whole tier, the override's jobs and
  reason in it. The overrides today are the workflows (`.github/workflows/`), the local actions
  their jobs call (`.github/actions/`), `tools/ci/`, the locks and manifests and the image
  smoke's own inputs (all whole-tier), the installer (`static`) and the SDK snapshot script
  (`apps-contract`).
- **Always planned:** the `rails` job, every `security` job, every `live` job, and each job an
  `after` of a planned one names.
- **Fail-safe: the whole tier.** An empty diff, a path no input but the whole tree names, a
  path that is not a plain path inside the repository (empty, absolute, `..`, not in its
  normal form, a newline, a carriage return or a NUL in it, not a string), a list that cannot
  be read, `--full`, and any error inside the planner. A pathspec that matches every path
  (`:(glob)**`, `:/`, `*`) still schedules its job but names no path, so a path only such
  inputs reach runs the whole tier: a forgotten path runs everything, never nothing.
- **Pathspecs are read as git reads them.** The loader takes a pathspec only in git's normal
  form (no `.` or empty part, since git reads `.` as the tree but `:(top).` as nothing) and
  refuses a wildcard the planner would read otherwise than git: a backslash, a `[` it never
  closes, a `/` or a `[:name:]` in a class, `literal` with `glob`. A test holds the matcher to
  `git ls-files` over the forms it accepts.
- **The rails floor** is the median minutes of `ci.yml`'s passing `rails` job over a folder of
  telemetry history (`--history`). A head it cannot measure from (no job list, minutes that
  are not a finite positive number) is skipped; with none it reads
  `{"minutes": null, "runs": 0}`, never a guessed number.
- **Exit codes:** 0 with a plan; 2, printing nothing, when the tier has no job in the catalogue
  (`weekly` and `release` have none yet) or the catalogue or its overrides cannot be read, so a
  caller plans everything.

`run --changed` plans the same way, from `git diff --name-only --no-renames -z` against the
base, so a move names both the path it left and the path it reached.

Every job but `e2e` and `apps-contract` declares the whole tree today. `e2e`'s inputs are the
whole tree less the paths that cannot reach the browser run, each exclusion carrying its reason
in the catalogue. Narrowing another job is a change of its own: its inputs, the reason, and a
planted path in `tests/rails/test_ci_plan_tree.py`, which also plants a path per override and
finds the override's reason in its plan.

To add a rule, prefer a declared input of the job it affects; use an override only for a path
that is no job's input but changes a job's result, and give it a reason that says how.

## The verdict

`tools/ci/verdict.py` is the one function that turns a plan's job results into green or red. It
is green only when the plan is not empty, every planned job has a result, every result for it on
every attempt is `success`, and every result names the candidate's head and partner revisions.
Each of these reads red, with a reason naming the job:

- a planned job with no result, or one that was cancelled, skipped or failed;
- a result word GitHub does not use;
- a result with no head, or for an older head, or for an older head of a partner repository;
- a red on an earlier attempt, even when a rerun went green;
- an empty plan;
- a job outside the plan that failed, was cancelled or gave a result word GitHub does not use,
  on any attempt. One outside the plan that was skipped or passed changes nothing.

It reads GitHub's `toJSON(needs)` and refuses any other shape: each job is named once, holding
exactly `result` and `outputs`. JSON keeps only the last of a repeated key, so a job's failure
followed by its success would otherwise read green. `needs` holds each job's latest attempt
only, so the command cannot see a red on an earlier attempt; the controller will read attempts
from the API. The head it stamps on a pull request's results is the pull request's head, never
`github.sha`, which is the test-merge commit.

`python -m tools.ci verdict`, or `python3 -I tools/ci/verdict.py` from a trusted job's checkout of
the default branch, exits 0 green, 1 red and 2 on input it cannot read. Any usage error is input
it cannot read: there is no `-h`, because a help page would exit 0 having judged nothing, and an
option given twice is refused rather than the last one winning. It appends its reasons, or why
it could not read its input, to the step summary.

On a push to `main` only, `decide_main_push` takes one more input: the planned jobs
`tools/ci/superseded.py` proved were replaced by a newer push (see "A burst of merges" below).
Such a cancelled job reads `superseded` instead of `cancelled`, and is still red. `decide`, the
reading of every candidate, takes no such input.

Nothing calls it yet. The results stage of each candidate workflow and the default branch's
controller will, so a candidate and its judge read the same results the same way.
`tests/test_ci_verdict.py` holds every red to a row, every refusal to a row naming its reason,
and a green plan to a control.

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
a later one replaced it. The suite's shards are the one exception (see "A burst of merges"
below): a commit whose shard was replaced is never read as checked. It reads superseded when
the API proves it, and unproven otherwise, red either way.
`ci.yml` runs on a push to `main` only: off `main` its one run is the
pull request's, and a newer push to the pull request's branch cancels the older run. A branch
with no pull request gets no `ci.yml` run, and GitHub starts no `pull_request` run while a pull
request has a merge conflict, so such a branch gets none until the conflict is resolved. It
cannot merge in that state either.

### A burst of merges

The suite is the exception. On `main`, `test-shard` and `test-macos` each join one group per
shard that every push's first attempt shares, with `cancel-in-progress: false`. A running shard
is never cancelled. While it runs, the next shard to join waits, and a later request's waiting
shard replaces the one before it. So a burst of merges usually runs the suite on its newest
commit, not once per commit. `tree-check`, the always-run jobs and every other job keep the
per-commit group.

The group replaces by order of joining, not by commit. An older push whose `tree-check` or
`shard-plan` runs slower can queue its shard after the newest commit's and evict it. Then the
newest commit's shard is the one cancelled. No later descendant replaced it, so its gate reads
`unproven` and fails closed, and that commit stays untested until someone re-runs it. A re-run
keys on its own commit, so it cannot be evicted again.

A commit whose shard was replaced is not verified on its own, and its gate (`test` for
`test-shard`, `test-macos-gate` for `test-macos`) stays red, with the reason
`superseded by <newer commit>` when the API proves it and `unproven` otherwise. A skipped check
would read as passing wherever a check is required, so the gate fails. The badge follows the
newest run. No release gate reads CI results yet: until #3911 lands, the
[release runbook](release-runbook.md#tagging) tags only a commit whose own main-push run was
green, and #3911 must enforce that.

The gate names that red only when the previous `main` commit's copy of
`tools/ci/superseded.py`, fetched as `tree-check`'s copy is but run under `python3 -I` (unlike
`tree-check`'s), proves it from the API:

- the run is a push to `main`;
- the shard was cancelled before any step ran;
- its check run carries GitHub's replacement annotation, naming exactly that shard's own group;
- a later push run of `ci.yml` on `main`, for a commit that descends from this one, has a job of
  the same name that was not skipped. The earliest such run is the one named;
- every cancelled shard of the run was replaced by one and the same commit.

Any other cancellation, or an answer the check cannot read, is `unproven`, and red as before.
On a pull request the word never exists. `verdict.decide_main_push` names it only on a push to
`main`; every candidate's reading, `verdict.decide`, takes no proof, so a pull request's
cancelled job is always `cancelled`. A superseded commit is never green.

The row `main-push-runs-and-is-never-cancelled` holds this: each suite job's group names its
job and shard, is shared by every first-attempt push to `main`, keys a re-run on its own commit,
and never cancels; no other job has a group of its own; and each gate (`test` and
`test-macos-gate`) runs the previous commit's check for its own job's group, names both reds and
never exits 0 on a superseded shard.

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
recorded for its pull request, is not checked: #3908 keeps it open until that record is posted.
It lives in the merge controller's check run (see "The merge verdict's record"). Until then
`GitHub <noreply@github.com>` commits a squash merge, a web-editor commit and a revert made from
a pull request's page alike, and rule 2 passes all three. A push to `main` that starts no run at
all (one made with a workflow's own token, say) is never checked either, because the next run
starts at its own `before`; rule 5 closes only the skip-marker case.

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

- `ci.yml` runs on every push to `main` and on no other push (no `**` or other branch, no
  `branches-ignore`, no bare `push:`, no tag, no negated branch, no path filter) and never
  cancels a `main` run, at the workflow or the job level. The suite's shards are the one
  exception: a running shard is never cancelled, but a newer push's waiting shard replaces an
  older one's, which the gates name as superseded (see "A burst of merges");
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
the shard planner and the weekly report read it.

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
- **The diagnostic rerun:** `rerun_result` is read from the `rerun_result` property `run-job`
  writes on each failed case it reran: `flake` or `repeated failure`, null for a case it did not
  rerun. Any other value, or one on a case that did not fail first, fails the collection.
- **Not yet recorded:** `quarantined` is #3947's, `cache_hits` is #3909's, and `worker` and
  `position` are #3949's. No API reports them, so they are null, never guessed.

`tools/ci/report.py` (`python -m tools.ci report --history <folder>`) reads only that schema
and fails (exit 2) on a file in another schema or a newer version, and on a folder that does
not exist; an empty folder is reported as no history. It prints the same table in a terminal
and to the job summary, and `--json` writes the figures.

It reads 90 days of history, about 860 files of about 49 MB each, without holding any of them:
first every file's run line, to find the window, then the test lines of the runs inside it, one
run at a time and one line at a time. A line over 1 MiB, or a file over 256 MiB, fails the
report; a file outside the window is read for its run line alone. Each run's outcomes are folded
into each test's figures before the next run is read, and a test's seconds are kept as each
distinct value with the number of runs that took it, so the percentiles are exact and what a
test takes grows with how many distinct seconds it records, never with its runs. The reader
that held every record took 1.1 GB for 4 main-sized runs.

The figures:

- **the window:** the 90 days up to the newest run. While less than 90 days exist, it states the
  exact window it read, and `window.mature` stays false, which keeps the 90-day replay and
  maturity checks off;
- **flake rates**, from first results: a first failure that a rerun then passed (a later
  attempt that passed, or a `rerun_result` of `flake`; a `repeated failure` is not one), over
  the test's runs, rated only from 10 runs up. Two records of one test in one attempt count once.
  A skip or an expected failure is not a run;
- **durations:** each test's p50 and p99, the 20 slowest, and the week-over-week trend;
- **the quarantine:** every entry of `tests/quarantine.toml` (`--quarantine` reads another list),
  with its issue, its assignee, its expiry and the days left, soonest first. On its date an
  entry has expired, as the suite reads it, and the report flags it. A missing list is an empty
  quarantine; an entry not in the list's shape fails the report;
- **scheduled runs:** from the newest slots file, each workflow's slots that ran, with the median
  lag, and every missed or pending slot as a line of its own.

`tools/ci/parity.py` (`python -m tools.ci parity --history <history-run-attempt.jsonl>`) reads
one attempt's history, from the `telemetry-history-<run>-<attempt>` artifact, and compares the
test node ids `test-shard` ran (its `junit-test-shard-*` reports) with those `test-macos` ran
(`junit-test-macos-*`). A skipped case counts as run on its side, since it was collected there.
It names every id only one side ran and exits 1 when there is one, and exits 0 when the two sets
are equal. An id one side ran more than once is named but is no difference. It exits 2 on a file
it can't read: missing, empty, not JSON lines, another schema or a newer version, a line over
1 MiB or a whole over 256 MiB, or no report of one side (a push whose tree was already tested
runs no `test-macos`). It reads only the file it is given, and no workflow runs it yet.

## Local runs

`python -m tools.ci run-job <id> [--shard i/N]` runs one catalogue job here, and
`python -m tools.ci run <tier> [--changed] [--fail-fast]` runs every job of a tier
(`tools/ci/jobs.py`). `make check` is `run change --working-tree` and `make gate` is
`run merge`.

- **CI's commands.** A job's toolchain installs run first, as `ci.yml` runs them, then each step,
  word for word. A step's environment is built from nothing: what a GitHub runner gives every
  step (`CI=true`, which the e2e job's Playwright config reads for its JSON report, retries and
  server; never `GITHUB_ACTIONS`, which says the run is on GitHub), then the workflow's
  (`UV_PYTHON_PREFERENCE`), then the job's, then the step's; of the caller's it gets only
  `PATH`. `{runner_temp}` is the job's own temporary folder, `{shard}` the shard asked for, and
  `{base}` `--base` or the merge base with `origin/main`.
- **Caches, managed Pythons and the project venv** go under `.local/run-job/cache`, never the
  caller's own: uv's cache and Pythons, the venv `uv sync` installs, npm's cache and the
  Playwright browsers. `npm ci` reinstalls the checkout's own `node_modules`, as it does on CI,
  and `uv venv` replaces a venv an earlier run left in the checkout (`UV_VENV_CLEAR`), where
  CI's checkout is new each run.
- **The job's time** is its `timeout_min` less a minute, shared by every step, so the loader
  refuses a `timeout_min` under 2. A pytest step still running when it runs out prints
  `possible xdist hang` and lists its group's live processes before the group is stopped.
- **Its reports.** Each JUnit report the job declares must exist and be newer than the job's
  start, or the job is red: a pytest that died before writing one is red, not green.
- **One record per job, or per shard.** `verdict.job_record` writes its key, result, head,
  partner revisions, attempt, the diagnostic rerun (below), which quarantined tests
  failed (read from the reports' `quarantine_issue` property), where a shard plan came from, a
  local worker count, and each step's argv, exit and seconds. The partner revisions are those
  of the partners the job's own external inputs name. `verdict.from_job_records` reads records
  back and refuses any other shape, NaN and Infinity among them. A checkout that was not clean
  at one head before and after the job leaves no record, since the head it would name is not
  the tree it ran; so does a partner checkout the job reads that was not at one head before and
  after it. The partner checkout (`.ci-apps`) and the e2e job's reports (`web/e2e-report.json`,
  `web/blob-report/`) are git-ignored, so they leave the checkout clean.
- **What runs here.** A job for the other platform family, in a container, or needing a
  toolchain or a partner checkout (`.ci-apps`) this machine lacks is named and not run. A
  sharded job run here plans its shards with equal weights, since `shards plan` reads CI's
  telemetry through the API; its record says `plan: offline`, and on GitHub that path is never
  taken. `--workers N` replaces the catalogue's count, is written in the record, and is refused
  when `GITHUB_ACTIONS` is set.
- **`run`** goes in catalogue order, each job after the jobs its `after` names, every shard of
  each, and judges each job's records with `verdict.decide` against the head and the partner
  revisions that job reads. It keeps going after a red unless `--fail-fast` is given, and runs
  nothing more after an interrupted job. `--changed` runs the jobs `tools/ci/plan.py` plans for
  the diff from the base (see "Planning a run"). `run-job` always runs the job it is given.
- **Exit codes:** 0 every planned job ran here and passed; 1 a job ran and failed, git asked
  for a credential, or a job made the real home's `.personalclaw`; 2 a job could not run here,
  was interrupted or left no record, or the input could not be read: an unknown job or tier, a
  tier with no job (`weekly` and `release` have none yet), an option given twice or not spelled
  in full (no prefix stands in for an option), or a job env naming a variable the runner owns,
  which the loader refuses too. An account whose entry in the user database names no absolute
  home runs no job here either, and exits 2 before any step.
- **`--working-tree`** (both verbs) runs on the checkout as it stands, uncommitted changes
  included, so a check before a commit can pass. The steps, their environment, the time limit
  and the JUnit check are the same; no record is written, since the steps ran on no commit, and
  the exit is the steps' result: 0 when every planned job ran here and passed, 1 on a red, 2
  only for a job that cannot run here or an interrupted one. With `--changed`, the plan reads
  uncommitted changes and untracked files git does not ignore. It is refused on GitHub, where
  the checkout is the commit under test. A working-tree run is no evidence for a commit: `run`
  judges no record, and says so.
- **Off its platform, in a working-tree run only.** A job of another platform family (an
  `ubuntu-` job on a Mac) runs here when every toolchain program it needs is on `PATH`, so a
  check before a commit means something on either platform. Its exit follows its steps, and the
  summary names each job that ran off its platform with the platform CI runs it on, and says a
  local result is never CI's evidence. A missing toolchain program, a container or a missing
  partner checkout is still "cannot run here" (exit 2). The committed-tree mode, which records,
  still refuses a job of another platform family, so an off-platform result never becomes a
  record.
- **Not yet:** nothing reuses a recorded pass, so `run` runs every job; and nothing reads these
  records on GitHub.

Each step runs through `tools/ci/run.py`, which runs one command from an empty environment
(#3920):

- **A folder of its own.** Each job gets a new scratch folder, which only its owner can open, with
  its own home and temporary folder. A folder that already exists is refused, so no earlier job's
  home or record is reused.
- **An environment built from nothing.** The job sees only its `PATH`, the scratch `HOME` and
  `TMPDIR`, a fixed locale (`LANG` and `LC_ALL` are `C.UTF-8`), git's variables, and what the job
  declares. No token, ssh agent or `GIT_DIR` of the caller's reaches it. A declared variable that
  would replace one the runner sets, the locale among them, or any `GIT_CONFIG*` one, is refused.
- **The default PersonalClaw home.** Nothing sets `PERSONALCLAW_HOME`, as on a CI runner, so the
  job's PersonalClaw home is the default one, `~/.personalclaw` under its scratch `HOME`. A job
  may not declare `PERSONALCLAW_HOME` either, so no catalogue entry points a job at a home
  outside its scratch folder.
- **A git of its own.** git reads no system file, and its global file is the scratch one. An empty
  `credential.helper` on git's command line clears every helper a file names, so no helper of the
  caller's or of any file runs. The helper git does run first is a tripwire. It answers nothing,
  records only what git asked for (the action, protocol and host), never the rest of the request,
  and each request it records fails the run, even when a helper the job names on its own git
  command line answers after it. A job that sets git's command-line configuration of its own
  replaces the tripwire: inside a pytest job the suite's conftest does that, and there the suite's
  git guard covers git instead.
- **Exit codes.** 0 is OK. 1 is red: a non-zero exit, a timeout, a credential git asked for, or a
  real home's `.personalclaw` the job made; the last two stay red even when the run is
  interrupted. 2 is partial: a SIGINT or SIGTERM the runner gets from before the job starts until
  its group is gone, and the run is never 0. The runner takes those signals on the main thread
  only, so it refuses to start a job on any other.
- **Stopping a job.** A timeout or a signal sends SIGTERM to the job's whole process group, then
  SIGKILL once every process of the group has exited or 5 seconds have passed, whichever is
  first. A signal the runner gets meanwhile, other than the one that began the stop, cuts the 5
  seconds short. When a job ends on its own, whatever is left of its group gets SIGKILL, so no
  process of the group outlives the run.
- **The real home.** The runner and the suite's real-home and git guards take the real home from
  the account's entry in the user database, never from `$HOME`, so a job's scratch `HOME` can't
  retarget them: a test inside a job that writes the real `~/.personalclaw` by its path is still
  refused. Where `$HOME` names that same folder by another path (a link, another letter case, a
  firmlink), the guards keep `$HOME`'s spelling, and watch what they watched when they took it
  from `$HOME`. A run also fails when the real home's `.personalclaw` was absent as the job
  started and is there as it ends. The runner only asks whether that folder exists; it never
  reads, lists or changes the real home, which may be a live PersonalClaw's. An account with no
  entry in the user database, or whose entry names no absolute path, starts no job.
- **Not guarded yet:** what a job does under a real home's `.personalclaw` that was already there,
  or one it made and removed again; the rest of the real home, the model caches the suite's
  model-root guard forbids among them, since that guard still takes its home from `$HOME`; the
  container or worktree a job runs in; and scheduling. A local result never stands in for
  GitHub's, and no workflow reads what it writes.

## Verdict reuse

`tools/ci/verdict_store.py` holds the rules a local run will reuse a job's result by (#3919).
Nothing consults it until `python -m tools.ci run` and `run-job` do.

- **The key** is a sha256 over every input a job's result depends on: the job id, its entry's
  text, the catalogue's version, the exact argv, a digest of the selected tests or shards, the
  commit's tree, each partner repository's commit, each lock file's digest, the toolchain, the OS
  and architecture, the runner or container, the advisory policy, any other declared input, and
  the host capabilities. A change to any one is another key. The OS and architecture must be
  named, so no two platforms share a key; an empty runner means the bare host. An id or digest
  that is not exactly one (the wrong length, uppercase, a newline after it) is refused.
- **The class** is the entry's, read by `catalogue.parse_reuse`. A job whose `security` is not
  `ordinary`, or that is live (`live = true`, or reading a live feed), is read as `never`,
  whatever it declares. The catalogue has no canary marker, so a canary job is `never` only by
  its own entry's class.
- **The store** is `ci-verdicts/` in the clone's common git folder, shared by every worktree of
  the clone; no other clone's store is read. Its git runs with no `GIT_*` variable, so a
  `GIT_DIR`, index or configuration a hook exported neither moves the store nor changes what git
  reports. git still reads the user's own global and system configuration. A local entry never
  reaches GitHub.
- **A pass is recorded** only when it needed no rerun and its quarantined-failure list is present
  and empty, from a checkout that was clean when the run began and is still clean, on the same
  head and tree, when it is recorded, and only when the entry would be read back. Clean means
  `git status --porcelain --untracked-files=all` prints nothing, so an untracked file counts. An
  entry is written to a temporary file, flushed to disk, and then renamed. One that is torn,
  malformed, of another version, or whose inputs do not hash to its name is a miss, never an
  error: the job runs.
- **A dirty run** neither records nor replays: every job runs, and its receipt reads `dirty: true`.
  The receipt's `dirty` and the replay rule come from one reading of the checkout, taken when the
  run began. A job whose inputs name another tree than the checkout's runs.
- **The host's capabilities** (`tools/ci/capabilities.py`) are the key's `capabilities`: each
  tool the suite probes, as `absent` or its resolved path and the sha256 of its bytes; the
  browser the browse proofs would use, found as `tests/browse_chrome.py` finds it, a `~` in its
  roots being the job's home; and each skip switch (`PERSONALCLAW_TEST_CONTAINER_RUNTIME`,
  `PERSONALCLAW_REQUIRE_INSTALL_PROOF`, `PERSONALCLAW_REQUIRE_BROWSE_PROOF`,
  `PERSONALCLAW_REQUIRE_NPM_TREE`, `PERSONALCLAW_TEST_CHROME`) as `unset`, or `set:<value>`, so
  a switch set to the text `unset` is not one left unset. Nothing is started to read them, never
  `--version`. The tools come from the suite itself, never a hand-kept list: every
  `shutil.which` call under `tests/`, read by AST, whatever `PATH` it is given (one a test builds
  may still hold the host's folders), whose tool is a literal, a loop over literals or a constant
  of them, or a helper's parameter every call in its own module gives a literal; every candidate
  a test hands the container runtime's `require`, `available` or `skip_reason`; and the container
  runtime's `PROGRAMS`, which cover its own lookups. A probe the scan cannot name refuses the
  fingerprint, and so does `shutil.which` taken as a value, a helper another module names, and a
  loop over a name bound more than once or over a list or set, so no pass is keyed on a list
  read short; the rail `tests/rails/test_ci_capabilities_tree.py` holds this tree to it.

## The quarantine

The one way a known failure is waived is an entry of `tests/quarantine.toml` (#3947). The rules
live in `tests/quarantine.py`, which `tests/conftest.py` loads in every session:

- **At most 10 entries,** each an `[[entry]]` table with exactly `nodeid`, `issue` (the number
  of the issue that tracks the fix), `assignee` (the GitHub login of who owns it), `expires` (a
  date at most 30 days out, read in UTC) and `reason` (one line).
- **Never a rail or a `security`-marked test.** An entry for a rail (`tests/rails/`) or a
  security test stops the session. The marker is read from the source, on the module, a class or
  the test, and again on each collected item the list names, so a mark a hook adds is refused too.
- **A list that breaks a rule stops the session, and no test runs:** an eleventh entry, an
  entry expired or more than 30 days out, a missing issue, assignee or reason, a reason of more
  than one line, an unknown field or key, a test listed twice, or an id that names no test pytest
  collects (a renamed test takes its entry with it). The source is read before collection; what
  only collection shows (a mark a hook adds, a parametrized case that is gone) is refused then,
  under xdist too, before any test runs; a run of part of a module (`file::test`, `--lf`) judges
  none of its entries by what it left out. Either way the session ends with exit 4 and the
  reason. On an entry's `expires` date it has expired, and CI goes red with no change to the
  repository: renew it with a reason, or fix the test.
- **A quarantined test still runs.** It is marked `xfail(strict=False)` at collection, so its
  failure is recorded as a quarantined failure, in the terminal summary and as the junit
  property `quarantine_issue`, instead of a red, and a pass stays a pass despite
  `xfail_strict = true`. An open issue alone waives nothing.
- **Nothing else waives a failure.** `tests/waiver_guard.py` reads the list on its own, imports
  nothing from `tests/quarantine.py`, and fails every test pytest waived as an expected failure
  that the list does not name: a source `xfail` mark, strict or not, `pytest.xfail()` in a test
  or a fixture, or a quarantine bug that waives more than it lists. A known gap a test pins is
  asserted as it stands (the miss), so closing the gap reds it, as a strict `xfail` did.

The contract row `the-quarantine-is-capped-assigned-and-expiring` holds each rule the source
shows with a planted entry, and three controls that must pass: ten entries, an entry expiring in
exactly 30 days, and one case of a parametrized test. `tests/test_quarantine.py` holds the
session's half: what collection refuses, how a quarantined test ends, that an empty list changes
no test's outcome, and the guard. It is `security`-marked, so no entry may waive the tests that
prove the list's rules. The file's shape is a contract with the weekly CI health report (#3948),
which reads it for the quarantine's status: a field changes in both together.

**The diagnostic rerun.** When a pytest step of `run-job` exits 1 (tests ran and some failed)
and nothing else went wrong, its failed tests are read from the step's own JUnit report, as XML,
and rerun once, alone, in a fresh process (`-n 0` when the step ran workers; a step that names no
worker count ran in one process already, and its pytest may have no xdist), with the step's
per-test `--timeout` and inside the job's remaining time. It classifies only: the job stays red
whatever the rerun shows, since the first result is the result and only an entry here waives a
known failure.

- **Which test a case names:** pytest names a case from its own rootdir, which can be a
  package's (`packages/personalclaw-client-py` has a pytest config of its own), so a case's
  classname is looked up from each folder at or above the paths the step collects, and counts
  only as a `.py` file under one of those paths. A case that names no such file, or two, is not
  rerun, and its record says so. The rerun's own report is read only when every case in it is one
  the step's report failed, named alike.
- **Never rerun:** a rail (`tests/rails/`), and any `security`-marked test, which the rerun
  deselects (`-m "not security"`). A failure there is red at once. A rail starts no process; a
  security-marked test still starts one, which collects its module and deselects it (exit 5),
  so the test itself never runs again.
- **A security-posture job:** the job's result is never rerun or reused, and a security-marked
  test is never rerun; an ordinary failed test inside it gets one diagnostic rerun, recorded
  only. The suite jobs (`python-suite`, `python-suite-macos`) are such jobs, so their ordinary
  failures are classified while the job stays red.
- **Recorded three ways:** each failed test as `flake` (it passed alone) or `repeated failure`
  (it failed again), or null with the reason it was not rerun, in the job record's `rerun` field;
  as a `rerun_result` property on its case in the job's report, which the telemetry collector
  reads into the test line's `rerun_result`; and as a `::warning` line.
- **The step's own outputs:** the files the step left beside its JUnit report (the real-home
  guard's `reports/real-home-guard.txt` among them) are put back after the rerun, and what the
  rerun wrote there moves to the job's scratch folder, so the job's reports describe the job.
- **Not rerun at all:** a passing job; a step that failed for any other reason (a usage error,
  the job's time running out, a signal, the credential tripwire, a program that is not pytest);
  and a step whose report is missing, older than the job or not XML. Their record's `rerun` is
  null. A test stopped by its own per-test `--timeout` is a failed test, and is rerun.

`tests/test_ci_jobs_rerun.py` plants a suite in a temporary repository and runs it through
`run-job`'s step path: a test that fails then passes, one that fails twice, one that times out
twice, and a security-marked one and a rail that fail, each job red; a package with its own
pytest root, whose failure is rerun as its own test and never as its namesake at the root; a step
stopped for its time, a signal or the tripwire, and a report it cannot trust, each with no rerun;
a rerun stopped at the end of the job's time, one the runner is interrupted in, and one whose
report cannot be read; and a session report beside the JUnit that the rerun leaves as the step
wrote it. The weekly report counts the `flake` and not the `repeated failure`.

The web half (Vitest, Playwright's `failOnFlakyTests`) comes later, and with it the CI contract's
row per runner, pytest's included: one retry, in a fresh process, with the same time budget, and
red on a first failure the retry passes. Until then a web job's failure is red with no rerun, and
the pytest half is held by `tests/test_ci_jobs_rerun.py` alone.

## State a test leaves behind

`tests/state_leak_guard.py` (#3949), loaded by `tests/conftest.py` in every process, names the
test that leaves process-global state changed, as itself, instead of the unrelated test on its
worker that turns red later.

- **What it reads.** A non-daemon thread the test started that is still alive after a short
  grace; the working directory; the handlers for `SIGINT`, `SIGTERM`, `SIGUSR1` and `SIGUSR2`; a
  child process the test started that is still running; a socket the test opened that is still
  listening (as the port guard tracks them); and each registry a `_restore_*` fence covers. The
  first five are read when the test's setup begins and again once its teardown has ended, so a
  fixture's own threads and ports, and a `monkeypatch` change, are not the test's. The directory
  and the handlers are given back once read. The environment stays `env_guard`'s.
- **Before the fences.** The fixture `_a_test_leaves_process_state_as_it_found_it` requests every
  fence, so its after-reading of the registries is taken before any fence restores one. Such a
  change is recorded as `fenced` and never fails while its fence stands; the record is what a
  fence's retirement is judged on.
- **Never failing:** a fenced change, and a thread named as a pool names its workers
  (`ThreadPoolExecutor`, asyncio's executor).
- **Report mode** is the default: every leak goes to `reports/state-leaks.json`, with the test's
  real xdist worker and its position in that worker's order, and the guard's own seconds. The
  terminal summary gives the count and the cost per test.
- **`--strict-state`** fails a leaking test in its teardown, with what it left, unless
  `tests/state_leak_allowlist.toml` names it with the issue that tracks its fix. At most 5
  entries, each exactly `nodeid` and `issue`; a list that breaks a rule stops the session.

Not read yet: files a test writes outside its temporary paths. Under xdist every worker writes
to one tree, so a per-test listing would charge one worker's writes to another worker's test;
the real-home guard already fails writes to the real home. The randomized-order leg and the
bisect tool come later in #3949.

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
