const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

// ── Electron's install gives the setuid bit only on a machine GitHub owns ─────────────────────
//
// `tools/ci/sandbox_helper.sh` makes a Chromium sandbox helper root's with mode 4755, which a
// GitHub-hosted Linux runner needs (Ubuntu's AppArmor rule): the toolchain's binary's, through
// `tools/ci/install_electron.sh`, and a packed app's, in the desktop smoke (#3942).
// `GITHUB_ACTIONS=true` is set on self-hosted runners too, so every `sudo` line sits inside a
// guard that names Linux, GitHub Actions and a github-hosted runner: the bit is never set on a
// machine someone else owns. Under `tools/ci run-job` a step gets neither variable, so the guard
// also takes run-job's marker, `PCLAW_SANDBOX_HELPER_HOST=github-hosted`, which run-job gives the
// helper's callers only after reading a GitHub-hosted runner from its own environment
// (tools/ci/jobs.py SANDBOX_HELPER_ENV); the guard names Linux either way.

const CI = path.join(__dirname, "..", "..", "tools", "ci");
const SCRIPT = path.join(CI, "sandbox_helper.sh");
const INSTALL = path.join(CI, "install_electron.sh");

/** Each `sudo` line, with the conditions of every `if` it sits inside. */
function sudoLines(src) {
  const open = [];
  const out = [];
  for (const raw of src.split("\n")) {
    const line = raw.replace(/#.*$/, "").trim();
    if (!line) continue;
    if (/^if\b/.test(line)) open.push(line);
    else if (/^fi\b/.test(line)) open.pop();
    else if (/^sudo\b/.test(line)) out.push({ line, guards: [...open] });
  }
  return out;
}

const NAMES = [
  /\$\(uname -s\)" = Linux/,
  /"\$\{GITHUB_ACTIONS:-\}" = true/,
  /"\$\{RUNNER_ENVIRONMENT:-\}" = github-hosted/,
  /"\$\{PCLAW_SANDBOX_HELPER_HOST:-\}" = github-hosted/,
];

describe("install_electron.sh", () => {
  it("sets no bit itself: the toolchain's helper goes through sandbox_helper.sh", () => {
    const src = fs.readFileSync(INSTALL, "utf8");
    assert.deepEqual(sudoLines(src), []);
    assert.match(src, /^sh tools\/ci\/sandbox_helper\.sh node_modules\/electron\/dist\/chrome-sandbox$/m);
  });
});

describe("sandbox_helper.sh", () => {
  it("sets the helper's bit only inside a guard naming Linux, GitHub Actions and a github-hosted runner, or run-job's marker", () => {
    const lines = sudoLines(fs.readFileSync(SCRIPT, "utf8"));
    assert.deepEqual(lines.map((l) => l.line), [
      'sudo chown root:root "$helper"',
      'sudo chmod 4755 "$helper"',
    ], "the two sudo lines, and no other");
    for (const { line, guards } of lines) {
      const guard = guards.join(" && ");
      for (const name of NAMES) assert.match(guard, name, `${line}: its guard names ${name}`);
    }
  });

  it("sees a sudo line outside the guard (the rail's positive control)", () => {
    const planted = 'set -eu\nsudo chmod 4755 x\nif [ "$(uname -s)" = Linux ]; then\n  sudo chown root:root x\nfi\n';
    const [outside, inside] = sudoLines(planted);
    assert.deepEqual(outside.guards, []);
    assert.equal(NAMES.every((n) => n.test(inside.guards.join(" "))), false, "a guard naming Linux alone is not enough");
  });
});
