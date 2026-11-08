#!/usr/bin/env python3
"""Install the pinned Baritone API mod into an existing Minecraft client profile.

Downloads only an official release, verifies SHA-256, and never starts Minecraft.
Uses the Python standard library. Source and license notices are written beside
the dependency; the JAR remains a separate, replaceable mod.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

VERSION = "1.11.3"
FILENAME = f"baritone-api-fabric-{VERSION}.jar"
RELEASE_URL = f"https://github.com/cabaletta/baritone/releases/tag/v{VERSION}"
DOWNLOAD_URL = f"https://github.com/cabaletta/baritone/releases/download/v{VERSION}/{FILENAME}"
ASSET_API_URL = "https://api.github.com/repos/cabaletta/baritone/releases/assets/540245077"
SOURCE_URL = f"https://github.com/cabaletta/baritone/tree/v{VERSION}"
LICENSE_URL = f"https://github.com/cabaletta/baritone/blob/v{VERSION}/LICENSE"
EXPECTED_SHA256 = "f9a9b17a41d7d7ad22c759058b14fd01e3319663282e66f99e18153237fa6767"
EXPECTED_SIZE = 4_810_562
NOTICE = f"""Baritone {VERSION}: Fabric API distribution for Minecraft 1.21 / 1.21.1
Upstream authors: leijurv, Brady, and Baritone contributors.
License: GNU Lesser General Public License v3; source headers allow v3 or later.
License text: {LICENSE_URL}
Corresponding upstream source: {SOURCE_URL}
Official release: {RELEASE_URL}
Downloaded artifact: {DOWNLOAD_URL}
SHA-256: {EXPECTED_SHA256}

This is the unmodified upstream JAR, installed as a separate replaceable mod.
HoloSpeed does not claim authorship of Baritone. Preserve upstream notices and
license terms when redistributing. Installing this file does not start Minecraft.
"""


class InstallError(Exception):
    """An expected installation failure with a useful user-facing message."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def profile_mods(minecraft_dir: Path) -> Path:
    profile = minecraft_dir.expanduser().resolve(strict=True)
    if not profile.is_dir():
        raise InstallError("--minecraft-dir must name an existing Minecraft profile directory")
    # Avoid accidentally installing into a home directory or an empty new path.
    markers = (profile / "options.txt", profile / "launcher_profiles.json")
    if not any(path.is_file() for path in markers):
        raise InstallError(
            "The directory does not look like an existing Minecraft client profile: "
            "expected options.txt or launcher_profiles.json. Launch that profile once first."
        )
    mods = profile / "mods"
    if mods.is_symlink():
        raise InstallError("Refusing a symlinked mods directory; select its real profile")
    if mods.exists() and not mods.is_dir():
        raise InstallError("The profile's mods path exists but is not a directory")
    mods.mkdir(exist_ok=True)
    return mods


def publish_without_overwrite(temporary: Path, destination: Path) -> None:
    """Publish an already-complete file atomically, refusing concurrent replacement."""
    try:
        os.link(temporary, destination)
    except FileExistsError as exc:
        raise InstallError(f"Refusing to replace existing file: {destination}") from exc


def download(output) -> int:
    """Try the public release URL, then its exact official asset API on network failure."""
    for index, url in enumerate((DOWNLOAD_URL, ASSET_API_URL)):
        request = Request(
            url,
            headers={
                "User-Agent": "HoloSpeed-dependency-installer/1",
                "Accept": "application/octet-stream",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        output.seek(0)
        output.truncate()
        received = 0
        try:
            with urlopen(request, timeout=45) as response:
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > EXPECTED_SIZE:
                        raise InstallError("Download exceeds the pinned release's byte count")
                    output.write(chunk)
            return received
        except (URLError, TimeoutError):
            if index == 1:
                raise
    raise AssertionError("No download URL attempted")


def install(minecraft_dir: Path) -> Path:
    mods = profile_mods(minecraft_dir)
    destination = mods / FILENAME
    conflicting = [
        path.name
        for path in mods.iterdir()
        if "baritone" in path.name.lower()
        and path.suffix.lower() == ".jar"
        and path.name != FILENAME
    ]
    if conflicting:
        raise InstallError(
            "Another Baritone JAR is present; review it before installing a second copy: "
            + ", ".join(sorted(conflicting))
        )
    if destination.is_symlink():
        raise InstallError("Refusing to use a symlink as the installed Baritone JAR")
    if destination.exists():
        if not destination.is_file() or sha256(destination) != EXPECTED_SHA256:
            raise InstallError(
                "The existing Baritone JAR does not match the pinned release. "
                "It was left unchanged; inspect or move it manually before retrying."
            )
    else:
        with tempfile.NamedTemporaryFile(prefix=".baritone-", dir=mods, delete=False) as output:
            temporary = Path(output.name)
            try:
                received = download(output)
                output.flush()
                os.fsync(output.fileno())
                output.close()
                if received != EXPECTED_SIZE or sha256(temporary) != EXPECTED_SHA256:
                    raise InstallError("Baritone download failed the pinned size/SHA-256 check")
                temporary.chmod(0o644)
                publish_without_overwrite(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

    notice = mods / f"baritone-{VERSION}-SOURCE.txt"
    if notice.is_symlink():
        raise InstallError("Refusing a symlinked Baritone source notice")
    if not notice.exists():
        with notice.open("x", encoding="utf-8") as output:
            output.write(NOTICE)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--minecraft-dir",
        required=True,
        type=Path,
        help="Existing client profile containing options.txt or launcher_profiles.json",
    )
    args = parser.parse_args()
    try:
        destination = install(args.minecraft_dir)
    except (InstallError, OSError, URLError) as exc:
        parser.exit(1, f"Baritone installation failed: {exc}\n")
    print(f"Verified Baritone {VERSION}: {destination}")
    print(f"SHA-256: {EXPECTED_SHA256}")
    print(f"License and source: {destination.parent / f'baritone-{VERSION}-SOURCE.txt'}")
    print("Minecraft was not started or restarted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
