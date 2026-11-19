import http.client
import io
import json
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from assistant_app.infrastructure.net import MAX_BYTES, AppError, _Deadline, request_json


class Response(io.BytesIO):
    status = 200


class NetworkTests(unittest.TestCase):
    def test_json_and_transport_policy(self):
        with patch("assistant_app.infrastructure.net.urllib.request.build_opener") as build:
            build.return_value.open.return_value = Response(b'{"ok":true}')
            self.assertEqual(request_json("POST", "https://example.com", data={"x": 1}), {"ok": True})
            proxy, redirect, http, https = build.call_args.args
            self.assertEqual(proxy.proxies, {})
            self.assertIsNone(redirect.redirect_request(None, None, 302, "", {}, "https://evil.test"))
            req = build.return_value.open.call_args.args[0]
            self.assertEqual(json.loads(req.data), {"x": 1})
            build.return_value.open.assert_called_once()

    def test_reject_unsafe_urls_before_dispatch(self):
        urls = [
            ("http://example.com", False),
            ("https://a:b@example.com", False),
            ("http://localhost:11434", True),
            ("http://127.0.0.2", True),
            ("https://example.com", True),
            ("http://127.0.0.1.evil.test", True),
            ("https://example.com\n", False),
        ]
        with patch("assistant_app.infrastructure.net.urllib.request.build_opener") as build:
            for url, local in urls:
                with self.subTest(url=url), self.assertRaises(AppError):
                    request_json("GET", url, local=local)
            build.assert_not_called()

    def test_loopback_and_form(self):
        for url in ("http://127.0.0.1:11434/api", "http://[::1]:11434/api"):
            with patch("assistant_app.infrastructure.net.urllib.request.build_opener") as build:
                build.return_value.open.return_value = Response(b"{}")
                request_json("POST", url, data={"x": "a b"}, form=True, local=True)
                self.assertEqual(build.return_value.open.call_args.args[0].data, b"x=a+b")

    def test_errors_sanitized_and_not_retried(self):
        for code in (302, 401, 429, 500):
            with patch("assistant_app.infrastructure.net.urllib.request.build_opener") as build:
                build.return_value.open.side_effect = HTTPError(
                    "secret-url", code, "secret-body", {}, io.BytesIO(b"private")
                )
                with self.assertRaises(AppError) as caught:
                    request_json("POST", "https://example.com")
                self.assertEqual(caught.exception.status, code)
                self.assertEqual(caught.exception.uncertain, code >= 500)
                self.assertNotIn("secret", str(caught.exception))
                build.return_value.open.assert_called_once()

    def test_ambiguous_failure(self):
        for method in ("GET", "POST", "PATCH", "DELETE"):
            with patch("assistant_app.infrastructure.net.urllib.request.build_opener") as build:
                build.return_value.open.side_effect = URLError("private credential")
                with self.assertRaises(AppError) as caught:
                    request_json(method, "https://example.com")
                self.assertEqual(caught.exception.uncertain, method != "GET")
                self.assertNotIn("private", str(caught.exception))

    def test_response_limit_and_invalid_json(self):
        for raw in (b"x" * (MAX_BYTES + 1), b"[]", b"not json", b"\xff"):
            with patch("assistant_app.infrastructure.net.urllib.request.build_opener") as build:
                build.return_value.open.return_value = Response(raw)
                with self.assertRaises(AppError) as caught:
                    request_json("POST", "https://example.com")
                self.assertTrue(caught.exception.uncertain)

    def test_empty_delete(self):
        with patch("assistant_app.infrastructure.net.urllib.request.build_opener") as build:
            response = Response(b"")
            response.status = 204
            build.return_value.open.return_value = response
            self.assertEqual(request_json("DELETE", "https://example.com"), {})

    def test_elapsed_deadline_including_headers_body_and_chunk_framing(self):
        # Each byte arrives within the socket inactivity timeout, but the whole
        # response must still expire. The only server thread is joined below.
        for stage in ("headers", "body", "chunk_header", "stall"):
            for method in ("GET", "POST"):
                with self.subTest(stage=stage, method=method):
                    listener = socket.socket()
                    listener.bind(("127.0.0.1", 0))
                    listener.listen(1)
                    listener.settimeout(2)
                    stop = threading.Event()
                    requests = []

                    def serve():
                        try:
                            conn, _ = listener.accept()
                            with conn:
                                conn.settimeout(2)
                                request = b""
                                while b"\r\n\r\n" not in request:
                                    request += conn.recv(4096)
                                requests.append(request)
                                if stage == "headers":
                                    stream = b"HTTP/1.1 200 OK\r\nX-Slow: " + b"x" * 100
                                elif stage == "chunk_header":
                                    conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
                                    stream = b"1;" + b"x" * 100
                                else:
                                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n")
                                    stream = b" " * 100 if stage == "body" else b""
                                for byte in stream:
                                    conn.sendall(bytes([byte]))
                                    if stop.wait(0.025):
                                        break
                                stop.wait(2)
                        except OSError:
                            pass

                    server = threading.Thread(target=serve)
                    server.start()
                    start = time.monotonic()
                    try:
                        with self.assertRaises(AppError) as caught:
                            request_json(
                                method, f"http://127.0.0.1:{listener.getsockname()[1]}", local=True, timeout=0.2
                            )
                        elapsed = time.monotonic() - start
                        self.assertLess(elapsed, 1.0)
                        self.assertGreater(elapsed, 0.15)
                        self.assertEqual(caught.exception.uncertain, method == "POST")
                        self.assertEqual(len(requests), 1)
                    finally:
                        stop.set()
                        listener.close()
                        server.join(3)
                    self.assertFalse(server.is_alive())

    def test_opener_time_consumes_read_budget_and_timer_is_joined(self):
        before = set(threading.enumerate())
        with patch("assistant_app.infrastructure.net.urllib.request.build_opener") as build:

            def open_response(*args, **kwargs):
                time.sleep(0.06)
                return Response(b"{}")

            build.return_value.open.side_effect = open_response
            with self.assertRaises(AppError) as caught:
                request_json("POST", "https://example.com", timeout=0.03)
            self.assertTrue(caught.exception.uncertain)
            build.return_value.open.assert_called_once()
        self.assertEqual(set(threading.enumerate()), before)

    def test_expired_connect_cannot_dispatch_a_write(self):
        deadline = _Deadline(0.03)

        def slow_resolve(*args, **kwargs):
            time.sleep(0.06)
            return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 80))]

        with (
            patch("assistant_app.infrastructure.net.socket.getaddrinfo", side_effect=slow_resolve),
            patch("assistant_app.infrastructure.net.socket.socket") as make_socket,
        ):
            connection = deadline.connection(http.client.HTTPConnection)("example.com")
            try:
                with self.assertRaises(TimeoutError):
                    connection.request("POST", "/", body=b"write")
                make_socket.assert_not_called()
            finally:
                connection.close()

    def test_resolved_addresses_share_one_deadline(self):
        now = [100.0]
        first, second = Mock(), Mock()
        limits = []
        first.settimeout.side_effect = lambda value: limits.append(value)
        second.settimeout.side_effect = lambda value: limits.append(value)

        def first_failure(target):
            self.assertIs(deadline.sock, first)
            now[0] += 0.04
            raise OSError("First address failed")

        def second_failure(target):
            self.assertIs(deadline.sock, second)
            now[0] += limits[-1] + 0.01
            raise TimeoutError()

        first.connect.side_effect = first_failure
        second.connect.side_effect = second_failure
        addresses = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", port)) for port in (81, 82, 83)]
        with (
            patch("assistant_app.infrastructure.net.time.monotonic", side_effect=lambda: now[0]),
            patch("assistant_app.infrastructure.net.socket.getaddrinfo", return_value=addresses),
            patch("assistant_app.infrastructure.net.socket.socket", side_effect=[first, second]) as make_socket,
        ):
            deadline = _Deadline(0.1)
            connection = deadline.connection(http.client.HTTPConnection)("example.com")
            start = time.monotonic()
            with self.assertRaises(TimeoutError):
                connection.request("POST", "/", body=b"write")
            self.assertLess(time.monotonic() - start, 0.18)
            self.assertEqual(make_socket.call_count, 2)
            self.assertAlmostEqual(limits[0], 0.1)
            self.assertAlmostEqual(limits[1], 0.06)
            first.sendall.assert_not_called()
            second.sendall.assert_not_called()
            first.close.assert_called_once()
            second.close.assert_called_once()
