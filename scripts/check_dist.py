"""Inspect local wheel/sdist contents without extracting or uploading them."""

import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from check_repository import ROOT, content_findings, forbidden_path


def inspect_archive(archive):
    findings, files, seen = [], set(), set()
    total = 0

    def record(name, content):
        nonlocal total
        canonical = PurePosixPath(name.replace("\\", "/")).as_posix().casefold()
        if canonical in seen:
            findings.append(f"{archive.name}: duplicate member {name}")
        seen.add(canonical)
        files.add(name)
        if forbidden_path(name):
            findings.append(f"{archive.name}: forbidden member {name}")
        # Scan every member, including duplicates, without echoing its contents.
        findings.extend(content_findings(f"{archive.name}/{name}", content))
        total += len(content)
        if len(seen) > 10000 or total > 50 * 1024 * 1024:
            raise ValueError("Archive exceeds inspection limits")

    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                if forbidden_path(item.filename):
                    findings.append(f"{archive.name}: forbidden original path {item.filename}")
                if stat.S_ISLNK(item.external_attr >> 16):
                    findings.append(f"{archive.name}: symlink member {item.filename}")
                    continue
                if item.is_dir():
                    continue
                if item.file_size > 5 * 1024 * 1024:
                    findings.append(f"{archive.name}: oversized member {item.filename}")
                    continue
                record(item.filename, package.read(item))
    else:
        expected_root = archive.name.removesuffix(".tar.gz")
        with tarfile.open(archive, "r:gz") as package:
            for item in package:
                # Validate the original path before stripping the common root.
                if forbidden_path(item.name):
                    findings.append(f"{archive.name}: forbidden original path {item.name}")
                    continue
                parts = PurePosixPath(item.name.replace("\\", "/")).parts
                if not parts or parts[0] != expected_root:
                    findings.append(f"{archive.name}: unexpected source root {item.name}")
                    continue
                if item.isdir():
                    continue
                if not item.isfile() or item.size > 5 * 1024 * 1024 or len(parts) < 2:
                    findings.append(f"{archive.name}: unexpected member {item.name}")
                    continue
                name = "/".join(parts[1:])
                stream = package.extractfile(item)
                if stream is None:
                    findings.append(f"{archive.name}: unreadable member {item.name}")
                    continue
                with stream:
                    record(name, stream.read())
    required = {
        "assistant_app/__main__.py",
        "assistant_app/ui.py",
        "assistant_app/bootstrap.py",
        "assistant_app/domain/models.py",
        "assistant_app/application/engine.py",
        "assistant_app/frontend/app.py",
        "assistant_app/infrastructure/storage.py",
        "assistant_app/infrastructure/providers/anthropic.py",
        "assistant_app/infrastructure/integrations/calendar.py",
        "assistant_app/mcp/__main__.py",
        "assistant_app/mcp/server.py",
        "assistant_app/retrieval/service.py",
        "assistant_app/retrieval/reader.py",
    }
    if not required <= files:
        findings.append(f"{archive.name}: application files missing")
    if not any(Path(name).name == "LICENSE" for name in files):
        findings.append(f"{archive.name}: license missing")
    return findings, len(files)


def main():
    archives = sorted((ROOT / "dist").glob("*.whl")) + sorted((ROOT / "dist").glob("*.tar.gz"))
    if not any(p.suffix == ".whl" for p in archives) or not any(p.name.endswith(".tar.gz") for p in archives):
        print("Build both a wheel and source distribution first: python -m build")
        return 1
    findings = []
    for archive in archives:
        try:
            issues, count = inspect_archive(archive)
        except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile, RuntimeError):
            findings.append(f"{archive.name}: archive could not be safely inspected")
            continue
        findings.extend(issues)
        print(f"Inspected {archive.name}: {count} files")
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
