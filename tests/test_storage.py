import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from assistant_app.domain.errors import AppError
from assistant_app.infrastructure.storage import Settings, Store, _dpapi


@unittest.skipUnless(os.name == "nt", "DPAPI requires Windows")
class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ["LOCALAPPDATA"])
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_dpapi_roundtrip(self):
        private = "private unicode \u2603".encode()
        encrypted = _dpapi(private)
        self.assertNotIn(private, encrypted)
        self.assertEqual(_dpapi(encrypted, True), private)
        with self.assertRaises(AppError):
            _dpapi(b"corrupt data", True)

    def test_defaults_and_revision(self):
        self.assertEqual(self.store.load_settings(), Settings())
        self.assertEqual(self.store.revision, 0)
        self.store.save_settings(Settings())
        self.assertEqual(self.store.revision, 0)
        self.store.save_settings(replace(Settings(), model="different"))
        self.assertEqual(self.store.revision, 1)
        self.store.set_secret("key", "private-key")
        self.assertEqual(self.store.revision, 2)
        self.store.set_secret("key", "private-key")
        self.store.set_secret("google_tokens", "refresh-token")
        self.assertEqual(self.store.revision, 2)
        self.assertEqual(Store(self.temp.name).get_secret("key"), "private-key")
        self.store.set_secret("key", "")
        self.assertEqual(self.store.revision, 3)
        self.assertEqual(self.store.get_secret("key"), "")

    def test_private_data_not_plaintext(self):
        self.store.set_secret("key", "credential-secret")
        self.store.save_preferences(["private-preference", "\u2603"])
        self.assertEqual(self.store.load_preferences(), ["private-preference", "\u2603"])
        for path in Path(self.temp.name).iterdir():
            self.assertNotIn(b"credential-secret", path.read_bytes())
            self.assertNotIn(b"private-preference", path.read_bytes())
        self.store.save_preferences([])
        self.assertEqual(self.store.load_preferences(), [])

    def test_failed_replace_preserves_previous(self):
        self.store.set_secret("key", "original")
        with patch("assistant_app.infrastructure.storage.os.replace", side_effect=OSError("private")):
            with self.assertRaises(AppError):
                self.store.set_secret("key", "replacement")
        self.assertEqual(self.store.get_secret("key"), "original")
        self.assertEqual([p.name for p in Path(self.temp.name).iterdir()], ["state.json"])

    def test_oversized_config_preserves_previous(self):
        self.store.save_settings(Settings(model="original"))
        with self.assertRaises(AppError):
            self.store.save_settings(Settings(model="x" * (4 * 1024 * 1024)))
        self.assertEqual(self.store.load_settings().model, "original")

    def test_corruption_fails_closed(self):
        (Path(self.temp.name) / "state.json").write_text("not json")
        with self.assertRaises(AppError):
            self.store.load_settings()

    def test_reject_workspace_and_invalid_values(self):
        with self.assertRaises(AppError):
            Store(Path(__file__).resolve().parents[1])
        with self.assertRaises(AppError):
            self.store.save_settings(Settings(model=None))
        with self.assertRaises(AppError):
            self.store.save_preferences(["ok", 1])


class StorageWorkspaceTests(unittest.TestCase):
    def test_workspace_rejected_even_when_localappdata_contains_source(self):
        workspace = Path(__file__).resolve().parents[1]
        with patch.dict(os.environ, {"LOCALAPPDATA": str(workspace.parent)}):
            for target in (workspace, workspace / "runtime-data", workspace / "assistant_app"):
                with self.subTest(target=target), self.assertRaises(AppError):
                    Store(target)

    def test_legacy_exports_share_canonical_identity(self):
        from assistant_app import storage as legacy
        from assistant_app.domain.models import Settings as DomainSettings

        self.assertIs(legacy.Store, Store)
        self.assertIs(legacy.Settings, DomainSettings)
