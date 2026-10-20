# Installed as /etc/profile.d/personalclaw.sh by Dockerfile.backend's runtime stage.
#
# The image puts its virtual environment on PATH with ENV, which every process the container
# runtime starts inherits. A LOGIN shell does not keep it: /etc/profile sets PATH from scratch,
# to a value that knows nothing of /opt/venv. So in the dashboard's terminal, which is a login
# shell, and in `docker exec -it personalclaw bash -l`, `personalclaw` was "not found".
# /etc/profile reads this directory after it resets PATH, so this is where the environment goes
# back in front. POSIX sh: dash reads this file as well as bash.
case ":${PATH}:" in
    *:/opt/venv/bin:*) ;;
    *) PATH="/opt/venv/bin:${PATH}"; export PATH ;;
esac
