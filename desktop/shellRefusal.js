"use strict";

/**
 * What a refusal of one of the shell's own requests to its gateway means for the sign-in it was
 * sent on (`classify`): the menu bar's reads, the registration and its state push, the unregister
 * at quit, the Show Buddy PATCH, `/api/listening`, and the one read before a found gateway's
 * same-start lift. Never a window's traffic.
 *
 * - `end`: the gateway ended that sign-in (`auth_bearer_invalid`, `session_signed_out`,
 *   `session_expired`). The shell stops asking with it, and nothing mints a new one on a refusal.
 * - `defect`: the gateway refused the window session's pair or found a cookie no pair goes with
 *   (`session_required` with `pair_wrong` or `unbound_cookie`). On the shell's own request that is
 *   this app's bug on a live sign-in: it is logged as one, the cookie and the header go, and the
 *   sign-in ends.
 * - `keep`: the pair didn't reach the gateway (`pair_missing`). That never ends a sign-in.
 * - `none`: anything else, handled as it always was.
 *
 * Applied only while the held sign-in has a pair; a gateway that gives none is handled as today.
 * Pure and Electron-free (`test/shellRefusal.test.js`).
 */

const ENDS = Object.freeze(["auth_bearer_invalid", "session_signed_out", "session_expired"]);
const DEFECTS = Object.freeze(["pair_wrong", "unbound_cookie"]);
const KEEPS = Object.freeze(["pair_missing"]);

/** `{code, reason}` of an error answer's envelope, with "" for what it does not say. */
function refusalOf(body) {
  const error = body && typeof body === "object" ? body.error : null;
  if (!error || typeof error !== "object") return { code: "", reason: "" };
  const code = typeof error.code === "string" ? error.code : "";
  const detail = error.detail && typeof error.detail === "object" ? error.detail : {};
  const reason = typeof detail.reason === "string" ? detail.reason : "";
  return { code, reason };
}

/** `end`, `defect`, `keep` or `none` for an answer with *status* and *body*. */
function classify(status, body) {
  if (status !== 401 && status !== 403) return "none";
  const { code, reason } = refusalOf(body);
  if (KEEPS.includes(reason)) return "keep";
  if (code === "session_required" && DEFECTS.includes(reason)) return "defect";
  if (ENDS.includes(code)) return "end";
  return "none";
}

module.exports = { DEFECTS, ENDS, KEEPS, classify, refusalOf };
