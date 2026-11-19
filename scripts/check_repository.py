"""Check publishable Git files without displaying potential secret values.

This intentionally scans tracked + untracked non-ignored files, not private local
data. It catches common patterns only and is not a comprehensive secret scanner.
"""

from __future__ import annotations

import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAMES = (
    "state.json",
    "preferences.bin",
    ".env",
    ".env.*",
    "client_secret*.json",
    "credentials*.json",
    "token*.json",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
)
PRIVATE_DIRS = {".local-notes", ".venv", ".venv-check", "__pycache__", ".git", "build", "dist"}
PATTERNS = {
    "Anthropic-style credential": re.compile(r"sk-ant-[A-Za-z0-9_-]{40,}"),
    "GitHub credential": re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})"),
    "Google API credential": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "Notion-style credential": re.compile(r"ntn_[A-Za-z0-9]{40,}"),
    "private key material": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "personal Windows path": re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\s\\/<>]+[\\/]"),
}


def forbidden_path(name):
    path = PurePosixPath(name.replace("\\", "/"))
    return (
        not path.parts
        or path.is_absolute()
        or bool(PureWindowsPath(name).drive)
        or ".." in path.parts
        or any(part.lower() in PRIVATE_DIRS or part.lower().startswith(".venv-") for part in path.parts)
        or any(fnmatch(path.name.lower(), pattern) for pattern in PRIVATE_NAMES)
        or path.suffix.lower() in {".pyc", ".pyo", ".log"}
    )


def content_findings(name, data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    findings = []
    for label, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{name}:{line}: {label} (value hidden)")
    return findings


def main():
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        index = subprocess.run(
            ["git", "ls-files", "-z", "--stage"], cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except (OSError, subprocess.CalledProcessError):
        print("Repository check needs Git and a working Git checkout.", file=sys.stderr)
        return 1
    names = sorted(set(result.stdout.decode("utf-8").strip("\0").split("\0")) - {""})
    findings = []
    checked = 0
    # Check the index as well as working files: an unstaged cleanup/deletion does
    # not remove credential content already staged for a future commit.
    for entry in index.stdout.decode("utf-8").split("\0"):
        if not entry:
            continue
        metadata, name = entry.split("\t", 1)
        mode, oid, stage = metadata.split()
        if stage != "0" or mode not in ("100644", "100755") or forbidden_path(name):
            findings.append(f"{name}: unsupported/private path or unresolved index entry")
            continue
        try:
            size = subprocess.run(
                ["git", "cat-file", "-s", oid], cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            if int(size.stdout) > 5 * 1024 * 1024:
                findings.append(f"{name}: oversized index entry; review manually")
                continue
            blob = subprocess.run(
                ["git", "cat-file", "blob", oid], cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            findings.extend(content_findings(f"{name} (index)", blob.stdout))
        except (OSError, ValueError, subprocess.CalledProcessError):
            findings.append(f"{name}: could not inspect index entry")
    for name in names:
        path = ROOT / name
        if not path.exists():
            continue  # Its index content was checked above, even if deleted locally.
        if forbidden_path(name) or path.is_symlink():
            findings.append(f"{name}: private/generated path or symlink must not be published")
            continue
        if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            findings.append(f"{name}: unexpected or oversized file; review manually")
            continue
        checked += 1
        findings.extend(content_findings(name, path.read_bytes()))
    for finding in findings:
        print(finding)
    if findings:
        return 1
    print(f"Checked {checked} publishable files: no configured private-file or credential patterns found.")
    print("This is a bounded pattern check, not a full secret scan. Review files before publication.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
