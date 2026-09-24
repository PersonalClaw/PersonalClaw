#!/usr/bin/env bash
# The whole point of this fixture: one ordinary-looking download.
#
# Fetching a data file at setup is not malice — plenty of honest apps do it — but it
# is exactly the kind of thing a user deserves to be told about before installing.
# So the scanner calls it a WARNING, the registry LISTS the app, and the verdict is
# shown on the listing. A registry that auto-rejected this would reject most real
# software; one that hid it would be lying by omission.
#
# (This comment deliberately avoids naming the command below: the scanner reports the
# FIRST match in the file as its evidence, and evidence quoting a comment instead of
# the real line would be useless to a reviewer.)
set -euo pipefail

curl -fsSL https://example.invalid/corpus.json -o corpus.json
