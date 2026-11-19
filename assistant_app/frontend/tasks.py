"""Run operations off Tk, delivering their results on the polling thread."""

import queue
import threading
import time

from assistant_app.domain.errors import AppError

_GENERIC_ERROR = (
    "The operation could not be completed. Check Settings and try again. No private diagnostic data was logged."
)


class TaskRunner:
    def __init__(self, app, clock=time.monotonic):
        self.app = app
        self.busy = False
        self.events = queue.Queue()
        self._worker_thread = None
        self.poll_id = None
        self._clock = clock
        self._started_at = None
        self._pending_event = None
        self._on_error = None
        self._closed = False

    def _report(self, error):
        # Reporting must not prevent cleanup or stop the polling loop.
        try:
            self.app._append("app", error)
        except Exception:
            pass

    def _deliver_error(self, generation, error, on_error):
        if self._closed or generation != self.app.engine.session:
            return
        self._report(error)
        if on_error and not self._closed and generation == self.app.engine.session:
            try:
                on_error(error)
            except AppError as exc:
                self._report(str(exc))
            except Exception:
                self._report(_GENERIC_ERROR)

    def _clear_busy(self):
        self.busy = False
        self._started_at = None
        self._on_error = None
        self.app.chat_view.set_busy(False)

    def _run(self, operation, done=None, label="Working…", on_error=None) -> bool:
        # Do not rely on _idle's UI guard to enforce single-flight execution.
        if self._closed or self.busy or self._worker_thread is not None:
            return False
        if not self.app._idle():
            return False
        generation = self.app.engine.session
        self.busy = True
        self._on_error = on_error

        def work():
            try:
                result = operation()
                error = None
            except AppError as exc:
                result, error = None, str(exc)
            except Exception:
                result, error = None, _GENERIC_ERROR
            self.events.put((generation, done, result, error))

        try:
            self._started_at = self._clock()
            self.app.chat_view.set_busy(True, label)
            self._worker_thread = threading.Thread(target=work, daemon=True)
            self._worker_thread.start()
        except Exception as exc:
            self._worker_thread = None
            try:
                self._clear_busy()
            except Exception:
                self._report(_GENERIC_ERROR)
            self._deliver_error(generation, str(exc) if isinstance(exc, AppError) else _GENERIC_ERROR, on_error)
            return False
        return True

    def _poll(self):
        self.poll_id = None
        if self._closed:
            return
        try:
            if self.busy and self._started_at is not None:
                try:
                    self.app.chat_view.update_busy(max(0, self._clock() - self._started_at))
                except Exception:
                    self._report(_GENERIC_ERROR)
            if self._pending_event is None:
                try:
                    self._pending_event = self.events.get_nowait()
                except queue.Empty:
                    return
            # Queue insertion can precede actual thread exit. Never block Tk.
            if self._worker_thread is not None:
                if self._worker_thread.is_alive():
                    return
                self._worker_thread.join(timeout=0)
                self._worker_thread = None
            generation, callback, result, error = self._pending_event
            self._pending_event = None
            on_error = self._on_error
            try:
                self._clear_busy()
            except Exception:
                self._report(_GENERIC_ERROR)
            if self._closed or generation != self.app.engine.session:
                return
            if error is not None:
                self._deliver_error(generation, error, on_error)
            elif callback:
                try:
                    callback(result)
                except Exception:
                    # The operation succeeded. A display failure must not offer
                    # request recovery that could replay already processed work.
                    self._report(
                        "The operation finished, but its result could not be displayed. "
                        "Do not repeat a change without inspecting its destination."
                    )
            if not self._closed and generation == self.app.engine.session:
                self.app._refresh_proposals()
        except AppError as exc:
            self._report(str(exc))
        except Exception:
            self._report(_GENERIC_ERROR)
        finally:
            if not self._closed:
                self.poll_id = self.app.root.after(100, self._poll)

    def close(self):
        """Refuse shutdown until the operation has finished and been delivered."""
        if self._closed:
            return True
        if self.busy or self._worker_thread is not None:
            return False
        self._closed = True
        if self.poll_id is not None:
            poll_id, self.poll_id = self.poll_id, None
            self.app.root.after_cancel(poll_id)
        return True
