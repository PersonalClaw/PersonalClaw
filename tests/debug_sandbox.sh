#!/usr/bin/env bash
# Quick on-host check of the sandbox: prints the detected backend and runs a
# wrapped `ls ~/.aws/` to confirm credential paths are blocked. Not part of the
# pytest suite — see tests/test_sandbox_*.py for automated coverage.
PYTHONPATH="src" \
python3 -c "
from personalclaw.sandbox import detect_backend, wrap_argv
print('Backend:', detect_backend())
import subprocess, sys, os
argv, cleanup = wrap_argv(['ls', os.path.expanduser('~/.aws/')], 'auto')
print('Wrapped argv:', argv[:3], '...')
try:
    r = subprocess.run(argv, capture_output=True, timeout=30, text=True)
    print('STDOUT:', r.stdout[:200])
    print('STDERR:', r.stderr[:500])
    print('RC:', r.returncode)
finally:
    if cleanup:
        os.unlink(cleanup)
"
