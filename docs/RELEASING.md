# Release guide

Use this checklist to validate, package, and publish a release. CI runs validation and produces inspection artifacts; maintainers handle publication separately.

## Pre-release checks

- Review the MIT license, copyright attribution, project name and repository URLs.
- Confirm repository visibility, maintainer access, vulnerability reporting, and available secret-scanning settings.
- Review selected OAuth scopes and provider setup instructions. Do not bundle an OAuth client file, API key or sample private account data.
- Review limitations and mark what was actually tested on live providers. Use test accounts and approve each intentional write through the UI.
- Run the checks below, inspect the archives, and exercise the floating icon, settings, approval/revision, session reset and preference deletion manually.
- Keep internal notes, user-specific paths, virtual environments and local runtime data out of source and distribution artifacts.

## Validate and build

From a clean virtual environment, install `.[dev]`, then run:

```powershell
python -m ruff check .
python -m ruff format --check .
python scripts/run_tests.py --headless
python scripts/check_repository.py
python -m build
python -m twine check dist/*
python scripts/check_dist.py
```

Run `python scripts/run_tests.py` in an interactive Windows session for the full visible UI suite. Also install the generated wheel in a separate virtual environment and run `scripts/smoke_installed.py` with that environment's Python from outside the source tree (pass the script's absolute path). This checks imports, the installed launcher and Windows-encrypted storage without showing a window or reading user data. Launch its `personal-assistant.exe` separately and test using non-sensitive data before calling the release verified.

## Publish a release

1. Update the version in `pyproject.toml` and move the relevant changelog entries into a release section using the actual release date.
2. Repeat validation and inspect the wheel and source archive. Confirm that the package-index namespace is owned by the publishing account before uploading.
3. Commit the release changes, create a version tag, and push the commit and tag to the repository.
4. Publish the verified artifacts through the maintainer's release process and include release notes, supported platforms, and known limitations.

CI artifacts are for inspection, not published releases. Credentials, local runtime data, and internal notes must remain outside all release artifacts.
