#!/bin/sh
# Combines breadth.yml's six coverage shards into coverage.xml (its `coverage` job).
# All six or no report: a shard's data missing would publish a smaller number as the suite's,
# with nothing to say part of it was never counted. No shard carries an earlier result (each
# runs the security-marked modules, so each runs every night), so every shard leaves its data.
set -eu
mkdir -p coverage-data
found=$(find coverage-data -name '.coverage.shard*' -type f | wc -l)
if [ "$found" -ne 6 ]; then
  echo "::error title=coverage data incomplete::found the data of $found of 6 shards."
  exit 1
fi
uv run coverage combine coverage-data
uv run coverage xml -o coverage.xml
echo "coverage of the whole suite: $(uv run coverage report --format=total)%"
