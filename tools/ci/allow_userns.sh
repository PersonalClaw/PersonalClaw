#!/bin/sh
# Let this runner's unprivileged processes make user namespaces, for a job whose tests need the
# sandbox's own launcher (the `userns` toolchain word, #3918).
#
#   (from the repository root) no arguments
#
# GitHub's Ubuntu 24.04 runners set kernel.apparmor_restrict_unprivileged_userns=1, which withholds
# the user and mount namespaces the sandbox's launcher needs. On a GitHub-hosted Linux runner, a
# throwaway machine, this sets it to 0 for the rest of the job. Anywhere else it changes nothing and
# fails: not on a developer's machine, and never on a self-hosted runner, which is a machine someone
# owns. run-job runs it only as the install of a job that names `userns`, gives it the runner's own
# GITHUB_ACTIONS and RUNNER_ENVIRONMENT there (tools/ci/jobs.py), and names such a job as one it
# cannot run on any other machine. A machine of your own keeps the restriction; there, give the
# sandbox an AppArmor profile that allows user namespaces instead.
set -eu

setting=kernel.apparmor_restrict_unprivileged_userns
if [ "$(uname -s)" != Linux ] || [ "${GITHUB_ACTIONS:-}" != true ] \
  || [ "${RUNNER_ENVIRONMENT:-}" != github-hosted ]; then
  echo "allow_userns: not a GitHub-hosted Linux runner, so $setting is left as it is" >&2
  exit 1
fi
before=$(sysctl -n "$setting" 2>/dev/null || echo absent)
if [ "$before" = 1 ]; then
  sudo sysctl -w "$setting=0" >/dev/null
fi
after=$(sysctl -n "$setting" 2>/dev/null || echo absent)
echo "allow_userns: $setting was $before, is $after"
[ "$after" != 1 ]
