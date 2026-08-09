# Slice 3 — Side effects, scope, termination, secrets

**What the slice added.** The effect ledger (idempotency keys, teardown), write-scope
enforcement, termination (sticky cancel, protocol-violation), the v2 `run-workflow` action
provider, and secret handling (`{{secret:KEY}}` resolution, `_has*` stripping, the
RedactingSink on the journal and event stream). This exemplar isolates the secret half —
the piece whose failure mode is the sharpest: a leaked credential in the journal, which the
flywheel reads, bug reports ship, and the UI renders.

**What this exemplar proves.** Two mechanisms:

- **`{{secret:KEY}}` goes only through the injected resolver.** Bindings never read the
  credential store directly, so an exemplar (or unit test) supplies its own resolver; a
  reference to an unset key raises a typed `BindingError` rather than resolving to an empty
  string. Both directions are asserted.
- **The RedactingSink scrubs the write path.** A real run whose (fake) model echoes a
  secret-shaped token into its OUTPUT is driven to completion, then the entire run directory
  is read back and grepped: the token appears nowhere on disk. The journal — the thing read
  by three downstream consumers — scrubbed it on the way out.

The token used is synthetic (an `sk-`-prefixed filler string), never a real credential; the
whole point is that this synthetic value must not survive to disk.

**Mechanism under test:** `personalclaw.workflows.bindings` secret resolution +
`personalclaw.workflows.journal` RedactingSink (WF2-R14).
