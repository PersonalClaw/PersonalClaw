"""The host's side of a container install: the one place its commands are spelled.

Inside the image nothing can drive the container runtime. The image is replaced from the host,
and the runtime starts, stops and restarts the gateway, which is the container's own main
process. So every surface that would act on the gateway here says which command to run on the
host instead: ``personalclaw update`` and the Updates panel, ``personalclaw stop`` and
``restart``, and ``personalclaw service``. Each of those commands comes from this module, and
``tests/test_container_host.py`` holds README.md's ``docker run`` and
``docs/guides/containers.md`` to it, so a documented command and the one the product prints
cannot drift apart again. (They did: the update instructions named Docker Compose while the
README's container install is a plain ``docker run``.)

Two documented installs run the image, and they need different commands:

* **the README's one container**, ``docker run … --name personalclaw … -v personalclaw_home:/data``.
  This is the image's default, with nothing set.
* **Docker Compose**, ``deploy/compose/compose.yaml``, which says so by setting
  ``PERSONALCLAW_CONTAINER_STARTED_BY=compose``.

A container cannot see how it was started: its name, its compose labels and its published port
all live on the host. That is why the compose file declares itself rather than the gateway
guessing.
"""

from __future__ import annotations

import os

#: The published gateway image.
IMAGE = "ghcr.io/personalclaw/personalclaw-gateway"
#: The README's container name and named volume.
CONTAINER_NAME = "personalclaw"
VOLUME = "personalclaw_home"
#: The compose file the README and the container guide run, from a checkout, and its gateway
#: service.
COMPOSE = "docker compose -f deploy/compose/compose.yaml"
COMPOSE_SERVICE = "personalclaw-gateway"
#: Set by ``deploy/compose/compose.yaml``; unset for the README's ``docker run``.
STARTED_BY_ENV = "PERSONALCLAW_CONTAINER_STARTED_BY"


def in_container() -> bool:
    """Whether this process runs in the published image. ``self_update`` owns the answer."""
    from personalclaw.self_update import detect_install_kind

    return detect_install_kind() == "container"


def started_by_compose() -> bool:
    """Whether Docker Compose started this container (its compose file says so)."""
    return (os.environ.get(STARTED_BY_ENV) or "").strip().lower() == "compose"


def run_command(tag: str = "latest") -> str:
    """The README's ``docker run``, for image *tag*: the command that makes the container.

    It publishes the port the image's gateway listens on, on the host's loopback only and under
    the same number, as the README runs it. That port is the product's default
    (``config.loader``'s, the image's ``PERSONALCLAW_PORT``), not this process's: the command
    makes the README's container, which sets no other port, so publishing a port a customized
    container moved to would map one the new container's gateway does not listen on.
    """
    from personalclaw.config.loader import _DEFAULT_PORT

    return (
        f"docker run -d --name {CONTAINER_NAME} --restart unless-stopped "
        f"-p 127.0.0.1:{_DEFAULT_PORT}:{_DEFAULT_PORT} -e PERSONALCLAW_BIND_HOST=0.0.0.0 "
        f"-v {VOLUME}:/data {IMAGE}:{tag or 'latest'}"
    )


def update_commands(tag: str) -> list[str]:
    """What the host runs to move this container onto image *tag*, in order.

    A container is updated by replacing it: pull the new image, remove the container, and make
    it again the way it was made. For the README's container that is its own ``docker run``, so
    the recreate cannot differ from the documented install by a flag. The volume is named, so
    ``docker rm`` leaves it, and the state in it, where it is.

    Compose selects the image through ``${PERSONALCLAW_IMAGE_TAG:-latest}``, and its pull and
    ``up`` run as separate processes, so the tag is set on both: on only one, the other would
    fall back to ``latest``. An empty *tag* is the bare commands, which is compose's own
    ``latest``.

    The first line is a shell comment saying whose flags these are. It is part of what both the
    Updates panel and ``personalclaw update`` show, and a shell pasting it ignores it.
    """
    if started_by_compose():
        prefix = f"PERSONALCLAW_IMAGE_TAG={tag} " if tag else ""
        return [
            "# From the checkout that holds the compose file "
            "(for a compose.yaml you downloaded on its own, use -f compose.yaml).",
            f"{prefix}{COMPOSE} pull",
            f"{prefix}{COMPOSE} up -d",
        ]
    image = f"{IMAGE}:{tag or 'latest'}"
    return [
        "# The README's docker run. If you started the container with another name, port or "
        "volume, use yours.",
        f"docker pull {image}",
        f"docker stop {CONTAINER_NAME}",
        f"docker rm {CONTAINER_NAME}",
        run_command(tag),
    ]


def stop_command() -> str:
    """What the host runs to stop the gateway: it stops the container it is the process of."""
    if started_by_compose():
        return f"{COMPOSE} stop {COMPOSE_SERVICE}"
    return f"docker stop {CONTAINER_NAME}"


def restart_command() -> str:
    """What the host runs to restart the gateway."""
    if started_by_compose():
        return f"{COMPOSE} restart {COMPOSE_SERVICE}"
    return f"docker restart {CONTAINER_NAME}"


def keeps_running() -> str:
    """Why a container install has no service to install, as the sentence the CLI says."""
    if started_by_compose():
        policy = "the compose file sets `restart: unless-stopped`"
    else:
        policy = "the README's `docker run` sets `--restart unless-stopped`"
    return (
        "PersonalClaw is running in a container, so the container runtime keeps it running, not "
        f"a service: {policy}, which brings the gateway back by itself after a crash, an "
        "out-of-memory kill or a Docker restart."
    )


def not_done_here(done: str, host_command: str) -> str:
    """What ``stop`` and ``restart`` say here instead of acting.

    The gateway is the container's own process. Stopped from inside, it ends the container, and
    the restart policy that keeps it running starts it again: a stop that does not stop. A
    restart from inside starts a second gateway in a container that is going away. So nothing is
    *done* here, and *host_command* is what does it.
    """
    return (
        "PersonalClaw is running in a container, and its gateway is the container's own "
        f"process, so the container runtime starts and stops it. Nothing was {done}. On the "
        f"host, run:\n\n    {host_command}"
    )
