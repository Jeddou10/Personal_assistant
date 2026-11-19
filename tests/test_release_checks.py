"""Regression tests for release hygiene; all credential-shaped data is synthetic."""

import contextlib
import importlib.util
import io
import sys
import tarfile
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
spec = importlib.util.spec_from_file_location("check_repository", SCRIPTS / "check_repository.py")
repository = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repository)
with patch.dict(sys.modules, {"check_repository": repository}):
    spec = importlib.util.spec_from_file_location("check_dist", SCRIPTS / "check_dist.py")
    distribution = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(distribution)


class ReleaseChecksTests(unittest.TestCase):
    def test_private_paths_case_drives_and_traversal(self):
        for name in (
            ".LOCAL-NOTES/private.md",
            ".VENV/file.py",
            ".venv-CHECK/file",
            "C:/private/file.py",
            "C:relative.py",
            "../secret",
            "/root/file",
            "state.json",
        ):
            with self.subTest(name=name):
                self.assertTrue(repository.forbidden_path(name))
        self.assertFalse(repository.forbidden_path("assistant_app/ui.py"))

    def test_reports_do_not_echo_credential_values(self):
        fake = "sk-ant-" + "A" * 45
        findings = repository.content_findings("example.txt", fake.encode())
        self.assertTrue(findings)
        self.assertNotIn(fake, str(findings))

    def test_archive_duplicates_are_rejected_and_every_member_scanned(self):
        with tempfile.TemporaryDirectory() as temp:
            wheel = Path(temp) / "example.whl"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(wheel, "w") as archive:
                    archive.writestr("assistant_app/__main__.py", "")
                    archive.writestr("assistant_app/ui.py", "")
                    archive.writestr("LICENSE", "test")
                    archive.writestr("duplicate.txt", "sk-ant-" + "A" * 45)
                    archive.writestr("duplicate.txt", "clean replacement")
            findings, _ = distribution.inspect_archive(wheel)
            self.assertTrue(any("duplicate member" in item for item in findings))
            self.assertTrue(any("credential" in item for item in findings))

    def test_archive_original_root_is_checked_before_stripping(self):
        with tempfile.TemporaryDirectory() as temp:
            for root in ("..", "/absolute", "C:/drive", "wrong-root"):
                archive_path = Path(temp) / "example.tar.gz"
                with tarfile.open(archive_path, "w:gz") as archive:
                    for name in ("assistant_app/__main__.py", "assistant_app/ui.py", "LICENSE"):
                        item = tarfile.TarInfo(root + "/" + name)
                        item.size = 0
                        archive.addfile(item, io.BytesIO(b""))
                findings, _ = distribution.inspect_archive(archive_path)
                self.assertTrue(findings, root)

    def test_index_is_scanned_even_when_working_file_was_deleted(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temp:
            fake = ("sk-ant-" + "A" * 45).encode()
            outputs = [b"notes.txt\0", b"100644 abc 0\tnotes.txt\0", str(len(fake)).encode(), fake]
            with (
                patch.object(repository, "ROOT", Path(temp)),
                patch.object(
                    repository.subprocess, "run", side_effect=[SimpleNamespace(stdout=data) for data in outputs]
                ),
            ):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    result = repository.main()
            self.assertEqual(result, 1)
            self.assertIn("(index)", output.getvalue())
            self.assertNotIn(fake.decode(), output.getvalue())


if __name__ == "__main__":
    unittest.main()
