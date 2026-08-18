# Registry Fixture (dangerous)

Identical to the clean fixture except that `scripts/install.sh` contains an
unambiguously destructive command. Its scanner verdict is `dangerous`.

This fixture exists to prove the listing policy's hard floor: a `dangerous` verdict
**blocks the listing**, and the rule that fired is recorded on the pull request. Not
intended for installation — the script carries its own guard, but do not run it.
