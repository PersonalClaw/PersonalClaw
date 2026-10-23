// The lint remediation relay that `.github/workflows/pr-feedback.yml` runs after CI.
//
// That workflow runs on `workflow_run` from the default branch with a token that can comment,
// and it reads an artifact the candidate's own CI run made. So everything in the artifact is
// untrusted data (docs/maintainers/ci.md, "Trust boundary"):
//
// - The pull request comes from the API: the one open pull request whose head is the run's
//   head commit, from the run's head repository. The artifact's `pr.txt` is only cross-checked.
//   When it names another pull request, is missing, or the head has moved on, nothing is posted.
// - The violations text is fenced here, with a fence longer than any backtick run inside it, so
//   text that closes the `<details>` block or adds a link shows as text.
// - Only a comment this workflow's own bot wrote is ever updated.
//
// It is a module so that `scripts/ci_pr_feedback.test.mjs` can run it against a stubbed API.

import { existsSync } from "node:fs";
import { open } from "node:fs/promises";
import path from "node:path";

export const MARKER = "<!-- lint-help -->";
export const BOT = "github-actions[bot]";
// GitHub refuses a comment over 65536 characters. The rest of the body is under 1000.
export const MAX_VIOLATIONS = 60000;
// The most of an artifact file ever read: its uploader keeps 60000 bytes of a step's output
// (tools/ci/run.py, OUTPUT_LIMIT), and a larger file is read only this far, never whole.
export const MAX_FILE_BYTES = 256 * 1024;
// A pull request's number, read only this far.
const MAX_PR_BYTES = 64;

/** The first `limit` bytes of `file`, as text: an artifact's file is never read whole. */
export async function readCapped(file, limit) {
  const handle = await open(file, "r");
  try {
    const buffer = Buffer.alloc(limit);
    const { bytesRead } = await handle.read(buffer, 0, limit, 0);
    return buffer.subarray(0, bytesRead).toString("utf8");
  } finally {
    await handle.close();
  }
}

/** The text inside a code fence no backtick run in it can close. */
export function fence(text) {
  const runs = text.match(/`+/g) ?? [];
  const longest = Math.max(2, ...runs.map((run) => run.length));
  const bar = "`".repeat(longest + 1);
  return `${bar}text\n${text}\n${bar}`;
}

export function commentBody(violations) {
  let shown = violations;
  if (shown.length > MAX_VIOLATIONS) {
    shown = `${shown.slice(0, MAX_VIOLATIONS)}\n… cut here: the full output is in the lint job's log`;
  }
  return [
    MARKER,
    "### `lint` is failing",
    "",
    "Most of this is auto-fixable. From the repo root:",
    "```bash",
    "source .venv/bin/activate   # or: uv sync --extra dev",
    "make format                 # black + isort, rewrites the files",
    "make lint                   # must print no errors before you push",
    "```",
    "Commit the reformatting and push. `make format` handles the `black` and",
    "`isort` sections below. `flake8` and `mypy` findings (a too-long line, an",
    "unused import, a type error) need an edit.",
    "",
    "Run `npm install` once in your clone and the `pre-commit` hook formats staged",
    "Python at commit time, so this stops happening.",
    "",
    "<details><summary>What CI saw</summary>",
    "",
    fence(shown),
    "",
    "</details>",
  ].join("\n");
}

/** The one open pull request whose head is the run's head commit, or null. */
export async function pullRequestOf({ github, owner, repo, run }) {
  const headSha = run?.head_sha;
  const headRepo = run?.head_repository?.full_name;
  if (!headSha || !headRepo) return null;
  const pulls = await github.paginate(github.rest.pulls.list, {
    owner,
    repo,
    state: "open",
    per_page: 100,
  });
  const matches = pulls.filter(
    (pull) => pull?.head?.sha === headSha && pull?.head?.repo?.full_name === headRepo,
  );
  return matches.length === 1 ? matches[0].number : null;
}

/**
 * Post or update the remediation comment. Returns what it did, for the log and the tests:
 * `{ posted: <number> }`, or `{ posted: null, reason }`.
 */
export async function relay({ github, context, core, dir }) {
  const skip = (reason) => {
    core.info(`nothing posted: ${reason}`);
    return { posted: null, reason };
  };
  const prFile = path.join(dir, "pr.txt");
  const violationsFile = path.join(dir, "violations.md");
  if (!existsSync(prFile) || !existsSync(violationsFile)) {
    return skip("this run left no lint feedback");
  }
  const violations = (await readCapped(violationsFile, MAX_FILE_BYTES)).trim();
  if (!violations) return skip("the violations text is empty");

  const { owner, repo } = context.repo;
  const number = await pullRequestOf({ github, owner, repo, run: context.payload.workflow_run });
  if (number === null) {
    return skip("no single open pull request has this run's head commit");
  }
  const claimed = (await readCapped(prFile, MAX_PR_BYTES)).trim();
  if (claimed !== String(number)) {
    core.warning(
      `the artifact names pull request ${JSON.stringify(claimed.slice(0, 40))}, ` +
        `but this run's head is pull request #${number}`,
    );
    return skip("the artifact names another pull request");
  }

  const body = commentBody(violations);
  const comments = await github.paginate(github.rest.issues.listComments, {
    owner,
    repo,
    issue_number: number,
  });
  const existing = comments.find(
    (comment) => comment?.user?.login === BOT && comment?.body?.includes(MARKER),
  );
  if (existing) {
    await github.rest.issues.updateComment({ owner, repo, comment_id: existing.id, body });
  } else {
    await github.rest.issues.createComment({ owner, repo, issue_number: number, body });
  }
  return { posted: number };
}
