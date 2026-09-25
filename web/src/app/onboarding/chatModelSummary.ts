/** The one sentence onboarding's step-3 row and the done-screen recap say about the chat model.
 *
 *  Two surfaces on two screens say it — the collapsed essentials row and "Chat model: …" in the
 *  recap — and they are reached two ways: a first pass (the lane's own verdict) and a re-entered
 *  run (the flow's `GET /api/onboarding`). #3528 was the two ways disagreeing, so the words live
 *  here and both ways pass the same two facts in:
 *
 *  - `model`: the bound model's name from the live binding (`boundModelLabel`), `''` when nothing
 *    is bound and resolution came from the implicit "first capable provider" rule;
 *  - `floor`: what answers is the small zero-config model PersonalClaw downloads.
 *
 *  🔴 The floor is named even when it is BOUND. Onboarding binds it when a user downloads it in
 *  step 3, and a recap reading just `SmolLM2-135M-Instruct-Q8_0` presents a 135M model as a
 *  finished model setup — the conclusion-about-the-product the honesty requirement exists to
 *  prevent. "A configured provider" is never said about it, because nobody configured one.
 */
export function chatModelSummary(model: string, floor: boolean): string {
  if (floor) {
    return model
      ? `${model} — the small model PersonalClaw downloaded`
      : 'Ready — using the small model PersonalClaw downloaded'
  }
  return model || 'Ready — using a configured provider'
}
