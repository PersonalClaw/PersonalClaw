const { describe, it } = require("node:test");
const assert = require("node:assert/strict");

const { classify } = require("../shellRefusal");

/** The gateway's error envelope (`http_errors.json_error`). */
function envelope(code, reason) {
  return { error: { code, message: "said by the gateway", ...(reason ? { detail: { reason, at: 1 } } : {}) } };
}

describe("classify: what a refusal of the shell's own request means for its sign-in", () => {
  it("ends the sign-in the gateway ended", () => {
    for (const code of ["auth_bearer_invalid", "session_signed_out", "session_expired"]) {
      assert.equal(classify(403, envelope(code)), "end", code);
      assert.equal(classify(401, envelope(code)), "end", code);
    }
  });

  it("calls a refused pair or an unbound cookie on the shell's own request a defect", () => {
    assert.equal(classify(403, envelope("session_required", "pair_wrong")), "defect");
    assert.equal(classify(403, envelope("session_required", "unbound_cookie")), "defect");
  });

  it("never ends a sign-in whose pair only didn't arrive", () => {
    assert.equal(classify(403, envelope("session_required", "pair_missing")), "keep");
  });

  it("leaves every other answer to the handling it always had", () => {
    assert.equal(classify(200, { ok: true }), "none");
    assert.equal(classify(404, envelope("not_found")), "none");
    assert.equal(classify(403, envelope("owner_presence_required")), "none");
    assert.equal(classify(403, envelope("session_required", "not_signed_in")), "none");
    assert.equal(classify(403, null), "none");
    assert.equal(classify(403, "not json"), "none");
    assert.equal(classify(500, envelope("session_expired")), "none");
  });
});
