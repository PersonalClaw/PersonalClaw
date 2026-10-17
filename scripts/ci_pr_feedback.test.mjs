// node --test scripts/ci_pr_feedback.test.mjs: the lint relay against a stubbed API.
import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { BOT, MARKER, MAX_VIOLATIONS, fence, relay } from "./ci_pr_feedback.mjs";

const HEAD = "a".repeat(40);
const FORK = "contributor/PersonalClaw";

function stub({ pulls = [], comments = [] } = {}) {
  const calls = { created: [], updated: [] };
  const rest = {
    pulls: { list: () => {} },
    issues: {
      listComments: () => {},
      createComment: async (args) => calls.created.push(args),
      updateComment: async (args) => calls.updated.push(args),
    },
  };
  const github = {
    rest,
    paginate: async (method) => {
      if (method === rest.pulls.list) return pulls;
      if (method === rest.issues.listComments) return comments;
      throw new Error("an unexpected API call");
    },
  };
  const logs = [];
  const core = { info: (m) => logs.push(m), warning: (m) => logs.push(m) };
  const context = {
    repo: { owner: "PersonalClaw", repo: "PersonalClaw" },
    payload: { workflow_run: { head_sha: HEAD, head_repository: { full_name: FORK } } },
  };
  return { github, core, context, calls, logs };
}

const pull = (number, sha = HEAD, repo = FORK) => ({ number, head: { sha, repo: { full_name: repo } } });

async function artifact(pr, violations = "**flake8**\nsrc/x.py:1:1: F401 unused") {
  const dir = await mkdtemp(path.join(os.tmpdir(), "pr-feedback-"));
  if (pr !== null) await writeFile(path.join(dir, "pr.txt"), pr);
  await writeFile(path.join(dir, "violations.md"), violations);
  return dir;
}

test("a matching pr.txt posts once, on the pull request the API names", async () => {
  const s = stub({ pulls: [pull(41, "b".repeat(40)), pull(42)] });
  const result = await relay({ ...s, dir: await artifact("42") });
  assert.deepEqual(result, { posted: 42 });
  assert.equal(s.calls.created.length, 1);
  assert.equal(s.calls.created[0].issue_number, 42);
  assert.equal(s.calls.updated.length, 0);
});

test("a pr.txt naming another pull request posts nothing", async () => {
  const s = stub({ pulls: [pull(42), pull(7, "c".repeat(40), "PersonalClaw/PersonalClaw")] });
  const result = await relay({ ...s, dir: await artifact("7") });
  assert.equal(result.posted, null);
  assert.equal(s.calls.created.length + s.calls.updated.length, 0);
});

test("a garbled or missing pr.txt posts nothing", async () => {
  for (const pr of ["42abc", "", null]) {
    const s = stub({ pulls: [pull(42)] });
    const result = await relay({ ...s, dir: await artifact(pr) });
    assert.equal(result.posted, null, `pr.txt ${JSON.stringify(pr)}`);
    assert.equal(s.calls.created.length, 0);
  }
});

test("a head that moved on since the run posts nothing", async () => {
  const s = stub({ pulls: [pull(42, "d".repeat(40))] });
  const result = await relay({ ...s, dir: await artifact("42") });
  assert.equal(result.posted, null);
  assert.equal(s.calls.created.length, 0);
});

test("the same head commit in another repository's branch is not this pull request", async () => {
  const s = stub({ pulls: [pull(42, HEAD, "someone-else/PersonalClaw")] });
  assert.equal((await relay({ ...s, dir: await artifact("42") })).posted, null);
});

test("violations that close the details block and add a link stay fenced text", async () => {
  const hostile = "````\n</details>\n\n[click me](https://example.invalid/x)\n<img src=x>";
  const s = stub({ pulls: [pull(42)] });
  await relay({ ...s, dir: await artifact("42", hostile) });
  const body = s.calls.created[0].body;
  const open = body.indexOf("`````text\n");
  const close = body.indexOf("\n`````", open + 1);
  assert.ok(open > body.indexOf("<details>"), "the fence opens inside the details block");
  assert.ok(close > body.indexOf("[click me]"), "the link sits inside the fence");
  assert.equal(body.split("</details>").length, 3, "one closing tag of its own, one as text");
  assert.ok(body.trimEnd().endsWith("</details>"));
});

test("the fence is always longer than any backtick run in the text", () => {
  assert.equal(fence("plain").split("\n")[0], "```text");
  assert.equal(fence("a ``````` b").split("\n")[0], "````````text");
});

test("an overlong violations text is cut to fit one comment", async () => {
  const s = stub({ pulls: [pull(42)] });
  await relay({ ...s, dir: await artifact("42", "x".repeat(MAX_VIOLATIONS * 2)) });
  assert.ok(s.calls.created[0].body.length < 65536);
});

test("only the bot's own marked comment is updated", async () => {
  const comments = [
    { id: 1, user: { login: "contributor" }, body: `${MARKER} planted by someone else` },
    { id: 2, user: { login: BOT }, body: `${MARKER}\nold` },
  ];
  const s = stub({ pulls: [pull(42)], comments });
  await relay({ ...s, dir: await artifact("42") });
  assert.deepEqual(s.calls.updated.map((c) => c.comment_id), [2]);
  assert.equal(s.calls.created.length, 0);
});

test("a marked comment by anyone else leads to a new comment, not an edit", async () => {
  const comments = [{ id: 1, user: { login: "contributor" }, body: MARKER }];
  const s = stub({ pulls: [pull(42)], comments });
  await relay({ ...s, dir: await artifact("42") });
  assert.equal(s.calls.updated.length, 0);
  assert.equal(s.calls.created.length, 1);
});
