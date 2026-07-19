const { describe, it } = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const fs = require("fs");
const { findPersonalclawBin } = require("../find-bin");

const HOME = "/mock/home";
const RESOURCES = "/mock/resources";
const DIRNAME = "/mock/electron";

const fakeOs = { homedir: () => HOME };

const only = (target) => ({
  accessSync: (p) => { if (p !== target) throw new Error("ENOENT"); },
  constants: { X_OK: fs.constants.X_OK },
});

const none = {
  accessSync: () => { throw new Error("ENOENT"); },
  constants: { X_OK: fs.constants.X_OK },
};

describe("findPersonalclawBin", () => {
  it("returns bundled path when it exists", () => {
    const bundled = path.join(RESOURCES, "backend-dist", "personalclaw-backend", "personalclaw-backend");
    const fakeFs = only(bundled);
    const result = findPersonalclawBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, bundled);
  });

  it("returns ~/.local/bin/personalclaw when bundled paths don't exist", () => {
    const localBin = path.join(HOME, ".local", "bin", "personalclaw");
    const fakeFs = only(localBin);
    const result = findPersonalclawBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, localBin);
  });

  it("returns ~/.personalclaw-app/.venv/bin/personalclaw when only venv binary exists", () => {
    const venvBin = path.join(HOME, ".personalclaw-app", ".venv", "bin", "personalclaw");
    const fakeFs = only(venvBin);
    const result = findPersonalclawBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, venvBin);
  });

  it("returns ../bin/personalclaw relative to dirname when only that path exists", () => {
    const binPath = path.resolve(DIRNAME, "..", "bin", "personalclaw");
    const fakeFs = only(binPath);
    const result = findPersonalclawBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, binPath);
  });

  it("falls back to bare 'personalclaw' when no candidates are executable", () => {
    const result = findPersonalclawBin(none, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, "personalclaw");
  });

  it("returns first match when multiple candidates exist", () => {
    const bundled = path.join(RESOURCES, "backend-dist", "personalclaw-backend", "personalclaw-backend");
    const localBin = path.join(HOME, ".local", "bin", "personalclaw");
    const fakeFs = {
      accessSync: (p) => { if (p !== bundled && p !== localBin) throw new Error("ENOENT"); },
      constants: { X_OK: fs.constants.X_OK },
    };
    const result = findPersonalclawBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, bundled);
  });

  it("handles resourcesPath being undefined", () => {
    const localBin = path.join(HOME, ".local", "bin", "personalclaw");
    const fakeFs = only(localBin);
    const result = findPersonalclawBin(fakeFs, fakeOs, path, undefined, DIRNAME);
    assert.equal(result, localBin);
  });

  it("resolves dirname-relative dev path correctly", () => {
    const devBin = path.resolve(DIRNAME, "backend-dist", "personalclaw-backend", "personalclaw-backend");
    const fakeFs = only(devBin);
    const result = findPersonalclawBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, devBin);
  });

  it("skips candidates that throw non-ENOENT errors (e.g. EACCES)", () => {
    const venvBin = path.join(HOME, ".personalclaw-app", ".venv", "bin", "personalclaw");
    const fakeFs = {
      accessSync: (p) => { if (p !== venvBin) throw new Error("EACCES"); },
      constants: { X_OK: fs.constants.X_OK },
    };
    const result = findPersonalclawBin(fakeFs, fakeOs, path, RESOURCES, DIRNAME);
    assert.equal(result, venvBin);
  });
});
