"""Run a collector inside a container that cannot reach anything.

The gate itself is trusted code. The revision under test is not: its ``core``
runs inside the collector, beside real documents. In the cloud gate every
collector therefore runs in a throwaway container with:

* no network at all (``--network none``);
* no environment but the handful of variables named here -- the host's
  environment, and every token in it, never reaches the container;
* a non-root user, no capabilities, no privilege escalation, a read-only root
  filesystem, a process limit and a memory limit;
* only the paths the collector needs, each bind-mounted at the path it has on
  the host, read-only unless it is the collector's own output or scratch
  directory;
* no container log: whatever the revision prints is discarded by the engine,
  as the gate discards it locally.

A container is named when it starts, so it can be removed however the run
ends. Killing the ``docker run`` client does not stop the container behind it.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tools.corpus_gate.manifest import SetupError

LABEL = "losslift.corpus-gate=1"
NOBODY = "65534:65534"
_UNSAFE_IN_MOUNT = (",", "\n", "\r", "=", '"')


@dataclass(frozen=True)
class DockerSandbox:
    image: str
    docker: str = "docker"
    memory: str = "3g"
    cpus: str = "1"
    pids: int = 256
    python: str = "/usr/local/bin/python"

    def user(self) -> str:
        uid = os.getuid() if hasattr(os, "getuid") else 0
        # Never root inside. A root host hands its writable directories to
        # nobody instead (see ``prepare``); any other host user maps straight
        # through, so the files it must later delete are its own.
        return NOBODY if uid == 0 else f"{uid}:{os.getgid()}"

    def prepare(self, writable: list[Path], readable: list[Path] = ()) -> None:
        """Give the container user what it must write, and read, on a root host.

        On any other host the container runs as the host user, who already
        owns all of it. Ownership never widens a read-only mount.
        """
        if not (hasattr(os, "getuid") and os.getuid() == 0):
            return
        for path in writable:
            os.chown(path, 65534, 65534)
        for folder in readable:
            for path in [folder, *folder.rglob("*")]:
                os.chown(path, 65534, 65534, follow_symlinks=False)

    @staticmethod
    def new_name() -> str:
        return f"losslift-gate-{secrets.token_hex(8)}"

    def command(
        self,
        *,
        name: str,
        argv: list[str],
        env: dict[str, str],
        read_only: list[Path],
        writable: list[Path],
        workdir: Path,
    ) -> list[str]:
        words = [
            self.docker, "run", "--rm", "-i",
            "--name", name,
            "--label", LABEL,
            "--pull", "never",
            "--network", "none",
            "--ipc", "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", self.user(),
            "--pids-limit", str(self.pids),
            "--memory", self.memory,
            "--memory-swap", self.memory,
            "--cpus", self.cpus,
            "--log-driver", "none",
            "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m",
            "--workdir", str(workdir),
        ]
        for key, value in sorted(env.items()):
            words += ["--env", f"{key}={value}"]
        for path, flag in [(p, ",readonly") for p in read_only] + [(p, "") for p in writable]:
            text = str(path)
            if not path.is_absolute() or any(mark in text for mark in _UNSAFE_IN_MOUNT):
                raise SetupError("a sandbox mount path is not usable")
            words += ["--mount", f"type=bind,source={text},target={text}{flag}"]
        return words + [self.image, self.python, *argv]

    def stop(self, name: str) -> None:
        subprocess.run(
            [self.docker, "rm", "--force", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
