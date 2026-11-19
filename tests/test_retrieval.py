"""Synthetic fixtures only: native Windows integration plus injected handle races."""

import hashlib
import os
import tempfile
import threading
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest.mock import patch

from assistant_app.domain.errors import AppError
from assistant_app.retrieval import RetrievalLimits, RetrievalService, WindowsDocumentLoader
from assistant_app.retrieval.lexical import LexicalIndex
from assistant_app.retrieval.reader import HandleInfo, WindowsAPI, check_document_name, safe_path


class FakeAPI:
    """No filesystem reads; simulates the low-level Windows handle boundary."""

    def __init__(self):
        self.data = b"alpha\nbeta alpha\n"
        self.root = r"C:\fixtures"
        self.path = self.root + r"\Note.txt"
        self.infos = {
            "C:\\": HandleInfo(0x10, (1, 1), 0, 0, 0),
            self.root: HandleInfo(0x10, (1, 2), 0, 0, 0),
            self.path: HandleInfo(0, (1, 3), len(self.data), 1, 1),
        }
        self.finals = {}
        self.opened = []
        self.closed = []
        self.reads = 0
        self.before_read = None
        self.on_info = None
        self.remote = False

    def local(self, path):
        return not self.remote

    def open(self, path, directory):
        if path not in self.infos:
            raise AppError("Missing fixture")
        self.opened.append(path)
        return path

    def close(self, handle):
        self.closed.append(handle)

    def info(self, handle):
        if self.on_info:
            self.on_info(handle)
        return self.infos[handle]

    def final_path(self, handle):
        return self.finals.get(handle, handle)

    def read(self, handle, maximum):
        self.reads += 1
        if self.before_read:
            self.before_read()
        return self.data[: maximum + 1]


class HandleTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.loader = WindowsDocumentLoader(api=self.api)
        self.root = self.loader.approve_root(self.api.root)

    def read(self):
        return self.loader.read(self.root, self.api.path, 1000)

    def test_valid_read_hash_and_handles_closed(self):
        doc = self.read()
        self.assertEqual(doc.sha256, hashlib.sha256(self.api.data).hexdigest())
        self.assertEqual(doc.relative_path, "Note.txt")
        self.assertCountEqual(self.api.closed, self.api.opened)

    def test_outside_handle_paths_rejected_before_bytes(self):
        for final in (r"C:\elsewhere\Note.txt", r"C:\fixtures-other\Note.txt", r"D:\fixtures\Note.txt"):
            with self.subTest(final=final):
                self.api.finals[self.api.path] = final
                with self.assertRaises(AppError):
                    self.read()
                self.assertEqual(self.api.reads, 0)
        self.assertCountEqual(self.api.closed, self.api.opened)

    def test_reparse_each_ancestor_and_file_before_bytes(self):
        for path in self.api.infos:
            with self.subTest(path=path):
                original = self.api.infos[path]
                self.api.infos[path] = replace(original, attributes=original.attributes | 0x400)
                with self.assertRaises(AppError):
                    self.read()
                self.api.infos[path] = original
                self.assertEqual(self.api.reads, 0)
        self.assertCountEqual(self.api.closed, self.api.opened)

    def test_root_replaced_before_bytes(self):
        self.api.infos[self.api.root] = replace(self.api.infos[self.api.root], identity=(1, 88))
        with self.assertRaises(AppError):
            self.read()
        self.assertEqual(self.api.reads, 0)

    def test_ancestor_final_path_rejected_before_bytes(self):
        self.api.finals[self.api.root] = r"C:\elsewhere"
        with self.assertRaises(AppError):
            self.read()
        self.assertEqual(self.api.reads, 0)

    def test_after_read_path_root_and_file_changes_rejected(self):
        def change_path():
            self.api.finals[self.api.path] = r"C:\elsewhere\Note.txt"

        def change_root():
            self.api.infos[self.api.root] = replace(self.api.infos[self.api.root], identity=(1, 90))

        def change_file():
            self.api.infos[self.api.path] = replace(self.api.infos[self.api.path], modified=9)

        for mutate in (change_path, change_root, change_file):
            with self.subTest(mutate=mutate.__name__):
                self.setUp()
                self.api.before_read = mutate
                with self.assertRaises(AppError):
                    self.read()
                self.assertCountEqual(self.api.closed, self.api.opened)

    def test_oversized_file_checked_before_bytes(self):
        with self.assertRaises(AppError):
            self.loader.read(self.root, self.api.path, 2)
        self.assertEqual(self.api.reads, 0)

    def test_strict_utf8_and_binary_rejected(self):
        for data in (b"\xff", b"a\x00b"):
            self.api.data = data
            self.api.infos[self.api.path] = replace(self.api.infos[self.api.path], size=len(data))
            with self.assertRaises(AppError):
                self.read()

    def test_remote_and_nonwindows_fail_closed(self):
        self.api.remote = True
        with self.assertRaises(AppError):
            self.read()
        with patch("assistant_app.retrieval.reader.os.name", "posix"):
            with self.assertRaises(AppError):
                WindowsAPI()

    def test_path_and_name_policy(self):
        for path in (r"\\server\share\x.txt", r"\\?\C:\x.txt", "C:\\x.txt:stream", "C:\\x.txt "):
            with self.subTest(path=path), self.assertRaises(AppError):
                safe_path(path)
        for name in (
            ".env",
            ".env.txt",
            "credentials.json",
            "secrets.txt",
            "token.json",
            "private-key.txt",
            "node_modules/a.txt",
            ".git/config.txt",
            "dist/a.txt",
            "a.py",
            "package-lock.json",
        ):
            with self.subTest(name=name), self.assertRaises(AppError):
                check_document_name("C:\\fixtures\\" + name)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.service = RetrievalService(loader=WindowsDocumentLoader(api=self.api))
        self.roots = self.service.set_roots([self.api.root])

    def ingest(self):
        return self.service.ingest([self.api.path])

    def test_index_clear_failure_cannot_return_revoked_snippets(self):
        for revoke in (
            lambda service: service.set_roots([]),
            lambda service: service.revoke_roots([service.roots[0].root_id]),
        ):
            index = LexicalIndex()
            service = RetrievalService(loader=WindowsDocumentLoader(api=self.api), index=index)
            service.set_roots([self.api.root])
            service.ingest([self.api.path])
            with patch.object(index, "clear", side_effect=RuntimeError("private diagnostic")):
                with self.assertRaises(AppError):
                    revoke(service)
            self.assertEqual(service.roots, ())
            for operation in (
                lambda: service.search("alpha"),
                lambda: service.build_context("alpha"),
                lambda: service.ingest([]),
            ):
                with self.assertRaises(AppError):
                    operation()
            service.clear()
            self.assertEqual(service.search("alpha"), ())

    def test_index_replace_failure_disables_stale_search_until_clear(self):
        index = LexicalIndex()
        service = RetrievalService(loader=WindowsDocumentLoader(api=self.api), index=index)
        service.set_roots([self.api.root])
        service.ingest([self.api.path])
        self.api.data = self.api.data.replace(b"alpha", b"omega")
        with patch.object(index, "replace", side_effect=RuntimeError("private diagnostic")):
            with self.assertRaises(AppError):
                service.search("alpha")
        with self.assertRaises(AppError):
            service.search("alpha")
        service.clear()
        self.assertEqual(service.search("alpha"), ())
        service.ingest([self.api.path])
        self.assertTrue(service.search("omega"))

    def test_failed_ingest_index_update_requires_explicit_recovery(self):
        index = LexicalIndex()
        service = RetrievalService(loader=WindowsDocumentLoader(api=self.api), index=index)
        service.set_roots([self.api.root])
        replace_index = index.replace

        def partial_update(chunks):
            replace_index(chunks)
            raise RuntimeError("Failed after mutation")

        with patch.object(index, "replace", side_effect=partial_update):
            with self.assertRaises(AppError):
                service.ingest([self.api.path])
        with self.assertRaises(AppError):
            service.build_context("alpha")
        service.clear()
        self.assertEqual(service.search("alpha"), ())

    def test_explicit_ingestion_immutable_citations_and_context(self):
        self.assertEqual(self.api.reads, 0)
        self.assertEqual(self.service.search("alpha"), ())
        info = self.ingest()[0]
        hits = self.service.search("alpha")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].citation.root_id, self.roots[0].root_id)
        self.assertEqual(hits[0].citation.relative_path, "Note.txt")
        self.assertEqual((hits[0].citation.start_line, hits[0].citation.end_line), (1, 2))
        self.assertEqual(hits[0].citation.sha256, info.sha256)
        with self.assertRaises(FrozenInstanceError):
            hits[0].citation.root_id = "other"
        context = self.service.build_context("alpha")
        self.assertIn(info.sha256, context.text)
        self.assertEqual(context.hits, hits)
        self.assertEqual(self.service.build_context("alpha", max_chars=1).text, "")
        self.assertEqual(self.service.search("absent"), ())

    def test_clear_revoke_and_new_roots_discard(self):
        self.ingest()
        self.service.clear()
        self.assertEqual(self.service.roots, self.roots)
        self.assertEqual(self.service.search("alpha"), ())
        self.ingest()
        self.assertEqual(self.service.revoke_roots([self.roots[0].root_id]), ())
        self.assertEqual(self.service.search("alpha"), ())
        with self.assertRaises(AppError):
            self.ingest()
        roots = self.service.set_roots([self.api.root])
        self.assertNotEqual(roots[0].root_id, self.roots[0].root_id)
        self.ingest()
        self.service.set_roots([])
        self.assertEqual(self.service.search("alpha"), ())

    def test_stale_hash_even_same_metadata_is_removed_not_refreshed(self):
        self.ingest()
        original = self.api.data
        self.api.data = self.api.data.replace(b"alpha", b"omega")
        self.assertEqual(self.service.search("alpha"), ())
        self.api.data = original
        self.assertEqual(self.service.search("alpha"), ())
        self.ingest()
        self.assertTrue(self.service.search("alpha"))

    def test_search_reuses_reader_and_invalidates_path_escape(self):
        self.ingest()
        self.api.finals[self.api.path] = r"C:\outside\Note.txt"
        self.assertEqual(self.service.search("alpha"), ())
        self.assertEqual(self.api.reads, 1)  # Invalid search never reads any bytes.

    def test_search_invalidates_root_replacement_and_file_replacement(self):
        for path in (self.api.root, self.api.path):
            with self.subTest(path=path):
                self.setUp()
                self.ingest()
                self.api.infos[path] = replace(self.api.infos[path], identity=(1, 99))
                self.assertEqual(self.service.search("alpha"), ())

    def test_revocation_during_ingest_or_search_cannot_return_data(self):
        for operation in (self.ingest, lambda: self.service.search("alpha")):
            self.api.before_read = None
            self.service.set_roots([self.api.root])
            self.ingest()
            self.api.before_read = lambda: self.service.set_roots([])
            with self.assertRaises(AppError):
                operation()
            self.assertEqual(self.service.roots, ())
            self.assertEqual(self.service.search("alpha"), ())

    def test_context_rechecks_generation_after_formatting(self):
        self.ingest()
        import json

        original = json.dumps

        def revoke(*args, **kwargs):
            self.service.clear()
            return original(*args, **kwargs)

        with patch("assistant_app.retrieval.service.json.dumps", side_effect=revoke):
            with self.assertRaises(AppError):
                self.service.build_context("alpha")

    def test_concurrent_clear_does_not_wait_for_reader_and_cancels_result(self):
        self.ingest()
        entered, resume = threading.Event(), threading.Event()
        errors = []

        def pause():
            entered.set()
            if not resume.wait(5):
                raise AssertionError("Test reader timed out")

        def search():
            try:
                self.service.search("alpha")
            except Exception as error:
                errors.append(error)

        self.api.before_read = pause
        worker = threading.Thread(target=search)
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.service.clear()
        finally:
            resume.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AppError)

    def test_limits_and_batch_atomicity(self):
        self.service = RetrievalService(
            loader=WindowsDocumentLoader(api=self.api), limits=RetrievalLimits(max_documents=1, max_total_bytes=4)
        )
        self.service.set_roots([self.api.root])
        with self.assertRaises(AppError):
            self.ingest()
        self.assertEqual(self.service.search("alpha"), ())
        with self.assertRaises(AppError):
            self.service.ingest([self.api.path, self.api.path])
        for kwargs in ({"limit": -1}, {"limit": 21}, {"limit": True}):
            with self.assertRaises(AppError):
                self.service.search("alpha", **kwargs)
        with self.assertRaises(AppError):
            self.service.search("x" * 2001)
        with self.assertRaises(AppError):
            self.service.build_context("alpha", max_chars=12001)
        with self.assertRaises(AppError):
            self.service.set_roots([self.api.root, self.api.root + r"\child"])
        self.assertEqual(len(self.service.roots), 1)

    def test_long_line_citations_and_chunk_limits(self):
        self.api.data = b"alpha alpha alpha\r\nbeta\r\ngamma"
        self.api.infos[self.api.path] = replace(self.api.infos[self.api.path], size=len(self.api.data))
        self.service = RetrievalService(
            loader=WindowsDocumentLoader(api=self.api), limits=RetrievalLimits(chunk_chars=6)
        )
        self.service.set_roots([self.api.root])
        self.ingest()
        hits = self.service.search("alpha", limit=20)
        self.assertEqual(len(hits), 3)
        self.assertTrue(all((hit.citation.start_line, hit.citation.end_line) == (1, 1) for hit in hits))
        beta = self.service.search("beta")[0]
        self.assertEqual((beta.citation.start_line, beta.citation.end_line), (2, 3))
        self.assertEqual(beta.citation.sha256, hashlib.sha256(self.api.data).hexdigest())
        for limits in (
            RetrievalLimits(chunk_chars=6, max_chunks_per_document=1),
            RetrievalLimits(chunk_chars=6, max_chunks=1),
        ):
            service = RetrievalService(loader=WindowsDocumentLoader(api=self.api), limits=limits)
            service.set_roots([self.api.root])
            with self.assertRaises(AppError):
                service.ingest([self.api.path])


@unittest.skipUnless(os.name == "nt", "Native Windows secure reader")
class WindowsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="retrieval-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.file = self.root / "Notes.md"
        self.file.write_bytes(b"first\nsecond needle\nthird\n")
        self.service = RetrievalService()
        self.service.set_roots([self.root])

    def test_native_ingest_edit_delete_and_reingest(self):
        self.service.ingest([self.file])
        self.assertEqual(self.service.search("needle")[0].citation.relative_path, "Notes.md")
        self.file.write_text("changed needle", encoding="utf-8")
        self.assertEqual(self.service.search("needle"), ())
        self.service.ingest([self.file])
        self.assertTrue(self.service.search("needle"))
        self.file.unlink()
        self.assertEqual(self.service.search("needle"), ())

    def test_directory_outscope_and_non_utf8_rejected(self):
        with self.assertRaises(AppError):
            self.service.ingest([self.root])
        with tempfile.TemporaryDirectory(prefix="retrieval-outside-") as outside:
            path = Path(outside) / "outside.txt"
            path.write_text("needle", encoding="utf-8")
            with self.assertRaises(AppError):
                self.service.ingest([path])
        self.file.write_bytes(b"\xff")
        with self.assertRaises(AppError):
            self.service.ingest([self.file])

    def test_native_open_writer_and_delete_are_denied_while_handle_held(self):
        loader = WindowsDocumentLoader()
        root = loader.approve_root(str(self.root))
        original = loader.api.read

        def read(handle, maximum):
            with self.assertRaises(OSError):
                self.file.write_bytes(b"changed")
            with self.assertRaises(OSError):
                self.file.unlink()
            return original(handle, maximum)

        with patch.object(loader.api, "read", side_effect=read):
            self.assertIn("needle", loader.read(root, str(self.file), 1000).text)

    def test_native_ancestor_rename_denied_during_read(self):
        loader = WindowsDocumentLoader()
        root = loader.approve_root(str(self.root))
        original = loader.api.read

        def read(handle, maximum):
            with self.assertRaises(OSError):
                self.root.rename(self.root.with_name(self.root.name + "-moved"))
            return original(handle, maximum)

        with patch.object(loader.api, "read", side_effect=read):
            loader.read(root, str(self.file), 1000)


if __name__ == "__main__":
    unittest.main()
