import base64
import hashlib
import io
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, build_opener

from assistant_app.domain.errors import AppError
from assistant_app.infrastructure import oauth
from assistant_app.infrastructure.storage import Settings


class MemoryStore:
    def __init__(self):
        self.secrets = {}
        self.settings = Settings()
        self.revision = 0

    def get_secret(self, name):
        return self.secrets.get(name, "")

    def set_secret(self, name, value):
        if name != "google_tokens" and self.secrets.get(name) != value:
            self.revision += 1
        self.secrets[name] = value

    def load_settings(self):
        return self.settings


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.client = {"client_id": "desktop-id", "client_secret": "desktop-secret"}
        self.tokens = {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_at": time.time() + 3600,
            "email": "me@example.com",
        }
        self.store.set_secret("google_client", json.dumps(self.client))
        self.store.set_secret("google_tokens", json.dumps(self.tokens))

    def test_cached_token_and_account_binding(self):
        with patch.object(oauth, "request_json") as request:
            self.assertEqual(oauth.get_access_token(self.store), "access")
            self.store.settings = replace(self.store.settings, gmail_account="other@example.com")
            with self.assertRaises(AppError):
                oauth.get_access_token(self.store)
            request.assert_not_called()

    def test_refresh_preserves_refresh_token_and_revision(self):
        self.tokens["expires_at"] = 0
        self.store.set_secret("google_tokens", json.dumps(self.tokens))
        revision = self.store.revision
        with patch.object(
            oauth,
            "request_json",
            side_effect=[{"access_token": "new", "expires_in": 3600}, {"emailAddress": "me@example.com"}],
        ) as request:
            self.assertEqual(oauth.get_access_token(self.store), "new")
            self.assertTrue(request.call_args_list[0].kwargs["form"])
        self.assertEqual(self.store.revision, revision)
        self.assertEqual(json.loads(self.store.get_secret("google_tokens"))["refresh_token"], "refresh")

    def test_refresh_identity_mismatch_and_revocation(self):
        self.tokens["expires_at"] = 0
        self.store.set_secret("google_tokens", json.dumps(self.tokens))
        for responses in (
            [{"access_token": "new", "expires_in": 3600}, {"emailAddress": "other@example.com"}],
            [AppError("credential private", status=401)],
        ):
            with patch.object(oauth, "request_json", side_effect=responses):
                with self.assertRaises(AppError) as caught:
                    oauth.get_access_token(self.store)
                self.assertNotIn("private", str(caught.exception))
        self.assertEqual(json.loads(self.store.get_secret("google_tokens"))["access_token"], "access")

    def test_connect_stores_verified_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "client.json"
            path.write_text(json.dumps({"installed": {**self.client, "token_uri": "https://evil.test"}}))
            with (
                patch.object(oauth, "_authorize", return_value=("code", "verifier", "http://127.0.0.1:123/callback")),
                patch.object(
                    oauth,
                    "request_json",
                    side_effect=[
                        {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
                        {"emailAddress": "me@example.com"},
                    ],
                ) as request,
            ):
                self.assertEqual(oauth.connect_google(self.store, path), "me@example.com")
                self.assertEqual(request.call_args_list[0].args[1], oauth.TOKEN_URL)
                self.assertEqual(request.call_args_list[0].kwargs["data"]["code_verifier"], "verifier")
        self.assertEqual(self.store.settings.gmail_account, "")
        self.assertEqual(self.store.revision, 2)

    def test_callback_state_and_pkce(self):
        captured = {}
        threads = []

        def browser(url):
            params = parse_qs(urlsplit(url).query)
            captured.update(params)

            def callback():
                opener = build_opener(ProxyHandler({}))
                target = params["redirect_uri"][0]
                try:
                    opener.open(target + "?state=wrong&code=evil", timeout=5)
                except HTTPError as exc:
                    captured["invalid_status"] = exc.code
                    exc.close()
                with opener.open(target + "?state=" + params["state"][0] + "&code=good", timeout=5) as response:
                    response.read()

            thread = threading.Thread(target=callback)
            threads.append(thread)
            thread.start()
            return True

        with patch.object(oauth.webbrowser, "open", side_effect=browser):
            code, verifier, redirect = oauth._authorize(self.client)
        for thread in threads:
            thread.join(6)
        self.assertEqual(code, "good")
        self.assertEqual(captured["invalid_status"], 400)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(captured["code_challenge"], [expected])
        self.assertNotIn("client_secret", captured)
        self.assertEqual(set(captured["scope"][0].split()), set(oauth.SCOPES))

    def test_callback_timeout(self):
        with patch.object(oauth.webbrowser, "open", return_value=True), patch.object(oauth, "CALLBACK_TIMEOUT", 0):
            with self.assertRaises(AppError):
                oauth._authorize(self.client)

    def test_callback_errors_do_not_log(self):
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with oauth._CallbackServer(("127.0.0.1", 0), oauth.BaseHTTPRequestHandler) as server:
                try:
                    raise ValueError("private-code")
                except ValueError:
                    server.handle_error(None, ("127.0.0.1", 1))
            self.assertEqual(stderr.getvalue(), "")

    def test_calendar_paging_and_filtering(self):
        item = {"id": "calendar", "summary": "Personal", "accessRole": "owner", "private": "hidden"}
        with patch.object(
            oauth, "request_json", side_effect=[{"items": [item], "nextPageToken": "next"}, {"items": []}]
        ) as request:
            self.assertEqual(oauth.list_calendars(self.store), [{k: item[k] for k in ("id", "summary", "accessRole")}])
            self.assertIn("pageToken=next", request.call_args.args[1])
        with patch.object(oauth, "request_json", return_value={"items": [], "nextPageToken": "repeat"}):
            with self.assertRaises(AppError):
                oauth.list_calendars(self.store)

    def test_incomplete_scopes_rejected(self):
        with self.assertRaises(AppError):
            oauth._tokens({"access_token": "a", "refresh_token": "r", "expires_in": 3600, "scope": oauth.SCOPES[0]})
