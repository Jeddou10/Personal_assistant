"""Google desktop OAuth. Credentials never enter URLs other than the code callback."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from assistant_app.domain.errors import AppError
from assistant_app.domain.models import JsonObject
from assistant_app.domain.ports import StorePort

from .net import request_json

SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
)
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
CALLBACK_TIMEOUT = 180
_lock = threading.RLock()


class _CallbackServer(HTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        # socketserver's default prints tracebacks; callback URLs contain codes.
        pass


def _client(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), str) or not value[key] for key in ("client_id", "client_secret")
    ):
        raise AppError("Select a valid Google Desktop OAuth client file.")
    return {key: value[key] for key in ("client_id", "client_secret")}


def _secret_json(store: StorePort, name: str) -> JsonObject:
    try:
        value = json.loads(store.get_secret(name))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except Exception:
        raise AppError("Reconnect your Google account.") from None


def _tokens(response: JsonObject, previous: JsonObject | None = None) -> JsonObject:
    try:
        access = response["access_token"]
        expires = float(response["expires_in"])
        refresh = response.get("refresh_token") or (previous or {}).get("refresh_token")
        if (
            not isinstance(access, str)
            or not access
            or not isinstance(refresh, str)
            or not refresh
            or not math.isfinite(expires)
            or expires <= 0
            or response.get("token_type", "Bearer").lower() != "bearer"
        ):
            raise ValueError()
        if "scope" in response and not set(SCOPES).issubset(response["scope"].split()):
            raise ValueError()
        return {"access_token": access, "refresh_token": refresh, "expires_at": time.time() + expires}
    except Exception:
        raise AppError("Google authorization was incomplete. Reconnect your account.") from None


def _profile(access: str) -> str:
    profile = request_json("GET", PROFILE_URL, headers={"Authorization": "Bearer " + access})
    email = profile.get("emailAddress")
    if not isinstance(email, str) or "@" not in email or any(c.isspace() for c in email):
        raise AppError("Google account identity could not be verified.")
    return email


def _authorize(client: dict[str, str]) -> tuple[str, str, str]:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            self.request.settimeout(2)
            super().setup()

        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            valid = (
                parsed.path == "/callback"
                and self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
                and len(query.get("state", [])) == 1
                and secrets.compare_digest(query["state"][0], state)
            )
            if valid:
                if len(query.get("code", [])) == 1 and "error" not in query:
                    result["code"] = query["code"][0]
                else:
                    result["error"] = True
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.end_headers()
            self.wfile.write(b"You may close this window." if valid else b"Invalid authorization callback.")

    try:
        with _CallbackServer(("127.0.0.1", 0), Handler) as server:
            server.timeout = 0.5
            redirect = f"http://127.0.0.1:{server.server_port}/callback"
            url = (
                AUTH_URL
                + "?"
                + urlencode(
                    {
                        "client_id": client["client_id"],
                        "redirect_uri": redirect,
                        "response_type": "code",
                        "scope": " ".join(SCOPES),
                        "state": state,
                        "code_challenge": challenge,
                        "code_challenge_method": "S256",
                        "access_type": "offline",
                        "prompt": "consent",
                    }
                )
            )
            if not webbrowser.open(url):
                raise AppError("Could not open the browser for Google authorization.")
            deadline = time.monotonic() + CALLBACK_TIMEOUT
            while not result and time.monotonic() < deadline:
                server.handle_request()
            if not result.get("code"):
                raise AppError("Google authorization was cancelled or timed out.")
            return result["code"], verifier, redirect
    except AppError:
        raise
    except Exception:
        raise AppError("Google authorization callback failed.") from None


def connect_google(store: StorePort, client_file: str | Path) -> str:
    with _lock:
        try:
            path = Path(client_file)
            if path.stat().st_size > 65536:
                raise ValueError()
            client = _client(json.loads(path.read_text(encoding="utf-8"))["installed"])
        except Exception:
            raise AppError("Select a valid Google Desktop OAuth client file.") from None
        code, verifier, redirect = _authorize(client)
        try:
            response = request_json(
                "POST",
                TOKEN_URL,
                form=True,
                data={
                    **client,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": redirect,
                    "grant_type": "authorization_code",
                },
            )
            tokens = _tokens(response)
            email = _profile(tokens["access_token"])
        except AppError:
            raise AppError("Google authorization failed. Reconnect your account.") from None
        tokens["email"] = email
        # Explicit reconnect invalidates proposals even if the Desktop client is unchanged.
        saved_client = dict(client, connection_id=secrets.token_urlsafe(16))
        store.set_secret("google_client", json.dumps(saved_client))
        store.set_secret("google_tokens", json.dumps(tokens))
        return email


def get_access_token(store: StorePort) -> str:
    with _lock:
        tokens = _secret_json(store, "google_tokens")
        try:
            email = tokens["email"]
            bound = store.load_settings().gmail_account
            if not isinstance(email, str) or "@" not in email or (bound and bound.casefold() != email.casefold()):
                raise ValueError()
            access = tokens["access_token"]
            expiry = float(tokens["expires_at"])
            if not isinstance(access, str) or not access or not math.isfinite(expiry):
                raise ValueError()
        except Exception:
            raise AppError("Reconnect the configured Google account.") from None
        if expiry > time.time() + 60:
            return access
        try:
            client = _client(_secret_json(store, "google_client"))
            refresh = tokens["refresh_token"]
            if not isinstance(refresh, str) or not refresh:
                raise ValueError()
            response = request_json(
                "POST", TOKEN_URL, form=True, data={**client, "refresh_token": refresh, "grant_type": "refresh_token"}
            )
            updated = _tokens(response, tokens)
            if _profile(updated["access_token"]).casefold() != email.casefold():
                raise ValueError()
            updated["email"] = email
            store.set_secret("google_tokens", json.dumps(updated))
            return updated["access_token"]
        except Exception:
            raise AppError("Google credentials expired or were revoked. Reconnect your account.") from None


def list_calendars(store: StorePort) -> list[dict]:
    access = get_access_token(store)
    calendars = []
    page = None
    seen = set()
    for _ in range(10):
        params = {"maxResults": 250}
        if page:
            params["pageToken"] = page
        response = request_json(
            "GET",
            "https://www.googleapis.com/calendar/v3/users/me/calendarList?" + urlencode(params),
            headers={"Authorization": "Bearer " + access},
        )
        try:
            items = response.get("items", [])
            if not isinstance(items, list) or len(items) > 250:
                raise ValueError()
            for item in items:
                if not all(isinstance(item.get(k), str) and item[k] for k in ("id", "summary", "accessRole")):
                    raise ValueError()
                calendars.append({k: item[k] for k in ("id", "summary", "accessRole")})
            page = response.get("nextPageToken")
            if not page:
                return calendars
            if not isinstance(page, str) or page in seen:
                raise ValueError()
            seen.add(page)
        except Exception:
            raise AppError("Google returned an invalid calendar list.") from None
    raise AppError("Calendar list exceeded the paging limit; results are incomplete.")
