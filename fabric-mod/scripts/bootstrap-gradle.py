"""Install the wrapper-pinned distribution for CI, checking its SHA256 first.

The public release GET redirect intermittently returns 504 before Gradle starts.
Resolve the official redirect with HEAD, then fetch its asset with bounded retries.
The wrapper remains the source of both the version and the required checksum.
"""

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
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
    url = url.replace("https://services.gradle.org/", "https://downloads.gradle.org/")
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
    ]
    with tempfile.TemporaryDirectory(prefix="holospeed-gradle-download-") as temporary:
        archive = Path(temporary) / "gradle.zip"
        resolved = None
        for attempt in range(4):
            probe = subprocess.run(
                curl
                + [
                    "--head",
                    "--max-time",
                    "60",
                    "--output",
                    os.devnull,
                    "--write-out",
                    "%{http_code}\n%{url_effective}",
                    url,
                ],
                check=False,
                text=True,
                capture_output=True,
            )
            status, _, candidate = probe.stdout.partition("\n")
            if probe.returncode == 0 and status == "200" and candidate.startswith("https://"):
                resolved = candidate
                break
            if attempt < 3:
                time.sleep(2)
        if resolved is None:
            raise OSError("Official Gradle download was unavailable after four attempts")
        subprocess.run(
            curl
            + [
                "--fail",
                "--retry",
                "3",
                "--retry-delay",
                "2",
                "--max-time",
                "180",
                "--output",
                str(archive),
                resolved,
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
        print(f"Gradle bootstrap failed: {type(error).__name__}", file=sys.stderr)
        sys.exit(1)
