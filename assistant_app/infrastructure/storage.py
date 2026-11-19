"""Atomic local configuration and Windows user-bound encrypted private data."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import tempfile
import threading
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path

from assistant_app.domain.errors import AppError
from assistant_app.domain.models import JsonObject, Settings


def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise AppError("Private storage requires Windows DPAPI.")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise AppError(
            "Windows could not unlock private storage." if decrypt else "Windows could not protect private storage."
        )
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


class Store:
    def __init__(self, root: str | Path | None = None) -> None:
        try:
            base = Path(os.environ["LOCALAPPDATA"]).resolve()
            self.root = Path(root).resolve() if root is not None else base / "PersonalAssistant"
            workspace = Path(__file__).resolve().parents[2]
            if not self.root.is_relative_to(base) or self.root.is_relative_to(workspace):
                raise ValueError()
            self.root.mkdir(parents=True, exist_ok=True)
        except Exception:
            raise AppError("Storage must be under LOCALAPPDATA outside the source workspace.") from None
        self._lock = threading.RLock()

    def _read(self) -> JsonObject:
        path = self.root / "state.json"
        if not path.exists():
            return {"settings": asdict(Settings()), "secrets": {}, "revision": 0}
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError()
            state = json.loads(path.read_text(encoding="utf-8"))
            if type(state["revision"]) is not int or state["revision"] < 0 or not isinstance(state["secrets"], dict):
                raise ValueError()
            self._validate_settings(Settings(**state["settings"]))
            return state
        except Exception:
            raise AppError("Saved configuration could not be read.") from None

    def _atomic(self, name: str, data: bytes) -> None:
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as output:
                temp = output.name
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, self.root / name)
        except Exception:
            raise AppError("Local data could not be saved.") from None
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)

    def _write(self, state: JsonObject) -> None:
        data = json.dumps(state).encode("utf-8")
        if len(data) > 4 * 1024 * 1024:
            raise AppError("Saved configuration exceeds the size limit.")
        self._atomic("state.json", data)

    @staticmethod
    def _validate_settings(settings: Settings) -> None:
        if not isinstance(settings, Settings) or any(not isinstance(v, str) for v in asdict(settings).values()):
            raise AppError("Invalid settings.")

    @property
    def revision(self) -> int:
        with self._lock:
            return self._read()["revision"]

    def load_settings(self) -> Settings:
        with self._lock:
            return Settings(**self._read()["settings"])

    def save_settings(self, settings: Settings) -> None:
        self._validate_settings(settings)
        with self._lock:
            state = self._read()
            if state["settings"] != asdict(settings):
                state["settings"] = asdict(settings)
                state["revision"] += 1
                self._write(state)

    def get_secret(self, name: str) -> str:
        with self._lock:
            value = self._read()["secrets"].get(name)
            if value is None:
                return ""
            try:
                return _dpapi(base64.b64decode(value, validate=True), True).decode("utf-8")
            except Exception:
                raise AppError("Private credential could not be read.") from None

    def set_secret(self, name: str, value: str) -> None:
        if not isinstance(name, str) or not name or not isinstance(value, str):
            raise AppError("Invalid private credential.")
        with self._lock:
            state = self._read()
            if self.get_secret(name) == value:
                return
            if value:
                state["secrets"][name] = base64.b64encode(_dpapi(value.encode("utf-8"))).decode("ascii")
            else:
                state["secrets"].pop(name, None)
            if name != "google_tokens":
                state["revision"] += 1
            self._write(state)

    def load_preferences(self) -> list[str]:
        with self._lock:
            path = self.root / "preferences.bin"
            if not path.exists():
                return []
            try:
                if path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError()
                result = json.loads(_dpapi(path.read_bytes(), True))
                self._validate_preferences(result)
                return result
            except Exception:
                raise AppError("Saved preferences could not be read.") from None

    @staticmethod
    def _validate_preferences(values: list[str]) -> None:
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise AppError("Preferences must be a list of text entries.")
        if len(json.dumps(values).encode("utf-8")) > 1024 * 1024:
            raise AppError("Preferences exceed the size limit.")

    def save_preferences(self, values: list[str]) -> None:
        self._validate_preferences(values)
        with self._lock:
            self._atomic("preferences.bin", _dpapi(json.dumps(values).encode("utf-8")))
