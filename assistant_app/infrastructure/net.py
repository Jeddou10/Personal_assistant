"""Bounded, single-attempt HTTP transport. Never expose remote error bodies."""

from __future__ import annotations

import http.client
import json
import math
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from assistant_app.domain.errors import AppError

MAX_BYTES = 2 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: dict[str, str] | None, newurl: Any
    ) -> Any:
        return None


class _Deadline:
    """Only the caller performs I/O; the timer can only interrupt its socket.

    OS hostname resolution is synchronous and cannot be interrupted here. Check
    the deadline after it returns so an expired request can never be dispatched.
    """

    def __init__(self, timeout: float) -> None:
        self.end = time.monotonic() + timeout
        self.lock = threading.Lock()
        self.sock = None
        self.timer = threading.Timer(timeout, self.interrupt)

    def remaining(self) -> float:
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        return remaining

    def interrupt(self) -> None:
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def attach(self, sock: socket.socket) -> None:
        with self.lock:
            self.sock = sock
            try:
                sock.settimeout(self.remaining())
            except Exception:
                sock.close()
                raise

    def connection(self, base: Any) -> Any:
        deadline = self

        class Connection(base):
            def connect(self) -> None:
                self.timeout = deadline.remaining()
                if base is http.client.HTTPSConnection:
                    http.client.HTTPConnection.connect(self)
                    self.sock = self._context.wrap_socket(
                        self.sock, server_hostname=self.host, do_handshake_on_connect=False
                    )
                    deadline.attach(self.sock)
                    self.sock.do_handshake()
                else:
                    super().connect()
                deadline.attach(self.sock)

            def send(self, data: Any) -> None:
                deadline.remaining()
                super().send(data)

        def create_connection(address: Any, timeout: float, source_address: Any = None) -> Any:
            # create_connection() resets the timeout for every resolved address.
            # Own the loop so all addresses share one deadline, and register each
            # socket before connect so the deadline timer can interrupt it.
            host, port = address
            addresses = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
            last_error = None
            for family, kind, protocol, _, target in addresses:
                deadline.remaining()
                sock = socket.socket(family, kind, protocol)
                try:
                    if source_address:
                        sock.bind(source_address)
                    deadline.attach(sock)
                    deadline.remaining()
                    sock.connect(target)
                    deadline.remaining()
                    return sock
                except OSError as exc:
                    sock.close()
                    last_error = exc
                    deadline.remaining()
            raise last_error or OSError("No addresses available")

        def factory(*args, **kwargs) -> Any:
            deadline.remaining()
            connection = Connection(*args, **kwargs)
            connection._create_connection = create_connection
            return connection

        return factory


class _HTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, deadline: Any) -> None:
        super().__init__()
        self.connection = deadline.connection(http.client.HTTPConnection)

    def http_open(self, request: Any) -> Any:
        return self.do_open(self.connection, request)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, deadline: Any) -> None:
        super().__init__()
        self.connection = deadline.connection(http.client.HTTPSConnection)

    def https_open(self, request: Any) -> Any:
        return self.do_open(self.connection, request, context=self._context)


def request_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    data: Any = None,
    timeout: float = 30,
    local: bool = False,
    form: bool = False,
) -> dict:
    """HTTPS by default; local=True permits only literal loopback HTTP."""
    uncertain = False
    try:
        parts = urllib.parse.urlsplit(url)
        if (
            parts.username is not None
            or parts.password is not None
            or parts.fragment
            or not parts.hostname
            or any(ord(c) <= 32 or ord(c) == 127 for c in url)
        ):
            raise ValueError()
        _ = parts.port
        if local:
            if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "::1"):
                raise ValueError()
        elif parts.scheme != "https":
            raise ValueError()
        method = method.upper()
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
            raise ValueError()
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
        request_headers = dict(headers or {})
        body = None
        if data is not None:
            body = (
                urllib.parse.urlencode(data).encode("utf-8")
                if form
                else json.dumps(data, allow_nan=False).encode("utf-8")
            )
            if len(body) > MAX_BYTES:
                raise ValueError()
            request_headers.setdefault(
                "Content-Type", "application/x-www-form-urlencoded" if form else "application/json"
            )
        request_headers.setdefault("Accept", "application/json")
        request = urllib.request.Request(url, body, request_headers, method=method)
    except Exception:
        raise AppError("Invalid network request.") from None
    deadline = _Deadline(timeout)
    deadline.timer.start()
    try:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(), _HTTPHandler(deadline), _HTTPSHandler(deadline)
        )
        uncertain = method not in ("GET", "HEAD")
        with opener.open(request, timeout=deadline.remaining()) as response:
            deadline.remaining()
            status = response.status
            if not 200 <= status < 300:
                raise AppError("Remote request failed.", status=status, uncertain=uncertain and status >= 500)
            chunks, size = [], 0
            while size <= MAX_BYTES:
                deadline.remaining()
                chunk = response.read1(min(65536, MAX_BYTES + 1 - size))
                deadline.remaining()
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
            raw = b"".join(chunks)
            if len(raw) > MAX_BYTES:
                raise AppError("Remote response exceeded the size limit.", uncertain=uncertain)
            if not raw and status == 204:
                return {}
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError()
            return result
    except AppError:
        raise
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise AppError(
            "Remote request failed.", status=status, uncertain=uncertain and (status >= 500 or status == 408)
        ) from None
    except Exception:
        raise AppError("Remote request failed or returned invalid data.", uncertain=uncertain) from None
    finally:
        deadline.timer.cancel()
        deadline.timer.join()
