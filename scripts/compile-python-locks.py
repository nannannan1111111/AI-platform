"""Compile deterministic Linux Python lock files from pyproject.toml."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PIP_TOOLS_VERSION = "7.5.2"
LOCKS = (
    ("requirements.lock", (), "python -m piptools compile --generate-hashes --strip-extras "
     "--output-file requirements.lock pyproject.toml"),
    ("requirements-dev.lock", ("--extra", "dev"), "python -m piptools compile --extra dev --generate-hashes "
     "--strip-extras --output-file requirements-dev.lock pyproject.toml"),
)


def _run(arguments: list[str], *, environment: dict[str, str] | None = None) -> None:
    subprocess.run(arguments, check=True, env=environment)


def _compile(output: Path, extras: tuple[str, ...], display_command: str) -> None:
    environment = os.environ.copy()
    environment["CUSTOM_COMPILE_COMMAND"] = display_command
    _run(
        [
            sys.executable,
            "-m",
            "piptools",
            "compile",
            *extras,
            "--generate-hashes",
            "--strip-extras",
            "--resolver",
            "backtracking",
            "--quiet",
            "--output-file",
            str(output),
            "pyproject.toml",
        ],
        environment=environment,
    )


def main() -> int:
    """Update locks, or return nonzero when committed locks are stale."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()

    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "--root-user-action",
            "ignore",
            f"pip-tools=={PIP_TOOLS_VERSION}",
        ]
    )

    backend_root = Path.cwd()
    if arguments.check:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            stale: list[str] = []
            for filename, extras, command in LOCKS:
                candidate = temporary_root / filename
                _compile(candidate, extras, command)
                committed = backend_root / filename
                if not committed.is_file() or candidate.read_bytes() != committed.read_bytes():
                    stale.append(filename)
            if stale:
                print(f"Stale Python lock files: {', '.join(stale)}", file=sys.stderr)
                return 1
        return 0

    for filename, extras, command in LOCKS:
        _compile(backend_root / filename, extras, command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
