# When a variation earns its own test

Prove each behaviour once, at the highest layer where a user can observe it cheaply: an HTTP
call, a CLI run, a browser journey. Every pytest item pays the suite's per-test floor (its
autouse fixtures, its setup and teardown), so a property proved again for every input it accepts
costs the suite a floor per input while proving nothing the first case did not. The census below
says where that cost is largest.

## The rule

A variation earns its own test only if it:

1. **takes a code path the representative case does not**;
2. **is a security or fail-closed boundary**, so every refusal path keeps a test that proves it
   refuses (a module or test carrying the `security` marker, `tests/README.md`);
3. **pins a bug that actually happened**;
4. **is a distinct door a user can drive**: a route family, a CLI verb, a settings panel; or
5. **is platform behaviour**, kept on the leg that can show it.

Every other variation is a **row in one table-driven test per property**, using pytest's built-in
`subtests` fixture:

- the test reports every failing row, not only the first;
- it asserts the row count, so a table that shrank cannot pass;
- each failure message carries the row's input;
- rows that share one fixture reset the state they touch after each row, or assert it unchanged,
  so a row cannot pass on what the row before it left.

A test that pins source text becomes a behaviour assertion, or an AST rail with a stated reason
when the property is structural.

## A parametrized test, converted

Before: one item per spelling, each paying the floor, each setting up its own home.

```python
@pytest.mark.parametrize("raw", ["false", "False", "0", "no", "off"])
def test_a_false_spelling_reads_false(raw, tmp_path):
    assert read_flag(raw) is False
```

After: one item, every row checked, every failing row named.

```python
FALSE_SPELLINGS = ["false", "False", "0", "no", "off"]


def test_every_false_spelling_reads_false(subtests):
    assert len(FALSE_SPELLINGS) == 5, "a spelling was dropped from the table"
    for raw in FALSE_SPELLINGS:
        with subtests.test(raw=raw):
            assert read_flag(raw) is False, f"{raw!r} read {read_flag(raw)!r}"
```

A failing row prints as `SUBFAILED(raw='no')` with its own message, and the rows after it still
run. The same holds inside an `async def` test under pytest-asyncio; `tests/test_ci_variation_census.py`
proves it on a planted async table. `tests/rails/test_ci_contract.py` is a real table test: every CI
property is one row, its controls run as subtests.

When rows share a fixture, undo what each row changes before the next:

```python
def test_every_setting_round_trips(subtests, home):
    for key, value in ROWS:
        with subtests.test(key=key):
            before = home.config_text()
            write_setting(home, key, value)
            assert read_setting(home, key) == value
            home.restore_config(before)  # the next row starts from what this one found
```

## Consolidating a family

Every consolidation follows one procedure, and its commit carries both artefacts:

1. **A map in the commit body** from each removed test item to the row or test that now proves it.
2. **Unchanged line coverage of the code under test.** Run the touched test files before and
   after the change, with the module they cover:

   ```bash
   .venv/bin/python -m pytest <touched test files> -n 0 -q \
       --cov=personalclaw.<module> --cov-report=json:.local/scratch/cov-before.json
   # make the change, then the same command with cov-after.json
   .venv/bin/python - <<'EOF'
   import json
   def covered(name):
       files = json.load(open(f".local/scratch/{name}.json"))["files"]
       return {(path, line) for path, f in files.items() for line in f["executed_lines"]}
   before, after = covered("cov-before"), covered("cov-after")
   print("lost:", sorted(before - after))
   print("gained:", len(after - before))
   EOF
   ```

   "lost" must be empty. Put both counts in the commit body.

## Where the suite pays most: the census

[variation-census.md](variation-census.md) lists every parametrized function with 20 or more
cases, ranked by the seconds its cases took in total, and flags the ones carrying the `security`
marker: a consolidation of a flagged function keeps every refusal path. It informs; it gates
nothing, and nothing checks that it is current.

The published numbers come from the four Linux `junit-test-shard-*` artifacts of one CI run,
which together are one full pass of the suite; the census's header names the run, the commit it
tested and the commit the collection was taken at. A case count reads `(N timed)` when the run
timed fewer cases than were collected: a test added after the run, or one whose cases depend on
the machine collecting (the apps boundary test collects a sibling apps checkout's files when one
is there).

To regenerate it (the junit artifacts are kept 7 days; `gh run download <run> -n
junit-test-shard-<n>` fetches one):

```bash
.venv/bin/python -m pytest --collect-only -qq -n 0 > .local/scratch/collection.txt
.venv/bin/python -m pytest --collect-only -qq -n 0 -m security > .local/scratch/security.txt
.venv/bin/python -m tools.ci variation-census \
    --collection .local/scratch/collection.txt \
    --security-collection .local/scratch/security.txt \
    --junit <shard-1.xml> --junit <shard-2.xml> --junit <shard-3.xml> --junit <shard-4.xml> \
    --commit <collected commit> --run <run id> --run-commit <the run's commit> \
    --output docs/maintainers/variation-census.md
```

`--collect-only -qq` prints one test id per line because the suite's `addopts` start with
`--verbose`. Each collection takes about three minutes; the census itself under a second.
