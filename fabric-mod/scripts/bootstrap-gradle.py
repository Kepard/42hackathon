"""Install the wrapper-pinned distribution for CI, checking its SHA256 first.

The release-page redirect returns 504 on some CI networks, including HEAD requests.
Use Gradle's official GitHub release-assets API instead of that web frontend.
The wrapper remains the source of both the version and the required checksum.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def main() -> None:
    properties = Path(__file__).resolve().parents[1] / "gradle/wrapper/gradle-wrapper.properties"
    values = dict(line.split("=", 1) for line in properties.read_text().splitlines() if "=" in line)
    url = values["distributionUrl"].replace("\\:", ":")
    match = re.fullmatch(
        r"https://(?:services|downloads)\.gradle\.org/distributions/gradle-([0-9.]+)-bin\.zip", url
    )
    checksum = values["distributionSha256Sum"]
    if not match or not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise ValueError("A stable official Gradle URL and pinned SHA256 are required")
    version = match.group(1)
    destination = Path(sys.argv[1]).resolve()
    curl = [
        "curl",
        "--silent",
        "--show-error",
        "--location",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--fail",
        "--retry",
        "2",
        "--retry-delay",
        "2",
    ]
    with tempfile.TemporaryDirectory(prefix="holospeed-gradle-download-") as temporary:
        archive = Path(temporary) / "gradle.zip"
        metadata = subprocess.check_output(
            curl
            + [
                "--max-time",
                "30",
                "--header",
                "Accept: application/vnd.github+json",
                f"https://api.github.com/repos/gradle/gradle-distributions/releases/tags/v{version}",
            ],
            text=True,
        )
        assets = json.loads(metadata).get("assets", [])
        asset = next(
            (item for item in assets if item.get("name") == f"gradle-{version}-bin.zip"), None
        )
        if not isinstance(asset, dict) or not re.fullmatch(
            r"https://api\.github\.com/repos/gradle/gradle-distributions/releases/assets/[0-9]+",
            asset.get("url", ""),
        ):
            raise ValueError("Official release does not contain the wrapper-pinned Gradle asset")
        print(
            f"Downloading official Gradle {version} asset {asset['id']} through the release API",
            flush=True,
        )
        subprocess.run(
            curl
            + [
                "--header",
                "Accept: application/octet-stream",
                "--max-time",
                "180",
                "--output",
                str(archive),
                asset["url"],
            ],
            check=True,
        )
        with archive.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != checksum:
            raise ValueError("Gradle distribution checksum mismatch")
        with zipfile.ZipFile(archive) as distribution:
            distribution.extractall(destination)
    binary = destination / f"gradle-{version}" / "bin" / "gradle"
    binary.chmod(0o755)
    subprocess.run([str(binary), "--version"], check=True)
    with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as github_path:
        github_path.write(str(binary.parent) + "\n")
    print(f"Verified and installed Gradle {version} from the official distribution")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(f"Gradle bootstrap failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
