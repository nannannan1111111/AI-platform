"""Compare exported container filesystems while ignoring archive timestamps."""

from __future__ import annotations

import argparse
import hashlib
import tarfile
from pathlib import Path


def _inventory(path: Path) -> list[tuple[str, bytes, int, int, int, int, str, str]]:
    result: list[tuple[str, bytes, int, int, int, int, str, str]] = []
    with tarfile.open(path) as archive:
        for member in sorted(archive.getmembers(), key=lambda item: item.name):
            digest = ""
            if member.isfile():
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"Unable to read regular file from archive: {member.name}")
                digest = hashlib.sha256(source.read()).hexdigest()
            result.append(
                (member.name, member.type, member.mode, member.uid, member.gid, member.size, member.linkname, digest)
            )
    return result


def main() -> int:
    """Return zero when paths, contents, ownership, and modes are identical."""
    parser = argparse.ArgumentParser()
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    arguments = parser.parse_args()

    left = _inventory(arguments.left)
    right = _inventory(arguments.right)
    if left != right:
        for index in range(max(len(left), len(right))):
            left_item = left[index] if index < len(left) else None
            right_item = right[index] if index < len(right) else None
            if left_item != right_item:
                print(f"First filesystem difference at entry {index}:\nleft={left_item!r}\nright={right_item!r}")
                break
        return 1
    print(f"Canonical filesystem inventory matches: {len(left)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
