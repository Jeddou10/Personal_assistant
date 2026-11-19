"""TaskRunner lifecycle tests without Tk windows or remote operations."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from assistant_app.domain.errors import AppError
from assistant_app.frontend.tasks import TaskRunner


class FakeRoot:
    def __init__(self):
        self.callbacks = {}
        self.cancelled = []
        self.serial = 0

    def after(self, delay, callback):
        assert delay == 100
        self.serial += 1
        self.callbacks[self.serial] = callback
        return self.serial

    def after_cancel(self, ident):
        self.cancelled.append(ident)
        self.callbacks.pop(ident, None)

    def tick(self):
        ident = next(iter(self.callbacks))
        self.callbacks.pop(ident)()


class FakeThread:
    def __init__(self, target, daemon):
        self.target = target
        self.alive = False
        self.joins = []

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        assert not self.alive, "Tk must never join a live worker"
        assert timeout == 0, "Tk must never block on join"
        self.joins.append(timeout)

    def finish(self):
        self.target()
        self.alive = False


class TaskRunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = FakeRoot()
        self.chat = Mock()
        self.app = SimpleNamespace(
            root=self.root,
            chat_view=self.chat,
            engine=SimpleNamespace(session=1),
            _idle=Mock(return_value=True),
            _append=Mock(),
            _refresh_proposals=Mock(),
        )
        self.now = 10.0
        self.runner = TaskRunner(self.app, clock=lambda: self.now)
        self.runner.poll_id = self.root.after(100, self.runner._poll)
        patcher = patch("assistant_app.frontend.tasks.threading.Thread", FakeThread)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pending_event_waits_without_live_join_and_ticks_elapsed(self):
        done = Mock()
        self.assertTrue(self.runner._run(lambda: "answer", done, "Reading"))
        worker = self.runner._worker_thread
        worker.target()  # Event queued, but thread has not exited yet.
        self.now = 12.5
        self.root.tick()
        self.assertTrue(self.runner.busy)
        self.assertEqual(worker.joins, [])
        done.assert_not_called()
        self.chat.update_busy.assert_called_once_with(2.5)
        self.assertEqual(len(self.runner._pending_event), 4)
        self.assertFalse(self.runner._run(Mock()))
        worker.alive = False
        self.root.tick()
        done.assert_called_once_with("answer")
        self.assertEqual(worker.joins, [0])
        self.assertIsNone(self.runner._worker_thread)
        self.assertFalse(self.runner.busy)
        self.chat.set_busy.assert_any_call(True, "Reading")
        self.chat.set_busy.assert_called_with(False)
        self.assertEqual(len(self.root.callbacks), 1)

    def test_stale_result_and_error_drop_all_callbacks_but_clear_busy(self):
        for error in (None, "Safe error"):
            with self.subTest(error=error):
                done, on_error = Mock(), Mock()
                self.runner._run(Mock(), done, on_error=on_error)
                worker = self.runner._worker_thread
                self.runner.events.put((self.app.engine.session, done, "old", error))
                worker.alive = False
                self.app.engine.session += 1
                self.root.tick()
                done.assert_not_called()
                on_error.assert_not_called()
                self.app._append.assert_not_called()
                self.app._refresh_proposals.assert_not_called()
                self.assertFalse(self.runner.busy)
                self.assertIsNone(self.runner._worker_thread)
                self.chat.set_busy.assert_called_with(False)

    def test_start_failure_restores_state_and_redacts_without_retry(self):
        operation, on_error = Mock(), Mock(side_effect=RuntimeError("private callback"))
        with patch.object(FakeThread, "start", side_effect=RuntimeError("private start")) as start:
            self.assertFalse(self.runner._run(operation, on_error=on_error))
        start.assert_called_once()
        operation.assert_not_called()
        self.assertFalse(self.runner.busy)
        self.assertIsNone(self.runner._worker_thread)
        self.chat.set_busy.assert_called_with(False)
        on_error.assert_called_once()
        messages = str(self.app._append.call_args_list)
        self.assertNotIn("private start", messages)
        self.assertNotIn("private callback", messages)
        self.assertIn("No private diagnostic data was logged.", messages)
        self.root.tick()
        self.assertEqual(len(self.root.callbacks), 1)

    def test_errors_are_delivered_only_by_poll_and_app_errors_preserved(self):
        for exc in (AppError("Reconnect your account"), RuntimeError("private-token")):
            with self.subTest(exc=type(exc)):
                operation = Mock(side_effect=exc)
                on_error = Mock()
                self.runner._run(operation, on_error=on_error)
                self.runner._worker_thread.finish()
                on_error.assert_not_called()
                self.root.tick()
                operation.assert_called_once()
                on_error.assert_called_once()
                error = on_error.call_args.args[0]
                self.assertNotIn("private-token", error)
                if isinstance(exc, AppError):
                    self.assertEqual(error, str(exc))
                else:
                    self.assertIn("No private diagnostic data was logged.", error)

    def test_callback_and_refresh_failures_do_not_stop_polling(self):
        on_error = Mock()
        self.app._refresh_proposals.side_effect = RuntimeError("private refresh")
        self.runner._run(lambda: 42, Mock(side_effect=RuntimeError("private result")), on_error=on_error)
        self.runner._worker_thread.finish()
        self.root.tick()
        self.assertFalse(self.runner.busy)
        on_error.assert_not_called()
        self.assertEqual(len(self.root.callbacks), 1)
        self.assertNotIn("private", str(self.app._append.call_args_list).replace("private diagnostic", "diagnostic"))
        self.root.tick()
        self.assertEqual(len(self.root.callbacks), 1)

    def test_update_and_clear_busy_failures_still_allow_cleanup(self):
        self.chat.update_busy.side_effect = RuntimeError("private tick")
        self.runner._run(lambda: 42)
        self.chat.set_busy.side_effect = RuntimeError("private UI")
        self.app._append.side_effect = RuntimeError("private reporting")
        self.runner._worker_thread.finish()
        self.root.tick()
        self.assertFalse(self.runner.busy)
        self.assertIsNone(self.runner._worker_thread)
        self.assertEqual(len(self.root.callbacks), 1)

    def test_close_refuses_live_worker_then_is_idempotent(self):
        self.runner._run(lambda: 42)
        worker = self.runner._worker_thread
        self.assertFalse(self.runner.close())
        self.assertIs(self.runner._worker_thread, worker)
        self.assertEqual(len(self.root.callbacks), 1)
        worker.finish()
        self.root.tick()
        poll_id = self.runner.poll_id
        self.assertTrue(self.runner.close())
        self.assertTrue(self.runner.close())
        self.assertEqual(self.root.cancelled, [poll_id])
        self.runner._poll()
        self.assertEqual(self.root.callbacks, {})
        self.assertFalse(self.runner._run(Mock()))

    def test_close_inside_callback_prevents_rearming(self):
        self.runner._run(lambda: 42, lambda _: self.runner.close())
        self.runner._worker_thread.finish()
        self.root.tick()
        self.assertEqual(self.root.callbacks, {})
        self.assertIsNone(self.runner.poll_id)
        self.app._refresh_proposals.assert_not_called()

    def test_independent_start_guards(self):
        self.app._idle.return_value = False
        self.assertFalse(self.runner._run(Mock()))
        self.app._idle.return_value = True
        self.runner._worker_thread = FakeThread(Mock(), True)
        self.assertFalse(self.runner._run(Mock()))

    def test_real_worker_error_callback_runs_on_polling_thread(self):
        # Use a real Python thread without creating a Tk interpreter.
        delivered_on = []
        on_error = Mock(side_effect=lambda _: delivered_on.append(threading.get_ident()))
        owner = threading.get_ident()
        with patch("assistant_app.frontend.tasks.threading.Thread", REAL_THREAD):
            self.runner._run(Mock(side_effect=RuntimeError("private worker")), on_error=on_error)
            worker = self.runner._worker_thread
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            on_error.assert_not_called()
            self.root.tick()
        on_error.assert_called_once()
        self.assertEqual(delivered_on, [owner])


REAL_THREAD = threading.Thread

if __name__ == "__main__":
    unittest.main()
