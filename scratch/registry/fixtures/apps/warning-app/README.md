# Registry Fixture (warning)

Identical to the clean fixture except that `scripts/setup.sh` downloads a corpus
file. That download is what makes its scanner verdict `warning`.

This fixture exists to prove the listing policy's least obvious rule: a `warning`
verdict is **displayed on the listing and blocks nothing**. Not intended for
installation.
